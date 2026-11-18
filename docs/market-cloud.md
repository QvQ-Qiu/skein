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

YatTerra 是同学做的部署管理平台，API v1（`grp-deploy` 13007、`platform-gui` 14000、`web-gui` 17500）：

- `groups`：项目组生命周期（start / stop / restart / logs）
- `deploys`：组内部署 CRUD，run / stop / status / logs
- `databases`、`storage`：库与桶的凭据发放
- `host/health`、`audit`、`tokens`、`users`

**认证是 Bearer token，token 尚未提供给我们。** 也就是说：能力存在、接口清楚，但我们今天没有拿到钥匙，也没有任何代码接入它。

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
