import pandas as pd
from datetime import date

def flow_next_maturity_adjustment(
    actual: pd.DataFrame,        # market_sales_actual
    target: pd.DataFrame,         # market_sales_target，含下一期续报结束时间
    channel: str,                 # 大班型_渠道来源
    terms: list[str],             # 分析期次（逐期判断）
    today: date,                  # 执行当天
) -> pd.DataFrame:
    """返回与 actual 同粒度的 market_sales_actual_flow_adjusted；不成熟期次行带预测补充续报量。"""
    # 1) 过滤范围
    a = actual[(actual['大班型_渠道来源'] == channel) & (actual['学期期次'].isin(terms))].copy()

    # 2) 逐期判断成熟度
    next_end = target.set_index('学期期次')['下一期续报结束时间']
    for term in terms:
        sub = a[a['学期期次'] == term]
        flow_to_next = sub.loc[sub['招生流向类型'] == '流入下一期', '实际招生量'].sum()
        total = sub['实际招生量'].sum()
        ratio = flow_to_next / total if total else 0
        end = next_end.get(term)
        if ratio > 0 and end is not None and end >= today:
            mark = '不成熟'
        else:
            mark = '成熟'
        a.loc[a['学期期次'] == term, '流入下一期成熟度判断'] = mark

    # 3) 对不成熟期次估算 班型预估续报率（4 级降级）
    def estimate_renewal_rate(row) -> tuple[float, str]:
        # 级 1: 同期次同定标班型非流入下一期成熟样本
        c1 = a[(a['学期期次'] == row['学期期次']) & (a['定标班型'] == row['定标班型']) & (a['招生流向类型'] != '流入下一期')]
        if c1['实际招生量'].sum() > 0:
            return (c1['期内续报量'].sum() / c1['实际招生量'].sum(), '同期次同班型成熟样本')
        # 级 2: 同渠道同定标班型最近成熟期次
        ...
        # 级 3: 同 sku+业务核算组+定标渠道类型 整体
        ...
        # 级 4: 当前渠道整体成熟样本
        return (a['期内续报量'].sum() / a['实际招生量'].sum(), '渠道整体兜底')

    # 4) 计算预测补充续报量 + 下限保护
    a['预计补充续报量'] = 0.0
    a['预测修正口径'] = ''
    mask_immature_flow = (a['流入下一期成熟度判断'] == '不不成熟') & (a['招生流向类型'] == '流入下一期')
    for idx, row in a[mask_immature_flow].iterrows():
        rate, source = estimate_renewal_rate(row)
        predicted = row['实际招生量'] * rate
        supplement = max(predicted - row['期内续报量'], 0)
        a.at[idx, '预计补充续报量'] = supplement
        a.at[idx, '预测修正口径'] = source

    a['预测修正后期内续报量'] = a['期内续报量'] + a['预计补充续报量']

    # 5) 下限保护：在预测生成阶段，按 (期次 × sku × 业务核算组 × 定标渠道类型 × 定标班型) 粒度校验
    #    修正后续报率 < 观测续报率 时，把该粒度预计补充续报量降到使 修正后=观测后
    ...

    a['generated_date'] = today
    return a
