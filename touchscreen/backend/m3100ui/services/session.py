"""Login / logout / session info."""
from ..auth import COOKIE
from ..errors import field_error


def _cookie(sid: str, max_age: int) -> str:
    return "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (COOKIE, sid, max_age)


def register(app):
    r = app.router

    def info(session):
        st = app.settings()
        return {"authenticated": bool(session), "csrf": session.csrf if session else None,
                "router_name": st["router_name"], "expert_mode": bool(st["expert_mode"]),
                "idle_timeout_s": int(st["session_idle_s"]), "mock": app.mock}

    @r.route("GET", "/api/session", auth=False)
    def get_session(req):
        """Who am I (public)."""
        return info(req.session)

    @r.route("POST", "/api/login", auth=False)
    def login(req):
        """Log in with the device admin password."""
        pw = req.field("password", "")
        if not isinstance(pw, str) or not pw:
            raise field_error("password", "Enter the admin password")
        s = app.auth.login(pw, req.client_ip)
        req.set_cookie = _cookie(s.sid, int(app.settings()["session_max_s"]))
        app.audit(req, "login")
        return info(s)

    @r.route("POST", "/api/logout")
    def logout(req):
        """End this session."""
        app.auth.logout(req.session.sid)
        req.set_cookie = _cookie("", 0)
        return {"ok": True}
