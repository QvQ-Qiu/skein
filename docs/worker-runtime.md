# Worker 运行环境：当前能力、业界方案对比与定稿

> 2026-11-19 · 面向「工雁在哪跑、代码在哪改、隔离做到哪一层」的定稿文档
> 结论先行：**暂不分级，全部内部可信，统一用 L1 进程沙箱（systemd-run + cgroup + nft）；代码只在仓库里改；gVisor / microVM 作为升级路径备查。**

## 0. 决策摘要

| 问题 | 定稿 |
| --- | --- |
| 代码在哪改 | 仓库里的 worktree；节点只执行，不存代码 |
| 默认隔离层 | **L1 进程沙箱**（systemd-run + cgroup + nft），所有任务统一 |
| gVisor（L2） | 代码路径保留，**暂不启用**；接入外部/不可信仓库时再切 |
| microVM（L3） | 本机无 `/dev/kvm`，不列入当前计划 |
| 凭据 | 沙箱里只放仓库 deploy key（限分支）与 LLM 网关 key；生产密钥不进 worker |
| 出网 | nft 端口白名单（GitHub / LLM 网关 / 控制面 / 论坛回环） |
| harness | 可替换：`claude` 或 `pi`，调度与沙箱不动 |
| 红线 | 不删 pod；不手改运行中代码；不自动合入 |

## 1. 当前能力（事实清单）

### 1.1 控制面（`services/control`）

- 准入矩阵（程序判定，不调模型）、Thread/工单归并、派工、验收、取消、跟进。
- WebSocket 节点通道：`node.register/heartbeat`、`worker.create/created`、`dispatch/ack/result`、`review`。
- 会话权威：index + 内容寻址 blob，逐 turn 幂等，节点 pin。
- Outbox 单写 + reconcile；论坛兼容写回。
- 提示词金丝雀（可编辑四段、乐观锁、改幅上限、两次回滚冻结）。
- Watchdog 确定性信号；影子判定；运行记录；审计。
- 实时推送 `GET /internal/stream`（SSE，2s 快照差分 + 15s 心跳）。
- UniSSO 内接登录：`/api/session`、`/login`、`/logout`；`/internal/*` 双认证（令牌 or 会话）。

### 1.2 节点与执行（`services/node-agent`）

- 拨入注册 + 心跳；按指令创建/销毁 worker 会话。
- 每个 worker 独立 git worktree，跨单续接同一 cc 会话（`--resume`）。
- transcript 逐轮同步回控制面（失败入队补传）。
- 任务结束回报分支、base commit、commits、diffstat、自测总结。

### 1.3 隔离现状

- `services/node-agent/sandbox.mjs`：**systemd-run 瞬态 service** + 专用 `agent` 用户 +
  cgroup v2（MemoryMax / CPUQuota / TasksMax / RuntimeMaxSec）+ `ProtectSystem=strict` +
  `ReadWritePaths` + nft cgroup 端口白名单。
- **默认关闭**（`SANDBOX_ENABLED=0`）；宿主准备（`agent` 用户、`/srv/agent/jobs`、nft 规则）尚未执行。
- 也就是说：现在真派单，cc 是在宿主上裸跑的。

### 1.4 控制台（`services/console`）

四个工作面（工作台 / 需求 / 运行 / 平台）+ 工作台首屏「阵眼 + 闭环流水线」+ SSE 实时 + UniSSO 登录。

### 1.5 测试与部署状态

- 186 个 Python 测试、9 个 Node 测试、ruff 干净、CI 全绿。
- 服务器 `/root/skein` 已同步；`dist` 已构建。
- **P0 只读演练** `skein-p0.service` 在 7801 跑着（独立库，321 事件 / 9 工单，不写不派）。
- **正式服务未安装**（`skein-control` 未启用，`/etc/skein` 未建）。
- **UniSSO 已注册并验通**：`Skein 控制台`（app_647d8010af88f775），password/verify、session/verify、revoke 全 200。
- 旧雁阵 `yanzhen` 仍在服务；node-agent 未改指向。

