# Skein 交接文档

> 2026-11-19 · 给接手的人：先读这一份，再按需读 `docs/product.md`、`docs/p2.md`、`docs/deployment.md`。
>
> **历史文档（2026-11-21 更新）**：旧 P0/P2 Router、`host.py`、`router/`、Outbox 与相关预留接口已删除；
> 当前模块、接口和环境变量以 `docs/control-plane.md` 为准。本文第 7、8 节仅作历史记录。

## 1. 这是什么

**Skein（雁阵）：把真实用户反馈，变成可验收的修复。**

小团队维护者用的 Super-agent 控制台：论坛帖/评论/群消息进入事件流，程序层准入后生成工单，派给节点上的 Claude Code 会话执行，交付后由人验收。**不自动合入、不自动部署。**

产品定义见 `docs/product.md`。四个工作面：

```text
工作台  现在要我做什么（待验收 / 卡住 / 正在跑）
需求    工单列表、详情、派发、验收
运行    总览拓扑、节点、Worker 会话、事件、信号
平台    准入矩阵、提示词、运行记录、审计、日志
```

## 2. 代码结构

```text
services/
  control/      Python 控制面（FastAPI + SQLite 领域库 + Session 权威 + Outbox）
  node-agent/   Node 节点 daemon（反向拨入、worktree、Claude Code、transcript 同步）
  console/      Vue 3 + Vite + naive-ui 控制台
    src/        源码
    dist/       构建产物（gitignore；部署包会带上）
    legacy/     旧静态页，挂在 /console-legacy
  console/web/  旧雁阵 Vue 前端，仅作视觉参考，不接新接口
legacy/
  dispatcher/   旧 Node 雁阵调度器，生产仍在 /root/yanzhen 跑
deploy/
  scripts/      migrate_yanzhen.py、console-smoke.mjs
  p0/           P0 只读演练：env、systemd、打包脚本
docs/
  product.md    产品定位
  architecture.md / p2.md / deployment.md / p0-deploy.md
```

## 3. 当前状态

| 项 | 状态 |
| --- | --- |
| 控制面 | 173 后端测试通过，ruff 全绿 |
| 控制台 | vue-tsc 通过，生产构建通过 |
| 真实数据 | 9 张工单、14 条派工状态、3 个会话、321 事件已迁入 staging |
| P0 只读演练 | 服务器 `/root/skein-p0`，端口 7801，systemd `skein-p0` |
| 旧生产 | `yanzhen.ssemarket.cn` 仍是旧 Node 雁阵，**未切换** |
| P2 | 常驻 Router、订阅直通、Inline/Spill、取消、Watchdog、多信道入口已实现，默认关闭 |

## 4. 本地运行

```bash
# 后端
cd services/control
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m skein            # 127.0.0.1:7800

# 前端
cd services/console
npm install
npm run dev                              # Vite，代理 /internal 到 7800
npm run build                            # 输出 dist
```

控制面令牌用 `SKEIN_TOKEN`；浏览器右上角令牌框写入 `localStorage` 的 `skein.token`。

本地预览库当前是迁移后的真实数据：`services/control/data/preview.db`；演示库备份在 `preview-demo.db`。

## 5. 服务器

SSH：

```bash
ssh SSE_Market     # root@175.178.90.215
```

P0 只读演练：

```bash
systemctl status skein-p0
curl -s http://127.0.0.1:7801/health/live
ls -l /srv/skein-p0/skein.db
cat /etc/skein-p0/env     # SKEIN_FORUM_BASE= 为空，写入口 503
```

浏览器通过隧道访问：

```bash
ssh -L 7801:127.0.0.1:7801 root@175.178.90.215
# 本机打开 http://127.0.0.1:7801/console/
```

更新 P0 代码：

```bash
# 本机
bash deploy/p0/package.sh
scp dist/skein-p0-*.tar.gz root@175.178.90.215:/root/skein-p0.tar.gz
# 服务器
tar xzf /root/skein-p0.tar.gz -C /root/skein-p0
systemctl restart skein-p0
```

