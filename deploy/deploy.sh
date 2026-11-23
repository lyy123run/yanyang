#!/usr/bin/env bash
# 云端部署脚本（Ubuntu 22.04/24.04，腾讯云 / 阿里云轻量或 ECS 均适用）
# 用法：sudo bash deploy/deploy.sh
set -euo pipefail

APP_DIR=/opt/marathon_trainer
PROJECT_NAME=marathon_trainer
PORT=8001

echo "[1/7] 安装系统依赖"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx

echo "[2/7] 复制项目到 ${APP_DIR}"
mkdir -p "${APP_DIR}"
# 若项目就在当前目录，则原地使用；否则请手动 scp 上传后调整此路径
if [ ! -f "${APP_DIR}/manage.py" ]; then
  cp -r "$(dirname "$0")/.."/* "${APP_DIR}/" || true
fi
cd "${APP_DIR}"

echo "[3/7] 创建虚拟环境并安装依赖"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "[4/7] 读取环境变量"
if [ -f .env ]; then
  set -a; source .env; set +a
fi
export DJANGO_DEBUG="${DJANGO_DEBUG:-0}"
export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-*}"
export LOG_DIR="${LOG_DIR:-${APP_DIR}}"
mkdir -p "${LOG_DIR}"

echo "[5/7] 数据库迁移与静态文件收集"
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput

echo "[6/7] 首次部署时初始化演示数据（如已初始化可跳过）"
if [ ! -f .deployed ]; then
  .venv/bin/python manage.py seed_demo || true
  touch .deployed
fi

echo "[7/7] 配置 Gunicorn + Nginx"
cat > /etc/systemd/system/marathon.service <<EOF
[Unit]
Description=Marathon Training System (Gunicorn)
After=network.target

[Service]
User=root
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/gunicorn config.wsgi:application \\
  --bind 127.0.0.1:${PORT} --workers 3 --timeout 60
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/nginx/sites-available/marathon <<EOF
server {
    listen 80;
    server_name _;

    client_max_body_size 25m;

    location /static/ {
        alias ${APP_DIR}/staticfiles/;
        expires 7d;
    }
    location /media/ {
        alias ${APP_DIR}/media/;
    }
    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/marathon /etc/nginx/sites-enabled/marathon
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable marathon
systemctl restart marathon
nginx -t && systemctl restart nginx

echo "部署完成：http://服务器公网IP/"
echo "管理员后台：http://服务器公网IP/admin/ （账号 coach / coach123，请立即改密码）"
