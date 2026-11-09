"""Mock: Device & Services area setters (power save, display, LED, buzzer, GPS, file sharing, ANS,
TR-069, watchdog, mifi_health).  Every handler rewrites the matching get_* output in the vendor's own
format (copied from the printf formats in the binaries / the captured fixtures), so the service parsers
run unchanged in --mock.  REST resources that mirror CLI state (PowerSaveSettings, GPSSettings,
BatteryPreservationMode/Enabled, WiFi/DisplaySettings, WatchdogSettings) are kept in sync both ways
through the link services.device.mock_link() sets up per App instance.
"""
import re
import time

from ..adapters.mock import cli_handler, rest_handler

SCREEN_TIMEOUTS = [30, 60, 120, 300, 600, 28800, 0]


# ------------------------------------------------------------------------------------------ helpers
def _status(fn, module, rc=0, msg="success."):
    return "%s returned %d (%s: %s)\n" % (fn, rc, module, msg)


def _cmd_head(cmd):
    return "command [%s]\n\n" % cmd


def _sub(store, cli, cmd, pattern, value):
    """Replace group 2 of `pattern` (prefix, value, suffix groups) in the stored output of cli cmd."""
    txt = store.text(cli, cmd)
    if txt is None:
        return
    rx = re.compile(pattern, re.M)
    if rx.search(txt):
        txt = rx.sub(lambda m: m.group(1) + str(value) + m.group(3), txt, count=1)
        store.set_text(cli, cmd, txt)


def _br(label):
    """Pattern for `label:[v]` / `label: [v]` / `label=[v]` lines."""
    return r"^(\s*" + re.escape(label) + r"\s*[:=]?\s*\[)([^\]]*)(\])"


def _rest(store):
    return store.state.get("_device_rest")


def _store(rest):
    return getattr(rest, "_device_cli_store", None)


def _rest_set(store, path, changes):
    rest = _rest(store)
    if rest is not None and isinstance(rest.data.get(path), dict):
        rest.data[path].update(changes)


def _sysint(fn, prompt="", rc=0):
    return ("Starting sysint client library\n%s%s returned %d\nStopping sysint client library %d\n"
            "sysinterfaceShutdown complete 0\n" % (prompt, fn, rc, rc))


def _a(answers, i, default="0"):
    return answers[i] if i < len(answers) else default


# ------------------------------------------------------------------------------------------ power save
def _ps(store, field, value):
    _sub(store, "powersave_cli", "get_settings", r"^(settings\." + field + r": \[)(-?\d+)(\])", int(value))


@cli_handler("sysintcli", "setPowerSaveEnabled")
def _sys_ps_en(store, answers, args):
    _ps(store, "enabled", _a(answers, 0))
    _rest_set(store, "PowerSaveSettings", {"PowerSaveEnabled": _a(answers, 0) == "1"})
    return _sysint("sys_setPowerSaveEnabled", "Enable ? ( 1 or 0 ): ")


@cli_handler("sysintcli", "setDeepSleepEnabled")
def _sys_ds_en(store, answers, args):
    _ps(store, "deep_sleep_enabled", _a(answers, 0))
    return _sysint("sys_setDeepSleepEnabled", "Enable ? ( 1 or 0 ): ")


@cli_handler("sysintcli", "setAutoShutdownEnabled")
def _sys_as_en(store, answers, args):
    _ps(store, "shutdown_enabled", _a(answers, 0))
    return _sysint("sys_setAutoShutdownEnabled", "Enable ? ( 1 or 0 ): ")


@cli_handler("sysintcli", "setPowerSaveSettings")
def _sys_ps_set(store, answers, args):
    idle, wake, shut = (int(_a(answers, i)) for i in range(3))
    _ps(store, "deep_sleep_idle_time", idle)
    _ps(store, "deep_sleep_wakeup_time", wake)
    _ps(store, "shutdown_idle_time", shut)
    _rest_set(store, "PowerSaveSettings", {"PowerSaveDeepSleepIdleDuration": idle, "PowerSaveDeepSleepMaxDuration": wake})
    return _sysint("sys_setPowerSaveSettings", "", 0).replace("returned 0\n", "returned 0 \n", 1)


