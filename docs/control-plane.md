# 控制面（services/control）

> 2026-11-21 · 重构后的当前状态：无秋坞、无影子、无旧版兼容；控制面只做雁阵自己的事。

## 一、模块

| 模块 | 文件 | 说明 |
| --- | --- | --- |
| 领域库 | `skein/db.py` | SQLite WAL，schema v10；工单/线程/事件/运行/调用/Agent 消息 |
| 准入 | `skein/admission.py` | 确定性 ignore/record/process；不调模型 |
| 控制台 Agent | `skein/agent.py` | 管理员用自然语言查/操作雁阵；工具调用 + 审计 + 调用留痕 |
| 工单/线程 | `skein/store.py` | Thread 归并、建单、去重 |
| 节点通道 | `skein/channel.py` | WebSocket 注册/心跳/建 worker/派单/结果；多节点 |
| 调度 | `skein/scheduler.py` | 在线窗口、容量、平台匹配 |
| 定时作业 | `skein/driver.py`、`runs.py` | cron 到点执行，按计划分钟去重 |
| Watchdog | `skein/watchdog.py`、`watchdog_agent.py` | 确定性信号 + 自动建单 + 每日巡检 |
| 会话权威 | `skein/session_store.py` | 索引 + 内容寻址 blob；逐轮幂等 |
| SSO | `skein/sso.py` | UniSSO 内接；internal=集市用户、is_admin=管理员 |
| 小秋门面 | `skein/qa_proxy.py` | chat/ops 上游代理；chat 带签名身份头 |
| 调用留痕 | `skein/llm_trace.py` | 小秋/Agent 上报的输入、输出、tokens、耗时 |
| 控制台 | `services/console/` | Vue 3；工作台/Agent/需求/运行/平台 |

已删除：`poll.py`、`ingest.py`、`forum_source.py`（改为小秋 intake）、
`shadow.py`、`review.py`（影子对比）、`compat.py`（旧 yanzhen 写入口）、
`host.py`、`router/`（旧 Router 常驻链路）、`outbox.py`、`channels.py`（无消费者的对外写）、
`services/console/legacy`、`deploy/p0`、`migrate_yanzhen.py`。

## 二、跑与测

```bash
make test        # 单元测试
make lint        # ruff
make run-control # 控制面（HTTP）
```

## 三、环境变量

| 变量 | 默认 | 用途 |
| --- | --- | --- |
| `SKEIN_DB` | `data/skein.db` | 领域库路径 |
| `SKEIN_BLOB_ROOT` | `data/blobs` | transcript 内容寻址目录 |
| `SKEIN_TOKEN` | 空 | 内部/节点令牌；为空则机器接口 401 |
| `SKEIN_HOST` / `SKEIN_PORT` | `127.0.0.1` / `7800` | 监听地址 |
| `SKEIN_LLM_BASE` / `SKEIN_LLM_KEY` | 空 | OpenAI 兼容网关；Agent 与 Watchdog 共用 |
| `SKEIN_AGENT_MODEL` | `deepseek-v4-flash-vision-exp` | 控制台 Agent 模型 |
| `SKEIN_DISPATCH` | `0` | 事件是否自动建单（当前人工/Agent 建单） |
| `SKEIN_DRIVER` / `SKEIN_JOBS_FILE` | `0` / 空 | 定时作业驱动 |
| `SKEIN_PLATFORM_REPOS` | 空 | 平台 → 仓库路径 JSON |
| `SKEIN_WATCHDOG*` | 关 | 信号、自动建单、每日巡检 |
| `SKEIN_SSO_*` | 空 | UniSSO 内接 |
| `SKEIN_QA_CHAT_UPSTREAM` / `SKEIN_QA_OPS_UPSTREAM` | 空 | 小秋 chat/ops 上游 |
| `SKEIN_ASSERTION_KEY` | 空 | chat 身份头签名，与小秋 `SKEIN_USER_SECRET` 相同 |
| `SKEIN_OPS_PANEL_SECRET` | 空 | 给 ops 上游代签面板会话 |

## 四、接口

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/health/live` | 无 | 存活 |
| GET | `/health/ready` | 无 | DB 就绪 |
| GET | `/api/session` | 无 | SSO 探测；`surface=market` 放集市用户 |
| POST | `/api/session/login` | 无 | 邮箱+密码 → UniSSO → Cookie |
| POST | `/api/session/logout` | 无 | 单点登出 + 双清 Cookie |
| GET/POST | `/api/v1/agent/*`、`/task/*` | chat 上游 | 小秋 chat 代理 |
| GET/POST | `/ops/*` | 管理员 | 小秋 ops 感知代理 |
| GET | `/internal/summary` | 令牌/管理员 | 计数概览 |
| GET | `/internal/stream` | 令牌/管理员 | SSE 快照 |
| POST | `/internal/tickets` | 令牌 | 外部建单（Watchdog/脚本） |
| POST | `/internal/intake` | 令牌 | 前厅（小秋）建单：来源/thread/requester/摘要；dedup 幂等，评论/楼中楼自动 followup |
| POST | `/internal/tickets/{id}/dispatch` | 令牌/管理员 | 派给节点 |
| POST | `/internal/tickets/{id}/cancel` | 令牌/管理员 | 取消在途派工 |
| POST | `/internal/tickets/{id}/review` | 管理员 | approve / reject |
| POST | `/internal/agent/chat` | 管理员 SSO | 控制台 Agent 对话 |
| GET | `/internal/agent/thread/{id}` | 管理员 SSO | Agent 历史 |
| POST | `/internal/llm/calls` | 令牌 | 上报一次模型调用 |
| GET | `/internal/llm/sessions` / `calls` | 令牌/管理员 | 会话与调用明细 |
| GET | `/internal/runs` | 令牌/管理员 | 作业运行记录 |
| GET | `/internal/nodes` / `signals` / `prompts` | 令牌/管理员 | 节点/信号/提示词 |
| POST | `/internal/worker/turn` | 令牌 | transcript 上传 |
| WS | `/api/agent/channel` | 协议内 token | 节点反向拨入 |

## 五、数据

`events`、`threads`、`tickets`、`assignments`、`subscriptions`、`runs`、
`prompt_revisions`、`prompt_pointers`、`audit`、`kv`、`sessions`、`session_turns`、
`dispatches`、`nodes`、`llm_calls`、`agent_messages`。

## 六、高可用与备份

- `skein-control` systemd `Restart=always`；节点 WebSocket 自动重连，重启不丢状态。
- 每日 `deploy/scripts/backup-skein.sh` 用 SQLite backup API 备份到 `/srv/skein/backups`，保留 7 天。
- 单机 SQLite 仍是单写点；要多控制面需换 Postgres。当前规模用「进程自愈 + 每日备份 + 多节点执行」满足。
