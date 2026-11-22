import pytest

from skein.forge import (
    ForgeError,
    GitHubClient,
    PullRequest,
    PullRequestService,
    RepoTarget,
    github_repo_from_remote,
    normalize_repos,
)


def test_parse_github_remotes() -> None:
    assert github_repo_from_remote("git@github.com:QvQ-Qiu/skein.git") == "QvQ-Qiu/skein"
    assert github_repo_from_remote("https://github.com/QvQ-Qiu/skein") == "QvQ-Qiu/skein"
    assert github_repo_from_remote("ssh://git@github.com/o/r.git/") == "o/r"
    assert github_repo_from_remote("git@gitee.com:o/r.git") is None
    assert github_repo_from_remote("") is None


def test_create_draft_pr_payload() -> None:
    calls = []

    def http(method, url, payload):
        calls.append((method, url, payload))
        return 201, {"number": 7, "html_url": "https://github.com/o/r/pull/7", "state": "open"}

    client = GitHubClient("tok", http=http)
    pr = client.create("o/r", "fix/x", "main", "title", "body")

    assert pr == PullRequest(number=7, url="https://github.com/o/r/pull/7")
    method, url, payload = calls[0]
    assert method == "POST"
    assert url == "https://api.github.com/repos/o/r/pulls"
    assert payload == {
        "title": "title",
        "head": "fix/x",
        "base": "main",
        "body": "body",
        "draft": True,
    }


def test_existing_pr_returned_on_422() -> None:
    calls = []

    def http(method, url, payload):
        calls.append((method, url))
        if method == "POST":
            return 422, {"message": "A pull request already exists"}
        return 200, [{"number": 3, "html_url": "https://github.com/o/r/pull/3"}]

    client = GitHubClient("tok", http=http)
    pr = client.create("o/r", "fix/x", "main", "t", "b")

    assert pr == PullRequest(number=3, url="https://github.com/o/r/pull/3")
    assert calls[1][0] == "GET"
    assert "head=o%3Afix%2Fx" in calls[1][1]


def test_create_failure_raises() -> None:
    client = GitHubClient("tok", http=lambda *_: (500, {"message": "boom"}))
    with pytest.raises(ForgeError):
        client.create("o/r", "fix/x", "main", "t", "b")


def test_service_skips_unpushed_or_gitee() -> None:
    service = PullRequestService(GitHubClient("tok", http=lambda *_: (201, {})))

    assert service.open_for({"code": "SK-1"}, {"ok": True, "pushed": False, "branch": "b"}) is None
    assert (
        service.open_for(
            {"code": "SK-1"},
            {"ok": True, "pushed": True, "branch": "b", "remote": "git@gitee.com:o/r.git"},
        )
        is None
    )


def test_service_uses_platform_mapping_and_builds_body() -> None:
    calls = []

    def http(method, url, payload):
        calls.append((url, payload))
        return 201, {"number": 9, "html_url": "https://github.com/o/r/pull/9"}

    service = PullRequestService(
        GitHubClient("tok", http=http),
        repos={"skein": "o/r"},
        base="main",
    )
    pr = service.open_for(
        {"code": "SK-9", "title": "修登录", "summary": "点登录没反应"},
        {
            "ok": True,
            "pushed": True,
            "branch": "fix/yz-sk-9",
            "base_commit": "abc1234",
            "commits": "abc1234 fix login",
            "diffstat": "1 file changed",
            "remote": "/home/cloud/repos/skein",
            "platform": "skein",
        },
    )

    assert pr is not None
    assert pr.base == "main"
    _, payload = calls[0]
    assert payload["head"] == "fix/yz-sk-9"
    assert payload["base"] == "main"
    assert payload["title"] == "SK-9: 修登录"
    assert "点登录没反应" in payload["body"]
    assert "不会自动合入" in payload["body"]


def test_normalize_repos_accepts_both_forms() -> None:
    assert normalize_repos({"a": "o/r"}) == {"a": RepoTarget(slug="o/r")}
    assert normalize_repos({"b": {"slug": "o/r2", "base": "develop"}}) == {
        "b": RepoTarget(slug="o/r2", base="develop")
    }
    assert normalize_repos({"d": RepoTarget(slug="o/r3", base="main")}) == {
        "d": RepoTarget(slug="o/r3", base="main")
    }
    assert normalize_repos({"c": 42}) == {}  # 非法值直接丢弃
    assert normalize_repos(None) == {}


def test_repo_info_and_has_branch() -> None:
    def http(method, url, payload):
        if url.endswith("/repos/o/r"):
            return 200, {"default_branch": "prod"}
        if url.endswith("/repos/o/r/branches/develop"):
            return 404, {"message": "Not Found"}
        return 500, {}

    client = GitHubClient("tok", http=http)
    assert client.repo_info("o/r") == {"default_branch": "prod"}
    assert client.has_branch("o/r", "develop") is False


def test_base_auto_uses_develop_then_default_branch() -> None:
    def http(method, url, payload):
        if url.endswith("/repos/o/r"):
            return 200, {"default_branch": "prod"}
        if url.endswith("/repos/o/r/branches/develop"):
            return 200, {"name": "develop"}
        return 500, {}

    service = PullRequestService(GitHubClient("tok", http=http))
    assert service.base_for("", "o/r") == "develop"

    def http_no_develop(method, url, payload):
        if url.endswith("/repos/o/r"):
            return 200, {"default_branch": "prod"}
        if url.endswith("/repos/o/r/branches/develop"):
            return 404, {}
        return 500, {}

    service = PullRequestService(GitHubClient("tok", http=http_no_develop))
    assert service.base_for("", "o/r") == "prod"


def test_base_priority_repo_then_global_then_auto() -> None:
    calls = []

    def http(method, url, payload):
        calls.append(url)
        if url.endswith("/repos/o/r"):
            return 200, {"default_branch": "prod"}
        if url.endswith("/repos/o/r/branches/develop"):
            return 200, {}
        return 500, {}

    client = GitHubClient("tok", http=http)
    per_repo = PullRequestService(
        client, repos={"market": RepoTarget(slug="o/r", base="staging")}
    )
    assert per_repo.base_for("market", "o/r") == "staging"
    # base 显式配置时不应发起任何探测请求
    assert calls == []

    global_base = PullRequestService(client, base="release")
    assert global_base.base_for("", "o/r") == "release"

    auto = PullRequestService(client)
    assert auto.base_for("market", "o/r") == "develop"
    assert auto.base_for("", "o/r") == "develop"  # 命中缓存，不再探测


def test_auto_base_falls_back_to_default_when_branch_probe_fails() -> None:
    def http(method, url, payload):
        if url.endswith("/repos/o/r"):
            return 200, {"default_branch": "prod"}
        raise ForgeError("network down")

    service = PullRequestService(GitHubClient("tok", http=http))
    assert service.base_for("", "o/r") == "prod"
