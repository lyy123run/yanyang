"""16 周马拉松课表生成（丹尼尔斯静态框架）+ 两周校准期

结构：每周 5 个训练日 + 2 个休息日（周一/周五休息）
阶段：基础期 W1-4 / 提升期 W5-9 / 峰值期 W10-14 / 减量期 W15-16
渐进负荷：周跑量每周 +5%，第 4/8/12 周减载 20%，最后两周减量
校准期：前两周用 VDOT-2 的保守配速排课（自报 5K 成绩可能偏高）
"""
from datetime import date, timedelta

from . import vdot as vd
from .models import Plan, Workout

PHASES = [(1, 4, "基础期"), (5, 9, "提升期"), (10, 14, "峰值期"), (15, 16, "减量期")]

DETAIL = {
    "E": "全程 E 配速，能正常对话；跑后拉伸 10 分钟。",
    "T": "热身 2km + 3×8 分钟 T 配速（间歇 2 分钟慢跑）+ 冷身 2km。",
    "I": "热身 3km + 5×1000m I 配速（间歇 400m 慢跑）+ 冷身 2km。",
    "R": "热身 3km + 6×200m R 配速（间歇 200m 慢跑）+ 冷身 2km。",
    "M": "热身 2km + M 配速持续跑 + 冷身 2km。",
    "LONG": "长距离 E 配速，最后 3km 可提至 M 配速，注意补水。",
    "REST": "完全休息或散步 20 分钟，保证睡眠。",
    "XT": "交叉训练 40 分钟（游泳/单车），保持有氧不增加冲击负荷。",
}


def phase_of(week_no):
    for lo, hi, name in PHASES:
        if lo <= week_no <= hi:
            return name
    return "备赛期"


def weekly_volume(base_km, week_no, weeks=16):
    if week_no >= weeks - 1:
        return round(base_km * 1.35, 1)          # 第 15 周：明显减量
    if week_no >= weeks:
        return round(base_km * 0.95, 1)          # 第 16 周：比赛周
    vol = base_km * (1 + 0.05 * (week_no - 1))
    if week_no in (4, 8, 12):                    # 减载周
        vol *= 0.8
    return round(vol, 1)


def sessions_for(week_no):
    """返回 (日偏移, 课型) 列表，周一为偏移 0。"""
    if week_no == 16:
        return [(0, "REST"), (1, "E"), (2, "REST"), (3, "E"), (4, "REST"),
                (5, "REST"), (6, "M")]  # 周日比赛
    if week_no <= 3:
        return [(0, "REST"), (1, "T"), (2, "E"), (3, "E"), (4, "REST"), (5, "E"), (6, "LONG")]
    if week_no == 4:
        return [(0, "REST"), (1, "T"), (2, "E"), (3, "E"), (4, "REST"), (5, "E"), (6, "LONG")]
    if week_no <= 9:
        return [(0, "REST"), (1, "I"), (2, "E"), (3, "T"), (4, "REST"), (5, "E"), (6, "LONG")]
    if week_no <= 14:
        return [(0, "REST"), (1, "I"), (2, "E"), (3, "M"), (4, "REST"), (5, "E"), (6, "LONG")]
    return [(0, "REST"), (1, "T"), (2, "E"), (3, "E"), (4, "REST"), (5, "E"), (6, "LONG")]


def _split_distances(total, week_no):
    """按课型分配周跑量：长距离占比随阶段提高。"""
    if week_no == 16:
        return {"M": 42.195, "E1": 8.0, "E2": 6.0}
    long_ratio = 0.30 if week_no <= 4 else (0.34 if week_no <= 9 else 0.38)
    if week_no <= 4 or week_no >= 15:
        rest = (1 - long_ratio - 0.14) / 3
        out = {"LONG": total * long_ratio, "T": total * 0.14,
               "E1": total * rest, "E2": total * rest, "E3": total * rest}
    elif week_no <= 9:
        out = {"LONG": total * long_ratio, "I": total * 0.13, "T": total * 0.14,
               "E1": total * 0.15, "E2": total * 0.13}
    else:
        out = {"LONG": total * long_ratio, "I": total * 0.12, "M": total * 0.15,
               "E1": total * 0.15, "E2": total * 0.13}
    # 归一到周跑量
    s = sum(out.values())
    return {k: round(v * total / s, 1) for k, v in out.items()}