### 1.6 硬件与运行时事实（2026-11-19 实测）

| 项 | 值 |
| --- | --- |
| 集市机 k3s | 4 vCPU / 15.5 GiB，Requests 525m CPU（13%）/ 732Mi（4%） |
| `/dev/kvm` | **不存在**（无嵌套虚拟化） |
| gVisor | `runsc` 已装（docker 与 containerd 均注册）；k3s 有 `gvisor` RuntimeClass（runsc） |
| 集市云面板（YatTerra） | admin 账号 caps = 2C / 2Gi / 5Gi，GPU 不允许 |
| unisso | WZ8 上 `build/unisso_v2`，frp 13004 → 32004；源码未纳管 |
| 已用 Docker Runtime | `runc`、`runsc`（qiudock 沙箱在用） |

### 1.7 部署拓扑：每个东西跑在哪（2026-11-19 实测）

三台机器，一条代码线：

| 机器 | 地址 | 角色 |
| --- | --- | --- |
| 开发笔记本 | Windows | 代码仓库、Claude 会话、本地预览 |
| 集市机 A | 175.178.90.215（VM-0-4-ubuntu） | 主力机：入口、主站、秋坞、旧雁阵、P0 演练 |
| WZ8 | 100.97.31.26（58.249.112.36） | 集市云 k3s 集群（`clouds` 命名空间）；UniSSO |

**集市机 A 上正在跑的**：

| 组件 | 跑法 | 地址 |
| --- | --- | --- |
| nginx 反向代理 | 容器 `sse_market_server-nginx_proxy-1` | :80 / :443，所有 `*.ssemarket.cn` |
| 主站后端 / MySQL / Redis | 容器 `server` / `sse_market_db` / redis | 127.0.0.1:8080 / :3506 / 内网 |
| 秋坞与 ops 面板等 | k3s `qiudock` 命名空间，52 个 pod | 集群内 |
| **旧雁阵 controller** | systemd `yanzhen.service`（`server/index.js`） | :3030 |
| **旧雁阵 node-agent** | systemd `yanzhen-node.service`（`worker/node-agent.mjs`） | peer :9101；注册节点 `n-market-host`「集市机沙箱节点」，容量 3 |
| **新控制面 P0 演练** | systemd `skein-p0.service` | 127.0.0.1:7801（只读，独立库） |
| frps | systemd `frps.service` | :7000 控制口、:7501 管理口；把 WZ8:32004 映射为 :13004 |

**WZ8 上**：k3s（:6443）+ 集市云平台管理的几十个 pod；UniSSO `unisso_v2` 监听 127.0.0.1:32004，经 frp 变成 `sso.ssemarket.cn`。

**新体系落地位置**：

| 组件 | 机器 | 形式 | 地址/路径 |
| --- | --- | --- | --- |
| `skein-control` | A | systemd `skein-control.service` | 127.0.0.1:7800 |
| 控制台 | A | 由 control 直接托管 `dist` | `https://skein.ssemarket.cn/console/` |
| 新 node-agent | A | systemd | peer **:9102**（9101 已被旧节点占用） |
| worker（cc） | A | systemd-run 瞬态 service，用户 `agent` | `/srv/agent/jobs/<工单>` |
| 正式库 | A | SQLite WAL | `/srv/skein/skein.db` + `/srv/skein/blobs` |
| 论坛写回 | A | Outbox → 本地主站 | 127.0.0.1:8080 |
| SSO | A → WZ8 | 控制面服务端调用 | `https://sso.ssemarket.cn` |

为什么全放 A：内部可信 + A 还有余量（4C / 15.5 GiB，requests 只占 13% / 4%），多一层 pod 只增加网络和部署复杂度。要扩容时，在任何一台机器上再起一个 node-agent 就是加节点，控制面协议不变。

## 2. 当前方案

### 2.1 形态

