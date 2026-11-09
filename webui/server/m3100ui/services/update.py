"""Software update: over-the-air update status, check / download / install control,
automatic-update settings and update history.

Sources (field map: docs/features/system.md):
  REST  SoftwareUpdate/Status; PUT SoftwareUpdate/CheckUpdate | Download | DownloadDefer | Install |
        InstallDefer (restapi.fcgi SoftwareUpdateHandler -> webui_su_* -> webuid -> the update agent;
        on the carrier build every su_api_* call is routed to OMA-DM)
  webui_cli get_software_update_status/_info/_settings, set_software_update_settings,
        get_software_installation_duration, get_last_software_update_status,
        get_install_defer_time, get_mdip_restart_defer_time
  fota_cli full_history, last_success, revert_status, last_update_check, install_progress_status,
        max_pkg_size, sideload_max_pkg_size, cfg_get_current_record
  nua_cli status, get_settings, get_su_info, get_poll_interval, get_autoinstall_time,
        installation_status, progress  (+ set_settings / set_poll_interval for the generic NUA agent)
Firmware files are never uploaded or installed from here (no sideload in v1).
"""
import re
from typing import Any, Dict, List, Optional

from .. import validate
from ..adapters.cli import parse_pairs
from ..errors import ApiError, field_error, unavailable
from .system import parse_history_date, run_setter

STATUS = {  # WEBUI_SOFTWARE_UPDATE_* (stock software_update template comments)
    0: ("idle", "No update activity"), 1: ("checking", "Checking for updates"), 2: ("config_updated", "Configuration updated"),
    3: ("up_to_date", "No update available"), 4: ("available", "Update available"), 5: ("downloading", "Downloading the update"),
    6: ("ready", "Downloaded and ready to install"), 7: ("installing", "Installing"), 8: ("installed", "Update installed"),
    9: ("error", "Update problem"), 10: ("deferred", "Installation postponed"), 11: ("cancelled", "Cancelled"),
    12: ("confirm_download", "Waiting for permission to download"),
}
ERRORS = {
    0: "Checking for updates failed", 1: "Download failed", 2: "Installation failed", 3: "Battery too low",
    4: "Updates are not available while roaming", 5: "No mobile network coverage", 6: "Check failed: battery too low",
    7: "Check failed: the device is in fail-safe mode", 8: "Download failed: battery too low",
    9: "Another update session is already running",
}
RESULT_OK = (200, 210, 214)
RECORD_TYPES = {"FOTA_RECORD_TYPE_SU": "software", "FOTA_RECORD_TYPE_CFG": "configuration",
                "FOTA_RECORD_TYPE_REVERTED_CFG": "reverted configuration"}
ACTIONS = {  # action -> (REST sub-resource, allowed states, tier)
    "check": ("CheckUpdate", ("idle", "up_to_date", "available", "error", "cancelled", "config_updated", "installed",
                              "deferred", "unknown"), 2),
    "download": ("Download", ("available", "confirm_download"), 2),
    "download_postpone": ("DownloadDefer", ("available", "confirm_download"), 1),
    "install": ("Install", ("available", "ready", "deferred", "confirm_download"), 3),
    "install_postpone": ("InstallDefer", ("ready", "available"), 1),
}


def _strip_note(n: str) -> str:
    return (n or "").strip().strip("()").strip()


def parse_records(text: str) -> List[Dict[str, Any]]:
    """fota_cli full_history / last_success / revert_status -> records (newest first as printed)."""
    out = []
    for block in re.split(r"^Record no:\[\d+\]\s*$", text or "", flags=re.M)[1:]:
        vals: Dict[str, str] = {}
        notes: Dict[str, str] = {}
        for k, v, n in parse_pairs(block):
            vals.setdefault(k, v)
            notes.setdefault(k, n)

        def num(k):
            try:
                return int(vals.get(k, ""))
            except ValueError:
                return None
        code = num("status")
        out.append({
            "id": num("record id"),
            "type": RECORD_TYPES.get(_strip_note(notes.get("record type", "")), _strip_note(notes.get("record type", "")) or None),
            "status_code": code, "status_text": _strip_note(notes.get("status", "")), "success": code in RESULT_OK,
            "version": vals.get("version", ""), "date": parse_history_date(vals.get("update date")),
            "package": vals.get("package name", ""), "description": vals.get("description", ""),
            "size_bytes": num("size in bytes"), "update_type": _strip_note(notes.get("update type", "")),
            "source": vals.get("source", ""), "vendor": vals.get("vendor", ""), "estimated_min": num("est upd. time"),
            "agent": vals.get("agent", ""), "boot_preference": num("boot preference"),
            "deferred_count": num("install_defer_count"),
        })
    return out