def build_plan(user, profile, start_date, weeks=16, base_km=None):
    base_km = base_km or profile.weekly_base_km or 40.0
    five_k = profile.five_k_seconds
    vdot_value = vd.vdot_from_5k(five_k) if five_k else 0
    if vdot_value <= 0:
        raise ValueError("请先填写 5K 成绩")

    Plan.objects.filter(user=user, status="active").update(status="archived")
    plan = Plan.objects.create(
        user=user, start_date=start_date, weeks=weeks, vdot=round(vdot_value, 1),
        five_k_seconds=five_k, weekly_base_km=base_km,
    )

    objs = []
    for w in range(1, weeks + 1):
        total = weekly_volume(base_km, w, weeks)
        splits = _split_distances(total, w)
        keys = sorted([k for k in splits if k not in ("LONG", "I", "T", "M")])
        day_plan = sessions_for(w)
        alloc_map = {"LONG": "LONG", "I": "I", "T": "T", "M": "M",
                     "E1": keys[0] if keys else "E1",
                     "E2": keys[1] if len(keys) > 1 else keys[0] if keys else "E1",
                     "E3": keys[2] if len(keys) > 2 else keys[-1] if keys else "E1"}
        easy_seq = 0
        for offset, wtype in day_plan:
            d = start_date + timedelta(days=7 * (w - 1) + offset)
            if wtype == "REST":
                km, pace = 0.0, 0
            elif w == 16:
                if wtype == "M":
                    km, pace = 42.195, vd.pace_sec_per_km(vdot_value, "M")
                else:
                    easy_seq += 1
                    km = splits.get("E1" if easy_seq == 1 else "E2", 6.0)
                    pace = vd.pace_sec_per_km(vdot_value, "E")
            elif wtype == "E":
                easy_seq += 1
                key = f"E{easy_seq}"
                if key not in splits:
                    key = f"E{len(keys)}" if len(keys) >= 1 else "E1"
                km = splits.get(key, splits.get("E1", 0))
                pace = vd.pace_sec_per_km(max(vdot_value - 2, 20) if w <= 2 else vdot_value, "E")
            elif wtype == "LONG":
                km = splits.get("LONG", 0)
                pace = vd.pace_sec_per_km(max(vdot_value - 2, 20) if w <= 2 else vdot_value, "E")
            elif wtype == "M" and w == 16:
                km = 42.195
                pace = vd.pace_sec_per_km(vdot_value, "M")
            else:
                km = splits.get(wtype, 0)
                pace = vd.pace_sec_per_km(max(vdot_value - 2, 20) if w <= 2 else vdot_value, wtype)

            detail = DETAIL.get(wtype, "")
            if w == 16 and wtype == "M":
                detail = "马拉松比赛日：按 M 配速执行，注意赛前 3 小时进食。"
            if w <= 2 and wtype != "REST":
                detail += "（校准期：配速按保守值排，两周后按实际数据自动修正）"
            duration = int(round(km * pace / 60)) if pace else 0

            objs.append(Workout(
                user=user, plan=plan, date=d, week_no=w, phase=phase_of(w),
                wtype=wtype, distance_km=round(km, 1), pace_sec=pace,
                duration_min=duration, detail=detail,
            ))
    Workout.objects.bulk_create(objs)
    return plan


def recalibrate(plan, profile):
    """两周校准：用校准期内最好的一次等效 5K 表现修正 VDOT（限制 ±3），重建剩余课表配速。"""
    from .models import Record
    from .vdot import riegel_equiv_time, vdot_from_5k

    recs = Record.objects.filter(user=plan.user, date__gte=plan.start_date,
                                 date__lte=plan.start_date + timedelta(days=13),
                                 distance_km__gte=3.0)
    if not recs:
        return None

    best = None
    for r in recs:
        if r.duration_min <= 0:
            continue
        eq = riegel_equiv_time(r.distance_km, r.duration_min * 60)
        v = vdot_from_5k(eq)
        if best is None or v > best[0]:
            best = (v, r)
    if not best:
        return None
    est = best[0]
    delta = max(-3.0, min(3.0, est - plan.vdot))
    new_vdot = round(plan.vdot + delta, 1)
    plan.vdot = new_vdot
    plan.calibrated = True
    plan.save(update_fields=["vdot", "calibrated"])

    for w in Workout.objects.filter(plan=plan, is_adjusted=False, date__gt=plan.start_date + timedelta(days=13)):
        if not w.pace_sec:
            continue
        key = "E" if w.wtype in ("E", "LONG") else w.wtype
        if key in vd.INTENSITY:
            w.pace_sec = vd.pace_sec_per_km(new_vdot, key)
            w.duration_min = int(round(w.distance_km * w.pace_sec / 60))
            w.save(update_fields=["pace_sec", "duration_min"])
    return {"before": plan.vdot - delta, "after": new_vdot, "estimate": round(est, 1),
            "record": best[1]}
