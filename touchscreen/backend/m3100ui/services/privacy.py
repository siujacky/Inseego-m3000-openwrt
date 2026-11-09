"""Privacy & vendor services (features of the third-party mod ROMs, docs/recon/rom-mods.md):

- vendor cloud / telemetry / carrier-management daemons: status (systemctl is-active / is-enabled) and
  "turn off" / "turn on" (tier 2).  Turning off = a systemd drop-in that makes the unit's start condition
  fail (works although the vendor units live in /etc/systemd/system, where `systemctl mask` refuses) +
  daemon-reload + stop + disable.  Turning on removes exactly that drop-in and restores the previous
  enable state.  The previous state is kept in <state_dir>/privacy.json.
- "Keep vendor logs in RAM": mounts a size-limited tmpfs over /opt/nvtl/log (the mods replaced the
  directory with a symlink to /dev/shm; a mount is reversible and deletes nothing).  Idempotent: only
  mounts when nothing is mounted there, only unmounts its own mount (source name m3ui-vendor-log).
- Health-log upload to the vendor (mifi_health_cli get/set_log_settings).

In --mock nothing touches the PC: systemd and mount state are simulated per App instance.
"""
import os
import threading
import time
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import ApiError, field_error, not_found, unavailable, vendor_error
from .device import b01, flag, run_setter

LOG_DIR = "/opt/nvtl/log"
LOG_MOUNT_SOURCE = "m3ui-vendor-log"
LOG_MOUNT_SIZE = "16m"
DROPIN_NAME = "50-m3ui-privacy.conf"
DROPIN_TEXT = ("# Written by the router web UI (Services > Privacy & vendor services).\n"
               "# Delete this file and run `systemctl daemon-reload` to let the service start again.\n"
               "[Unit]\nConditionPathExists=/m3ui-service-turned-off-by-web-ui\n")

# name -> (systemd units, label, what it does, what stops working, watchdog app names)
VENDOR_SERVICES = [
    ("cloud_engined", ["cloud_engined.service"], "Cloud engine",
     "Vendor cloud connection used for remote management, file transfers and diagnostic uploads.",
     "Remote management from the vendor cloud and diagnostic uploads.", ["cloud_engine_main_thread"]),
    ("deviceagentd", ["deviceagentd.service"], "Device management agent",
     "Keeps an MQTT session to the vendor management portal (check-ins, heartbeat, scheduled restarts).",
     "The vendor management portal and restarts scheduled from it.", []),
    ("agentmd", ["agentmd.service"], "Agent manager",
     "Starts the vendor management agents and uploads their files.", "Vendor management agents.", []),
    ("longshipd", ["longshipd.service"], "Fleet management agent",
     "Zero-touch provisioning and fleet management client (firmware reports, data-usage monitoring).",
     "Fleet management and zero-touch provisioning.", []),
    ("omadmd", ["omadmd.service"], "OMA-DM client",
     "Carrier device management: provisioning, firmware-update sessions and their reports.",
     "Carrier-pushed settings and firmware updates over the air.", ["omadm_main"]),
    ("omadm_proxy", ["omadm_proxy.service"], "OMA-DM push proxy",
     "Receives the carrier's device-management push messages.", "Carrier-initiated management sessions.", []),
    ("nuad", ["nuad.service", "nua_watchdog.service"], "Update and upload agent",
     "Looks for and downloads firmware updates and uploads health logs (its watchdog unit is handled together).",
     "Automatic firmware update checks and health-log uploads.", []),
    ("duad", ["duad.service"], "Data usage reporting",
     "Reports data usage to the carrier back-end.", "Data-usage reports to the carrier (local counters keep working).",
     ["dua_main_thread"]),
    ("wdcp", ["wdcp.service"], "Diagnostics collection",
     "Collects device and network diagnostics for the vendor.", "Vendor diagnostics collection.", ["wdcp"]),
    ("tr069d", ["tr069d.service"], "TR-069 client",
     "Remote management by an auto-configuration server (ACS).", "TR-069 remote management (Services > TR-069).", []),
    ("ccd", ["ccd.service"], "Carrier customization",
     "Applies carrier-specific settings when a different SIM is inserted.",
     "Automatic carrier-specific setup (APN presets, branding) when the SIM changes.", []),
]
SERVICES = {s[0]: s for s in VENDOR_SERVICES}


