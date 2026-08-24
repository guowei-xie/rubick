"""护栏:审计导出必须**流式**产出,不许把整批行摊进内存。

守的坑:导出默认 limit=50000、上限 200000,原先是 `list(db.scalars(...))` 把整批 AuditLog
实体读进来,再由 to_csv_bytes 在内存里拼成完整字符串、再 encode —— 同一份数据最多三份并存。
而每条 run_query 的 detail 里带着最长 20000 字符的 executed_sql,audit_logs 又是全库写入量
最大的表。后端是**单进程 uvicorn,同时托管 SPA、/api 和 /health**:这个进程 OOM 不是
「导出失败」,是全站 502,而触发它的只是管理员的一次正常点击。
"""
import asyncio
import csv
import io

import pytest

from app.api.routes.audit import export_logs
from app.models.audit import ACTION_EXPORT_AUDIT, AuditLog
from app.models.user import ROLE_ADMIN
from app.services import audit_service, result_service
from tests.conftest import max_audit_id, new_audit_rows

# ID 段 9140
EXPORTER = 9140
ROWS = 40
# 本文件独占的 resource_type:库不按用例清理,按动作筛会把别的测试文件的行也捞进来
MARK = "exp-fixture"


@pytest.fixture
def exporter(user_factory):
    return user_factory(EXPORTER, ROLE_ADMIN, "导出审计的管理员", prefix="exp")


@pytest.fixture
def bulk(db, exporter):
    """造一批带大 detail 的审计行,贴住真实形状(detail 里就是有 20000 字符的 SQL)。

    库不按用例清理(见 conftest),故 get-or-create:否则每个用例各加 40 行,后面的用例
    数出来的行数一次比一次多。
    """
    from sqlalchemy import func, select

    have = db.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.resource_type == MARK)
    ) or 0
    for i in range(have, ROWS):
        audit_service.log(
            db, user=exporter, action="run_query", resource_type=MARK, resource_id=i,
            detail={"executed_sql": "x" * 2000, "seq": i},
        )
    return ROWS


def _drain(resp) -> tuple[str, int]:
    """把流式响应的分片拼回文本,返回 (文本, 分片数)。

    StreamingResponse 会把同步迭代器包成 async 的,所以这里跑一次事件循环把它抽干。
    """
    async def collect():
        return [c async for c in resp.body_iterator]

    chunks = asyncio.run(collect())
    raw = b"".join(c if isinstance(c, bytes) else c.encode("utf-8") for c in chunks)
    return raw.decode("utf-8-sig"), len(chunks)


def test_export_is_streamed_not_materialised(db, exporter, bulk, monkeypatch):
    """整份内容不许经过 to_csv_bytes —— 那个函数就是「先全拼好再返回」的那条路。"""
    def explode(*_a, **_k):
        raise AssertionError("导出仍在走一次性拼装的 to_csv_bytes,没有真的流式化")

    monkeypatch.setattr(result_service, "to_csv_bytes", explode)
    resp = export_logs(resource_type=MARK, db=db, admin=exporter, ip=None)
    text, n_chunks = _drain(resp)
    assert n_chunks > 1, "只吐了一片 = 还是整份在内存里拼好的"
    assert len(text.splitlines()) == bulk + 1  # 表头 + 数据行


def test_export_content_is_unchanged(db, exporter, bulk):
    """流式化不许改变导出内容:表头、行数、BOM、字段口径都要和从前一致。"""
    resp = export_logs(resource_type=MARK, db=db, admin=exporter, ip=None)
    text, _ = _drain(resp)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][:2] == ["id", "时间"]
    assert rows[0][-1] == "详情"
    assert len(rows) == bulk + 1
    assert '"seq"' in rows[1][-1] or "seq" in rows[1][-1]


def test_export_itself_is_audited_before_streaming(db, exporter, bulk):
    """导出动作本身要留痕,而且**在开始吐数据之前**就落库 ——
    响应体是惰性生成的,把审计写在生成器里等于把合规记录押在客户端读不读完上。"""
    since = max_audit_id(db)
    resp = export_logs(resource_type=MARK, db=db, admin=exporter, ip=None)
    written = [r.action for r in new_audit_rows(db, since)]
    assert ACTION_EXPORT_AUDIT in written, "还没开始吐数据,导出这条审计就该已经在库里"
    _drain(resp)


def test_export_truncates_at_limit_and_says_so_in_the_audit(db, exporter, bulk):
    """锁住 limit 的口径:超出就只给最新那批,而且审计里记的是**实际给出的条数**。

    这不是 bug 护栏,是把 docstring 与管理端提示语新写明的那句承诺钉住 —— CSV 里没有任何
    截断标记,所以「这一份不是全部」这件事只能靠导出前的提示和审计里的 count 传达。管理端
    (AuditPage.EXPORT_MAX_ROWS)现在显式传这个 limit,它变了这条就该红。
    """
    since = max_audit_id(db)
    cap = bulk - 5
    resp = export_logs(resource_type=MARK, limit=cap, db=db, admin=exporter, ip=None)
    text, _ = _drain(resp)
    assert len(text.splitlines()) == cap + 1, "超出 limit 的行不该出现在 CSV 里"
    assert "截断" not in text, "CSV 里确实没有截断标记 —— 这正是提示语必须存在的理由"
    export_row = [r for r in new_audit_rows(db, since) if r.action == ACTION_EXPORT_AUDIT][0]
    assert export_row.detail["count"] == cap, "审计要记实际导出的条数,不是筛选命中的总数"


def test_export_uses_its_own_session(db, exporter, bulk):
    """生成器跑在请求依赖拆解之后,不能借用 Depends(get_db) 那个会话。"""
    resp = export_logs(resource_type=MARK, db=db, admin=exporter, ip=None)
    db.close()  # 模拟依赖已经收尾
    text, _ = _drain(resp)
    assert len(text.splitlines()) == bulk + 1
