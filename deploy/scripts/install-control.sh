#!/usr/bin/env bash
# Skein 控制面正式安装：迁移正式库 → 写 /etc/skein/env → 装 systemd → 启动 → 自检。
# 只做加法：不删旧服务（yanzhen），不动 node-agent，不开派单（SKEIN_DISPATCH 默认 0）。
# 幂等：重复执行会覆盖 /etc/skein/env 与 unit（各自留 .bak-<ts> 备份）。
set -euo pipefail

SKEIN_REPO=${SKEIN_REPO:-/root/skein}
SKEIN_ENV=${SKEIN_ENV:-/etc/skein/env}
SKEIN_DB=${SKEIN_DB:-/srv/skein/skein.db}
OLD_DB=${OLD_DB:-/root/yanzhen/data/yanzhen.db}
UNIT=${UNIT:-/etc/systemd/system/skein-control.service}
TS=$(date +%Y%m%d%H%M%S)

echo "== 1/5 迁移正式库（已存在则跳过迁移，只补齐表结构） =="
install -d -m 700 /srv/skein
if [ -f "$SKEIN_DB" ]; then
  echo "  已有 $SKEIN_DB，不重跑迁移"
else
  python3 "$SKEIN_REPO/deploy/scripts/migrate_yanzhen.py" --old "$OLD_DB" --new "$SKEIN_DB" --dry-run
  python3 "$SKEIN_REPO/deploy/scripts/migrate_yanzhen.py" --old "$OLD_DB" --new "$SKEIN_DB"
fi

echo "== 2/5 写 $SKEIN_ENV =="
install -d -m 700 /etc/skein
if [ -f "$SKEIN_ENV" ]; then cp "$SKEIN_ENV" "$SKEIN_ENV.bak-$TS"; fi
{
  echo "SKEIN_DB=$SKEIN_DB"
  echo "SKEIN_BLOB_ROOT=/srv/skein/blobs"
  echo "SKEIN_PORT=7800"
  echo "SKEIN_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  echo "SKEIN_SHADOW=0"
  echo "SKEIN_DISPATCH=0"
  echo "SKEIN_FORUM_BASE=http://127.0.0.1:8080"
  echo "SKEIN_FORUM_TOKEN=${SKEIN_FORUM_TOKEN:-}"
  echo "SKEIN_SSO_BASE=https://sso.ssemarket.cn"
  if [ -f /root/unisso-migration/clients/skein.env ]; then
    # 凭据只落 env 文件，不回显
    grep -E '^UNISSO_CLIENT_ID=' /root/unisso-migration/clients/skein.env | sed 's/^UNISSO_CLIENT_ID=/SKEIN_SSO_CLIENT_ID=/'
    grep -E '^UNISSO_CLIENT_SECRET=' /root/unisso-migration/clients/skein.env | sed 's/^UNISSO_CLIENT_SECRET=/SKEIN_SSO_CLIENT_SECRET=/'
  fi
  echo "SKEIN_HOST=172.17.0.1"
  echo "SKEIN_CONTROL_URL=http://127.0.0.1:7800"
  echo "SKEIN_WATCHDOG=1"
  echo "SKEIN_WATCHDOG_TICKETS=1"
  echo "SKEIN_WATCHDOG_DISPATCH=${SKEIN_WATCHDOG_DISPATCH:-1}"
  echo "SKEIN_WATCHDOG_INTERVAL=300"
  echo "SKEIN_DAILY_TICKET_HOUR=${SKEIN_DAILY_TICKET_HOUR:-9}"
  echo "SKEIN_TICKET_DAILY_CAP=${SKEIN_TICKET_DAILY_CAP:-5}"
} > "$SKEIN_ENV"
chmod 600 "$SKEIN_ENV"
grep -q '^SKEIN_SSO_CLIENT_ID=' "$SKEIN_ENV" && echo "  SSO 凭据已并入" || echo "  未发现 clients/skein.env，先按令牌模式跑（SSO 注册后再补）"

echo "== 3/5 装 systemd unit =="
if [ -f "$UNIT" ]; then cp "$UNIT" "$UNIT.bak-$TS"; fi
cat > "$UNIT" <<UNIT
[Unit]
Description=Skein control plane
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$SKEIN_REPO/services/control
EnvironmentFile=$SKEIN_ENV
ExecStart=/usr/bin/env PYTHONPATH=$SKEIN_REPO/services/control/src python3 -m skein
Restart=always
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now skein-control

echo "== 3b/5 装 watchdog（自维护回路，独立进程） =="
WUNIT=/etc/systemd/system/skein-watchdog.service
if [ -f "$WUNIT" ]; then cp "$WUNIT" "$WUNIT.bak-$TS"; fi
cat > "$WUNIT" <<WUNIT
[Unit]
Description=Skein watchdog (external control loop: tickets / daily review)
After=network-online.target skein-control.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$SKEIN_REPO/services/control
EnvironmentFile=$SKEIN_ENV
ExecStart=/usr/bin/env PYTHONPATH=$SKEIN_REPO/services/control/src python3 -m skein.watchdog_agent
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
WUNIT
systemctl daemon-reload
systemctl enable --now skein-watchdog

echo "== 4/5 自检 =="
sleep 2
curl -fsS http://127.0.0.1:7800/health/live
echo
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7800/api/session)
echo "/api/session -> $code（401=已配 SSO，200=令牌模式）"

echo "== 5/5 冒烟 =="
set -a; . "$SKEIN_ENV"; set +a
node "$SKEIN_REPO/deploy/scripts/console-smoke.mjs" || echo "冒烟有失败项，按输出排查"
echo "完成。旧雁阵未动；node-agent 仍指向旧控制器。"
