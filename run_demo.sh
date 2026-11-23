#!/usr/bin/env bash
# 一键本地演示：建虚拟环境 -> 装依赖 -> 迁移 -> 初始化演示数据 -> 启动
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
pip install -q -r requirements.txt
python manage.py migrate --noinput
python manage.py seed_demo
echo "启动：http://127.0.0.1:8000   （Ctrl+C 结束）"
python manage.py runserver 0.0.0.0:8000
