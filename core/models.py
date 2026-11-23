"""核心数据模型：User/Profile、Plan、Workout、Record、Rule、ExplanationRecord、TrackPoint
另含推送配置与推送日志（扩展功能支撑）。
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Profile(models.Model):
    ROLE_CHOICES = [("runner", "跑者"), ("admin", "管理者")]
    GENDER_CHOICES = [("M", "男"), ("F", "女")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="profile", verbose_name="账号")
    role = models.CharField("角色", max_length=10, choices=ROLE_CHOICES, default="runner")
    gender = models.CharField("性别", max_length=1, choices=GENDER_CHOICES, default="M")
    age = models.PositiveSmallIntegerField("年龄", default=20)
    hr_rest = models.PositiveSmallIntegerField("静息心率 HRrest", default=60)
    five_k_seconds = models.PositiveIntegerField("5K 成绩（秒）", default=0)
    five_k_date = models.DateField("最近一次 5K 测试日期", null=True, blank=True)
    weekly_base_km = models.FloatField("基准周跑量（km）", default=40.0)
    weight = models.FloatField("体重（kg）", default=60.0)
    email_notify = models.BooleanField("开启邮箱推送", default=True)
    wechat_openid = models.CharField("微信 OpenID（服务号授权后获得）", max_length=64, blank=True)
    notify_email = models.EmailField("接收推送的邮箱", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "跑者档案"
        verbose_name_plural = "跑者档案"

    def __str__(self):
        return f"{self.user.username}（{self.get_role_display()}）"

    @property
    def is_admin(self):
        return self.role == "admin" or self.user.is_staff

    @property
    def hr_max(self):
        return 208 - 0.7 * self.age  # Tanaka 公式

    @property
    def hrr(self):
        return max(1, self.hr_max - self.hr_rest)

    def five_k_display(self):
        s = self.five_k_seconds
        return f"{s // 60}:{s % 60:02d}" if s else "未填写"


class Plan(models.Model):
    STATUS = [("active", "进行中"), ("archived", "已归档")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="用户")
    start_date = models.DateField("开始日期（第1周周一）")
    weeks = models.PositiveSmallIntegerField("周期周数", default=16)
    goal = models.CharField("目标", max_length=20, default="马拉松")
    vdot = models.FloatField("VDOT", default=0)
    five_k_seconds = models.PositiveIntegerField("生成时 5K 成绩（秒）", default=0)
    weekly_base_km = models.FloatField("基准周跑量", default=40)
    calibrated = models.BooleanField("已完成两周校准", default=False)
    status = models.CharField("状态", max_length=10, choices=STATUS, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "训练计划"
        verbose_name_plural = "训练计划"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} {self.start_date} {self.weeks}周 VDOT{self.vdot:.1f}"

    def week_start(self, week_no):
        return self.start_date + timezone.timedelta(days=7 * (week_no - 1))


class Workout(models.Model):
    TYPE_CHOICES = [
        ("E", "E 轻松跑"), ("M", "M 马拉松配速跑"), ("T", "T 乳酸阈值跑"),
        ("I", "I 间歇跑"), ("R", "R 重复跑"), ("LONG", "长距离跑"),
        ("REST", "休息"), ("XT", "交叉训练"),
    ]
    STATUS = [("planned", "待完成"), ("completed", "已完成"), ("missed", "未完成")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="用户")
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="workouts", verbose_name="计划")
    date = models.DateField("日期")
    week_no = models.PositiveSmallIntegerField("第几周")
    phase = models.CharField("阶段", max_length=20, default="基础期")
    wtype = models.CharField("课型", max_length=6, choices=TYPE_CHOICES, default="E")
    distance_km = models.FloatField("距离(km)", default=0)
    pace_sec = models.PositiveIntegerField("目标配速(秒/km)", default=0)
    duration_min = models.PositiveSmallIntegerField("预计时长(分钟)", default=0)
    detail = models.TextField("训练说明", blank=True)
    is_adjusted = models.BooleanField("是否为调整版本", default=False)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="adjustments", verbose_name="原计划课次")
    adjust_reason = models.CharField("调整原因（规则编号）", max_length=120, blank=True)
    status = models.CharField("完成状态", max_length=10, choices=STATUS, default="planned")

    class Meta:
        verbose_name = "课表"
        verbose_name_plural = "课表"
        ordering = ["date", "wtype"]
        indexes = [models.Index(fields=["user", "date"])]

    def __str__(self):
        return f"{self.date} W{self.week_no} {self.get_wtype_display()} {self.distance_km}km"

    @property
    def pace_display(self):
        if not self.pace_sec:
            return "—"
        return f"{self.pace_sec // 60}′{self.pace_sec % 60:02d}″/km"

    @property
    def type_label(self):
        return dict(self.TYPE_CHOICES).get(self.wtype, self.wtype)

    @property
    def is_quality(self):
        return self.wtype in ("I", "R", "T", "M")

    def effective(self):
        """当天生效的课次：若存在调整版本则返回调整版本，否则返回自身。"""
        adj = self.adjustments.filter(user=self.user).order_by("-id").first()
        return adj or self


class Record(models.Model):
    SOURCE = [("manual", "手动录入"), ("gpx", "GPX 导入"), ("tcx", "TCX 导入")]
    METHOD = [("trimp", "TRIMP（心率）"), ("srpe", "sRPE（心率缺失）")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="用户")
    workout = models.ForeignKey(Workout, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="records", verbose_name="对应课次")
    date = models.DateField("训练日期")
    distance_km = models.FloatField("实际距离(km)", default=0)
    duration_min = models.FloatField("实际时长(分钟)", default=0)
    avg_hr = models.PositiveSmallIntegerField("平均心率", null=True, blank=True)
    rpe = models.PositiveSmallIntegerField("疲劳度 RPE", default=5)
    pace_sec = models.PositiveIntegerField("实际配速(秒/km)", default=0)
    source = models.CharField("数据来源", max_length=10, choices=SOURCE, default="manual")
    trimp = models.FloatField("训练冲量 TRIMP", default=0)
    load_method = models.CharField("负荷算法", max_length=10, choices=METHOD, default="trimp")
    completion_rate = models.FloatField("完成率", default=1.0)
    notes = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "打卡记录"
        verbose_name_plural = "打卡记录"
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["user", "date"])]

    def __str__(self):
        return f"{self.date} {self.distance_km}km TRIMP {self.trimp:.1f}"

    @property
    def pace_display(self):
        if not self.pace_sec:
            return "—"
        return f"{self.pace_sec // 60}′{self.pace_sec % 60:02d}″/km"

    @property
    def completion_pct(self):
        return int(round((self.completion_rate or 0) * 100))


class Rule(models.Model):
    SCALE = [("day", "当日/次日"), ("week", "下一周")]
    code = models.CharField("规则编号", max_length=8, unique=True)
    name = models.CharField("规则名称", max_length=60)
    description = models.TextField("规则描述", blank=True)
    condition_expr = models.TextField("条件表达式（可读形式）", blank=True)
    condition_json = models.TextField("条件表达式树（JSON）", default="{}")
    action_type = models.CharField("动作类型", max_length=40, default="none")
    action_params = models.TextField("动作参数（JSON）", default="{}")
    priority = models.PositiveSmallIntegerField("优先级", default=50)
    safety_flag = models.BooleanField("安全规则", default=False)
    scale = models.CharField("作用尺度", max_length=6, choices=SCALE, default="week")
    enabled = models.BooleanField("启用", default=True)
    source = models.CharField("来源依据", max_length=120, blank=True)

    class Meta:
        verbose_name = "规则配置"
        verbose_name_plural = "规则配置"
        ordering = ["-safety_flag", "-priority", "code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def is_safety(self):
        return self.safety_flag


class ExplanationRecord(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="用户")
    record = models.ForeignKey(Record, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="explanations", verbose_name="触发打卡")
    rule = models.ForeignKey(Rule, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="规则")
    rule_code = models.CharField("规则编号快照", max_length=8, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="触发时间")
    facts_snapshot = models.TextField("事实输入快照（JSON）", blank=True)
    matched_expr = models.TextField("匹配条件（含实际数值）", blank=True)
    action_text = models.TextField("执行动作", blank=True)
    affected_dates = models.TextField("受影响课次日期", blank=True)
    conflict_set = models.TextField("冲突集快照（JSON）", blank=True)
    applied = models.BooleanField("是否最终执行", default=True)
    skip_reason = models.TextField("未执行原因", blank=True)

    class Meta:
        verbose_name = "规则解释记录"
        verbose_name_plural = "规则解释记录"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.rule_code} {'执行' if self.applied else '未执行'}"

    @property
    def facts(self):
        import json
        try:
            return json.loads(self.facts_snapshot or "{}")
        except ValueError:
            return {}

    @property
    def conflicts(self):
        import json
        try:
            return json.loads(self.conflict_set or "[]")
        except ValueError:
            return []


class TrackPoint(models.Model):
    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name="track_points",
                               verbose_name="打卡记录")
    seq = models.PositiveIntegerField("序号", default=0)
    lat = models.FloatField("纬度", default=0)
    lon = models.FloatField("经度", default=0)
    elevation = models.FloatField("海拔", default=0)
    ts = models.CharField("时间戳", max_length=32, blank=True)
    hr = models.PositiveSmallIntegerField("瞬时心率", null=True, blank=True)

    class Meta:
        verbose_name = "轨迹点"
        verbose_name_plural = "轨迹点"
        ordering = ["seq"]


class PushConfig(models.Model):
    CHANNEL = [("email", "邮箱"), ("wechat", "微信服务号")]
    channel = models.CharField("渠道", max_length=10, choices=CHANNEL, unique=True)
    enabled = models.BooleanField("启用", default=False)
    config = models.TextField("配置（JSON）", default="{}", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "推送渠道配置"
        verbose_name_plural = "推送渠道配置"

    def __str__(self):
        return f"{self.get_channel_display()} {'启用' if self.enabled else '停用'}"

    @property
    def data(self):
        import json
        try:
            return json.loads(self.config or "{}")
        except ValueError:
            return {}


class PushLog(models.Model):
    STATUS = [("success", "已发送"), ("dry_run", "演练模式"), ("failed", "发送失败")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="用户")
    channel = models.CharField("渠道", max_length=10)
    target = models.CharField("接收方标识", max_length=120, blank=True)
    title = models.CharField("标题", max_length=120, blank=True)
    content = models.TextField("推送内容", blank=True)
    status = models.CharField("状态", max_length=10, choices=STATUS, default="dry_run")
    error = models.TextField("错误信息", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "推送日志"
        verbose_name_plural = "推送日志"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%m-%d %H:%M} {self.channel} {self.get_status_display()}"
