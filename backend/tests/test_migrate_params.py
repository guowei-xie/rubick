"""参数定义 v1 → v2 迁移:映射正确性与幂等性。"""
from app.migrate import _migrate_params_v2
from app.models.template import STATUS_DRAFT, SqlTemplate, TemplateVersion


def _mk_version(db, sql_text: str, params: list[dict]) -> int:
    tmpl = SqlTemplate(
        name="迁移测试", datasource_id=1, dialect="mysql", status=STATUS_DRAFT, author_id=1
    )
    db.add(tmpl)
    db.flush()
    ver = TemplateVersion(
        template_id=tmpl.id, version_no=1, sql_text=sql_text, params=params, author_id=1
    )
    db.add(ver)
    db.commit()
    return ver.id


def test_migrate_v1_params(db):
    ver_id = _mk_version(
        db,
        "SELECT * FROM t WHERE d >= :dr_start AND d <= :dr_end "
        "AND city IN (:cities) AND uid NOT IN (:bad) AND s = :s",
        [
            {"name": "dr", "type": "date_range", "required": True, "label": "日期"},
            {"name": "cities", "type": "multi_enum", "required": False, "enum_sql": "SELECT DISTINCT city FROM t"},
            {"name": "bad", "type": "multi_enum", "required": True},
            {"name": "s", "type": "enum", "options": ["a", "b"], "default": "a"},
        ],
    )
    db.close()  # 迁移用自己的 Session

    _migrate_params_v2()

    from app.core.database import SessionLocal

    s = SessionLocal()
    try:
        ver = s.get(TemplateVersion, ver_id)
        by_name = {p["name"]: p for p in ver.params}
        # date_range → 两个 single
        assert by_name["dr_start"]["kind"] == "single" and "YYYY-MM-DD" in by_name["dr_start"]["description"]
        assert by_name["dr_end"]["label"] == "日期(止)"
        # multi_enum → list,方向按 SQL 判定,enum_sql 保留
        assert by_name["cities"] == {
            "name": "cities", "kind": "list", "label": "cities",
            "list_mode": "in", "enum_sql": "SELECT DISTINCT city FROM t",
        }
        assert by_name["bad"]["list_mode"] == "not_in"
        # enum → single,options 折叠进说明
        assert by_name["s"]["kind"] == "single" and "可选值:a / b" in by_name["s"]["description"]
        assert "dr" not in by_name

        # 幂等:再跑一遍不变
        before = ver.params
        s.close()
        _migrate_params_v2()
        s2 = SessionLocal()
        assert s2.get(TemplateVersion, ver_id).params == before
        s2.close()
    finally:
        s.close()
