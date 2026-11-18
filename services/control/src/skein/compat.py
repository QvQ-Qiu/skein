"""主站写接口的兼容层：作业不改代码，只把 base_url 指到控制面。

两种形态：
- `/internal/featured`：X-Internal-Token，批量加精，按帖幂等；
- `/api/auth/post`：用户 Bearer 发帖，按内容哈希幂等。

控制面在这里持有写权限，作业只发请求。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class ForumCompatWriter:
    def __init__(self, base: str, timeout: float = 30.0) -> None:
        self._base = base.rstrip("/")
        self._timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, kind: str, object_: str, payload: dict) -> dict:
        if kind == "featured":
            return self._post(
                "/internal/featured",
                {"post_ids": [int(object_)]},
                {"X-Internal-Token": payload.get("token", "")},
            )
        if kind == "post":
            return self._post(
                "/api/auth/post",
                payload["body"],
                {"Authorization": payload.get("authorization", "")},
            )
        raise ValueError(f"unknown kind {kind}")

    def _post(self, path: str, body: dict, headers: dict) -> dict:
        request = urllib.request.Request(
            self._base + path,
            data=json.dumps(body, ensure_ascii=False).encode(),
            method="POST",
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"forum returned {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc
        return json.loads(raw) if raw else {}
