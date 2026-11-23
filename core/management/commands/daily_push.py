"""每日自动推送管理命令

用法：
    python manage.py daily_push                # 对所有有活跃计划的用户执行
    python manage.py daily_push --username runner
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from core.models import Plan
from core.push import daily_push


def active_plan(user):
    return Plan.objects.filter(user=user, status="active").first()


class Command(BaseCommand):
    help = "执行每日训练提醒与疲劳状态推送（邮箱/Server酱微信/服务号）"

    def add_arguments(self, parser):
        parser.add_argument("--username", default="", help="只推送给指定用户")

    def handle(self, *args, **options):
        users = User.objects.filter(is_active=True)
        if options["username"]:
            users = users.filter(username=options["username"])

        count = 0
        for user in users:
            if not active_plan(user):
                continue
            title, body, logs = daily_push(user)
            summary = ", ".join(f"{l.channel}:{l.status}" for l in logs)
            self.stdout.write(f"{user.username} -> {title} [{summary}]")
            count += 1
        self.stdout.write(self.style.SUCCESS(f"每日推送完成，共 {count} 个用户"))