**P0 边界：不停旧雁阵、不改 node-agent、不打开论坛写入口、不跑 poll。**

旧生产仓库在 `/root/yanzhen`；新 main 代码在 `/root/skein`。下线旧系统前不要删 `/root/yanzhen`。

## 6. 数据迁移

```bash
python3 deploy/scripts/migrate_yanzhen.py \
  --old /root/yanzhen/data/yanzhen.db \
  --new /srv/skein-p0/skein.db
```

预期：`{"events":321,"tickets":9,"assignments":14,"dispatches":14,"sessions":3,"nodes":1}`。

- 可重复执行：第二次全 0，不覆盖节点在线状态。
- `dispatches` 是旧 action 日志压缩后的每 `(ticket, worker)` 终态；388 条 progress 历史未迁。
- 旧 transcript 字节不在 SQLite，Workers 页显示 0 轮是正常。
- 迁移会改标题编码为 UTF-8；终端乱码只是 Windows 控制台。

## 7. 关键开关

```ini
SKEIN_TOKEN=...              # 控制面令牌
SKEIN_PORT=7801              # P0 端口
SKEIN_FORUM_BASE=            # 为空：论坛写入 503（P0 必须）
SKEIN_FORUM_TOKEN=
SKEIN_SHADOW=0
SKEIN_DISPATCH=0
SKEIN_DRIVER=0

# P2（默认全关）
SKEIN_ROUTER_SESSION=0
SKEIN_ROUTER_BATCH=8
SKEIN_ROUTER_INTERVAL=0.5
SKEIN_CONTROL_URL=           # 订阅直通需要；P0 为空
SKEIN_WATCHDOG=0
SKEIN_WATCHDOG_AUTO=0
SKEIN_WATCHDOG_INTERVAL=300
```

## 8. P2 已实现与边界

见 `docs/p2.md`。摘要：

- 常驻 Router + 批处理：`router/session.py`、`router/runtime.py`、`host.dispatch_batch`。
- 订阅直通：`POST /internal/events/followup`，采集进程回退 Router。
- Inline/Spill：`router/context.py`，事件摘要 200 字，正文留库。
- 取消：`POST /internal/tickets/{id}/cancel`，工单回暂存；cc 进程不杀。
- Watchdog：`watchdog_agent.py`，默认只观察；`AUTO=1` 只进金丝雀，不自动升格。
- 多信道入口：`POST /internal/trigger`。

已知边界：

- 订阅直通 nack 会丢事件，没有 ack 重试。
- 异步 ReAct 的 `start/stop` 工具协议还没做。
- 链式召回、按信道定制的出站 writer 还没做。
- Watchdog 没有“目标偏航”模型判断，只处理确定性信号。
- 常驻 Router 单会话，没有分片/多层过滤。

## 9. 下一步建议

1. **切生产前**：在 P0 端口跑影子模式一周，核对 `shadow_decisions` 与人工判断。
2. **产品化 2**：统一剩余空态、节点/Worker 深链已做，补 Ticket 列表空态和移动端不追求。
3. **P2 边界**：先做订阅直通 ack/重试，再做异步 `start/stop`，最后链式召回和信道出站。
4. **不要**在验证前打开 `SKEIN_DISPATCH=1` 或论坛写入口。

## 10. 最近检查点

本地分支：

```text
p0-rehearsal   P0 迁移修复 + 只读部署单元
p2-core        P2 核心
product-1      产品定位、四工作面、工作台、About、工单深链
```

这些是本地检查点，最终以 `main` 最新提交为准；远端 `origin/main` 在推送后更新。

## 11. 排障顺序

1. `/health/live` 是否 200。
2. `/internal/summary` 带令牌是否正常。
3. `/internal/tickets` 是否 9 条。
4. 控制台 `/console/` 是否为 Vite 产物（有 `id="app"` 和 `/console/assets/`）；否则是 legacy。
5. 日志页或 `journalctl -u skein-p0 -n 100`。
6. 写入口是否 503（P0 应为 503）。
7. 磁盘水位：根盘长期 94%，数据目录放 `/srv`，不要放根盘。
