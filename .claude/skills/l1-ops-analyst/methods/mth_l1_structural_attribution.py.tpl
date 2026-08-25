import pandas as pd

def structural_attribution(
    base: pd.DataFrame,      # 基期 (含 [招生量, 续报量])
    curr: pd.DataFrame,      # 现期 (含 [招生量, 续报量])
    group_keys: list[str],   # 分组维度，如 ['sku'] 或 ['sku', '招生分配类型']
) -> pd.DataFrame:
    """返回各分组的结构影响 / 续报率影响 / 总影响。"""
    # 1) 聚合到分组粒度
    b = base.groupby(group_keys, as_index=False).agg(基期招生量=('招生量','sum'), 基期续报量=('续报量','sum'))
    c = curr.groupby(group_keys, as_index=False).agg(现期招生量=('招生量','sum'), 现期续报量=('续报量','sum'))
    df = b.merge(c, on=group_keys, how='outer').fillna(0)

    # 2) 招生占比与续报率
    base_total = df['基期招生量'].sum()
    curr_total = df['现期招生量'].sum()
    df['基期招生占比'] = df['基期招生量'] / base_total if base_total else 0
    df['现期招生占比'] = df['现期招生量'] / curr_total if curr_total else 0
    df['基期续报率'] = df.apply(lambda r: r['基期续报量']/r['基期招生量'] if r['基期招生量'] else 0, axis=1)
    df['现期续报率'] = df.apply(lambda r: r['现期续报量']/r['现期招生量'] if r['现期招生量'] else 0, axis=1)

    # 3) 新增分组保护：基期占比=0 且 现期占比>0 → 用现期续报率兜底
    df['归因用基期续报率'] = df.apply(
        lambda r: r['现期续报率'] if (r['基期招生占比'] == 0 and r['现期招生占比'] > 0) else r['基期续报率'],
        axis=1,
    )

    # 4) 整体续报率（基期 / 现期）
    base_overall_rate = df['基期续报量'].sum() / base_total if base_total else 0

    # 5) 结构影响 与 续报率影响
    df['招生占比变化'] = df['现期招生占比'] - df['基期招生占比']
    df['结构影响'] = df['招生占比变化'] * (df['归因用基期续报率'] - base_overall_rate)
    df['续报率影响'] = df['现期招生占比'] * (df['现期续报率'] - df['归因用基期续报率'])
    df['总影响'] = df['结构影响'] + df['续报率影响']

    return df.sort_values('总影响', ascending=True)  # 拖累场景升序；拉动场景调用方降序
