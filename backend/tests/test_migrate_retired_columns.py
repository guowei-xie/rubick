"""死列迁移:模型退役的列要在存量库里一并清掉,残留的阻断列必须被体检拦住。

回归的是线上真实故障:rubick_query_jobs.modes 从模型删了却没配套迁移,
线上表里残留为 json NOT NULL 无默认值,于是取数入队的 INSERT 全部失败
(MySQL 严格模式 1364),表现为前端只弹一句「取数失败」且不生成运行记录。
"""
import pytest
from sqlalchemy import JSON, Column, MetaData, inspect as sa_inspect, text

from app.core.database import Base, engine, tbl
from app.migrate import _assert_no_blocking_orphan_columns, _drop_retired_columns
from app.models.query_job import JOB_QUEUED, QueryJob


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa_inspect(engine).get_columns(table)}


def _recreate_with_modes() -> None:
    """把 query_jobs 还原成「带 modes(NOT NULL 且无默认值)」的旧 schema。

    整套模型表都复制进临时 MetaData,否则 query_jobs 的外键指不到目标表、建表编译失败。
    """
    md = MetaData()
    for t in Base.metadata.sorted_tables:
        t.to_metadata(md)
    old = md.tables[tbl("query_jobs")]
    old.append_column(Column("modes", JSON, nullable=False))
    QueryJob.__table__.drop(engine, checkfirst=True)
    old.create(engine)


def test_retired_modes_column_dropped_and_writes_restored(db):
    jobs = tbl("query_jobs")
    try:
        _recreate_with_modes()
        assert "modes" in _columns(jobs)
        with engine.begin() as conn:  # 一条存量运行记录(旧 schema 下写入)
            conn.execute(
                text(
                    f"INSERT INTO {jobs} (id, user_id, template_id, datasource_id, params, modes,"
                    " status, source, created_at, updated_at) VALUES (9001, 1, 1, 1, '{}', '{}',"
                    " 'success', 'run', '2026-08-03 10:59:42', '2026-08-03 10:59:42')"
                )
            )

        # 残留死列会阻断该表所有写入,体检必须在迁移期就报出来
        with pytest.raises(RuntimeError, match="modes"):
            _assert_no_blocking_orphan_columns()

        _drop_retired_columns()

        assert "modes" not in _columns(jobs)
        _assert_no_blocking_orphan_columns()  # 体检通过
        with engine.begin() as conn:  # 历史运行记录在重建中保留
            assert conn.execute(text(f"SELECT status FROM {jobs} WHERE id = 9001")).scalar() == "success"

        # 按当前模型入队(enqueue 走的就是这条路径)重新可写
        db.add(QueryJob(id=9002, user_id=1, template_id=1, datasource_id=1, params={}, status=JOB_QUEUED))
        db.commit()
        assert db.get(QueryJob, 9002).status == JOB_QUEUED

        # 幂等:再跑一次不报错、无副作用
        _drop_retired_columns()
        assert "modes" not in _columns(jobs)
    finally:
        # 本用例直接改了共享 SQLite 的表结构,收尾恢复成模型定义的空表,不影响其它用例
        db.rollback()
        QueryJob.__table__.drop(engine, checkfirst=True)
        QueryJob.__table__.create(engine, checkfirst=True)
