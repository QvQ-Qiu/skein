# Skein 交接文档

> 2026-11-21 · 当前生产：**https://skein.ssemarket.cn/console/**（根路径 302 到 console）
> 小秋只保留 chat + 论坛前厅；ops 面、秋坞、旧雁阵 yanzhen 均已下线。

## 1. 一分钟版本

Skein 是集市论坛的 Super-agent 控制面：论坛吐槽/反馈 → 小秋前厅建单 → 管理员验收 →
工雁在节点 worktree 修复 → 原帖回写。**验收和合入永远由人做，不自动合入。**

当前分工：

- **Skein 控制面**：工单、派工、节点、定时作业、Watchdog、控制台、内置 Agent。
- **小秋 chat**：`/api/v1/agent/*`、`/task/*` 的对话/知识库助手。
- **小秋前厅**：轮询论坛帖/评论/楼中楼，回执、`intake` 建单、`notify` 原帖回写。
- **Go 主站**：论坛 API + 小秋机器人回帖内部端点。

## 2. 仓库与服务器

| 组件 | 仓库 | 服务器路径 |
| --- | --- | --- |
| Skein | `github.com/QvQ-Qiu/skein`，主分支 `main` | `/root/skein` |
| 小秋 | `gitee.com/QvQ-Qiu/xiaoqiu`，主分支 `main` | 裸仓 `/root/git/xiaoqiu.git`；构建源 `/root/xiaoqiu-build` |
| Go 主站 | `gitee.com/yang-peiyue/SSE_market_server`，分支 `online` | `/root/SSE_Market/SSE_market_server` |

Skein 仓结构：

```text
services/control/     FastAPI 控制面 + SQLite 领域库
services/node-agent/  节点 daemon（反向拨入、worktree、Claude Code）
services/console/     Vue 3 控制台（构建产物 dist/）
deploy/               systemd/env/验收脚本
docs/                 当前文档
```

## 3. 生产拓扑

```text
浏览器 ── https://skein.ssemarket.cn/console/ ── nginx ── 控制面 172.17.0.1:7800
                                                       │
                                                       ├─ 小秋 chat 127.0.0.1:8119
                                                       ├─ Go 主站 127.0.0.1:8080
                                                       └─ 节点 WebSocket /api/agent/channel
节点 n-cloud-skein（集市云，平台 forum/skein，2 worker）
```

- 控制台登录走 UniSSO：`password/verify` + 共享会话 Cookie；控制台要求 `is_admin=1`。
  UniSSO 代码仓 `YatTerra/unisso` 默认分支是 **main**（没有 master）；M2M claims 补丁 `260021c` 已在 main。
- 当前在线执行节点只有 `n-cloud-skein`；旧的 `n-market-host` 已移除。
- `skein-control`、`skein-watchdog` 是 systemd 服务；小秋 chat 是 host network Docker 容器。
- ops 面、`xiaoqiu-ops` 容器、旧 `yanzhen` / `yanzhen-node` 已停止/删除；`/root/yanzhen` 代码目录仍保留。

## 4. 核心流程

1. **前厅**：`xiaoqiu/app/frontdesk.py` 每 60 秒轮询论坛；帖子/评论/楼中楼统一走 LLM 判断，
   所有吐槽都回，bug/request 调控制面 `POST /internal/intake`。
2. **建单/合并**：同帖已有活跃工单时评论/楼中楼作为 followup 合并，不重复建单。
3. **派工**：管理员在控制台派单，或 Agent/Console 调 `/internal/tickets/{id}/dispatch`；
   节点 WebSocket 建 worker，工雁在 worktree 完成并回传结果。
4. **验收/回写**：管理员 approve → `public_status=fixed` → 控制面调小秋
   `/internal/skein/notify` → 小秋以机器人账号回原帖；reject 回 `need_info`。
5. **定时作业**：`/etc/skein/jobs.json` 驱动 tech-digest ingest/daily/star/weekly、
   post-curator；作业源码镜像 `/srv/skein-jobs/repo-cache/Agent.git`。
6. **Watchdog**：确定性信号自动建单（派单超时、作业失败、节点离线等），`skein-watchdog` 独立进程。

