# 事件与前厅闭环：当前契约

> 2026-11-21 · 已上线：论坛吐槽/反馈 → 小秋回执 → Skein 建单 → 管理员验收 → 原帖回写。
> 本文只描述正在运行的路径；未实现的点单独列在最后。

## 1. 两条入口

### 1.1 聊天任务（记录型）

集市用户通过 SSO 登录后，发到下面三条路径的任务消息，会在转发给小秋之前被 Skein 记成一条事件：

- `POST /api/v1/agent/task`
- `POST /api/v1/agent/task/sse`
- `POST /task/sse`

| 字段 | 值 |
| --- | --- |
| `origin` | `chat` |
| `thread` | `session_id`（同一聊天会话归一条 Thread） |
| `origin_id` | `session_id#sha256(task)[:16]`（重试幂等） |
| `actor` | unisso `sub` |
| `verdict` | 默认 `record`（只记账，不自动建单） |

- 只有通过 SSO 的请求会记账；只有论坛 JWT 的请求不记。
- 事件在控制台「运行 → 事件」可见；Thread 的 `origin=chat`。
- bootstrap / close / resume 不算任务，不记账。

聊天里显式转工单仍可用 `POST /api/v1/agent/ticket`（任意集市用户 + SSO），只建 `staged` 单，不自动派工。

### 1.2 论坛前厅（闭环型）

小秋 chat 容器里的 `frontdesk` monitor 每 60 秒轮询论坛最近 10 分钟的帖子和评论：

- 机器人账号（`XQ_BOT_UID=95317`）自己的内容直接跳过；
- 规则先过滤明显无关内容，再用 DeepSeek 判断 `reply / skip` 和 `bug / request / venting`；
- **所有吐槽/反馈都回**；只有 `bug` / `request` 调 Skein `intake` 建单；
- 回执由小秋以机器人账号发到原帖/原评论，带工单号 `SK-...`；
- 建单失败仍然回执，只是没有工单号；
- 每条前厅判断都写入 Skein `llm_calls`，`source=frontdesk`，会话 `forum:post:<id>` / `forum:comment:<id>`。

monitor 配置（chat 容器环境变量）：

| 变量 | 生产值 | 说明 |
| --- | --- | --- |
| `FRONTDESK_ENABLED` | `1` | 开前厅 |
| `FRONTDESK_INTERVAL` | `60` | 轮询间隔（秒） |
| `FRONTDESK_LOOKBACK` | `600` | 只看最近多少秒的新内容 |
| `FRONTDESK_MAX_PER_HOUR` | `12` | 每小时最多回多少条，防刷屏 |
| `FRONTDESK_MODEL` | `deepseek-v4-flash-vision-exp` | 分类模型 |
| `FRONTDESK_LLM_BASE` | `https://api.deepseek.com/v1` | 分类模型网关 |
| `SKEIN_CONTROL_URL` | `http://172.17.0.1:7800` | Skein 控制面 |
| `SKEIN_TOKEN` | 控制面服务令牌 | intake / 留痕 |
| `GO_INTERNAL_URL` | `http://127.0.0.1:8080` | 主站 Go 服务 |
| `INTERNAL_TOKEN` | 与 Go 服务一致 | 发帖回执 |
| `XQ_BOT_UID` | `95317` | 小秋论坛账号 |

## 2. 小秋 → Skein：intake

`POST /internal/intake`（`X-Skein-Token`）：

```json
{
  "source": "forum_post | forum_comment | chat",
  "thread_id": "帖子 ID（评论也填所属帖子 ID）",
  "message_id": "帖子或评论 ID",
  "requester": "forum:<userID>",
  "title": "登录按钮没反应",
  "summary": "点登录按钮没反应，控制台报 500",
  "category": "bug | request | venting",
  "severity": "low | normal | high",
  "visibility": "market",
  "dedup_key": ""
}
```

返回：

```json
{
  "ticket_id": "...",
  "code": "SK-...",
  "status": "staged",
  "public_status": "received",
  "created": true
}
```

- `source=forum_post|forum_comment|chat` 决定 Thread 的 `origin`；`origin_id` 对帖子是帖子 ID，对评论是 `帖子ID:评论ID`。
- `dedup_key` 为空时用 `source|thread|message|requester|title|summary` 的 sha256 前 32 位；重复调用返回同一张单，`created=false`。
- `origin`、`origin_id`、`requester`、`public_status` 落在 tickets 表，管理员在工单列表能直接看到来源和报障人。
- 建单只是 `staged`，不自动派工；管理员在控制台派工/验收。

## 3. Skein → 小秋：公开状态回写

Skein 在以下时机调用小秋：

| 内部动作 | 公开状态 | 小秋在原帖回 |
| --- | --- | --- |
| 派工成功 | `working` | “小秋更新：已收到，处理中（SK-...）。” |
| 验收通过 `done` | `fixed` | “小秋更新：已修复，维护者已验收（SK-...）。” |
| 验收驳回 `rejected` | `need_info` | “小秋更新：这条还需要补充复现步骤（SK-...）。” |

请求：

```http
POST {SKEIN_QA_CHAT_UPSTREAM}/internal/skein/notify
X-Skein-Token: <SKEIN_TOKEN>
Content-Type: application/json

{
  "ticket_id": "...",
  "code": "SK-...",
  "public_status": "working | fixed | need_info",
  "origin": "forum_post | forum_comment",
  "origin_id": "7907 | 7907:23158",
  "title": "...",
  "summary": "..."
}
```

- 同一张单、同一公开状态只发一次（`tickets.public_status` 相等时跳过）。
- 小秋收到后调 Go 主站 `POST /internal/xiaoqiu/reply`（`X-Internal-Token`），以 `XQ_BOT_UID` 身份发到原帖或原评论。
- 回写失败只记日志，不阻塞工单；目前没有 outbox 重试（见第 4 节）。

`received`（已收到）不单独发状态消息：它由 monitor 的第一条回执带工单号表达。

## 4. 还没做

- Thread 订阅直通：后续评论仍按新条目过 monitor，没有直接追加到已有工单；
- `POST /internal/tickets/{id}/followup` 尚未实现；
- 公开状态回写没有 outbox 重试，Skein 发失败只记日志；
- 用户没有「我的反馈」状态页，进度只在原帖回复里；
- chat 任务不自动建单，只有显式按钮或论坛 monitor 建单；
- 管理员 ops 对话还没有写操作工具（派工/取消仍走控制台）。

## 5. 已验证

2026-11-21 论坛实测（测试帖与评论已清理）：

1. 用户发帖 → monitor 分类为 bug → 自动建单 `SK-6E4BFA9E` → 原帖回执带工单号；
2. 管理员验收通过 → `public_status=fixed` → 原帖收到「已修复，维护者已验收」。

## 6. 一句话

**论坛里的每一条吐槽都有人接；可执行的 bug/需求变成 Skein 工单；验收结果回到原帖。**