```
论坛/群消息 ──► 控制面（准入/工单/派工）──WS──► 节点（node-agent）
                                                   │
                                          worktree + cc 会话
                                                   │
                                     transcript 逐轮回传 ──► 控制面会话权威
                                                   │
                                        结果（分支/提交/diffstat）──► 人验收
```

一句话：**Router 在控制面，Worker 在节点，人审是硬闸。**

### 2.2 代码在哪改（三处合法位置）

| 场景 | 位置 | 动作 |
| --- | --- | --- |
| 工单修复 | 节点的独立 worktree | 改完 push 修复分支，人来合 |
| 紧急排障 | 开发者本机 / 会话 | 改仓库 → push → 服务器 pull/部署 |
| 进 pod | **只读、重启、看日志** | 不手改运行中的代码 |

判定标准只有一条：**改动必须在仓库里、能被下一次部署重现。** 容器里的手改既活不过下一次部署，也会让仓库与线上失配。

### 2.3 现有隔离（L1）

- 隔离对象：worker 进程。
- 边界：文件系统（只读系统 + 指定可写目录）、资源（cgroup）、网络（nft 端口白名单）、时长。
- 优点：零额外依赖、启动快（毫秒）、cc 直接跑。
- 缺点：**仍是宿主内核上的进程**；对「恶意仓库」不构成安全边界（见 §3.4）。

### 2.4 已知缺口

1. 沙箱默认关、宿主未准备 → 现在等价于无隔离。
2. 没有容器/gVisor 档；node-agent 只会 `systemd-run`。
3. 凭据未做 secretless：cc 继承节点环境，理论可读到宿主上放的东西。
4. 出网白名单只到端口级，未按域名/代理。
5. harness 只有 claude，没有 Pi/其它适配。
6. unisso 源码未纳管，「worker 改 unisso」目前不成立。
7. 正式切换未做（安装、nginx、node-agent 指向、开派单）。

## 3. 业界方案 A/B/C

### A. 进程级沙箱 + harness

**代表**：Anthropic `sandbox-runtime`（`srt`，bubblewrap / macOS seatbelt + 代理白名单，2025-10 开源预览）；Claude Code 内置 sandboxing；Pi pod（2026-10，把 pi 会话跑进服务器上的隔离环境，「pod」不是 k8s）；devcontainer 方案。

- 隔离强度：中。文件与网络可限制，但仍共享宿主内核。
- 硬件：无要求。
- 启动：毫秒级。
- 运维：低。
- 适合：**可信仓库**的日常修补、CI 内跑 agent。
- 风险：2026-09 的 Beltdown 证明恶意仓库可通过 `git` 调用逃出 macOS 沙箱且**不触发权限提示**。

### B. 容器级强隔离（gVisor）

**代表**：Docker/containerd + `runsc`（用户态内核，拦系统调用）；我们已有现成能力——qiudock 沙箱就是 docker runtime=runsc，k3s 也有 `gvisor` RuntimeClass。

- 隔离强度：中高。syscall 层过滤，宿主内核暴露面显著缩小。
- 硬件：无要求（不需要 KVM）。
- 启动：百毫秒到秒级。
- 运维：中（镜像、运行时、清理策略）。
- 适合：**默认 worker 档**，尤其是仓库不完全可信时。
- 风险：不是零（gVisor 自身有 CVE 历史），但远好于裸进程。

### C. microVM / 托管沙箱

**代表**：Firecracker 类 microVM、E2B、Modal、Isolade（2026-08，secretless microVM）、Tarit（2026-07，自托管沙箱云）、Aether（2026-07，cc/codex/opencode devbox）、Maritime（YC F26，2026-11-18，托管 agent 云）。

- 隔离强度：高（硬件虚拟化边界）。
- 硬件：**需要 `/dev/kvm`；本机没有**。
- 启动：百毫秒到数秒。
- 运维：高。
- 适合：多租户、不可信代码、需要快照/回滚。
- 现状：我们暂时上不了；集市云 pod（YatTerra）可作为「一个会话一个 pod」的近似形态，但 caps 只有 2C/2Gi，且不能 exec。

