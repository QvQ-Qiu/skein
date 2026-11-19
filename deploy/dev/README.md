# 本地联调（无外部依赖）

换机器或新同学上手时，用假 UniSSO 把控制台完整跑起来，不碰生产凭据。

## 1. 假 UniSSO

```bash
python deploy/dev/mock-unisso.py          # 监听 127.0.0.1:13099
```

账号：`e2e@ssemarket.cn / right-pass`（internal），`guest@ssemarket.cn / right-pass`（非 internal）。
实现了 `password/verify`、`session/verify`、`session/revoke` 三条契约。

## 2. 控制面（从仓库根目录）

```bash
cd services/control
PYTHONPATH=src \
SKEIN_DB=data/ui-preview.db SKEIN_BLOB_ROOT=data/ui-blobs SKEIN_TOKEN=dev-token \
SKEIN_SSO_BASE=http://127.0.0.1:13099 \
SKEIN_SSO_CLIENT_ID=dev-client SKEIN_SSO_CLIENT_SECRET=dev-secret \
SKEIN_COOKIE_DOMAIN=host \
python -m skein
```

`SKEIN_COOKIE_DOMAIN=host` 是本地 http 调试用的 host-only Cookie；生产不设（按 unisso 响应种 `.ssemarket.cn`）。

## 3. 控制台

```bash
cd services/console
npm ci && npm run build
cd ../..
node deploy/dev/preview-proxy.mjs services/console/dist http://127.0.0.1:7800 5173
```

打开 `http://127.0.0.1:5173/console/`，用上面的 E2E 账号登录。

## 4. 感知层（主机/Pod 页）

这两页的数据来自小秋 ops 的 `/ops/*`，本地需要控制面能到 ops 上游：

- 本机直接跑小秋 ops 时：`SKEIN_QA_OPS_UPSTREAM=http://127.0.0.1:8118`；
- 只调控制面时：可不设，页面会 503，属预期。

控制面为 ops 上游代签 `ops_session` 需要 `SKEIN_OPS_PANEL_SECRET`（与 ops 的
`OPS_PANEL_SECRET` 相同）。**该密钥不进仓库，也不进浏览器。**

## 说明

- 生产部署命令见 `docs/deployment.md`；迁移进度见 `docs/migration-qiudock.md`。
- 旧的本地启动脚本（含面板密钥）属于机器私有，不入库。
