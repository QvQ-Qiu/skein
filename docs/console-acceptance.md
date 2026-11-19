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

脚本依次检查：`GET /health/live`（200 且 `status=ok`）；`GET /console/`（200、`content-type` 含 `text/html`、HTML 含 `id="app"` 与 `/console/assets/`）；HTML 里每个 `/console/assets/*.js|css` 都是 200；不带令牌的 `GET /internal/tickets` 为 401；带令牌的 `GET /internal/tickets?limit=5` 里 `tickets` 为数组且每条有 `id/code/status`；带令牌的 `GET /internal/signals` 里 `signals` 为数组且每条有 `kind/id`；带令牌的 `GET /internal/stream` 返回 200、`content-type` 含 `text/event-stream`，并在 5 秒内收到首帧 `event: snapshot`（读完即取消，不会挂在流上）。列表非空时再请求第一条的 `GET /internal/tickets/{id}`：200 通过，404 打印 `SKIP  工单详情（接口未上线）`。404 一律记 SKIP，不区分「路由未挂」和「没有这张单」。列表为空则不请求详情。

## 浏览器

打开 `/console/`。首次进入是落地页，在「控制面令牌」里填入与 `SKEIN_TOKEN` 相同的值，回车后进入工作台；令牌写入 `localStorage` 键 `skein.token`。进去之后换令牌点右上角「令牌」。

1. **工作台首屏。** 进入后停在「工作台」。自上而下应当是：阵眼（待你验收 / 卡住的事 / 正在跑 / 已验收 四个数字 + 右侧极坐标阵图）、六段闭环流水线（反馈 / 准入 / 工单 / 派工 / 交付 / 验收）、三栏（待验收队列 / 卡住的事 / 阵中动态）。1280×720 下首屏能看到阵眼、流水线和待验收的第一行。
2. **阵眼可点。** 点「待你验收」落到需求列表；点「卡住的事」落到信号页；点「正在跑」落到运行总览；点流水线任一段落到对应页面。
3. **验收读完即变。** 待验收队列里点「通过」，该行消失、状态变 done；点「驳回」先弹确认，确认后变 rejected。这两步会改状态，用测试单。
4. **空态说下一步。** 空库时阵眼为 0、雷达只有空环、三个面板给空态文案，不出现假数据，也不报错。
5. **信号页四类，并指回对象。** 四类都要能对上具体对象：派单超时（工单 id、节点 id）、作业失败（作业名、日期）、写回失败（类型与对象）、节点离线但有在途工单（节点 id、工单 id）。只有「暂无信号」时，这一条不算通过。
6. **断开 7800 后有明确错误，页面不白屏。** 页面已经打开后停进程，再点另一个导航。页头和导航仍在，内容区出现错误条并可重试。停进程后不要刷新，刷新得到的是浏览器连接错误页。
7. **1280×720 与 1920×1080 无横向滚动。** 两个尺寸下各看工作台、需求、运行、节点、信号、平台。控制台执行 `document.documentElement.scrollWidth <= document.documentElement.clientWidth`。纵向滚动可以有。
8. **实时推送。** 顶栏与工作台标题旁显示「实时」。另开一个终端改一条数据（例如把一张 review 单通过），3 秒内工作台的数字和队列自己变化，不用刷新。停掉控制面几秒后状态变「轮询」、页面不白屏；拉起后自动回到「实时」并补上断线期间的变化。

## 当前限制

令牌在 `localStorage` 键 `skein.token`。OAuth 会话还没换上（[console.md](console.md) 第四节）。同机脚本能读到完整控制面令牌。

实时推送走 SSE；断流时工作台与总览退回 15/10 秒轮询，其余页面仍是轮询。总览力导网是自绘实现，换 AntV G6 是待办。
