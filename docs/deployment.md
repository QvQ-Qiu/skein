# 部署与切换

> 2026-11-21 · 当前生产：A 控制面 + 集市云 pod 执行节点 + 小秋 chat/ops 容器。秋坞已删除。
> 旧雁阵 `yanzhen` 不在本文范围；它仍在跑，等确认后单独停。

## 一、安装控制面（A）

```bash
git -C /root/skein pull --ff-only
cd /root/skein
sudo bash deploy/scripts/install-control.sh
```

脚本做四件事：

1. `install -d /srv/skein`；
2. 写 `/etc/skein/env`（600，已有则备份为 `.bak-<ts>`）；
3. 装 `skein-control.service`（`Restart=always`）并启用；
4. 装每日备份 cron（03:17，`deploy/scripts/backup-skein.sh`，保留 7 天）。

控制台随控制面托管：`services/console/dist` 存在就挂 `/console/`；不再有 legacy 兜底。

```bash
cd /root/skein/services/console
npm ci && npm run build
systemctl restart skein-control
```

## 二、环境变量（/etc/skein/env）

```ini
SKEIN_DB=/srv/skein/skein.db
SKEIN_BLOB_ROOT=/srv/skein/blobs
SKEIN_TOKEN=<32 字节随机>
SKEIN_HOST=172.17.0.1
SKEIN_PORT=7800

SKEIN_LLM_BASE=https://api.deepseek.com/v1
SKEIN_LLM_KEY=<deepseek key>
SKEIN_AGENT_MODEL=deepseek-v4-flash-vision-exp

SKEIN_DISPATCH=0
SKEIN_DRIVER=1
SKEIN_JOBS_FILE=/etc/skein/jobs.json
SKEIN_PLATFORM_REPOS={"forum":"/home/cloud/repos/skein"}

SKEIN_SSO_BASE=https://sso.ssemarket.cn
SKEIN_SSO_CLIENT_ID=app_xxxx
SKEIN_SSO_CLIENT_SECRET=sk_xxxx

SKEIN_QA_CHAT_UPSTREAM=http://127.0.0.1:8119
SKEIN_QA_OPS_UPSTREAM=http://127.0.0.1:8118
SKEIN_OPS_PANEL_SECRET=<与小秋 ops 相同>
SKEIN_ASSERTION_KEY=<与小秋 chat 的 SKEIN_USER_SECRET 相同>

SKEIN_WATCHDOG=1
SKEIN_WATCHDOG_TICKETS=1
SKEIN_WATCHDOG_DISPATCH=1
SKEIN_WATCHDOG_INTERVAL=300
SKEIN_DAILY_TICKET_HOUR=9
SKEIN_TICKET_DAILY_CAP=5
```

- `SKEIN_LLM_KEY` 为空时控制台 Agent 返回 503；Router 也不可用。
- 小秋 chat/ops 另有自己的模型池：qwen 主 + DeepSeek 备（见 `llm-trace.md`）。
- `SKEIN_DISPATCH=0`：事件先记账/人工建单；需要自动派单再开。
- `SKEIN_COOKIE_DOMAIN` 只在本地 http 调试设为 `host`；生产不设。

## 三、SSO

- 应用：UniSSO 内接 `Skein 控制台`，凭据在 `/root/unisso-migration/clients/skein.env`。
- `internal=true` = 集市用户；`is_admin=true` = 管理员（unisso 返回 `is_admin` / `is_super_admin`）。
- 集市用户：`/api/session?surface=market` 可进；控制台/ops 403 `not_admin`。
- 管理员：控制台、ops、Agent 可进。
- 验收账号：`music-e2e-test@ssemarket.cn`（集市用户，非管理员）。

## 三·五、小秋前厅（论坛闭环）

chat 容器镜像 `xiaoqiu:1.0.104-skein-frontdesk`，host network，监听 `8119`；
`SKEIN_QA_CHAT_UPSTREAM` 指向 `http://127.0.0.1:8119`。

构建：

```bash
cd /root/xiaoqiu-build
docker build -f docker/Dockerfile.skein-frontdesk -t xiaoqiu:1.0.104-skein-frontdesk .
```

镜像直接覆盖 `app/frontdesk.py` 和 `app/api/internal.py`；`app/main.py` 仍用
`docker/patch_main_frontdesk.py` 增量打补丁（基础镜像的 main 含其他生产修复，不能整份覆盖）。

