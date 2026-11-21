# 会话与模型调用留痕（Skein 内）

> 2026-11-21 · 不接 Langfuse；Skein 自己存、自己看。

## 1. 存什么

SQLite `llm_calls`（schema v8）：

| 字段 | 说明 |
| --- | --- |
| `session_id` | 聊天 thread_id / 业务会话 ID |
| `actor` | unisso `sub`（小秋回报时带） |
| `source` | `skein` / `chat` / `ops` |
| `model` | 模型名 |
| `prompt` / `response` | 截断到 16k 字符 |
| `prompt_tokens` / `completion_tokens` | 网关或 LangChain 返回的用量；没有就 0 |
| `latency_ms` | 调用耗时 |
| `status` / `error` | `ok` / `error` |
| `created_at` | 服务端时间 |

## 2. 谁在写

- **Skein 自己**：`HttpLLM`（Router / 直答）每次补全自动写，`source=skein`。
- **小秋 chat / ops**：`app/observability/skein_trace.py` 的 LangChain 回调，按 thread_id 归组，`source=chat` / `ops`，`actor=unisso sub`；不再发 Langfuse。
- **工雁（cc）**：暂未上报单次模型调用；它的 transcript 走会话权威，另算。
- **定时作业**：tech-digest 等脚本用自己的模型客户端，暂未接入。

## 3. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/internal/llm/calls` | 上报一次调用（机器令牌） |
| GET | `/internal/llm/sessions` | 按 session 聚合：调用数、tokens、最近模型/状态 |
| GET | `/internal/llm/calls?session_id=&source=&limit=` | 某会话的调用明细 |

## 4. 控制台

「运行 → 会话」：

- 左边按会话列出：来源、调用数、tokens、用户、最近时间；
- 右边是每次调用：模型、延迟、tokens、状态；展开看输入/输出；
- 10 秒自动刷新；点左侧会话切换。

## 5. 配置

- 控制面：无新增配置，`llm_calls` 表随 schema 自动建。
- 小秋 chat / ops 容器：
  - `SKEIN_CONTROL_URL=http://172.17.0.1:7800`
  - `SKEIN_TOKEN=<控制面服务令牌>`
  - 删除 `LANGFUSE_*`（不再上报 Langfuse）
- 留痕失败只写 debug 日志，不影响对话。

### 5.1 模型池（2026-11-21）

- qdockd 关闭后，chat/ops 主模型改直连 `https://api.ssemarket.cn/v1` + `qwen-3.8-flash`
  （`LLM_API_KEY_1`）；不再走 `10.43.196.33:7700/v1/llm`。
- 生产 settings 从容器里取出，只替换 `llm` / `ops_llm` 段，存
  `/etc/skein/xiaoqiu-chat-settings.yaml` 与 `/etc/skein/xiaoqiu-ops-settings.yaml`，
  容器以只读挂载覆盖镜像内配置。
- 镜像：chat `xiaoqiu:1.0.95-skein-trace`，ops `xiaoqiu:1.0.100-skein-trace`。
- Langfuse env、回调、ops 的 `langfuse_trace` 工具与 `lftrace` 模块已移除。
- 烟测：chat 任务返回 200，留痕显示 `qwen-3.8-flash`、7738/35 tokens、28.4s。

## 6. 还没做

- 工雁（cc）单次模型调用上报；定时作业模型调用上报。
- 自动清理（保留天数）与敏感字段脱敏。
- 控制台按模型/用户搜索、成本换算。

## 7. 一句话

**不靠 Langfuse；Skein 自己留每一次会话和模型调用，控制台里能直接翻。**
