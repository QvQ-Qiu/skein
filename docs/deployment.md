# 部署与切换

> **P0 只读演练不要按本文执行**，见 [p0-deploy.md](p0-deploy.md)。
> 本文是切换生产的步骤，会停旧雁阵、改 node-agent、打开论坛写入口。

> 2026-11-19 · 把新控制面装到 A 上，替换旧雁阵
> 原则：旧服务全程可回滚；数据迁移可重复执行

## 一、前置条件

| 项 | 要求 | 现状 |
| --- | --- | --- |
| Python | ≥ 3.10 | 宿主 3.10.12，满足 |
| 端口 | 7800（qdockd 占 7700，不能撞） | 空闲 |
| 数据目录 | `/srv/skein`，**不要放根盘** | 需创建 |
| 论坛只读库 | `xiaoqiu_ro`，已存在 | 可直接用 |
| 论坛内部令牌 | `INTERNAL_TOKEN`，在 `/root/SSE_Market/ops_tokens.env` | 已有 |
| 模型网关 | `api.ssemarket.cn/v1` 或本机网关 | 已有 |
| 节点 | 至少一台能跑 cc 的机器 | 现有 node-agent |

## 二、安装

```bash
cd /root/skein/services/control
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
```

## 三、环境变量

写到 `/etc/skein/env`（600），至少包含：

```ini
SKEIN_DB=/srv/skein/skein.db
SKEIN_BLOB_ROOT=/srv/skein/blobs
SKEIN_TOKEN=<控制面内部令牌，随机 32 字节>
SKEIN_PORT=7800

SKEIN_LLM_BASE=https://api.ssemarket.cn/v1
SKEIN_LLM_KEY=<模型 key>
SKEIN_DIRECT_MODEL=<直答模型>
SKEIN_ROUTE_MODEL=<分派模型>

SKEIN_FORUM_BASE=http://127.0.0.1:8080
SKEIN_FORUM_TOKEN=<主站 INTERNAL_TOKEN>

SKEIN_FORUM_DB_HOST=127.0.0.1
SKEIN_FORUM_DB_PORT=3506
SKEIN_FORUM_DB_USER=xiaoqiu_ro
SKEIN_FORUM_DB_PASSWORD=<只读库密码>

# 阶段开关：先影子，观察一周后再开真派单
SKEIN_SHADOW=1
SKEIN_DISPATCH=0
SKEIN_DRIVER=0

SKEIN_PLATFORM_REPOS={"forum":"/srv/agent/jobs/forum/repo"}
```

作业驱动与时刻表在确认后追加：

```ini
SKEIN_DRIVER=1
SKEIN_SCHEDULES=[{"job":"daily","at":"09:15"},{"job":"curator","at":"10:20"}]
SKEIN_JOB_COMMANDS={"daily":"/srv/agent/jobs/daily/run.sh","curator":"/srv/agent/jobs/curator/run.sh"}
SKEIN_JOB_TIMEOUT=14400
```

## 四、systemd

`/etc/systemd/system/skein-control.service`：