### 3.4 2026 年的逃逸教训（决定选型的依据）

| 事件 | 时间 | 教训 |
| --- | --- | --- |
| Beltdown：恶意仓库逃出 Claude Code 的 macOS 沙箱（经 git 调用，无提示） | 2026-09-11 | 进程级沙箱不能对抗恶意仓库；凭据不能进 guest |
| OpenAI Codex 沙箱两次逃逸 | 2026-09-15 | 同上 |
| Cloudflare sandbox 逃逸 | 2026-09-24 | 沙箱实现越复杂，边界越要少 |
| Docker Desktop hypervisor guest→host | 2026-09-19 | 虚拟化层也非绝对；叠层 + 最小权限 |

行业收敛出的三条规矩：**① 不可信代码至少进容器/VM；② 真凭据不进 guest，只给占位符；③ 出网走宿主侧、agent 改不了的代理。**

### 3.5 对比表

| 维度 | A 进程沙箱 | B gVisor 容器 | C microVM / 托管 |
| --- | --- | --- | --- |
| 对恶意仓库 | 不够 | 够 | 最够 |
| 硬件要求 | 无 | 无 | KVM |
| 冷启动 | ms | 100ms–s | 100ms–s |
| 运维成本 | 低 | 中 | 高 |
| 凭据隔离 | 靠约定 | 可以做到 | 天生更好 |
| 快照/回滚 | 无 | 镜像 + 卷 | 支持 |
| 我们可用性 | 已有 | **已有（runsc）** | 暂不可用 |
| 适 harness | cc/pi 都行 | cc/pi 都行 | cc/pi 都行 |

## 4. 最终建议架构

### 4.1 分层

**当前决定：只开 L1 一档到底。** 全部内部、仓库可信，进程沙箱解决的是「别把机器跑爆、别乱读文件、别乱出网」，不是「对抗恶意代码」。

```
当前   L1 进程沙箱   systemd-run + cgroup + nft    所有 worker
备查   L2 gVisor     docker run --runtime=runsc     接入外部/不可信仓库时再切（代码路径保留）
备查   L3 microVM    Firecracker 类                 有 KVM 的机器再做
```

§3.4 的逃逸事件针对的是「恶意仓库」场景；我们现阶段不把外部代码丢进 worker，因此接受 L1 的风险。**什么时候必须升级 L2**：开始接第三方仓库、公开 PR、或工单来源不可控时。

### 4.2 目标形态

```
                     ┌──────────── 控制面（A / skein-control :7800）───────┐
                     │ 准入 · 工单 · 派工 · 会话权威 · Outbox · 审计       │
                     └───────────────▲───────────────────┬───────────────┘
                                     │ WS :9102 结果/心跳 │ dispatch
                     ┌───────────────┴───────────────────▼───────────────┐
                     │ 节点（node-agent，A 上 systemd 服务）               │
                     │  worktree=/srv/agent/jobs/<ticket>                 │
                     │  ┌───────────────────────────────────────────┐    │
                     │  │ systemd-run 瞬态 service（user=agent）      │    │
                     │  │   cgroup：MemoryMax / CPUQuota / TasksMax  │    │
                     │  │   ProtectSystem=strict + ReadWritePaths    │    │
                     │  │   nft 端口白名单（GitHub/LLM/控制面/8080）  │    │
                     │  │   harness: claude 或 pi                     │    │
                     │  └───────────────────────────────────────────┘    │
                     └───────────────────────────────────────────────────┘
```

- 瞬态 service 的寿命 = 一次派单；结果与 transcript 先回传，再回收。
- 回收 systemd 瞬态 unit 不是「删 pod」；但任何长驻服务的停止/重启仍需显式审批。
- 没有「大 pod」这个中间层：节点就是 A 上的 node-agent；扩容 = 在另一台机器再起一个 node-agent。

### 4.3 凭据与出网（按 Beltdown 的规矩）

