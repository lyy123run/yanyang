# 马拉松自适应训练系统（Django）

面向业余跑者的 16 周马拉松备赛系统：根据 5K 成绩推算 VDOT 与五区配速，生成课表；每日打卡后计算
TRIMP / CTL / ATL / TSB / ACWR，由轻量级产生式规则引擎判断是否调整课表，并把“为什么调整”写入
解释记录。支持跑者 / 管理者两种角色、手动打卡与 GPX/TCX 手表数据导入、邮箱与微信服务号推送，
可部署到云端供他人通过浏览器访问。

> 本系统按《开题报告》的技术路线实现：Django + B/S + 丹尼尔斯 VDOT + 规则引擎（R01–R06）
> + 解释记录 + GPX 解析。规则阈值全部存数据库，可后台配置，不需要改代码。

---

## 1. 已实现功能（对应你提的 5 点）

| 需求 | 实现情况 |
|---|---|
| ① Web 系统 | Django 5.2 + SQLite，B/S 架构，8 个业务页面 + 管理端 + Django Admin |
| ② 部署到云端 | 提供 `deploy/` 生产部署脚本（Gunicorn + Nginx + systemd），已在本机用生产模式验证；另附免费云平台步骤 |
| ③ 推送训练内容到微信、邮箱 | 邮箱 SMTP、微信服务号模板消息；未配置凭据时进入“演练模式”生成完整报文并写日志 |
| ④ 跑者 / 管理者角色 | 跑者只看自己的数据；管理者维护规则、参数、账号与推送配置，不直接改用户 ATL/CTL 与课表结果 |
| ⑤ 数据来源：手动 + 手表导入 | 手动打卡表单；GPX / TCX 解析（距离、时长、平均心率、步频、轨迹点）写入同一张 Record 表 |

补充实现（开题报告要求）：两周校准期、VDOT 重新估算、课表原计划/调整版本并存、解释日志、
ECharts 可视化（负荷曲线、16 周跑量对比）、CSV 导出、输入校验、异常捕获、操作与规则日志。

---

## 2. 核心模块

```
marathon_trainer/
├─ config/                 # settings / urls / wsgi
├─ core/
│  ├─ vdot.py              # Daniels/Gilbert 公式：VDOT、五区配速、心率区间
│  ├─ load.py              # TRIMP（Banister）、CTL42/CTL28/ATL7、TSB、ACWR、sRPE 兜底
│  ├─ planner.py           # 16 周课表生成、渐进负荷、减载/减量、两周校准
│  ├─ rules.py             # 规则引擎：事实采集→谓词匹配→冲突消解→动作→解释记录
│  ├─ rules_seed.py        # R01–R06 内置规则（阈值可改）
│  ├─ imports.py           # GPX / TCX 解析（XML + 轨迹点）
│  ├─ push.py              # 邮箱 SMTP / 微信服务号模板消息 / 演练模式
│  ├─ forms.py             # 全部输入校验（生理范围、配速范围、日期、JSON）
│  ├─ views.py             # 业务视图 + 权限隔离
│  └─ management/commands/seed_demo.py
├─ templates/              # 13 个页面模板
├─ static/                 # 样式 + 本地 ECharts
├─ demo_data/sample_5k.gpx # 演示用手表文件
├─ docs/                   # 截图、演示手册、实现说明
└─ deploy/                 # gunicorn.service / nginx.conf / deploy.sh
```

数据库 7 张核心表（与开题报告一致）：Profile（User 扩展）、Plan、Workout、Record、Rule、
ExplanationRecord、TrackPoint；另加 PushConfig / PushLog 支撑推送。

---

## 3. 本地运行

```bash
cd marathon_trainer
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo          # 初始化演示账号 + 16 周课表 + 4 周历史数据
python manage.py runserver 0.0.0.0:8000
```

浏览器打开 `http://127.0.0.1:8000/`。

