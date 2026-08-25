#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/llama-server.pid"
[[ -f "$PID_FILE" ]] || { echo "没有 PID 文件，服务可能未运行"; exit 0; }
pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    echo "PID 文件无效: $PID_FILE" >&2
    exit 1
fi
if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
    done
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
    echo "Qwen3-TTS 已停止: pid=$pid"
else
    echo "Qwen3-TTS 进程已不存在: pid=$pid"
fi
rm -f "$PID_FILE"
