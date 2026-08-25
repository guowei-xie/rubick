import pandas as pd


def gmv_three_factor(df: pd.DataFrame) -> pd.DataFrame:
 """GMV 三因子序贯分解: GMV = E(招生量) × R(续报率) × P(客单价).
 df 需含分组列 group 及 基期 E0,R0,P0 / 现期 E1,R1,P1。
 率 R、客单价 P 须由外部先汇总分子分母再相除得到, 禁算术平均。
 固定顺序 招生量→续报率→客单价, 禁调换(交叉项被后置因子吸收)。
 """
 d = df.copy
 d["gmv0"] = d.E0 * d.R0 * d.P0
 d["gmv1"] = d.E1 * d.R1 * d.P1
 d["gap"] = d.gmv1 - d.gmv0
 d["impact_admission"] = (d.E1 - d.E0) * d.R0 * d.P0
 d["impact_renewal"] = d.E1 * (d.R1 - d.R0) * d.P0
 d["impact_arpu"] = d.E1 * d.R1 * (d.P1 - d.P0)
 d["closure"] = (d.impact_admission + d.impact_renewal
 + d.impact_arpu - d.gap).abs
 assert (d["closure"] < 1e-6).all, "序贯闭合失败"
 return d


def total_row(d: pd.DataFrame) -> dict:
 """总计行: 因子影响直接求和=整体缺口; 达成率按整体重算, 分母0记 N/A。"""
 cols = ["gmv0", "gmv1", "gap", "impact_admission",
 "impact_renewal", "impact_arpu"]
 tot = {c: d[c].sum for c in cols}
 tot["achievement"] = tot["gmv1"] / tot["gmv0"] if tot["gmv0"] else None
 tot["share_admission"] = tot["impact_admission"] / tot["gap"] if tot["gap"] else None
 return tot