## 5. 配置与数据

- 控制面：`/etc/skein/env`（600），字段见 `docs/control-plane.md`；不要从仓库读生产密钥。
- 小秋 chat：环境变量在容器 `docker inspect xiaoqiu-chat`；生产 settings 只读挂载
  `/etc/skein/xiaoqiu-chat-settings.yaml`。
- 控制面库：`/srv/skein/skein.db`；每日 03:17 `deploy/scripts/backup-skein.sh` 备份到
  `/srv/skein/backups/`，保留 7 天。
- 小秋 PG：DSN 在小秋环境；ops 旧表已归档到
  `/srv/skein/backups/xiaoqiu-ops-history-20261121.json.gz` 后删除。
- 论坛：Go 容器 `server` + MySQL `sse_market_db`；内部回帖端点
  `/internal/xiaoqiu/reply`，RAG hook 已指向 chat 8119。

## 6. 部署/回滚

### Skein

```bash
cd /root/skein
git pull --ff-only
cd services/console && npm run build
cd /root/skein && systemctl restart skein-control
curl -s http://172.17.0.1:7800/health/ready
```

备份恢复：停 `skein-control` → 替换 `/srv/skein/skein.db` → 启动。

### 小秋

```bash
cd /root/xiaoqiu-build
git pull --ff-only
docker build -f docker/Dockerfile.skein-frontdesk -t xiaoqiu:<tag> .
# 用 docker inspect xiaoqiu-chat 的 Env 重建容器；镜像会先清空基础镜像的 app 再覆盖仓库 app
```

### Go

源码改完必须 `go build` + 替换容器 `/app/serve` + `docker restart server`；生产二进制是热替换，
重建镜像前先留 `/root/serve.bak-*`。

## 7. 自检

```bash
curl -s http://172.17.0.1:7800/health/live
curl -s http://172.17.0.1:7800/health/ready
curl -s -H "X-Skein-Token: $SKEIN_TOKEN" http://172.17.0.1:7800/internal/summary
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8119/health/live
systemctl is-active skein-control skein-watchdog
docker logs --tail 50 xiaoqiu-chat | grep frontdesk
```

控制台：**https://skein.ssemarket.cn/console/**。

## 8. 当前边界

- L4 自动合入永不做；验收/合入必须人工。
- 小秋 ops、秋坞、qdock 沙箱/平台上报、旧 yanzhen 均已下线；相关历史文档只作存档。
- 已删除：旧 Router/Outbox/订阅、`/internal/trigger`、`/internal/chat`、
  `/internal/outbox/emit`、`/api/v1/agent/ticket`、`/ops/*` 代理。
- 旧雁阵代码/数据保留在 `/root/yanzhen`、`/srv/ro/yanzhen`；需要彻底删除时先确认。
- 当前有一个真实自维护工单由 `tech-digest-ingest` 失败触发，处理状态看控制台「需求」。

## 9. 排障入口

| 现象 | 先看 |
| --- | --- |
| 控制台 401/403 | UniSSO 账号 `internal`/`is_admin`；nginx → 7800；`/api/session` |
| 前厅不回帖 | `docker logs xiaoqiu-chat` 的 frontdesk；`SKEIN_CONTROL_URL`/`GO_INTERNAL_URL` |
| 工单不派工 | 控制台节点在线/容量；`skein-control` 日志；`/internal/nodes` |
| 作业失败 | `/internal/runs`；`/srv/skein-jobs`；Watchdog 自动建单 |
| 控制面异常 | `journalctl -u skein-control -n 100`；`/srv/skein/backups` |
| RAG 不更新 | Go `server` 日志 `[raghook]`；chat `/internal/rag/*`；Qdrant |

## 10. 下一步

1. 给前厅补齐 Thread 订阅/独立 followup 或 outbox 重试（当前同帖合并已做，公开状态只回原 origin）。
2. 工雁 Claude Code 单次模型调用/transcript 上报（目前只有小秋/Agent/前厅留痕）。
3. 节点容量从 `n-cloud-skein` 扩展；节点离线/作业失败信号保持人工验收。
4. 需要彻底清理 `/root/yanzhen` 时再单独确认。
