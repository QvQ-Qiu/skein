# 集市云能怎么发挥

> 2026-11-18 · 在「只能通过 YatTerra 用 pod」的约束下，能真正拿到的能力
> 前提：token、镜像源、出网、配额四项待确认（见 market-cloud.md 末节）

## 一、先划清能做什么

| 用法 | 价值 | 可行性 | 依赖 |
| --- | --- | --- | --- |
| GPU 批处理（嵌入回填、重排打分、OCR、TTS） | 高 | 高 | 镜像 + S3 |
| 代码 Worker（cc 改码） | 高 | 中 | 镜像 + 出网 + transcript 同步 |
| CI / 测试跑批 | 中高 | 高 | 镜像 + 结果回传 |
| 日报 / 精华帖的生成段 | 中 | **低** | 它们要打 `127.0.0.1:8080`，pod 里没有回环 |
| 自建模型服务 | 低 | 低 | 与同学现有服务重复，不碰 |

**结论：能发挥的是「算」，不是「写」。** 集市云 pod 适合做无状态、结果可回传的重活；任何需要本机回环或宿主能力的事仍然只能在 A 上。

## 二、两块缺失的基础件（按 YatTerra 实测修正）

### 2.1 用 Pod 存储当「盘」，MinIO 待确认

YatTerra 的 Pod 自带存储配额（默认 20G），这是 pod worker 唯一能确定拿到的持久空间。MinIO 桶虽然存在，但 `/api/infra/storage` 属管理员面，普通用户能否建桶与发 key **未确认**。

因此：

- transcript、未推送的 git bundle、脏补丁，在每轮结束时写到 **Pod 本地存储 + 平台注入的 `REPORT_URL`**；
- 是否能长期留在 MinIO，等权限确认后再定；
- A 上的 Session 权威仍存索引与内容寻址文件——pod 的本地存储只是传输中转，不是权威。

### 2.2 两套回传，用途不同

平台已经注入 `DEPLOY_ID` / `REPORT_URL` / `REPORT_TOKEN`，所以**进度与结果不需要我们自建回调**，直接用平台通道。

但论坛写仍然需要 A 上的中继，因为 pod 没有论坛凭据，也不该有：

```
POST https://ssemarket.cn/internal/outbox/emit
Header: X-Skein-Token: <per-job token>
Body: { "kind": "post|comment|featured", "object": "...", "payload": {...}, "effect_key": "..." }
```

两者分工：**平台通道报「干到哪了」，Outbox 报「要不要写出去」。** 前者是遥测，后者是写权限。

## 三、Cloud Worker 契约

| 环节 | 做法 |
| --- | --- |
| 提交 | `POST /api/pods` 建 Pod（cpu/mem/gpus/storage/node），再建 deploy（`source=repo`，指定分支与启动脚本，`mode=oneshot`） |
| 输入 | 工单内容、短期工单 token、仓库地址与分支，通过 `POST /pods/:name/env` 注入 |
| 运行 | **不能自带镜像**：在平台预置的 `cloud-ubuntu` 环境里 clone 仓库、装依赖、跑脚本；日志走 stdout |
| 进度 | 用平台注入的 `REPORT_URL` / `REPORT_TOKEN` 上报，不自建心跳通道 |
| 产物 | 写 Pod 本地存储；能否进 MinIO 待确认 |
| 回写 | 只通过 A 的 `outbox/emit`，不直接碰论坛 |
| 结束 | oneshot 自然结束，或主动 stop；A 侧超时也会 stop |
| 失败 | 分三类：提交失败 / 起来了但没上报 / 上报了但没回执。分别重试、查 logs、标失败 |

## 四、调度器要加的一层

工单派发时按能力选后端：

```
能力需求 → HostWorker（A/D/E） | CloudWorker（集市云 pod）
```

- 需要回环、宿主盘、长期会话 → Host
- 需要 GPU、需要算力突增、可以无状态 → Cloud
- 两者都可用时优先 Cloud，把 A 的 4 核留给论坛与 Router

## 五、礼貌与配额（共享集群）

1. 最多同时 N 个 deploy（建议先 2–3 个），跑完立刻 stop。
2. 资源规格写死上限，不申请超出任务需要的量。
3. 名字统一 `skein-` 前缀，便于同学在平台上认出是谁的。
4. 不碰其它 group，不读别人的 logs。
5. 平台不可用时**不重试风暴**：退避、上限、失败就回 Host。

## 六、待确认（决定这套能不能落地）

1. 账号与权限：用谁的身份接入？普通 user 能否建 Pod 与 deploy，个人 API Token 能否长期使用。
2. 仓库访问：私有仓库的 token 怎么给，平台从 GitHub/Gitee 拉代码时走什么网络。
3. 出网：pod clone 仓库与装依赖已见间接证据，但能否访问 A（`ssemarket.cn`）仍未确认。
4. 资源与配额：`/pods/limits` 的实际数值，GPU 是否可申请。
5. 存储：Pod 本地存储在 stop/重建后是否保留；普通用户能否建 MinIO 桶、发 key。
6. 基础环境：`cloud-ubuntu` 预装了什么（Python 版本、git、pip），决定 worker 的启动脚本怎么写。
7. 日志保留：deploy 停止后日志留多久，决定失败排查窗口。
8. 并发：同时能跑几个 deploy。

## 七、一句话

**集市云让我们能把「算」送出去，把「写」留在 A。** 这正好对应架构里两条已有的分工：Worker 只交结果，Outbox 独占写权限。
