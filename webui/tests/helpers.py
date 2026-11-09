"""Test helpers: an App in mock mode with a temp state dir, and an HTTP-free API client.

    from tests.helpers import MockClient
    c = MockClient()            # logged in, CSRF handled
    data = c.get("/api/status")
    c.put("/api/wifi/primary", {"ssid": "x"}, expect=400)
    c.app.cli.store.calls       # every vendor setter call made (FakeCli)
    c.app.rest.calls            # every vendor REST write (FakeRest)

Run all tests:  python -m unittest discover -s tests -t .
(also with Python 3.8:  uv run --python 3.8 python -m unittest discover -s tests -t .)
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

from m3100ui import config as config_mod  # noqa: E402
from m3100ui.app import App  # noqa: E402
from m3100ui.errors import ApiError  # noqa: E402
from m3100ui.httpd import Request  # noqa: E402

FIXTURES = os.path.join(ROOT, "fixtures")


class _FakeHandler:
    """Just enough of BaseHTTPRequestHandler for Request objects."""

    def __init__(self, headers=None, ip="192.168.1.100"):
        self.headers = headers or {}
        self.client_address = (ip, 50000)


def make_app(**overrides):
    state = tempfile.mkdtemp(prefix="m3ui-test-")
    cfg = config_mod.load(None, dict({"mock": True, "state_dir": state, "fixtures_dir": FIXTURES,
                                      "web_root": os.path.join(ROOT, "web")}, **overrides))
    app = App(cfg)
    app._test_state_dir = state
    return app


class MockClient:
    """Calls route handlers directly (no sockets) with the same auth/CSRF rules as httpd."""

    def __init__(self, app=None, login=True, ip="192.168.1.100"):
        self.app = app or make_app()
        self.ip = ip
        self.session = None
        if login:
            self.session = self.app.auth.login(self.app._password(), ip)

    def call(self, method, path, body=None, expect=200, query=None, headers=None):
        method = method.upper()
        try:
            route, params = self.app.router.match(method, path)
            raw = json.dumps(body).encode() if body is not None else b""
            sess = self.session if self.session and self.app.auth.session(self.session.sid) else None
            if route.auth:
                if not sess:
                    raise ApiError(401, "unauthorized", "Login required")
            req = Request(self.app, _FakeHandler(headers or {}, self.ip), method, path, query or {}, raw, params, sess)
            result = route.handler(req)
            status = 200 if result is not None else 204
        except ApiError as e:
            status, result = e.status, e.to_json()
        if expect is not None and status != expect:
            raise AssertionError("%s %s -> %s (expected %s): %s" % (method, path, status, expect, json.dumps(result)[:800]))
        if hasattr(result, "body") and hasattr(result, "content_type"):
            return result
        return result

    def get(self, path, expect=200, query=None):
        return self.call("GET", path, None, expect, query)

    def put(self, path, body, expect=200):
        return self.call("PUT", path, body, expect)

    def post(self, path, body=None, expect=200):
        return self.call("POST", path, body if body is not None else {}, expect)

    def delete(self, path, body=None, expect=200):
        return self.call("DELETE", path, body, expect)

    def confirm(self, body=None, tier=2):
        """Body with the confirmation fields a tier-2/3 endpoint requires."""
        b = dict(body or {})
        b["confirm"] = True
        if tier >= 3:
            b["password"] = self.app._password()
        return b

    def cleanup(self):
        shutil.rmtree(getattr(self.app, "_test_state_dir", ""), ignore_errors=True)


def read_fixture(cli, cmd):
    with open(os.path.join(FIXTURES, "cli", cli, cmd + ".txt"), "r", encoding="utf-8") as f:
        return f.read()
