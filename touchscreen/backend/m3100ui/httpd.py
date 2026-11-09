"""HTTP server: static SPA files + JSON API, with the web-security rules of design §7."""
import email.utils
import gzip
import http.cookies
import ipaddress
import json
import logging
import mimetypes
import os
import socket
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from .auth import COOKIE
from .errors import ApiError

log = logging.getLogger("m3100ui.http")

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                               "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                               "form-action 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/manifest+json", ".webmanifest")


class Request:
    """What an API handler sees."""

    def __init__(self, app, handler: "Handler", method: str, path: str, query: Dict[str, str], body: bytes,
                 params: Dict[str, str], session):
        self.app = app
        self._h = handler
        self.method = method
        self.path = path
        self.query = query
        self.body = body
        self.params = params
        self.session = session
        self.client_ip = handler.client_address[0]
        self._json: Any = None
        self.response_headers: Dict[str, str] = {}
        self.set_cookie: Optional[str] = None

    def header(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self._h.headers.get(name, default)

    @property
    def json(self) -> Any:
        if self._json is None:
            if not self.body:
                self._json = {}
            else:
                try:
                    self._json = json.loads(self.body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise ApiError(400, "bad_json", "Request body is not valid JSON")
        return self._json

    def field(self, name: str, default: Any = None, required: bool = False) -> Any:
        data = self.json if isinstance(self.json, dict) else {}
        if required and name not in data:
            raise ApiError(400, "invalid_field", "Missing field: %s" % name, {"field": name})
        return data.get(name, default)


class Response:
    """Non-JSON response (file download, plain text)."""

    def __init__(self, body: bytes, content_type: str = "application/octet-stream", status: int = 200,
                 headers: Optional[Dict[str, str]] = None):
        self.body = body
        self.content_type = content_type
        self.status = status
        self.headers = headers or {}


class Handler(BaseHTTPRequestHandler):
    server_version = "router"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 60          # per-socket idle timeout: idle keep-alive/slow clients release their thread
    app = None  # set by make_server

    # -- plumbing ---------------------------------------------------------------------------
    def log_message(self, fmt, *args):  # quiet default logging; we log ourselves
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        if not host:
            return False
        if host.startswith("["):
            name = host[1:host.find("]")] if "]" in host else host
        else:
            name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        name = name.lower().rstrip(".")
        if name in ("localhost",) or name in [h.lower() for h in self.app.cfg.get("allowed_hosts", [])]:
            return True
        if name in self.app.extra_hosts():
            return True
        try:
            ipaddress.ip_address(name.split("%")[0])
            return True
        except ValueError:
            return False

    def _send(self, status: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None,
              cookie: Optional[str] = None) -> None:
        self.send_response(status)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, data: Any, extra=None, cookie=None) -> None:
        body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        extra = dict(extra or {})
        extra.setdefault("Cache-Control", "no-store")
        if len(body) > 1400 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body, 5)
            extra["Content-Encoding"] = "gzip"
            extra["Vary"] = "Accept-Encoding"
        self._send(status, body, "application/json; charset=utf-8", extra, cookie)

    def _cookie_sid(self) -> Optional[str]:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            c = http.cookies.SimpleCookie(raw)
        except http.cookies.CookieError:
            return None
        m = c.get(COOKIE)
        return m.value if m else None

    def _read_body(self, limit: int) -> bytes:
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if te == "chunked":
            raise ApiError(411, "length_required", "Chunked bodies are not supported")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad_length", "Invalid Content-Length")
        if n > limit:
            # drain a moderately oversized body so the client can read our 413 (no TCP reset)
            self.close_connection = True
            if n <= 16 * 1024 * 1024:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)
            raise ApiError(413, "too_large", "Request body too large")
        return self.rfile.read(n) if n > 0 else b""

    # -- dispatch ---------------------------------------------------------------------------
    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def _dispatch(self):
        t0 = time.time()
        status = 500
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        try:
            if not self._host_ok():
                status = 421
                self._send(421, b"Misdirected request", "text/plain")
                return
            if path.startswith("/api/"):
                status = self._api(path, url.query)
            else:
                status = self._static(path)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # last-resort guard; never leak tracebacks to the client
            log.error("unhandled error for %s %s\n%s", self.command, path, traceback.format_exc())
            try:
                self._send_json(500, {"error": {"code": "internal", "message": "Internal error"}})
            except OSError:
                pass
        finally:
            log.info("%s %s %s %d %.0fms", self.client_address[0], self.command, path, status, (time.time() - t0) * 1000)

    def _api(self, path: str, qs: str) -> int:
        app = self.app
        method = "GET" if self.command == "HEAD" else self.command
        try:
            route, params = app.router.match(method, path)
            body = b""
            if method not in ("GET", "HEAD"):
                body = self._read_body(app.cfg["max_upload_bytes"] if route.upload else app.cfg["max_body_bytes"])
            session = app.auth.session(self._cookie_sid())
            if route.auth:
                if not session:
                    raise ApiError(401, "unauthorized", "Login required")
                if method not in ("GET", "HEAD"):
                    app.auth.check_csrf(session, self.headers.get("X-CSRF-Token"))
                    origin = self.headers.get("Origin")
                    if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
                        raise ApiError(403, "bad_origin", "Cross-origin request refused")
            query = {k: v[-1] for k, v in urllib.parse.parse_qs(qs, keep_blank_values=True).items()}
            req = Request(app, self, method, path, query, body, params, session)
            result = route.handler(req)
            if isinstance(result, Response):
                extra = dict(result.headers)
                extra.setdefault("Cache-Control", "no-store")
                self._send(result.status, result.body, result.content_type, extra, req.set_cookie)
                return result.status
            status = 200 if result is not None else 204
            if status == 204:
                self._send(204, b"", "application/json", {"Cache-Control": "no-store"}, req.set_cookie)
            else:
                self._send_json(200, result, req.response_headers, req.set_cookie)
            return status
        except ApiError as e:
            extra = {}
            if e.status == 429 and isinstance(e.details, dict) and e.details.get("retry_after"):
                extra["Retry-After"] = str(e.details["retry_after"])
            self._send_json(e.status, e.to_json(), extra)
            return e.status

    # -- static files -----------------------------------------------------------------------
    _gz_cache: Dict[Tuple[str, float], bytes] = {}
    _gz_lock = threading.Lock()

    def _static(self, path: str) -> int:
        if self.command not in ("GET", "HEAD"):
            self._send(405, b"Method not allowed", "text/plain")
            return 405
        root = os.path.realpath(self.app.cfg["web_root"])
        rel = path.lstrip("/") or "index.html"
        full = os.path.realpath(os.path.join(root, rel))
        if not full.startswith(root + os.sep) and full != root:
            self._send(404, b"Not found", "text/plain")
            return 404
        if not os.path.isfile(full):
            # SPA fallback for client-side routes; real asset misses stay 404
            if "." in os.path.basename(rel):
                self._send(404, b"Not found", "text/plain")
                return 404
            full = os.path.join(root, "index.html")
        st = os.stat(full)
        etag = '"%x-%x"' % (int(st.st_mtime), st.st_size)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return 304
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml", "application/manifest+json"):
            ctype += "; charset=utf-8"
        extra = {"ETag": etag, "Cache-Control": "no-cache",
                 "Last-Modified": email.utils.formatdate(st.st_mtime, usegmt=True)}
        compressible = ctype.startswith(("text/", "application/json", "image/svg+xml"))
        if compressible and "gzip" in (self.headers.get("Accept-Encoding") or "") and st.st_size > 1024:
            key = (full, st.st_mtime)
            with self._gz_lock:
                body = self._gz_cache.get(key)
                if body is None:
                    with open(full, "rb") as f:
                        body = gzip.compress(f.read(), 6)
                    self._gz_cache[key] = body
            extra["Content-Encoding"] = "gzip"
            extra["Vary"] = "Accept-Encoding"
        else:
            with open(full, "rb") as f:
                body = f.read()
        self._send(200, body, ctype, extra)
        return 200


class DualStackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except (OSError, AttributeError):
                pass
        super().server_bind()


def make_server(app, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    if ":" in host:
        class V6(DualStackServer):
            address_family = socket.AF_INET6
        try:
            return V6((host, port), handler)
        except OSError as e:
            log.warning("IPv6 bind on %s:%d failed (%s); falling back to IPv4", host, port, e)
            host = "0.0.0.0"
    return DualStackServer((host, port), handler)
