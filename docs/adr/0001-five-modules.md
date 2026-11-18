# ADR 0001：采用 Super-agent 五模块架构

- 状态：已接受
- 日期：2026-11-18

## 背景

系统里有四个各自演化的部分：雁阵（调度台）、秋坞（运行时平台）、小秋（论坛 agent）、ops 采样与面板。它们各自解决过问题，但边界重叠，缺少统一的会话与事件模型。

## 决定

按 Super-agent 架构重组，五个模块：

1. 双层架构：常驻 Router + 隔离 Worker
2. 事件驱动：Trigger / Thread / 订阅直通
3. 面向人的交互：异步工具、多信道输出
4. 计算与状态分离：无状态 Loop + Session 权威
5. 外部 Watchdog：独立于被监视系统

三份权威分开：Session（transcript）、领域（工单/订阅/审计）、论坛（帖/评论/加精）。

## 后果

- 雁阵的单条分类器不再复用，改为常驻 Router 宿主。
- 秋坞 k3s 编排退场，warm 池作为「按需执行环境」保留。
- 小秋的图与工具保留为 Worker，但不再直接对人说话。
- 集市云只作为执行环境（YatTerra pod），不作为控制面。

## 依据

完整论证见 `docs/architecture.md`，以及 `docs/market-cloud.md`、`docs/market-cloud-leverage.md`。
