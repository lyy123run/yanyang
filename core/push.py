"""训练内容推送：邮箱（SMTP）与微信服务号模板消息

- 邮箱推送依赖用户邮箱地址，走 Django SMTP，不需要手机号；
- 微信推送依赖服务号 OpenID（用户授权后获得），不需要手机号；
- 未配置真实凭据时进入“演练模式”（dry_run）：生成完整报文并写入推送日志，用于演示与联调。
"""
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from datetime import date, timedelta

from .models import Plan, Profile, PushConfig, PushLog, Record, Workout

logger = logging.getLogger(__name__)


def _fmt_pace(sec):
    if not sec:
        return "—"
    return f"{sec // 60}′{sec % 60:02d}″/km"


def current_week_no(plan, today=None):
    today = today or date.today()
    if today < plan.start_date:
        return 1
    return min(plan.weeks, ((today - plan.start_date).days // 7) + 1)


def build_report(user, plan, metrics=None):
    """组装推送内容：本周课表 + 负荷指标 + 最近一次调整原因。"""
    from .load import load_metrics
    from .models import ExplanationRecord

    today = date.today()
    week_no = current_week_no(plan, today)
    week_start = plan.start_date + timedelta(days=7 * (week_no - 1))
    week_end = week_start + timedelta(days=6)

    workouts = Workout.objects.filter(plan=plan, week_no=week_no, is_adjusted=False).order_by("date")
    adjusted = Workout.objects.filter(user=user, plan=plan, is_adjusted=True,
                                      date__gte=week_start, date__lte=week_end)
    adj_map = {}
    for a in adjusted:
        if a.parent_id:
            adj_map[a.parent_id] = a

    if metrics is None:
        recs = list(Record.objects.filter(user=user, date__lte=today))
        metrics = load_metrics(recs, end_date=today)

    lines = [f"【第 {week_no} 周训练计划】{week_start:%m-%d} ~ {week_end:%m-%d}"]
    for w in workouts:
        eff = adj_map.get(w.id)
        if eff and eff.wtype != w.wtype:
            lines.append(f"· {w.date:%m-%d} {eff.type_label} {eff.distance_km:g}km @ {_fmt_pace(eff.pace_sec)}（原 {w.type_label}，已调整）")
        elif w.wtype == "REST":
            lines.append(f"· {w.date:%m-%d} 休息")
        else:
            lines.append(f"· {w.date:%m-%d} {w.type_label} {w.distance_km:g}km @ {_fmt_pace(w.pace_sec)}")

    last = ExplanationRecord.objects.filter(user=user).order_by("-created_at").first()
    lines.append("")
    if last and last.rule_code != "NONE":
        lines.append(f"最近一次调整：{last.rule_code} · {last.action_text}")
        if last.affected_dates:
            lines.append(f"受影响课次：{last.affected_dates}")
    else:
        lines.append("最近一次打卡未触发调整规则，计划保持不变。")

    lines.append("")
    lines.append(f"体能/疲劳：CTL42={metrics['ctl42']:.1f}，ATL7={metrics['atl7']:.1f}，"
                 f"TSB={metrics['tsb']:.1f}，ACWR={metrics['acwr']:.2f}（{metrics['acwr_zone']}）")
    if metrics.get("estimated"):
        lines.append("提示：训练数据不足 28 天，负荷指标为初始估算值，持续打卡后自动收敛。")
    subject = f"马拉松训练系统 · 第{week_no}周训练计划与疲劳状态"
    return subject, "\n".join(lines)


# ------------------------------------------------------------------ 邮箱
def send_email(user, profile, subject, body):
    target = (profile.notify_email or user.email or "").strip()
    if not target:
        log = PushLog.objects.create(user=user, channel="email", target="",
                                     title=subject, content=body, status="failed",
                                     error="未填写邮箱地址")
        return log

    backend = settings.EMAIL_BACKEND
    live = "smtp" in backend and bool(settings.EMAIL_HOST_USER) and bool(settings.EMAIL_HOST_PASSWORD)
    try:
        if live:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [target], fail_silently=False)
            status, err = "success", ""
        else:
            # 演示模式：控制台后端，完整报文写入推送日志
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [target], fail_silently=False)
            status, err = "dry_run", "未配置 SMTP 凭据，邮件内容已输出到服务器控制台"
    except Exception as exc:
        logger.exception("邮件发送失败")
        status, err = "failed", str(exc)
    return PushLog.objects.create(user=user, channel="email", target=target,
                                  title=subject, content=body, status=status, error=err)


# ------------------------------------------------------------------ 微信服务号
def _get_wechat_config():
    cfg = PushConfig.objects.filter(channel="wechat").first()
    data = cfg.data if cfg else {}
    return cfg, data


