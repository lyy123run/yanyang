#!/usr/bin/env python3
"""每日自动推送任务（标准库实现，供 GitHub Actions / 本地定时调用）

链路：从 PythonAnywhere 令牌端点取每日报文 -> 转发到 Server酱（微信）/ SMTP 邮箱。
PythonAnywhere 免费版无法直接访问外网 API，所以外发动作放在本脚本所在环境执行。

环境变量：
  CRON_URL             令牌端点完整 URL，例如
                       https://liuyanyang2026.pythonanywhere.com/push/cron/<TOKEN>/?username=runner
  SERVERCHAN_SENDKEY   可选，Server酱 Turbo 密钥（SCT 开头，微信接收，免费 5 条/天）
  SMTP_HOST/PORT/USER/PASSWORD  可选，邮箱 SMTP，例如 smtp.qq.com / 465 / 邮箱 / 授权码
  MAIL_TO              可选，接收邮箱，默认用报文中的 email
"""
import json
import os
import smtplib
import sys
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from email.header import Header


def http_request(url, data=None, timeout=15):
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def fetch_reports():
    url = os.environ.get("CRON_URL", "")
    if not url:
        sys.exit("缺少环境变量 CRON_URL")
    raw = http_request(url)
    data = json.loads(raw)
    reports = data.get("reports", [])
    if not reports:
        print("令牌端点没有返回任何报告")
    return reports


def push_serverchan(title, content):
    key = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if not key:
        return "skipped:no-serverchan-key"
    url = f"https://sctapi.ftqq.com/{urllib.parse.quote(key)}.send"
    data = urllib.parse.urlencode({"title": title, "desp": content}).encode("utf-8")
    payload = json.loads(http_request(url, data=data))
    if payload.get("code") != 0:
        return f"failed:{payload}"
    return "wechat:ok"


def push_email(title, content, default_to):
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    if not (host and user and password):
        return "skipped:no-smtp-config"
    port = int(os.environ.get("SMTP_PORT", "465"))
    to = os.environ.get("MAIL_TO", "").strip() or default_to
    if not to:
        return "failed:no-recipient"

    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = user
    msg["To"] = to
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as server:
            server.login(user, password)
            server.sendmail(user, [to], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [to], msg.as_string())
    return f"email:ok->{to}"


def main():
    results = []
    for rep in fetch_reports():
        title, content = rep["title"], rep["content"]
        results.append((rep["username"], push_serverchan(title, content),
                        push_email(title, content, rep.get("email", ""))))
    for row in results:
        print(row)
    if not results:
        sys.exit(1)


if __name__ == "__main__":
    main()
