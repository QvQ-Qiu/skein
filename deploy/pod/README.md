# 集市云 pod 执行节点

> 2026-11-19 · pod `skein`（集市云 / YatTerra）

## 结论

- 隔离边界是 **pod 本身**：容器里没有 systemd，也没有 mount namespace 权限，
  `systemd-run` 与 `bubblewrap` 都起不来（`unshare -m` 直接 EPERM）。
  worker 以 `cloud` 用户直接跑，每单独立 HOME（`~/repos/home-<workerId>`）。
- 控制面在集市机 A：`https://skein.ssemarket.cn`（nginx → `172.17.0.1:7800`）。
- 出网：GitHub、npmmirror、`skein.ssemarket.cn`、LLM 网关可达。

## 一次性准备

```bash
ssh -p 22068 cloud@ssemarket.cn        # 面板给的凭证
bash setup.sh                           # 装 node22 / claude / bubblewrap / 拉仓库
```

1. `setup.sh` 会打印一把公钥；在 GitHub 把 Add deploy key（**读写**，只给 `QvQ-Qiu/skein`）。
2. 从控制面节点拷 `~/.claude/settings.json`（只含 `env`：`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / 模型映射）。
3. 写 `~/node-agent.env`（600）：

```ini
CONTROLLER_URL=wss://skein.ssemarket.cn/api/agent/channel
AGENT_TOKEN=<A 上 /etc/skein/env 的 SKEIN_TOKEN>
NODE_ID=n-cloud-skein
NODE_NAME=集市云-skein
PLATFORMS=forum
REPO_MAP={"forum":"/home/cloud/repos/skein"}
MAX_WORKERS=2                 # 并发上限（不是会话数；空闲会话不占进程）
CLAUDE_BIN=/home/cloud/.local/node/bin/claude
SKEIN_CONTROL_URL=https://skein.ssemarket.cn
SKEIN_TOKEN=<同一个 SKEIN_TOKEN>
IS_SANDBOX=1
WORKER_FULL_BYPASS=1
```

注意 `REPO_MAP` 的引号要保留（`set -a; . env` 会剥掉裸引号）：

```bash
sed -i "s|^REPO_MAP=.*|REPO_MAP='{\"forum\":\"/home/cloud/repos/skein\"}'|" ~/node-agent.env
```

4. 启动：

```bash
bash start-node-agent.sh
```

5. 控制面确认：

```bash
. /etc/skein/env
curl -s -H "X-Skein-Token: $SKEIN_TOKEN" http://127.0.0.1:7800/internal/nodes
```

应看到 `n-cloud-skein`、`status=online`、`max_workers=2`。

## pod 重启后

没有 systemd，重启后手动跑 `bash ~/start-node-agent.sh`。仓库与 node 都在 `~/`，
若 pod 被重建则重跑 `setup.sh`。

## 为什么不叠沙箱

pod 内 `unshare -m` / `bwrap` 均 EPERM（看 §结论）。要更强隔离只有两个方向：
让平台给 pod 配 `runtimeClassName=gvisor`，或者换一台能开 mount namespace 的机器。
当前按「内部可信、pod 即边界」处理（见 `docs/worker-runtime.md` §4.1）。

## 权限边界

worker（cc）不继承控制面令牌：`SKEIN_TOKEN`、`AGENT_TOKEN`、`CONTROLLER_URL`、
`SKEIN_CONTROL_URL` 在拉起时被剔除（`services/node-agent/worker-env.mjs`）。
worker 只看得到工单、仓库与自己 HOME 下的 LLM 配置；不能调 `/internal/*`。