**Windows（在自己的笔记本上演示）**

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 0.0.0.0:8000
```

**演示账号**

| 账号 | 密码 | 角色 |
|---|---|---|
| `runner` | `runner123` | 跑者（5K 17:05，VDOT 59.8，已有 4 周历史训练） |
| `runner2` | `runner2123` | 跑者（5K 20:00，用于演示数据隔离） |
| `coach` | `coach123` | 管理者（同时是 Django 超级用户，可进 `/admin/`） |

> 重跑 `python manage.py seed_demo --reset` 可恢复干净演示数据。

---

## 4. 演示流程（10 分钟）

详见 `docs/答辩演示手册.md`，含逐步操作、台词与预判问答。要点：

1. 登录跑者 → 仪表盘（VDOT、CTL/ATL/TSB/ACWR、今日课表、负荷曲线）
2. 16 周课表（原计划 vs 调整后、周跑量对比图）
3. 打卡 **用例 A（心率超限）** → R03 触发：当日降级 + 48h 保护，解释页给出实际数值
4. 解释日志（命中/未执行/未命中全部留痕）
5. **用例 B（连续未完成）** → R05 与 R01 同时命中，取更保守结果，R01 标“未执行”并写原因
6. **用例 C（正常完成）** → R06 加量 5%（上限 10%）
7. GPX 导入（`demo_data/sample_5k.gpx`）
8. 推送页：邮件正文预览 + 微信模板报文 + 推送日志（不需要手机号）
9. 管理端：修改规则阈值 / 新增 R07（可真实参与判断）、Django Admin
10. 部署说明：本地可跑，云端方案见第 5 节

打卡页提供 `?preset=A/B/C` 三个预填按钮，现场不用手填数字。

---

## 5. 部署到云端

### 方案一：免费（推荐给导师演示用，零成本）

**PythonAnywhere**（对 Django 最友好，免费版不休眠，已实测部署成功）

> 本系统实际部署地址：`https://liuyanyang2026.pythonanywhere.com`（账号 liuyanyang2026，2026-11-22 上线）

1. 注册 <https://www.pythonanywhere.com>（Beginner 免费账号，需邮箱确认）
2. Files 页上传源码 zip 并解压，`mkvirtualenv --python=/usr/bin/python3.10 mtvenv`
3. `pip install -r requirements.txt && python manage.py migrate && python manage.py seed_demo --reset && python manage.py collectstatic --noinput`
4. Web 标签页：Add new web app → Manual configuration → Python 3.10
5. 编辑 WSGI 文件（`/var/www/用户名_pythonanywhere_com_wsgi.py`），把项目目录加入 `sys.path`，
   `os.environ['DJANGO_SETTINGS_MODULE']='config.settings'`，并加一行
   `os.environ['CRON_PUSH_TOKEN']='自选随机字符串'`（每日自动推送的接口密钥），
   `application=get_wsgi_application()`
6. Web 页 Virtualenv 处填 `/home/用户名/.virtualenvs/mtvenv`，点绿色 Reload
7. 静态资源由 WhiteNoise 直接提供，无需额外映射；得到 `https://用户名.pythonanywhere.com`，任何电脑/手机浏览器都能打开
8. **免费版有效期为 1 个月（不是 3 个月，2026-11 实测）**：每月至少登录一次，点 Web 页
   “Run until 1 month from today”续期，否则站点到期自动停用；CPU/带宽有限，适合演示，不适合正式长期使用
9. **每日自动推送（微信/邮箱）**：免费版无定时任务且出站网络受限，外发由 GitHub Actions
   定时调用令牌接口 `/push/cron/<token>/` 再转发，配置步骤见 `docs/自动推送配置指南.md`

### 方案二：云服务器（腾讯云轻量 / 阿里云 ECS，学生优惠几十到一百元/年）

```bash
# 上传项目后（项目目录内）
sudo bash deploy/deploy.sh
```
脚本会装 Python/Nginx、建虚拟环境、迁移、收集静态文件、生成 systemd 服务与 Nginx 反代，
默认 Gunicorn 监听 127.0.0.1:8001，Nginx 对外 80 端口。

**部署前务必**：
- 复制 `.env.example` 为 `.env`，改 `DJANGO_SECRET_KEY`、`DJANGO_ALLOWED_HOSTS`（填公网 IP/域名）
- 云厂商安全组放行 80（HTTPS 需再放行 443 并配证书）
- 修改 `coach` 密码，`DEBUG=0`
- 多用户并发建议把数据库从 SQLite 换成 MySQL（`settings.py` 里改 `DATABASES`）

### 方案三：本机 + 内网穿透（临时演示）

```bash
python manage.py runserver 0.0.0.0:8000
# 另开终端（需自行安装 ngrok）
ngrok http 8000
```
得到临时公网地址；本机关机即失效，不属于真正云端部署。

