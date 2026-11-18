# 控制台计划

> 2026-11-19 · 现状是静态页，目标是 Vue 3 + naive-ui 的实体控制台

## 一、现状

控制面已经托管一个**无构建的静态页**：`http://127.0.0.1:7800/console/`。

| 视图 | 数据来源 | 能做什么 |
| --- | --- | --- |
| 概览 | `/internal/summary` | 事件、Thread、工单、待验收、会话、影子判定的计数 |
| 工单 | `/internal/tickets` | 列表；通过/驳回；派单 |
| 节点 | `/internal/nodes` | 在线状态、平台、容量、最后心跳 |
| 运行 | `/internal/runs` | 作业、日期、状态、说明 |
| 信号 | `/internal/signals` | 派单超时、作业失败、写回失败、节点离线 |
| 提示词 | `/internal/prompts` | 稳定/金丝雀指针；升格、回退、解冻 |

文件在 `services/console/`：`index.html`、`app.js`、`style.css`。改完刷新即生效。

这一版的目的只有一个：**切换期间人能在浏览器里验收工单和看告警**，不追求信息架构。ops 面板下线后，它的告警能力由「信号」页承接。

## 二、目标形态

以实体为中心，六页一级导航：

```
总览 Fleet │ 工单 Tickets │ Worker │ 节点 Nodes │ 事件 Events │ 平台 Platform
```

| 页 | 回答什么 | 核心组件 |
| --- | --- | --- |
| 总览 | 现在谁在干什么、卡在哪 | 全屏拓扑（G6 力导）+ 顶部指标条 + 实体抽屉 |
| 工单 | 排队与验收 | 高密度表格 + 详情三栏（正文/各路回报/验收动作） |
| Worker | 这个 worker 卡在哪一步 | transcript 时间线、工具调用折叠、分支与 diffstat |
| 节点 | 哪台机器在跑、健不健康 | 卡片网格、心跳延迟、并发会话数 |
| 事件 | 一条 Trigger 怎么变成工单 | 实时流 + 准入判定 + 按 Thread 聚合 |
| 平台 | 为什么这么判、怎么改 | 准入矩阵编辑、提示词修订、审计、成本 |

完整 IA 与视觉规则见 `docs/plan.md` 的对应章节与评审包里的面板重设计稿（服务器 `/root/superagent-review/`）。

## 三、技术选型

| 项 | 选择 | 理由 |
| --- | --- | --- |
| 框架 | Vue 3 + Vite | 两套现有面板都是 Vue 3，复用面最大 |
| 组件库 | naive-ui | ops-console 已在用，避免两套组件 |
| 图 | AntV G6 | 既定选型；dagre 布局、combo 分组、minimap |
| 状态 | Pinia | — |
| 实时 | SSE 或轮询 | 先轮询 `/internal/summary`，后续加 SSE |
| 鉴权 | `X-Skein-Token`，存 localStorage | 与 API 一致；后续换成 OAuth 会话 |

**不引 3D、粒子、视差；装饰性循环动画一律不做。** 动效只在状态变化处出现（派单、心跳、租约到期）。

## 四、接口差距

现在够用，但目标形态还需要补：

| 需要 | 现状 | 要加的接口 |
| --- | --- | --- |
| 工单详情 | 只有列表 | `GET /internal/tickets/{id}`（含 assignments、dispatch、result） |
| Worker 列表与 transcript | 无 | `GET /internal/sessions`、`GET /internal/sessions/{id}/turns` |
| 事件流 | 无 | `GET /internal/events?since=`、`GET /internal/threads/{id}` |
| 拓扑数据 | 节点与工单分散 | `GET /internal/topology`（节点/工单/派工/订阅的边） |
| 准入矩阵编辑 | 无 | `GET/PUT /internal/admission` |
| 成本 | 无 | 网关侧按 run 汇总，或在 `summary` 里加 |

## 五、迁移步骤

1. **保持静态页可用**（现在），切换与验收期间它就是唯一的验收入口。
2. **补接口**：上表里的前四项，先服务静态页，再服务 Vue。
3. **搭 Vue 骨架**：`services/console` 加 `package.json`、`vite.config.ts`、`src/`；先做「工单」与「信号」两页，覆盖最高频的两个动作（验收、看告警）。
4. **接管路由**：Vue 构建产物输出到 `services/console/dist`，控制面把 `/console` 的挂载目录切到 `dist`；静态页保留为 `/console-legacy`。
5. **补齐其余四页**：总览拓扑、Worker、节点、事件、平台。
6. **鉴权升级**：从 localStorage 令牌换成 OAuth 会话（与论坛身份一致）。

## 六、验收标准

- 打开 `/console/` 3 秒内出工单列表；
- 一张 review 状态的单能在页面上完成通过/驳回，状态立刻变为 done/rejected；
- 信号页能展示四类信号并指回具体对象；
- 断开 7800 后页面给出明确错误，不白屏；
- 页面在 1280×720 与 1920×1080 下无横向滚动。

## 七、不做

- 不做移动端适配（桌面优先）。
- 不做主题切换（深色定版）。
- 不做自定义仪表盘拖拽。
