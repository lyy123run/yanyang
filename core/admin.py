from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import (ExplanationRecord, Plan, Profile, PushConfig, PushLog, Record, Rule,
                     TrackPoint, Workout)

admin.site.register([Plan, Workout, Record, ExplanationRecord, TrackPoint, PushLog])


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "priority", "safety_flag", "scale", "action_type",
                    "enabled", "source")
    list_editable = ("priority", "safety_flag", "enabled")
    list_filter = ("safety_flag", "scale", "enabled")
    search_fields = ("code", "name", "description")
    ordering = ("-safety_flag", "-priority")
    fieldsets = (
        ("基本信息", {"fields": ("code", "name", "description", "source")}),
        ("条件（可配置阈值）", {"fields": ("condition_expr", "condition_json")}),
        ("动作", {"fields": ("action_type", "action_params", "scale")}),
        ("冲突消解", {"fields": ("priority", "safety_flag", "enabled")}),
    )
    help_text = "条件表达式树示例：{\"and\":[{\"fact\":\"acwr\",\"op\":\"gt\",\"value\":1.5}]}"


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "gender", "age", "hr_rest", "five_k_display", "weekly_base_km")
    list_filter = ("role", "gender")
    search_fields = ("user__username", "user__email")


@admin.register(PushConfig)
class PushConfigAdmin(admin.ModelAdmin):
    list_display = ("channel", "enabled", "updated_at")
