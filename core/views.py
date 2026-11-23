"""业务视图：登录注册、仪表盘、课表、打卡、记录、解释日志、推送、管理端"""
import hmac
import json
from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import imports as gpx_imports
from . import load as load_mod
from . import planner, push
from . import rules as rule_engine
from . import vdot as vd
from .forms import CheckinForm, GPXUploadForm, ProfileForm, RegisterForm, RuleForm
from .models import (ExplanationRecord, Plan, Profile, PushConfig, PushLog, Record, Rule,
                     TrackPoint, Workout)


# ------------------------------------------------------------------ 工具
def get_profile(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def active_plan(user):
    return Plan.objects.filter(user=user, status="active").first()


def admin_required(view):
    def wrapper(request, *args, **kwargs):
        profile = get_profile(request.user)
        if not (profile.is_admin or request.user.is_superuser):
            messages.error(request, "该页面仅管理者可访问")
            return redirect("dashboard")
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


def _plan_weeks(plan):
    """构造按周分组的课表数据，区分原计划与调整版本。"""
    originals = list(Workout.objects.filter(plan=plan, is_adjusted=False).order_by("date"))
    adjusted = {a.parent_id: a for a in Workout.objects.filter(
        plan=plan, is_adjusted=True).order_by("id")}
    weeks = []
    for w in range(1, plan.weeks + 1):
        items = [o for o in originals if o.week_no == w]
        total_plan = round(sum(i.distance_km for i in items), 1)
        total_eff = round(sum(adjusted[i.id].distance_km if i.id in adjusted else i.distance_km
                              for i in items), 1)
        weeks.append({
            "no": w,
            "phase": items[0].phase if items else "",
            "start": plan.start_date + timedelta(days=7 * (w - 1)),
            "end": plan.start_date + timedelta(days=7 * (w - 1) + 6),
            "total_plan": total_plan,
            "total_eff": total_eff,
            "adjusted_count": sum(1 for i in items if i.id in adjusted),
            "workouts": [{"o": o, "eff": adjusted.get(o.id)} for o in items],
        })
    return weeks


def _effective_workout(user, day):
    w = Workout.objects.filter(user=user, date=day, is_adjusted=False).order_by("id").first()
    if not w:
        return None
    adj = Workout.objects.filter(parent=w, is_adjusted=True).order_by("id").first()
    return adj or w


# ------------------------------------------------------------------ 认证
def register(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            Profile.objects.create(user=user, role="runner", notify_email=user.email)
            login(request, user)
            messages.success(request, "注册成功，请先补全跑者档案（5K 成绩、静息心率）")
            return redirect("profile")
    else:
        form = RegisterForm()
    return render(request, "register.html", {"form": form})


class _LoginForm(AuthenticationForm):
    pass


def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect("dashboard")
        messages.error(request, "用户名或密码错误")
    else:
        form = AuthenticationForm()
    return render(request, "login.html", {"form": form})


@login_required
def profile_view(request):
    profile = get_profile(request.user)
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "档案已保存")
            return redirect("profile")
        messages.error(request, "保存失败，请检查标红项")
    else:
        form = ProfileForm(instance=profile)
    plan = active_plan(request.user)
    kcal = profile.age
    return render(request, "profile.html", {
        "form": form, "profile": profile, "plan": plan,
        "zones": vd.zone_table(plan.vdot) if plan else [],
        "vdot_now": vd.vdot_from_5k(profile.five_k_seconds) if profile.five_k_seconds else 0,
        "hr_max": round(profile.hr_max, 1),
    })


@login_required
@require_http_methods(["POST"])
def plan_create(request):
    profile = get_profile(request.user)
    if not profile.five_k_seconds:
        messages.error(request, "请先在档案中填写 5K 成绩，系统才能推算 VDOT 与配速")
        return redirect("profile")
    start = date.today() - timedelta(days=date.today().weekday())  # 本周一
    try:
        planner.build_plan(request.user, profile, start)
        messages.success(request, "已生成 16 周训练课表（前两周为校准期，配速保守）")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("plan")


# ------------------------------------------------------------------ 仪表盘
@login_required
def dashboard(request):
    profile = get_profile(request.user)
    plan = active_plan(request.user)
    today = date.today()
    if plan:
        week_no = min(plan.weeks, max(1, ((today - plan.start_date).days // 7) + 1))
    else:
        week_no = 0

    recs = list(Record.objects.filter(user=request.user, date__lte=today).order_by("date"))
    metrics = load_mod.load_metrics(recs, end_date=today) if recs else load_mod.load_metrics([])
    today_w = _effective_workout(request.user, today) if plan else None
    this_week = [w for w in Workout.objects.filter(plan=plan, week_no=week_no, is_adjusted=False)] if plan else []
    adj_map = {}
    if plan:
        for a in Workout.objects.filter(plan=plan, is_adjusted=True, parent__isnull=False):
            adj_map[a.parent_id] = a
    week_items = [{"o": w, "eff": adj_map.get(w.id)} for w in this_week]

    latest_explain = ExplanationRecord.objects.filter(user=request.user).order_by("-created_at").first()
    chart = metrics.get("curve", [])[-42:]
    return render(request, "dashboard.html", {
        "profile": profile, "plan": plan, "week_no": week_no, "metrics": metrics,
        "today_w": today_w, "week_items": week_items,
        "recent": Record.objects.filter(user=request.user).order_by("-date")[:5],
        "latest_explain": latest_explain,
        "vdot": plan.vdot if plan else (vd.vdot_from_5k(profile.five_k_seconds) if profile.five_k_seconds else 0),
        "zones": vd.zone_table(plan.vdot) if plan else [],
        "chart": json.dumps(chart, ensure_ascii=False),
        "total_km": round(sum(r.distance_km for r in recs), 1),
        "hr_max": round(profile.hr_max),
    })


# ------------------------------------------------------------------ 课表
@login_required
def plan_view(request):
    plan = active_plan(request.user)
    if not plan:
        messages.info(request, "还没有训练计划，请先生成 16 周课表")
        return redirect("profile")
    weeks = _plan_weeks(plan)
    today = date.today()
    week_no = min(plan.weeks, max(1, ((today - plan.start_date).days // 7) + 1))
    focus = request.GET.get("week")
    focus_no = int(focus) if focus and focus.isdigit() else week_no
    timeline = [{"week": w["no"], "plan": w["total_plan"], "eff": w["total_eff"],
                 "adjusted": w["adjusted_count"]} for w in weeks]
    return render(request, "plan.html", {
        "plan": plan, "weeks": weeks, "week_no": week_no, "focus_no": focus_no,
        "timeline": json.dumps(timeline, ensure_ascii=False),
        "zones": vd.zone_table(plan.vdot),
    })


# ------------------------------------------------------------------ 打卡
def _preset_values(request, plan, profile, today):
    """演示用例预填：A 心率超限 / B 连续未完成 / C 正常完成。"""
    preset = request.GET.get("preset", "")
    w = _effective_workout(request.user, today)
    if not w or w.wtype == "REST":
        return {}, ""
    base = {
        "date": today,
        "distance_km": round(w.distance_km, 1),
        "duration_min": round(w.distance_km * (w.pace_sec or 300) / 60, 1),
        "rpe": 6,
    }
    hr_rest, hrr = profile.hr_rest, profile.hrr
    if preset == "A":
        base["avg_hr"] = int(round(hr_rest + 0.92 * hrr))
        base["notes"] = "演示用例A：配速正常但心率偏高"
        note = "已按用例A预填：平均心率约为 HRR 92%，用于验证 R03 心率安全规则"
    elif preset == "B":
        base["distance_km"] = round(w.distance_km * 0.6, 1)
        base["duration_min"] = round(w.distance_km * 0.6 * (w.pace_sec or 300) / 60 + 5, 1)
        base["rpe"] = 9
        base["notes"] = "演示用例B：未完成计划量"
        note = "已按用例B预填：距离仅为计划的 60%，RPE=9，用于验证 R05 连续未完成减量"
    elif preset == "C":
        base["avg_hr"] = int(round(hr_rest + 0.68 * hrr))
        base["notes"] = "演示用例C：正常完成"
        note = "已按用例C预填：心率位于 E 区间，用于验证正常推进（R06 加量）"
    else:
        note = ""
    return base, note


@login_required
def checkin(request):
    profile = get_profile(request.user)
    plan = active_plan(request.user)
    if not plan:
        messages.error(request, "尚未生成训练计划")
        return redirect("profile")

    if request.method == "POST":
        form = CheckinForm(request.POST, profile=profile)
        if form.is_valid():
            with transaction.atomic():
                data = form.cleaned_data
                day = data["date"]
                workout = Workout.objects.filter(user=request.user, date=day,
                                                 is_adjusted=False).order_by("id").first()
                planned_km = workout.distance_km if workout else 0
                completion = min(1.3, data["distance_km"] / planned_km) if planned_km else 1.0
                pace = int(round(data["duration_min"] * 60 / data["distance_km"])) if data["distance_km"] else 0
                hr = data.get("avg_hr")
                if hr:
                    trimp = load_mod.trimp(data["duration_min"], hr, profile.hr_rest,
                                           profile.hr_max, profile.gender)
                    method = "trimp"
                else:
                    trimp = load_mod.srpe(data["duration_min"], data["rpe"])
                    method = "srpe"
                record = Record.objects.create(
                    user=request.user, workout=workout, date=day,
                    distance_km=data["distance_km"], duration_min=data["duration_min"],
                    avg_hr=hr, rpe=data["rpe"], pace_sec=pace, source="manual",
                    trimp=trimp, load_method=method, completion_rate=round(completion, 3),
                    notes=data.get("notes", ""),
                )
                if workout:
                    workout.status = "completed" if completion >= 0.8 else "missed"
                    workout.save(update_fields=["status"])
                result = rule_engine.run(record, plan, profile)
                # 两周校准：校准期结束当天自动修正 VDOT
                if (not plan.calibrated) and (day - plan.start_date).days >= 13:
                    planner.recalibrate(plan, profile)
            request.session["last_result"] = {
                "matched": result["matched"], "executed": result["executed"],
                "week_no": result["week_no"],
            }
            messages.success(request, "打卡成功，规则引擎已完成判断")
            return redirect("checkin_result", pk=record.pk)
        messages.error(request, "输入校验未通过，请检查标红提示")
    else:
        init, note = _preset_values(request, plan, profile, date.today())
        form = CheckinForm(initial=init, profile=profile)
        if note:
            messages.info(request, note)

    today = date.today()
    today_w = _effective_workout(request.user, today)
    week_no = min(plan.weeks, max(1, ((today - plan.start_date).days // 7) + 1))
    return render(request, "checkin.html", {
        "form": form, "today_w": today_w, "plan": plan, "week_no": week_no,
        "profile": profile, "hr_max": round(profile.hr_max, 1),
    })


@login_required
def checkin_result(request, pk):
    record = get_object_or_404(Record, pk=pk, user=request.user)
    explanations = ExplanationRecord.objects.filter(record=record).order_by("-applied", "-id")
    applied = [e for e in explanations if e.applied and e.rule_code != "NONE"]
    skipped = [e for e in explanations if not e.applied]
    no_match = any(e.rule_code == "NONE" for e in explanations)
    plan = active_plan(request.user)
    week_no = min(plan.weeks, max(1, ((record.date - plan.start_date).days // 7) + 1)) if plan else 0
    next_week = [{"o": w, "eff": Workout.objects.filter(parent=w, is_adjusted=True).first()}
                 for w in Workout.objects.filter(plan=plan, week_no=week_no + 1, is_adjusted=False)]
    return render(request, "checkin_result.html", {
        "record": record, "applied": applied, "skipped": skipped, "no_match": no_match,
        "plan": plan, "next_week": next_week, "week_no": week_no,
        "facts": explanations.first().facts if explanations else {},
    })


# ------------------------------------------------------------------ 记录与导入
@login_required
def records(request):
    qs = Record.objects.filter(user=request.user).select_related("workout")
    page = Paginator(qs, 12).get_page(request.GET.get("page"))
    return render(request, "records.html", {"page": page, "total_km": round(
        sum(r.distance_km for r in qs), 1)})


@login_required
@require_http_methods(["GET", "POST"])
def upload_gpx(request):
    profile = get_profile(request.user)
    plan = active_plan(request.user)
    if request.method == "POST":
        form = GPXUploadForm(request.POST, request.FILES)
        if form.is_valid() and plan:
            f = form.cleaned_data["file"]
            try:
                parsed = gpx_imports.parse_file(f.name, f.read())
            except Exception as exc:
                messages.error(request, f"解析失败：{exc}")
                return render(request, "upload.html", {"form": form})
            day = form.cleaned_data.get("date") or date.today()
            day = min(day, date.today())
            workout = Workout.objects.filter(user=request.user, date=day, is_adjusted=False).first()
            planned = workout.distance_km if workout else 0
            completion = min(1.3, parsed["distance_km"] / planned) if planned else 1.0
            hr = parsed["avg_hr"]
            duration = parsed["duration_min"] or 1
            trimp = (load_mod.trimp(duration, hr, profile.hr_rest, profile.hr_max, profile.gender)
                     if hr else load_mod.srpe(duration, 5))
            with transaction.atomic():
                record = Record.objects.create(
                    user=request.user, workout=workout, date=day,
                    distance_km=parsed["distance_km"], duration_min=round(duration, 1),
                    avg_hr=hr, rpe=5,
                    pace_sec=int(round(duration * 60 / parsed["distance_km"])) if parsed["distance_km"] else 0,
                    source=parsed["source"], trimp=trimp,
                    load_method="trimp" if hr else "srpe",
                    completion_rate=round(completion, 3),
                    notes=f"由 {f.name} 自动导入",
                )
                for i, p in enumerate(parsed["points"][:2000]):
                    TrackPoint.objects.create(record=record, seq=i, lat=p["lat"], lon=p["lon"],
                                              elevation=p["ele"], ts=p["ts"], hr=p["hr"])
                if workout:
                    workout.status = "completed" if completion >= 0.8 else "missed"
                    workout.save(update_fields=["status"])
                rule_engine.run(record, plan, profile)
            messages.success(request, f"导入成功：{parsed['distance_km']}km / "
                                       f"{parsed['duration_min']}分钟 / 平均心率 {hr or '无'}，已写入打卡记录")
            return redirect("checkin_result", pk=record.pk)
        else:
            if not plan:
                messages.error(request, "尚未生成训练计划")
    else:
        form = GPXUploadForm()
    return render(request, "upload.html", {"form": form})


@login_required
def explanations(request):
    qs = ExplanationRecord.objects.filter(user=request.user).select_related("rule", "record")
    page = Paginator(qs, 15).get_page(request.GET.get("page"))
    return render(request, "explanations.html", {"page": page})


# ------------------------------------------------------------------ 推送
@login_required
def push_center(request):
    profile = get_profile(request.user)
    plan = active_plan(request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "send":
            subject, body, logs = push.push_both(request.user, profile, plan)
            ok = [l for l in logs if l.status != "failed"]
            messages.success(request, f"推送已执行：{len(ok)} 个渠道成功（演练模式下写入日志不外发）")
            return redirect("push_center")
        if action == "save":
            profile.notify_email = request.POST.get("notify_email", "").strip()
            profile.wechat_openid = request.POST.get("wechat_openid", "").strip()
            profile.email_notify = request.POST.get("email_notify") == "on"
            profile.save()
            messages.success(request, "推送接收设置已保存")
            return redirect("push_center")
    subject, body = push.build_report(request.user, plan) if plan else ("", "")
    logs = PushLog.objects.filter(user=request.user).order_by("-created_at")[:10]
    cron_url = ""
    if getattr(settings, "CRON_PUSH_TOKEN", ""):
        cron_url = request.build_absolute_uri(
            f"/push/cron/{settings.CRON_PUSH_TOKEN}/?username={request.user.username}")
    return render(request, "push.html", {
        "profile": profile, "plan": plan, "subject": subject, "body": body, "logs": logs,
        "channels": PushConfig.objects.all(), "cron_url": cron_url,
    })


# ------------------------------------------------------------------ 定时任务令牌端点
def cron_report(request, token):
    """外部定时任务（GitHub Actions 等）调用：令牌校验后返回每日报文 JSON。

    无需登录；令牌通过环境变量 CRON_PUSH_TOKEN 配置。
    可选 ?username=xxx 只取某个用户，默认返回全部有活跃计划的用户。
    """
    expected = getattr(settings, "CRON_PUSH_TOKEN", "")
    if not expected or not hmac.compare_digest(str(token), str(expected)):
        return HttpResponseBadRequest("invalid token")

    username = request.GET.get("username", "").strip()
    users = User.objects.filter(is_active=True)
    if username:
        users = users.filter(username=username)

    payload = []
    for user in users:
        plan = active_plan(user)
        if not plan:
            continue
        title, body, metrics = push.build_daily_report(user, plan)
        payload.append({
            "username": user.username,
            "email": user.email,
            "title": title,
            "content": body,
            "metrics": {
                "ctl42": round(metrics["ctl42"], 1),
                "atl7": round(metrics["atl7"], 1),
                "tsb": round(metrics["tsb"], 1),
                "acwr": round(metrics["acwr"], 2),
                "acwr_zone": metrics["acwr_zone"],
            },
        })
    return JsonResponse({"generated_at": date.today().isoformat(), "reports": payload},
                        json_dumps_params={"ensure_ascii": False})


# ------------------------------------------------------------------ 管理端
@login_required
@admin_required
def manage_home(request):
    ctx = {
        "rule_count": Rule.objects.count(),
        "enabled_count": Rule.objects.filter(enabled=True).count(),
        "runner_count": Profile.objects.filter(role="runner").count(),
        "record_count": Record.objects.count(),
        "explain_count": ExplanationRecord.objects.count(),
        "rules": Rule.objects.all(),
        "users": Profile.objects.select_related("user").all()[:20],
        "recent_logs": ExplanationRecord.objects.select_related("user").order_by("-created_at")[:8],
        "push_logs": PushLog.objects.select_related("user").order_by("-created_at")[:6],
    }
    return render(request, "manage_home.html", ctx)


@login_required
@admin_required
def manage_rules(request):
    if request.method == "POST":
        form = RuleForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f"规则 {form.cleaned_data['code']} 已保存，引擎下次判断即生效")
            return redirect("manage_rules")
        messages.error(request, "保存失败，请检查 JSON 格式")
    else:
        form = RuleForm(initial={"condition_json": '{"fact": "", "op": "gt", "value": 0}',
                                 "action_params": "{}", "priority": 50})
    return render(request, "manage_rules.html", {"rules": Rule.objects.all(), "form": form})


@login_required
@admin_required
def manage_rule_edit(request, pk):
    rule = get_object_or_404(Rule, pk=pk)
    if request.method == "POST":
        form = RuleForm(request.POST, instance=rule)
        if form.is_valid():
            form.save()
            messages.success(request, f"规则 {rule.code} 已更新")
            return redirect("manage_rules")
    else:
        form = RuleForm(instance=rule)
    return render(request, "manage_rule_edit.html", {"form": form, "rule": rule})


@login_required
@admin_required
@require_http_methods(["POST"])
def manage_rule_toggle(request, pk):
    rule = get_object_or_404(Rule, pk=pk)
    rule.enabled = not rule.enabled
    rule.save(update_fields=["enabled"])
    messages.success(request, f"规则 {rule.code} 已{'启用' if rule.enabled else '停用'}")
    return redirect("manage_rules")


@login_required
@admin_required
def manage_push(request):
    if request.method == "POST":
        channel = request.POST.get("channel")
        raw = request.POST.get("config", "{}")
        try:
            json.loads(raw)
        except ValueError:
            messages.error(request, "配置必须是合法 JSON")
            return redirect("manage_push")
        cfg, _ = PushConfig.objects.get_or_create(channel=channel)
        cfg.config = raw
        cfg.enabled = request.POST.get("enabled") == "on"
        cfg.save()
        messages.success(request, "推送渠道配置已保存")
        return redirect("manage_push")
    return render(request, "manage_push.html", {
        "channels": PushConfig.objects.all(),
        "wechat_tpl": json.dumps({
            "touser": "<OpenID>",
            "template_id": "<模板ID>",
            "data": {"first": {"value": "第N周训练计划"}, "keyword1": {"value": "马拉松训练系统"},
                     "keyword2": {"value": "课表摘要…"}},
        }, ensure_ascii=False, indent=2),
    })


@login_required
def plan_export(request):
    """导出当前课表为 CSV（演示可追溯与数据可携带）。"""
    import csv
    from django.http import HttpResponse
    plan = active_plan(request.user)
    if not plan:
        return HttpResponseBadRequest("无计划")
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="training_plan.csv"'
    resp.write("\ufeff")
    writer = csv.writer(resp)
    writer.writerow(["周次", "阶段", "日期", "课型", "距离km", "目标配速", "预计时长min", "状态", "是否调整"])
    for w in Workout.objects.filter(plan=plan).order_by("date"):
        writer.writerow([w.week_no, w.phase, w.date, w.type_label, w.distance_km,
                         w.pace_display, w.duration_min,
                         w.get_status_display(), "是" if w.is_adjusted else "否"])
    return resp


@login_required
def api_metrics(request):
    recs = list(Record.objects.filter(user=request.user))
    m = load_mod.load_metrics(recs)
    return JsonResponse({
        "metrics": {"ctl42": m["ctl42"], "atl7": m["atl7"], "tsb": m["tsb"],
                    "acwr": m["acwr"], "zone": m.get("acwr_zone", "")},
        "curve": m["curve"],
    })