def _access_token(appid, secret):
    url = ("https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential"
           f"&appid={urllib.parse.quote(appid)}&secret={urllib.parse.quote(secret)}")
    with urllib.request.urlopen(url, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "access_token" not in data:
        raise RuntimeError(f"获取 access_token 失败：{data}")
    return data["access_token"]


def send_wechat(user, profile, title, content):
    cfg, conf = _get_wechat_config()
    openid = (profile.wechat_openid or conf.get("default_openid") or "").strip()
    template_id = conf.get("template_id", "")
    payload = {
        "touser": openid or "<未绑定>",
        "template_id": template_id or "<未配置模板ID>",
        "url": conf.get("link_url", ""),
        "data": {
            "first": {"value": title, "color": "#173177"},
            "keyword1": {"value": "马拉松训练系统", "color": "#173177"},
            "keyword2": {"value": content[:200], "color": "#173177"},
            "remark": {"value": "本消息由训练系统自动推送，可在网页端查看完整课表。"},
        },
    }
    appid, secret = conf.get("appid"), conf.get("secret")
    if not (appid and secret and template_id and openid):
        log = PushLog.objects.create(
            user=user, channel="wechat", target=openid or "",
            title=title, content=content + "\n\n[报文预览]\n" + json.dumps(payload, ensure_ascii=False),
            status="dry_run", error="未配置服务号 AppID/AppSecret/模板ID/OpenID，已生成报文预览")
        return log

    try:
        token = _access_token(appid, secret)
        url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={token}"
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("errcode") != 0:
            raise RuntimeError(f"微信返回错误：{data}")
        log = PushLog.objects.create(user=user, channel="wechat", target=openid,
                                     title=title, content=content, status="success")
    except Exception as exc:
        logger.exception("微信推送失败")
        log = PushLog.objects.create(user=user, channel="wechat", target=openid,
                                     title=title, content=content, status="failed", error=str(exc))
    return log


def push_both(user, profile, plan):
    subject, body = build_report(user, plan)
    logs = []
    if profile.email_notify:
        logs.append(send_email(user, profile, subject, body))
    logs.append(send_wechat(user, profile, subject, body))
    return subject, body, logs


# ------------------------------------------------------------------ 每日自动推送
def build_daily_report(user, plan, metrics=None):
    """每日报文：今日训练安排（生效版本）+ 疲劳指标 + 昨日打卡摘要。"""
    from .load import load_metrics

    today = date.today()
    if metrics is None:
        recs = list(Record.objects.filter(user=user, date__lte=today))
        metrics = load_metrics(recs, end_date=today)

    if plan:
        week_no = current_week_no(plan, today)
        todays = Workout.objects.filter(plan=plan, user=user, date=today, is_adjusted=False).first()
        if todays:
            eff = todays.effective()
            if eff.wtype == "REST":
                plan_line = "今日安排：休息恢复（可做拉伸/放松）"
            else:
                plan_line = (f"今日安排：{eff.type_label} {eff.distance_km:g}km，"
                             f"目标配速 {_fmt_pace(eff.pace_sec)}，预计 {eff.duration_min} 分钟")
                if eff.is_adjusted:
                    plan_line += f"（已由 {eff.adjust_reason} 调整）"
        else:
            plan_line = "今日无课表安排"
    else:
        week_no, plan_line = 0, "暂无进行中的训练计划"

    yesterday = Record.objects.filter(user=user, date=today - timedelta(days=1)).first()
    if yesterday:
        if yesterday.avg_hr:
            y_line = (f"昨日打卡：{yesterday.distance_km:g}km / {yesterday.duration_min:g}分钟 / "
                      f"平均心率 {yesterday.avg_hr} / RPE {yesterday.rpe} / TRIMP {yesterday.trimp:.0f}")
        else:
            y_line = (f"昨日打卡：{yesterday.distance_km:g}km / {yesterday.duration_min:g}分钟 / "
                      f"RPE {yesterday.rpe} / sRPE {yesterday.trimp:.0f}")
    else:
        y_line = "昨日无打卡记录"

    acwr = metrics["acwr"]
    if acwr > 1.5:
        warn = "疲劳预警：ACWR 超过 1.5，注意控制强度、保证睡眠"
    elif metrics["tsb"] < -20:
        warn = "疲劳预警：TSB 低于 -20，近期以恢复为主"
    else:
        warn = "负荷状态正常，按计划执行即可"

    lines = [
        plan_line,
        y_line,
        f"疲劳指标：CTL42={metrics['ctl42']:.1f}，ATL7={metrics['atl7']:.1f}，"
        f"TSB={metrics['tsb']:.1f}，ACWR={acwr:.2f}（{metrics['acwr_zone']}）",
        warn,
    ]
    if metrics.get("estimated"):
        lines.append("提示：数据不足 28 天，指标为估算值，持续打卡后自动收敛。")
    title = f"每日训练提醒 · {today:%m-%d}（第{week_no}周）"
    return title, "\n".join(lines), metrics


# ------------------------------------------------------------------ Server酱（微信）
def send_serverchan(user, title, content):
    """通过 Server酱 Turbo 推送到微信（免费 5 条/天）。密钥取环境变量或推送配置。"""
    key = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if not key:
        cfg = PushConfig.objects.filter(channel="serverchan").first()
        key = (cfg.data.get("sendkey", "") if cfg else "")
    if not key:
        return PushLog.objects.create(
            user=user, channel="serverchan", target="", title=title, content=content,
            status="dry_run", error="未配置 Server酱 SendKey，已生成报文预览（https://sct.ftqq.com 免费获取）")
    try:
        url = f"https://sctapi.ftqq.com/{urllib.parse.quote(key)}.send"
        data = urllib.parse.urlencode({"title": title, "desp": content}).encode("utf-8")
        req = urllib.request.Request(url, data=data, timeout=10)
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if payload.get("code") != 0:
            raise RuntimeError(f"Server酱返回：{payload}")
        return PushLog.objects.create(user=user, channel="serverchan", target=key[:8] + "***",
                                      title=title, content=content, status="success")
    except Exception as exc:
        logger.exception("Server酱推送失败")
        return PushLog.objects.create(user=user, channel="serverchan", target=key[:8] + "***",
                                      title=title, content=content, status="failed", error=str(exc))


def daily_push(user):
    """给单个用户执行每日自动推送（管理命令/本地/付费主机使用）。"""
    profile, _ = Profile.objects.get_or_create(user=user)
    plan = Plan.objects.filter(user=user, status="active").first()
    title, body, _ = build_daily_report(user, plan)
    logs = []
    if profile.email_notify:
        logs.append(send_email(user, profile, title, body))
    logs.append(send_serverchan(user, title, body))
    logs.append(send_wechat(user, profile, title, body))
    return title, body, logs
