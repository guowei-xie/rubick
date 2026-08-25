import numpy as np
import pandas as pd
import statsmodels.formula.api as smf


def did_with_cluster_se(df: pd.DataFrame, y: str, treat: str, post: str,
                        cluster: str, covars: list = None) -> dict:
    """标准 DID，按处理单元聚类稳健标准误（必须，否则严重低估 SE）。"""
    rhs = f"{treat} + {post} + {treat}:{post}"
    if covars:
        rhs += " + " + " + ".join(covars)
    m = smf.ols(f"{y} ~ {rhs}", data=df).fit(
        cov_type="cluster", cov_kwds={"groups": df[cluster]})
    key = f"{treat}:{post}"
    ci = m.conf_int().loc[key]
    return {"att": m.params[key], "se": m.bse[key], "p_value": m.pvalues[key],
            "ci_low": ci[0], "ci_high": ci[1],
            "n_clusters": df[cluster].nunique(),
            "warning": "处理单元 < 30 → 改用 wild cluster bootstrap"
                       if df[cluster].nunique() < 30 else None}


def parallel_trend_event_study(df: pd.DataFrame, y: str, treat: str,
                               period: str, base_period, cluster: str) -> pd.DataFrame:
    """事件研究：逐期估 treat 交互系数。所有 pre 期系数应不显著且无趋势。"""
    d = df.copy()
    d["_p"] = d[period].astype(str)
    m = smf.ols(f"{y} ~ C(_p) + {treat}:C(_p, Treatment(reference='{base_period}'))",
                data=d).fit(cov_type="cluster", cov_kwds={"groups": d[cluster]})
    rows = []
    for name in m.params.index:
        if treat in name and "C(_p" in name:
            ci = m.conf_int().loc[name]
            rows.append({"term": name, "coef": m.params[name],
                         "ci_low": ci[0], "ci_high": ci[1],
                         "p_value": m.pvalues[name],
                         "significant": m.pvalues[name] < 0.05})
    return pd.DataFrame(rows)


def psm_balance_check(df: pd.DataFrame, treat: str, covars: list,
                      weight: str = None) -> pd.DataFrame:
    """匹配/加权后的协变量均衡：标准化均值差 SMD，阈值 0.1。"""
    rows = []
    for c in covars:
        t, ctl = df[df[treat] == 1], df[df[treat] == 0]
        if weight:
            mt = np.average(t[c], weights=t[weight])
            mc = np.average(ctl[c], weights=ctl[weight])
        else:
            mt, mc = t[c].mean(), ctl[c].mean()
        pooled = np.sqrt((t[c].var(ddof=1) + ctl[c].var(ddof=1)) / 2)
        smd = (mt - mc) / pooled if pooled else 0.0
        rows.append({"covariate": c, "treat_mean": mt, "control_mean": mc,
                     "smd": smd, "balanced": abs(smd) < 0.1})
    return pd.DataFrame(rows)


def its_model(df: pd.DataFrame, y: str, time: str, intervention_time,
              maxlags: int = 4) -> dict:
    """中断时间序列：水平跳变 + 斜率变化，HAC 标准误处理自相关。"""
    d = df.sort_values(time).copy()
    d["_t"] = np.arange(len(d))
    d["_D"] = (d[time] >= intervention_time).astype(int)
    t0 = d.loc[d["_D"] == 1, "_t"].min()
    d["_tD"] = (d["_t"] - t0) * d["_D"]
    m = smf.ols(f"{y} ~ _t + _D + _tD", data=d).fit(
        cov_type="HAC", cov_kwds={"maxlags": maxlags})
    return {"level_change": m.params["_D"], "level_p": m.pvalues["_D"],
            "slope_change": m.params["_tD"], "slope_p": m.pvalues["_tD"],
            "n_pre": int((d["_D"] == 0).sum()),
            "warning": "干预前周期 < 8，ITS 不稳" if (d["_D"] == 0).sum() < 8 else None,
            "reminder": "无对照组：必须列出同期其他事件清单"}


def design_effect(n_orders: int, n_clusters: int, icc: float) -> dict:
    """整群设计的样本量膨胀。按老师组分流时，订单数不等于有效样本量。"""
    m = n_orders / n_clusters
    deff = 1 + (m - 1) * icc
    return {"avg_cluster_size": m, "icc": icc, "design_effect": deff,
            "effective_n": n_orders / deff,
            "warning": "老师组数 < 30 → 用 wild cluster bootstrap；< 10 → 只做描述，不做推断"
                       if n_clusters < 30 else None}


def estimate_icc(df: pd.DataFrame, y: str, cluster: str) -> float:
    """从历史数据估组内相关系数（单因素随机效应 ANOVA 法）。"""
    grp = df.groupby(cluster)[y]
    k = grp.ngroups
    n_i = grp.size().to_numpy(float)
    m0 = (n_i.sum() - (n_i ** 2).sum() / n_i.sum()) / (k - 1)
    msb = (n_i * (grp.mean().to_numpy(float) - df[y].mean()) ** 2).sum() / (k - 1)
    msw = ((grp.transform("mean") - df[y]) ** 2).sum() / (len(df) - k)
    var_b = max((msb - msw) / m0, 0.0)
    return float(var_b / (var_b + msw)) if (var_b + msw) > 0 else 0.0


def build_control_pool(df: pd.DataFrame, treat_group_ids: list,
                       class_tag_col: str = "goal_class_tag",
                       group_col: str = "renewal_counselor_group_id",
                       treat_class_tags: list = None) -> dict:
    """默认对照组构造：同定标班型大盘，且【剔除实验组老师组】。"""
    tags = treat_class_tags or df[df[group_col].isin(treat_group_ids)][class_tag_col].unique().tolist()
    same_tag = df[df[class_tag_col].isin(tags)]
    treat = same_tag[same_tag[group_col].isin(treat_group_ids)]
    control = same_tag[~same_tag[group_col].isin(treat_group_ids)]
    return {"class_tags": tags,
            "treat_orders": len(treat), "treat_clusters": treat[group_col].nunique(),
            "control_orders": len(control), "control_clusters": control[group_col].nunique(),
            "treat": treat, "control": control,
            "note": "未剔除实验组的大盘对照会稀释效应，估计值系统性低估"}
