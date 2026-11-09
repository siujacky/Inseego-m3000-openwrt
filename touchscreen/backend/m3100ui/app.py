"""Composition root: adapters + state + auth + routes, plus helpers every service uses."""
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from . import adapters as adapters_mod
from .auth import Auth
from .errors import ApiError
from .router import Router
from .state import StateStore

log = logging.getLogger("m3100ui")


class TTLCache:
    def __init__(self):
        self._d: Dict[str, tuple] = {}
        self._lock = threading.Lock()
        self._key_locks: Dict[str, threading.Lock] = {}

    def get(self, key: str, ttl: float, fn: Callable[[], Any]) -> Any:
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
            kl = self._key_locks.setdefault(key, threading.Lock())
        with kl:  # one producer per key; others wait and reuse
            with self._lock:
                hit = self._d.get(key)
                if hit and time.time() - hit[0] < ttl:
                    return hit[1]
            val = fn()
            with self._lock:
                self._d[key] = (time.time(), val)
            return val

    def invalidate(self, prefix: str = "") -> None:
        with self._lock:
            for k in list(self._d):
                if k.startswith(prefix):
                    self._d.pop(k, None)


class Features:
    """Feature availability for /api/meta: name -> probe() -> bool (cached)."""

    def __init__(self, cache: TTLCache):
        self.cache = cache
        self.probes: Dict[str, Callable[[], bool]] = {}

    def add(self, name: str, probe: Callable[[], bool]) -> None:
        self.probes[name] = probe

    def all(self) -> Dict[str, bool]:
        out = {}
        for name, probe in self.probes.items():
            def run(p=probe):
                try:
                    return bool(p())
                except Exception as e:  # a failing probe just hides the feature
                    log.debug("feature probe %s failed: %s", name, e)
                    return False
            out[name] = self.cache.get("feature:" + name, 120, run)
        return out


class App:
    def __init__(self, cfg, adapters: Optional[adapters_mod.Adapters] = None):
        self.cfg = cfg
        self.started = time.time()
        self.adapters = adapters or adapters_mod.build(cfg)
        self.cli = self.adapters.cli
        self.rest = self.adapters.rest
        self.at = self.adapters.at
        self.system = self.adapters.system
        self.mock = self.adapters.mock
        self.state = StateStore(cfg["state_dir"])
        self.cache = TTLCache()
        self.features = Features(self.cache)
        self.router = Router()
        self.route = self.router.route
        self.auth = Auth(self._password, self.state.settings,
                         hash_store=None if cfg.get("platform") == "openwrt" else self.state,
                         password_cache_s=0 if cfg.get("platform") == "openwrt" else 60)
        from . import services
        services.register_all(self)
        if not self.mock and hasattr(self.rest, "start_background") and cfg.get("vendor_keepalive", True):
            self.rest.start_background()
        if not self.mock and cfg.get("platform") != "openwrt" and cfg.get("selfheal", True):
            from . import selfheal
            selfheal.start(self)

    def extra_hosts(self):
        """Names the router answers to besides IP literals: its vendor DNS name and device name (+ .local).
        Cached; failures just return the last known set (DNS-rebinding guard stays closed)."""
        def load():
            names = set()
            if self.cfg.get("platform") == "openwrt":
                return names   # explicitly configured allowed_hosts; no privileged commands on public requests
            if self.mock:
                return names
            for cmd, label in (("get_dns_name", None), ("get_device_name", None)):
                r = self.cli_try("mifi_qcmap_cli", cmd, timeout=5)
                if not r:
                    continue
                for _k, v, _n in r.pairs:
                    v = (v or "").strip().lower().rstrip(".")
                    if v and all(c.isalnum() or c in "-." for c in v) and len(v) <= 63:
                        names.add(v)
                        if "." not in v:
                            names.add(v + ".local")
            return names
        try:
            return self.cache.get("extra_hosts", 300, load)
        except Exception:
            return set()

    def _password(self) -> str:
        if self.cfg.get("platform") == "openwrt":
            return self.adapters.native.password()
        if self.mock:
            return self.state.settings().get("mock_password") or self.cfg.get("mock_password", "admin")
        return adapters_mod.vendor_password(self.cli)

    # -- helpers for services ---------------------------------------------------------------
    def settings(self) -> Dict[str, Any]:
        return self.state.settings()

    def audit(self, req, action: str, details: Optional[Dict[str, Any]] = None) -> None:
        actor = req.client_ip if req is not None else "system"
        self.state.audit(actor, action, details)

    def require_tier(self, req, tier: int) -> None:
        """Enforce design §8 confirmations.  Body must carry confirm=true (tier>=2) and
        password=<admin password> (tier 3).  The UI shows the dialog on HTTP 428."""
        if tier < 2:
            return
        body = req.json if isinstance(req.json, dict) else {}
        if body.get("confirm") is not True:
            raise ApiError(428, "confirm_required", "Confirmation required", {"tier": tier})
        if tier >= 3:
            self.auth.reauth(body.get("password"), req.client_ip)

    def rest_try(self, path: str, default: Any = None) -> Any:
        """GET a vendor REST resource; unsupported/disabled resources return `default`."""
        try:
            return self.rest.get(path)
        except ApiError as e:
            if e.code in ("unsupported", "disabled", "vendor_unreachable", "vendor_error"):
                return default
            raise

    def cli_try(self, cli: str, command: str, answers=(), timeout: Optional[float] = None):
        """Run a read-only CLI command; returns CliResult or None when it failed."""
        try:
            r = self.cli.run(cli, command, answers, timeout=timeout)
        except ApiError:
            return None
        return r if r.ok else None
