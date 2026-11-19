#!/usr/bin/env bash
# post-curator：从本地镜像仓跑精华帖策展（原 qiudock CronJob 的等价物）。
# 读库走 xiaoqiu_ro 只读账号；写回只调主站 /internal/featured。
set -euo pipefail

ROOT=${SKEIN_JOB_ROOT:-/srv/skein-jobs}
SRC="$ROOT/agent"
MIRROR=${SKEIN_AGENT_MIRROR:-file:///root/qiudock/repo-cache/Agent.git}
VENV="$ROOT/venv"
STATE=${POST_CURATOR_STATE:-/root/SSE_Market/SSE-Agent/post-curator/data}

mkdir -p "$ROOT" "$STATE"
# 同一个克隆与 venv：作业之间串行，避免并发 reset / pip 互相踩。
exec 9>"$ROOT/job.lock"
flock 9

if [ ! -d "$SRC/.git" ]; then
  git clone -q "$MIRROR" "$SRC"
fi
git -C "$SRC" fetch -q origin
git -C "$SRC" reset -q --hard origin/main

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q -i https://mirrors.aliyun.com/pypi/simple \
    requests beautifulsoup4 pymysql
fi

set -a
# shellcheck disable=SC1091
. /root/SSE_Market/ops_tokens.env
# shellcheck disable=SC1091
. /root/SSE_Market/SSE-Agent/tech-digest/.env
set +a

export LLM_BASE_URL=https://api.ssemarket.cn/v1
export LLM_MODEL=${POST_CURATOR_MODEL:-qwen-3.8-flash}
export LLM_API_KEY=${SSE_MARKET_API_KEY:?missing SSE_MARKET_API_KEY}
unset QDOCK_API_KEY || true
export FORUM_DB_HOST=${FORUM_DB_HOST:-127.0.0.1}
export FORUM_DB_PORT=${FORUM_DB_PORT:-3506}
export FORUM_DB_NAME=${FORUM_DB_NAME:-ssemarket}
export FORUM_DB_USER=${FORUM_DB_RO_USER:?missing FORUM_DB_RO_USER}
export FORUM_DB_PASSWORD=${FORUM_DB_RO_PASSWORD:?missing FORUM_DB_RO_PASSWORD}
export MARKET_INTERNAL_BASE_URL=${MARKET_INTERNAL_BASE_URL:-http://127.0.0.1:8080}
export INTERNAL_TOKEN=${INTERNAL_TOKEN:-}

ln -sfn "$STATE" "$SRC/post-curator/data"
cd "$SRC/post-curator"
exec "$VENV/bin/python" main.py scan "$@"
