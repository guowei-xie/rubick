import pandas as pd

FUNNEL_ORDER = ['加微率', '加微首到率', '首到留存率', '完课转化率']

def chain_substitution(base_rates: dict[str, float], curr_rates: dict[str, float]) -> pd.DataFrame:
    """连环替代法。base_rates / curr_rates 形如 {'加微率': 0.32, ...}。
    返回 DataFrame: [环节, 基期值, 现期值, 影响值（对期内续报率）]。"""
    base = [base_rates[k] for k in FUNNEL_ORDER]
    curr = [curr_rates[k] for k in FUNNEL_ORDER]

    def product(arr):
        p = 1.0
        for x in arr:
            p *= x
        return p

    rows = []
    state = base.copy()
    prev_rate = product(state)
    for i, name in enumerate(FUNNEL_ORDER):
        state[i] = curr[i]
        cur_rate = product(state)
        rows.append({
            '环节': name,
            '基期值': base[i],
            '现期值': curr[i],
            '期内续报率影响': cur_rate - prev_rate,
        })
        prev_rate = cur_rate

    df = pd.DataFrame(rows)
    df = df.reindex(df['期内续报率影响'].abs().sort_values(ascending=False).index)
    return df.reset_index(drop=True)


def aggregate_rates_from_counts(counts: pd.DataFrame) -> dict[str, float]:
    """counts 含字段：实际招生量、微信添加量、首课到课量、4课完课量、期内续报量。
    返回聚合后重算的 4 个环节率（分母为 0 时该环节 None，调用方应标 不可验证）。"""
    def safe_div(n, d):
        return (n / d) if d else None
    return {
        '加微率': safe_div(counts['微信添加量'].sum(), counts['实际招生量'].sum()),
        '加微首到率': safe_div(counts['首课到课量'].sum(), counts['微信添加量'].sum()),
        '首到留存率': safe_div(counts['4课完课量'].sum(), counts['首课到课量'].sum()),
        '完课转化率': safe_div(counts['期内续报量'].sum(), counts['4课完课量'].sum()),
    }
