"""控制面侧的 GitHub PR：工雁只推分支，这里用 API 建 draft PR。

令牌只留在控制面，不进节点、不进 worker。只在 result 明确 pushed 时调用。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Http = Callable[[str, str, dict | None], tuple[int, dict]]

_REMOTE_RE = re.compile(
    r"github\.com[/:](?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

# 分支存在性缓存的存活期：develop 可能中途被建出来，不能永远缓存。
_BASE_TTL = 600.0


class ForgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    state: str = "open"
    base: str = ""


@dataclass(frozen=True)
class RepoTarget:
    """SKEIN_GITHUB_REPOS 的条目：平台 → 仓库与 PR 基准分支（base 空 = 自动解析）。"""

    slug: str = ""
    base: str = ""


def normalize_repos(raw: dict[str, Any] | None) -> dict[str, RepoTarget]:
    """值兼容 "owner/repo"、{"slug": ..., "base": ...} 与 RepoTarget 本身三种写法。"""
    repos: dict[str, RepoTarget] = {}
    for key, value in (raw or {}).items():
        if isinstance(value, RepoTarget):
            repos[str(key)] = value
        elif isinstance(value, str):
            repos[str(key)] = RepoTarget(slug=value)
        elif isinstance(value, dict):
            repos[str(key)] = RepoTarget(
                slug=str(value.get("slug") or ""),
                base=str(value.get("base") or ""),
            )
    return repos


def github_repo_from_remote(remote: str) -> str | None:
    """从 git remote 里取 owner/repo；不是 GitHub 就返回 None。"""
    value = (remote or "").strip()
    if not value:
        return None
    match = _REMOTE_RE.search(value)
    if match is None:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


class GitHubClient:
    def __init__(
        self,
        token: str,
        api: str = "https://api.github.com",
        timeout: float = 15.0,
        http: Http | None = None,
    ) -> None:
        self._token = token
        self._api = api.rstrip("/")
        self._timeout = timeout
        self._http = http or self._urllib

    def _urllib(self, method: str, url: str, payload: dict | None) -> tuple[int, dict]:
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "skein-control",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
                return response.status, json.loads(raw.decode() or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                data = json.loads(raw.decode() or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = {"message": raw.decode("utf-8", "replace")}
            return exc.code, data
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ForgeError(f"github request failed: {exc}") from exc

    def _pull(self, data: dict) -> PullRequest | None:
        number = data.get("number")
        url = str(data.get("html_url") or "")
        if not isinstance(number, int) or not url:
            return None
        raw_base = data.get("base")
        base = str(raw_base.get("ref") or "") if isinstance(raw_base, dict) else str(raw_base or "")
        return PullRequest(number=number, url=url, state=str(data.get("state") or "open"), base=base)

    def repo_info(self, repo: str) -> dict | None:
        status, data = self._http("GET", f"{self._api}/repos/{repo}", None)
        return data if status == 200 and isinstance(data, dict) else None

    def has_branch(self, repo: str, branch: str) -> bool:
        url = f"{self._api}/repos/{repo}/branches/{urllib.parse.quote(branch, safe='')}"
        status, _ = self._http("GET", url, None)
        return status == 200

    def find_open(self, repo: str, head: str) -> PullRequest | None:
        owner = repo.split("/", 1)[0]
        query = urllib.parse.urlencode({"state": "open", "head": f"{owner}:{head}"})
        status, data = self._http(
            "GET", f"{self._api}/repos/{repo}/pulls?{query}", None
        )
        if status != 200 or not isinstance(data, list):
            return None
        for item in data:
            if isinstance(item, dict):
                found = self._pull(item)
                if found is not None:
                    return found
        return None

    def create(
        self,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> PullRequest:
        if "/" not in repo:
            raise ForgeError(f"invalid repo: {repo}")
        if not head:
            raise ForgeError("missing head branch")
        payload = {
            "title": title[:250],
            "head": head,
            "base": base,
            "body": body[:60000],
            "draft": draft,
        }
        status, data = self._http("POST", f"{self._api}/repos/{repo}/pulls", payload)
        if status == 201:
            created = self._pull(data)
            if created is not None:
                return created
            raise ForgeError("github returned an unexpected pull request payload")
        if status == 422:
            existing = self.find_open(repo, head)
            if existing is not None:
                return existing
        message = str(data.get("message") or "")
        raise ForgeError(f"github {status}: {message}"[:300])


class PullRequestService:
    """把一次工雁 result 变成 draft PR。

    基准分支解析顺序：仓库级 base → 全局 SKEIN_GITHUB_BASE → 自动
    （仓库有 develop 用 develop，否则用仓库默认分支）。自动结果按仓库短暂缓存。
    """

    def __init__(
        self,
        client: GitHubClient,
        repos: dict[str, RepoTarget | str] | None = None,
        base: str = "",
        draft: bool = True,
    ) -> None:
        self._client = client
        self._repos = normalize_repos(repos)
        self._base = (base or "").strip()
        self._draft = draft
        self._auto_cache: dict[str, tuple[str, float]] = {}

    def _target_for(self, result: dict) -> RepoTarget:
        repo = github_repo_from_remote(str(result.get("remote") or ""))
        if repo:
            return RepoTarget(slug=repo)
        target = self._repos.get(str(result.get("platform") or ""))
        return target if target is not None else RepoTarget()

    def base_for(self, platform: str, repo: str) -> str:
        target = self._repos.get(platform)
        if target is not None and target.base:
            return target.base
        if self._base:
            return self._base
        return self._auto_base(repo)

    def _auto_base(self, repo: str) -> str:
        if not repo:
            return "main"
        cached = self._auto_cache.get(repo)
        if cached is not None and time.monotonic() < cached[1]:
            return cached[0]
        info = self._client.repo_info(repo)
        default = str(info.get("default_branch") or "") if info else ""
        try:
            has_develop = self._client.has_branch(repo, "develop")
        except ForgeError:
            # 网络抖动 ≠ 仓库没有 develop：退回默认分支，但不缓存，下次重新探测
            return default or "main"
        branch = "develop" if has_develop else (default or "main")
        self._auto_cache[repo] = (branch, time.monotonic() + _BASE_TTL)
        return branch

    @staticmethod
    def _body(ticket: dict, result: dict, base: str = "") -> str:
        code = str(ticket.get("code") or "")
        title = str(ticket.get("title") or "")
        summary = str(ticket.get("summary") or "").strip()
        parts = [
            f"Skein 工单 {code}：{title}",
            "",
        ]
        if summary:
            parts += ["摘要：", summary[:4000], ""]
        parts += [
            f"- 分支：{result.get('branch') or ''}",
            f"- 基线：{result.get('base_commit') or ''}",
        ]
        if base:
            parts += [f"- 目标分支：{base}（合入即触发测试环境部署）"]
        parts += [
            "- 提交：",
            str(result.get("commits") or "（无）")[:3000],
            "",
            "- 变更：",
            str(result.get("diffstat") or "（无）")[:3000],
            "",
            "由 Skein 工雁提交，等待人工验收；不会自动合入，也不会自动部署。",
        ]
        return "\n".join(parts)

    def open_for(self, ticket: dict, result: Any) -> PullRequest | None:
        if not isinstance(result, dict):
            return None
        if not result.get("ok") or not result.get("pushed") or not result.get("branch"):
            return None
        target = self._target_for(result)
        repo = target.slug
        if repo is None or not repo:
            return None
        platform = str(result.get("platform") or "")
        base = self.base_for(platform, repo)
        code = str(ticket.get("code") or "")
        title = str(ticket.get("title") or "").strip() or "Skein fix"
        pr = self._client.create(
            repo=repo,
            head=str(result.get("branch")),
            base=base,
            title=f"{code}: {title}" if code else title,
            body=self._body(ticket, result, base),
            draft=self._draft,
        )
        if not pr.base:
            pr = PullRequest(number=pr.number, url=pr.url, state=pr.state, base=base)
        return pr
