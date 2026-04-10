#!/usr/bin/env bash
# 一键启动前端 + 后端
# Ctrl+C 会同时终止两个进程

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── 清理函数：退出时杀掉所有子进程 ──────────────────────
cleanup() {
  echo ""
  # echo "[Vingolf] 正在关闭..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  # echo "[Vingolf] 已退出"
}
trap cleanup INT TERM EXIT

# ── 后端 ─────────────────────────────────────────────────
if [ -f "$ROOT/.venv/bin/activate" ]; then
  source "$ROOT/.venv/bin/activate"
else
  echo "[ERROR] 未找到 .venv，请先执行: pip install -e ."
  exit 1
fi

echo "[Backend]  启动中 → http://localhost:8000"
cd "$ROOT"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload \
  2>&1 | sed 's/^/[Backend]  /' &
BACKEND_PID=$!

# ── 等待后端就绪 ─────────────────────────────────────────
echo "[Vingolf]  等待后端启动..."
for i in $(seq 1 20); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "[Backend]  已就绪 ✓"
    break
  fi
  sleep 0.5
done

# ── 前端 ─────────────────────────────────────────────────
FRONTEND="$ROOT/frontend"

if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "[Frontend] 首次运行，安装依赖..."
  npm install --prefix "$FRONTEND"
fi

echo "[Frontend] 启动中 → http://localhost:5173"
npm run dev --prefix "$FRONTEND" \
  2>&1 | sed 's/^/[Frontend] /' &
FRONTEND_PID=$!

# ── 提示 ─────────────────────────────────────────────────
echo ""
echo "  Frontend  →  http://localhost:5173"
echo "  Backend   →  http://localhost:8000"
echo "  API Docs  →  http://localhost:8000/docs"
echo ""
echo "  按 Ctrl+C 退出"
echo ""

# 等待任一子进程退出（意外崩溃时自动退出）
wait -n "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
