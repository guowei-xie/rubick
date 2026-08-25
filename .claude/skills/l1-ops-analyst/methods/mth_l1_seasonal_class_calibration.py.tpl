import pandas as pd


def seasonal_calibrate(
    r_cur: float, r_prev: float,        # 本期 / 上期率
    r_ly_cur: float, r_ly_prev: float,  # 去年本期 / 去年上期率
    tol: float = 0.001,                 # 幅度"相当"阈值(率单位, 0.001=0.10%)
) -> dict:
    """季节性班型双判: 幅度对比 + 绝对水平对比。率先汇总再相除后传入。"""
    for v in (r_cur, r_prev, r_ly_cur, r_ly_prev):
        if pd.isna(v):
            return {"判定": "N/A", "说明": "存在缺失/分母为0, 不做季节校准"}
    d_cur = r_cur - r_prev
    d_ly = r_ly_cur - r_ly_prev
    yoy_gap = r_cur - r_ly_cur
    seasonal = abs(d_cur - d_ly) <= tol
    if seasonal and yoy_gap >= 0:
        verdict = "季节性正常回升, 且已达/超去年水平"
    elif seasonal and yoy_gap < 0:
        verdict = "季节性正常回升, 但绝对水平未恢复到去年, 需补同比缺口"
    else:
        verdict = "环比幅度超出季节性常态, 含运营真实变化(方向存疑,需观察)"
    return {
        "环比幅度": d_cur, "去年同期幅度": d_ly,
        "同比缺口": yoy_gap, "属季节性": seasonal, "判定": verdict,
    }
