"""表单与输入校验：所有输入前后端双重校验，防止脏数据进入负荷计算"""
import re
from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Profile, Record, Rule


class RegisterForm(UserCreationForm):
    username = forms.CharField(label="用户名", max_length=30,
                               widget=forms.TextInput(attrs={"class": "input", "placeholder": "登录账号"}))
    email = forms.EmailField(label="邮箱", required=False,
                             widget=forms.EmailInput(attrs={"class": "input", "placeholder": "用于接收训练方案推送"}))
    password1 = forms.CharField(label="密码", widget=forms.PasswordInput(attrs={"class": "input"}))
    password2 = forms.CharField(label="确认密码", widget=forms.PasswordInput(attrs={"class": "input"}))

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def clean_username(self):
        name = self.cleaned_data["username"]
        if User.objects.filter(username=name).exists():
            raise forms.ValidationError("该用户名已被注册")
        return name


class ProfileForm(forms.ModelForm):
    five_k_input = forms.CharField(label="5K 成绩（分:秒）", required=False,
                                   widget=forms.TextInput(attrs={"class": "input", "placeholder": "如 17:05"}))
    five_k_date = forms.DateField(label="5K 测试日期", required=False,
                                  widget=forms.DateInput(format="%Y-%m-%d",
                                                         attrs={"class": "input", "type": "date"}))

    class Meta:
        model = Profile
        fields = ["gender", "age", "hr_rest", "weekly_base_km", "weight",
                  "notify_email", "wechat_openid", "email_notify"]
        widgets = {
            "gender": forms.Select(attrs={"class": "input"}),
            "age": forms.NumberInput(attrs={"class": "input", "min": 12, "max": 90}),
            "hr_rest": forms.NumberInput(attrs={"class": "input", "min": 30, "max": 110}),
            "weekly_base_km": forms.NumberInput(attrs={"class": "input", "min": 10, "max": 160, "step": "0.5"}),
            "weight": forms.NumberInput(attrs={"class": "input", "min": 35, "max": 130, "step": "0.1"}),
            "notify_email": forms.EmailInput(attrs={"class": "input"}),
            "wechat_openid": forms.TextInput(attrs={"class": "input", "placeholder": "服务号授权后获得，可留空"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.five_k_seconds:
            s = self.instance.five_k_seconds
            self.initial.setdefault("five_k_input", f"{s // 60}:{s % 60:02d}")
        self.initial.setdefault("five_k_date", self.instance.five_k_date)

    def clean_five_k_input(self):
        raw = (self.cleaned_data.get("five_k_input") or "").strip()
        if not raw:
            return ""
        m = re.fullmatch(r"(\d{1,2})[:：](\d{1,2})", raw)
        if not m:
            raise forms.ValidationError("格式应为 分:秒，例如 17:05")
        minutes, seconds = int(m.group(1)), int(m.group(2))
        total = minutes * 60 + seconds
        if not (12 * 60 <= total <= 45 * 60):
            raise forms.ValidationError("5K 成绩应在 12:00–45:00 之间，请检查输入")
        if seconds >= 60:
            raise forms.ValidationError("秒数应小于 60")
        return total

    def clean_age(self):
        age = self.cleaned_data["age"]
        if not (12 <= age <= 90):
            raise forms.ValidationError("年龄应在 12–90 岁之间")
        return age

    def clean_hr_rest(self):
        hr = self.cleaned_data["hr_rest"]
        if not (30 <= hr <= 110):
            raise forms.ValidationError("静息心率应在 30–110 bpm 之间")
        return hr

    def save(self, commit=True):
        profile = super().save(commit=False)
        val = self.cleaned_data.get("five_k_input")
        if val:
            profile.five_k_seconds = int(val)
            profile.five_k_date = self.cleaned_data.get("five_k_date")
        if commit:
            profile.save()
        return profile


class CheckinForm(forms.Form):
    date = forms.DateField(label="训练日期",
                           widget=forms.DateInput(format="%Y-%m-%d",
                                                  attrs={"class": "input", "type": "date"}))
    distance_km = forms.FloatField(label="实际距离（km）", min_value=0.5, max_value=60,
                                   widget=forms.NumberInput(attrs={"class": "input", "step": "0.01"}))
    duration_min = forms.FloatField(label="实际时长（分钟）", min_value=5, max_value=600,
                                    widget=forms.NumberInput(attrs={"class": "input", "step": "0.1"}))
    avg_hr = forms.IntegerField(label="平均心率（可留空）", required=False, min_value=60, max_value=220,
                                widget=forms.NumberInput(attrs={"class": "input", "placeholder": "没有心率数据可留空"}))
    rpe = forms.IntegerField(label="主观疲劳度 RPE（1–10）", min_value=1, max_value=10, initial=5,
                             widget=forms.NumberInput(attrs={"class": "input", "min": 1, "max": 10}))
    notes = forms.CharField(label="备注", required=False, max_length=200,
                            widget=forms.TextInput(attrs={"class": "input"}))

    def __init__(self, *args, profile=None, **kwargs):
        self.profile = profile
        super().__init__(*args, **kwargs)
        if not self.initial.get("date"):
            self.initial["date"] = date.today()

    def clean_date(self):
        d = self.cleaned_data["date"]
        if d > date.today():
            raise forms.ValidationError("不能为未来日期打卡")
        if self.profile:
            from .models import Plan
            plan = Plan.objects.filter(user=self.profile.user, status="active").first()
            if plan and d < plan.start_date:
                raise forms.ValidationError(f"日期早于当前计划开始日 {plan.start_date}")
        return d

    def clean_avg_hr(self):
        hr = self.cleaned_data.get("avg_hr")
        if hr and self.profile:
            hr_max = self.profile.hr_max
            if hr > hr_max + 5:
                raise forms.ValidationError(
                    f"平均心率 {hr} bpm 超出生理合理范围（该跑者 HRmax≈{hr_max:.0f}），请检查输入")
        return hr

    def clean(self):
        cleaned = super().clean()
        dist = cleaned.get("distance_km")
        dur = cleaned.get("duration_min")
        if dist and dur:
            pace = dur * 60 / dist
            if pace < 130:
                raise forms.ValidationError(f"推算配速 {int(pace // 60)}′{int(pace % 60):02d}″/km 过快（<2′10″/km），请检查距离或时长")
            if pace > 900:
                raise forms.ValidationError("推算配速慢于 15′00″/km，请检查距离或时长")
        return cleaned


class GPXUploadForm(forms.Form):
    file = forms.FileField(label="GPX / TCX 文件",
                           widget=forms.ClearableFileInput(attrs={"class": "input", "accept": ".gpx,.tcx"}))
    date = forms.DateField(label="归入训练日期", required=False,
                           widget=forms.DateInput(format="%Y-%m-%d",
                                                  attrs={"class": "input", "type": "date"}))

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > 20 * 1024 * 1024:
            raise forms.ValidationError("文件不得超过 20MB")
        if not f.name.lower().endswith((".gpx", ".tcx")):
            raise forms.ValidationError("仅支持 .gpx 或 .tcx 文件")
        return f


class RuleForm(forms.ModelForm):
    class Meta:
        model = Rule
        fields = ["code", "name", "description", "condition_expr", "condition_json",
                  "action_type", "action_params", "priority", "safety_flag", "scale", "enabled", "source"]
        widgets = {
            "code": forms.TextInput(attrs={"class": "input"}),
            "name": forms.TextInput(attrs={"class": "input"}),
            "description": forms.Textarea(attrs={"class": "input", "rows": 2}),
            "condition_expr": forms.Textarea(attrs={"class": "input", "rows": 2}),
            "condition_json": forms.Textarea(attrs={"class": "input", "rows": 6}),
            "action_params": forms.Textarea(attrs={"class": "input", "rows": 2}),
            "priority": forms.NumberInput(attrs={"class": "input", "min": 1, "max": 999}),
            "scale": forms.Select(attrs={"class": "input"}),
            "source": forms.TextInput(attrs={"class": "input"}),
        }

    def clean_condition_json(self):
        import json
        raw = self.cleaned_data["condition_json"]
        try:
            json.loads(raw)
        except ValueError as exc:
            raise forms.ValidationError(f"条件表达式树必须是合法 JSON：{exc}")
        return raw

    def clean_action_params(self):
        import json
        raw = self.cleaned_data["action_params"] or "{}"
        try:
            json.loads(raw)
        except ValueError as exc:
            raise forms.ValidationError(f"动作参数必须是合法 JSON：{exc}")
        return raw
