# P0 只读演练部署

> 目标：在服务器上用**独立目录、独立端口、独立库**把新控制面和真实 9 张单跑起来。
> **不停旧雁阵、不改 node-agent、不打开论坛写入口、不启动采集/派单。**

## 1. 准备代码包

本机仓库根目录：

```bash
bash deploy/p0/package.sh
```

产物在 `dist/skein-p0-<时间>.tar.gz`，包含 `services/control` 源码、`services/console/dist`、`deploy/p0`、`docs`，不包含 `.git`、`.venv`、`node_modules`、`data`。

上传到服务器：

```bash
scp dist/skein-p0-*.tar.gz root@<host>:/root/
ssh root@<host>
mkdir -p /root/skein-p0 && tar xzf /root/skein-p0-*.tar.gz -C /root/skein-p0 --strip-components=1
```

## 2. 建库并迁移

```bash
mkdir -p /srv/skein-p0 /etc/skein-p0
cd /root/skein-p0
python3 deploy/scripts/migrate_yanzhen.py \
  --old /root/yanzhen/data/yanzhen.db \
  --new /srv/skein-p0/skein.db
```

预期输出：

```json
{"events":321,"tickets":9,"assignments":14,"dispatches":14,"sessions":3,"nodes":1}
```

重复执行应输出全 0，且不会把节点状态打回 offline。

## 3. 环境文件

```bash
cp deploy/p0/env.example /etc/skein-p0/env
chmod 600 /etc/skein-p0/env
TOKEN=$(openssl rand -hex 32)
sed -i "s/replace-with-openssl-rand-hex-32/$TOKEN/" /etc/skein-p0/env
```

**P0 必须保持：**

```ini
SKEIN_FORUM_BASE=
SKEIN_FORUM_TOKEN=
SKEIN_SHADOW=0
SKEIN_DISPATCH=0
SKEIN_DRIVER=0
```

`SKEIN_FORUM_BASE` 为空时，`/internal/outbox/emit`、`/api/auth/post`、`/internal/featured` 都会返回 503，不会碰到论坛。

## 4. 安装与启动

```bash
cd /root/skein-p0/services/control
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
cp /root/skein-p0/deploy/p0/skein-p0.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now skein-p0
curl -s http://127.0.0.1:7801/health/live
```

预期 `{"status":"ok"}`。

## 5. 验收

```bash
TOKEN=$(grep '^SKEIN_TOKEN=' /etc/skein-p0/env | cut -d= -f2)
curl -s -H "X-Skein-Token: $TOKEN" http://127.0.0.1:7801/internal/tickets?limit=100 | jq '.tickets | length'
# 9

curl -s -H "X-Skein-Token: $TOKEN" http://127.0.0.1:7801/internal/sessions | jq '.sessions | length'
# 3

curl -s -H "X-Skein-Token: $TOKEN" http://127.0.0.1:7801/internal/logs | jq '.logs'
# 至少有一条 control plane started
```

浏览器通过 SSH 隧道访问，不挂公网：

```bash
ssh -L 7801:127.0.0.1:7801 root@<host>
# 本机打开 http://127.0.0.1:7801/console/
```

`/console/` 必须是 Vite 产物（有 `id="app"` 和 `/console/assets/`）；如果是旧静态页，说明 `services/console/dist` 没进包。`/console-legacy` 只用于回看旧页。

## 6. 明确不做

- 不 `systemctl stop yanzhen`
- 不改 node-agent 的 `CONTROLLER_URL`
- 不配 `SKEIN_FORUM_BASE` / `SKEIN_FORUM_TOKEN`
- 不跑 `python -m skein.poll`
- 不点真实工单的「通过/驳回」，除非是对 `/srv/skein-p0/skein.db` 这个副本

## 7. 回滚

```bash
systemctl disable --now skein-p0
rm -rf /root/skein-p0 /srv/skein-p0 /etc/skein-p0
```

旧雁阵全程未动。

## 8. 影子模式（P0 之后单独一步）

影子需要模型网关和论坛只读库。准备好以下条件后再开：

- `/etc/skein-p0/env` 填 `SKEIN_LLM_BASE`、`SKEIN_LLM_KEY`
- 填 `SKEIN_FORUM_DB_*`（只读账号）
- `SKEIN_SHADOW=1`，`SKEIN_DISPATCH=0`
- 启动独立的 `skein-p0-poll.service`（见 `deploy/p0/skein-p0-poll.service`）

启动 poll 前必须把 `wm:forum` 设成已迁事件的 `MAX(created_at)`，否则首轮只回看 24 小时。不要把旧库的 `wm_market_post` 数值直接抄进新库。
