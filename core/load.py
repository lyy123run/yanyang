"""训练负荷与疲劳指标：TRIMP / CTL / ATL / TSB / ACWR

公式：
- Banister TRIMP：TRIMP = t * ΔHR * a * e^(b * ΔHR)
  男 a=0.64 b=1.92；女 a=0.86 b=1.67；ΔHR 为储备心率百分比（Karvonen）。
- CTL42 = TRIMP 的 42 天指数移动平均；CTL28 = 28 天（用于 ACWR）
- ATL7 = 7 天指数移动平均
- TSB = CTL42 - ATL7
- ACWR（Gabbett）= ATL7 / CTL28
缺失心率时用 sRPE = 时长(分钟) * RPE 替代，并标记 load_method='srpe'
"""
import math
from collections import OrderedDict
from datetime import date, timedelta

TRIMP_COEF = {"M": (0.64, 1.92), "F": (0.86, 1.67)}


def hrr_pct(avg_hr, hr_rest, hr_max):
    """储备心率百分比（0~1 区间之外做截断保护）。"""
    if not avg_hr:
        return None
    denom = max(1.0, hr_max - hr_rest)
    return max(0.0, min(1.0, (avg_hr - hr_rest) / denom))


def trimp(duration_min, avg_hr, hr_rest, hr_max, gender="M"):
    if not avg_hr or duration_min <= 0:
        return 0.0
    d = hrr_pct(avg_hr, hr_rest, hr_max)
    a, b = TRIMP_COEF.get(gender, TRIMP_COEF["M"])
    return round(duration_min * d * a * math.exp(b * d), 2)


def srpe(duration_min, rpe):
    return round(max(0, duration_min) * max(1, min(10, rpe)), 2)


def daily_load_series(records, start=None, end=None):
    """把打卡记录聚合成按天的负荷序列 {date: load}，缺失日补 0。"""
    by_day = {}
    for r in records:
        if r.date is None:
            continue
        by_day[r.date] = by_day.get(r.date, 0.0) + (r.trimp or 0.0)
    if not by_day:
        return OrderedDict()
    start = start or min(by_day)
    end = end or max(by_day)
    series = OrderedDict()
    d = start
    while d <= end:
        series[d] = round(by_day.get(d, 0.0), 2)
        d += timedelta(days=1)
    return series


def ewma(series, tau, init=None):
    """指数移动平均，alpha = 1 - e^(-1/tau)，等价于 CTL_t = CTL_(t-1) + (load_t - CTL_(t-1))/tau 的连续形式。

    冷启动处理：新用户没有历史负荷时，CTL 从 0 起算会长时间为负偏差（TSB 虚低），
    因此用前 7 天平均负荷作为初值（init），页面对数据不足 28 天的指标标注“估算值”。
    """
    alpha = 1 - math.exp(-1.0 / tau)
    out = OrderedDict()
    prev = float(init or 0.0)
    for d, load in series.items():
        prev = prev + (load - prev) * alpha if prev else load * alpha
        out[d] = round(prev, 2)
    return out


def load_metrics(records, end_date=None):
    """返回当日指标与完整曲线。数据不足 14 天时返回 estimated 标记。"""
    series = daily_load_series(records, end=end_date)
    if not series:
        return {
            "ctl42": 0, "ctl28": 0, "atl7": 0, "tsb": 0, "acwr": 0,
            "days": 0, "estimated": True, "curve": [],
            "acwr_high_streak": 0, "acwr_zone": "无数据",
        }
    first = list(series.values())[:7]
    seed = sum(first) / len(first) if first else 0.0
    seed28 = sum(list(series.values())[:28]) / max(1, len(list(series.values())[:28]))
    ctl42 = ewma(series, 42, seed)
    ctl28 = ewma(series, 28, seed28)
    atl7 = ewma(series, 7, seed)
    days = len(series)
    end = list(series)[-1]

    tsb = ctl42[end] - atl7[end]
    acwr = (atl7[end] / ctl28[end]) if ctl28[end] else 0.0

    # 连续“ACWR>1.5 且 TSB<-20”天数（R04 事实）
    streak = 0
    for d in reversed(list(series)):
        a = (atl7[d] / ctl28[d]) if ctl28[d] else 0.0
        if a > 1.5 and (ctl42[d] - atl7[d]) < -20:
            streak += 1
        else:
            break

    if acwr == 0:
        zone = "无数据"
    elif acwr < 0.8:
        zone = "偏低（负荷不足）"
    elif acwr <= 1.3:
        zone = "安全区间 0.8–1.3"
    elif acwr <= 1.5:
        zone = "偏高"
    else:
        zone = "危险 >1.5"

    curve = [
        {"date": d.isoformat(), "ctl42": ctl42[d], "ctl28": ctl28[d], "atl7": atl7[d],
         "tsb": round(ctl42[d] - atl7[d], 2), "load": series[d]}
        for d in series
    ]
    return {
        "ctl42": ctl42[end], "ctl28": ctl28[end], "atl7": atl7[end],
        "tsb": round(tsb, 2), "acwr": round(acwr, 2), "days": days,
        "estimated": days < 28, "curve": curve,
        "acwr_high_streak": streak, "acwr_zone": zone, "end": end,
    }


def week_actual_km(records, week_start, week_end):
    return round(sum(r.distance_km for r in records
                     if week_start <= r.date <= week_end), 2)
