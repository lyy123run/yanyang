"""内置规则种子数据（R01–R06）。阈值全部可后台修改。"""
import json

from .models import Rule

RULES = [
    {
        "code": "R01", "name": "周跑量周增幅≤10%",
        "description": "下周计划跑量超过本周实际跑量 1.1 倍时，把下周跑量下调至本周实际×1.1。",
        "condition_expr": "周末评估 且 下周计划增幅 > 10%",
        "condition_json": {"and": [
            {"fact": "is_week_end", "op": "eq", "value": True, "label": "周末评估"},
            {"fact": "weekly_growth", "op": "gt", "value": 0.10, "label": "下周计划增幅"}]},
        "action_type": "cap_weekly_growth", "action_params": {"cap": 1.10},
        "priority": 70, "safety_flag": True, "scale": "week",
        "source": "丹尼尔斯《经典跑步训练法》渐进负荷原则",
    },
    {
        "code": "R02", "name": "高强度后至少休息48h",
        "description": "完成 I/R 课后的 48 小时内不再安排 I/R/T，改为 E 跑或休息。",
        "condition_expr": "48 小时内存在 I/R 强度课",
        "condition_json": {"fact": "intensity_within_48h", "op": "eq", "value": True,
                           "label": "48h内有I/R强度课"},
        "action_type": "block_intensity_48h", "action_params": {},
        "priority": 90, "safety_flag": True, "scale": "day",
        "source": "丹尼尔斯恢复原则",
    },
    {
        "code": "R03", "name": "心率强度超限保护",
        "description": "计划为 E/M/LONG 而实际平均心率≥85% HRR，或超过计划区间上限 10 个百分点时，当日降级为 E 跑，其后 48h 不排高强度。",
        "condition_expr": "(E/M/LONG 且 %HRR ≥ 85) 或 (超计划上限 ≥ 10 个百分点)",
        "condition_json": {"or": [
            {"and": [
                {"fact": "is_easy_m", "op": "eq", "value": True, "label": "当日为E/M/LONG"},
                {"fact": "hr_hrr_pct", "op": "gte", "value": 85, "label": "实际%HRR"}]},
            {"fact": "hr_over_plan_delta", "op": "gte", "value": 10, "label": "超计划上限百分点"}]},
        "action_type": "downgrade_today",
        "action_params": {"distance_factor": 0.8, "block_intensity": True},
        "priority": 100, "safety_flag": True, "scale": "day",
        "source": "Karvonen 法 + Tanaka 公式 + 丹尼尔斯心率区间",
    },
    {
        "code": "R04", "name": "疲劳累积保护",
        "description": "ACWR>1.5 且 TSB<-20 连续 3 天时，下周跑量×0.8 并插入休息日。",
        "condition_expr": "ACWR>1.5 且 TSB<-20 连续3天",
        "condition_json": {"and": [
            {"fact": "acwr", "op": "gt", "value": 1.5, "label": "ACWR"},
            {"fact": "tsb", "op": "lt", "value": -20, "label": "TSB"},
            {"fact": "acwr_high_streak", "op": "gte", "value": 3, "label": "连续高危天数"}]},
        "action_type": "reduce_next_week_volume",
        "action_params": {"factor": 0.8, "insert_rest": True},
        "priority": 95, "safety_flag": True, "scale": "week",
        "source": "Gabbett ACWR + Banister TRIMP",
    },
    {
        "code": "R05", "name": "连续未完成减量",
        "description": "连续 2 次完成率<80% 或 RPE≥9 时，下周跑量×0.9。",
        "condition_expr": "连续低完成次数 ≥ 2",
        "condition_json": {"fact": "low_completion_streak", "op": "gte", "value": 2,
                           "label": "连续低完成次数"},
        "action_type": "reduce_next_week_volume", "action_params": {"factor": 0.9},
        "priority": 60, "safety_flag": False, "scale": "week",
        "source": "自定规则 / 教练经验",
    },
    {
        "code": "R06", "name": "配速达标加量",
        "description": "周末评估：近两周完成率≥80%、ACWR 在 0.8–1.3 且无安全规则触发时，下周跑量+5%，上限为原计划 110%。",
        "condition_expr": "周末评估 且 近两周完成率≥80% 且 ACWR∈[0.8,1.3] 且 无安全规则触发",
        "condition_json": {"and": [
            {"fact": "is_week_end", "op": "eq", "value": True, "label": "周末评估"},
            {"fact": "completion_rate_2w", "op": "gte", "value": 0.8, "label": "近两周完成率"},
            {"fact": "acwr", "op": "between", "value": [0.8, 1.3], "label": "ACWR"},
            {"fact": "safety_triggered", "op": "eq", "value": False, "label": "无安全规则触发"}]},
        "action_type": "increase_next_week_volume", "action_params": {"factor": 1.05, "cap": 1.10},
        "priority": 50, "safety_flag": False, "scale": "week",
        "source": "丹尼尔斯渐进负荷 + 自定规则",
    },
]


def ensure_rules(force=False):
    """写入或补齐内置规则；force=True 时覆盖已有配置。"""
    created = 0
    for item in RULES:
        defaults = {k: (json.dumps(v, ensure_ascii=False) if k in ("condition_json", "action_params") else v)
                    for k, v in item.items() if k != "code"}
        if force:
            Rule.objects.update_or_create(code=item["code"], defaults=defaults)
            created += 1
        else:
            obj, ok = Rule.objects.get_or_create(code=item["code"], defaults=defaults)
            created += int(ok)
    return created