def parse_mounts(text: str) -> List[Dict[str, str]]:
    out = []
    for line in (text or "").splitlines():
        f = line.split()
        if len(f) >= 4:
            dec = lambda s: s.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")  # noqa: E731
            out.append({"source": dec(f[0]), "target": dec(f[1]), "fstype": f[2], "options": f[3]})
    return out


def register(app):
    r = app.router
    lock = threading.Lock()
    mock: Dict[str, Any] = {"units": {}, "mounted": False}
    systemd_dir = app.cfg.get("systemd_dir", "/etc/systemd/system") if hasattr(app.cfg, "get") else "/etc/systemd/system"

    def pstate() -> Dict[str, Any]:
        return app.state.read_json("privacy.json", {}) or {}

    def save_pstate(s: Dict[str, Any]) -> None:
        app.state.write_json("privacy.json", s)

    # ------------------------------------------------------------------ systemd
    def systemctl(action: str, unit: str) -> Dict[str, Any]:
        if app.mock:
            u = mock["units"].setdefault(unit, {"active": "active", "enabled": "enabled"})
            if action == "is-active":
                return {"ok": u["active"] == "active", "output": u["active"] + "\n"}
            if action == "is-enabled":
                return {"ok": u["enabled"] == "enabled", "output": u["enabled"] + "\n"}
            if action in ("stop", "start"):
                u["active"] = "inactive" if action == "stop" or u.get("blocked") else "active"
            elif action in ("disable", "enable"):
                u["enabled"] = action + "d"
            return {"ok": True, "output": ""}
        return app.system.systemctl(action, unit)

    def daemon_reload() -> None:
        if not app.mock:
            app.system.run(["systemctl", "daemon-reload"], timeout=60)

    def dropin(unit: str) -> str:
        return os.path.join(systemd_dir, unit + ".d", DROPIN_NAME)

    def blocked(unit: str) -> bool:
        if app.mock:
            return bool(mock["units"].get(unit, {}).get("blocked"))
        return os.path.isfile(dropin(unit))

    def set_blocked(unit: str, on: bool) -> None:
        if app.mock:
            mock["units"].setdefault(unit, {"active": "active", "enabled": "enabled"})["blocked"] = on
            return
        path = dropin(unit)
        if on:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(DROPIN_TEXT)
            os.replace(tmp, path)
        elif os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                if "Written by the router web UI" not in f.read():
                    raise vendor_error("Refusing to delete %s: it was not written by this UI" % path)
            os.remove(path)
            try:
                os.rmdir(os.path.dirname(path))      # only succeeds when the .d directory is now empty
            except OSError:
                pass

    def unit_status(unit: str) -> Dict[str, Any]:
        a = systemctl("is-active", unit)
        e = systemctl("is-enabled", unit)
        aout, eout = (a.get("output") or "").strip(), (e.get("output") or "").strip()
        missing = any(x in (aout + " " + eout).lower() for x in ("no such file", "not-found", "could not be found",
                                                                  "not available on this device"))
        act = aout.splitlines()[-1].strip() if aout else "unknown"
        en = eout.splitlines()[-1].strip() if eout else "unknown"
        if missing:
            act, en = "not-found", "not-found"
        return {"unit": unit, "active": act, "enabled": en, "blocked": blocked(unit), "installed": not missing}

    def watched_names() -> List[str]:
        from .device import parse_watchdog_apps
        res = app.cli_try("watchdog_cli", "get_app_list")
        return parse_watchdog_apps(res.text) if res else []

    def service_view(name: str, wd: List[str]) -> Dict[str, Any]:
        _n, units, label, what, stops, wd_names = SERVICES[name]
        st = [unit_status(u) for u in units]
        main = st[0]
        return {"name": name, "label": label, "description": what, "stops": stops, "units": st,
                "active": main["active"], "enabled": main["enabled"], "blocked": any(s["blocked"] for s in st),
                "running": main["active"] in ("active", "activating", "reloading"),
                "watched": any(n in wd for n in wd_names), "installed": main["installed"]}

    @r.route("GET", "/api/privacy/services")
    def list_services(req):
        """Vendor cloud, telemetry and carrier-management services with their state."""
        wd = watched_names()
        prev = pstate().get("services", {})
        out = []
        for s in VENDOR_SERVICES:
            v = service_view(s[0], wd)
            v["turned_off_at"] = (prev.get(s[0]) or {}).get("t")
            out.append(v)
        return {"services": out, "mock": app.mock}

    def get_service(name: str):
        if name not in SERVICES:
            raise not_found("Unknown vendor service")
        return SERVICES[name]

    @r.route("POST", "/api/privacy/services/{name}/disable")
    def disable_service(req):
        """Turn a vendor service off and keep it off after restarts (reversible)."""
        name = req.params["name"]
        _n, units, label, _w, stops, _wd = get_service(name)
        app.require_tier(req, 2)
        with lock:
            ps = pstate()
            rec = (ps.get("services") or {}).get(name) or {}
            before = {u: unit_status(u) for u in units}
            if not before[units[0]]["installed"]:
                raise unavailable("%s is not installed on this unit" % label)
            for u in units:
                set_blocked(u, True)
            daemon_reload()
            for u in units:
                systemctl("stop", u)
                if before[u]["enabled"] == "enabled":
                    systemctl("disable", u)
            rec = {"t": int(time.time()), "was_enabled": {u: rec.get("was_enabled", {}).get(u, before[u]["enabled"])
                                                          for u in units}}
            ps.setdefault("services", {})[name] = rec
            save_pstate(ps)
        app.audit(req, "privacy.service.disable", {"service": name})
        return service_view(name, watched_names())

    @r.route("POST", "/api/privacy/services/{name}/enable")
    def enable_service(req):
        """Turn a vendor service back on (removes this UI's block and restores its start-at-boot state)."""
        name = req.params["name"]
        _n, units, _label, _w, _s, _wd = get_service(name)
        app.require_tier(req, 2)
        with lock:
            ps = pstate()
            rec = (ps.get("services") or {}).pop(name, None) or {}
            for u in units:
                set_blocked(u, False)
            daemon_reload()
            for u in units:
                if (rec.get("was_enabled") or {}).get(u, "enabled") == "enabled":
                    systemctl("enable", u)
                systemctl("start", u)
            save_pstate(ps)
        app.audit(req, "privacy.service.enable", {"service": name})
        return service_view(name, watched_names())

    # ------------------------------------------------------------------ vendor logs in RAM
    def log_mount() -> Optional[Dict[str, str]]:
        if app.mock:
            return {"source": LOG_MOUNT_SOURCE, "target": LOG_DIR, "fstype": "tmpfs",
                    "options": "rw,nosuid,nodev,noexec,size=16384k"} if mock["mounted"] else None
        try:
            with open(app.system.p("/proc/mounts"), "r", encoding="utf-8", errors="replace") as f:
                mounts = parse_mounts(f.read())
        except OSError:
            return None
        hits = [m for m in mounts if m["target"].rstrip("/") == LOG_DIR]
        return hits[-1] if hits else None

    def logs_state() -> Dict[str, Any]:
        m = log_mount()
        want = bool((pstate().get("vendor_logs") or {}).get("enabled"))
        ours = bool(m and m["fstype"] == "tmpfs" and m["source"] == LOG_MOUNT_SOURCE)
        return {"enabled": want, "active": ours, "path": LOG_DIR, "size": LOG_MOUNT_SIZE,
                "foreign_mount": bool(m and not ours), "mount": m,
                "available": app.mock or os.path.isdir(app.system.p(LOG_DIR))}

    def mount_logs() -> None:
        m = log_mount()
        if m:
            if m["fstype"] == "tmpfs" and m["source"] == LOG_MOUNT_SOURCE:
                return                                   # already in RAM: nothing to do
            raise unavailable("Something else is already mounted on %s; not stacking another mount" % LOG_DIR)
        if app.mock:
            mock["mounted"] = True
            return
        base = app.system.p(LOG_DIR)
        if not os.path.isdir(base):
            raise unavailable("%s does not exist on this unit" % LOG_DIR)
        dirs = []
        for dp, dn, _fn in os.walk(base):
            for d in dn:
                rel = os.path.relpath(os.path.join(dp, d), base)
                if rel.count(os.sep) < 4:
                    dirs.append(rel)
            if len(dirs) > 200:
                break
        res = app.system.run(["mount", "-t", "tmpfs", "-o", "size=%s,mode=0755,nosuid,nodev,noexec" % LOG_MOUNT_SIZE,
                              LOG_MOUNT_SOURCE, LOG_DIR], timeout=15)
        if not res.get("ok"):
            raise vendor_error("Mounting a RAM disk on %s failed: %s" % (LOG_DIR, (res.get("output") or "").strip()[:200]))
        for rel in dirs:                                  # vendor daemons expect their sub-directories
            try:
                os.makedirs(os.path.join(base, rel), exist_ok=True)
            except OSError:
                pass

    def unmount_logs() -> None:
        m = log_mount()
        if not m:
            return
        if not (m["fstype"] == "tmpfs" and m["source"] == LOG_MOUNT_SOURCE):
            raise unavailable("The mount on %s was not made by this UI; leaving it alone" % LOG_DIR)
        if app.mock:
            mock["mounted"] = False
            return
        res = app.system.run(["umount", LOG_DIR], timeout=15)
        if not res.get("ok"):
            res = app.system.run(["umount", "-l", LOG_DIR], timeout=15)   # files held open by vendor daemons
            if not res.get("ok"):
                raise vendor_error("Unmounting %s failed: %s" % (LOG_DIR, (res.get("output") or "").strip()[:200]))

    @r.route("GET", "/api/privacy/vendor-logs")
    def get_logs(req):
        """Keep vendor logs in RAM (less flash wear; logs are lost at restart)."""
        return logs_state()

    @r.route("PUT", "/api/privacy/vendor-logs")
    def put_logs(req):
        """Turn 'keep vendor logs in RAM' on or off (mounts / unmounts a RAM disk on the vendor log folder)."""
        data = validate.only_keys(req.json, ["enabled"])
        if "enabled" not in data:
            raise field_error("enabled", "Nothing to change")
        v = validate.boolean("enabled", data["enabled"])
        app.require_tier(req, 2)
        with lock:
            if v:
                mount_logs()
            else:
                unmount_logs()
            ps = pstate()
            ps["vendor_logs"] = {"enabled": v, "t": int(time.time())}
            save_pstate(ps)
        app.audit(req, "privacy.vendor_logs", {"enabled": v})
        return logs_state()

    def apply_at_start() -> None:
        try:
            if (pstate().get("vendor_logs") or {}).get("enabled"):
                with lock:
                    mount_logs()
        except (ApiError, OSError) as e:      # best effort; the page shows the real state
            import logging
            logging.getLogger("m3100ui.privacy").warning("vendor logs in RAM not applied at start: %s", e)

    if not app.mock:
        threading.Thread(target=apply_at_start, name="m3ui-vendor-logs", daemon=True).start()

    # ------------------------------------------------------------------ health-log upload
    def upload() -> Dict[str, Any]:
        res = app.cli_try("mifi_health_cli", "get_log_settings")
        if not res:
            raise unavailable("Health-log upload settings are not available on this unit")
        return {"enabled": flag(res.get("enabled")), "interval_s": res.get_int("upload_interval")}

    @r.route("GET", "/api/privacy/health-upload")
    def get_upload(req):
        """Upload of health/diagnostic logs to the vendor."""
        return upload()

    @r.route("PUT", "/api/privacy/health-upload")
    def put_upload(req):
        """Turn the health-log upload on or off and set its interval."""
        data = validate.only_keys(req.json, ["enabled", "interval_s"])
        cur = upload()
        en = validate.boolean("enabled", data["enabled"]) if "enabled" in data else cur["enabled"]
        iv = validate.integer("interval_s", data["interval_s"], 3600, 30 * 86400) if "interval_s" in data else cur["interval_s"]
        if en is None or iv is None:
            raise unavailable("Current upload settings could not be read; refusing a partial write")
        if (en, iv) != (cur["enabled"], cur["interval_s"]):
            run_setter(app, "mifi_health_cli", "set_log_settings", [b01(en), iv], "Saving health-log upload")
        app.audit(req, "privacy.health_upload", {"keys": sorted(data)})
        return upload()

    app.features.add("privacy.services", lambda: True)
