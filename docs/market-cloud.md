# 集市云：主力算力，但只能通过 YatTerra 用 pod

> 2026-11-18 · 面向 Skein 架构的执行环境说明
> 事实来源：10-01 全量盘点（`qiudock/docs/ESTATE_INVENTORY_20261001.md`）+ 对旧雁阵代码的检索
> 状态：**许多细节仍待确认**，见文末「未确认」

## 一、它是什么

集市云是我们团队的主力算力集群：

- GPU 推理农场：qwen3.8-27b（主力 LLM）、bge-m3（嵌入）、reranker、Spark 4B、OCR、TTS、ComfyUI
- 共享数据服务：Qdrant（25033/25034）、MySQL（25006）、Redis（25079）
- 统一 RAG（13019）、旧 assist（13012）
- 三十多个团队项目的前后端

**我们不是集群管理员。** 没有 kubeconfig、没有节点 SSH（凭据未提供）、不能改调度、不能装 DaemonSet。对集群本身，我们只有「使用」的权限。

## 二、我们能用什么

### 2.1 通过 frp 隧道消费的服务（今天就在用）

| 能力 | 入口 |
| --- | --- |
| LLM 网关 | `api.ssemarket.cn/v1`（回环 13005 → 对外 18888） |
| 嵌入 bge-m3 | 13011 → 23011 |
| 重排 | 13021 → 23021 |
| Qdrant | 25033 REST / 25034 gRPC |
| 统一 RAG | 13019 → 23019 |
| 共享 MySQL / Redis | 25006 / 25079 |
| S3 中继 | 13037 |

这些是**下游调用**：请求出去、结果回来，不产生我们自己的进程。

### 2.2 通过 YatTerra 平台创建 pod（今天没用上）

YatTerra 是同学做的部署管理平台（`platform-gui` 14000，Vite+React；`web-gui` 17500 是另一个 Go 服务，不是它；`grp-deploy` 13007 当前不可达）。2026-11-18 只读实测（普通用户视角）：

**登录**：账号密码 `POST /api/auth/login`；游客 `POST /api/auth/guest`（只读）；OAuth 走 UniSSO 或 SSE Market；会话用 Cookie + CSRF；个人 API Token 用 `Authorization: Bearer`（`/api/tokens`）。平台域 `cloud.ssemarket.cn`。

**角色**：super / admin / user / guest。user 只能看见自己的 Pod；guest 只读。

**Pod（资源层）**：`POST /api/pods {name, cpu, mem, gpus, storage, node, tag}`，默认 2C/4G/20G；可启停、重启、改名、换节点、resize；有 SSH/Web 终端、文件读写上传、日志指标。

**Deploy（应用层）**：**不能选镜像**。Pod 是平台预置的 `cloud-ubuntu` 类环境，部署只跑仓库或本地脚本：

- 字段：`name`、`source(repo|local)`、`repo/branch/subdir` 或 `script`、私有库 token、`mode(service|oneshot)`、`kind(serve|train)`、`gpu`、`vram`、`health`、`health_timeout`、`auto_deploy`、`contract`（延迟/抢占/截止/预计时长）
- 平台注入：`PORT=8080`、`LOG_DIR`、**`DEPLOY_ID` / `REPORT_URL` / `REPORT_TOKEN`**
- `/shared` 只读挂载；supervisord 拉起服务；oneshot 可重复执行；`PRIORITY` 参与压力驱逐

**状态与日志**：Pod 状态 Running/Stopped/Pending/Failed/Succeeded；`GET /pods/:name/deploys`、`/:id/status`（healthy/unhealthy）、`/:id/logs?tail=200`，另有 SSE 日志流。

**存储**：MinIO。`/api/infra/storage` 可列桶与 key，但 `infra.*` 属管理员面；普通用户能拿到的是 **Pod 自身的存储配额**与 `/shared` 只读。桶的创建/删除权限对普通用户是否开放，未确认。

**配额**：`GET /pods/limits` 返回 `{scoped, caps{cpu, mem_gib, storage_gib, gpu_allowed}}`，超限拒绝或夹取；按 Pod 资源逐小时从个人额度扣费。

**出网**：未确认。间接证据是可以部署 GitHub/Gitee 仓库、模板里有 `pip install`，说明至少能拉依赖与仓库。

**这直接推翻了两条早期假设**：① 不能自带镜像，worker 只能是「仓库 + 启动脚本」；② 平台已经提供 `REPORT_URL`/`REPORT_TOKEN`，结果回传不必自建回调入口。

## 三、旧雁阵与集市云的真实关系：零

检索 `server/`、`worker/`、`docs/` 全仓：没有 YatTerra、没有 13007、没有 `deploys`、没有任何创建 pod 的代码。

雁阵的 `nodes` 表里有一个 `pod` 字段，但那是 node agent 自报的机器标识（如 `n-iwiki-host`），不是集市云的 pod。雁阵今天只认**反向拨入的机器**，不认集群里的容器。

**结论：新架构里的「按需执行环境」如果要落在集市云，是从零开始，不是改造。**

## 四、对 Skein 架构的含义

### 4.1 执行环境分两类，契约不同

| | 宿主机上的 node（A / D / E） | 集市云 pod（YatTerra） |
| --- | --- | --- |
| 创建方式 | node agent 主动拨入注册 | 调 YatTerra API 建 deploy |
| 生命周期 | 长期在线 | 由平台管理，我们只能 run/stop |
| 本机状态 | 可以留 worktree、transcript | **不能假设有持久盘** |
| 宿主能力 | 受 systemd cgroup 限制 | 不能要 hostPath / docker.sock / hostNetwork |
| 结果回传 | 反向连接 | 必须主动回写到 A（HTTP 或消息） |
| 失败表现 | 心跳消失 | API 报错、pod 退出、日志可查 |

### 4.2 三条硬约束

1. **不能假设集市云 pod 有持久化。** Session 权威必须在 A；pod 里的 cc transcript 必须按轮同步回来，这与架构里「transcript 同步」的裁决正好对上，而且对 pod worker 是**必需**而不是可选。
2. **不能假设能进 pod。** 没有 exec、没有 attach。排障只能靠 `logs` 接口和 worker 主动上报。
3. **不能把 YatTerra 当可靠调度器。** 它是同学的平台，可能限流、可能改版、可能挂。我们的调度必须能容忍「提交失败」「提交成功但没起来」「起来了但结果没回来」。

### 4.3 因此调度器要多一层

- 提交前：确认 YatTerra 可达、token 有效。
- 提交后：记下 deploy id，本地记 `submitted`。
- 运行中：定期查 status；超时则 stop 并标失败。
- 结果：worker 主动回写 A；没回写则以 status 为准。
- 重试：同一工单重派要换新 deploy，不能假设旧 pod 还能用。

## 五、未确认（需要用户或 YatTerra 维护者回答）

1. **token**：能不能申请一个只读 + deploy 权限的长期 token。
2. **镜像来源**：worker 镜像从哪个 registry 拉（私有仓库？集市云本地？）。
3. **出网**：pod 能不能反向连接 A（`ssemarket.cn` 或 frp），还是只能 A 主动调它。
4. **资源规格**：deploy 能指定 CPU/内存/GPU 吗，有没有配额。
5. **保留策略**：deploy 停止后日志留多久。
6. **并发**：同时能跑多少个 deploy。

## 六、一句话

**集市云是主力，但它是「租来的算力」：我们能决定跑什么、什么时候停，不能决定它跑在哪、怎么跑、盘在哪。** 新架构必须把这个不对称写进执行环境契约里。
