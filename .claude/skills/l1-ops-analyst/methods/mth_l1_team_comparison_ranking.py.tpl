import pandas as pd


def rank_teams_structure_corrected(
    df: pd.DataFrame,          # 明细: [team, class, 招生量, 续报量]
    team_key: str = "team",
    class_key: str = "class",
) -> pd.DataFrame:
    """直接标准化剥离班型结构差异后, 按同口径率排名。率先汇总再相除。"""
    g = df.groupby([team_key, class_key], as_index=False).agg(
        招生量=("招生量", "sum"), 续报量=("续报量", "sum"))
    g["率"] = g.apply(
        lambda r: r["续报量"] / r["招生量"] if r["招生量"] else float("nan"),
        axis=1)

    # 统一参照结构 = 全池各班型招生占比
    pool = g.groupby(class_key)["招生量"].sum()
    w_base = (pool / pool.sum()).rename("w_base")

    def corrected(sub):
        s = sub.merge(w_base, left_on=class_key, right_index=True, how="left")
        s = s.dropna(subset=["率"])
        if not len(s):
            return float("nan")
        return (s["w_base"] * s["率"]).sum() / s["w_base"].sum()

    def raw_rate(s):
        denom = s["招生量"].sum()
        return s["续报量"].sum() / denom if denom else float("nan")

    raw = g.groupby(team_key).apply(raw_rate)
    out = pd.DataFrame({"原始率": raw})
    out["结构修正后率"] = g.groupby(team_key).apply(corrected)
    out["结构影响"] = out["原始率"] - out["结构修正后率"]
    out = out.sort_values("结构修正后率", ascending=False)
    out["排名"] = range(1, len(out) + 1)
    return out.reset_index()
