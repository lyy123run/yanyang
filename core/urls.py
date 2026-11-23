from django.urls import path
from django.contrib.auth.views import LogoutView

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("logout/", LogoutView.as_view(next_page="login"), name="logout"),
    path("register/", views.register, name="register"),
    path("profile/", views.profile_view, name="profile"),
    path("plan/create/", views.plan_create, name="plan_create"),
    path("plan/", views.plan_view, name="plan"),
    path("plan/export/", views.plan_export, name="plan_export"),
    path("checkin/", views.checkin, name="checkin"),
    path("checkin/<int:pk>/result/", views.checkin_result, name="checkin_result"),
    path("records/", views.records, name="records"),
    path("records/upload/", views.upload_gpx, name="upload_gpx"),
    path("explanations/", views.explanations, name="explanations"),
    path("push/", views.push_center, name="push_center"),
    path("push/cron/<str:token>/", views.cron_report, name="cron_report"),
    path("api/metrics/", views.api_metrics, name="api_metrics"),
    # 管理端
    path("manage/", views.manage_home, name="manage_home"),
    path("manage/rules/", views.manage_rules, name="manage_rules"),
    path("manage/rules/<int:pk>/edit/", views.manage_rule_edit, name="manage_rule_edit"),
    path("manage/rules/<int:pk>/toggle/", views.manage_rule_toggle, name="manage_rule_toggle"),
    path("manage/push/", views.manage_push, name="manage_push"),
]