@rest_handler("PUT", "PowerSaveSettings")
def _rest_ps(rest, body):
    cur = rest.data.setdefault("PowerSaveSettings", {})
    cur.update(body or {})
    st = _store(rest)
    if st is not None:
        if "PowerSaveEnabled" in (body or {}):
            _ps(st, "enabled", 1 if body["PowerSaveEnabled"] else 0)
        if "PowerSaveDeepSleepIdleDuration" in (body or {}):
            _ps(st, "deep_sleep_idle_time", body["PowerSaveDeepSleepIdleDuration"])
        if "PowerSaveDeepSleepMaxDuration" in (body or {}):
            _ps(st, "deep_sleep_wakeup_time", body["PowerSaveDeepSleepMaxDuration"])
    return None


# ------------------------------------------------------------------------------------------ universal charging / preservation
@cli_handler("sysintcli", "setUniversalChargingSettings")
def _sys_uc_set(store, answers, args):
    _sub(store, "dsm_cli", "get_settings", r"^(settings\.battery_pack_low_battery_threshold: \[)(\d+)(\])", int(_a(answers, 0)))
    return _sysint("sys_setUniversalChargingSettings", "Enter threshold ( percentage ) : ")


@cli_handler("sysintcli", "setUniversalChargingEnabled")
def _sys_uc_en(store, answers, args):
    _sub(store, "dsm_cli", "get_settings", r"^(settings\.battery_pack_active: \[)(\d+)(\])", int(_a(answers, 0)))
    return _sysint("sys_setUniversalChargingEnabled", "Enable ? ( 1 or 0 ): ")


@cli_handler("dsm_cli", "set_battery_health_enabled")
def _dsm_bh(store, answers, args):
    _sub(store, "dsm_cli", "get_battery_health_enabled", _br("Battery Health Mode"), int(_a(answers, 0)))
    _rest_set(store, "BatteryPreservationMode/Enabled", {"Enabled": _a(answers, 0) == "1"})
    return "Battery_life_extenstion mode (1=on, 0=off): " + _status("dsm_set_battery_health_enabled", "DSM").rstrip("\n")


@rest_handler("PUT", "BatteryPreservationMode/Enabled")
def _rest_bp(rest, body):
    rest.data.setdefault("BatteryPreservationMode/Enabled", {}).update(body or {})
    st = _store(rest)
    if st is not None and "Enabled" in (body or {}):
        _sub(st, "dsm_cli", "get_battery_health_enabled", _br("Battery Health Mode"), 1 if body["Enabled"] else 0)
    return None


# ------------------------------------------------------------------------------------------ display (devui)
DEVUI_BOOL = {
    "set_display_setting_enabled": ("get_display_setting_enabled", "display_setting_enabled"),
    "set_quickstart_enabled": ("get_quickstart_enabled", "quickstart_enabled"),
    "set_usb_connection_prompt_enabled": ("get_usb_connection_prompt_enabled", "usb_connection_prompt_enabled"),
    "set_notification_display_wakeup_setting_enabled": ("get_notification_display_wakeup_setting_enabled",
                                                        "deviceui_get_notification_display_wakeup_setting_enabled"),
    "set_data_usage_touchscreen_enabled": ("get_data_usage_touchscreen_enabled", "deviceui_get_data_usage_touchscreen_enabled"),
    "set_display_ethernet_port_enabled": ("get_display_ethernet_port_enabled", "deviceui_get_display_ethernet_port_enabled"),
    "set_file_sharing_display_setting_enabled": ("get_file_sharing_display_setting_enabled", "file_sharing_display_setting_enabled"),
    "set_vital_update_prompt_enabled": ("get_vital_update_prompt_enabled", "vital_update_prompt_enabled"),
    "set_admin_password_enabled": ("get_admin_password_enabled", "admin_password_setting_enabled"),
    "set_wifi_display_setting_enabled": ("get_wifi_display_setting_enabled", "deviceui_get_wifi_display_setting_enabled"),
}


