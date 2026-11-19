# 秋坞 → Skein 迁移图

> 2026-11-19 · 目标：秋坞的功能全部迁入 Skein，运维面板与秋坞下架
> 原则：先建新路径并验证，再停旧路径；任何一步都可回滚；不直接删资源，只先 scale 0 / suspend。

## 1. 秋坞现在有什么（实测）

**长驻（k3s `qiudock` 命名空间）**

| 工作负载 | 镜像 | 作用 |
| --- | --- | --- |
| `qiudock-platform`（qdockd） | `qdockd:1.4.12-sso` | 平台控制面：调度、配额、会话、LLM 网关 `/v1/llm` |
| `ops-console` | `ops-console:0.3.8` | 运维面板 |
| `ag-xiaoqiu-chat` | `xiaoqiu:1.0.91` | 小秋 chat 面 |
| `ag-xiaoqiu-ops` | `xiaoqiu:1.0.96-sso` | 小秋 ops 面（依赖 QDOCK_API_KEY / MCP / SANDBOX_URL） |
| `sbx-*` ×3 | `qiudock-sandbox:0.1` | warm 沙箱池 |
| `image-pins` | `qiudock-sandbox:0.1` | 沙箱镜像钉 |

**定时作业（CronJob）**：tech-digest daily 09:15 / ingest 每 2h / star 周六 09:25 / weekly 周日 10:00；post-curator daily 10:20。

## 2. 功能 → 归属

| 秋坞能力 | Skein 里的归属 | 状态 |
| --- | --- | --- |
| tech-digest（4 个班次） | **Skein driver（cron 调度）+ runs + Watchdog**，作业体仍用原仓库脚本（`deploy/jobs/run-tech-digest.sh`） | P1，dry-run 中 |
| post-curator（每日） | 同上（`deploy/jobs/run-post-curator.sh`） | P1，dry-run 中 |
| LLM 网关 `/v1/llm` | **直连 `api.ssemarket.cn/v1`**（同 key，`qwen-3.8-flash` 已验证 200） | P2 |
| 沙箱池 `sbx` / `image-pins` | 取消：worker 跑在集市云 pod，pod 即边界 | P3 |
| 小秋 chat 面 | 入口与聊天：短期保留独立容器，中期并入 Router/Worker | P4 |
| 小秋 ops 面 | 依赖 qdockd 的 MCP/沙箱工具；先把工具面替换成 Skein 的节点/日志/worker 能力 | P4 |
| 运维面板 ops-console | Skein 控制台；面板独有的 pod 日志/终端/审批随秋坞一起退场，保留「服务状态 + 日志」到控制台待办 | P5 |
| qdockd 平台 | 下线 | P5 |

## 3. 阶段与验收

### P1 定时作业（进行中）

**进度（2026-11-19）**：包装脚本已上线 `/srv/skein-jobs`；post-curator `--dry-run --limit 3` 通过
（候选 71、评审 3、零写入）；tech-digest `ingest --dry-run` 与 `star --dry-run` 运行中。
下一步：`/etc/skein/jobs.json` 接入 driver，观察一次真跑后再 suspend qiudock CronJob。

1. `deploy/jobs/` 两个包装脚本部署到 `/srv/skein-jobs`：本地镜像仓克隆、venv 依赖、flock 串行、直连网关。
2. dry-run 验收：tech-digest `ingest --dry-run`（不落池）+ `daily --dry-run`（今日已发布会 skip，属正常）；post-curator `scan --dry-run --limit 3`。
3. 调度切到 Skein：`SKEIN_DRIVER=1` + `SKEIN_SCHEDULES`（cron）+ `SKEIN_JOB_COMMANDS`，控制面重启后生效。
4. 明日 09:15 / 10:20 首次真跑，看 `runs` 表与发帖结果；成功后再 `kubectl patch cronjob ... suspend=true`。
5. 观察一周再删除 qiudock CronJob（需人工确认；先 suspend 即可回滚）。

### P2 网关

- 已证：`api.ssemarket.cn/v1` + 现有 key + `qwen-3.8-flash` 可用。
- 待办：把仍指向 `10.43.196.33:7700/v1/llm` 的调用方逐个切直连（小秋 chat 先查它用的是不是直连 key）。

### P3 沙箱池

