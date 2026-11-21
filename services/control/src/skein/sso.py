"""UniSSO 内接：M2M 密码校验 + 共享会话 Cookie。

契约（/root/unisso-migration/SSO-CONTRACT.md）：
- 登录  POST /api/oauth/password/verify（JSON）→ user + sso_session + sso_cookie_*
- 探测  POST /api/oauth/session/verify（form: session=&client_id=&client_secret=）
- 登出  POST /api/oauth/session/revoke（form，同上）
Cookie 名以响应为准（unisso_sid，旧名 sse_sso 兜底）；值不可自证，必须服务端回验。
401 是「没登录/会话坏」，429/5xx/超时是「unisso 暂不可用」——两者语义必须分开。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

BASE = "https://sso.ssemarket.cn"
COOKIE_NAMES = ("unisso_sid", "sse_sso")
VERIFY_TTL = 60.0
CACHE_MAX = 512

CURRENT_PRINCIPAL: ContextVar[Principal | None] = ContextVar(
    "skein_principal", default=None
)


class SsoUnavailable(Exception):
    """unisso 不可达 / 429 / 5xx：不能当成登出。"""


class SsoRejected(Exception):
    """会话或凭据确实无效：可以放心拒绝。"""

    def __init__(self, code: str, status: int = 401) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Principal:
    sub: str
    name: str = ""
    email: str = ""
    internal: bool = False
    picture: str = ""
    admin: bool = False
    super_admin: bool = False
    roles: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.name or self.email or self.sub

    def as_dict(self) -> dict[str, Any]:
        return {
            "sub": self.sub,
            "name": self.name,
            "email": self.email,
            "internal": self.internal,
            "picture": self.picture,
            "admin": self.admin,
            "super_admin": self.super_admin,
            "roles": list(self.roles),
        }


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def principal_from(payload: Any) -> Principal | None:
    user = payload.get("user") if isinstance(payload, dict) else None
    if not isinstance(user, dict) or not user.get("sub"):
        return None
    raw_roles = user.get("roles") or []
    roles = tuple(str(role) for role in raw_roles) if isinstance(raw_roles, list) else ()
    super_admin = _flag(user.get("is_super_admin")) or "super_admin" in roles
    admin = (
        _flag(user.get("is_admin"))
        or _flag(user.get("admin"))
        or "admin" in roles
        or super_admin
    )
    return Principal(
        sub=str(user["sub"]),
        name=str(user.get("name") or ""),
        email=str(user.get("email") or ""),
        internal=_flag(user.get("internal")),
        picture=str(user.get("picture") or ""),
        admin=admin,
        super_admin=super_admin,
        roles=roles,
    )


@dataclass(frozen=True)
class SsoConfig:
    base: str = BASE
    client_id: str = ""
    client_secret: str = ""
    # None=按 unisso 响应种；""=host-only（本地 http 调试）；其他值=显式域
    cookie_domain: str | None = None
    require_internal: bool = True
    timeout: float = 5.0

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)


def _read_json(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _post(
    base: str,
    path: str,
    body: bytes,
    content_type: str,
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _read_json(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _read_json(error.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SsoUnavailable(str(error)) from error


class SsoClient:
    def __init__(self, config: SsoConfig) -> None:
        self._config = config

    def password_verify(self, email: str, password: str) -> dict[str, Any]:
        body = json.dumps(
            {
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "email": email,
                "password": password,
            }
        ).encode()
        status, payload = _post(
            self._config.base,
            "/api/oauth/password/verify",
            body,
            "application/json",
            self._config.timeout,
        )
        if status == 200:
            return payload
        if status == 401:
            raise SsoRejected("invalid_credentials", 401)
        if status == 403:
            raise SsoRejected("password_verify_forbidden", 403)
        if status == 429:
            raise SsoUnavailable("too_many_attempts")
        raise SsoUnavailable(f"unisso_http_{status}")

    def session_verify(self, session: str) -> Principal | None:
        body = urllib.parse.urlencode(
            {
                "session": session,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
            }
        ).encode()
        status, payload = _post(
            self._config.base,
            "/api/oauth/session/verify",
            body,
            "application/x-www-form-urlencoded",
            self._config.timeout,
        )
        if status == 200:
            return principal_from(payload)
        if status == 401:
            raise SsoRejected("invalid_sso", 401)
        if status == 403:
            raise SsoRejected("not_internal", 403)
        raise SsoUnavailable(f"unisso_http_{status}")

    def session_revoke(self, session: str) -> None:
        body = urllib.parse.urlencode(
            {
                "session": session,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
            }
        ).encode()
        status, _ = _post(
            self._config.base,
            "/api/oauth/session/revoke",
            body,
            "application/x-www-form-urlencoded",
            self._config.timeout,
        )
        if status >= 500:
            raise SsoUnavailable(f"unisso_http_{status}")
        if status in (401, 403):
            raise SsoRejected("invalid_sso", 401)


class SsoAuth:
    """读请求 Cookie → 回验 unisso → Principal；结果缓存 60 秒。"""

    def __init__(
        self,
        client: Any,
        config: SsoConfig,
        verify: Callable[[str], Principal | None] | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._verify = verify if verify is not None else client.session_verify
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[Any, float]] = {}

    @staticmethod
    def cookie_value(cookies: dict[str, str]) -> str | None:
        for name in COOKIE_NAMES:
            value = cookies.get(name)
            if value:
                return value
        return None

    def verify(self, cookies: dict[str, str], level: str = "admin") -> Principal:
        session = self.cookie_value(cookies)
        if not session:
            raise SsoRejected("no_sso", 401)
        now = time.time()
        with self._lock:
            hit = self._cache.get(session)
        if hit and hit[1] > now:
            cached = hit[0]
            if isinstance(cached, SsoRejected):
                raise cached
            if isinstance(cached, Principal):
                # 缓存的是会话对应的主体，不是授权结论；每次按入口重新判。
                self._check_policy(cached, level)
                return cached
        try:
            principal = self._verify(session)
        except SsoRejected as error:
            self._remember(session, error, now)
            raise
        if principal is None:
            error = SsoRejected("invalid_sso", 401)
            self._remember(session, error, now)
            raise error
        self._check_policy(principal, level)
        self._remember(session, principal, now)
        return principal

    def check(self, principal: Principal, level: str = "admin") -> None:
        """密码登录拿到的 Principal 也要过同一套入口策略。"""
        self._check_policy(principal, level)

    def _check_policy(self, principal: Principal, level: str) -> None:
        if level == "admin":
            # 管理员必须是集市用户，并且 unisso 标了 is_admin。
            if not principal.internal:
                raise SsoRejected("not_internal", 403)
            if not principal.admin:
                raise SsoRejected("not_admin", 403)
            return
        # market：集市用户（unisso internal）可进；external 不进。
        if self._config.require_internal and not principal.internal:
            raise SsoRejected("not_internal", 403)

    def _remember(self, session: str, value: Any, now: float) -> None:
        with self._lock:
            if len(self._cache) >= CACHE_MAX:
                for key in list(self._cache)[: CACHE_MAX // 4]:
                    self._cache.pop(key, None)
            self._cache[session] = (value, now + VERIFY_TTL)

    def invalidate(self, session: str) -> None:
        with self._lock:
            self._cache.pop(session, None)


def cookie_domain_for(config: SsoConfig, from_response: str | None = None) -> str | None:
    """种 Cookie 用的域：显式覆盖优先，其次响应字段。空串表示 host-only。"""
    if config.cookie_domain is not None:
        return config.cookie_domain or None
    return from_response or None


def cookie_delete_domain(config: SsoConfig) -> str | None:
    """清 Cookie 用的域：无法拿到响应字段，按显式覆盖或生产默认。"""
    if config.cookie_domain is not None:
        return config.cookie_domain or None
    return ".ssemarket.cn"
