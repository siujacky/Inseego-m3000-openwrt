"""Repair the vendor web/login stack when it fails to come up (device mode only).

Observed on unit 76fe1623 (2026-10-31): after an abrupt reset, tz_securefsd (TrustZone secure
settings, holds the admin login settings) was never started although online.target was active.
webuid then exits ("settings_get_setting(webui.authentication.settings) failed"), the stock UI is
broken and `webui_cli get_login_settings` answers "process not running" — so nobody can log in.
Starting tz_securefsd and restarting nvtl_webserver.service fixes it.

This thread waits until the system has settled, then checks the vendor login store; on that exact
failure it starts tz_securefsd (if inactive) and restarts the vendor web server (if webuid is not
running).  At most MAX_REPAIRS per boot; every repair is logged and audited.
"""
import logging
import threading
import time

log = logging.getLogger("m3100ui.selfheal")
MAX_REPAIRS = 3
SETTLE_UPTIME_S = 120


def _vendor_login_ok(app):
    try:
        r = app.cli.run("webui_cli", "get_login_settings", timeout=10)
    except Exception as e:  # runner/policy errors: do not try to repair anything
        log.debug("selfheal check failed to run: %s", e)
        return True, ""
    if r.ok:
        return True, ""
    return False, (r.message or "").lower()


def _active(app, unit):
    return app.system.systemctl("is-active", unit).get("output", "").strip() == "active"


def _running(app, name):
    res = app.system.run(["pidof", name], timeout=5)
    return bool(res.get("output", "").strip())


def check_and_repair(app):
    """One check; returns a dict describing what was done (empty when healthy)."""
    ok, msg = _vendor_login_ok(app)
    if ok or "process not running" not in msg:
        return {}
    done = {}
    if not _active(app, "tz_securefsd.service"):
        app.system.systemctl("start", "tz_securefsd.service")
        done["tz_securefsd"] = "started"
        time.sleep(3)
    if not _running(app, "webuid"):
        app.system.systemctl("restart", "nvtl_webserver.service")
        done["nvtl_webserver"] = "restarted"
        time.sleep(5)
    done["healthy_after"] = _vendor_login_ok(app)[0]
    app.state.audit("system", "selfheal.vendor_web", done)
    log.warning("repaired vendor web/login stack: %s", done)
    return done


def start(app):
    if app.mock:
        return

    def loop():
        repairs = 0
        while True:
            try:
                up = app.system.uptime()
                if up < SETTLE_UPTIME_S:
                    time.sleep(max(5.0, SETTLE_UPTIME_S - up))
                    continue
                if repairs < MAX_REPAIRS:
                    if check_and_repair(app):
                        repairs += 1
                        app.auth.forget_password_cache()
            except Exception as e:  # never let the healer crash the service
                log.warning("selfheal round failed: %s", e)
            time.sleep(300)

    threading.Thread(target=loop, name="m3ui-selfheal", daemon=True).start()
