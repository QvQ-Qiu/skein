# 控制面（services/control）

> 2026-11-18 · 对应 `docs/architecture.md` 的落地状态

## 一、已经实现的

| 模块 | 文件 | 说明 |
| --- | --- | --- |
| 领域库 | `skein/db.py` | SQLite，schema v5，11 张表，外键开启、WAL |
| 准入矩阵 | `skein/admission.py` | 确定性三档：ignore / record / process；不调模型 |
| 直答契约 | `skein/router/direct.py` | 请求冻结 + 序列化复核；响应深检；工具调用一律降级 dispatch |
| 事件分派 | `skein/router/dispatch.py` | 工具集写死为 route/attach/ignore/stop/note |
| Router 宿主 | `skein/host.py` | 聊天直答、事件分派；建单与审计同一事务 |
| Session 权威 | `skein/session_store.py` | 索引行 + 内容寻址文件；按轮幂等；节点钉住 |
| Outbox | `skein/outbox.py` | 单次写、失败落库、重启对账 |
| HTTP 接口 | `skein/api.py` | 健康、聊天、transcript、写回、概览 |
| 采集 | `skein/ingest.py`、`skein/forum_source.py` | 公开视图 → Trigger；水位；Thread 归并 |
| 影子派单 | `skein/shadow.py` | 真模型判断，只记录，零副作用 |
| 通道 | `skein/channels.py` | 论坛内部口写入，禁用系统代理 |
| 模型客户端 | `skein/llm_http.py` | OpenAI 兼容网关，强制 JSON |

## 二、还没实现的

- **真派单**：现在 `verdict=process` 只进影子，不建单。等影子对比确认后再开。
- **Watchdog**：信号采集、金丝雀、提示词写入都没做。
- **节点侧 transcript 同步**：控制面已能收，`services/node-agent` 还没发。
- **集市云执行环境**：YatTerra 客户端未写，token 未拿到。
- **面板**：`services/console` 还是旧代码。
- **调度**：`runs` 表有结构，没有驱动。

## 三、怎么跑

```bash
make test        # 76 个测试
make lint        # ruff
make run-control # 控制面（HTTP）
make run-poll    # 采集循环
```

### 环境变量

| 变量 | 默认 | 用途 |
| --- | --- | --- |
| `SKEIN_DB` | `data/skein.db` | 领域库路径 |
| `SKEIN_BLOB_ROOT` | `data/blobs` | transcript 内容寻址目录 |
| `SKEIN_TOKEN` | 空 | 内部接口令牌；为空则全部 401 |
| `SKEIN_PORT` | `7700` | 控制面监听端口（仅 127.0.0.1） |
| `SKEIN_DIRECT_MODEL` | `direct` | 直答模型 |
| `SKEIN_ROUTE_MODEL` | `route` | 事件分派模型 |
| `SKEIN_LLM_BASE` / `SKEIN_LLM_KEY` | 空 | 模型网关 |
| `SKEIN_FORUM_BASE` / `SKEIN_FORUM_TOKEN` | `http://127.0.0.1:8080` / 空 | 论坛写入口 |
| `SKEIN_FORUM_DB_*` | 本机 3506 | 论坛只读库（采集用） |
| `SKEIN_POLL_INTERVAL` | `60` | 采集间隔（秒） |
| `SKEIN_SHADOW` | `0` | 开影子派单；需要模型网关 |

## 四、接口

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/health/live` | 无 | 存活 |
| POST | `/internal/chat` | 有 | 聊天直答/分派 |
| POST | `/internal/worker/turn` | 有 | transcript 上传（base64） |
| POST | `/internal/outbox/emit` | 有 | 对外写，单次 |
| GET | `/internal/summary` | 有 | 计数概览 |

鉴权头：`X-Skein-Token`。

## 五、数据

领域库里的表：`events`、`threads`、`tickets`、`assignments`、`subscriptions`、`runs`、`effects`、`prompt_revisions`、`prompt_pointers`、`audit`、`kv`、`sessions`、`session_turns`、`shadow_decisions`。

三份权威的边界不混：transcript 在内容寻址文件，工单在领域库，帖与评论在论坛 MySQL。

## 六、影子对比

影子派单只记录判断，是否可靠要靠人工对照：

```bash
make review-export   # 导出待标注样本到 labels.csv
# 填写 expected_action 列（route / attach / ignore / stop / note）
make review-report   # 输出一致率、混淆矩阵与分歧列表
```

报告包含：影子总数、已标注数、覆盖率、一致率、混淆矩阵（行=人工，列=模型）以及前 20 条分歧。

## 七、下一步

按 `docs/migration.md` 的 S3–S5：影子对比 → 准入接管采集 → Session 同步接节点。