```ini
[Unit]
Description=Skein control plane
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/skein/services/control
EnvironmentFile=/etc/skein/env
ExecStart=/root/skein/services/control/.venv/bin/python -m skein
Restart=always
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

启用：

```bash
systemctl daemon-reload
systemctl enable --now skein-control
curl -s http://127.0.0.1:7800/health/live
```

### 控制台

`/console/` 由控制面直接托管构建产物，不需要单独的服务：

```bash
cd /root/skein/services/console
npm ci
npm run build          # 产物在 dist/，已被 .gitignore 忽略
systemctl restart skein-control
```

没有 `dist/` 时控制面退回 `services/console/legacy/` 静态页；旧页始终保留在 `/console-legacy/`。

自维护回路是独立 unit（不进主进程）：`skein-watchdog.service`，读同一份 `/etc/skein/env`。
`SKEIN_WATCHDOG_TICKETS=1` 把新信号变成工单，`SKEIN_WATCHDOG_DISPATCH=1` 直接派给平台 `skein`，
`SKEIN_DAILY_TICKET_HOUR=9` 每天 9 点建「每日巡检」，`SKEIN_TICKET_DAILY_CAP=5` 是每日上限。
暂停回路：`systemctl stop skein-watchdog` 或把 `SKEIN_WATCHDOG_TICKETS=0`。

定时作业（原秋坞 CronJob）由控制面 driver 执行：`SKEIN_DRIVER=1` +
`SKEIN_JOBS_FILE=/etc/skein/jobs.json`（`{"schedules":[{"job","cron"}],"commands":{...}}`），
作业体在 `/srv/skein-jobs/`，迁移与回滚见 [migration-qiudock.md](migration-qiudock.md)。

### UniSSO（内接无感登录）

`/etc/skein/env` 追加：

```ini
SKEIN_SSO_BASE=https://sso.ssemarket.cn
SKEIN_SSO_CLIENT_ID=app_xxxx            # /root/unisso-migration/clients/skein.env
SKEIN_SSO_CLIENT_SECRET=sk_xxxx         # 只进这个文件，600
# 缺省=按 unisso 响应种（生产 .ssemarket.cn）；host=不带 Domain，仅本地调试
SKEIN_COOKIE_DOMAIN=
```

行为与契约（`/root/unisso-migration/SSO-CONTRACT.md`）：

- `GET /api/session`：探测。`{"mode":"token"}` 表示没配 SSO；配了则回验 Cookie，401 落登录页，503 表示 unisso 暂不可用。
- `POST /api/session/login`：邮箱+密码 → `password/verify` → 种共享 Cookie（名字/域/时长全按响应字段）。
- `POST /api/session/logout`：`session/revoke` + 双名清 Cookie（`unisso_sid`、`sse_sso`）。
- `/internal/*` 双认证：`X-Skein-Token`（node-agent、脚本、迁移）或 UniSSO 会话。人必须 `internal=1`。
- unisso 503/超时不清本地会话；登录接口每 IP 每分钟 10 次。
- 验收用 E2E 账号 `music-e2e-test@ssemarket.cn`（见 `deploy-check-sso.sh` 样板）。

### 两级身份（2026-11-21）

- unisso 的 `internal=true` 是**集市用户**（院内身份），`is_admin=true` 才是**管理员**；
  `is_super_admin` 也算管理员。unisso 的 `password/verify` / `session/verify` 已返回
  `is_admin` / `is_super_admin`。
- `GET /api/session?surface=market` 与 `POST /api/session/login {"surface":"market"}`
  只放 `internal=true`（集市用户）；控制台、`/ops/*`、`/internal/*` 要求 `internal=true`
  且 `is_admin=true`。集市用户撞控制台返回 403 `not_admin`，外部用户返回 `not_internal`。
- 控制面 → 小秋 chat 带 `X-Skein-User: v1.<base64url>.<hmac>` 短时身份头，
  载荷含 `internal / admin / super_admin`；小秋按 `admin` 映射 admin/user。
  `/etc/skein/env` 的 `SKEIN_ASSERTION_KEY` 与 `/etc/skein/xiaoqiu-chat.env` 的
  `SKEIN_USER_SECRET` 必须是同一个随机值（600，不进仓库）。
- 未配置 `SKEIN_USER_SECRET` 时小秋忽略身份头、回退论坛 JWT（滚动发布安全）。
- 当前 chat 镜像 `xiaoqiu:1.0.92-skein-user`（从 `1.0.91` 增量替换
  `app/api/deps.py`，构建件 `docker/Dockerfile.skein-user`）。

对外入口（2026-11-19 已上线）：

- nginx 是现有 `sse_market_server-nginx_proxy-1` 容器，主进程用 `-c /etc/nginx/custom.conf` 启动；
  校验/重载必须带 `-c`：`nginx -t -c /etc/nginx/custom.conf`。默认的 `/etc/nginx/nginx.conf` 是镜像自带文件，改了不生效。
- server 块放在 `custom.conf` 的 `http {}` **内部**（模板见 `deploy/nginx/skein.conf`）。
- 控制面绑 docker 网桥，容器的 nginx 才够得到：`/etc/skein/env` 加 `SKEIN_HOST=172.17.0.1`（默认 `127.0.0.1`）。
- 证书：`docker run --rm -v /root/SSE_Market/certbot/www:/var/www/certbot -v /root/SSE_Market/Nginx:/etc/letsencrypt certbot/certbot certonly --webroot -w /var/www/certbot -d skein.ssemarket.cn`。
- 执行节点见 [`deploy/pod/README.md`](../deploy/pod/README.md)：集市云 pod `skein`，node-agent 通过 `wss://skein.ssemarket.cn/api/agent/channel` 反向拨入。

## 五、数据迁移

旧库只读，新库可重复迁移：

```bash
python3 /root/skein/deploy/scripts/migrate_yanzhen.py \
  --old /root/yanzhen/data/yanzhen.db \
  --new /srv/skein/skein.db
```

先 `--dry-run` 看计数。2026-11-19 实测：321 事件、9 工单、14 条 assignment、14 条 dispatch、3 会话、1 节点。

## 六、切换步骤

1. **旁路观察**：新服务起来后 `SKEIN_SHADOW=1`，采集不派单，影子记录判定。
2. **节点接入**：把 node-agent 的 `CONTROLLER_URL` 指到 `ws://127.0.0.1:7800/api/agent/channel`，`SKEIN_CONTROL_URL` 指到 `http://127.0.0.1:7800`，重启 node-agent。
3. **停旧服务**：`systemctl stop yanzhen`（旧控制器）；旧面板入口保留只读一段时间。
4. **验证**：
   - `curl /internal/summary` 计数正常增长；
   - 面板 `/console/` 能看工单、节点、信号；
   - 派一张测试单，走完 ack → result → review。
5. **开真派单**：观察一周后再把 `SKEIN_DISPATCH=1`。

## 七、回滚

```bash
systemctl stop skein-control
systemctl start yanzhen
# node-agent 的 CONTROLLER_URL 改回 ws://127.0.0.1:3030/api/agent/channel
```

旧仓 `/root/yanzhen` 停在 `prod-legacy`，数据目录从未被新服务写入，回滚不需要恢复备份。

## 八、切换后再做

- 停用 `qdockd` / k3s 上的旧负载（秋坞下线）；
- 停用 ops 面板（其能力由 `/console/` 的信号页承接）；
- 确认 `qdock_cron_alert` 与旧 CronJob 已由 `SKEIN_DRIVER` 接管。