def _devui_bool_handler(setcmd, getcmd, label):
    def h(store, answers, args):
        _sub(store, "devui_cli", getcmd, _br(label), int(_a(answers, 0)))
        if setcmd == "set_wifi_display_setting_enabled":
            _rest_set(store, "WiFi/DisplaySettings", {"AllowDeviceUIWiFiEnabled": _a(answers, 0) == "1"})
        return _status("deviceui_" + setcmd, "DEVUI_FRAMEWORK")
    return h


for _set, (_get, _label) in DEVUI_BOOL.items():
    cli_handler("devui_cli", _set)(_devui_bool_handler(_set, _get, _label))


@cli_handler("devui_cli", "set_screen_timeout_settings")
def _devui_timeout(store, answers, args):
    i = int(_a(answers, 0))
    _sub(store, "devui_cli", "get_screen_timeout_settings", _br("screen_timeout"), SCREEN_TIMEOUTS[i] if 0 <= i <= 6 else -1)
    return _status("deviceui_set_screen_timeout_settings", "DEVUI_FRAMEWORK")


def _devui_wifi(which):
    def h(store, answers, args):
        name, key = int(_a(answers, 0)), int(_a(answers, 1))
        cmd = "get_%s_wifi_info_display_settings" % which
        _sub(store, "devui_cli", cmd, _br("show_wifi_name_enabled"), name)
        _sub(store, "devui_cli", cmd, _br("show_wifi_key_enabled"), key)
        suffix = "Primary" if which == "primary" else "Guest"
        _rest_set(store, "WiFi/DisplaySettings", {"HideSSIDTouchEnabled" + suffix: not name,
                                                  "HidePasswordTouchEnabled" + suffix: not key})
        return _status("deviceui_set_%s_wifi_info_display_settings" % which, "DEVUI_FRAMEWORK")
    return h


cli_handler("devui_cli", "set_primary_wifi_info_display_settings")(_devui_wifi("primary"))
cli_handler("devui_cli", "set_guest_wifi_info_display_settings")(_devui_wifi("guest"))


@rest_handler("PUT", "WiFi/DisplaySettings")
def _rest_wifi_display(rest, body):
    cur = rest.data.setdefault("WiFi/DisplaySettings", {})
    cur.update(body or {})
    st = _store(rest)
    if st is not None:
        for which, suffix in (("primary", "Primary"), ("guest", "Guest")):
            cmd = "get_%s_wifi_info_display_settings" % which
            _sub(st, "devui_cli", cmd, _br("show_wifi_name_enabled"), 0 if cur.get("HideSSIDTouchEnabled" + suffix) else 1)
            _sub(st, "devui_cli", cmd, _br("show_wifi_key_enabled"), 0 if cur.get("HidePasswordTouchEnabled" + suffix) else 1)
        _sub(st, "devui_cli", "get_wifi_display_setting_enabled", _br("deviceui_get_wifi_display_setting_enabled"),
             1 if cur.get("AllowDeviceUIWiFiEnabled", True) else 0)
    return None


# ------------------------------------------------------------------------------------------ LED / buzzer
@cli_handler("led_cli", "set_enabled")
def _led(store, answers, args):
    _sub(store, "led_cli", "get_enabled", r"^(enabled\[)(\d+)(\])", int(_a(answers, 0)))
    return 'Select LED enabled setting [0("not enabled"), 1("Enabled")]enabled: %s\n' % _a(answers, 0) + \
        _status("led_set_enabled", "LED")


