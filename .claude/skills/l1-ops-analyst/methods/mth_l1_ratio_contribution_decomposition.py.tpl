import pandas as pd


def ratio_contribution(df: pd.DataFrame, num: str = "numer",
 den: str = "denom",
 tgt: str = "target_ratio") -> pd.DataFrame:
 """比值型指标(人效/客单价/率)可加贡献分解通法。
 df: 每行一个子群 c, 含分子 num、分母 den、子群目标比值 tgt。
 贡献_c = (Δ_c / ΣΔ) · (整体实际 − 整体目标), Σ 闭合于整体缺口。
 率/比值先汇总分子分母再相除, 禁算术平均; 分母0 / ΣΔ=0 记 N/A。
 """
 d = df.copy
 d["m_act"] = d[num] / d[den].replace(0, pd.NA) # 子群实际比值, 分母0->N/A
 d["delta"] = d["m_act"] - d[tgt]
 den_sum = d[den].sum
 # 整体: 先汇总分子分母再相除; 整体目标同口径(目标分子=tgt*den)
 m_all = d[num].sum / den_sum if den_sum else None
 m_tgt_all = (d[tgt] * d[den]).sum / den_sum if den_sum else None
 dm = None if (m_all is None or m_tgt_all is None) else m_all - m_tgt_all
 sd = d["delta"].sum
 d["contrib"] = (d["delta"] / sd) * dm if (sd and dm is not None) else pd.NA
 d["contrib_share"] = d["contrib"] / dm if dm else pd.NA
 assert not sd or dm is None or abs(d["contrib"].sum - dm) < 1e-6, "贡献闭合失败"
 return d
