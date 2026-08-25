"""订阅计划 due 计算(纯函数):latest_planned_at 返回「≤ now 的最近一个计划时刻」。

月末顺延、跨月回溯、跨零点这些边界全在这里钉死 —— 调度器(tick)只消费这个函数的返回值,
函数对了,水位比较那半边只剩「大于就 fire」一条规则。
"""
from datetime import datetime

from app.services.subscription_service import describe_schedule, latest_planned_at


# ---------------------------------------------------------------- daily


def test_daily_after_time_is_today():
    now = datetime(2026, 8, 24, 10, 0)
    assert latest_planned_at("daily", [], "09:30", now) == datetime(2026, 8, 24, 9, 30)


def test_daily_before_time_falls_back_to_yesterday():
    now = datetime(2026, 8, 24, 8, 0)
    assert latest_planned_at("daily", [], "09:30", now) == datetime(2026, 8, 23, 9, 30)


def test_daily_exact_time_counts():
    now = datetime(2026, 8, 24, 9, 30)
    assert latest_planned_at("daily", [], "09:30", now) == now


def test_daily_crosses_midnight():
    now = datetime(2026, 9, 1, 0, 10)
    assert latest_planned_at("daily", [], "23:50", now) == datetime(2026, 8, 31, 23, 50)


# ---------------------------------------------------------------- weekly


def test_weekly_picks_most_recent_of_multiple_days():
    # 2026-08-24 是周一;选了周一、周四,now=周三 → 最近一期是本周一
    now = datetime(2026, 8, 26, 12, 0)
    assert latest_planned_at("weekly", [1, 4], "09:00", now) == datetime(2026, 8, 24, 9, 0)


def test_weekly_today_but_time_not_reached_uses_previous_hit():
    # now=周四 08:00,时刻 09:00 未到 → 退回本周一
    now = datetime(2026, 8, 27, 8, 0)
    assert latest_planned_at("weekly", [1, 4], "09:00", now) == datetime(2026, 8, 24, 9, 0)


def test_weekly_single_day_wraps_to_last_week():
    # 只选周五,now=周一 → 上周五
    now = datetime(2026, 8, 24, 12, 0)
    assert latest_planned_at("weekly", [5], "18:00", now) == datetime(2026, 8, 21, 18, 0)


def test_weekly_empty_days_never_fires():
    assert latest_planned_at("weekly", [], "09:00", datetime(2026, 8, 24, 12, 0)) is None


# ---------------------------------------------------------------- monthly(含小月顺延)


def test_monthly_plain_day():
    now = datetime(2026, 8, 24, 12, 0)
    assert latest_planned_at("monthly", [15], "09:00", now) == datetime(2026, 8, 15, 9, 0)


def test_monthly_day_not_reached_falls_back_to_last_month():
    now = datetime(2026, 8, 10, 12, 0)
    assert latest_planned_at("monthly", [15], "09:00", now) == datetime(2026, 7, 15, 9, 0)


def test_monthly_31_clamps_to_february_end():
    # 选 31 号,now=3 月初 → 上一期是 2 月,顺延到 2/28(2026 非闰年)
    now = datetime(2026, 3, 1, 12, 0)
    assert latest_planned_at("monthly", [31], "09:00", now) == datetime(2026, 2, 28, 9, 0)


def test_monthly_31_clamps_to_30_day_month():
    # 选 31 号,now=5 月初 → 上一期是 4 月,顺延到 4/30
    now = datetime(2026, 5, 1, 12, 0)
    assert latest_planned_at("monthly", [31], "09:00", now) == datetime(2026, 4, 30, 9, 0)


def test_monthly_30_and_31_clamp_to_same_day_dedupes():
    # 30、31 在 2 月都顺延到 2/28 —— 同一个时刻,水位比较天然去重,不会跑两次
    now = datetime(2026, 3, 1, 12, 0)
    assert latest_planned_at("monthly", [30, 31], "09:00", now) == datetime(2026, 2, 28, 9, 0)


def test_monthly_multiple_days_picks_latest_past():
    # 选 1、15 号,now=8/20 → 最近一期是 8/15
    now = datetime(2026, 8, 20, 12, 0)
    assert latest_planned_at("monthly", [1, 15], "09:00", now) == datetime(2026, 8, 15, 9, 0)


def test_monthly_january_wraps_to_previous_year_december():
    now = datetime(2026, 1, 5, 12, 0)
    assert latest_planned_at("monthly", [20], "09:00", now) == datetime(2025, 12, 20, 9, 0)


def test_monthly_empty_days_never_fires():
    assert latest_planned_at("monthly", [], "09:00", datetime(2026, 8, 24, 12, 0)) is None


# ---------------------------------------------------------------- 非法配置 fail-closed


def test_invalid_at_time_never_fires():
    now = datetime(2026, 8, 24, 12, 0)
    assert latest_planned_at("daily", [], "9点半", now) is None
    assert latest_planned_at("daily", [], "", now) is None
    assert latest_planned_at("daily", [], None, now) is None


def test_unknown_freq_never_fires():
    assert latest_planned_at("hourly", [], "09:00", datetime(2026, 8, 24, 12, 0)) is None


def test_out_of_range_days_are_ignored():
    # weekly 里混进 0/8 一律忽略;全非法 = 永不触发
    now = datetime(2026, 8, 26, 12, 0)
    assert latest_planned_at("weekly", [0, 8], "09:00", now) is None
    assert latest_planned_at("weekly", [0, 1], "09:00", now) == datetime(2026, 8, 24, 9, 0)


# ---------------------------------------------------------------- 中文描述


def test_describe_schedule_wording():
    class S:  # 只要鸭子型的四个属性
        enabled = True
        freq = "weekly"
        days = [4, 1]
        at_time = "09:00"

    assert describe_schedule(S()) == "每周一、四 09:00"
    S.freq, S.days = "daily", []
    assert describe_schedule(S()) == "每天 09:00"
    S.freq, S.days, S.at_time = "monthly", [1, 15], "07:30"
    assert describe_schedule(S()) == "每月1日、15日 07:30"
    S.enabled = False
    assert describe_schedule(S()) is None
    assert describe_schedule(None) is None