@cli_handler("buzzer_cli", "on")
def _buzz_on(store, answers, args):
    store.state["buzzer"] = {"on_ms": _a(answers, 0), "off_ms": _a(answers, 1), "repeat": _a(answers, 2), "t": time.time()}
    return ("What is on time( in millsecs ): What is off time( in millsecs ): What is repeat count: " +
            "buzzer_on returned 0 (BUZZER: success.)\n")


@cli_handler("buzzer_cli", "off")
def _buzz_off(store, answers, args):
    store.state.pop("buzzer", None)
    return "buzzer_off returned 0 (BUZZER: success.)\n"


# ------------------------------------------------------------------------------------------ GPS
MOCK_FIX = (52.520008, 13.404954)          # neutral sample position for --mock


def _fix_text(lat, lon):
    now = int(time.time())
    lines = ["alt_ellipsoid:[%f]" % 41.5, "fix_quality:[%d]" % 1, "fixed:[%d]" % 1, "heading:[%lf]" % 0.0,
             "horizontal_conf:[%d]" % 68, "horizontal_dop:[%f]" % 0.9, "horizontal_speed:[%f]" % 0.0,
             "horiz_speed_unc:[%f]" % 0.4, "horiz_unc_cir:[%f]" % 6.5, "latitude:[%lf]" % lat, "longitude:[%lf]" % lon,
             "num_locked_prn:[%d]" % 9, "position_dop:[%f]" % 1.6, "timestamp:[%u]" % now,
             "first_fix_timestamp:[%u]" % (now - 42), "vertical_dop:[%f]" % 1.3, "vertical_speed:[%f]" % 0.0,
             "vert_speed_unc:[%f]" % 0.6, "vertical_unc:[%f]" % 9.8]
    return _cmd_head("get_last_fix") + _status("cmd_get_last_fix", "GPS") + "\n".join(lines) + "\n"


def _gps_privacy(store):
    txt = store.text("gps_cli", "get_privacy") or ""
    return "accepted:[1]" in txt


def _gps_engine(store, on):
    store.set_text("gps_cli", "gps_status", _cmd_head("gps_status") + _status("cmd_gps_status", "GPS") +
                   "status:[%s]\n" % ("Fixed" if on else "Disabled"))
    if on:
        store.set_text("gps_cli", "get_last_fix", _fix_text(*MOCK_FIX))
    _rest_set(store, "GPSSettings", {"GPSEnabled": bool(on)})


def _gps_set_privacy(store, v):
    store.set_text("gps_cli", "get_privacy", _cmd_head("get_privacy") + _status("cmd_get_privacy", "GPS") +
                   "accepted:[%d]\n" % (1 if v else 0))
    if not v:
        store.set_text("gps_cli", "get_last_fix", _cmd_head("get_last_fix") +
                       _status("cmd_get_last_fix", "GPS", 1001, "unable to comply because the privacy policy has not been accepted."))


@cli_handler("gps_cli", "set_active")
def _gps_active(store, answers, args):
    store.set_text("gps_cli", "get_active", _cmd_head("get_active") + _status("cmd_get_active", "GPS") +
                   "default_status:[%s]\n" % ("on" if _a(answers, 0) == "1" else "off"))
    return _status("cmd_set_active", "GPS")


@cli_handler("gps_cli", "set_privacy")
def _gps_priv(store, answers, args):
    _gps_set_privacy(store, _a(answers, 0) == "1")
    return _status("cmd_set_privacy", "GPS")


@cli_handler("gps_cli", "set_nmea_tcp")
def _gps_nmea(store, answers, args):
    en, port = int(_a(answers, 0)), int(_a(answers, 1, "11010"))
    store.set_text("gps_cli", "get_nmea_tcp", _cmd_head("get_nmea_tcp") + _status("cmd_get_nmea_tcp", "GPS") +
                   "enabled:[%d]\nport:[%d]\n" % (en, port))
    _rest_set(store, "GPSSettings", {"NMEAOverWiFiEnabled": bool(en), "NMEAOverWiFiPortNumber": port})
    return _status("cmd_set_nmea_tcp", "GPS")


