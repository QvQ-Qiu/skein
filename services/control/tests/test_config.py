from skein.config import Config


def test_platform_repos_parsed() -> None:
    config = Config.from_env({"SKEIN_PLATFORM_REPOS": '{"forum": "/srv/repo"}'})
    assert config.platform_repos == {"forum": "/srv/repo"}


def test_platform_repos_invalid_json_is_empty() -> None:
    assert Config.from_env({"SKEIN_PLATFORM_REPOS": "not-json"}).platform_repos == {}


def test_platform_repos_default_empty() -> None:
    assert Config.from_env({}).platform_repos == {}


def test_router_session_defaults_off() -> None:
    config = Config.from_env({})
    assert config.router_session is False
    assert config.router_batch == 8
    assert config.router_interval == 0.5


def test_router_session_parsed() -> None:
    config = Config.from_env(
        {
            "SKEIN_ROUTER_SESSION": "1",
            "SKEIN_ROUTER_BATCH": "4",
            "SKEIN_ROUTER_INTERVAL": "0.2",
        }
    )
    assert config.router_session is True
    assert config.router_batch == 4
    assert config.router_interval == 0.2


def test_control_url_parsed() -> None:
    config = Config.from_env({"SKEIN_CONTROL_URL": "http://127.0.0.1:7800"})
    assert config.control_url == "http://127.0.0.1:7800"
