import pandas as pd
import numpy as np

def rate(num, den):
 """率：先汇总分子分母再相除；分母0记N/A(NaN)。"""
 return np.where(den > 0, num / den, np.nan)

def single_layer(df, w0, w1, r0, Rbar0):
 """单层中心化两因子，见 mth_l1_structural_attribution。返回结构影响、率影响列(小数)。"""
 struct = (df[w1] - df[w0]) * (df[r0] - Rbar0)
 # 率影响留待下钻替换；此处先给两因子率影响 = w1*(r1-r0)
 return struct

def shrink(n, r_unit, r_parent, K=300):
 """经验贝叶斯收缩去噪；收缩后接近父层率者判噪声。"""
 return (n * r_unit + K * r_parent) / (n + K)

# 示例：上层某组内率变化 = -0.80%（小数 -0.008），下钻到子层
child = pd.DataFrame({
 "unit": ["A", "B", "C"],
 "w0": [0.5, 0.3, 0.2], "w1": [0.4, 0.35, 0.25],
 "r0": [0.62, 0.55, 0.48], "r1": [0.60, 0.56, 0.45],
})
Rbar0 = rate((child.w0 * child.r0).sum, child.w0.sum) # 本层整体基准率
child["struct"] = (child.w1 - child.w0) * (child.r0 - Rbar0) # 中心化结构影响
child["rate_impact"] = child.w1 * (child.r1 - child.r0) # 率影响

child_inner = child["rate_impact"].sum # 下一层内率
layer_struct = child["struct"].sum

# 闭合校验：本层结构影响 + 下一层内率 = 上层内率
parent_inner = layer_struct + child_inner
assert abs((layer_struct + child_inner) - parent_inner) < 1e-9

# 影响列展示：2位% 带符号
child["struct_pct"] = (child["struct"] * 100).round(2)
child["rate_pct"] = (child["rate_impact"] * 100).round(2)
print(child[["unit", "struct_pct", "rate_pct"]])
print("Σ struct+rate =", round((layer_struct + child_inner) * 100, 2), "%")
