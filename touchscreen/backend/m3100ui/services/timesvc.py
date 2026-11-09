"""Time: router clock and time zone, mobile-network time, the vendor NTP servers, and "sync now" /
sync at start-up / periodic sync with busybox ntpd (the 8800L mod ROM runs `ntpd -n -q -p pool.ntp.org`
after the web server starts).

- ccm2_cli get/set_ntp_url: the connection manager's NTP servers, minimum delay between syncs, retries.
- modem2_cli get_network_time: NITZ time from the mobile network (stale without a SIM).
- Sync settings live in <state_dir>/network_time.json (app.state), not in vendor settings.
"""
import ipaddress
import re
import threading
import time
from typing import Any, Dict

from .. import validate
from ..errors import ApiError, field_error, unavailable

HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                     r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
STATE_FILE = "network_time.json"
DEFAULTS = {"sync_at_boot": False, "interval_h": 0, "server": "pool.ntp.org", "last": None}


def ntp_host(field: str, v: Any, allow_empty: bool = False) -> str:
    """Strict host check: an IP literal or a DNS name; nothing that could look like an option."""
    s = "" if v is None else str(v).strip()
    if not s:
        if allow_empty:
            return ""
        raise field_error(field, "Enter a time server name or address")
    try:
        a = ipaddress.ip_address(s)
        if a.is_multicast or a.is_unspecified:
            raise field_error(field, "This address cannot be a time server")
        return str(a)
    except ValueError:
        pass
    if s.startswith("-") or not HOST_RE.match(s) or len(s) > 127:
        raise field_error(field, "Enter a host name such as pool.ntp.org")
    return s


def parse_ntp(res) -> Dict[str, Any]:
    if res is None:
        return {}
    return {"servers": [res.get("url1", ""), res.get("url2", ""), res.get("url3", "")],
            "min_delay_s": res.get_int("min_delay"), "retries": res.get_int("retries")}


def parse_network_time(res) -> Dict[str, Any]:
    """modem2_cli get_network_time: Time:[..] Timestamp:[..] Time zone:[15-min units] DST:[0|1]."""
    if res is None:
        return {"available": False}
    ts = res.get_int("Timestamp")
    tzq = res.get_int("Time zone")
    return {"available": ts is not None, "text": res.get("Time", ""), "timestamp": ts,
            "tz_offset_min": tzq * 15 if tzq is not None else None, "dst": bool(res.get_int("DST", 0))}


