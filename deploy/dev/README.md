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

## 4. 感知层

节点/信号数据直接来自控制面 `/internal/nodes`、`/internal/signals`。
小秋 ops 面已下线，旧 `/ops/*` 页面不再存在。

## 说明

- 生产部署命令见 `docs/deployment.md`；迁移进度见 `docs/migration-qiudock.md`。
- 旧的本地启动脚本（含面板密钥）属于机器私有，不入库。
