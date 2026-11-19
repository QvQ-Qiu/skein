# 控制台验收

> 2026-11-19 · 冒烟脚本加浏览器步骤。判断标准是 [console.md](console.md) 第六节。

控制面默认 `http://127.0.0.1:7800`。`SKEIN_TOKEN` 与进程环境里的值相同，请求头是 `X-Skein-Token`。令牌为空时 `/internal/*` 全部 401。

## 启动

本地（PowerShell，仓库内）：

```powershell
cd services/control
$env:PYTHONPATH = "src"
$env:SKEIN_DB = "data/preview.db"
$env:SKEIN_BLOB_ROOT = "data/blobs"
$env:SKEIN_TOKEN = "dev-token"
python -m skein
```

bash 用 `services/control/dev.sh`（未导出令牌时默认为 `dev`，库为 `data/preview.db`）。`make run-control` 只执行 `python -m skein`，令牌和库路径仍要自己导出。

服务器按 [deployment.md](deployment.md)：令牌写在 `/etc/skein/env`，然后 `systemctl enable --now skein-control`。先确认：

```bash
curl -s http://127.0.0.1:7800/health/live
```

应返回 `{"status":"ok"}`。

## 冒烟

需要 Node.js 22+。`SKEIN_BASE` 可省略，默认 `http://127.0.0.1:7800`。令牌与控制面进程相同。

本地 PowerShell：

```powershell
$env:SKEIN_TOKEN = "dev-token"
node deploy/scripts/console-smoke.mjs
```

本地 bash：`export SKEIN_TOKEN=dev-token` 后执行同一条 `node` 命令。

服务器（仓库在 `/root/skein`）：

```bash
cd /root/skein
set -a
. /etc/skein/env
set +a
node deploy/scripts/console-smoke.mjs
```

每条请求超时 5 秒。有 `FAIL` 时退出码为 1。`SKIP` 不导致失败。末行形如 `3 passed, 1 failed, 1 skipped`。

脚本依次检查：`GET /health/live`（200 且 `status=ok`）；`GET /console/`（200、`content-type` 含 `text/html`、HTML 含 `id="app"` 与 `/console/assets/`）；HTML 里每个 `/console/assets/*.js|css` 都是 200；不带令牌的 `GET /internal/tickets` 为 401；带令牌的 `GET /internal/tickets?limit=5` 里 `tickets` 为数组且每条有 `id/code/status`；带令牌的 `GET /internal/signals` 里 `signals` 为数组且每条有 `kind/id`。列表非空时再请求第一条的 `GET /internal/tickets/{id}`：200 通过，404 打印 `SKIP  工单详情（接口未上线）`。404 一律记 SKIP，不区分「路由未挂」和「没有这张单」。列表为空则不请求详情。

## 浏览器

打开 `/console/`。右上角令牌框填入与 `SKEIN_TOKEN` 相同的值并回车，写入 `localStorage` 键 `skein.token`，状态变为「已连接」。下面按当前静态页的导航操作。

1. **3 秒内出工单列表。** 点「工单」。3 秒内出现表头（编号、标题、状态、操作）。Network 中 `GET /internal/tickets?limit=100` 为 200。空库时表体可以是空的，右上角不能是「失败」。
2. **通过后变为 done。** 准备一张 `status=review` 的测试单，点「通过」。该行马上变成 `done`，通过/驳回按钮消失。只有 `review` 显示这两个按钮，其他状态显示「派单」。
3. **驳回后变为 rejected。** 另取一张 `review` 测试单，点「驳回」。该行马上变成 `rejected`。这两步会改状态，用测试单。
4. **信号页四类，并指回对象。** 点「信号」。四类都要能对上具体对象（缺的先造数据再刷新）：派单超时（工单 id、节点 id）、作业失败（作业名、日期）、写回失败（类型与对象）、节点离线但有在途工单（节点 id、工单 id）。只有「暂无信号」时，这一条不算通过。
5. **断开 7800 后有明确错误，页面不白屏。** 页面已经打开后停进程（Ctrl+C 或 `systemctl stop skein-control`），再点另一个导航。页头和导航仍在，右上角为红色「失败：…」。停进程后不要刷新，刷新得到的是浏览器连接错误页。拉起进程后再点一次，状态回到「已连接」。
6. **1280×720 与 1920×1080 无横向滚动。** 两个尺寸下各看概览、工单、节点、运行、信号、提示词。控制台执行 `document.documentElement.scrollWidth <= document.documentElement.clientWidth`。纵向滚动可以有。

## 当前限制

令牌在 `localStorage` 键 `skein.token`。OAuth 会话还没换上（[console.md](console.md) 第五节第 6 步）。同机脚本能读到完整控制面令牌。

下列接口未上线，对应页面是占位，不能用来勾第六节：

| 页面 | 缺的接口 |
| --- | --- |
| 工单详情三栏 | `GET /internal/tickets/{id}`（列表、通过、驳回、派单仍走已有接口；详情 404 时冒烟记 SKIP） |
| Worker | `GET /internal/sessions`、`GET /internal/sessions/{id}/turns` |
| 事件 | `GET /internal/events`、`GET /internal/threads/{id}` |
| 总览拓扑 | `GET /internal/topology` |
| 平台准入 | `GET/PUT /internal/admission` |
| 成本 | `/internal/summary` 还没有按 run 的费用 |

冒烟按迁移后的构建产物来写：`/console/` 含 `id="app"`，脚本和样式在 `/console/assets/`。现在挂的是无构建静态页（`services/console/index.html`，相对路径 `app.js` 与 `style.css`）。切到 `dist` 之前，「控制台首页」和「静态资源」会失败；健康检查和接口行仍说明服务是否可用。第六节的点击在这张静态页上做：概览、工单、节点、运行、信号、提示词已经接上现有接口。
