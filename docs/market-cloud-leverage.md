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

## 二、两块缺失的基础件

### 2.1 用 S3 当「盘」

pod 没有持久盘，但它有 YatTerra 的 storage API（buckets/keys）。这正好补上架构里 Session 权威的存储形态：

- Session 索引行留在 A 的领域库；
- transcript、git bundle、脏补丁、大产物上传到 S3，按 sha256 内容寻址；
- A 只存指针，不存字节。

这样「pod 无状态」不再是缺陷，而是默认形态。

### 2.2 写回中继

pod 里的 worker 拿不到论坛回环，但可以打 A 的公网 HTTPS。需要在 A 上开一个受控入口：

```
POST https://ssemarket.cn/internal/outbox/emit
Header: X-Skein-Token: <per-job token>
Body: { "kind": "post|comment|featured", "object": "...", "payload": {...}, "effect_key": "..." }
```

- 每张工单发一个短期 token，绑定 ticket_id 与允许的动词；
- `effect_key` 交给 Outbox 做单次写，重复提交返回已存在；
- 校验在 A 上，pod 只负责请求。

这比直接给 pod 论坛凭据安全得多，也和架构里「Worker 只交结果」一致。

## 三、Cloud Worker 契约

| 环节 | 做法 |
| --- | --- |
| 提交 | 调 YatTerra `deploys` 创建，名字以 `skein-` 开头，带资源规格与 TTL |
| 输入 | 工单内容 + 短期 token + S3 预签名地址，全部走环境变量，不落盘 |
| 运行 | 镜像里只跑任务本体；日志写 stdout（YatTerra 可查） |
| 产物 | 上传 S3，回执带 sha256；小结果可直接放进回调 body |
| 回写 | 只通过 `outbox/emit`，不直接碰论坛 |
| 心跳 | 每 30 秒打一次 `POST /internal/worker/heartbeat`，带 deploy_id |
| 结束 | 主动 stop；A 侧超时也会 stop |
| 失败 | 分三类：提交失败 / 起来了但没心跳 / 有心跳但没回执。分别重试、等 TTL、查日志 |

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

1. token 是否能拿到，权限范围多大（能否只给 deploy + logs）。
2. 镜像从哪个 registry 拉，能不能用我们的私有仓。
3. pod 能否访问公网/A（决定是「pod 回调 A」还是「A 轮询拉结果」）。
4. 能否指定 GPU 与资源规格，配额多少。
5. S3 的桶与凭据由谁发、容量与保留策略。
6. deploy 停止后日志保留多久（决定失败排查窗口）。

## 七、一句话

**集市云让我们能把「算」送出去，把「写」留在 A。** 这正好对应架构里两条已有的分工：Worker 只交结果，Outbox 独占写权限。
