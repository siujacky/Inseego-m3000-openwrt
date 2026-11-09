"""Vendor REST API client (`/rest/1.0/*` served by the stock lighttpd + restapi.fcgi).

Authentication is the stock web login (reverse-engineered in ../m3000/mifi_web.py):
  GET /login/  -> page with gSecureToken (31 hex chars) and the `mifisess` cookie
  salt = "$2a$10$" + bcrypt_radix64(first 16 token chars as bytes)
  POST /submitLogin/  shaPassword=bcrypt(password, salt) & gSecureToken=<token>
The session cookie then authorises REST calls; writes are plain JSON PUT/POST/DELETE.
On the device bcrypt comes from libxcrypt via the `crypt` module; on a PC from the
`bcrypt` package (only needed for --transport adb/ssh development).
"""
import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional, Tuple

from ..errors import ApiError, vendor_error

_B64 = "./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
TOKEN_RE = re.compile(r'id="gSecureToken"[^>]*value="([0-9a-fA-F]+)"|gSecureToken\s*=\s*"([0-9a-fA-F]+)"')


def bcrypt_radix64(data: bytes) -> str:
    out = []
    i, n = 0, len(data)
    while i < n:
        c1 = data[i]
        i += 1
        out.append(_B64[(c1 >> 2) & 0x3F])
        c1 = (c1 & 0x03) << 4
        if i >= n:
            out.append(_B64[c1 & 0x3F])
            break
        c2 = data[i]
        i += 1
        c1 |= (c2 >> 4) & 0x0F
        out.append(_B64[c1 & 0x3F])
        c1 = (c2 & 0x0F) << 2
        if i >= n:
            out.append(_B64[c1 & 0x3F])
            break
        c2 = data[i]
        i += 1
        c1 |= (c2 >> 6) & 0x03
        out.append(_B64[c1 & 0x3F])
        out.append(_B64[c2 & 0x3F])
    return "".join(out)


def bcrypt_hash(password: str, salt: str) -> str:
    try:
        import crypt  # libxcrypt on the device supports $2a$
        h = crypt.crypt(password, salt)
        if h and h.startswith("$2"):
            return h
    except ImportError:
        pass
    try:
        import bcrypt  # type: ignore
        return bcrypt.hashpw(password.encode("utf-8"), salt.encode("ascii")).decode("ascii")
    except ImportError:
        raise ApiError(500, "no_bcrypt", "No bcrypt implementation available for the vendor login")


def login_hash(token: str, password: str) -> str:
    salt_bytes = bytes((ord(c) & 0xFF) for c in token[:16])
    return bcrypt_hash(password, "$2a$10$" + bcrypt_radix64(salt_bytes))


class VendorRest:
    def __init__(self, base: str, password_provider: Callable[[], str], timeout: float = 12.0):
        self.base = base.rstrip("/")
        self.password_provider = password_provider
        self.timeout = timeout
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))
        self._login_lock = threading.Lock()
        self._logged_in_at = 0.0

    # -- low level -------------------------------------------------------------------------
    def _raw(self, method: str, url: str, data: Optional[bytes] = None, headers: Optional[dict] = None
             ) -> Tuple[int, bytes]:
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read() if hasattr(e, "read") else b""
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(502, "vendor_unreachable", "Vendor web service unreachable: %s" % e)

    def login(self) -> None:
        with self._login_lock:
            if self._logged_in_at and time.time() - self._logged_in_at < 10:
                return  # another thread (or the keepalive) just logged in while we waited
            status, page = self._raw("GET", self.base + "/login/")
            m = TOKEN_RE.search(page.decode("utf-8", "replace"))
            if status != 200 or not m:
                raise vendor_error("Vendor login page not available (HTTP %s)" % status)
            token = m.group(1) or m.group(2)
            body = urllib.parse.urlencode({"shaPassword": login_hash(token, self.password_provider()),
                                           "gSecureToken": token}).encode()
            self._raw("POST", self.base + "/submitLogin/", body, {
                "X-Requested-With": "XMLHttpRequest", "Referer": self.base + "/login/",
                "Content-Type": "application/x-www-form-urlencoded"})
            # verify with a cheap authenticated REST call (rendering the vendor home page takes seconds)
            status, _body = self._raw("GET", self.base + "/rest/1.0/SimStatus", None, {"Accept": "application/json"})
            if status == 401:
                raise vendor_error("Vendor login rejected (admin password changed outside this UI?)")
            self._logged_in_at = time.time()

    # -- keep one vendor session warm ----------------------------------------------------------
    def start_background(self, interval: float = 240.0) -> None:
        """Log in at start-up and ping every `interval` s (vendor idle logout is 600 s), so no user
        request ever waits for the ~8 s vendor login."""
        def loop():
            while True:
                try:
                    if not self._logged_in_at:
                        self.login()
                    else:
                        status, _b = self._raw("GET", self.base + "/rest/1.0/SMSStatus", None, {"Accept": "application/json"})
                        if status == 401:
                            self._logged_in_at = 0.0
                            self.login()
                except Exception:  # vendor stack restarting, password changed, … retry next round
                    self._logged_in_at = 0.0
                time.sleep(interval if self._logged_in_at else 30.0)

        t = threading.Thread(target=loop, name="vendor-rest-keepalive", daemon=True)
        t.start()

    # -- REST --------------------------------------------------------------------------------
    def request(self, method: str, path: str, body: Any = None, _retry: bool = True) -> Any:
        if not self._logged_in_at:
            self.login()
        url = self.base + "/rest/1.0/" + path.lstrip("/")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        status, raw = self._raw(method, url, data, {"Content-Type": "application/json", "Accept": "application/json"})
        if status == 401 and _retry:
            self._logged_in_at = 0.0
            self.login()
            return self.request(method, path, body, _retry=False)
        text = raw.decode("utf-8", "replace").strip()
        parsed: Any = None
        if text:
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = text
        if status >= 400:
            msg = parsed.get("Error") if isinstance(parsed, dict) else (parsed or "HTTP %d" % status)
            code = {404: "unsupported", 501: "unsupported", 403: "disabled"}.get(status, "vendor_error")
            raise ApiError(502 if code == "vendor_error" else 503, code, "Vendor REST %s %s: %s" % (method, path, msg),
                           {"vendor_status": status, "resource": path})
        return parsed

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def put(self, path: str, body: Any) -> Any:
        return self.request("PUT", path, body)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body)

    def delete(self, path: str, body: Any = None) -> Any:
        return self.request("DELETE", path, body)

    def merge_put(self, path: str, changes: dict) -> Any:
        """GET the resource, overlay `changes`, PUT the full object back, return the fresh GET."""
        cur = self.get(path)
        if not isinstance(cur, dict):
            raise vendor_error("Unexpected vendor response for %s" % path)
        unknown = [k for k in changes if k not in cur]
        if unknown:
            raise ApiError(400, "invalid_field", "Unknown field(s) for %s: %s" % (path, ", ".join(unknown)),
                           {"field": unknown[0]})
        new = dict(cur)
        new.update(changes)
        self.put(path, new)
        return self.get(path)
