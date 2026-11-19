#!/usr/bin/env bash
# 启动/重启 pod 上的 node-agent（pod 没有 systemd，用 setsid 常驻）。
# 前提：~/node-agent.env 已按 README 填好。
set -euo pipefail
cd "$HOME/repos/skein/services/node-agent"
[ -f "$HOME/node-agent.env" ] || { echo "缺 ~/node-agent.env"; exit 1; }
pkill -x node 2>/dev/null || true
sleep 1
set -a
. "$HOME/node-agent.env"
set +a
export PATH="$HOME/.local/node/bin:$PATH"
setsid nohup node node-agent.mjs > "$HOME/node-agent.log" 2>&1 < /dev/null &
sleep 2
tail -n 5 "$HOME/node-agent.log"
