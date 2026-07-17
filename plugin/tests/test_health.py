from __future__ import annotations

import http.server
import threading

from hostpanel_nodejs import health


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, *args):
        pass


def _serve(status: int):
    _Handler.status = status
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_healthy_endpoint_returns_none():
    server = _serve(200)
    try:
        assert health.wait_healthy(server.server_port, "/healthz", 5, 1) is None
    finally:
        server.shutdown()


def test_500_reported_at_deadline():
    server = _serve(500)
    try:
        result = health.wait_healthy(server.server_port, "/healthz", 1, 1)
        assert result is not None and "500" in result
    finally:
        server.shutdown()


def test_connection_refused_reported():
    # Port 1 on loopback: nothing listens there.
    result = health.wait_healthy(1, "/", 1, 1)
    assert result is not None
