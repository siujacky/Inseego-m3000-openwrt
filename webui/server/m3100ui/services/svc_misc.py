"""Services: file sharing (SMB share of USB storage), notifications (ANS), TR-069 client, CBSD (read-only).

- file_sharing_cli settings are stored even when the daemon is not running; REST FileSharingSettings is
  501 on this firmware, so the page is hidden unless the service runs (feature "services.files").
- ans_cli set_notification_settings REPLACES the whole notification table, so every write is a
  read-modify-write of the complete list (never fewer entries than were read).
- tr069_cli setters echo passwords to stdout: their output is never returned or logged.
- CBSD (CBRS SAS registration) is regulated spectrum: settings and status are shown read-only and no
  register/grant/heartbeat command is exposed.
"""
import re
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import field_error, not_found, unavailable
from .device import b01, flag, mock_link, run_setter, to_int

# ------------------------------------------------------------------------------------------ file sharing
_FS_PATTERNS = {
    "enabled": re.compile(r"^file sharing enabled\b.*:\s*(\d+)\s*$", re.M),
    "host_name": re.compile(r"^file sharing samba_host_name:[ \t]*(\S*)[ \t]*$", re.M),
    "share_all": re.compile(r"^file sharing_option\b.*:\s*(\d+)\s*$", re.M),
    "read_write": re.compile(r"^file share_all_rw\b.*:\s*(\d+)\s*$", re.M),
}
_FS_FOLDER_RE = re.compile(r"ShareFoldersList\[(\d+)\]:\s*Entry share_folder_name=(.*?),\s*share_folder_type=(\d+)")
FOLDER_RE = r"^[A-Za-z0-9_.-]{1,64}$"
HOST_RE = r"^[A-Za-z0-9][A-Za-z0-9-]{0,14}$"


