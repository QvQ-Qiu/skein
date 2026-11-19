"""把 xiaoqiu 的两面挂到控制面后面：统一入口 + 权限隔离。

- `/api/v1/agent/*`、`/task/*` → chat 上游：论坛用户 JWT 原样透传，
  用户级鉴权仍由上游负责；Cookie 不转发，避免把 SSO 会话带给上游。
- `/ops/*` → ops 上游：只允许内部主体（服务令牌，或 SSO 会话且 internal=1），
  由 api.py 的 internal_only 依赖把关。

上游地址为空时对应路由返回 503，控制面等同没启用这层代理。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import AsyncIterator

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
}


class QAProxy:
    def __init__(
        self,
        chat_upstream: str = "",
        ops_upstream: str = "",
        client: httpx.AsyncClient | None = None,
        panel_secret: str = "",
        panel_sub: str = "",
        panel_email: str = "skein@ssemarket.cn",
        panel_ttl: int = 3600,
    ) -> None:
        self.chat_upstream = chat_upstream.rstrip("/")
        self.ops_upstream = ops_upstream.rstrip("/")
        self._panel_secret = panel_secret
        self._panel_sub = panel_sub
        self._panel_email = panel_email
        self._panel_ttl = panel_ttl
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
            follow_redirects=False,
        )

    @property
    def enabled_chat(self) -> bool:
        return bool(self.chat_upstream)

    @property
    def enabled_ops(self) -> bool:
        return bool(self.ops_upstream)

    def _panel_cookie(self) -> str:
        """给 ops 上游签一个面板会话：ops_session=sub|uid|email|exp.HMAC。"""
        if not (self._panel_secret and self._panel_sub):
            return ""
        exp = int(time.time()) + self._panel_ttl
        payload = f"{self._panel_sub}||{self._panel_email}|{exp}"
        sig = hmac.new(
            self._panel_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return f"ops_session={payload}|{sig}"

    async def forward(self, request: Request, upstream: str, path: str) -> StreamingResponse:
        url = f"{upstream}/{path.lstrip('/')}"
        if request.url.query:
            url += f"?{request.url.query}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_HEADERS and key.lower() != "cookie"
        }
        if upstream == self.ops_upstream:
            panel = self._panel_cookie()
            if panel:
                headers["cookie"] = panel
        body = await request.body()
        outgoing = self._client.build_request(
            request.method, url, headers=headers, content=body or None
        )
        try:
            response = await self._client.send(outgoing, stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"qa upstream error: {exc}") from exc
        response_headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in HOP_HEADERS
            and key.lower() not in ("content-length", "content-encoding")
        }
        return StreamingResponse(
            self._stream(response),
            status_code=response.status_code,
            headers=response_headers,
            background=BackgroundTask(response.aclose),
        )

    @staticmethod
    async def _stream(response: httpx.Response) -> AsyncIterator[bytes]:
        async for chunk in response.aiter_raw():
            yield chunk