| 项 | 做法 |
| --- | --- |
| 仓库写权限 | 每仓库一把 deploy key，只允许 push `fix/*` 分支；用完即弃 |
| LLM | 网关按 worker 发短期 key，限额 |
| 生产密钥 | **不进容器**；需要时用占位符，由宿主侧代理代换 |
| 出网 | 只走宿主代理，白名单：GitHub、LLM 网关、控制面 |
| DNS | 固定解析，禁容器改 `/etc/resolv.conf` |
| 日志 | 不记录 token/secret/密码 |

### 4.4 harness 可替换

- `WORKER_BIN=claude|pi`；pi 走 print/JSON 模式，输出规范化到现有 `{ok, summary, sessionId}` 契约。
- 沙箱与调度不感知 harness；换模型/换工具不动控制面。

### 4.5 代码与部署的边界

要「大 pod 改某个服务」，该服务先得是三件套：

```
仓库（源码） → 构建脚本（产出可替换物） → 部署命令（替换 + 重启 + 自检）
```

unisso 目前只有二进制在跑 → **前置任务**：拿到源码/仓库，补 build + deploy 脚本，纳入验收。

## 5. 路线图

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P0（本周） | A 上准备 L1：建 `agent` 用户、`/srv/agent/jobs`、nft 白名单；新 node-agent 用 `PEER_PORT=9102` + `SANDBOX_ENABLED=1` 接入 | 派一张测试单：worker 以 `agent` 运行、超内存被 cgroup 杀、出网只通白名单、结果与 transcript 回来 |
| P1 | `WORKER_BIN=pi` 适配；worker 生命周期与残留清理；节点容量守卫 | pi 跑通一张测试单；中断后无残留进程与 worktree |
| P2 | unisso 三件套（源码 → 构建 → 部署）；按需加第二个 node-agent | unisso 可被工单修改并通过部署脚本上线 |
| 备查 | L2 gVisor 切换；L3 microVM（有 KVM 时） | 接入外部/不可信仓库时启动 |

L1 阶段的验收是「跑不爆、读不到、出不去」，不是「防逃逸」——防逃逸是 L2 的目标。

## 6. 红线与非目标

- **不删 pod**；长驻服务的停止/重启必须显式审批。
- **不手改运行中代码**；紧急修改也必须回写仓库。
- **不自动合入**；人审是硬闸。
- 不做多租户；不追求「任意代码安全执行」的通用平台。
- 不把 microVM 当作近期目标（硬件不允许）。

## 7. 来源

- Anthropic，Claude Code Sandboxing（工程博客，2025-10-20）：https://www.anthropic.com/engineering/claude-code-sandboxing
- anthropic-experimental/sandbox-runtime（`srt`，bubblewrap/seatbelt + 代理白名单）：https://github.com/anthropic-experimental/sandbox-runtime
- Beltdown：Escaping the Claude Code sandbox（2026-09-11）：https://accomplish.ai/blog/beltdown-escaping-the-claude-code-sandbox/
- 同站：Escaping the OpenAI Codex sandbox, twice（2026-09-15）、Guest to host: escaping Docker's hypervisor（2026-09-19）、Escaping the Cloudflare sandbox（2026-09-24）
- earendil-works/pi（MIT，极简 harness）：https://github.com/earendil-works/pi
- Show HN: Pi pod（2026-10-02，自托管会话沙箱）：https://pipod.dev/ · 讨论 https://news.ycombinator.com/item?id=49937304
- Show HN: Pi-hosts（2026-04-29）：https://github.com/hunvreus/pi-hosts
- Show HN: Isolade（2026-08-04，secretless microVM）、Tarit（2026-07-08）、Aether（2026-07-11）、Launch HN: Maritime（2026-11-18）——均为 HN 同期条目
- 本机实测：`runsc` 已注册于 docker/containerd，k3s `gvisor` RuntimeClass；无 `/dev/kvm`；集市云面板 caps=2C/2Gi
