# xiaoqiu 指标源替换（qdockd → 雁阵控制面）

> 2026-11-19 · ops 面迁出秋坞的一部分。镜像以现有 `xiaoqiu:1.0.96-sso` 为基底打补丁，
> 只替换指标采集与告警规则，其它代码保持原样，避免整仓重建带来的漂移。

## 做了什么

| 文件 | 改动 |
| --- | --- |
| `app/ops/skein_metrics.py` | 新模块：`/internal/summary` + `/internal/signals` + `/internal/topology` → `qdock_metric_samples`（名字前缀 `skein_`） |
| `app/main.py` | 四处 `qdock as qdock_metrics` 导入改为 `skein_metrics`；调度门改为 `qdock.enabled or skein.enabled` |
| `app/config.py` | 新增 `SkeinCfg`（base_url / token_env / enabled / scrape_seconds / prune_days） |
| `app/ops/alerts.py` | METRICS 增加 `skein_*`；SEED 增加「待验收积压 / 卡住信号 / 节点离线 / 采集断供」四条规则 |
| `config/settings.yaml` | 新增 `skein:` 段；`qdock.enabled` 置 false（旧镜像回滚锚） |

序列字段映射（`/ops/platform/series` 原结构不变）：

```
warm        ← skein_nodes_online       creating     ← skein_active_dispatches
watermark   ← skein_review_backlog     reject_rate  ← skein_stalled_signals
p50/p99/rps/err5xx ← None（雁阵是控制面，不是请求网关）
```

## 构建

```bash
BASE_IMAGE=xiaoqiu:1.0.96-sso OUT_IMAGE=xiaoqiu:1.0.97-skein bash build.sh
```

## 运行（ops 面，宿主 docker）

```bash
docker run -d --name xiaoqiu-ops --network host \
  --env-file /etc/skein/xiaoqiu-ops.env -e XIAOQIU_PORT=8118 \
  -v /root/SSE_Market/log/server:/srv/go-log:ro \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --restart unless-stopped --log-opt max-size=50m --log-opt max-file=3 \
  xiaoqiu:1.0.97-skein
```

`/etc/skein/xiaoqiu-ops.env` 由原 Deployment 的 14 个 env 解出，另加 `SKEIN_TOKEN`
（控制面令牌），600 权限。镜像自带的生产 `.env` 提供 PG/Qdrant/面板密钥。

## 验证

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8118/health/live   # 200
docker exec -w /srv/sse-agent xiaoqiu-ops python -c "
from dotenv import load_dotenv; load_dotenv('/srv/sse-agent/.env')
import asyncio; from app import db; from app.config import get_config
async def m():
    p=await db.create_pool(get_config().pg_dsn, name='chk', min_size=1, max_size=1)
    for r in await p.fetch(\"select name,value from qdock_metric_samples where name like 'skein_%' order by ts desc limit 5\"): print(r['name'], float(r['value']))
    await p.close()
asyncio.run(m())"
```

应看到 `skein_review_backlog` / `skein_nodes_online` 等采样。

## 回滚

1. `kubectl scale deploy ag-xiaoqiu-ops-v-2611041612 -n qiudock --replicas=1`
2. 恢复 `/root/SSE_Market/nginx_proxy/nginx.conf.bak-ops-cutover-*`，`nginx -t -c /etc/nginx/custom.conf` 后 reload
3. `docker stop xiaoqiu-ops`
