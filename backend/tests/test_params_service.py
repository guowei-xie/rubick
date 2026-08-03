"""参数校验/绑定、列表展开、渲染。"""
import pytest

from app.core.exceptions import RubicError
from app.services import params_service as ps


def _defs(*defs):
    return list(defs)


def test_bind_scalar_types():
    defs = _defs(
        {"name": "d", "type": "date", "required": True},
        {"name": "n", "type": "number", "required": True},
        {"name": "t", "type": "text", "required": True},
    )
    bound = ps.validate_and_bind(defs, {"d": "2026-08-03", "n": "42", "t": "hi"})
    assert bound == {"d": "2026-08-03", "n": 42, "t": "hi"}


def test_bind_ranges_expand():
    defs = _defs(
        {"name": "dr", "type": "date_range", "required": True},
        {"name": "nr", "type": "number_range", "required": True},
    )
    bound = ps.validate_and_bind(defs, {"dr": ["2026-01-01", "2026-02-01"], "nr": [1, 9]})
    assert bound["dr_start"] == "2026-01-01" and bound["dr_end"] == "2026-02-01"
    assert bound["nr_min"] == 1 and bound["nr_max"] == 9


def test_required_missing_raises():
    with pytest.raises(RubicError):
        ps.validate_and_bind(_defs({"name": "x", "type": "text", "required": True}), {})


def test_optional_multi_enum_empty_is_unfiltered():
    defs = _defs({"name": "ids", "type": "multi_enum", "required": False})
    bound = ps.validate_and_bind(defs, {"ids": []})
    assert isinstance(bound["ids"], ps._Unfiltered)


def test_enum_option_validation():
    defs = _defs({"name": "s", "type": "enum", "required": True, "options": ["a", "b"]})
    assert ps.validate_and_bind(defs, {"s": "a"})["s"] == "a"
    with pytest.raises(RubicError):
        ps.validate_and_bind(defs, {"s": "z"})


def test_expand_in_list():
    sql = "SELECT * FROM t WHERE user_id IN (:ids)"
    new_sql, out = ps.expand_list_params(sql, {"ids": ["u1", "u2"]})
    assert "IN (:ids__0, :ids__1)" in new_sql
    assert out == {"ids__0": "u1", "ids__1": "u2"}


def test_expand_eq_becomes_in():
    sql = "SELECT * FROM t WHERE city = :c"
    new_sql, out = ps.expand_list_params(sql, {"c": ["bj", "sh"]})
    assert "city IN (:c__0, :c__1)" in new_sql
    assert out == {"c__0": "bj", "c__1": "sh"}


def test_expand_not_in_unfiltered_neutralizes_whole_predicate():
    # UNFILTERED 的 NOT IN 必须整体中和为 1=1,不能残留列名
    sql = "SELECT * FROM t WHERE user_id NOT IN (:ids)"
    new_sql, _ = ps.expand_list_params(sql, {"ids": ps.UNFILTERED})
    assert "1=1" in new_sql
    assert "user_id 1=1" not in new_sql  # 回归:曾经的错误产物


def test_render_sql_quotes_and_nulls():
    out = ps.render_sql("SELECT :a, :b, :c", {"a": "x'y", "b": 5, "c": None})
    assert "'x''y'" in out and " 5" in out and "NULL" in out
