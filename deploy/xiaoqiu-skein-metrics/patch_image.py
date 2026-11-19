"""把 xiaoqiu 镜像里的指标源从 qdockd 换成雁阵控制面。

输入：一个工作目录，内含从基础镜像抽出的
  app/main.py、app/config.py、app/ops/alerts.py、config/settings.yaml
输出：就地打补丁；任何一处锚点没命中都会报错（防止镜像漂移时静默半打）。

补丁内容：
1) main.py：`from app.ops import qdock as qdock_metrics` → `skein_metrics`
   （变量名保持不变，启动/采集/关闭/序列四处调用同时换源）
2) config.py：新增 SkeinCfg（base_url/token_env/url_env/enabled/scrape_seconds/prune_days）
3) alerts.py：METRICS 增加 skein_* 四个指标；SEED 增加四条雁阵规则
4) settings.yaml：增加 skein 段；qdock 段的 enabled 置为 false（只作为旧镜像回滚锚）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKELETON = '''
class SkeinCfg(BaseModel):
    """雁阵平台指标采集（替代 qdockd 指标）：控制面 summary/signals/topology。"""
    base_url: str = "http://172.17.0.1:7800"
    token_env: str = "SKEIN_TOKEN"
    url_env: str = ""
    enabled: bool = False
    scrape_seconds: int = 30
    prune_days: int = 3


'''

RULES = '''
    # ---- 雁阵平台（替代秋坞指标） ----
    ("雁阵待验收积压", "skein_review_backlog", ">", 5.0, 1, "warn", 720),
    ("雁阵卡住信号", "skein_stalled_signals", ">", 0.0, 1, "critical", 300),
    ("雁阵节点离线", "skein_nodes_offline", ">", 0.0, 1, "critical", 300),
    ("雁阵采集断供", "skein_scrape_age_s", ">", 600.0, 1, "warn", 60),'''

QDOCK_ENABLED = re.compile(r"(^qdock:[^\n]*\n(?:[ \t].*\n)*?[ \t]+enabled: )true", re.M)


def must_replace(text: str, old: str, new: str, name: str) -> str:
    if old not in text:
        raise SystemExit(f"锚点未命中：{name}")
    return text.replace(old, new, 1)


def patch(work: Path) -> None:
    main = work / "app/main.py"
    text = main.read_text()
    count = text.count("from app.ops import qdock as qdock_metrics")
    if count != 4:
        raise SystemExit(f"main.py 的 qdock 导入应为 4 处，实际 {count}")
    text = text.replace(
        "from app.ops import qdock as qdock_metrics",
        "from app.ops import skein_metrics as qdock_metrics",
    )
    text = must_replace(
        text,
        "    if cfg.qdock.enabled:\n        _scheduler.add_job(\n            qdock_metrics.scrape_once,",
        "    if cfg.qdock.enabled or cfg.skein.enabled:\n        _scheduler.add_job(\n            qdock_metrics.scrape_once,",
        "scheduler gate",
    )
    main.write_text(text)

    cfg = work / "app/config.py"
    text = cfg.read_text()
    text = must_replace(text, "class EdgeCfg(BaseModel):", SKELETON + "class EdgeCfg(BaseModel):", "SkeinCfg 插入点")
    text = must_replace(
        text,
        "    qdock: QdockCfg = Field(default_factory=QdockCfg)",
        "    qdock: QdockCfg = Field(default_factory=QdockCfg)\n"
        "    skein: SkeinCfg = Field(default_factory=SkeinCfg)",
        "Config 字段",
    )
    text = must_replace(
        text,
        '        qdock=QdockCfg(**raw.get("qdock", {})),',
        '        qdock=QdockCfg(**raw.get("qdock", {})),\n'
        '        skein=SkeinCfg(**raw.get("skein", {})),',
        "settings 装载",
    )
    cfg.write_text(text)

    alerts = work / "app/ops/alerts.py"
    text = alerts.read_text()
    text = must_replace(
        text,
        '"qdock_scrape_age_s",',
        '"qdock_scrape_age_s",\n'
        '           "skein_review_backlog", "skein_stalled_signals", '
        '"skein_nodes_offline", "skein_scrape_age_s",',
        "METRICS",
    )
    anchor = '    ("秋坞采集断供", "qdock_scrape_age_s", ">", 600.0, 1, "warn", 60),'
    text = must_replace(text, anchor, anchor + RULES, "SEED")
    alerts.write_text(text)

    settings = work / "config/settings.yaml"
    text = settings.read_text()
    if not QDOCK_ENABLED.search(text):
        raise SystemExit("settings.yaml: qdock.enabled 锚点未命中")
    text = QDOCK_ENABLED.sub(r"\1false", text, count=1)
    text = text.rstrip() + """
skein:
  enabled: true
  base_url: http://172.17.0.1:7800
  token_env: SKEIN_TOKEN
  scrape_seconds: 30
  prune_days: 3
"""
    settings.write_text(text)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_image.py <workdir>")
    patch(Path(sys.argv[1]))
    print("patched")
