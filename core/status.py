"""疲劳状态的"人话"解读：把 TSB / ACWR 等指标翻译成一句话结论。

阈值依据（业界通用约定，非本系统自定义）：
- TSB 分区参考 TrainingPeaks 官方 Performance Manager Chart 文档：
  TSB > +5 为新鲜（fresh），< -10 为疲劳，< -20 为过载风险
  https://www.trainingpeaks.com/learn/articles/using-the-performance-manager-chart/
- ACWR 0.8–1.3 安全区间参考 Gabbett (2016), British Journal of Sports Medicine：
  https://bjsm.bmj.com/content/50/5/273
"""


def fatigue_status(metrics):
    """返回三级状态（green/yellow/red，数据不足为 gray）及一句话结论、依据与建议。"""
    tsb = float(metrics.get("tsb") or 0)
    acwr = float(metrics.get("acwr") or 0)
    days = int(metrics.get("days") or 0)

    if days == 0 or acwr == 0:
        return {
            "level": "gray",
            "label": "数据不足，暂无法评估",
            "reason": "完成几次打卡后，系统会自动生成状态结论",
            "advice": "",
            "text": "【数据不足】完成几次打卡后，系统会自动生成状态结论",
        }

    basis = f"TSB {tsb:+.1f}，ACWR {acwr:.2f}"

    if tsb < -20 or acwr > 1.5:
        level = "red"
        label = "过度疲劳，建议立即休息恢复"
        advice = "暂停高强度训练，保证睡眠与营养，持续不缓解请就医评估"
    elif tsb < -10 or acwr > 1.3:
        level = "yellow"
        label = "较疲劳，建议降低强度"
        advice = "把今天的课换成轻松跑或休息，等 TSB 回升后再上强度"
    elif tsb < -5:
        level = "yellow"
        label = "偏疲劳，注意恢复"
        advice = "避免继续加量，重点关注睡眠和拉伸放松"
    elif acwr < 0.8 or tsb > 10:
        level = "green"
        label = "状态新鲜，可以正常训练甚至上强度"
        advice = "近期负荷偏保守、恢复充分，可按计划推进"
    else:
        level = "green"
        label = "状态良好，按计划训练即可"
        advice = "负荷与恢复平衡，继续保持"

    reason = f"依据：{basis}"
    return {
        "level": level,
        "label": label,
        "reason": reason,
        "advice": advice,
        "text": f"【{label}】{reason}；建议：{advice}",
    }