@cli_handler("gps_cli", "agps_mode_set")
def _gps_mode(store, answers, args):
    mode = {"0": "XTRA", "1": "MS-A", "2": "MS-B"}.get(_a(answers, 0), "XTRA")
    store.set_text("gps_cli", "get_mode", _cmd_head("get_mode") + _status("cmd_get_mode", "GPS") + "mode:[%s]\n" % mode)
    return _status("cmd_agps_mode_set", "GPS")


@cli_handler("gps_cli", "gps_start")
def _gps_start(store, answers, args):
    if not _gps_privacy(store):
        return _status("cmd_gps_start", "GPS", 1001, "unable to comply because the privacy policy has not been accepted.")
    _gps_engine(store, True)
    return _status("cmd_gps_start", "GPS")


@cli_handler("gps_cli", "gps_stop")
def _gps_stop(store, answers, args):
    _gps_engine(store, False)
    return _status("cmd_gps_stop", "GPS")


@cli_handler("gps_cli", "force_xtra")
def _gps_xtra(store, answers, args):
    store.state["xtra_requested"] = time.time()
    return _status("cmd_force_xtra", "GPS")


@rest_handler("PUT", "GPSSettings")
def _rest_gps(rest, body):
    body = body or {}
    cur = rest.data.setdefault("GPSSettings", {})
    st = _store(rest)
    if "GPSEnabled" in body and bool(body["GPSEnabled"]) != bool(cur.get("GPSEnabled")):
        if st is not None:
            if body["GPSEnabled"]:
                _gps_set_privacy(st, True)        # the vendor handler accepts the agreement first
            _gps_engine(st, bool(body["GPSEnabled"]))
    cur.update(body)
    if st is not None and ("NMEAOverWiFiEnabled" in body or "NMEAOverWiFiPortNumber" in body):
        st.set_text("gps_cli", "get_nmea_tcp", _cmd_head("get_nmea_tcp") + _status("cmd_get_nmea_tcp", "GPS") +
                    "enabled:[%d]\nport:[%d]\n" % (1 if cur.get("NMEAOverWiFiEnabled") else 0,
                                                    int(cur.get("NMEAOverWiFiPortNumber") or 11010)))
    return None


# ------------------------------------------------------------------------------------------ file sharing
def _fs_text(en, host, opt, rw):
    return (_cmd_head("settings_get") +
            "file sharing enabled - 1(enabled), 0(disabled): %d\n" % en +
            "file sharing samba_host_name: %s\n" % host +
            "file sharing_option - 1(share full partition), 0(share selected folders): %d\n" % opt +
            "file share_all_rw - 1(full partition share is RW), 0(full partition share is RO) : %d\n" % rw +
            _status("file_sharing_get_settings", "FILE_SHARING"))


def _fs_current(store):
    txt = store.text("file_sharing_cli", "settings_get") or ""
    def num(rx):
        m = re.search(rx, txt, re.M)
        return int(m.group(1)) if m else 0
    m = re.search(r"^file sharing samba_host_name:[ \t]*(\S*)", txt, re.M)
    return (num(r"^file sharing enabled\b.*:\s*(\d+)\s*$"), m.group(1) if m else "",
            num(r"^file sharing_option\b.*:\s*(\d+)\s*$"), num(r"^file share_all_rw\b.*:\s*(\d+)\s*$"))


