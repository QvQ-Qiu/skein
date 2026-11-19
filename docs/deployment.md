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
