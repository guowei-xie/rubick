"""结果落地:CSV 序列化 + 本地文件系统存储 + 预览 + 过期清理。

结果文件存放在 settings.result_dir_path(即 DATA_DIR/results)下,object_key 即相对该目录的
路径(如 jobs/12/xxx.csv)—— 相对,所以换存储位置只是改配置 + 搬文件,库里不用动。
下载通过带签名 token 的后端端点(见 query 路由),不再依赖对象存储签名 URL。
"""
from __future__ import annotations

import codecs
import csv
import io
import time
from collections.abc import Iterator
from itertools import chain, islice
from pathlib import Path

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

# 写到一半的结果文件的后缀(写完即改名去掉它)。write_csv 与 cleanup_expired 共用一个常量
# —— 两处各写一遍字面量的下场是清理器认不出临时文件,而那种漏删是永久的。
_PART_SUFFIX = ".part"

# UTF-8 BOM 便于 Excel 直接打开中文
_BOM = b"\xef\xbb\xbf"


def iter_csv_bytes(columns, rows) -> Iterator[bytes]:
    """把列名 + 行迭代器**流式**序列化成 CSV 字节片。

    唯一的 CSV 写法住在这里,两个出口共用:审计导出直接把它当响应体(见 audit 路由),
    取数结果由 write_csv 把它写进文件。两边的量级都不可控 —— 取数行数上限已可关闭,
    审计导出可以有二十万行、每行还带着一段 SQL 原文,而后端是**单进程 uvicorn、
    同时托管 SPA 与 /api**:一次性拼装把它撑爆不是「导出失败」,是全站 502。
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
        # 直接把驱动给的行交给 csv:它本来就把 None 写成空字段(与 "" 逐字节相同)。
        # 这里曾有一句 ["" if v is None else v for v in row] —— 每行多一个列表 + 一遍
        # 遍历,占 CSV 序列化开销的三成,而行数已经没有上限了。别再加回来。
        writer.writerow(row)
        n += 1
        if n >= _CSV_CHUNK_ROWS:
            yield flush()
            n = 0
    if n:
        yield flush()


def write_csv(object_key: str, columns, rows, *, max_rows: int | None = None) -> tuple[int, bool]:
    """把列名 + 行迭代器**流式**写成结果文件,返回 (写入行数, 是否因 max_rows 截断)。

    取数结果的落地入口。整条链路(服务端游标 → 这里 → 文件)上都没有「把所有行攒起来」
    的一步,所以取数进程的内存与结果行数无关 —— 这正是行数上限得以关掉的前提。
    max_rows=None 即不限;给了正数则写到那么多行为止,并回报「还有更多」。

    先写 .part 再改名:中途失败(引擎报错、超时、进程被打断)时留下的是一个不会被误当成
    结果的临时文件,而不是一份看着正常、实际只有前半截的 CSV。**进程被 SIGKILL 时连
    unlink 都来不及**,所以 cleanup_expired 也认这个后缀 —— 别让它们在盘上待到永远。
    """
    path = _abs_path(object_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + _PART_SUFFIX)
    written = 0
    truncated = False

    def counted() -> Iterator:
        nonlocal written, truncated
        for row in rows:
            if max_rows is not None and written >= max_rows:
                truncated = True
                return
            written += 1
            yield row

    try:
        with tmp.open("wb") as fp:
            for chunk in iter_csv_bytes(columns, counted()):
                fp.write(chunk)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return written, truncated


def exists(object_key: str) -> bool:
    return _abs_path(object_key).exists()


def is_gone(job) -> bool:
    """这次运行的结果**实际上已经取不到了**:过了保留期,或文件不在盘上(手工清理 / 迁移丢失)。

    「取不到」只在这里判一次。此前预览与下载各判一半 —— 下载判了文件在不在盘上,预览没判,
    于是文件被手工清掉时预览返回的是**一张没有任何解释的空表**(read_csv_preview 读不到
    行就给空列表),而业务方从那张表上看不出「结果没了」还是「这次真的一行都没查到」。
    """
    return job.result_expired or not exists(job.result_object_key)


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

    **写到一半的 .part 也归这里收**(见 write_csv):worker 被 SIGKILL 就会留下一个,
    而 `deploy.sh update` 每次都可能这么杀。它们同样按保留期删 —— 一个 .part 早就是垃圾了,
    但拿保留期当门槛能保证绝不会删到**正在写**的那一份,那才是不能出错的一边。
    """
    base = settings.result_dir_path
    if not base.exists():
        return 0
    cutoff = time.time() - settings.RESULT_RETENTION_DAYS * 86400
    removed = 0
    for f in chain(base.rglob("*.csv"), base.rglob(f"*.csv{_PART_SUFFIX}")):
        try:
            if f.stat().st_mtime < cutoff:
                if protected_keys and f.relative_to(base).as_posix() in protected_keys:
                    continue
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed
