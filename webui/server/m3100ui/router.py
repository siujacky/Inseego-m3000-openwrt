"""Tiny route registry.

    @app.route("GET", "/api/wifi/{band}")            # auth required by default
    def wifi(req): return {...}

Handlers receive a Request and return JSON-serialisable data (or a Response).
Options: auth=False for public endpoints, tier=N (design §8) for audit/confirmation,
upload=True to allow bodies up to max_upload_bytes.
"""
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .errors import ApiError

_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class Route:
    def __init__(self, method: str, pattern: str, handler: Callable, auth: bool, tier: int, upload: bool,
                 summary: str):
        self.method = method
        self.pattern = pattern
        self.handler = handler
        self.auth = auth
        self.tier = tier
        self.upload = upload
        self.summary = summary
        rx = "^" + _PARAM_RE.sub(lambda m: "(?P<%s>[^/]+)" % m.group(1), re.escape(pattern).replace(r"\{", "{").replace(r"\}", "}")) + "$"
        self.regex = re.compile(rx)


class Router:
    def __init__(self):
        self.routes: List[Route] = []

    def add(self, method: str, pattern: str, handler: Callable, auth: bool = True, tier: int = 0,
            upload: bool = False, summary: str = "") -> Route:
        method = method.upper()
        for r in self.routes:
            if r.method == method and r.pattern == pattern:
                raise ValueError("duplicate route %s %s" % (method, pattern))
        r = Route(method, pattern, handler, auth, tier, upload, summary or (handler.__doc__ or "").strip().split("\n")[0])
        self.routes.append(r)
        return r

    def route(self, method: str, pattern: str, **kw) -> Callable:
        def deco(fn: Callable) -> Callable:
            self.add(method, pattern, fn, **kw)
            return fn
        return deco

    def match(self, method: str, path: str) -> Tuple[Route, Dict[str, str]]:
        allowed = []
        for r in self.routes:
            m = r.regex.match(path)
            if m:
                if r.method == method:
                    return r, m.groupdict()
                allowed.append(r.method)
        if allowed:
            raise ApiError(405, "method_not_allowed", "Method %s not allowed" % method, {"allowed": sorted(set(allowed))})
        raise ApiError(404, "not_found", "No such API endpoint: %s" % path)

    def describe(self) -> List[Dict[str, Any]]:
        return [{"method": r.method, "path": r.pattern, "auth": r.auth, "tier": r.tier, "summary": r.summary}
                for r in sorted(self.routes, key=lambda r: (r.pattern, r.method))]
