"""结果落地:CSV 序列化 + 本地文件系统存储 + 预览 + 过期清理。

结果文件存放在 settings.RESULT_DIR 下,object_key 即相对该目录的路径(如 jobs/12/xxx.csv)。
下载通过带签名 token 的后端端点(见 query 路由),不再依赖对象存储签名 URL。
"""
from __future__ import annotations

import codecs
import csv
import io
import time
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


def to_csv_bytes(result: QueryResult) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(result.columns)
    for row in result.rows:
        writer.writerow(["" if v is None else v for v in row])
    # UTF-8 BOM 便于 Excel 直接打开中文
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


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


def cleanup_expired() -> int:
    """删除超过保留期的结果文件,返回删除个数。worker 定期调用。"""
    base = settings.result_dir_path
    if not base.exists():
        return 0
    cutoff = time.time() - settings.RESULT_RETENTION_DAYS * 86400
    removed = 0
    for f in base.rglob("*.csv"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed
