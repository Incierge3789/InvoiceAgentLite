#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-5050}"
PY="${PY:-python3}"

mkdir -p logs

# venv 準備
if [ ! -d ".venv" ]; then
  "$PY" -m venv .venv
fi
source .venv/bin/activate

# requirements.txt が変わったときだけ依存を入れる（初回は数分）
REQ_HASH_NEW="$(shasum -a 256 requirements.txt | awk '{print $1}')"
REQ_HASH_OLD="$(cat .deps_hash 2>/dev/null || true)"
if [ "$REQ_HASH_NEW" != "$REQ_HASH_OLD" ]; then
  echo "▶ 依存関係をインストールしています…（初回のみ1〜3分）"
  python -m pip install -U pip wheel >/dev/null
  python -m pip install -r requirements.txt >/dev/null
  echo "$REQ_HASH_NEW" > .deps_hash
fi

# 古いPIDが残っていたら整理
if [ -f .server.pid ]; then
  PID="$(cat .server.pid || true)"
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    kill -TERM "$PID" 2>/dev/null || true
    sleep 1
  fi
  rm -f .server.pid
fi

# 空いているポートを 5050→5060 で自動探索
for p in $(seq "$PORT" 5060); do
  if ! lsof -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1; then PORT=$p; break; fi
done

echo "▶ サーバ起動中… http://127.0.0.1:$PORT"
# バックグラウンド起動（ログに出力）
nohup gunicorn -b 127.0.0.1:$PORT app:app > logs/server.out 2> logs/server.err &
SERVER_PID=$!
echo $SERVER_PID > .server.pid

# ヘルスチェック（最大30秒）
for i in {1..60}; do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    (open "http://127.0.0.1:$PORT/upload" 2>/dev/null || true)
    echo "✅ 起動しました（停止は Stop.command を実行）"
    # デフォルトでターミナルを閉じる。残したいときは AUTO_CLOSE_TERMINAL=0 で起動
    if [ "${AUTO_CLOSE_TERMINAL:-1}" = "1" ]; then
      osascript -e 'tell application "Terminal" to if (count of windows) > 0 then close front window' 2>/dev/null || true
    fi
    exit 0
  fi
  sleep 0.5
done

echo "❌ 起動に失敗。logs/server.err を確認してください。"
exit 1