容器关键环境（完整 env 以 `docker inspect xiaoqiu-chat` 为准）：

```ini
FRONTDESK_ENABLED=1
FRONTDESK_INTERVAL=60
FRONTDESK_LOOKBACK=600
FRONTDESK_MAX_PER_HOUR=12
FRONTDESK_MODEL=deepseek-v4-flash-vision-exp
FRONTDESK_LLM_BASE=https://api.deepseek.com/v1
SKEIN_CONTROL_URL=http://172.17.0.1:7800
SKEIN_TOKEN=<控制面服务令牌>
GO_INTERNAL_URL=http://127.0.0.1:8080
INTERNAL_TOKEN=<主站 INTERNAL_TOKEN>
XQ_BOT_UID=95317
```

主站 Go 前置：`/internal/xiaoqiu/reply` 已上线，用 `X-Internal-Token` 鉴权，
以 `XQ_BOT_UID` 身份写 `pcomments`/`ccomments`；楼中楼响应带 `reply_ccomment_id`
时用 `ReplyCcommentID` 嵌套回复。契约见 [`chat-events.md`](chat-events.md)。

已知缺口：主站 `XQ_AT_URL` 仍指向旧 chat pod 的 `8110`，@ 小秋触发的旧链路不可用；
前厅 monitor 不依赖它，直接轮询论坛。

## 四、执行节点（集市云 pod）

见 [`deploy/pod/README.md`](../deploy/pod/README.md)：

- `node-agent` 通过 `wss://skein.ssemarket.cn/api/agent/channel` 反向拨入；
- `REPO_MAP` 指向 pod 内仓库；`MAX_WORKERS` 是并发上限；
- worker 环境剔除控制面令牌；
- pod 重启后手动 `bash ~/start-node-agent.sh`（无 systemd）。

## 五、定时作业

`SKEIN_DRIVER=1` + `SKEIN_JOBS_FILE=/etc/skein/jobs.json`：

```json
{
  "schedules": [
    {"job": "tech-digest-ingest", "cron": "7 */2 * * *"},
    {"job": "tech-digest-daily", "cron": "15 9 * * *"},
    {"job": "tech-digest-star", "cron": "25 9 * * 6"},
    {"job": "tech-digest-weekly", "cron": "0 10 * * 0"},
    {"job": "post-curator-daily", "cron": "20 10 * * *"}
  ],
  "commands": {
    "tech-digest-ingest": "/srv/skein-jobs/run-tech-digest.sh ingest",
    "tech-digest-daily": "/srv/skein-jobs/run-tech-digest.sh daily",
    "tech-digest-star": "/srv/skein-jobs/run-tech-digest.sh star",
    "tech-digest-weekly": "/srv/skein-jobs/run-tech-digest.sh weekly",
    "post-curator-daily": "/srv/skein-jobs/run-post-curator.sh"
  }
}
```

- 作业代码镜像在 `/srv/skein-jobs/repo-cache/Agent.git`；`skein_repo_mirror` 每 15 分钟更新。
- 运行记录按 `(job, 计划分钟)` 去重；`7 */2 * * *` 一天跑多次。
- 秋坞 CronJob 已全部删除，不再双跑。

## 六、控制台页面

- 工作台 / Agent / 需求 / 运行 / 平台。
- 运行 → 会话：每次会话与模型调用；运行 → 事件：chat/forum 事件。
- Agent：管理员用自然语言查工单/节点/运行/信号/模型调用；派工/取消需明确要求。

## 七、备份与回滚

- 备份：`/srv/skein/backups/skein-*.db`（7 天）；恢复 = 停 `skein-control`，替换 `skein.db`，启动。
- 控制面回滚：`systemctl restart skein-control`；代码回滚用 git。
- 秋坞已删除，无回滚 Secrets；作业镜像和源码在本地/gitee。
- 旧雁阵 `yanzhen` / `yanzhen-node` 仍 active，停它需要单独确认。

## 八、自检

```bash
curl -s http://172.17.0.1:7800/health/live
curl -s http://172.17.0.1:7800/health/ready
. /etc/skein/env
curl -s -H "X-Skein-Token: $SKEIN_TOKEN" http://172.17.0.1:7800/internal/summary | head -c 400
docker logs --tail 30 xiaoqiu-chat 2>&1 | grep -i frontdesk
```
