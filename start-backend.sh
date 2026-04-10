#!/usr/bin/env bash
# 启动后端 (FastAPI + uvicorn, port 8000)
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# 激活虚拟环境
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
else
  echo "[ERROR] 未找到 .venv，请先执行: pip install -e ."
  exit 1
fi

echo "[Backend] 启动 Vingolf API → http://localhost:8000"
echo "[Backend] Docs → http://localhost:8000/docs"
echo ""

exec uvicorn main:app --host 0.0.0.0 --port 8000 --reload
