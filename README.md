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

## 仓库结构

```
skein/    控制面（Python）：Router 宿主、准入、领域库、Outbox
worker/   节点 daemon：反向拨入、worktree、Claude Code、transcript 同步
web/      控制台（Vue3）
docs/     架构与设计文档
server/   [迁移中] 重构前的 Node 调度台
scripts/  运维脚本
```

## 开发

```bash
python -m pytest        # 25 个测试，覆盖准入矩阵、空工具直答契约、领域库约束
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
