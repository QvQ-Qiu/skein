from skein.config import Config


def test_platform_repos_parsed() -> None:
    config = Config.from_env({"SKEIN_PLATFORM_REPOS": '{"forum": "/srv/repo"}'})
    assert config.platform_repos == {"forum": "/srv/repo"}


def test_platform_repos_invalid_json_is_empty() -> None:
    assert Config.from_env({"SKEIN_PLATFORM_REPOS": "not-json"}).platform_repos == {}


def test_platform_repos_default_empty() -> None:
    assert Config.from_env({}).platform_repos == {}


def test_control_url_parsed() -> None:
    config = Config.from_env({"SKEIN_CONTROL_URL": "http://127.0.0.1:7800"})
    assert config.control_url == "http://127.0.0.1:7800"


def test_dispatch_stale_default_and_override() -> None:
    assert Config.from_env({}).dispatch_stale == 7200
    assert Config.from_env({"SKEIN_DISPATCH_STALE": "0"}).dispatch_stale == 0
    assert Config.from_env({"SKEIN_DISPATCH_STALE": "900"}).dispatch_stale == 900
