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
- 帖子先用规则过滤明显无关内容；**一级评论和楼中楼（ccomment）一律交给 LLM 判断**，避免“补充/还是不行”这类跟进被关键词漏掉；
- **所有吐槽/反馈都回**；只有 `bug` / `request` 调 Skein `intake` 建单；
- 同一帖子已有活跃工单时，新评论/楼中楼作为 followup 合并到该工单，不重复建单；
- 回执由小秋以机器人账号发到原帖/原评论/原楼中楼，带工单号 `SK-...`；楼中楼回执用 `ReplyCcommentID` 嵌在原回复下；
- 建单失败仍然回执，只是没有工单号；
- 每条前厅判断都写入 Skein `llm_calls`，`source=frontdesk`，会话 `forum:post:<id>` / `forum:comment:<id>` / `forum:ccomment:<id>`。

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
  "source": "forum_post | forum_comment | forum_ccomment | chat",
  "thread_id": "帖子 ID（评论/楼中楼也填所属帖子 ID）",
  "message_id": "帖子/评论/楼中楼 ID",
  "parent_id": "楼中楼所属的一级评论 ID（仅 forum_ccomment）",
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
  "created": true,
  "followup": false
}
```

- `source=forum_post|forum_comment|forum_ccomment|chat` 决定 Thread 的 `origin`；`origin_id` 对帖子是帖子 ID，对一级评论是 `帖子ID:评论ID`，对楼中楼是 `帖子ID:评论ID:楼中楼ID`。
- `forum_comment` / `forum_ccomment` 且同帖已有非终态工单时，不新建单：摘要追加 `[followup ...]`，返回同一张单，`created=false, followup=true`。
- 工单已完成/驳回后，同一帖的新评论/楼中楼会重新建单（回归/重开）。
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
  "origin": "forum_post | forum_comment | forum_ccomment",
  "origin_id": "7907 | 7907:23158 | 7910:23166:16151",
  "title": "...",
  "summary": "..."
}
```

- 同一张单、同一公开状态只发一次（`tickets.public_status` 相等时跳过）。
- 小秋收到后调 Go 主站 `POST /internal/xiaoqiu/reply`（`X-Internal-Token`），以 `XQ_BOT_UID` 身份发到原帖/原评论/原楼中楼；楼中楼带 `reply_ccomment_id`，嵌套在原回复下。
- 回写失败只记日志，不阻塞工单；目前没有 outbox 重试（见第 4 节）。

`received`（已收到）不单独发状态消息：它由 monitor 的第一条回执带工单号表达。

## 4. 还没做

- Thread 订阅覆盖一级评论和楼中楼：同帖有活跃工单时自动合并为 followup；独立 `POST /internal/tickets/{id}/followup` 接口未实现；
- 合并后的公开状态只回到工单原 origin（通常是帖子），不会逐条回到每个 followup 评论/楼中楼；
- 公开状态回写没有 outbox 重试，Skein 发失败只记日志；
- 用户没有「我的反馈」状态页，进度只在原帖回复里；
- chat 任务不自动建单，只有显式按钮或论坛 monitor 建单；
- 管理员 ops 对话还没有写操作工具（派工/取消仍走控制台）。

## 5. 已验证

2026-11-21 论坛实测（测试帖与评论已清理）：

1. bug 帖 → monitor 自动建单 `SK-CB70A329` → 原帖回执带工单号；
2. 同帖「补充：控制台还报 500」→ 不再新建单，合并进 `SK-CB70A329`，并在评论下回执；
3. bug 评论（无活跃工单）→ 新建 `SK-CD5A615A`，`origin=forum_comment`、`origin_id=7909:23163`；
4. bug 楼中楼（无活跃工单）→ 新建 `SK-68FFD241`，`origin=forum_ccomment`、`origin_id=7910:23166:16151`；回执和验收状态都嵌套在原楼中楼下；
5. 两张单验收通过 → 原帖收到「已修复」，评论下也收到「已修复」；
6. 纯吐槽 → 只回执、不建单；机器人自己的评论 → 跳过。

## 6. 一句话

**论坛里的每一条吐槽都有人接；可执行的 bug/需求变成 Skein 工单；验收结果回到原帖。**
