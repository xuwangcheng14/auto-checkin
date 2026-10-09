#!/usr/bin/env bash
# 自动签到管理平台 启动脚本 (Linux / macOS)
# 用法:
#   ./start.sh                 前台启动 (Ctrl+C 停止)
#   ./start.sh --port 8080     参数原样传给 app.py
#   ./start.sh -d              后台启动 (日志: data/app.log)
#   ./start.sh stop            停止后台进程
#   ./start.sh status          查看后台进程状态
set -e
cd "$(dirname "$0")"

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[error] 未找到 Python, 请先安装 Python 3.9+"
    exit 1
fi

if ! "$PY" -c "import flask" >/dev/null 2>&1; then
    echo "[setup] 首次运行, 安装依赖..."
    "$PY" -m pip install -r requirements.txt
fi

PID_FILE="data/app.pid"
mkdir -p data

is_running() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

case "${1:-}" in
    -d|--daemon)
        shift
        if is_running; then
            echo "[start] 已在后台运行 (PID $(cat "$PID_FILE"))"
            exit 0
        fi
        nohup "$PY" app.py "$@" >> data/app.log 2>&1 &
        echo $! > "$PID_FILE"
        echo "[start] 已后台启动 (PID $!), 日志: data/app.log"
        ;;
    stop)
        if is_running; then
            kill "$(cat "$PID_FILE")"
            rm -f "$PID_FILE"
            echo "[stop] 已停止"
        else
            echo "[stop] 后台进程未在运行"
            rm -f "$PID_FILE"
        fi
        ;;
    status)
        if is_running; then
            echo "[status] 运行中 (PID $(cat "$PID_FILE"))"
        else
            echo "[status] 未运行"
        fi
        ;;
    *)
        exec "$PY" app.py "$@"
        ;;
esac
