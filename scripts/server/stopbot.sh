#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

stop_pid() {
  local file="$1"
  local name="$2"
  if [[ -f "$file" ]]; then
    local pid
    pid="$(cat "$file")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "Ugasen $name (PID $pid)"
    fi
    rm -f "$file"
  fi
}

stop_pid logs/scheduler.pid "scheduler"
stop_pid logs/telegram.pid "telegram"

echo "Football DC bot ugasen."
