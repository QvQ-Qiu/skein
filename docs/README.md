# 文档索引

按「先看什么」排序。

## 想快速了解这套系统

1. [product.md](product.md) —— 一句话、核心闭环、四个工作面
2. [platform-vision.md](platform-vision.md) —— 前厅/后厂：小秋 × 雁阵的闭环愿景与权责
3. [architecture.md](architecture.md) —— 五模块与三份权威，为什么这么分层
4. [control-plane.md](control-plane.md) —— 当前模块、接口、环境变量、备份

## 要接小秋

- [chat-events.md](chat-events.md) —— 聊天事件 → Skein 事件/工单的首版契约
- [llm-trace.md](llm-trace.md) —— 会话与模型调用留痕（Skein 内，不接 Langfuse）

## 要部署

- [deployment.md](deployment.md) —— 安装、env、systemd、控制台构建、备份、自检
- [migration-qiudock.md](migration-qiudock.md) —— 秋坞迁入与彻底删除记录

## 要接身份与权限

- [permission-model.md](permission-model.md) —— 两级身份（集市用户 / 管理员）、UniSSO 判断

## 要接集市云

- [market-cloud.md](market-cloud.md) —— 集市云是什么、能用什么、做不到什么
- [market-cloud-leverage.md](market-cloud-leverage.md) —— 哪些活能送出去、Cloud Worker 契约

## 要跑不可信代码

- [worker-runtime.md](worker-runtime.md) —— 当前能力、业界 A/B/C、L1/L2/L3
- [sandbox.md](sandbox.md) —— L1 systemd 沙箱的配额、做不到的事、止损三步

## 要做前端

- [console.md](console.md) —— 控制台页面、构建、接口

## 决策记录

- [adr/0001-five-modules.md](adr/0001-five-modules.md) —— 为什么采用五模块

## 历史材料

- [design.md](design.md) —— 旧雁阵的设计文档
- [plan.md](plan.md) —— 2026-11 的迁移计划（含已删除的影子/采集）
- [migration.md](migration.md) —— 旧雁阵十步迁移
- [p2.md](p2.md) —— 常驻 Router 批处理的早期计划（未上线，代码已删）
- 完整的调研与评审在服务器 `/root/superagent-review/`。
