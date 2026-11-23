"""轻量级产生式规则引擎（事实采集 → 谓词匹配 → 冲突消解 → 动作执行 → 解释记录）

设计要点（与开题报告一致）：
1. 事实不硬编码：每次打卡后从 Record/Workout 实时聚合；
2. 规则存 Rule 表，condition_json 为结构化条件树（and/or/not/叶子比较），阈值可在后台改；
3. 逐条匹配，不提前 break，命中进入冲突集；
4. 冲突消解：安全规则（safety_flag=1）全部执行（按 priority 降序）；非安全规则按作用尺度分组，
   同尺度取优先级最高者；周尺度若出现“加量/降量”方向冲突，取更保守一侧（跑量最小）；
5. 所有命中规则（含未执行的）都写 ExplanationRecord，形成可解释闭环；
6. 触发后新增 adjusted 版本 Workout，不覆盖原计划。
"""
import json
import logging
from datetime import timedelta

from . import load as load_mod
from . import vdot as vd
from .models import ExplanationRecord, Record, Rule, Workout

logger = logging.getLogger(__name__)

OPS = {
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "between": lambda a, b: a is not None and b[0] <= a <= b[1],
    "in": lambda a, b: a in b,
}


# ---------------------------------------------------------------- 条件树求值
def eval_condition(node, facts, detail_out=None):
    """递归求值条件树，同时把叶子比较的实际数值收集到 detail_out（用于生成可读解释）。"""
    if not node:
        return False
    if isinstance(node, list):
        return all(eval_condition(n, facts, detail_out) for n in node)
    if "and" in node:
        return all(eval_condition(n, facts, detail_out) for n in node["and"])
    if "or" in node:
        results = [eval_condition(n, facts, detail_out) for n in node["or"]]
        return any(results)
    if "not" in node:
        return not eval_condition(node["not"], facts, detail_out)
    if "fact" in node:
        fact = node["fact"]
        op = node.get("op", "eq")
        value = node.get("value")
        actual = facts.get(fact)
        try:
            ok = bool(OPS.get(op, OPS["eq"])(actual, value))
        except Exception:
            ok = False
        if ok and detail_out is not None:
            detail_out.append(_leaf_text(node, actual))
        return ok
    return False


def _leaf_text(node, actual):
    op_cn = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "=", "ne": "≠",
             "between": "∈", "in": "∈"}
    op = op_cn.get(node.get("op"), node.get("op"))
    val = node.get("value")
    if isinstance(actual, bool):
        actual_txt = "是" if actual else "否"
    elif isinstance(actual, float):
        actual_txt = f"{actual:.2f}"
    else:
        actual_txt = str(actual)
    return f"{node.get('label', node['fact'])}={actual_txt}{op}{val}"


def render_condition(node):
    """把条件树转成可读表达式（后台展示用）。"""
    if not node:
        return "—"
    if isinstance(node, list):
        return " 且 ".join(render_condition(n) for n in node)
    if "and" in node:
        return "(" + " 且 ".join(render_condition(n) for n in node["and"]) + ")"
    if "or" in node:
        return "(" + " 或 ".join(render_condition(n) for n in node["or"]) + ")"
    if "not" in node:
        return "非(" + render_condition(node["not"]) + ")"
    if "fact" in node:
        op_cn = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "=", "ne": "≠",
                 "between": "∈", "in": "∈"}
        return f"{node.get('label', node['fact'])} {op_cn.get(node.get('op'), node.get('op'))} {node.get('value')}"
    return "—"


# ---------------------------------------------------------------- 事实采集
def _consecutive(records, predicate):
    n = 0
    for r in sorted(records, key=lambda x: (x.date, x.id), reverse=True):
        if predicate(r):
            n += 1
        else:
            break
    return n


