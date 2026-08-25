import numpy as np
import pandas as pd
from scipy import stats


def srm_check(counts: dict, expected_ratio: dict = None) -> dict:
    """分流比例失衡检验。counts={'control': 10021, 'treat': 9880}"""
    groups = list(counts)
    obs = np.array([counts[g] for g in groups], dtype=float)
    if expected_ratio is None:
        exp_p = np.ones(len(groups)) / len(groups)
    else:
        w = np.array([expected_ratio[g] for g in groups], dtype=float)
        exp_p = w / w.sum()
    exp = obs.sum() * exp_p
    chi2 = ((obs - exp) ** 2 / exp).sum()
    p = 1 - stats.chi2.cdf(chi2, df=len(groups) - 1)
    return {"chi2": chi2, "p_value": p, "observed": dict(zip(groups, obs)),
            "expected": dict(zip(groups, exp)),
            "srm_detected": bool(p < 0.001),
            "action": "p<0.001 → 停止读数，回查分流/埋点/过滤条件" if p < 0.001 else "通过"}


def proportion_test(x1: int, n1: int, x2: int, n2: int, alpha: float = 0.05) -> dict:
    """比例型指标：效应量 + 95% CI + p 值（未合并方差算 CI，合并方差算 p）。"""
    p1, p2 = x1 / n1, x2 / n2
    p_pool = (x1 + x2) / (n1 + n2)
    se_pool = np.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    z = (p2 - p1) / se_pool
    p_value = 2 * (1 - stats.norm.cdf(abs(z)))
    se_unpool = np.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    zc = stats.norm.ppf(1 - alpha / 2)
    lift = p2 - p1
    return {"control_rate": p1, "treat_rate": p2, "absolute_lift": lift,
            "relative_lift": lift / p1 if p1 else np.nan,
            "ci_low": lift - zc * se_unpool, "ci_high": lift + zc * se_unpool,
            "p_value": p_value}


def welch_t_test(a: np.ndarray, b: np.ndarray, alpha: float = 0.05) -> dict:
    """均值型指标：Welch t（默认不假设等方差）。"""
    res = stats.ttest_ind(b, a, equal_var=False)
    diff = b.mean() - a.mean()
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    df = se ** 4 / ((a.var(ddof=1) / len(a)) ** 2 / (len(a) - 1)
                    + (b.var(ddof=1) / len(b)) ** 2 / (len(b) - 1))
    tc = stats.t.ppf(1 - alpha / 2, df)
    return {"diff": diff, "ci_low": diff - tc * se, "ci_high": diff + tc * se,
            "p_value": float(res.pvalue), "df": df}


def ratio_metric_delta(df: pd.DataFrame, group_col: str, num_col: str,
                       den_col: str, alpha: float = 0.05) -> dict:
    """比率型指标（分子分母都是随机量），delta method 求方差。

    df 必须已聚合到【分流单位】粒度：一行一个分流单位，num/den 为该单位的分子分母。
    """
    out = {}
    for g, sub in df.groupby(group_col):
        x, y = sub[num_col].to_numpy(float), sub[den_col].to_numpy(float)
        n = len(sub)
        xb, yb = x.mean(), y.mean()
        vx, vy = x.var(ddof=1), y.var(ddof=1)
        cxy = np.cov(x, y, ddof=1)[0, 1]
        var = (vx / yb ** 2 - 2 * xb * cxy / yb ** 3 + xb ** 2 * vy / yb ** 4) / n
        out[g] = {"ratio": xb / yb, "var": var, "n": n}
    (ga, a), (gb, b) = sorted(out.items())[0], sorted(out.items())[1]
    diff = b["ratio"] - a["ratio"]
    se = np.sqrt(a["var"] + b["var"])
    zc = stats.norm.ppf(1 - alpha / 2)
    return {"control": ga, "treat": gb, "control_ratio": a["ratio"],
            "treat_ratio": b["ratio"], "diff": diff,
            "relative_lift": diff / a["ratio"],
            "ci_low": diff - zc * se, "ci_high": diff + zc * se,
            "p_value": 2 * (1 - stats.norm.cdf(abs(diff / se)))}


def cuped_adjust(y: np.ndarray, x_pre: np.ndarray) -> dict:
    """CUPED 方差缩减。x_pre 必须是实验【开始前】的协变量。"""
    theta = np.cov(y, x_pre, ddof=1)[0, 1] / x_pre.var(ddof=1)
    y_adj = y - theta * (x_pre - x_pre.mean())
    rho = np.corrcoef(y, x_pre)[0, 1]
    return {"y_adj": y_adj, "theta": theta, "rho": rho,
            "var_reduction": 1 - y_adj.var(ddof=1) / y.var(ddof=1),
            "effective_sample_multiplier": 1 / (1 - rho ** 2)}


def holm_bonferroni(p_values: dict, alpha: float = 0.05) -> dict:
    """少量对比的族错误率控制。"""
    items = sorted(p_values.items(), key=lambda kv: kv[1])
    m, out, rejected = len(items), {}, True
    for i, (k, p) in enumerate(items):
        thr = alpha / (m - i)
        rejected = rejected and p <= thr
        out[k] = {"p": p, "threshold": thr, "significant": rejected}
    return out
