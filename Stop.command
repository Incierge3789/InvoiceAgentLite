#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-5050}"

# .server.pid を優先
if [ -f .server.pid ]; then
  PID="$(cat .server.pid || true)"
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    echo "▶ サーバ停止中（PID $PID）…"
    kill -TERM "$PID" 2>/dev/null || true
    sleep 1
    kill -9 "$PID" 2>/dev/null || true
  fi
  rm -f .server.pid
else
  # PID 無ければポートで検索（5050〜5060を網羅）
  if command -v lsof >/dev/null 2>&1; then
    for p in $(seq 5050 5060); do
      PIDS="$(lsof -ti tcp:$p || true)"
      [ -n "$PIDS" ] && kill -TERM $PIDS 2>/dev/null || true
    done
  fi
fi

echo "✅ 停止しました。ブラウザは手動で閉じても大丈夫です。"