def register(app):
    r = app.router
    lock = threading.Lock()

    def settings() -> Dict[str, Any]:
        s = dict(DEFAULTS)
        s.update(app.state.read_json(STATE_FILE, {}) or {})
        return s

    def save(ch: Dict[str, Any]) -> Dict[str, Any]:
        s = settings()
        s.update(ch)
        app.state.write_json(STATE_FILE, s)
        return s

    def do_sync(server: str, reason: str) -> Dict[str, Any]:
        host = ntp_host("server", server)
        if not lock.acquire(blocking=False):
            raise ApiError(409, "conflict", "A time synchronisation is already running")
        try:
            before = time.time()
            t0 = time.monotonic()
            res = app.system.run(["ntpd", "-n", "-q", "-p", host], timeout=25)
            if not res.get("ok") and "not available" in str(res.get("output", "")):
                res = app.system.run(["busybox", "ntpd", "-n", "-q", "-p", host], timeout=25)
            elapsed = time.monotonic() - t0
            after = time.time()
            last = {"t": int(after), "ok": bool(res.get("ok")), "server": host, "reason": reason,
                    "timed_out": bool(res.get("timed_out")),
                    "clock_change_s": round(after - before - elapsed, 1),
                    "output": str(res.get("output", ""))[-2000:]}
            save({"last": last})
            return last
        finally:
            lock.release()

    def state() -> Dict[str, Any]:
        now = time.time()
        lt = time.localtime(now)
        off = -time.altzone if (lt.tm_isdst > 0 and time.daylight) else -time.timezone
        ntp = parse_ntp(app.cli_try("ccm2_cli", "get_ntp_url"))
        return {
            "now": now, "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
            "local": time.strftime("%Y-%m-%d %H:%M:%S", lt), "timezone": time.strftime("%Z", lt) or "UTC",
            "tz_offset_s": off, "uptime_s": int(app.system.uptime()),
            "clock_plausible": now > 1700000000,
            "ntp": ntp or None,
            "network_time": parse_network_time(app.cli_try("modem2_cli", "get_network_time")),
            "sync": settings(),
        }

    @r.route("GET", "/api/time")
    def get_time(req):
        """Router clock, time zone, mobile-network time, NTP servers and sync settings."""
        return state()

    @r.route("PUT", "/api/time/ntp")
    def put_ntp(req):
        """The vendor connection manager's NTP servers and timing."""
        data = validate.only_keys(req.json, ["servers", "min_delay_s", "retries"])
        cur = parse_ntp(app.cli_try("ccm2_cli", "get_ntp_url"))
        if not cur:
            raise unavailable("The NTP settings are not available right now")
        servers = data.get("servers", cur["servers"])
        if not isinstance(servers, list) or len(servers) != 3:
            raise field_error("servers", "Enter three server slots (the last two may be empty)")
        servers = [ntp_host("servers", servers[0])] + [ntp_host("servers", s, allow_empty=True) for s in servers[1:]]
        delay = validate.integer("min_delay_s", data.get("min_delay_s", cur.get("min_delay_s") or 604800), 60, 2592000)
        retries = validate.integer("retries", data.get("retries", cur.get("retries") or 60), 0, 1000)
        res = app.cli.run("ccm2_cli", "set_ntp_url", [delay, retries] + servers)
        res.raise_for_error("Saving the NTP servers")
        app.audit(req, "time.ntp", {"keys": sorted(data)})
        return state()

    @r.route("PUT", "/api/time/sync")
    def put_sync(req):
        """Sync at start-up / every N hours and the server used for it (stored by this UI)."""
        data = validate.only_keys(req.json, ["sync_at_boot", "interval_h", "server"])
        ch: Dict[str, Any] = {}
        if "sync_at_boot" in data:
            ch["sync_at_boot"] = validate.boolean("sync_at_boot", data["sync_at_boot"])
        if "interval_h" in data:
            ch["interval_h"] = validate.integer("interval_h", data["interval_h"], 0, 168)
        if "server" in data:
            ch["server"] = ntp_host("server", data["server"])
        save(ch)
        app.audit(req, "time.sync_settings", {"keys": sorted(ch)})
        return state()

    @r.route("POST", "/api/time/sync")
    def sync_now(req):
        """Set the clock now from an NTP server (busybox ntpd -n -q -p SERVER)."""
        data = validate.only_keys(req.json, ["server"])
        server = data.get("server") or settings()["server"]
        last = do_sync(server, "manual")
        app.audit(req, "time.sync", {"server": last["server"], "ok": last["ok"]})
        out = state()
        out["result"] = last
        return out

    def scheduler():
        time.sleep(60)            # let the WAN come up after a restart
        try:
            st = settings()
            if st.get("sync_at_boot"):
                do_sync(st.get("server") or "pool.ntp.org", "start-up")
        except Exception:         # never let the scheduler die on a bad setting / busy lock
            pass
        while True:
            time.sleep(300)
            try:
                st = settings()
                hours = int(st.get("interval_h") or 0)
                last = st.get("last") or {}
                if hours > 0 and time.time() - float(last.get("t") or 0) >= hours * 3600:
                    do_sync(st.get("server") or "pool.ntp.org", "periodic")
            except Exception:
                pass

    if not app.mock and not app.cfg.get("disable_background_jobs"):
        t = threading.Thread(target=scheduler, name="m3ui-ntp", daemon=True)
        t.start()
