#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PID=""
FRONTEND_PID=""

port_in_use() {
  [[ -n "$(ss -H -ltn "sport = :${1}")" ]]
}

check_port() {
  local service="$1"
  local port="$2"

  if port_in_use "${port}"; then
    echo "[dev-all] ERROR: ${service} 端口 ${port} 已被占用" >&2
    exit 1
  fi
}

cleanup() {
  trap - INT TERM EXIT

  if [[ -n "${FRONTEND_PID}" ]] && kill -0 "${FRONTEND_PID}" 2>/dev/null; then
    kill -TERM -- "-${FRONTEND_PID}" 2>/dev/null || true
  fi

  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill -TERM -- "-${BACKEND_PID}" 2>/dev/null || true
  fi

  wait "${FRONTEND_PID}" 2>/dev/null || true
  wait "${BACKEND_PID}" 2>/dev/null || true
}

trap 'cleanup; exit 130' INT TERM
trap cleanup EXIT

check_port "后端" 8001
check_port "前端" 5180

echo "[dev-all] 启动后端: http://127.0.0.1:8001"
setsid bash -c 'cd "$1" && exec uv run main.py run --env=dev' bash "${ROOT_DIR}/backend" &
BACKEND_PID=$!

echo "[dev-all] 启动前端: http://localhost:5180/web/#/login"
setsid bash -c 'cd "$1" && exec pnpm dev' bash "${ROOT_DIR}/frontend/web" &
FRONTEND_PID=$!

echo "[dev-all] 前后端已启动，按 Ctrl+C 停止"

while true; do
  if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
    echo "[dev-all] ERROR: 后端已退出，停止前端" >&2
    exit 1
  fi

  if ! kill -0 "${FRONTEND_PID}" 2>/dev/null; then
    echo "[dev-all] ERROR: 前端已退出，停止后端" >&2
    exit 1
  fi

  sleep 1
done
