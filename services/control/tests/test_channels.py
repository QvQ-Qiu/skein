import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from skein.channels import ForumWriteError, HttpWriter


class Handler(BaseHTTPRequestHandler):
    received: ClassVar[list] = []
    status = 200

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append((self.path, self.headers.get("X-Internal-Token"), body))
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"post_id": 7}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture()
def base_url():
    Handler.received = []
    Handler.status = 200
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_writer_posts_to_internal_endpoint(base_url):
    response = HttpWriter(base_url, "tok")("post", "day:2026-11-18", {"title": "x"})
    assert response == {"post_id": 7}
    path, token, body = Handler.received[0]
    assert path == "/internal/post"
    assert token == "tok"
    assert body == {"object": "day:2026-11-18", "payload": {"title": "x"}}


def test_writer_raises_on_server_error(base_url):
    Handler.status = 500
    with pytest.raises(ForumWriteError):
        HttpWriter(base_url, "tok")("post", "o", {})
