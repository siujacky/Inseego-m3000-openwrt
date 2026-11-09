"""Meta information, UI settings, route listing."""
from .. import __version__, validate
from ..errors import field_error


def _problems(app):
    from ..adapters import cli_specs
    from .. import mock_handlers
    out = {}
    out.update({"service " + k: v for k, v in (getattr(app, "failed_modules", {}) or {}).items()})
    out.update({"spec " + k: v for k, v in cli_specs.SPEC_ERRORS.items()})
    if app.mock:
        out.update({"mock " + k: v for k, v in mock_handlers.FAILED.items()})
    return out


def register(app):
    r = app.router

    @r.route("GET", "/api/meta", auth=False)
    def meta(req):
        """Public identity for the login page (no identifiers, no versions)."""
        return {"name": app.settings()["router_name"], "product": "5G Router", "ui_version": __version__,
                "platform": app.cfg.get("platform", "stock")}

    @r.route("GET", "/api/meta/full")
    def meta_full(req):
        """Everything the UI needs at start-up: features, versions, settings."""
        info = app.cache.get("meta:deviceinfo", 300, lambda: app.rest_try("DeviceInfo", {}) or {})
        return {
            "name": app.settings()["router_name"],
            "ui_version": __version__,
            "mock": app.mock,
            "platform": app.cfg.get("platform", "stock"),
            "model": info.get("DeviceInfoModelNumber", "M3100"),
            "firmware": info.get("DeviceInfoSwVersion", ""),
            "modem_firmware": info.get("DeviceInfoModemFwVersion", ""),
            "wifi_firmware": info.get("DeviceInfoWiFiFwVesion", ""),
            "hardware": info.get("DeviceInfoHwVersion", ""),
            "features": app.features.all(),
            "settings": app.settings(),
            "problems": _problems(app),
        }

    @r.route("GET", "/api/meta/routes")
    def routes(req):
        """All API endpoints (for the expert API explorer)."""
        return app.router.describe()

    @r.route("GET", "/api/settings")
    def get_settings(req):
        """UI settings."""
        return app.settings()

    @r.route("PUT", "/api/settings")
    def put_settings(req):
        """Change UI settings (router name, expert mode, session timeout, theme, poll interval)."""
        data = validate.only_keys(req.json, ["router_name", "expert_mode", "session_idle_s", "theme", "status_poll_s"])
        ch = {}
        if "router_name" in data:
            ch["router_name"] = validate.text("router_name", data["router_name"], 1, 32).strip()
            if not ch["router_name"]:
                raise field_error("router_name", "Enter a name")
        if "expert_mode" in data:
            ch["expert_mode"] = validate.boolean("expert_mode", data["expert_mode"])
        if "session_idle_s" in data:
            ch["session_idle_s"] = validate.integer("session_idle_s", data["session_idle_s"], 120, 86400)
        if "theme" in data:
            ch["theme"] = validate.choice("theme", data["theme"], ["system", "light", "dark"])
        if "status_poll_s" in data:
            ch["status_poll_s"] = validate.integer("status_poll_s", data["status_poll_s"], 2, 60)
        s = app.state.update_settings(ch)
        app.audit(req, "settings.update", {"keys": sorted(ch)})
        return s