def register(app):
    r = app.router

    def cli(cli_name: str, cmd: str, answers=()):
        return app.cli_try(cli_name, cmd, answers)

    def slow(cli_name: str, cmd: str, ttl: float = 600):
        """Values that change only with a firmware/config update: cached (status is polled every few seconds)."""
        return app.cache.get("update:%s:%s" % (cli_name, cmd), ttl, lambda: cli(cli_name, cmd))

    def raw_status() -> Optional[Dict[str, Any]]:
        st = app.rest_try("SoftwareUpdate/Status")
        if isinstance(st, dict):
            return dict(st, _source="rest")
        res = cli("webui_cli", "get_software_update_status")
        if not res:
            return None
        return {"Status": res.get_int("status", 0), "Value": res.get_int("value", 0), "Version": res.get("version") or "",
                "IsUserInitiated": bool(res.get_int("isuserinitiated", 0)), "SuiValue": res.get_int("sui value", 0),
                "Mdip": res.get_int("mdip value", 0), "_source": "cli"}

    def state_of(st: Dict[str, Any]) -> Dict[str, Any]:
        code = int(st.get("Status") or 0)
        value = int(st.get("Value") or 0)
        name, label = STATUS.get(code, ("unknown", "Unknown state (%d)" % code))
        return {"code": code, "name": name, "label": label, "version": st.get("Version") or "",
                "user_initiated": bool(st.get("IsUserInitiated")), "mdip": int(st.get("Mdip") or 0),
                "sui": int(st.get("SuiValue") or 0), "progress": value if code == 5 else None,
                "error": {"code": value, "label": ERRORS.get(value, "Error %d" % value)} if code == 9 else None}

    def agents_view(nua) -> Dict[str, Any]:
        """NUA (generic update agent) details; on carrier firmware it is normally not running."""
        out: Dict[str, Any] = {"nua_running": bool(nua)}
        if nua:
            for key, cmd in (("nua_status", "status"), ("nua_update_info", "get_su_info"),
                             ("nua_installation", "installation_status"), ("nua_progress", "progress")):
                res = nua if cmd == "status" else cli("nua_cli", cmd)
                if res:
                    out[key] = {k: v for k, v, _n in res.pairs if k not in ("command",)}
        return out

    def status_view() -> Dict[str, Any]:
        st = raw_status()
        if st is None:
            raise unavailable("The software update service is not available on this router")
        state = state_of(st)
        info = cli("webui_cli", "get_software_update_info")
        available = None
        if info and any((info.get(k) or "") not in ("", "0") for k in ("source_sw_version", "target_sw_version", "package size")):
            available = {"source_version": info.get("source_sw_version") or "", "target_version": info.get("target_sw_version") or "",
                         "description": info.get("package description") or "", "size_bytes": info.get_int("package size"),
                         "estimated_min": info.get_int("estimated update time")}
        lc = cli("fota_cli", "last_update_check")
        dur = slow("webui_cli", "get_software_installation_duration", 120)
        dfr = cli("webui_cli", "get_install_defer_time")
        mdip = cli("webui_cli", "get_mdip_restart_defer_time")
        last = cli("webui_cli", "get_last_software_update_status")
        prog = cli("fota_cli", "install_progress_status")
        nua = slow("nua_cli", "status", 60)
        mx = slow("fota_cli", "max_pkg_size")
        smx = slow("fota_cli", "sideload_max_pkg_size")
        cfg = slow("fota_cli", "cfg_get_current_record")
        dev = app.cache.get("update:deviceinfo", 120, lambda: app.rest_try("DeviceInfo", {}) or {})
        actions = {a: state["name"] in allowed for a, (_res, allowed, _t) in ACTIONS.items()}
        return {
            "state": state, "source": st.get("_source"), "available": available,
            "last_check": parse_history_date(lc.get("last update check timestamp")) if lc else None,
            "install_duration_min": dur.get_int("installation_duration") if dur else None,
            "install_defer_s": dfr.get_int("install remaining time in seconds") if dfr else None,
            "restart_defer_s": mdip.get_int("mdip restart defer time in seconds") if mdip else None,
            "last_install_result": last.get_int("last software update status") if last else None,
            "install_in_progress": bool(prog) and "not in progress" not in prog.text.lower(),
            "max_package_bytes": mx.get_int("max package size in bytes") if mx else None,
            "configuration_version": cfg.get("configuration version") if cfg else None,
            "agents": agents_view(nua),
            "current": {"firmware": dev.get("DeviceInfoSwVersion", ""), "modem": dev.get("DeviceInfoModemFwVersion", ""),
                        "pri": dev.get("DeviceInfoPRIVersionDetailed") or dev.get("DeviceInfoPRIVersion", ""),
                        "software": dev.get("DeviceInfoCuteVersion", ""), "web": dev.get("DeviceInfoWebUISwVersion", "")},
            "actions": actions,
            "sideload": {"supported": False, "max_bytes": smx.get_int("max package size in bytes") if smx else None,
                         "reason": "Installing firmware files uploaded from a computer is not offered in this version."},
            "note": "Checking contacts the vendor's update servers over the mobile connection. Installing an update "
                    "replaces the firmware and restarts the router.",
        }

    @r.route("GET", "/api/update/status")
    def get_status(req):
        """Current update state, available update, last check and agent info."""
        return status_view()

    @r.route("GET", "/api/update/history")
    def get_history(req):
        """Update history (all records), last successful update and reverted configurations."""
        full = cli("fota_cli", "full_history")
        last = cli("fota_cli", "last_success")
        rev = cli("fota_cli", "revert_status")
        if not (full or last):
            raise unavailable("The update history is not available")
        return {"records": parse_records(full.text) if full else [], "last_success": (parse_records(last.text) or [None])[0] if last else None,
                "reverted": parse_records(rev.text) if rev else []}

    # -- settings ------------------------------------------------------------------------------
    def settings_view() -> Dict[str, Any]:
        su = cli("webui_cli", "get_software_update_settings")
        nua = cli("nua_cli", "get_settings")
        poll = cli("nua_cli", "get_poll_interval")
        auto = cli("nua_cli", "get_autoinstall_time")
        out: Dict[str, Any] = {"auto_update": None, "auto_check": None, "low_battery_threshold": None}
        if su:
            out.update(auto_update=bool(su.get_int("fumoenabled", 0)), auto_check=bool(su.get_int("autopollenabled", 0)),
                       low_battery_threshold=su.get_int("lowbatterythreshold"))
        out["nua"] = None if not nua else {
            "fumo": bool(nua.get_int("fumo enabled", 0)), "poll": bool(nua.get_int("poll enabled", 0)),
            "opt_out_disabled": bool(nua.get_int("fota opt out disabled", 0)),
            "low_battery": nua.get_int("low battery threshold"), "low_battery_charger": nua.get_int("low battery threshold with charger"),
            "poll_interval_s": poll.get_int("poll internval, seconds") if poll else None,
            "autoinstall_s": auto.get_int("auto-install, seconds") if auto else None,
            "model": nua.get("model string") or "", "running": bool(cli("nua_cli", "is_running")),
        }
        out["available"] = su is not None
        return out

    @r.route("GET", "/api/update/settings")
    def get_settings(req):
        """Automatic update settings (and the generic NUA agent's settings)."""
        return settings_view()

    @r.route("PUT", "/api/update/settings")
    def put_settings(req):
        """Automatic updates on/off, automatic checks on/off, low-battery limit."""
        data = validate.only_keys(req.json, ["auto_update", "auto_check", "low_battery_threshold"])
        cur = settings_view()
        if not cur["available"]:
            raise unavailable("The automatic update settings are not available")
        f = validate.boolean("auto_update", data.get("auto_update", cur["auto_update"]))
        p = validate.boolean("auto_check", data.get("auto_check", cur["auto_check"]))
        lb = validate.integer("low_battery_threshold", data.get("low_battery_threshold", cur["low_battery_threshold"] or 20), 1, 99)
        run_setter(app, "webui_cli", "set_software_update_settings", ["1" if f else "0", "1" if p else "0", str(lb)],
                   what="Saving the update settings")
        app.audit(req, "update.settings", {"auto_update": f, "auto_check": p, "low_battery_threshold": lb})
        return settings_view()

    @r.route("PUT", "/api/update/nua")
    def put_nua(req):
        """Settings of the generic NUA update agent (not the active agent on carrier firmware)."""
        data = validate.only_keys(req.json, ["fumo", "poll", "opt_out_disabled", "low_battery", "low_battery_charger"])
        cur = settings_view()["nua"]
        if cur is None:
            raise unavailable("The NUA update agent settings are not available")
        vals = [
            ("-f", validate.boolean("fumo", data.get("fumo", cur["fumo"]))),
            ("-p", validate.boolean("poll", data.get("poll", cur["poll"]))),
            ("-s", validate.boolean("opt_out_disabled", data.get("opt_out_disabled", cur["opt_out_disabled"]))),
            ("-l", validate.integer("low_battery", data.get("low_battery", cur["low_battery"] or 25), 1, 99)),
            ("-w", validate.integer("low_battery_charger", data.get("low_battery_charger", cur["low_battery_charger"] or 10), 0, 99)),
        ]
        args = []
        for flag, v in vals:
            args += [flag, str(int(v))]
        run_setter(app, "nua_cli", "set_settings", args, what="Saving the NUA settings")
        app.audit(req, "update.nua", {k: int(v) for k, v in vals})
        return settings_view()

    @r.route("PUT", "/api/update/nua/poll-interval")
    def put_nua_poll(req):
        """NUA check interval in seconds (minimum 60; applies after an agent or router restart)."""
        data = validate.only_keys(req.json, ["seconds"])
        secs = validate.integer("seconds", data.get("seconds"), 60, 30 * 86400)
        run_setter(app, "nua_cli", "set_poll_interval", [str(secs)], what="Saving the check interval")
        app.audit(req, "update.nua.poll_interval", {"seconds": secs})
        return settings_view()

    # -- actions --------------------------------------------------------------------------------
    def action(req, name: str):
        resource, allowed, tier = ACTIONS[name]
        validate.only_keys(req.json, [])
        st = raw_status()
        state = state_of(st) if st else {"name": "unknown"}
        if state["name"] not in allowed:
            raise field_error("_", "Not possible now (%s)" % STATUS.get(state.get("code", -1), ("", state["name"]))[1].lower())
        app.require_tier(req, tier)
        try:
            app.rest.put("SoftwareUpdate/" + resource, {})
        except ApiError as e:
            if e.code in ("unsupported", "disabled"):
                raise unavailable("This update action is not available on this router (vendor REST SoftwareUpdate/%s)" % resource)
            raise
        app.audit(req, "update." + name)
        app.cache.invalidate("update:")
        return status_view()

    @r.route("POST", "/api/update/check")
    def check(req):
        """Ask the update servers for a new version (tier 2; uses the mobile connection)."""
        return action(req, "check")

    @r.route("POST", "/api/update/download")
    def download(req):
        """Download the available update (tier 2)."""
        return action(req, "download")

    @r.route("POST", "/api/update/download/postpone")
    def download_postpone(req):
        """Postpone downloading the available update."""
        return action(req, "download_postpone")

    @r.route("POST", "/api/update/install")
    def install(req):
        """Install the update now; the router restarts (tier 3)."""
        return action(req, "install")

    @r.route("POST", "/api/update/install/postpone")
    def install_postpone(req):
        """Postpone installing a downloaded update."""
        return action(req, "install_postpone")
