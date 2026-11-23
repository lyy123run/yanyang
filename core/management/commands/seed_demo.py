"""初始化演示数据：跑者账号、管理者账号、16 周课表、历史打卡、推送渠道配置

用法：
    python manage.py seed_demo [--reset]
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


from core import load as load_mod
from core import planner
from core import rules as rule_engine
from core.models import Plan, Profile, PushConfig, Record, Workout
from core.rules_seed import ensure_rules
from core.vdot import pace_sec_per_km

RUNNER = {
    "username": "runner", "password": "runner123",
    "email": "runner@example.com",
    "profile": dict(role="runner", gender="M", age=20, hr_rest=50,
                    five_k_seconds=17 * 60 + 5, five_k_date=date(2026, 11, 1),
                    weekly_base_km=50, weight=60,
                    notify_email="runner@example.com", wechat_openid=""),
}
COACH = {"username": "coach", "password": "coach123", "email": "coach@example.com"}

# 不同课型按 HRR 的典型心率百分比，以及 RPE
HR_RATIO = {"E": 0.65, "LONG": 0.68, "T": 0.86, "M": 0.82, "I": 0.93, "R": 0.95}
RPE = {"E": 5, "LONG": 6, "T": 7, "M": 7, "I": 8, "R": 8}


class Command(BaseCommand):
    help = "初始化演示数据（清空后重建）"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="删除演示账号及其数据后重建")
        parser.add_argument("--weeks", type=int, default=4, help="历史数据保留的完整周数")

    def handle(self, *args, **opts):
        today = date.today()
        this_monday = today - timedelta(days=today.weekday())
        start = this_monday - timedelta(days=7 * opts["weeks"])

        if opts["reset"]:
            User.objects.filter(username__in=["runner", "runner2", "coach"]).delete()
            self.stdout.write("已删除旧演示账号数据")

        ensure_rules()

        runner = self._make_user(RUNNER, is_staff=False)
        profile = self._profile(runner, RUNNER["profile"])
        plan = Plan.objects.filter(user=runner, status="active").first()
        if not plan:
            plan = planner.build_plan(runner, profile, start)
        plan.calibrated = True
        plan.save(update_fields=["calibrated"])

        self._seed_history(runner, profile, plan, today)
        self._make_second_runner(start)
        coach = self._make_user(COACH, is_staff=True)
        self._profile(coach, dict(role="admin", gender="M", age=35, hr_rest=58,
                                  five_k_seconds=0, weekly_base_km=30, weight=70,
                                  notify_email="coach@example.com"))

        PushConfig.objects.get_or_create(channel="email", defaults={
            "enabled": True,
            "config": '{"note": "SMTP 凭据通过环境变量配置，未配置时为演练模式"}',
        })
        PushConfig.objects.get_or_create(channel="wechat", defaults={
            "enabled": False,
            "config": '{"appid": "", "secret": "", "template_id": "", "link_url": ""}',
        })

        recs = list(Record.objects.filter(user=runner))
        m = load_mod.load_metrics(recs, end_date=today)
        self.stdout.write(self.style.SUCCESS(
            f"初始化完成：计划 {plan.start_date} 起 16 周，VDOT {plan.vdot}，"
            f"历史打卡 {len(recs)} 条，CTL42={m['ctl42']} ATL7={m['atl7']} "
            f"ACWR={m['acwr']} TSB={m['tsb']}"
        ))

    # ------------------------------------------------------------------
    def _make_user(self, spec, is_staff=False):
        user, created = User.objects.get_or_create(
            username=spec["username"],
            defaults={"email": spec.get("email", ""), "is_staff": is_staff})
        if is_staff:
            user.is_staff = True
            user.is_superuser = True
        user.set_password(spec["password"])
        user.save()
        return user

    def _profile(self, user, data):
        profile, _ = Profile.objects.get_or_create(user=user, defaults=data)
        for k, v in data.items():
            setattr(profile, k, v)
        profile.save()
        return profile

    def _seed_history(self, user, profile, plan, today):
        existing_dates = set(Record.objects.filter(user=user).values_list("date", flat=True))
        workouts = list(Workout.objects.filter(plan=plan, is_adjusted=False, date__lt=today))
        last_record = None
        for w in workouts:
            if w.wtype in ("REST",) or w.date in existing_dates or w.distance_km <= 0:
                continue
            duration = w.distance_km * (w.pace_sec or 300) / 60
            ratio = HR_RATIO.get(w.wtype, 0.65)
            avg_hr = int(round(profile.hr_rest + ratio * profile.hrr))
            trimp = load_mod.trimp(duration, avg_hr, profile.hr_rest, profile.hr_max, profile.gender)
            rec = Record.objects.create(
                user=user, workout=w, date=w.date,
                distance_km=w.distance_km, duration_min=round(duration, 1),
                avg_hr=avg_hr, rpe=RPE.get(w.wtype, 6),
                pace_sec=w.pace_sec, source="manual", trimp=trimp,
                load_method="trimp", completion_rate=1.0,
                notes="演示历史数据",
            )
            w.status = "completed"
            w.save(update_fields=["status"])
            last_record = rec

        # 最近一条历史打卡跑一次引擎，日志里留下“正常推进/保护”的真实记录
        if last_record:
            rule_engine.run(last_record, plan, profile)

    def _make_second_runner(self, start):
        spec = dict(RUNNER)
        spec["username"] = "runner2"
        spec["password"] = "runner2123"
        spec["email"] = "runner2@example.com"
        spec["profile"] = dict(spec["profile"], five_k_seconds=20 * 60,
                               notify_email="runner2@example.com")
        user = self._make_user(spec, is_staff=False)
        profile = self._profile(user, spec["profile"])
        if not Plan.objects.filter(user=user, status="active").exists():
            planner.build_plan(user, profile, start)
