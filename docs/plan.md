# 总体规划与进度

> 2026-11-19 · 目标是让 Skein 取代旧雁阵，随后下线秋坞与 ops 面板

## 一、目标

| 阶段 | 内容 | 完成标志 |
| --- | --- | --- |
| M1 | 控制面可用 | 采集、准入、派单、执行、验收闭环，测试与 CI 全绿 |
| M2 | 接管生产 | 新控制面接 node-agent，日报/精华帖写入走 Outbox |
| M3 | 下线旧系统 | 停旧雁阵、停 qdockd/k3s 旧负载、停 ops 面板 |
| M4 | 收尾 | 沙箱默认开启、面板按新 IA 重写、文档归档 |

## 二、已完成

### 控制面（services/control）

| 能力 | 文件 | 提交 |
| --- | --- | --- |
| 领域库 schema v6（14 张表） | `db.py` | `2f5c43f` 起 |
| 确定性准入矩阵 | `admission.py` | `2f5c43f` |
| 空工具直答（冻结/深检） | `router/direct.py` | `2f5c43f`、`7f891a5` |
| Router 宿主（聊天/事件） | `host.py` | `80f998c` |
| Session 权威（内容寻址、按轮幂等） | `session_store.py` | `4f08e96` |
| Outbox（单次写、对账） | `outbox.py` | `ca11eaa` |
| HTTP 接口与进程入口 | `api.py`、`__main__.py` | `4d71fac` |
| 论坛采集（公开视图、水位、Thread 归并） | `ingest.py`、`forum_source.py` | `58919a4` |
| 影子派单 | `shadow.py` | `412292e` |
| 真派单开关 | `host.py`、`poll.py` | 同上 |
| 节点注册与调度 | `scheduler.py` | 后续提交 |
| WebSocket 节点通道 | `channel.py` | 后续提交 |
| 主站写入口兼容 | `compat.py` | 后续提交 |
| 定时作业驱动 | `driver.py`、`runs.py` | 后续提交 |
| 提示词金丝雀与冻结 | `prompts.py` | 后续提交 |
| Watchdog 信号 | `watchdog.py` | 后续提交 |
| 控制台静态页 | `services/console/` | 后续提交 |
| 迁移脚本 | `deploy/scripts/migrate_yanzhen.py` | 待提交 |
| systemd 沙箱封装 | `services/node-agent/sandbox.mjs` | 已提交 |

### 节点侧（services/node-agent）

- transcript 按轮同步、失败入队、409 视为已同步
- 沙箱参数构造（systemd service + cgroup + 挂载保护），默认关闭

### 文档

`architecture.md`（五模块与三份权威）、`control-plane.md`（接口与环境变量）、`migration.md`（十步迁移）、`deployment.md`（部署与切换）、`sandbox.md`（沙箱）、`market-cloud.md` 与 `market-cloud-leverage.md`（集市云）、`adr/0001`。

## 三、剩余工作

### 必须做（M2）

1. **部署到 A**：按 `deployment.md` 建 venv、env、systemd。
2. **数据迁移**：跑 `migrate_yanzhen.py`，先 dry-run 核对计数。
3. **节点接入**：node-agent 指向新通道并重启。
4. **旁路观察**：影子模式跑一段时间，用 `review.py` 对比人工判断。
5. **切真派单**：影子一致率可接受后开 `SKEIN_DISPATCH=1`。

### 切换后（M3）

6. 停旧雁阵；观察一周再删 `/root/yanzhen`（保留 `prod-legacy` 分支）。
7. 停 qdockd 与 k3s 上的旧负载（秋坞下线）。
8. 停 ops 面板；信号页承接其告警能力。

### 优化（M4）

9. Watchdog 自动提议：依据信号让模型改写 Worker 提示词（当前只有人工触发）。
10. 沙箱默认开启：完成宿主账号、`/srv/agent/jobs`、nft 白名单后设 `SANDBOX_ENABLED=1`。
11. 面板迁 Vue 3 + naive-ui（`services/console` 目录已留好）。
12. 集市云 Cloud Worker：YatTerra token 与出网确认后接入。

## 四、验收标准

- 采集：论坛新帖/评论 5 分钟内出现在 `events`，Thread 归并正确；
- 派单：一张单从 `staged` 走到 `review`，节点与工单在面板上可见；
- 写入：日报与加精经 Outbox 写出，重复触发不产生第二篇；
- 告警：派单超时、作业失败、写回失败、节点离线都能在信号页看到；
- 回滚：停新服务、起旧服务、改 node URL，10 分钟内恢复。

## 五、风险与依赖

| 风险 | 应对 |
| --- | --- |
| 宿主 Python 3.10 与依赖兼容 | 已按 ≥3.10 编写；venv 隔离 |
| 端口 7700 被 qdockd 占用 | 控制面改用 7800 |
| 根盘水位高 | 数据目录放 `/srv/skein`，不放根盘 |
| 节点跑飞影响生产 | 沙箱封装已就绪，切换前完成宿主准备并开启 |
| 影子判断与人工不一致 | 用 `review.py` 量化，不一致则先调整提示词再开真派单 |
| 集市云 API 未授权 | Cloud Worker 留到最后，不阻塞 M2/M3 |
