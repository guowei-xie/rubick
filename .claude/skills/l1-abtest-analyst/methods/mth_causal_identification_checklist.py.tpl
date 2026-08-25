import numpy as np
import pandas as pd
import statsmodels.formula.api as smf


def e_value(rr: float, ci_low: float = None) -> dict:
    """E-value：推翻该效应所需的未观测混杂最小强度（风险比尺度）。

    rr 为风险比/优势比；均值型指标先按 Cohen's d 近似转换：rr ≈ exp(0.91*d)。
    """
    def _ev(x):
        x = 1 / x if x < 1 else x
        return x + np.sqrt(x * (x - 1))
    out = {"e_value_point": _ev(rr)}
    if ci_low is not None:
        out["e_value_ci"] = 1.0 if ci_low <= 1 <= rr else _ev(ci_low)
    out["reading"] = "E-value 远大于现实中可能的混杂强度 → 结论稳健；接近 1 → 结论脆弱"
    return out


def overlap_check(df: pd.DataFrame, treat: str, pscore: str,
                  bins: int = 20) -> pd.DataFrame:
    """正值性/共同支撑：倾向得分分箱后两组样本量分布。任一箱单边为 0 → 无重叠。"""
    d = df.copy()
    d["_bin"] = pd.cut(d[pscore], bins=bins)
    tab = d.pivot_table(index="_bin", columns=treat, values=pscore,
                        aggfunc="count", observed=False).fillna(0)
    tab.columns = [f"n_group_{c}" for c in tab.columns]
    tab["has_overlap"] = (tab > 0).all(axis=1)
    return tab.reset_index()


def hte_interaction_test(df: pd.DataFrame, y: str, treat: str,
                         subgroup: str, cluster: str = None) -> dict:
    """异质效应资格检验：交互项不显著 → 没有资格做子群拆解。"""
    kw = {"cov_type": "cluster", "cov_kwds": {"groups": df[cluster]}} if cluster else {}
    m = smf.ols(f"{y} ~ {treat} * C({subgroup})", data=df).fit(**kw)
    inter = [t for t in m.params.index if ":" in t]
    pmin = float(min(m.pvalues[inter])) if inter else 1.0
    return {"interaction_terms": inter,
            "min_p_value": pmin,
            "eligible_for_subgroup": pmin < 0.05,
            "note": "不显著仍要拆 → 结论必须标注「探索性发现，需独立验证」"}


def post_treatment_guard(covariates: list, treatment_time_known: dict) -> dict:
    """处理后变量守卫：把协变量按「是否在干预前已确定」分类，拦下会造成对撞偏差的项。

    treatment_time_known: {列名: True/False}，True 表示干预发生前该值已确定。
    """
    safe = [c for c in covariates if treatment_time_known.get(c, False)]
    unsafe = [c for c in covariates if not treatment_time_known.get(c, False)]
    return {"safe_to_control": safe, "must_exclude": unsafe,
            "blocked": bool(unsafe),
            "reason": "处理后变量既不能当协变量也不能当筛选条件（对撞偏差）"}