@cli_handler("file_sharing_cli", "settings_set")
def _fs_set(store, answers, args):
    en = int(_a(answers, 0))
    if en == 0:                       # vendor behaviour: the struct is sent with zeroed name/options
        store.set_text("file_sharing_cli", "settings_get", _fs_text(0, "", 0, 0))
    else:
        opt = int(_a(answers, 2))
        rw = int(_a(answers, 3)) if opt == 1 else 0
        store.set_text("file_sharing_cli", "settings_get", _fs_text(1, _a(answers, 1, ""), opt, rw))
    return _status("file_sharing_set_settings", "FILE_SHARING")


@cli_handler("sysintcli", "setSambaEnabled")
def _sys_samba(store, answers, args):
    _en, host, opt, rw = _fs_current(store)
    store.set_text("file_sharing_cli", "settings_get", _fs_text(int(_a(answers, 0)), host, opt, rw))
    return _sysint("sys_setSambaEnabled", "Enable ? ( 1 or 0 ): ")


@cli_handler("file_sharing_cli", "settings_list_set")
def _fs_list(store, answers, args):
    n = int(_a(answers, 0))
    lines = ["Settings ShareFoldersList[%d]: Entry share_folder_name=%s, share_folder_type=%s"
             % (i, _a(answers, 1 + 2 * i, ""), _a(answers, 2 + 2 * i)) for i in range(n)]
    store.set_text("file_sharing_cli", "settings_list_get", _cmd_head("settings_list_get") +
                   _status("file_sharing_get_folder_settings_list", "FILE_SHARING") + "Settings item List:\n" +
                   "\n".join(lines) + "\n")
    return _status("file_sharing_set_folder_settings_list", "FILE_SHARING")


@cli_handler("file_sharing_cli", "start_stop_share")
def _fs_share(store, answers, args):
    store.state["fs_sharing"] = (args or ["stop"])[0] == "start"
    return _status("file_sharing_start_stop_file_sharing", "FILE_SHARING")


@cli_handler("file_sharing_cli", "mount_umount_partition")
def _fs_mount(store, answers, args):
    store.state["fs_mounted"] = (args or ["unmount"])[0] == "mount"
    return _status("file_sharing_mount_umount", "FILE_SHARING")


# ------------------------------------------------------------------------------------------ notifications (ANS)
SURF_NOTE = "(0 => None, 1 => Browser, 2 => LCD, 3 => All)"


@cli_handler("ans_cli", "set_stealth_mode_enabled")
def _ans_stealth(store, answers, args):
    _sub(store, "ans_cli", "get_stealth_mode_enabled", _br("enabled"), int(_a(answers, 0)))
    return _status("ans_set_stealth_mode_enabled", "ANS")


@cli_handler("ans_cli", "set_audio_alerts_enabled")
def _ans_audio(store, answers, args):
    _sub(store, "ans_cli", "get_audio_alerts_enabled", _br("enabled"), int(_a(answers, 0)))
    return _status("ans_set_audio_alerts_enabled", "ANS")


@cli_handler("ans_cli", "set_software_update_notification_settings")
def _ans_su(store, answers, args):
    _sub(store, "ans_cli", "get_software_update_notification_settings", _br("Show Initial software update notification"),
         int(_a(answers, 0)))
    _sub(store, "ans_cli", "get_software_update_notification_settings", _br("Show Vital software update notification"),
         int(_a(answers, 1)))
    return _status("ans_set_software_update_notification_settings", "ANS")


@cli_handler("ans_cli", "set_notification_settings")
def _ans_set(store, answers, args):
    n = int(_a(answers, 0))
    lines = []
    for i in range(n):
        b = 1 + 5 * i
        lines += ["list[%d].id:[%s]" % (i, _a(answers, b, "")), "list[%d].enabled:[%s]" % (i, _a(answers, b + 1)),
                  "list[%d].audio_alert_enabled:[%s]" % (i, _a(answers, b + 2)),
                  "list[%d].wake_display_enabled:[%s]" % (i, _a(answers, b + 3)),
                  "list[%d].display_surfaces:[%s]%s" % (i, _a(answers, b + 4), SURF_NOTE)]
    store.set_text("ans_cli", "get_notification_settings", _cmd_head("get_notification_settings") +
                   _status("ans_get_notification_settings", "ANS", 3,
                           "one or more buffer type parameters passed into API is too small.") +
                   _status("ans_get_notification_settings", "ANS") + "ANS notifications item List:\n" + "\n".join(lines) + "\n")
    return _status("ans_set_notification_settings", "ANS")


