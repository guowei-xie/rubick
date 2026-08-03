"""参数校验/绑定、列表展开、渲染(v2:single/list 两种、一律必填)。"""
import pytest

from app.core.exceptions import RubicError
from app.services import params_service as ps


def _defs(*defs):
    return list(defs)


def test_bind_single_is_str():
    defs = _defs({"name": "d", "kind": "single"}, {"name": "n", "kind": "single"})
    bound = ps.validate_and_bind(defs, {"d": "2026-08-03", "n": 42})
    assert bound == {"d": "2026-08-03", "n": "42"}  # 一切皆字符串


def test_bind_list_is_str_list():
    defs = _defs({"name": "ids", "kind": "list"})
    bound = ps.validate_and_bind(defs, {"ids": ["u1", 2]})
    assert bound == {"ids": ["u1", "2"]}


def test_missing_raises():
    for empty in ({}, {"x": ""}, {"x": None}):
        with pytest.raises(RubicError):
            ps.validate_and_bind(_defs({"name": "x", "kind": "single"}), empty)


def test_empty_list_raises():
    with pytest.raises(RubicError):
        ps.validate_and_bind(_defs({"name": "ids", "kind": "list"}), {"ids": []})


def test_legacy_type_normalizes_to_kind():
    # 漏迁移的旧 shape:multi_enum → list,其余 → single;required/default 被忽略(一律必填)
    defs = _defs(
        {"name": "ids", "type": "multi_enum", "required": False},
        {"name": "d", "type": "date", "required": True, "default": "2024-01-01"},
    )
    bound = ps.validate_and_bind(defs, {"ids": ["a"], "d": "2026-01-01"})
    assert bound == {"ids": ["a"], "d": "2026-01-01"}
    with pytest.raises(RubicError):  # default 不再回填
        ps.validate_and_bind(defs, {"ids": ["a"]})


def test_detect_is_list():
    sql = "SELECT * FROM t WHERE d = :d AND uid IN (:ids) AND city NOT IN (:cs)"
    assert ps.detect_is_list(sql, "ids") is True
    assert ps.detect_is_list(sql, "cs") is True  # NOT IN 也是值列表
    assert ps.detect_is_list(sql, "d") is False  # 单值
    assert ps.detect_is_list(sql, "") is False


def test_expand_in_list():
    # 最小占位符展开:作者已写好括号,替换裸 :ids 即得 IN (:ids__0, :ids__1)
    sql = "SELECT * FROM t WHERE user_id IN (:ids)"
    new_sql, out = ps.expand_list_params(sql, {"ids": ["u1", "u2"]})
    assert "user_id IN (:ids__0, :ids__1)" in new_sql
    assert out == {"ids__0": "u1", "ids__1": "u2"}


def test_expand_not_in_keeps_direction():
    sql = "SELECT * FROM t WHERE user_id NOT IN (:ids)"
    new_sql, out = ps.expand_list_params(sql, {"ids": ["u1", "u2"]})
    assert "user_id NOT IN (:ids__0, :ids__1)" in new_sql
    assert out == {"ids__0": "u1", "ids__1": "u2"}


def test_expand_word_boundary_no_prefix_clobber():
    # :id 不应误伤 :ids;展开后无残留裸占位符
    sql = "SELECT * FROM t WHERE a IN (:id) AND b IN (:ids)"
    new_sql, out = ps.expand_list_params(sql, {"id": ["x"], "ids": ["y", "z"]})
    assert "a IN (:id__0)" in new_sql
    assert "b IN (:ids__0, :ids__1)" in new_sql
    assert out == {"id__0": "x", "ids__0": "y", "ids__1": "z"}


def test_expand_passthrough_scalar():
    sql = "SELECT * FROM t WHERE d = :d"
    new_sql, out = ps.expand_list_params(sql, {"d": "2026-01-01"})
    assert new_sql == sql
    assert out == {"d": "2026-01-01"}


def test_render_sql_quotes_and_nulls():
    out = ps.render_sql("SELECT :a, :b, :c", {"a": "x'y", "b": 5, "c": None})
    assert "'x''y'" in out and " 5" in out and "NULL" in out
