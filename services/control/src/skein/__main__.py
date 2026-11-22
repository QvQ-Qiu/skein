"""进程入口：python -m skein"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import uvicorn

from skein import db, logbuf
from skein.api import create_app
from skein.config import Config
from skein.driver import Driver, parse_schedules
from skein.forge import GitHubClient, PullRequestService, github_repo_from_remote
from skein.sso import SsoConfig


def test_envs_by_platform(cfg: Config) -> dict[str, str]:
    """SKEIN_TEST_ENVS 以仓库 slug 为键；控制台按平台查，这里换好键。"""
    envs: dict[str, str] = {}
    for platform, target in cfg.github_repos.items():
        url = cfg.test_envs.get(target.slug, "")
        if url:
            envs[platform] = url
    for platform, remote in cfg.platform_repos.items():
        url = cfg.test_envs.get(github_repo_from_remote(remote) or "", "")
        if url:
            envs.setdefault(platform, url)
    return envs


def build() -> tuple:
    cfg = Config.from_env()
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.blob_root).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.blob_root).mkdir(parents=True, exist_ok=True)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    sso = SsoConfig(
        base=cfg.sso_base,
        client_id=cfg.sso_client_id,
        client_secret=cfg.sso_client_secret,
        cookie_domain=cfg.sso_cookie_domain,
    )
    pr_service = None
    if cfg.github_token:
        pr_service = PullRequestService(
            GitHubClient(cfg.github_token, api=cfg.github_api),
            repos=cfg.github_repos,
            base=cfg.github_base,
            draft=cfg.github_pr_draft,
        )
    app = create_app(
        conn,
        cfg.blob_root,
        cfg.token,
        platform_repos=cfg.platform_repos,
        pr_service=pr_service,
        test_envs=test_envs_by_platform(cfg),
        sso_config=sso,
        qa_chat_upstream=cfg.qa_chat_upstream,
        qa_user_secret=cfg.assertion_key,
        llm_base=cfg.llm_base,
        llm_key=cfg.llm_key,
        agent_model=cfg.agent_model,
    )
    return app, cfg, conn


def main() -> None:
    logbuf.install()
    app, cfg, conn = build()
    logging.getLogger("skein").info(
        "control plane started db=%s port=%s", cfg.db_path, cfg.port
    )
    schedules_raw = cfg.schedules
    commands_raw = cfg.job_commands
    if cfg.jobs_file and Path(cfg.jobs_file).is_file():
        data = json.loads(Path(cfg.jobs_file).read_text(encoding="utf-8"))
        schedules_raw = json.dumps(data.get("schedules") or [])
        commands_raw = json.dumps(data.get("commands") or {})
        logging.getLogger("skein").info("作业配置来自 %s", cfg.jobs_file)
    if cfg.driver_enabled and schedules_raw:
        schedules = parse_schedules(schedules_raw)
        try:
            commands = json.loads(commands_raw or "{}")
        except json.JSONDecodeError:
            commands = {}
        if schedules:
            driver = Driver(conn, schedules, commands, timeout=cfg.job_timeout)
            threading.Thread(target=driver.run_forever, daemon=True).start()
    uvicorn.run(app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
