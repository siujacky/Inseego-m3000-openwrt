"""HTTP-level security tests against a real server socket (mock mode)."""
import http.client
import json
import threading
import unittest

from tests.helpers import make_app

from m3100ui.httpd import make_server


class HttpdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = make_app()
        cls.srv = make_server(cls.app, "127.0.0.1", 0)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or "192.168.1.1"}
        h.update(headers or {})
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r, raw

    def login(self):
        r, raw = self.req("POST", "/api/login", {"password": self.app._password()})
        self.assertEqual(r.status, 200, raw)
        cookie = r.getheader("Set-Cookie").split(";")[0]
        self.assertIn("HttpOnly", r.getheader("Set-Cookie"))
        self.assertIn("SameSite=Strict", r.getheader("Set-Cookie"))
        return cookie, json.loads(raw)["csrf"]

    def test_security_headers(self):
        r, _ = self.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")

    def test_dns_rebinding_host_rejected(self):
        r, _ = self.req("GET", "/api/meta", host="evil.example.com")
        self.assertEqual(r.status, 421)
        r, _ = self.req("GET", "/api/meta", host="router.lan")
        self.assertEqual(r.status, 200)
        r, _ = self.req("GET", "/api/meta", host="[fe80::1]:8088")
        self.assertEqual(r.status, 200)

    def test_auth_and_csrf(self):
        r, _ = self.req("GET", "/api/status")
        self.assertEqual(r.status, 401)
        cookie, csrf = self.login()
        r, _ = self.req("GET", "/api/status", headers={"Cookie": cookie})
        self.assertEqual(r.status, 200)
        r, _ = self.req("PUT", "/api/settings", {"theme": "dark"}, headers={"Cookie": cookie})
        self.assertEqual(r.status, 403)                       # no CSRF token
        r, _ = self.req("PUT", "/api/settings", {"theme": "dark"},
                        headers={"Cookie": cookie, "X-CSRF-Token": csrf, "Origin": "http://evil.example.com"})
        self.assertEqual(r.status, 403)                       # cross-origin
        r, raw = self.req("PUT", "/api/settings", {"theme": "dark"},
                          headers={"Cookie": cookie, "X-CSRF-Token": csrf, "Origin": "http://192.168.1.1"})
        self.assertEqual(r.status, 200, raw)
        r, _ = self.req("POST", "/api/logout", {}, headers={"Cookie": cookie, "X-CSRF-Token": csrf})
        self.assertEqual(r.status, 200)
        r, _ = self.req("GET", "/api/status", headers={"Cookie": cookie})
        self.assertEqual(r.status, 401)

    def test_static_traversal_and_spa_fallback(self):
        for p in ("/../server/m3100ui/auth.py", "/app/../../server/m3100ui/auth.py", "/%2e%2e/%2e%2e/README.md"):
            r, raw = self.req("GET", p)
            self.assertIn(r.status, (404, 200), p)
            self.assertNotIn(b"compare_digest", raw, p)        # never serves files outside web/
        r, raw = self.req("GET", "/cellular/bands")            # client-side route -> index.html
        self.assertEqual(r.status, 200)
        self.assertIn(b"<div id=\"app\"", raw)
        r, _ = self.req("GET", "/app/does-not-exist.js")
        self.assertEqual(r.status, 404)

    def test_body_limits_and_bad_json(self):
        cookie, csrf = self.login()
        big = b"{" + b" " * (300 * 1024) + b"}"
        r, _ = self.req("PUT", "/api/settings", big, headers={"Cookie": cookie, "X-CSRF-Token": csrf})
        self.assertEqual(r.status, 413)
        r, _ = self.req("PUT", "/api/settings", b"{not json", headers={"Cookie": cookie, "X-CSRF-Token": csrf})
        self.assertEqual(r.status, 400)

    def test_unknown_api_and_method(self):
        r, _ = self.req("GET", "/api/nope")
        self.assertEqual(r.status, 404)
        r, _ = self.req("DELETE", "/api/meta")
        self.assertEqual(r.status, 405)


if __name__ == "__main__":
    unittest.main()
