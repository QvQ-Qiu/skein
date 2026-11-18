"""对外通道。Outbox 的写操作最终落在这里。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class ForumWriteError(RuntimeError):
    pass


class HttpWriter:
    """按主站内部口的形状写出去。

    地址模板与鉴权头需要和主站对齐；当前按 `/internal/<kind>` +
    `X-Internal-Token` 实现。显式禁用系统代理，避免本地调试被 Clash 类代理劫持。
    """

    def __init__(self, base: str, token: str, timeout: float = 10.0) -> None:
        self._base = base.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, kind: str, object_: str, payload: dict) -> dict:
        url = f"{self._base}/internal/{kind}"
        body = json.dumps({"object": object_, "payload": payload}, ensure_ascii=False).encode()
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "X-Internal-Token": self._token},
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ForumWriteError(f"forum returned {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ForumWriteError(str(exc.reason)) from exc
        return json.loads(raw) if raw else {}
