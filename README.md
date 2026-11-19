# Skein · 雁阵

Super-agent 的调度与执行平台。

中文名「雁阵」——雁群飞行时排成的队形，英文正是 **skein**；skein 同时指「一绞线」，对应本架构里的核心概念 **Thread**。

## 架构

目的四个：**连续、高效、主动、持久**。

手段五个：双层架构（Router / Worker）、事件驱动（Trigger / Thread / 订阅直通）、面向人的交互、计算与状态分离、外部 Watchdog。

三份权威各管一处，互不合并：

| 权威 | 内容 | 位置 |
| --- | --- | --- |
| Session | transcript、轮次、会话指针 | 索引行 + 内容寻址文件 |
| 领域 | 工单、Thread、订阅、提示词修订、审计 | SQLite |
| 论坛 | 帖、评论、加精 | 论坛 MySQL |

完整设计见 [docs/architecture.md](docs/architecture.md)。

## 交接

新接手先读 [HANDOVER.md](HANDOVER.md)；产品定位见 [docs/product.md](docs/product.md)。

## 仓库结构

```
services/
  control/      控制面（Python）：Router 宿主、准入、领域库、Outbox
  node-agent/   节点 daemon（Node）：反向拨入、worktree、Claude Code、transcript 同步
  console/      控制台（Vue3 + naive-ui）
deploy/
  scripts/      发布与验收脚本
legacy/
  dispatcher/   [迁移中] 重构前的 Node 调度台
docs/
  README.md                  文档索引
  architecture.md            五模块与三份权威
  plan.md                    目标、进度与剩余工作
  control-plane.md           控制面现状、接口与环境变量
  console.md                 控制台现状、目标 IA 与迁移步骤
  deployment.md              部署、切换与回滚
  migration.md               十步迁移与服务器工作树
  sandbox.md                 沙箱方案与止损
  market-cloud.md            集市云执行环境契约
  market-cloud-leverage.md   集市云能怎么发挥
  adr/                       架构决策记录
```

## 开发

```bash
make test        # 控制面测试
make lint        # ruff
make build-console
```

## 状态

**重构进行中。** 当前代码是重构前的雁阵调度台 v0.4.0，作为迁移起点保留：

- `worker/node-agent.mjs` 的反向拨入、心跳、worktree 隔离将直接复用
- `server/db.js` 的工单与派工表将扩展为完整领域库
- `server/router.js` 的单条分类器**不再复用**——它不是常驻 Router
- `web/` 将作为新控制台的骨架

## 三条约束

1. 数据不可重建：帖、评论、加精、会话、工单。
2. 每一步可回滚：没有回滚点的切换不做。
3. 没有消费者的组件不建。
