#!/usr/bin/env bash
# 启动前端 (Vite dev server, port 5173)
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
FRONTEND="$ROOT/frontend"

if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "[Frontend] 首次运行，安装依赖..."
  npm install --prefix "$FRONTEND"
fi

echo "[Frontend] 启动 Vite → http://localhost:5173"
echo ""

exec npm run dev --prefix "$FRONTEND"