def parse_fs_settings(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, rx in _FS_PATTERNS.items():
        m = rx.search(text or "")
        if m:
            out[k] = m.group(1) if k == "host_name" else m.group(1) == "1"
    return out


def parse_fs_folders(text: str) -> List[Dict[str, Any]]:
    items = sorted((int(m.group(1)), m.group(2).strip(), m.group(3)) for m in _FS_FOLDER_RE.finditer(text or ""))
    return [{"name": n, "read_only": t == "1"} for _i, n, t in items]


# ------------------------------------------------------------------------------------------ notifications
_LIST_RE = re.compile(r"^list\[(\d+)\]\.(\w+):\[(.*?)\]", re.M)
SURFACES = ["none", "browser", "screen", "all"]
EVENT_LABELS = {
    "startup": "Router turned on", "shutdown": "Router turning off", "battery10": "Low battery",
    "batteryCritical": "Critically low battery", "batteryCharging": "Charging started", "batteryFull": "Battery full",
    "lowCharger": "Charger too weak", "batteryPreservationEnabled": "Battery preservation turned on",
    "batteryPreservationDisabled": "Battery preservation turned off", "simNone": "No SIM card",
    "simRestricted": "SIM restricted", "simPairingLocked": "SIM locked to another device", "simError": "SIM error",
    "simPINLocked": "SIM PIN required", "simPUKLocked": "SIM PUK required", "simNonVZW": "SIM from another carrier",
    "simChange": "SIM changed", "PCO2": "Service error (account)", "PCO3": "Service error (data plan)",
    "PCO5": "Service error (suspended)", "mnsAuto": "Network selection set to automatic", "saFivegMode": "5G standalone mode",
    "nsaFivegMode": "5G non-standalone mode", "switchToGlobalMode": "Switched to global mode", "noService": "No service",
    "roaming": "Roaming", "roamingDomestic": "Domestic roaming", "roamingInternational": "International roaming",
    "deviceJoinWiFi": "Device joined Wi-Fi", "wifiDisabled": "Wi-Fi turned off", "smsReceived": "New text message",
    "wdcpDone": "Diagnostics upload finished", "ucOK": "Charging other device", "ucLowBattery": "Stopped charging other device (low battery)",
    "ucTerminated": "Charging other device ended", "usbModePrompt": "USB connection prompt", "usbMassStorage": "USB storage connected",
    "usbMassStorageFailure": "USB storage problem", "softwareUpdate": "Software update available",
    "softwareUpdateSuccess": "Software update installed", "softwareUpdateFailure": "Software update failed",
    "systemUpdateSuccess": "System update installed", "systemUpdateFailure": "System update failed",
    "systemDiagnostics": "System diagnostics", "highTemp": "High temperature", "autoVpnFailure": "VPN connection failed",
    "data90": "90 % of data plan used", "data100": "Data plan used up",
}
# audio-alert groups of the stock Notification Preferences page (+ the ids it never offered)
EVENT_GROUPS = [
    ("power", "Turning on and off", ["startup", "shutdown"]),
    ("battery_low", "Low battery", ["battery10"]),
    ("battery_critical", "Critically low battery", ["batteryCritical"]),
    ("charging", "Charging", ["batteryCharging", "lowCharger", "batteryFull"]),
    ("charging_other", "Charging other devices", ["ucLowBattery", "ucOK", "ucTerminated"]),
    ("usb", "USB connection", ["usbModePrompt"]),
    ("usb_storage", "USB storage", ["usbMassStorage", "usbMassStorageFailure"]),
    ("sim", "SIM problems", ["simError", "simNone", "simNonVZW", "simChange", "simPINLocked", "simPUKLocked", "simRestricted",
                             "simPairingLocked"]),
    ("preservation", "Battery preservation", ["batteryPreservationEnabled", "batteryPreservationDisabled"]),
    ("sms", "New text message", ["smsReceived"]),
    ("device_join", "Device joined Wi-Fi", ["deviceJoinWiFi"]),
    ("software", "Software updates", ["softwareUpdate", "softwareUpdateSuccess", "softwareUpdateFailure", "systemUpdateSuccess",
                                      "systemUpdateFailure"]),
    ("roaming", "Roaming", ["roaming", "roamingDomestic", "roamingInternational"]),
    ("wifi_off", "Wi-Fi turned off", ["wifiDisabled"]),
    ("network", "Network selection and service", ["mnsAuto", "switchToGlobalMode", "noService", "saFivegMode", "nsaFivegMode"]),
    ("service_errors", "Service errors", ["PCO2", "PCO3", "PCO5"]),
    ("data", "Data plan usage", ["data90", "data100"]),
    ("other", "Other", ["highTemp", "autoVpnFailure", "systemDiagnostics", "wdcpDone"]),
]
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,29}$")


def parse_ans_list(text: str) -> List[Dict[str, str]]:
    items: Dict[int, Dict[str, str]] = {}
    for m in _LIST_RE.finditer(text or ""):
        items.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
    return [items[i] for i in sorted(items)]


def event_view(it: Dict[str, str]) -> Dict[str, Any]:
    sid = it.get("id", "")
    surf = to_int(it.get("display_surfaces"), 0) or 0
    group = next((g for g, _l, ids in EVENT_GROUPS if sid in ids), "other")
    return {"id": sid, "label": EVENT_LABELS.get(sid, sid), "group": group, "enabled": it.get("enabled") == "1",
            "audio": it.get("audio_alert_enabled") == "1", "wake": it.get("wake_display_enabled") == "1",
            "surfaces": SURFACES[surf] if 0 <= surf < len(SURFACES) else "all"}


# ------------------------------------------------------------------------------------------ TR-069
TR_SERVER = [("server_username", "TR069 Server UserName"), ("server_password", "TR069 Server Password"),
             ("server_url", "TR069 Server URL"), ("server_port", "TR069 Server Port Number"),
             ("server_interface", "TR069 Cellular WAN-Interface Type"), ("server_ip_type", "TR069 Server IP Type")]
TR_CLIENT = [("client_username", "TR069 Client UserName"), ("client_password", "TR069 Client Password"),
             ("client_port", "TR069 Client Port Number"), ("client_conn_auth", "TR069 Client Connection Request"),
             ("client_auth_mode", "TR069 Client Authenticate Mode"), ("client_inform_s", "TR069 Client Time Interval(secs)"),
             ("client_cwmp_version", "TR069 Client CWMP Version"), ("client_log_level", "TR069 Client Log Level")]
