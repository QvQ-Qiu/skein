# 聊天事件 → Skein：首版契约

> 2026-11-21 · 小秋 chat 接进雁阵的第一步：先记账，再显式转工单。

## 1. 现在接了什么

集市用户通过 SSO 登录后，发到下面三条路径的任务消息，会在转发给小秋之前被 Skein 记成一条事件：

- `POST /api/v1/agent/task`
- `POST /api/v1/agent/task/sse`
- `POST /task/sse`

事件字段：

| 字段 | 值 |
| --- | --- |
| `origin` | `chat` |
| `thread` | `session_id`（同一聊天会话归一条 Thread） |
| `origin_id` | `session_id#sha256(task)[:16]`（重试幂等） |
| `actor` | unisso `sub` |
| `verdict` | 默认 `record`（只记账，不建单） |

- 只有通过 SSO 的请求会记账；只有论坛 JWT 的请求不记。
- 事件在控制台「事件」页可见；Thread 的 `origin=chat`。
- bootstrap / close / resume 不算任务，不记账。

## 2. 显式转工单

`POST /api/v1/agent/ticket`，任意集市用户（需 SSO）：

```json
{
  "thread_id": "会话 ID",
  "title": "登录坏了",
  "summary": "点登录按钮没反应，控制台报 500"
}
```

返回：

```json
{
  "ticket_id": "...",
  "code": "SK-...",
  "status": "staged",
  "existing": false
}
```

- 只建 `staged` 工单，不自动派工；管理员在控制台派工/验收。
- `dedup_key=chat:<sha256(sub|thread|title|summary)>`，重复点返回同一张单，`existing=true`。
- summary 末尾带 `[chat] user=<sub> thread=<session_id>`，管理员能看到来源。

这是给聊天前端「转工单」按钮用的后端契约；按钮还没接。

## 3. 还没做

- 不按每条聊天自动建单（避免刷屏）；要不要加 LLM 语义过滤（`process → 建单`）再说。
- 集市用户还没有「我的工单」状态页（`/api/me` 未做）。
- 控制台工单详情还没显示「来自哪个聊天 thread」的时间线。
- ops 面仍是固定 `panel_sub`，未按真实管理员签断言。

## 4. 下一步

1. 聊天前端加「转工单」按钮，调 `/api/v1/agent/ticket`。
2. 控制台工单详情显示来源聊天 thread 与会话摘要。
3. 需要时再开 chat 的语义准入（只对明确报障/需求建单）。

## 5. 一句话

**聊天先变成雁阵能看见的事件；用户点「转工单」才建单。** 不自动刷单，也不丢上下文。
