"""Login, sessions, CSRF, throttling, re-authentication for tier-3 actions.

Password = the device admin password (same as the stock UI); see design §7.
Sessions are in memory only: restarting the service logs everybody out.
"""
import hashlib
import hmac
import secrets
import threading
import time
from typing import Callable, Dict, Optional

from .errors import ApiError, too_many, unauthorized

COOKIE = "m3ui_sid"


class Session:
    # created/last: wall clock for display; m_created/m_last: monotonic, used for expiry
    __slots__ = ("sid", "csrf", "ip", "created", "last", "m_created", "m_last")

    def __init__(self, ip: str):
        self.sid = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(24)
        self.ip = ip
        self.created = time.time()
        self.last = self.created
        self.m_created = time.monotonic()
        self.m_last = self.m_created

    def touch(self) -> None:
        self.last = time.time()
        self.m_last = time.monotonic()


class Auth:
    MAX_SESSIONS = 16

    def __init__(self, password_provider: Callable[[], str], settings: Callable[[], dict], hash_store=None,
                 password_cache_s=60):
        self.password_provider = password_provider
        self.settings = settings
        self.hash_store = hash_store          # StateStore for the fallback hash (None = disabled)
        self.password_cache_s = password_cache_s
        self.vendor_unavailable = False
        self.sessions: Dict[str, Session] = {}
        self.failures: Dict[str, list] = {}   # ip -> [count, last_fail_ts, locked_until]
        self.lock = threading.Lock()
        self._pw_cache: Optional[tuple] = None  # (password, fetched_at)

    # -- password ---------------------------------------------------------------------------
    def _password(self) -> str:
        c = self._pw_cache
        if c and time.time() - c[1] < self.password_cache_s:
            return c[0]
        pw = self.password_provider()
        self._pw_cache = (pw, time.time())
        return pw

    def forget_password_cache(self) -> None:
        self._pw_cache = None

    # A salted PBKDF2 hash of the last password the vendor store confirmed lets people log in while
    # the vendor store is down (tz_securefsd/webuid failing after an abrupt reset was observed).
    PBKDF2_ROUNDS = 20000

    def _remember(self, password: str) -> None:
        if not self.hash_store:
            return
        cur = self.hash_store.read_json("auth_fallback.json", {}) or {}
        salt = bytes.fromhex(cur["salt"]) if cur.get("salt") else secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self.PBKDF2_ROUNDS).hex()
        if cur.get("hash") != digest:
            self.hash_store.write_json("auth_fallback.json", {"salt": salt.hex(), "hash": digest,
                                                              "rounds": self.PBKDF2_ROUNDS, "t": int(time.time())})

    def _fallback_ok(self, password: str) -> bool:
        cur = (self.hash_store.read_json("auth_fallback.json", {}) if self.hash_store else None) or {}
        if not cur.get("hash") or not cur.get("salt"):
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(cur["salt"]),
                                     int(cur.get("rounds", self.PBKDF2_ROUNDS))).hex()
        return hmac.compare_digest(digest, cur["hash"])

    def check_password(self, password: str) -> bool:
        from .errors import ApiError as _ApiError
        try:
            expected = self._password()
        except _ApiError:
            # vendor password store unavailable: accept the last confirmed password, if any
            self.vendor_unavailable = True
            if self._fallback_ok(password):
                return True
            raise
        self.vendor_unavailable = False
        ok = hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8"))
        if ok:
            try:
                self._remember(password)
            except OSError:
                pass
        return ok

    # -- throttling -------------------------------------------------------------------------
    def _throttle_check(self, ip: str) -> None:
        f = self.failures.get(ip)
        if not f:
            return
        now = time.time()
        count, last, locked_until = f
        if locked_until > now:
            wait = max(1, int(locked_until - now))
            raise too_many("Too many failed logins. Try again in %d s" % wait, wait)
        if count >= 3:
            delay = min(30, 2 ** (count - 3))
            if now - last < delay:
                raise too_many("Wait %d s before the next attempt" % int(delay - (now - last) + 1),
                               int(delay - (now - last)) + 1)

    def _fail(self, ip: str) -> None:
        now = time.time()
        f = self.failures.setdefault(ip, [0, 0.0, 0.0])
        f[0] += 1
        f[1] = now
        if f[0] >= 10:
            f[2] = now + 300
            f[0] = 0

    # -- sessions ---------------------------------------------------------------------------
    def login(self, password: str, ip: str) -> Session:
        with self.lock:
            self._throttle_check(ip)
        ok = bool(password) and self.check_password(password)
        with self.lock:
            if not ok:
                self._fail(ip)
                raise ApiError(401, "bad_password", "Wrong password")
            self.failures.pop(ip, None)
            self._expire()
            if len(self.sessions) >= self.MAX_SESSIONS:
                oldest = min(self.sessions.values(), key=lambda s: s.m_last)
                self.sessions.pop(oldest.sid, None)
            s = Session(ip)
            self.sessions[s.sid] = s
            return s

    def logout(self, sid: str) -> None:
        with self.lock:
            self.sessions.pop(sid, None)

    def logout_all(self, except_sid: Optional[str] = None) -> None:
        with self.lock:
            for sid in list(self.sessions):
                if sid != except_sid:
                    self.sessions.pop(sid, None)

    def _expire(self) -> None:
        st = self.settings()
        idle, absolute = float(st.get("session_idle_s", 1800)), float(st.get("session_max_s", 43200))
        now = time.monotonic()
        for sid, s in list(self.sessions.items()):
            if now - s.m_last > idle or now - s.m_created > absolute:
                self.sessions.pop(sid, None)

    def session(self, sid: Optional[str], touch: bool = True) -> Optional[Session]:
        if not sid:
            return None
        with self.lock:
            self._expire()
            s = self.sessions.get(sid)
            if s and touch:
                s.touch()
            return s

    def require(self, sid: Optional[str]) -> Session:
        s = self.session(sid)
        if not s:
            raise unauthorized("Your session has expired. Please log in again.")
        return s

    @staticmethod
    def check_csrf(session: Session, token: Optional[str]) -> None:
        if not token or not hmac.compare_digest(token.encode(), session.csrf.encode()):
            raise ApiError(403, "csrf", "Missing or invalid CSRF token")

    def reauth(self, password: Optional[str], ip: str) -> None:
        """Tier-3 actions: the admin password must be entered again."""
        with self.lock:
            self._throttle_check(ip)
        if not password or not self.check_password(password):
            with self.lock:
                self._fail(ip)
            raise ApiError(403, "reauth_failed", "Password confirmation failed", {"field": "password"})