@cli_handler("ans_cli", "acknowledge_notification")
def _ans_ack(store, answers, args):
    nid = _a(answers, 0, "")
    txt = store.text("ans_cli", "get_current_notification_list") or ""
    items = {}
    for m in re.finditer(r"^list\[(\d+)\]\.(\w+):\[(.*?)\](.*)$", txt, re.M):
        items.setdefault(int(m.group(1)), []).append((m.group(2), m.group(3), m.group(4)))
    keep = [v for _k, v in sorted(items.items()) if dict((f, x) for f, x, _n in v).get("id") != nid]
    out = [_cmd_head("get_current_notification_list"), _status("ans_get_current_notification_list", "ANS"),
           "ANS notifications: count = %d; List:\n" % len(keep)]
    for i, fields in enumerate(keep):
        for f, x, note in fields:
            out.append("list[%d].%s:[%s]%s\n" % (i, f, x, note))
    store.set_text("ans_cli", "get_current_notification_list", "".join(out))
    return _status("ans_acknowledge_notification", "ANS")


# ------------------------------------------------------------------------------------------ TR-069
def _tr_onoff(store, cmd, v):
    store.set_text("tr069_cli", cmd, _cmd_head(cmd) + _status("cmd_tr069_" + cmd if cmd == "get_status" else "cmd_" + cmd,
                                                              "TR069") + "TR069 is:   [%s]\n" % ("Enable" if v == "1" else "Disable"))


@cli_handler("tr069_cli", "set_status")
def _tr_status(store, answers, args):
    _tr_onoff(store, "get_status", _a(answers, 0))
    return "Enter TR069 status (0=Disable, 1=Enable): " + _status("cmd_tr069_set_status", "TR069")


@cli_handler("tr069_cli", "set_preserve_tr069_config")
def _tr_preserve(store, answers, args):
    _tr_onoff(store, "get_preserve_tr069_config", _a(answers, 0))
    return _status("cmd_set_preserve_tr069_config", "TR069")


@cli_handler("tr069_cli", "set_acs_server_config")
def _tr_server(store, answers, args):
    u, p, url, port, iface, ipt = (_a(answers, i, "") for i in range(6))
    store.set_text("tr069_cli", "get_acs_server_config", _cmd_head("get_acs_server_config") +
                   _status("cmd_tr069_get_acs_server_config", "TR069") +
                   "TR069 Server UserName : [%s]\nTR069 Server Password : [%s]\nTR069 Server URL : [%s]\n"
                   "TR069 Server Port Number : [%s]\nTR069 Cellular WAN-Interface Type : [%s]\nTR069 Server IP Type : [%s]\n"
                   % (u, p, url, port, iface, ipt))
    # like the real CLI, the output echoes the password (the service must never return it)
    return ("username=[%s], password=[%s], URL=[%s], port=[%s], Interface=[%s], IPType=[%s]\n" % (u, p, url, port, iface, ipt) +
            _status("cmd_tr069_set_acs_server_config", "TR069"))


