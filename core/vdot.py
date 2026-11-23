"""VDOT 与配速推算（丹尼尔斯/吉尔伯特公式体系）

采用的公式（Daniels & Gilbert）：
1) 跑动氧耗：VO2 = -4.60 + 0.182258 * v + 0.000104 * v^2   （v 单位 m/min）
2) 比赛强度占 VO2max 的比例：
   %VO2max = 0.80 + 0.1896 * e^(-0.01278 t) + 0.2989 * e^(-0.1932 t)  （t 单位 min）
3) VDOT = VO2(比赛速度) / %VO2max
4) 目标配速：给定目标强度百分比 p，反解 v 使 VO2 = p * VDOT
本模块不自造系数，全部由上述两条公式推导；文献核对点：5K 20:00 → VDOT≈50，5K 25:00 → VDOT≈38。
"""
import math

# 五个强度区间对应的 VO2max 百分比（Daniels 训练强度区间）
INTENSITY = {
    "E": {"pct": 0.65, "label": "E 轻松跑", "range": (0.59, 0.74)},
    "M": {"pct": 0.81, "label": "M 马拉松配速", "range": (0.75, 0.84)},
    "T": {"pct": 0.88, "label": "T 乳酸阈值", "range": (0.83, 0.88)},
    "I": {"pct": 1.00, "label": "I 间歇", "range": (0.95, 1.00)},
    "R": {"pct": 1.05, "label": "R 重复跑", "range": (1.00, 1.05)},
}

# 心率区间（储备心率百分比 HRR），仅作辅助校验，M/T 重叠时以配速为主
HR_ZONE = {
    "E": (59, 74),
    "M": (79, 85),
    "T": (80, 88),
    "I": (95, 100),
    "R": (100, 100),
    "LONG": (59, 74),
}


def vo2_from_velocity(v_m_per_min):
    """Daniels/Gilbert 跑动氧耗方程（ml/kg/min）。"""
    return -4.60 + 0.182258 * v_m_per_min + 0.000104 * v_m_per_min ** 2


def velocity_for_vo2(vo2):
    """反解给定氧耗对应的速度（m/min），取正根。"""
    a, b, c = 0.000104, 0.182258, -(4.60 + vo2)
    disc = b * b - 4 * a * c
    if disc < 0:
        return 0.0
    return (-b + math.sqrt(disc)) / (2 * a)


def pct_vo2max(duration_min):
    """比赛时长对应的可持续 %VO2max。"""
    t = max(1.0, duration_min)
    return 0.80 + 0.1896 * math.exp(-0.01278 * t) + 0.2989 * math.exp(-0.1932 * t)


def vdot_from_performance(distance_m, seconds):
    """由任意一次比赛/测试成绩推算 VDOT。"""
    if seconds <= 0 or distance_m <= 0:
        return 0.0
    t = seconds / 60.0
    v = distance_m / t
    return vo2_from_velocity(v) / pct_vo2max(t)


def vdot_from_5k(seconds):
    return vdot_from_performance(5000, seconds)


def riegel_equiv_time(distance_km, seconds, target_km=5.0, exponent=1.06):
    """Riegel 公式：t2 = t1 * (d2/d1)^1.06，用于把非 5K 成绩折算成等效 5K 成绩。"""
    if distance_km <= 0 or seconds <= 0:
        return 0
    return seconds * (target_km / distance_km) ** exponent


def pace_sec_per_km(vdot, intensity_key):
    """目标配速（秒/公里）。intensity_key 属于 INTENSITY。"""
    pct = INTENSITY[intensity_key]["pct"]
    v = velocity_for_vo2(pct * vdot)
    if v <= 0:
        return 0
    return int(round(60000 / v))


def pace_range(vdot, intensity_key):
    lo, hi = INTENSITY[intensity_key]["range"]
    fast = pace_sec_per_km_from_pct(vdot, hi)   # 强度越高配速越快
    slow = pace_sec_per_km_from_pct(vdot, lo)
    return fast, slow


def pace_sec_per_km_from_pct(vdot, pct):
    v = velocity_for_vo2(pct * vdot)
    return int(round(60000 / v)) if v > 0 else 0


def fmt_pace(sec):
    if not sec:
        return "—"
    return f"{sec // 60}′{sec % 60:02d}″"


def fmt_time(seconds):
    if not seconds:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def zone_table(vdot):
    """返回五区配速表，供页面展示。"""
    rows = []
    for key in ("E", "M", "T", "I", "R"):
        fast, slow = pace_range(vdot, key)
        rows.append({
            "key": key,
            "label": INTENSITY[key]["label"],
            "pct": f"{INTENSITY[key]['range'][0] * 100:.0f}%–{INTENSITY[key]['range'][1] * 100:.0f}% VO2max",
            "pace": f"{fmt_pace(fast)}–{fmt_pace(slow)}/km",
        })
    return rows