### 已在本机验证的生产模式

```bash
DJANGO_DEBUG=0 DJANGO_ALLOWED_HOSTS="*" \
  gunicorn config.wsgi:application --bind 127.0.0.1:8001 --workers 3
```
首页、登录、静态资源均返回 200（WhiteNoise 托管静态文件，无需 Nginx 也能跑）。

---

## 6. 推送配置

| 渠道 | 标识 | 是否需要手机号 | 配置位置 |
|---|---|---|---|
| 邮箱 | 用户邮箱地址 | 不需要 | `.env` 的 SMTP 变量 + 跑者档案“接收推送的邮箱” |
| 微信服务号 | OpenID（授权后获得） | 不需要 | 管理端 → 推送渠道配置（AppID/AppSecret/模板ID），跑者填写 OpenID |
| 短信 | 手机号 | 需要 | 本系统未采用 |

- 邮箱：QQ/163 邮箱需开启 SMTP 并使用**授权码**（不是登录密码）
- 微信：个人订阅号没有模板消息权限，需认证服务号；**联调可用微信公众平台测试号**（个人可申请）
- 未配置凭据时：`EMAIL_BACKEND=console`、微信只生成报文 → 推送日志状态为 `dry_run`，
  页面上会明确标注“演练模式，未真实外发”，不会假装发送成功

---

## 7. 成熟度设计（答辩可讲）

- **输入校验**：心率不得超过 Tanaka HRmax+5；配速 2′10″–15′00″/km；日期不得为未来或早于计划；
  RPE 1–10；GPX ≤20MB 且仅 .gpx/.tcx；规则 JSON 必须合法 —— 前后端双重校验
- **异常兜底**：单条规则执行异常被捕获并记日志，不影响其它规则；解析失败给出中文提示；
  `DEBUG=0` 不向用户暴露堆栈，写 `app.log`
- **可追溯**：规则命中/未执行/无命中都写 ExplanationRecord；事实快照、冲突集、受影响课次全留痕
- **权限隔离**：所有查询按 `request.user` 过滤；管理端仅 `role=admin`/`is_staff` 可进，
  且管理者不能改用户的 ATL/CTL 与算法生成的课表
- **数据隔离与幂等**：调整版本按原课次 `parent` 更新，不重复生成；原计划永不被覆盖
- **冷启动**：前两周保守配速，第 14 天用校准期最好成绩按 Riegel 折算 5K 重估 VDOT（限制 ±3）

---

## 8. 公式与规则（与开题报告一致）

- VO2 = −4.60 + 0.182258·v + 0.000104·v²（v: m/min）
- %VO2max = 0.80 + 0.1896·e^(−0.01278t) + 0.2989·e^(−0.1932t)（t: min）
- VDOT = VO2(比赛速度) / %VO2max；5K 20:00 → VDOT 49.8，5K 25:00 → 38.3（与丹尼尔斯表一致）
- 配速：E 65%、M 81%、T 88%、I 100%、R 105% VDOT（反解速度）
- HRmax = 208 − 0.7×年龄；HRR 百分比 = (HR − HRrest)/(HRmax − HRrest)
- TRIMP = t·ΔHR·a·e^(b·ΔHR)，男 a=0.64 b=1.92；女 a=0.86 b=1.67
- CTL42 / CTL28 / ATL7 为指数移动平均（α = 1 − e^(−1/τ)）；TSB = CTL42 − ATL7；
  ACWR = ATL7 / CTL28
- 无心率：sRPE = 时长 × RPE，标记 `srpe`，不触发心率类规则

规则冲突：安全规则（R01–R04）优先；下周跑量类规则合并为一个冲突组取最保守结果
（如 R01 与 R05 同时命中取 0.9 倍）；当日尺度可叠加执行；未执行规则写明原因。

---

## 9. 已知边界（如实说明）

- 微信真实外发需认证服务号与模板消息权限；仓库默认走演练模式
- GPX 轨迹只存点并计算距离/时长/心率，暂不做地图渲染
- SQLite 适合单机演示，多用户并发建议 MySQL
- 无短信、无第三方运动平台 API 同步、无 AI 训练建议（规则引擎是核心）

## 10. 自检

```bash
python manage.py check
python manage.py seed_demo --reset
```
