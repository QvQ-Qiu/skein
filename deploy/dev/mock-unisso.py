"""本地联调用的假 unisso：实现 password/verify、session/verify、revoke 三条契约。

账号：e2e@ssemarket.cn / right-pass（internal），guest@ssemarket.cn / right-pass（非 internal）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

CLIENT_ID = "dev-client"
CLIENT_SECRET = "dev-secret"
SESSION = "sess-local-1"
USERS = {
    "e2e@ssemarket.cn": {
        "sub": "u-e2e",
        "name": "验收同学",
        "email": "e2e@ssemarket.cn",
        "internal": True,
        "picture": "",
    },
    "guest@ssemarket.cn": {
        "sub": "u-guest",
        "name": "访客",
        "email": "guest@ssemarket.cn",
        "internal": False,
        "picture": "",
    },
}


def check_client(body: dict) -> str | None:
    if body.get("client_id") != CLIENT_ID or body.get("client_secret") != CLIENT_SECRET:
        return "forbidden"
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        path = self.path.split("?")[0]
        if path == "/api/oauth/password/verify":
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            bad = check_client(body)
            if bad:
                self._send(403, {"detail": "password_verify_forbidden"})
                return
            user = USERS.get(str(body.get("email")))
            if user is None or body.get("password") != "right-pass":
                self._send(401, {"detail": "invalid_credentials"})
                return
            self._send(
                200,
                {
                    "ok": True,
                    "user": user,
                    "sso_session": SESSION,
                    "sso_cookie_name": "unisso_sid",
                    "sso_cookie_domain": ".ssemarket.cn",
                    "sso_max_age": 604800,
                },
            )
        elif path in ("/api/oauth/session/verify", "/api/oauth/session/revoke"):
            form = parse_qs(raw.decode())
            session = (form.get("session") or [""])[0]
            client = {
                "client_id": (form.get("client_id") or [""])[0],
                "client_secret": (form.get("client_secret") or [""])[0],
            }
            bad = check_client(client)
            if bad:
                self._send(403, {"detail": "forbidden"})
                return
            if session != SESSION:
                self._send(401, {"detail": "invalid_sso"})
                return
            if path.endswith("verify"):
                self._send(200, {"ok": True, "user": USERS["e2e@ssemarket.cn"]})
            else:
                self._send(200, {"ok": True})
        else:
            self._send(404, {"detail": "not_found"})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 13099), Handler)
    threading.current_thread().name = "mock-unisso"
    print("mock unisso on 13099")
    server.serve_forever()