def build_facts(record, plan, profile):
    date = record.date
    user = record.user
    week_no = ((date - plan.start_date).days // 7) + 1
    week_start = plan.start_date + timedelta(days=7 * (week_no - 1))
    week_end = week_start + timedelta(days=6)
    today_w = record.workout or Workout.objects.filter(
        user=user, date=date, is_adjusted=False).order_by("id").first()
    today_type = today_w.wtype if today_w else "E"

    recs = list(Record.objects.filter(user=user, date__lte=date).order_by("date", "id"))
    metrics = load_mod.load_metrics(recs, end_date=date)
    this_week_actual = load_mod.week_actual_km(recs, week_start, week_end)

    next_week_qs = Workout.objects.filter(plan=plan, week_no=week_no + 1, is_adjusted=False)
    next_week_plan = round(sum(w.distance_km for w in next_week_qs), 2)

    weekly_growth = ((next_week_plan - this_week_actual) / this_week_actual
                     if this_week_actual > 0 else 0.0)

    # 48 小时内是否有 I/R 强度课（不含本次）
    intensity_48h = any(
        r.workout and r.workout.wtype in ("I", "R") and r.id != record.id
        and date - timedelta(days=2) <= r.date < date
        for r in recs
    )

    hr_pct = (load_mod.hrr_pct(record.avg_hr, profile.hr_rest, profile.hr_max) or 0) * 100
    zone_key = "LONG" if today_type == "LONG" else today_type
    hr_upper = vd.HR_ZONE.get(zone_key, (59, 100))[1]

    def incomplete(r):
        return (r.completion_rate or 1) < 0.8 or (r.rpe or 0) >= 9

    last14 = [r for r in recs if r.date > date - timedelta(days=14)]
    completion_2w = (sum(min(1.0, r.completion_rate or 1) for r in last14) / len(last14)
                     if last14 else 0)

    facts = {
        "date": date.isoformat(),
        "week_no": week_no,
        "today_type": today_type,
        "is_easy_m": today_type in ("E", "M", "LONG"),
        "hr_hrr_pct": round(hr_pct, 1),
        "hr_plan_upper": hr_upper,
        "hr_over_plan_delta": round(hr_pct - hr_upper, 1),
        "has_hr": 1 if record.avg_hr else 0,
        "rpe": record.rpe,
        "completion_rate": round(record.completion_rate or 1, 3),
        "low_completion_streak": _consecutive(recs, incomplete),
        "intensity_within_48h": intensity_48h,
        "acwr": metrics["acwr"],
        "tsb": metrics["tsb"],
        "acwr_high_streak": metrics["acwr_high_streak"],
        "acwr_in_band": 0.8 <= (metrics["acwr"] or 0) <= 1.3,
        "ctl42": metrics["ctl42"],
        "atl7": metrics["atl7"],
        "weekly_growth": round(weekly_growth, 3),
        "this_week_actual_km": this_week_actual,
        "next_week_plan_km": next_week_plan,
        "completion_rate_2w": round(completion_2w, 3),
        "is_week_end": date.weekday() == 6,
        "safety_triggered": False,
    }
    return facts, metrics, week_no


# ---------------------------------------------------------------- 动作执行
def _upsert_adjusted(original, user, reason, **changes):
    adj = Workout.objects.filter(parent=original, is_adjusted=True).order_by("id").first()
    if adj is None:
        adj = Workout(user=user, plan=original.plan, date=original.date,
                      week_no=original.week_no, phase=original.phase,
                      wtype=original.wtype, distance_km=original.distance_km,
                      pace_sec=original.pace_sec, duration_min=original.duration_min,
                      detail=original.detail, is_adjusted=True, parent=original)
    for k, v in changes.items():
        setattr(adj, k, v)
    adj.adjust_reason = (reason or "")[:120]
    adj.duration_min = int(round(adj.distance_km * (adj.pace_sec or 0) / 60)) \
        if adj.distance_km and adj.pace_sec else 0
    adj.save()
    return adj


def _scale_week(plan, week_no, user, target_km, reason, vdot_val):
    originals = list(Workout.objects.filter(plan=plan, week_no=week_no,
                                            is_adjusted=False).exclude(wtype="REST"))
    total = sum(w.distance_km for w in originals)
    if total <= 0:
        return []
    ratio = target_km / total
    affected = []
    for w in originals:
        new_km = max(1.0, round(w.distance_km * ratio, 1))
        if abs(new_km - w.distance_km) < 0.05:
            continue
        key = "E" if w.wtype in ("E", "LONG") else w.wtype
        pace = vd.pace_sec_per_km(vdot_val, key) if key in vd.INTENSITY else w.pace_sec
        _upsert_adjusted(w, user, reason, distance_km=new_km, pace_sec=pace,
                         detail=f"{reason}：周跑量由 {total:.1f}km 调整为 {target_km:.1f}km，本课按比例调整。")
        affected.append(str(w.date))
    return affected


def _downgrade_today(record, plan, profile, reason, distance_factor=0.8):
    w = record.workout or Workout.objects.filter(user=record.user, date=record.date,
                                                 is_adjusted=False).order_by("id").first()
    if not w:
        return []
    new_km = max(1.0, round(w.distance_km * distance_factor, 1)) if w.distance_km else 0
    pace = vd.pace_sec_per_km(plan.vdot, "E")
    _upsert_adjusted(w, record.user, reason, wtype="E", distance_km=new_km, pace_sec=pace,
                     detail=f"{reason}：原计划 {w.type_label} {w.distance_km}km 降级为 E 配速轻松跑 {new_km}km。")
    return [str(w.date)]


def _force_rest_today(record, plan, profile, reason):
    w = record.workout or Workout.objects.filter(user=record.user, date=record.date,
                                                 is_adjusted=False).order_by("id").first()
    if not w:
        return []
    _upsert_adjusted(w, record.user, reason, wtype="REST", distance_km=0, pace_sec=0,
                     detail=f"{reason}：今日改为完全休息。")
    return [str(w.date)]


def _block_intensity_48h(record, plan, profile, reason):
    affected = []
    for w in Workout.objects.filter(user=record.user, plan=plan, is_adjusted=False,
                                    wtype__in=["I", "R", "T"],
                                    date__gt=record.date,
                                    date__lte=record.date + timedelta(days=2)):
        pace = vd.pace_sec_per_km(plan.vdot, "E")
        _upsert_adjusted(w, record.user, reason, wtype="E", pace_sec=pace,
                         detail=f"{reason}：48 小时内不安排高强度，{w.type_label} 改为 E 配速有氧跑。")
        affected.append(str(w.date))
    return affected


def _insert_rest(plan, week_no, user, reason):
    candidates = list(Workout.objects.filter(plan=plan, week_no=week_no, is_adjusted=False,
                                             wtype__in=["E"]).order_by("distance_km"))
    if not candidates:
        return []
    w = candidates[0]
    _upsert_adjusted(w, user, reason, wtype="REST", distance_km=0, pace_sec=0,
                     detail=f"{reason}：本周插入一个休息日，原 {w.distance_km}km 轻松跑取消。")
    return [str(w.date)]


def apply_action(rule, facts, record, plan, profile):
    """执行规则动作，返回 (动作说明, 受影响日期列表)。"""
    params = {}
    try:
        params = json.loads(rule.action_params or "{}")
    except ValueError:
        params = {}
    next_week = facts["week_no"] + 1
    originals = Workout.objects.filter(plan=plan, week_no=next_week, is_adjusted=False)
    original_total = sum(w.distance_km for w in originals)
    reason = f"{rule.code} {rule.name}"

    if rule.action_type == "reduce_next_week_volume":
        factor = float(params.get("factor", 0.9))
        target = round(original_total * factor, 1)
        affected = _scale_week(plan, next_week, record.user, target, reason, plan.vdot)
        text = f"第 {next_week} 周跑量按 {factor:.0%} 调整为 {target:.1f}km（原 {original_total:.1f}km）"
        if params.get("insert_rest"):
            affected += _insert_rest(plan, next_week, record.user, reason + "（插入休息日）")
            text += "，并插入 1 个休息日"
        return text, affected

    if rule.action_type == "cap_weekly_growth":
        cap = float(params.get("cap", 1.10))
        base = facts["this_week_actual_km"]
        target = round(min(base * cap, original_total), 1)
        affected = _scale_week(plan, next_week, record.user, target, reason, plan.vdot)
        return (f"下周计划跑量按“周增幅≤{(cap - 1):.0%}”上限下调至 {target:.1f}km"
                f"（本周实际 {base:.1f}km，原计划 {original_total:.1f}km）", affected)

    if rule.action_type == "increase_next_week_volume":
        factor = float(params.get("factor", 1.05))
        cap = float(params.get("cap", 1.10))
        target = round(min(original_total * factor, original_total * cap), 1)
        affected = _scale_week(plan, next_week, record.user, target, reason, plan.vdot)
        return (f"下周跑量上调 {factor - 1:.0%} 至 {target:.1f}km（上限为原计划 ×{cap:.0%}）", affected)

    if rule.action_type == "downgrade_today":
        factor = float(params.get("distance_factor", 0.8))
        affected = _downgrade_today(record, plan, profile, reason, factor)
        text = "当日课表降级为 E 配速轻松跑"
        if params.get("block_intensity", True):
            more = _block_intensity_48h(record, plan, profile, reason + "（次日保护）")
            affected += more
            if more:
                text += "，其后 48 小时高强度课改为 E 跑"
        return text, affected

    if rule.action_type == "force_rest_today":
        return "当日改为完全休息", _force_rest_today(record, plan, profile, reason)

    if rule.action_type == "block_intensity_48h":
        affected = _block_intensity_48h(record, plan, profile, reason)
        return "48 小时内不安排 I/R/T 高强度课，改为 E 跑", affected

    if rule.action_type == "none":
        return "仅记录，不调整课表", []

    return f"未知动作类型 {rule.action_type}，未执行", []


# ---------------------------------------------------------------- 主流程
def run(record, plan, profile):
    facts, metrics, week_no = build_facts(record, plan, profile)
    rules = list(Rule.objects.filter(enabled=True))
    matched_rules = []
    # 第一遍：先判定安全规则，得到 safety_triggered（影响 R06 等非安全规则的前置条件）
    for rule in sorted(rules, key=lambda r: (not r.safety_flag, -r.priority)):
        try:
            cond = json.loads(rule.condition_json or "{}")
        except ValueError:
            logger.warning("规则 %s 条件 JSON 解析失败，跳过", rule.code)
            continue
        details = []
        hit = eval_condition(cond, facts, details)
        if hit:
            facts["safety_triggered"] = facts["safety_triggered"] or rule.safety_flag
            matched_rules.append({"rule": rule,
                                  "matched_text": "，".join(details) or rule.condition_expr})
    safety = sorted([m for m in matched_rules if m["rule"].safety_flag],
                    key=lambda m: -m["rule"].priority)
    non_safety = [m for m in matched_rules if not m["rule"].safety_flag]

    explanations = []
    conflict_snapshot = [{"code": m["rule"].code, "priority": m["rule"].priority,
                          "safety": m["rule"].safety_flag, "scale": m["rule"].scale}
                         for m in matched_rules]

    executed = []
    skipped = []

    def target_volume(m):
        """规则命中的下周跑量结果，用于“更保守”比较。"""
        try:
            params = json.loads(m["rule"].action_params or "{}")
        except ValueError:
            params = {}
        total = facts["next_week_plan_km"]
        t = m["rule"].action_type
        if t == "reduce_next_week_volume":
            return total * float(params.get("factor", 0.9))
        if t == "cap_weekly_growth":
            return min(facts["this_week_actual_km"] * float(params.get("cap", 1.10)), total)
        if t == "increase_next_week_volume":
            return total * float(params.get("factor", 1.05))
        return total

    # 1) 所有“下周跑量类”规则（无论安全与否）合并为一个冲突组：取最保守（跑量最小）的一条
    vol_actions = ("reduce_next_week_volume", "cap_weekly_growth", "increase_next_week_volume")
    vol_group = [m for m in matched_rules if m["rule"].action_type in vol_actions]
    if vol_group:
        pick = min(vol_group, key=lambda m: (target_volume(m), -m["rule"].priority))
        executed.append(pick)
        for m in vol_group:
            if m is not pick:
                skipped.append((m, f"与 {pick['rule'].code} 同作用于下周跑量，按“取更保守结果”合并，本条未执行"))

    # 2) 其余安全规则（当日/次日尺度）全部执行
    for m in safety:
        if m in vol_group or m in executed:
            continue
        executed.append(m)

    # 3) 其余非安全规则：按作用尺度分组，同尺度取优先级最高者
    for scale in ("day", "week"):
        group = [m for m in non_safety if m not in vol_group and m not in executed
                 and m["rule"].scale == scale]
        if not group:
            continue
        pick = max(group, key=lambda m: m["rule"].priority)
        executed.append(pick)
        for m in group:
            if m is not pick:
                skipped.append((m, f"与 {pick['rule'].code} 同为{('当日' if scale == 'day' else '下周')}尺度，"
                                    f"按冲突消解策略未执行（取优先级更高者）"))

    # 执行动作
    results = []
    for m in executed:
        try:
            text, affected = apply_action(m["rule"], facts, record, plan, profile)
        except Exception as exc:  # 单条规则执行失败不影响其它规则
            logger.exception("规则 %s 执行失败", m["rule"].code)
            text, affected = f"执行失败：{exc}", []
        results.append({"rule": m["rule"], "text": text, "affected": affected})

    # 写解释记录（已执行）
    for r in results:
        explanations.append({
            "rule": r["rule"], "applied": True,
            "matched_expr": f"{r['rule'].condition_expr or render_condition(json.loads(r['rule'].condition_json or '{}'))}；实际数值：{_facts_brief(facts)}",
            "action_text": r["text"],
            "affected_dates": ", ".join(r["affected"]),
        })

    # 未执行规则也留痕
    for m, reason in skipped:
        explanations.append({
            "rule": m["rule"], "applied": False,
            "matched_expr": f"命中条件：{m['matched_text']}",
            "action_text": "未执行",
            "skip_reason": reason, "affected_dates": "",
        })

    # 无规则命中：保留“直接返回当前计划”分支
    if not matched_rules:
        explanations.append({
            "rule": None, "applied": True, "rule_code": "NONE",
            "matched_expr": "无规则命中",
            "action_text": "本次打卡未触发任何调整规则，维持当前计划。",
            "affected_dates": "",
        })

    saved = []
    for e in explanations:
        saved.append(ExplanationRecord.objects.create(
            user=record.user, record=record, rule=e.get("rule"),
            rule_code=(e.get("rule").code if e.get("rule") else "NONE"),
            facts_snapshot=json.dumps(_facts_snapshot(facts), ensure_ascii=False),
            matched_expr=e["matched_expr"], action_text=e["action_text"],
            affected_dates=e.get("affected_dates", ""),
            conflict_set=json.dumps(conflict_snapshot, ensure_ascii=False),
            applied=e["applied"], skip_reason=e.get("skip_reason", ""),
        ))

    return {
        "facts": facts, "metrics": metrics, "week_no": week_no,
        "matched": [m["rule"].code for m in matched_rules],
        "executed": [r["rule"].code for r in results],
        "results": results, "explanations": saved,
        "conflicts": conflict_snapshot,
    }


def _facts_brief(facts):
    keys = ["hr_hrr_pct", "acwr", "tsb", "weekly_growth", "completion_rate_2w",
            "low_completion_streak", "acwr_high_streak"]
    return "，".join(f"{k}={facts[k]}" for k in keys)


def _facts_snapshot(facts):
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in facts.items()}
