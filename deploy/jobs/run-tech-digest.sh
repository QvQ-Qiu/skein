#!/usr/bin/env bash
# tech-digest：从本地镜像仓跑 daily/star/weekly/ingest（原 qiudock Job 的等价物）。
# 依赖：/root/qiudock/repo-cache/Agent.git（qdock_repo_mirror 保活）、tech-digest/.env。
set -euo pipefail

TASK="${1:?usage: run-tech-digest.sh <ingest|daily|star|weekly> [args...]}"
shift || true

ROOT=${SKEIN_JOB_ROOT:-/srv/skein-jobs}
SRC="$ROOT/agent"
MIRROR=${SKEIN_AGENT_MIRROR:-file:///root/qiudock/repo-cache/Agent.git}
VENV="$ROOT/venv"
STATE=${TECH_DIGEST_STATE:-/root/SSE_Market/SSE-Agent/tech-digest/data}

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
. /root/SSE_Market/SSE-Agent/tech-digest/.env
set +a
export SSE_MARKET_BASE_URL=https://api.ssemarket.cn/v1
export SSE_MARKET_MODEL=${TECH_DIGEST_MODEL:-qwen-3.8-flash}
unset QDOCK_API_KEY || true

ln -sfn "$STATE" "$SRC/tech-digest/data"
cd "$SRC/tech-digest"

if [ "$TASK" = "star" ] || [ "$TASK" = "weekly" ]; then
  sed -i 's/"reasoning_effort": "none"/"reasoning_effort": "low"/' app/llm.py || true
  trap 'git -C "$SRC" checkout -- tech-digest/app/llm.py 2>/dev/null || true' EXIT
fi

exec "$VENV/bin/python" main.py "$TASK" "$@"
