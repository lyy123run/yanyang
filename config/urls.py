from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
]
admin.site.site_header = "马拉松自适应训练系统 · 管理后台"
admin.site.site_title = "马拉松训练系统管理"
admin.site.index_title = "基础参数与业务数据管理"
