import pandas as pd
import numpy as np

def rate(num, den):
 """率：先汇总分子分母再相除；分母0记N/A(NaN)。"""
 return np.where(den > 0, num / den, np.nan)

# 班型级：w=招生占比, r1=实际续报率, rA=A标率, rS=S标率(小数)
df = pd.DataFrame({
 "class": ["科特", "召回", "智配", "转介绍"],
 "w0": [0.30, 0.20, 0.30, 0.20], "w1": [0.28, 0.18, 0.36, 0.18],
 "rS": [0.60, 0.50, 0.45, 0.65],
 "rA": [0.58, 0.49, 0.42, 0.64],
 "r1": [0.57, 0.51, 0.40, 0.63],
})
R_barS = rate((df.w0 * df.rS).sum, df.w0.sum) # 整体S标率(中心化基准=闭合基线)

# 三因子（承 mth_l1_structural_attribution，率影响再拆）
df["struct"] = (df.w1 - df.w0) * (df.rS - R_barS) # 班型结构影响(中心化)
df["as_gap"] = df.w1 * (df.rA - df.rS) # A-S差距影响
df["a_exec"] = df.w1 * (df.r1 - df.rA) # A标执行影响(禁称率影响)

# 闭合校验：Σ三项 = 实际续报率 − 整体S标率
r1_overall = rate((df.w1 * df.r1).sum, df.w1.sum)
gap = r1_overall - R_barS
assert abs(df[["struct", "as_gap", "a_exec"]].sum.sum - gap) < 1e-9

# 展示：三项影响 2位% 带符号
for c in ["struct", "as_gap", "a_exec"]:
 df[c + "_pct"] = (df[c] * 100).round(2)
print(df[["class", "struct_pct", "as_gap_pct", "a_exec_pct"]])
print("缺口(实际-S标) =", round(gap * 100, 2), "%")