- worker 已在集市云 pod 跑；`sbx`/`image-pins` 无消费者后 scale 0。

### P4 小秋（进行中）

**已完成（2026-11-19）：统一入口与权限隔离**

- 控制面新增代理层：`/api/v1/agent/*`、`/task/*` → chat 上游（`http://127.0.0.1:8110`），
  `/ops/*` → ops 上游（`http://127.0.0.1:8100`），`SKEIN_QA_CHAT_UPSTREAM` / `SKEIN_QA_OPS_UPSTREAM` 配置。
- 权限边界：chat 只透传用户 JWT，**不转发 Cookie**（SSO 会话不外泄）；ops 只放服务令牌或
  SSO `internal=1`，其它一律 401/403。
- nginx 的 `/api/v1/agent/` 两处 location 已从 `xiaoqiu_chat` 切到 `host.docker.internal:7800`；
  公共路径实测：无 JWT 401、真 JWT 200、SSE `event: ping/content` 正常，未被缓冲。
- 回滚：恢复 `nginx.conf.bak-qa-cutover-*` 并 reload。

**chat 面已迁出 k8s（2026-11-19）**

- 镜像从 k3s containerd 导出并导入 docker（`xiaoqiu:1.0.91`，437MB），容器名 `xiaoqiu-chat`。
- 运行方式：`--network host`、`-e XIAOQIU_PORT=8119`、`--env-file /etc/skein/xiaoqiu-chat.env`（600，
  27 项，从原 Deployment 解出，含 secretKeyRef 解码）、`--restart unless-stopped`、
  `--log-opt max-size=50m --log-opt max-file=3`（宿主有两次满盘史，日志必须限容）。
- 控制面 `SKEIN_QA_CHAT_UPSTREAM=http://127.0.0.1:8119`；`ag-xiaoqiu-chat-v-2611011650` 已
  `scale --replicas=0`（对象保留，可回滚）。
- 验证：容器直连 JWT 200；公共链路（ssemarket.cn → Skein → 8119）真 JWT 200；缩容后仍 200。

**chat 回滚**：`kubectl scale deploy ag-xiaoqiu-chat-v-2611011650 -n qiudock --replicas=1`，
把 `SKEIN_QA_CHAT_UPSTREAM` 改回 `http://127.0.0.1:8110`，重启 `skein-control`。

**未完成**

- ops 面仍在 qiudock 命名空间（k8s Deployment），依赖 `QDOCK_*`（MCP/沙箱/指标）；
  工具面要映射到 Skein 的节点、日志、worker，或被明确裁掉，替换完成前不关 qdockd。
- 面板（ops-console）仍走自己的路径；下线前需要把「服务状态 + 服务日志」补到 Skein 控制台。

### P5 面板与平台

- ops-console 与 qdockd `scale 0`；保留 YAML、镜像、数据备份；**不删 pod/资源对象**。
- 控制台补「服务状态 + 服务日志」两页，替代面板的日常巡检入口。

## 4. 回滚

| 动作 | 回滚 |
| --- | --- |
| suspend CronJob | `kubectl patch cronjob <name> -p '{"spec":{"suspend":false}}'` |
| qdockd scale 0 | `kubectl scale deploy qiudock-platform --replicas=1` |
| 作业改走 Skein | `SKEIN_DRIVER=0` 并恢复 qiudock CronJob |
| 小秋 chat 走 Skein 门面 | 恢复 `/root/SSE_Market/nginx_proxy/nginx.conf.bak-qa-cutover-*` 并 `nginx -s reload`；论坛 chat 立即回到直连 xiaoqiu |

**新的运行依赖**：切流后论坛 chat 依赖 `skein-control` 存活（nginx → 7800 → 8110）。
控制面故障时 chat 会 502；回滚上面一行即可恢复直连。

## 5. 现状依赖

- `ag-xiaoqiu-chat` 的 LLM 用 `LLM_API_KEY_1/2`（需确认是否直连）；`ag-xiaoqiu-ops` 明确依赖 `QDOCK_API_KEY/QDOCK_MCP_URL/QDOCK_SANDBOX_URL`。
- ops-console 依赖 qdockd API；面板下架意味着这部分能力由 Skein 控制台接管或明确放弃。
- 作业已与 qdockd 解耦（直连网关 + 本地镜像仓）。