TR_XMPP = [("xmpp_username", "TR069 XMPP UserName"), ("xmpp_password", "TR069 XMPP Password"), ("xmpp_url", "TR069 XMPP URL"),
           ("xmpp_jabber_id", "TR069 XMPP Jabber ID"), ("xmpp_port", "TR069 XMPP Port Number"),
           ("xmpp_enabled", "TR069 XMPP Feature(0:Disable,1: Enabled)")]
TR_SECRETS = ["server_password", "client_password", "xmpp_password"]
TR_TEXT_MAX = 96          # fgets(buf, 99) in tr069_cli: longer lines would shift the next answer


def parse_tr069_mccmnc(text: str) -> List[Dict[str, Any]]:
    out, cur = [], None
    for line in (text or "").splitlines():
        m = re.match(r"^\s*CARRIER\s*:\s*(\S+)", line)
        if m:
            cur = {"carrier": m.group(1), "mccmnc": []}
            out.append(cur)
            continue
        m = re.match(r"^\s*MCCMNC\s*:\s*([0-9, ]+)", line)
        if m and cur is not None:
            cur["mccmnc"] = [x.strip() for x in m.group(1).split(",") if x.strip()]
    return out


def register(app):
    r = app.router
    mock_link(app)

    def cli(c, cmd):
        return app.cli_try(c, cmd)

    # ============================================================== file sharing
    def files() -> Dict[str, Any]:
        st = cli("file_sharing_cli", "settings_get")
        if not st:
            raise unavailable("File sharing is not available on this unit")
        s = parse_fs_settings(st.text)
        fl = cli("file_sharing_cli", "settings_list_get")
        state = cli("file_sharing_cli", "get_file_sharing_state")
        fst = cli("file_sharing_cli", "get_file_system_type")
        running = bool(cli("file_sharing_cli", "is_running"))
        in_prog = drive = None
        if state:
            m = re.search(r"in progress.*?:\s*(\d+)", state.text)
            in_prog = bool(m and m.group(1) == "1")
            m = re.search(r"mass drive status:\s*(\S+)", state.text)
            drive = m.group(1) if m else None
        fs_type = None
        if fst:
            m = re.search(r"File system type is:\s*(\S+)", fst.text)
            fs_type = m.group(1) if m else None
        return {"supported": True, "service_running": running, "enabled": s.get("enabled"), "host_name": s.get("host_name", ""),
                "share_all": s.get("share_all"), "read_write": s.get("read_write"),
                "folders": parse_fs_folders(fl.text) if fl else [], "in_progress": in_prog, "drive_status": drive,
                "fs_type": fs_type}

    @r.route("GET", "/api/services/files")
    def get_files(req):
        """File sharing (SMB) of a USB drive: settings, shared folders and state."""
        return files()

    @r.route("PUT", "/api/services/files")
    def put_files(req):
        """Change file sharing (on/off, network name, whole drive or selected folders, read-only/read-write)."""
        data = validate.only_keys(req.json, ["enabled", "host_name", "share_all", "read_write"])
        cur = files()
        en = validate.boolean("enabled", data["enabled"]) if "enabled" in data else bool(cur["enabled"])
        host = validate.text("host_name", data["host_name"], 1, 15, pattern=HOST_RE, no_spaces=True) \
            if "host_name" in data else cur["host_name"]
        share_all = validate.boolean("share_all", data["share_all"]) if "share_all" in data else bool(cur["share_all"])
        rw = validate.boolean("read_write", data["read_write"]) if "read_write" in data else bool(cur["read_write"])
        other = any(k in data and {"host_name": host, "share_all": share_all, "read_write": rw}[k] != cur.get(k)
                    for k in ("host_name", "share_all", "read_write"))
        if not en:
            if other:
                raise field_error("enabled", "Turn file sharing on to change its settings")
            if cur["enabled"]:
                # sysintcli setSambaEnabled keeps the name/options (file_sharing_cli settings_set 0 would blank them)
                run_setter(app, "sysintcli", "setSambaEnabled", ["0"], "Turning file sharing off")
        elif other or not cur["enabled"]:
            if not host or not re.match(HOST_RE, host):
                raise field_error("host_name", "Enter a network name (letters, digits and '-', up to 15 characters)")
            run_setter(app, "file_sharing_cli", "settings_set", ["1", host, b01(share_all), b01(rw)], "Saving file sharing")
        app.audit(req, "services.files.update", {"keys": sorted(data)})
        return files()

    @r.route("PUT", "/api/services/files/folders")
    def put_folders(req):
        """Replace the list of shared folders (used when not sharing the whole drive)."""
        data = validate.only_keys(req.json, ["folders"])
        items = data.get("folders")
        if not isinstance(items, list) or not 1 <= len(items) <= 16:
            raise field_error("folders", "Give between 1 and 16 folders")
        ans, seen = [str(len(items))], set()
        for it in items:
            if not isinstance(it, dict):
                raise field_error("folders", "Each folder needs a name")
            name = validate.text("name", it.get("name", ""), 1, 64, pattern=FOLDER_RE, no_spaces=True)
            if name.lower() in seen:
                raise field_error("name", "Folder %s is listed twice" % name)
            seen.add(name.lower())
            ro = validate.boolean("read_only", it.get("read_only", True))
            ans += [name, b01(ro)]
        run_setter(app, "file_sharing_cli", "settings_list_set", ans, "Saving shared folders")
        app.audit(req, "services.files.folders", {"count": len(items)})
        return files()

    @r.route("POST", "/api/services/files/share")
    def share_action(req):
        """Start or stop sharing now."""
        action = validate.choice("action", req.field("action", required=True), ["start", "stop"])
        run_setter(app, "file_sharing_cli", "start_stop_share", [action], "File sharing " + action)
        app.audit(req, "services.files.share", {"action": action})
        return files()

    @r.route("POST", "/api/services/files/drive")
    def drive_action(req):
        """Mount or safely unmount the USB drive."""
        action = validate.choice("action", req.field("action", required=True), ["mount", "unmount"])
        run_setter(app, "file_sharing_cli", "mount_umount_partition", [action], "USB drive " + action)
        app.audit(req, "services.files.drive", {"action": action})
        return files()

    app.features.add("services.files", lambda: app.cli_try("file_sharing_cli", "is_running") is not None
                     or app.rest_try("FileSharingSettings") is not None)

    # ============================================================== notifications
    def notif() -> Dict[str, Any]:
        st = cli("ans_cli", "get_stealth_mode_enabled")
        au = cli("ans_cli", "get_audio_alerts_enabled")
        su = cli("ans_cli", "get_software_update_notification_settings")
        wk = cli("devui_cli", "get_notification_display_wakeup_setting_enabled")
        if not st and not au:
            raise unavailable("Notification settings are not available on this unit")
        return {"stealth": flag(st.get("enabled")) if st else None, "audio": flag(au.get("enabled")) if au else None,
                "initial_update": flag(su.get("Show Initial software update notification")) if su else None,
                "vital_update": flag(su.get("Show Vital software update notification")) if su else None,
                "wake_on_notification": flag(wk.get("deviceui_get_notification_display_wakeup_setting_enabled")) if wk else None,
                "service_running": bool(cli("ans_cli", "is_running"))}

    @r.route("GET", "/api/services/notifications")
    def get_notif(req):
        """Alerts: stealth mode, sounds, wake the screen, software-update notifications."""
        return notif()

    @r.route("PUT", "/api/services/notifications")
    def put_notif(req):
        """Change alert settings."""
        data = validate.only_keys(req.json, ["stealth", "audio", "initial_update", "vital_update", "wake_on_notification"])
        cur = notif()
        for key, cmd in (("stealth", "set_stealth_mode_enabled"), ("audio", "set_audio_alerts_enabled")):
            if key in data:
                v = validate.boolean(key, data[key])
                if v != cur[key]:
                    run_setter(app, "ans_cli", cmd, [b01(v)], "Saving " + key)
        if "initial_update" in data or "vital_update" in data:
            ini = validate.boolean("initial_update", data["initial_update"]) if "initial_update" in data else cur["initial_update"]
            vit = validate.boolean("vital_update", data["vital_update"]) if "vital_update" in data else cur["vital_update"]
            if ini is None or vit is None:
                raise unavailable("Current software-update notification settings could not be read")
            if (ini, vit) != (cur["initial_update"], cur["vital_update"]):
                run_setter(app, "ans_cli", "set_software_update_notification_settings", [b01(ini), b01(vit)],
                           "Saving software-update notifications")
        if "wake_on_notification" in data:
            v = validate.boolean("wake_on_notification", data["wake_on_notification"])
            if v != cur["wake_on_notification"]:
                run_setter(app, "devui_cli", "set_notification_display_wakeup_setting_enabled", [b01(v)],
                           "Saving wake screen on alerts")
        app.audit(req, "services.notifications.update", {"keys": sorted(data)})
        return notif()

    def events_raw() -> List[Dict[str, str]]:
        res = cli("ans_cli", "get_notification_settings")
        if not res:
            raise unavailable("Notification events are not available on this unit")
        items = [it for it in parse_ans_list(res.text) if it.get("id")]
        if not items:
            raise unavailable("The notification table is empty or could not be read")
        return items

    def events() -> Dict[str, Any]:
        items = [event_view(it) for it in events_raw()]
        ids = set(e["id"] for e in items)
        groups = [{"id": g, "label": label, "ids": [i for i in gids if i in ids]} for g, label, gids in EVENT_GROUPS]
        known = set(i for g in groups for i in g["ids"])
        rest = [e["id"] for e in items if e["id"] not in known]
        if rest:
            groups[-1]["ids"] += rest
        return {"events": items, "groups": [g for g in groups if g["ids"]]}

    @r.route("GET", "/api/services/notifications/events")
    def get_events(req):
        """Every alert type with on/off, sound, screen wake-up and where it is shown."""
        return events()

    @r.route("PUT", "/api/services/notifications/events")
    def put_events(req):
        """Change alert types (the complete table is written back, as the vendor API replaces it)."""
        data = validate.only_keys(req.json, ["events"])
        changes = data.get("events")
        if not isinstance(changes, list) or not changes:
            raise field_error("events", "Nothing to change")
        items = events_raw()
        by_id = {it["id"]: it for it in items}
        for ch in changes:
            if not isinstance(ch, dict) or ch.get("id") not in by_id:
                raise field_error("events", "Unknown alert type %s" % (ch.get("id") if isinstance(ch, dict) else ch))
            it = by_id[ch["id"]]
            extra = [k for k in ch if k not in ("id", "enabled", "audio", "wake", "surfaces")]
            if extra:
                raise field_error(extra[0], "Unknown field")
            if "enabled" in ch:
                it["enabled"] = b01(validate.boolean("enabled", ch["enabled"]))
            if "audio" in ch:
                it["audio_alert_enabled"] = b01(validate.boolean("audio", ch["audio"]))
            if "wake" in ch:
                it["wake_display_enabled"] = b01(validate.boolean("wake", ch["wake"]))
            if "surfaces" in ch:
                it["display_surfaces"] = str(SURFACES.index(validate.choice("surfaces", ch["surfaces"], SURFACES)))
        ans = [str(len(items))]
        for it in items:
            if not EVENT_ID_RE.match(it["id"]):
                raise unavailable("Unexpected alert id in the vendor table; refusing to rewrite it")
            vals = [it.get("enabled"), it.get("audio_alert_enabled"), it.get("wake_display_enabled"), it.get("display_surfaces")]
            if any(v is None or not str(v).isdigit() for v in vals):
                raise unavailable("The vendor table for %s is incomplete; refusing to rewrite it" % it["id"])
            ans += [it["id"]] + [str(v) for v in vals]
        run_setter(app, "ans_cli", "set_notification_settings", ans, "Saving alert types")
        app.audit(req, "services.notifications.events", {"changed": sorted(c["id"] for c in changes)})
        return events()

    def current() -> List[Dict[str, Any]]:
        res = cli("ans_cli", "get_current_notification_list")
        if not res:
            return []
        out = []
        for it in parse_ans_list(res.text):
            sid = it.get("id", "")
            out.append({"id": sid, "label": EVENT_LABELS.get(sid, sid), "category": it.get("category", ""),
                        "severity": ["info", "warning", "alert", "critical"][min(3, max(0, to_int(it.get("severity"), 0) or 0))],
                        "persistent": it.get("lifetime") == "1", "audio_type": it.get("audio_alert_type", ""),
                        "surfaces": SURFACES[min(3, max(0, to_int(it.get("display_surfaces"), 0) or 0))]})
        return out

    @r.route("GET", "/api/services/notifications/current")
    def get_current(req):
        """Alerts that are active right now."""
        return current()

    @r.route("POST", "/api/services/notifications/current/{nid}/ack")
    def ack(req):
        """Acknowledge (dismiss) an active alert."""
        nid = req.params["nid"]
        if not EVENT_ID_RE.match(nid) or nid not in [c["id"] for c in current()]:
            raise not_found("No such active alert")
        run_setter(app, "ans_cli", "acknowledge_notification", [nid], "Dismissing the alert")
        app.audit(req, "services.notifications.ack", {"id": nid})
        return current()

    app.features.add("services.notifications", lambda: app.cli_try("ans_cli", "get_audio_alerts_enabled") is not None)

    # ============================================================== TR-069
    def tr_read(cmd, table) -> Dict[str, Any]:
        res = cli("tr069_cli", cmd)
        if not res:
            return {}
        return {k: res.get(label) for k, label in table}

    def tr069(raw_secrets: bool = False) -> Dict[str, Any]:
        st = cli("tr069_cli", "get_status")
        if not st:
            raise unavailable("TR-069 is not available on this unit")
        srv, clt, xmp = tr_read("get_acs_server_config", TR_SERVER), tr_read("get_acs_client_config", TR_CLIENT), \
            tr_read("get_xmpp_config", TR_XMPP)
        pres = cli("tr069_cli", "get_preserve_tr069_config")
        mod = cli("tr069_cli", "get_tr069_config_modifed")
        mcc = cli("tr069_cli", "get_tr069_mccmnc_config")
        vals: Dict[str, Any] = {}
        vals.update(srv)
        vals.update(clt)
        vals.update(xmp)
        out: Dict[str, Any] = {
            "enabled": flag(st.get("TR069 is")), "service_running": bool(cli("tr069_cli", "is_running")),
            "preserve": flag(pres.get("TR069 is")) if pres else None,
            "modified": flag(mod.get("Modified status")) if mod else None,
            "carriers": parse_tr069_mccmnc(mcc.text) if mcc else [],
            "server_username": vals.get("server_username") or "", "server_url": vals.get("server_url") or "",
            "server_port": to_int(vals.get("server_port")),
            "server_interface": {"1": "cellular", "0": "usb"}.get(str(vals.get("server_interface")), None),
            "server_ip_type": {"1": "ipv4", "0": "ipv6"}.get(str(vals.get("server_ip_type")), None),
            "client_username": vals.get("client_username") or "", "client_port": to_int(vals.get("client_port")),
            "client_conn_auth": flag(vals.get("client_conn_auth")),
            "client_auth_mode": {"0": "basic", "1": "digest"}.get(str(vals.get("client_auth_mode")), None),
            "client_inform_s": to_int(vals.get("client_inform_s")), "client_cwmp_version": to_int(vals.get("client_cwmp_version")),
            "client_log_level": to_int(vals.get("client_log_level")),
            "xmpp_enabled": flag(vals.get("xmpp_enabled")), "xmpp_username": vals.get("xmpp_username") or "",
            "xmpp_url": vals.get("xmpp_url") or "", "xmpp_jabber_id": vals.get("xmpp_jabber_id") or "",
            "xmpp_port": to_int(vals.get("xmpp_port")),
        }
        for k in TR_SECRETS:
            out[k + "_set"] = bool(vals.get(k))
            if raw_secrets:
                out[k] = vals.get(k) or ""
        return out

    @r.route("GET", "/api/services/tr069")
    def get_tr069(req):
        """TR-069 (remote management by an ACS): status, server, client and XMPP settings (passwords hidden)."""
        return tr069()

    @r.route("GET", "/api/services/tr069/secret")
    def get_tr069_secret(req):
        """Reveal the stored TR-069 passwords."""
        t = tr069(raw_secrets=True)
        app.audit(req, "services.tr069.reveal")
        return {k: t[k] for k in TR_SECRETS}

    def tr_text(key, v, allow_empty=True):
        s = validate.text(key, v, 0 if allow_empty else 1, TR_TEXT_MAX)
        if "\t" in s:
            raise field_error(key, "Tabs are not allowed")
        return s

    @r.route("PUT", "/api/services/tr069")
    def put_tr069(req):
        """Change TR-069 server/client/XMPP settings and 'keep after factory reset'."""
        keys = [k for k, _l in TR_SERVER + TR_CLIENT + TR_XMPP] + ["preserve"]
        data = validate.only_keys(req.json, keys)
        cur = tr069(raw_secrets=True)
        groups = {"server": [k for k, _l in TR_SERVER], "client": [k for k, _l in TR_CLIENT], "xmpp": [k for k, _l in TR_XMPP]}
        new = dict(cur)
        for k in data:
            if k == "preserve":
                continue
            v = data[k]
            if k.endswith("_password"):
                if v in (None, ""):
                    continue          # write-only: empty keeps the stored password
                new[k] = tr_text(k, v)
            elif k in ("server_url",):
                new[k] = tr_text(k, v)
                if new[k] and (" " in new[k] or not re.match(r"^[A-Za-z0-9:/._~%?#&=+@\[\]-]+$", new[k])):
                    raise field_error(k, "Enter a valid server address or URL")
            elif k in ("server_username", "client_username", "xmpp_username", "xmpp_url", "xmpp_jabber_id"):
                new[k] = tr_text(k, v)
            elif k in ("server_port", "client_port"):
                new[k] = validate.port(k, v)
            elif k == "xmpp_port":
                new[k] = validate.integer(k, v, 0, 65535)
            elif k == "server_interface":
                new[k] = validate.choice(k, v, ["cellular", "usb"])
            elif k == "server_ip_type":
                new[k] = validate.choice(k, v, ["ipv4", "ipv6"])
            elif k == "client_auth_mode":
                new[k] = validate.choice(k, v, ["basic", "digest"])
            elif k == "client_inform_s":
                new[k] = validate.integer(k, v, 0, 600)
            elif k == "client_cwmp_version":
                new[k] = validate.integer(k, v, 0, 9)
            elif k == "client_log_level":
                new[k] = validate.integer(k, v, 0, 4)
            elif k in ("client_conn_auth", "xmpp_enabled"):
                new[k] = validate.boolean(k, v)
        changed = {g for g, ks in groups.items() if any(k in data and new[k] != cur.get(k) for k in ks)}
        if "server" in changed:
            if new["server_port"] is None or new["server_interface"] is None or new["server_ip_type"] is None:
                raise unavailable("Current TR-069 server settings could not be read; refusing a partial write")
            run_setter(app, "tr069_cli", "set_acs_server_config",
                       [new["server_username"], new["server_password"], new["server_url"], new["server_port"],
                        "1" if new["server_interface"] == "cellular" else "0", "1" if new["server_ip_type"] == "ipv4" else "0"],
                       "Saving the TR-069 server")
        if "client" in changed:
            need = ["client_port", "client_conn_auth", "client_auth_mode", "client_inform_s", "client_cwmp_version",
                    "client_log_level"]
            if any(new[k] is None for k in need):
                raise unavailable("Current TR-069 client settings could not be read; refusing a partial write")
            run_setter(app, "tr069_cli", "set_acs_client_config",
                       [new["client_username"], new["client_password"], new["client_port"], b01(new["client_conn_auth"]),
                        "1" if new["client_auth_mode"] == "digest" else "0", new["client_inform_s"], new["client_cwmp_version"],
                        new["client_log_level"]], "Saving the TR-069 client")
        if "xmpp" in changed:
            if new["xmpp_enabled"] is None or new["xmpp_port"] is None:
                raise unavailable("Current XMPP settings could not be read; refusing a partial write")
            run_setter(app, "tr069_cli", "set_xmpp_config",
                       [new["xmpp_username"], new["xmpp_password"], new["xmpp_url"], new["xmpp_jabber_id"], new["xmpp_port"],
                        b01(new["xmpp_enabled"])], "Saving TR-069 XMPP")
        if "preserve" in data:
            v = validate.boolean("preserve", data["preserve"])
            if v != cur["preserve"]:
                run_setter(app, "tr069_cli", "set_preserve_tr069_config", [b01(v)], "Saving keep-after-reset")
        app.audit(req, "services.tr069.update", {"keys": sorted(data)})   # values (passwords) never logged
        return tr069()

    @r.route("PUT", "/api/services/tr069/status")
    def put_tr069_status(req):
        """Turn the TR-069 client on (hands remote management to the ACS: needs confirmation) or off."""
        data = validate.only_keys(req.json, ["enabled"])
        if "enabled" not in data:
            raise field_error("enabled", "Nothing to change")
        v = validate.boolean("enabled", data["enabled"])
        if v:
            app.require_tier(req, 2)
        run_setter(app, "tr069_cli", "set_status", [b01(v)], "Turning TR-069 " + ("on" if v else "off"))
        app.audit(req, "services.tr069.status", {"enabled": v})
        return tr069()

    @r.route("POST", "/api/services/tr069/restart")
    def restart_tr069(req):
        """Restart the TR-069 client so new settings take effect."""
        app.require_tier(req, 2)
        run_setter(app, "tr069_cli", "tr069_restart", [], "Restarting TR-069")
        app.audit(req, "services.tr069.restart")
        return tr069()

    app.features.add("services.tr069", lambda: app.cli_try("tr069_cli", "get_status") is not None)

    # ============================================================== CBSD (read-only)
    def kv(cmd, labels) -> Optional[Dict[str, str]]:
        res = cli("cbsd_cli", cmd)
        if not res or "failed" in res.text.lower() and res.rc is None:
            return None
        return {k: (res.get(lb) or "") for k, lb in labels}

    @r.route("GET", "/api/services/cbsd")
    def get_cbsd(req):
        """CBRS (CBSD) SAS, installer, certificate and antenna settings and registration state (read-only)."""
        sas = kv("get_sas_settings", [("operator", "Operator"), ("url", "URL"), ("version", "Version"), ("user_id", "CBSD_UserID")])
        cpi = kv("get_cpi_settings", [("user_id", "UserID"), ("name", "Name"), ("private_key", "PrivateKey")])
        cert = kv("get_cert_settings", [("certificate", "Certificate"), ("ca_certificate", "CA_Certificate"),
                                        ("private_key", "PrivateKey"), ("passphrase", "PrivateKeyPassPharse")])
        ant = kv("get_antenna_settings", [("azimuth", "Azimuth"), ("downtilt", "Downtilt")])
        if sas is None and cpi is None and ant is None:
            raise unavailable("CBRS (CBSD) is not available on this unit")
        info_res = cli("cbsd_cli", "get_cbsd_info")
        info_note = ""
        info = None
        if info_res:
            if "failed" in info_res.text.lower():
                info_note = "The CBRS device identity (FCC ID, serial number) is not configured on this unit."
            else:
                info = {k: v for k, v, _n in info_res.pairs}
        reg = cli("cbsd_cli", "get_reg_status")
        return {
            "sas": sas or {}, "installer": {"user_id": (cpi or {}).get("user_id", ""), "name": (cpi or {}).get("name", ""),
                                            "key_set": bool((cpi or {}).get("private_key"))},
            "certificates": {k + "_set": bool(v) for k, v in (cert or {}).items()},
            "antenna": {"azimuth": to_int((ant or {}).get("azimuth")), "downtilt": to_int((ant or {}).get("downtilt"))},
            "info": info, "info_note": info_note,
            "registration": {k: v for k, v, _n in reg.pairs} if reg else None,
            "service_running": bool(cli("cbsd_cli", "is_running")),
        }

    app.features.add("services.cbsd", lambda: app.cli_try("cbsd_cli", "get_sas_settings") is not None)
