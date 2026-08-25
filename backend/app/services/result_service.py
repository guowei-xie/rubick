"""结果落地:CSV 序列化 + 本地文件系统存储 + 预览 + 过期清理。

结果文件存放在 settings.RESULT_DIR 下,object_key 即相对该目录的路径(如 jobs/12/xxx.csv)。
下载通过带签名 token 的后端端点(见 query 路由),不再依赖对象存储签名 URL。
"""
from __future__ import annotations

import codecs
import csv
import io
import time
from collections.abc import Iterator
from itertools import islice
from pathlib import Path

from app.connectors.base import QueryResult
from app.core.config import settings


def _abs_path(object_key: str) -> Path:
    """object_key -> 结果目录下的绝对路径(防目录穿越)。"""
    base = settings.result_dir_path.resolve()
    p = (base / object_key).resolve()
    if base not in p.parents and p != base:
        raise ValueError("非法的结果路径")
    return p


# 一次吐出多少行。攒批是为了别让每行都变成一个 HTTP chunk(那样开销全在协议头上),
# 而不是为了缓存 —— 内存占用只与这个数字有关,与总行数无关。
_CSV_CHUNK_ROWS = 500

# UTF-8 BOM 便于 Excel 直接打开中文
_BOM = b"\xef\xbb\xbf"


def iter_csv_bytes(columns, rows) -> Iterator[bytes]:
    """把列名 + 行迭代器**流式**序列化成 CSV 字节片。

    唯一的 CSV 写法住在这里:to_csv_bytes 只是它的「全收进内存」版本。分成两个入口是因为
    两类调用方的规模天差地别 —— 取数结果受 MAX_RESULT_ROWS 收口,而审计导出可以有二十万行、
    每行还带着一段 SQL 原文,那种量级一次性拼装会把整个 API 进程(它同时还托管 SPA)撑爆。
    """
    buf = io.StringIO()
    writer = csv.writer(buf)

    def flush() -> bytes:
        """取出已写入的部分并清空缓冲 —— 缓冲里最多只压着 _CSV_CHUNK_ROWS 行。"""
        chunk = buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate(0)
        return chunk

    writer.writerow(columns)
    yield _BOM + flush()

    n = 0
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
        n += 1
        if n >= _CSV_CHUNK_ROWS:
            yield flush()
            n = 0
    if n:
        yield flush()


def to_csv_bytes(result: QueryResult) -> bytes:
    """整份 CSV 的字节串。行数受 MAX_RESULT_ROWS 收口的取数结果用它;
    规模不可控的导出走 iter_csv_bytes。"""
    return b"".join(iter_csv_bytes(result.columns, result.rows))


def upload_csv(object_key: str, data: bytes) -> None:
    path = _abs_path(object_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def exists(object_key: str) -> bool:
    return _abs_path(object_key).exists()


def local_path(object_key: str) -> Path:
    """供下载端点做流式响应用。"""
    return _abs_path(object_key)


def read_csv_preview(object_key: str, limit: int = 50) -> tuple[list[str], list[list]]:
    """读取已存 CSV 的表头 + 前 limit 行,用于运行记录预览。

    流式读取,取够 limit+1 行(表头 + limit)即停,避免把整份大结果读进内存。
    """
    path = _abs_path(object_key)
    if not path.exists():
        return [], []
    with path.open("rb") as fp:
        reader = csv.reader(codecs.getreader("utf-8-sig")(fp))  # 边读边解码去 BOM
        head = list(islice(reader, limit + 1))
    if not head:
        return [], []
    return head[0], head[1:]


def cleanup_expired(protected_keys: frozenset[str] = frozenset()) -> int:
    """删除超过保留期的结果文件,返回删除个数。worker 每小时调用一次(启动时也调一次)。

    protected_keys:按 mtime 已到期、但**仍不许删**的 object_key(当前只有一类 ——
    尚未被下一期取代的订阅结果,见 subscription_service.protected_result_keys)。
    本模块不 import 模型,保护名单由调用方算好传进来;默认空集,行为与从前一致。
    """
    base = settings.result_dir_path
    if not base.exists():
        return 0
    cutoff = time.time() - settings.RESULT_RETENTION_DAYS * 86400
    removed = 0
    for f in base.rglob("*.csv"):
        try:
            if f.stat().st_mtime < cutoff:
                if protected_keys and f.relative_to(base).as_posix() in protected_keys:
                    continue
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed
