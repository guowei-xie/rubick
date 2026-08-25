import numpy as np
from scipy import stats


def sample_size_proportion(p1: float, mde_rel: float, alpha: float = 0.05,
                           power: float = 0.8, ratio: float = 1.0,
                           two_sided: bool = True) -> dict:
    """比例型指标（转化率/续报率/退费率）双样本样本量。

    p1        : 对照组基线比例
    mde_rel   : 相对提升幅度，如 0.05 表示相对提升 5%
    ratio     : n_treat / n_control
    """
    p2 = p1 * (1 + mde_rel)
    z_a = stats.norm.ppf(1 - alpha / 2) if two_sided else stats.norm.ppf(1 - alpha)
    z_b = stats.norm.ppf(power)
    delta = p2 - p1
    n1 = (z_a + z_b) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2) / ratio) / delta ** 2
    n1 = int(np.ceil(n1))
    return {"p1": p1, "p2": p2, "absolute_mde": delta,
            "n_control": n1, "n_treat": int(np.ceil(n1 * ratio)),
            "n_total": int(np.ceil(n1 * (1 + ratio)))}


def sample_size_mean(sigma: float, mde_abs: float, alpha: float = 0.05,
                     power: float = 0.8) -> dict:
    """均值型指标（客单价/GMV per user）等分双样本样本量。"""
    z_a, z_b = stats.norm.ppf(1 - alpha / 2), stats.norm.ppf(power)
    n = 2 * (z_a + z_b) ** 2 * sigma ** 2 / mde_abs ** 2
    n = int(np.ceil(n))
    return {"n_per_group": n, "n_total": 2 * n}


def mde_from_n(p1: float, n_per_group: int, alpha: float = 0.05,
               power: float = 0.8) -> dict:
    """反向：给定可用样本量，能查出多大的效应（比例型近似解）。"""
    z_a, z_b = stats.norm.ppf(1 - alpha / 2), stats.norm.ppf(power)
    delta = (z_a + z_b) * np.sqrt(2 * p1 * (1 - p1) / n_per_group)
    return {"absolute_mde": delta, "relative_mde": delta / p1,
            "note": "近似解，假设 p2≈p1；效应较大时用 sample_size_proportion 迭代校正"}


def duration_days(n_total: int, daily_eligible: int, traffic_share: float = 1.0,
                  min_cycle_days: int = 7) -> dict:
    """把样本量换算成天数，并强制不低于一个完整业务周期。"""
    days = n_total / (daily_eligible * traffic_share)
    return {"raw_days": days, "recommended_days": max(np.ceil(days), min_cycle_days)}


def sample_size_clustered(n_per_group_individual: int, cluster_size: float,
                          icc: float) -> dict:
    """整群随机：把个体样本量换算成需要的群数（老师组数/班级组数）。"""
    import math
    deff = 1 + (cluster_size - 1) * icc
    n_inflated = n_per_group_individual * deff
    n_clusters = math.ceil(n_inflated / cluster_size)
    return {"design_effect": deff, "n_individual_per_group": n_inflated,
            "n_clusters_per_group": n_clusters,
            "feasible": n_clusters >= 15,
            "note": "每组群数 < 15 → 功效不足且聚类 SE 不可靠，考虑改用个体分流或延长周期"}