@cli_handler("tr069_cli", "set_acs_client_config")
def _tr_client(store, answers, args):
    u, p, port, conn, mode, pii, ver, lvl = (_a(answers, i, "") for i in range(8))
    store.set_text("tr069_cli", "get_acs_client_config", _cmd_head("get_acs_client_config") +
                   _status("cmd_tr069_get_acs_client_config", "TR069") +
                   "TR069 Client UserName : [%s]\nTR069 Client Password : [%s]\nTR069 Client Port Number: [%s]\n"
                   "TR069 Client Connection Request : [%s]\nTR069 Client Authenticate Mode: [%s]\n"
                   "TR069 Client Time Interval(secs) : [%s]\nTR069 Client CWMP Version : [%s]\nTR069 Client Log Level : [%s]\n"
                   % (u, p, port, conn, mode, pii, ver, lvl))
    return ("username=[%s], password=[%s], Port Number=[%s]\n" % (u, p, port) + _status("cmd_tr069_set_acs_client_config", "TR069"))


@cli_handler("tr069_cli", "set_xmpp_config")
def _tr_xmpp(store, answers, args):
    u, p, url, jid, port, en = (_a(answers, i, "") for i in range(6))
    store.set_text("tr069_cli", "get_xmpp_config", _cmd_head("get_xmpp_config") + _status("cmd_tr069_get_xmpp_config", "TR069") +
                   "TR069 XMPP UserName : [%s]\nTR069 XMPP Password : [%s]\nTR069 XMPP URL: [%s]\nTR069 XMPP Jabber ID : [%s]\n"
                   "TR069 XMPP Port Number: [%s]\nTR069 XMPP Feature(0:Disable,1: Enabled) : [%s]\n" % (u, p, url, jid, port, en))
    return ("username=[%s], password=[%s]\n" % (u, p)) + _status("cmd_tr069_set_xmpp_config", "TR069")


@cli_handler("tr069_cli", "tr069_restart")
def _tr_restart(store, answers, args):
    store.state["tr069_restarted"] = time.time()
    return _status("cmd_tr069_restart", "TR069")


# ------------------------------------------------------------------------------------------ watchdog / health
@cli_handler("watchdog_cli", "set_settings")
def _wd_set(store, answers, args):
    _sub(store, "watchdog_cli", "get_settings", _br("enabled"), int(_a(answers, 0)))
    _sub(store, "watchdog_cli", "get_settings", _br("interval"), int(_a(answers, 1)))
    _rest_set(store, "WatchdogSettings", {"WatchdogInterval": int(_a(answers, 1))})
    return _status("watchdog_set_settings", "WATCHDOG")


@rest_handler("PUT", "WatchdogSettings")
def _rest_wd(rest, body):
    rest.data.setdefault("WatchdogSettings", {}).update(body or {})
    st = _store(rest)
    if st is not None and "WatchdogInterval" in (body or {}):
        _sub(st, "watchdog_cli", "get_settings", _br("interval"), int(body["WatchdogInterval"]))
    return None


def _health_table(cmd, labels):
    def h(store, answers, args):
        for i, lb in enumerate(labels):
            _sub(store, "mifi_health_cli", cmd, _br(lb), int(_a(answers, i)))
        return _status("mifi_health_" + cmd.replace("get_", "set_"), "MIFI_HEALTH")
    return h


cli_handler("mifi_health_cli", "set_lowmem_reboot_settings")(_health_table(
    "get_lowmem_reboot_settings", ["mem_threshold", "check_interval", "min_uptime", "reboot_time_window_open",
                                   "reboot_time_window_end"]))
cli_handler("mifi_health_cli", "set_mem_diag_settings")(_health_table(
    "get_mem_diag_settings", ["MaxLogFileSize", "MaxNoOfBackups", "SafeZoneLeastMem", "CriticalZoneStartMem",
                              "SafeZonePollIntervalSec", "ModerateZonePollIntervalSec", "CriticalZonePollIntervalSec",
                              "SafeZoneStoreMemStatsIntervalMins", "ModerateZoneStoreMemStatsIntervalMins",
                              "CriticalZoneStoreMemStatsIntervalMins", "EnableDebugMode", "TopCPUUsageThreshold"]))
cli_handler("mifi_health_cli", "set_log_settings")(_health_table("get_log_settings", ["enabled", "upload_interval"]))
