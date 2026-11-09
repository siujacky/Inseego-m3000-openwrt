"""Device area: battery & power save, battery preservation, universal charging, display & touchscreen,
LED, buzzer test, temperature & health (thermal, SAR, memory/CPU), watchdog and mifi_health settings.

Data sources (see docs/features/device.md for the per-field map):
- REST BatteryStatus, PowerSaveSettings, BatteryPreservationMode/*, WatchdogSettings, WiFi/DisplaySettings
- powersave_cli / dsm_cli / devui_cli / led_cli / sar_cli / thermal_monitor_cli / watchdog_cli /
  mifi_health_cli reads, setters only through Specs in adapters/specs/device.py
- sysintcli sys_* setters for power save (they get-modify-set, preserving SuspendEnabled etc.)
- app.system: power_supplies(), thermal(), meminfo(), loadavg(), cpu(), uptime()
"""
import re
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import ApiError, field_error, unavailable

SCREEN_TIMEOUTS = [30, 60, 120, 300, 600, 28800, 0]   # devui enum index -> seconds (devui_cli table @0x4994)


# ---------------------------------------------------------------------------------------------- helpers
def flag(v: Any) -> Optional[bool]:
    """'1'/'0'/'on'/'off'/'Enable'/'Disable'/'true' -> bool; anything else (e.g. 'redacted') -> None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "on", "true", "yes", "enable", "enabled"):
        return True
    if s in ("0", "off", "false", "no", "disable", "disabled"):
        return False
    return None


def to_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(str(v).strip(), 10)
    except (TypeError, ValueError):
        return default


def to_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def b01(v: bool) -> str:
    return "1" if v else "0"


_SYSINT_RC_RE = re.compile(r"\b(sys_\w+) returned (-?\d+)")


def sysint_rc(text: str) -> Optional[int]:
    """sysintcli always exits 0 (main @0xad10 returns 0); the result is the last 'sys_X returned N' line."""
    hits = _SYSINT_RC_RE.findall(text or "")
    return int(hits[-1][1]) if hits else None


def run_setter(app, cli: str, cmd: str, answers=(), what: str = "") -> Any:
    """Run a Spec'd vendor setter; vendor failures become 502 with a readable message."""
    res = app.cli.run(cli, cmd, [str(a) for a in answers], check=False, what=what)
    label = what or "%s %s" % (cli, cmd)
    if cli == "sysintcli":
        rc = sysint_rc(res.text)
        if res.timed_out or rc is None or rc != 0 or "Unknown cmd" in res.text:
            from ..errors import vendor_error
            raise vendor_error("%s failed: %s" % (label, "timed out" if res.timed_out else
                                                  ("vendor code %s" % rc if rc is not None else "no result")),
                               {"cli": cli, "command": cmd, "rc": rc})
        return res
    res.raise_for_error(label)
    return res


def mock_link(app) -> None:
    """--mock only: let the mock REST and CLI handlers of this area see each other (keeps reads coherent)."""
    if app.mock:
        try:
            app.rest._device_cli_store = app.cli.store
            app.cli.store.state["_device_rest"] = app.rest
        except AttributeError:
            pass


# ---------------------------------------------------------------------------------------------- parsers
def parse_powersave(res) -> Dict[str, Any]:
    g = res.get_int
    return {"enabled": bool(g("settings.enabled", 0)), "deep_sleep_enabled": bool(g("settings.deep_sleep_enabled", 0)),
            "deep_sleep_idle_s": g("settings.deep_sleep_idle_time", 0),
            "deep_sleep_wakeup_s": g("settings.deep_sleep_wakeup_time", 0),
            "auto_shutdown_enabled": bool(g("settings.shutdown_enabled", 0)),
            "auto_shutdown_idle_s": g("settings.shutdown_idle_time", 0)}


def parse_dsm_state(res) -> Dict[str, Any]:
    out = {}
    for k in ("power_on_cause", "power_state", "charger_det", "battery_det", "battery_state", "battery_voltage_state",
              "battery_temperature_state", "usb_det", "usb_state", "dcp_det", "battery_pack_state", "power_button",
              "display_state", "typec_pwr_role", "usb_device_type"):
        v = res.get(k)
        if v is not None:
            out[k] = v
    for k in ("battery_capacity", "battery_current", "battery_temperature", "battery_voltage", "battery_id"):
        v = res.get_int(k)
        if v is not None:
            out[k] = v
    return out


def parse_dsm_settings(res) -> Dict[str, Any]:
    return {"critical_battery_pct": res.get_int("settings.critical_battery_threshold"),
            "battery_pack_active": res.get_int("settings.battery_pack_active"),
            "battery_pack_low_pct": res.get_int("settings.battery_pack_low_battery_threshold")}


_BH_RE = re.compile(r"Duration\s*\(in seconds\)\s*:\s*\[(\d+)\]|Min\.?\s*Charge threshold\s*:\s*\[(\d+)\]|"
                    r"Max\.?\s*Charge threshold\s*:\s*\[(\d+)\]", re.I)


def parse_battery_health_capability(text: str) -> Dict[str, Optional[int]]:
    out = {"duration_s": None, "min_pct": None, "max_pct": None}
    for m in _BH_RE.finditer(text or ""):
        if m.group(1):
            out["duration_s"] = int(m.group(1))
        elif m.group(2):
            out["min_pct"] = int(m.group(2))
        elif m.group(3):
            out["max_pct"] = int(m.group(3))
    return out


_TZ_RE = re.compile(r"\btz(\d+)\s+(-?\d+)")


def parse_thermal_report(text: str) -> List[Dict[str, Any]]:
    """thermal_monitor_cli report: 'thermal_zones - tz0 43356' (milli-degrees C)."""
    out = []
    for m in _TZ_RE.finditer(text or ""):
        v = int(m.group(2))
        out.append({"zone": "tz" + m.group(1), "celsius": round(v / 1000.0, 1) if abs(v) > 300 else float(v)})
    return out


_SAR_RE = re.compile(r"capacitive\s+(\w+)\s+enabled\s*:\s*\[(\d+)\]", re.I)


def parse_sar(text: str) -> Dict[str, bool]:
    return {m.group(1): m.group(2) == "1" for m in _SAR_RE.finditer(text or "")}


_LIST_NAME_RE = re.compile(r"list\[(\d+)\]\.name:\[(.*?)\]")


def parse_watchdog_apps(text: str) -> List[str]:
    items = sorted(((int(m.group(1)), m.group(2)) for m in _LIST_NAME_RE.finditer(text or "")))
    return [n for _i, n in items]


def devui_value(res, *labels) -> Optional[str]:
    if res is None:
        return None
    for lb in labels:
        v = res.get(lb)
        if v is not None:
            return v
    return None


# ---------------------------------------------------------------------------------------------- service
def register(app):
    r = app.router
    mock_link(app)

    def cli_get(cli, cmd):
        return app.cli_try(cli, cmd)

    # ------------------------------------------------------------------ battery
    def battery() -> Dict[str, Any]:
        bat = app.rest_try("BatteryStatus", {}) or {}
        st = cli_get("dsm_cli", "get_state")
        dsm = parse_dsm_state(st) if st else {}
        supplies = []
        try:
            supplies = app.system.power_supplies()
        except OSError:
            supplies = []
        sb = next((s for s in supplies if str(s.get("type", "")).lower() == "battery"), {})
        percent = bat.get("BatteryChargeLevelPercentage")
        if percent is None:
            percent = dsm.get("battery_capacity", sb.get("capacity"))
        volt = dsm.get("battery_voltage") or sb.get("voltage_now")
        cur = dsm.get("battery_current")
        if cur is None:
            cur = sb.get("current_now")
        temp = dsm.get("battery_temperature")
        if temp is None:
            temp = sb.get("temp")
        design = sb.get("charge_full_design")
        charging = bat.get("BatteryChargingState")
        if charging is None:
            charging = str(sb.get("status", "")).lower() == "charging"
        return {
            "present": bool(bat.get("BatteryPresent", dsm.get("battery_det", "true") == "true")),
            "percent": percent, "charging": bool(charging), "source": bat.get("BatteryChargingSource", ""),
            "state": dsm.get("battery_state", sb.get("status", "")), "health": sb.get("health", ""),
            "voltage_v": round(volt / 1e6, 3) if isinstance(volt, int) and volt > 1000 else None,
            "current_ma": round(cur / 1000.0, 1) if isinstance(cur, int) else None,
            "temperature_c": round(temp / 10.0, 1) if isinstance(temp, int) else None,
            "design_capacity_mah": int(design / 1000) if isinstance(design, int) and design > 0 else None,
            "charger": dsm.get("charger_det", ""), "power_state": dsm.get("power_state", ""),
            "power_on_cause": dsm.get("power_on_cause", ""), "voltage_state": dsm.get("battery_voltage_state", ""),
            "temperature_state": dsm.get("battery_temperature_state", ""), "usb": dsm.get("usb_det", ""),
            "usb_state": dsm.get("usb_state", ""), "typec_role": dsm.get("typec_pwr_role", ""),
            "battery_pack_state": dsm.get("battery_pack_state", ""), "dsm_available": bool(st),
            "supplies": supplies,
        }

    @r.route("GET", "/api/device/battery")
    def get_battery(req):
        """Battery status and charging details (REST BatteryStatus + dsm_cli get_state + sysfs)."""
        return app.cache.get("device:battery", 5, battery)

    # ------------------------------------------------------------------ power save
    def powersave() -> Dict[str, Any]:
        res = cli_get("powersave_cli", "get_settings")
        rest = app.rest_try("PowerSaveSettings")
        if res:
            out = parse_powersave(res)
        elif isinstance(rest, dict):
            out = {"enabled": bool(rest.get("PowerSaveEnabled")), "deep_sleep_enabled": None,
                   "deep_sleep_idle_s": rest.get("PowerSaveDeepSleepIdleDuration"),
                   "deep_sleep_wakeup_s": rest.get("PowerSaveDeepSleepMaxDuration"),
                   "auto_shutdown_enabled": None, "auto_shutdown_idle_s": None}
        else:
            raise unavailable("Power-save settings are not available on this unit")
        if isinstance(rest, dict):   # REST is authoritative for the three fields it carries
            out["enabled"] = bool(rest.get("PowerSaveEnabled", out["enabled"]))
            out["deep_sleep_idle_s"] = rest.get("PowerSaveDeepSleepIdleDuration", out["deep_sleep_idle_s"])
            out["deep_sleep_wakeup_s"] = rest.get("PowerSaveDeepSleepMaxDuration", out["deep_sleep_wakeup_s"])
        out["rest"] = isinstance(rest, dict)
        out["service_running"] = bool(cli_get("powersave_cli", "is_running"))
        return out

    @r.route("GET", "/api/device/powersave")
    def get_powersave(req):
        """Power save: deep sleep (idle time, wake-up timer) and automatic shutdown."""
        return powersave()

    def apply_powersave(req, data: Dict[str, Any]) -> None:
        cur = powersave()
        new = dict(cur)
        if "enabled" in data:
            new["enabled"] = validate.boolean("enabled", data["enabled"])
        if "deep_sleep_enabled" in data:
            new["deep_sleep_enabled"] = validate.boolean("deep_sleep_enabled", data["deep_sleep_enabled"])
        if "auto_shutdown_enabled" in data:
            new["auto_shutdown_enabled"] = validate.boolean("auto_shutdown_enabled", data["auto_shutdown_enabled"])
        if "deep_sleep_idle_s" in data:
            new["deep_sleep_idle_s"] = validate.integer("deep_sleep_idle_s", data["deep_sleep_idle_s"], 60, 86400)
        if "deep_sleep_wakeup_s" in data:
            v = validate.integer("deep_sleep_wakeup_s", data["deep_sleep_wakeup_s"], 0, 86400)
            if 0 < v < 3600:
                raise field_error("deep_sleep_wakeup_s", "Choose Never or at least 1 hour")
            new["deep_sleep_wakeup_s"] = v
        if "auto_shutdown_idle_s" in data:
            new["auto_shutdown_idle_s"] = validate.integer("auto_shutdown_idle_s", data["auto_shutdown_idle_s"], 3600, 86400)
        if new.get("deep_sleep_enabled") and not (new.get("deep_sleep_idle_s") or 0) >= 60:
            raise field_error("deep_sleep_idle_s", "Choose how long the router waits before deep sleep")
        if new.get("auto_shutdown_enabled") and not (new.get("auto_shutdown_idle_s") or 0) >= 3600:
            raise field_error("auto_shutdown_idle_s", "Choose how long the router waits before shutting down")
        if (new.get("deep_sleep_enabled") and new.get("auto_shutdown_enabled")
                and new["auto_shutdown_idle_s"] < new["deep_sleep_idle_s"]):
            # stock rule (device_preferences ValidateFormData): shutdown time must not be shorter than deep sleep
            raise field_error("auto_shutdown_idle_s", "Automatic shutdown must not happen before deep sleep")
        times_changed = [k for k in ("deep_sleep_idle_s", "deep_sleep_wakeup_s", "auto_shutdown_idle_s")
                         if k in data and new[k] != cur.get(k)]
        # 1) switch things off first
        for key, cmd in (("deep_sleep_enabled", "setDeepSleepEnabled"), ("auto_shutdown_enabled", "setAutoShutdownEnabled")):
            if key in data and cur.get(key) is not False and new[key] is False:
                run_setter(app, "sysintcli", cmd, ["0"], "Turning off " + key.replace("_", " "))
        # 2) times
        if times_changed:
            if cur["rest"] and "auto_shutdown_idle_s" not in times_changed:
                app.rest.merge_put("PowerSaveSettings", {"PowerSaveDeepSleepIdleDuration": int(new["deep_sleep_idle_s"] or 0),
                                                         "PowerSaveDeepSleepMaxDuration": int(new["deep_sleep_wakeup_s"] or 0)})
            else:
                run_setter(app, "sysintcli", "setPowerSaveSettings",
                           [int(new["deep_sleep_idle_s"] or 0), int(new["deep_sleep_wakeup_s"] or 0),
                            int(new["auto_shutdown_idle_s"] or 0)], "Saving power-save times")
        # 3) master switch
        if "enabled" in data and new["enabled"] != cur.get("enabled"):
            if cur["rest"]:
                app.rest.merge_put("PowerSaveSettings", {"PowerSaveEnabled": bool(new["enabled"])})
            else:
                run_setter(app, "sysintcli", "setPowerSaveEnabled", [b01(new["enabled"])], "Saving power save")
        # 4) switch things on last (times are already in place)
        for key, cmd in (("deep_sleep_enabled", "setDeepSleepEnabled"), ("auto_shutdown_enabled", "setAutoShutdownEnabled")):
            if key in data and cur.get(key) is not True and new[key] is True:
                run_setter(app, "sysintcli", cmd, ["1"], "Turning on " + key.replace("_", " "))
        app.cache.invalidate("device:")
        app.audit(req, "device.powersave.update", {"keys": sorted(data)})

    @r.route("PUT", "/api/device/powersave")
    def put_powersave(req):
        """Change power save (deep sleep, wake-up timer, automatic shutdown)."""
        data = validate.only_keys(req.json, ["enabled", "deep_sleep_enabled", "deep_sleep_idle_s", "deep_sleep_wakeup_s",
                                             "auto_shutdown_enabled", "auto_shutdown_idle_s"])
        apply_powersave(req, data)
        return powersave()

    @r.route("POST", "/api/device/powersave/never-sleep")
    def never_sleep(req):
        """Router mode: never deep-sleep and never shut down automatically."""
        apply_powersave(req, {"deep_sleep_enabled": False, "auto_shutdown_enabled": False})
        return powersave()

    # ------------------------------------------------------------------ battery preservation (battery health mode)
    def preservation() -> Dict[str, Any]:
        en = app.rest_try("BatteryPreservationMode/Enabled")
        stat = app.rest_try("BatteryPreservationMode/Status")
        cap = app.rest_try("BatteryPreservationMode/Capabilities")
        out = {"supported": False, "enabled": None, "status": None, "min_pct": None, "max_pct": None,
               "duration_s": None, "rest": isinstance(en, dict)}
        if isinstance(en, dict):
            out.update(supported=True, enabled=bool(en.get("Enabled")))
        else:
            res = cli_get("dsm_cli", "get_battery_health_enabled")
            if res:
                v = res.get("Battery Health Mode")
                out.update(supported=v is not None, enabled=flag(v))
        if isinstance(stat, dict):
            out["status"] = stat.get("Status")
        if isinstance(cap, dict):
            out.update(min_pct=cap.get("MinimumBatteryThreshold"), max_pct=cap.get("MaximumBatteryThreshold"),
                       duration_s=cap.get("Time"))
        else:
            res = cli_get("dsm_cli", "get_battery_health_capability")
            if res:
                out.update(parse_battery_health_capability(res.text))
        return out

    @r.route("GET", "/api/device/battery-preservation")
    def get_preservation(req):
        """Battery preservation (health) mode: keeps the charge between the vendor thresholds."""
        return preservation()

    @r.route("PUT", "/api/device/battery-preservation")
    def put_preservation(req):
        """Turn battery preservation mode on or off (thresholds are fixed by the vendor)."""
        data = validate.only_keys(req.json, ["enabled"])
        if "enabled" not in data:
            raise field_error("enabled", "Nothing to change")
        v = validate.boolean("enabled", data["enabled"])
        cur = preservation()
        if not cur["supported"]:
            raise unavailable("Battery preservation is not supported on this unit")
        if cur["rest"]:
            app.rest.merge_put("BatteryPreservationMode/Enabled", {"Enabled": v})
        else:
            run_setter(app, "dsm_cli", "set_battery_health_enabled", [b01(v)], "Saving battery preservation")
        app.audit(req, "device.battery_preservation.update", {"enabled": v})
        return preservation()

    # ------------------------------------------------------------------ universal charging (charge other devices)
    def universal() -> Dict[str, Any]:
        res = cli_get("dsm_cli", "get_settings")
        if not res:
            return {"supported": False}
        s = parse_dsm_settings(res)
        st = cli_get("dsm_cli", "get_state")
        return {"supported": s["battery_pack_low_pct"] is not None, "enabled": bool(s["battery_pack_active"]),
                "threshold_pct": s["battery_pack_low_pct"], "critical_battery_pct": s["critical_battery_pct"],
                "state": (st.get("battery_pack_state") if st else "") or ""}

    @r.route("GET", "/api/device/universal-charging")
    def get_universal(req):
        """Charging other devices from the router battery (power bank) and its low-battery stop threshold."""
        return universal()

    @r.route("PUT", "/api/device/universal-charging")
    def put_universal(req):
        """Change power-bank charging (on/off, stop threshold 5/25/50 %)."""
        data = validate.only_keys(req.json, ["enabled", "threshold_pct"])
        cur = universal()
        if not cur.get("supported"):
            raise unavailable("Charging other devices is not supported on this unit")
        if "threshold_pct" in data:
            t = validate.choice("threshold_pct", validate.integer("threshold_pct", data["threshold_pct"], 0, 100), [5, 25, 50])
            if t != cur["threshold_pct"]:
                run_setter(app, "sysintcli", "setUniversalChargingSettings", [t], "Saving the charging threshold")
        if "enabled" in data:
            v = validate.boolean("enabled", data["enabled"])
            if v != cur["enabled"]:
                run_setter(app, "sysintcli", "setUniversalChargingEnabled", [b01(v)], "Saving power-bank charging")
        app.audit(req, "device.universal_charging.update", {"keys": sorted(data)})
        return universal()

    # ------------------------------------------------------------------ display / touchscreen
    DEVUI_BOOL = [
        # key, get command, get labels, set command
        ("touch_settings_enabled", "get_display_setting_enabled", ("display_setting_enabled",), "set_display_setting_enabled"),
        ("quickstart_enabled", "get_quickstart_enabled", ("quickstart_enabled",), "set_quickstart_enabled"),
        ("usb_prompt_enabled", "get_usb_connection_prompt_enabled", ("usb_connection_prompt_enabled",),
         "set_usb_connection_prompt_enabled"),
        ("wake_on_notification", "get_notification_display_wakeup_setting_enabled",
         ("deviceui_get_notification_display_wakeup_setting_enabled", "notification_display_wakeup_setting_enabled"),
         "set_notification_display_wakeup_setting_enabled"),
        ("show_data_usage", "get_data_usage_touchscreen_enabled",
         ("deviceui_get_data_usage_touchscreen_enabled", "data_usage_touchscreen_enabled"), "set_data_usage_touchscreen_enabled"),
        ("show_ethernet", "get_display_ethernet_port_enabled",
         ("deviceui_get_display_ethernet_port_enabled", "display_ethernet_port_enabled"), "set_display_ethernet_port_enabled"),
        ("show_file_sharing", "get_file_sharing_display_setting_enabled", ("file_sharing_display_setting_enabled",),
         "set_file_sharing_display_setting_enabled"),
        ("vital_update_prompt", "get_vital_update_prompt_enabled", ("vital_update_prompt_enabled",),
         "set_vital_update_prompt_enabled"),
        ("show_admin_password", "get_admin_password_enabled", ("admin_password_setting_enabled", "admin_password_enabled"),
         "set_admin_password_enabled"),
    ]
    WIFI_DISPLAY = [  # key, REST field (inverted), devui command, index in (name, key)
        ("show_primary_ssid", "HideSSIDTouchEnabledPrimary", "primary", 0),
        ("show_primary_password", "HidePasswordTouchEnabledPrimary", "primary", 1),
        ("show_guest_ssid", "HideSSIDTouchEnabledGuest", "guest", 0),
        ("show_guest_password", "HidePasswordTouchEnabledGuest", "guest", 1),
    ]
    DISPLAY_KEYS = (["screen_timeout_s", "touch_wifi_settings_enabled"] + [k[0] for k in DEVUI_BOOL] +
                    [k[0] for k in WIFI_DISPLAY])

    def devui_info(which: str) -> Dict[str, Optional[bool]]:
        res = cli_get("devui_cli", "get_%s_wifi_info_display_settings" % which)
        if not res:
            return {"name": None, "key": None}
        return {"name": flag(res.get("show_wifi_name_enabled")), "key": flag(res.get("show_wifi_key_enabled"))}

    def display() -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        res = cli_get("devui_cli", "get_screen_timeout_settings")
        out["screen_timeout_s"] = to_int(res.get("screen_timeout")) if res else None
        for key, cmd, labels, _set in DEVUI_BOOL:
            out[key] = flag(devui_value(cli_get("devui_cli", cmd), *labels))
        rest = app.rest_try("WiFi/DisplaySettings")
        if isinstance(rest, dict):
            out["touch_wifi_settings_enabled"] = bool(rest.get("AllowDeviceUIWiFiEnabled", True))
            for key, field, _w, _i in WIFI_DISPLAY:
                out[key] = not bool(rest.get(field, False))
        else:
            out["touch_wifi_settings_enabled"] = flag(devui_value(cli_get("devui_cli", "get_wifi_display_setting_enabled"),
                                                                 "deviceui_get_wifi_display_setting_enabled",
                                                                 "wifi_display_setting_enabled"))
            info = {"primary": devui_info("primary"), "guest": devui_info("guest")}
            for key, _field, which, idx in WIFI_DISPLAY:
                out[key] = info[which]["name" if idx == 0 else "key"]
        out["rest"] = isinstance(rest, dict)
        out["screen_service_running"] = bool(cli_get("devui_cli", "is_running"))
        return out

    @r.route("GET", "/api/device/display")
    def get_display(req):
        """Touchscreen: timeout, what it shows (Wi-Fi name/password, data usage …) and which settings it may change."""
        d = display()
        if d["screen_timeout_s"] is None and all(d.get(k) is None for k, _c, _l, _s in DEVUI_BOOL):
            raise unavailable("The touchscreen settings are not available on this unit")
        return d

    @r.route("PUT", "/api/device/display")
    def put_display(req):
        """Change touchscreen settings."""
        data = validate.only_keys(req.json, DISPLAY_KEYS)
        cur = display()
        if "screen_timeout_s" in data:
            v = validate.integer("screen_timeout_s", data["screen_timeout_s"], 0, 28800)
            if v not in SCREEN_TIMEOUTS:
                raise field_error("screen_timeout_s", "Choose one of the offered timeouts")
            if v != cur["screen_timeout_s"]:
                run_setter(app, "devui_cli", "set_screen_timeout_settings", [SCREEN_TIMEOUTS.index(v)], "Saving the screen timeout")
        for key, _cmd, _labels, setcmd in DEVUI_BOOL:
            if key in data:
                v = validate.boolean(key, data[key])
                if v != cur.get(key):
                    run_setter(app, "devui_cli", setcmd, [b01(v)], "Saving " + key.replace("_", " "))
        wifi_keys = [k for k in [w[0] for w in WIFI_DISPLAY] + ["touch_wifi_settings_enabled"] if k in data]
        if wifi_keys:
            want = {k: validate.boolean(k, data[k]) for k in wifi_keys}
            if cur["rest"]:
                ch = {}
                for key, field, _w, _i in WIFI_DISPLAY:
                    if key in want and want[key] != cur.get(key):
                        ch[field] = not want[key]
                if "touch_wifi_settings_enabled" in want and want["touch_wifi_settings_enabled"] != cur.get("touch_wifi_settings_enabled"):
                    ch["AllowDeviceUIWiFiEnabled"] = want["touch_wifi_settings_enabled"]
                if ch:
                    app.rest.merge_put("WiFi/DisplaySettings", ch)
            else:
                for which in ("primary", "guest"):
                    kn, kk = "show_%s_ssid" % which, "show_%s_password" % which
                    if kn in want or kk in want:
                        name = want.get(kn, cur.get(kn))
                        key_ = want.get(kk, cur.get(kk))
                        if name is None or key_ is None:
                            raise unavailable("Current touchscreen Wi-Fi display settings could not be read")
                        run_setter(app, "devui_cli", "set_%s_wifi_info_display_settings" % which, [b01(name), b01(key_)],
                                   "Saving the %s Wi-Fi display" % which)
                if "touch_wifi_settings_enabled" in want and want["touch_wifi_settings_enabled"] != cur.get("touch_wifi_settings_enabled"):
                    run_setter(app, "devui_cli", "set_wifi_display_setting_enabled", [b01(want["touch_wifi_settings_enabled"])],
                               "Saving touchscreen Wi-Fi access")
        app.audit(req, "device.display.update", {"keys": sorted(data)})
        return display()

    # ------------------------------------------------------------------ LED
    def led() -> Dict[str, Any]:
        res = cli_get("led_cli", "get_enabled")
        if not res:
            raise unavailable("The status light cannot be controlled on this unit")
        return {"enabled": flag(res.get("enabled")), "service_running": bool(cli_get("led_cli", "is_running"))}

    @r.route("GET", "/api/device/led")
    def get_led(req):
        """Status light (LED) on/off."""
        return led()

    @r.route("PUT", "/api/device/led")
    def put_led(req):
        """Turn the status light on or off."""
        data = validate.only_keys(req.json, ["enabled"])
        if "enabled" not in data:
            raise field_error("enabled", "Nothing to change")
        v = validate.boolean("enabled", data["enabled"])
        run_setter(app, "led_cli", "set_enabled", [b01(v)], "Saving the status light")
        app.audit(req, "device.led.update", {"enabled": v})
        return led()

    # ------------------------------------------------------------------ buzzer
    @r.route("POST", "/api/device/buzzer")
    def buzzer_on(req):
        """Sound the buzzer briefly (test); short, bounded patterns only."""
        data = validate.only_keys(req.json, ["on_ms", "off_ms", "repeat"])
        on_ms = validate.integer("on_ms", data.get("on_ms", 100), 10, 1000)
        off_ms = validate.integer("off_ms", data.get("off_ms", 100), 10, 1000)
        rep = validate.integer("repeat", data.get("repeat", 1), 1, 5)
        run_setter(app, "buzzer_cli", "on", [on_ms, off_ms, rep], "Sounding the buzzer")
        app.audit(req, "device.buzzer.test", {"on_ms": on_ms, "off_ms": off_ms, "repeat": rep})
        return {"ok": True, "on_ms": on_ms, "off_ms": off_ms, "repeat": rep}

    @r.route("POST", "/api/device/buzzer/stop")
    def buzzer_off(req):
        """Stop the buzzer."""
        run_setter(app, "buzzer_cli", "off", [], "Stopping the buzzer")
        return {"ok": True}

    # ------------------------------------------------------------------ temperature & health
    def health() -> Dict[str, Any]:
        rep = cli_get("thermal_monitor_cli", "report")
        sar = cli_get("sar_cli", "get_sar_settings")
        wd_apps = cli_get("watchdog_cli", "get_app_list")
        fs = cli_get("mifi_health_cli", "get_ab_failsafe_settings")
        zones = []
        try:
            zones = sorted(app.system.thermal(), key=lambda z: -z["celsius"])
        except OSError:
            zones = []
        mem = app.system.meminfo()
        # thermal_monitor_cli/sar_cli print no status line; CliResult treats exit code 0 as ok
        sr = cli_get("sar_cli", "is_running")
        sr_text = sr.text.lower() if sr else ""
        return {
            "thermal": zones,
            "thermal_report": parse_thermal_report(rep.text) if rep else [],
            "sar": parse_sar(sar.text) if sar else {},
            "sar_running": "is running" in sr_text and "not running" not in sr_text,
            "memory": mem, "load": app.system.loadavg(), "cpu": app.system.cpu(), "uptime_s": int(app.system.uptime()),
            "watchdog_apps": parse_watchdog_apps(wd_apps.text) if wd_apps else [],
            "health_running": bool(cli_get("mifi_health_cli", "is_running")),
            "failsafe": {"verify_duration_min": fs.get_int("verify_duration"), "crash_count": fs.get_int("crash_cnt"),
                         "wdt_reboot_count": fs.get_int("wdt_reboot_cnt"),
                         "pdn_retry_count": fs.get_int("pdn_connect_retry_cnt")} if fs else None,
            "failsafe_running": bool(cli_get("mifi_health_cli", "is_ab_failsafe_running")),
        }

    @r.route("GET", "/api/device/health")
    def get_health(req):
        """Temperatures, SAR sensors, memory/CPU and the vendor health monitors."""
        return app.cache.get("device:health", 5, health)

    # ------------------------------------------------------------------ watchdog
    def watchdog() -> Dict[str, Any]:
        res = cli_get("watchdog_cli", "get_settings")
        rest = app.rest_try("WatchdogSettings")
        if not res and not isinstance(rest, dict):
            raise unavailable("Watchdog settings are not available on this unit")
        out = {"enabled": flag(res.get("enabled")) if res else None,
               "interval_s": res.get_int("interval") if res else None, "rest": isinstance(rest, dict)}
        if isinstance(rest, dict) and rest.get("WatchdogInterval") is not None:
            out["interval_s"] = rest.get("WatchdogInterval")
        out["service_running"] = bool(cli_get("watchdog_cli", "is_running"))
        return out

    @r.route("GET", "/api/device/watchdog")
    def get_watchdog(req):
        """Application watchdog (restarts the router when a vendor service hangs)."""
        return watchdog()

    @r.route("PUT", "/api/device/watchdog")
    def put_watchdog(req):
        """Change the watchdog; switching it off needs confirmation."""
        data = validate.only_keys(req.json, ["enabled", "interval_s"])
        cur = watchdog()
        en = validate.boolean("enabled", data["enabled"]) if "enabled" in data else cur["enabled"]
        iv = validate.integer("interval_s", data["interval_s"], 30, 3600) if "interval_s" in data else cur["interval_s"]
        if "enabled" in data and en != cur["enabled"]:
            if not en:
                app.require_tier(req, 2)
            if iv is None:
                raise field_error("interval_s", "Enter the watchdog interval")
            run_setter(app, "watchdog_cli", "set_settings", [b01(en), iv], "Saving the watchdog")
        elif "interval_s" in data and iv != cur["interval_s"]:
            if cur["rest"]:
                app.rest.merge_put("WatchdogSettings", {"WatchdogInterval": iv})
            else:
                if cur["enabled"] is None:
                    raise unavailable("Current watchdog state could not be read")
                run_setter(app, "watchdog_cli", "set_settings", [b01(cur["enabled"]), iv], "Saving the watchdog")
        app.audit(req, "device.watchdog.update", {"keys": sorted(data)})
        return watchdog()

    # ------------------------------------------------------------------ mifi_health: low-memory reboot, memory diagnostics
    LOWMEM = [("threshold_mb", "mem_threshold", 15, 400), ("check_interval_s", "check_interval", 10, 86400),
              ("min_uptime_h", "min_uptime", 0, 8760), ("window_start_h", "reboot_time_window_open", 0, 23),
              ("window_end_h", "reboot_time_window_end", 0, 23)]
    MEMDIAG = [("max_log_kb", "MaxLogFileSize", 1, 2048), ("max_backups", "MaxNoOfBackups", 0, 100),
               ("safe_zone_mb", "SafeZoneLeastMem", 1, 400), ("critical_zone_mb", "CriticalZoneStartMem", 1, 400),
               ("safe_poll_s", "SafeZonePollIntervalSec", 1, 86400), ("moderate_poll_s", "ModerateZonePollIntervalSec", 1, 86400),
               ("critical_poll_s", "CriticalZonePollIntervalSec", 1, 86400),
               ("safe_store_min", "SafeZoneStoreMemStatsIntervalMins", 1, 1440),
               ("moderate_store_min", "ModerateZoneStoreMemStatsIntervalMins", 1, 1440),
               ("critical_store_min", "CriticalZoneStoreMemStatsIntervalMins", 1, 1440),
               ("debug_mode", "EnableDebugMode", 0, 1), ("top_cpu_threshold", "TopCPUUsageThreshold", 0, 100)]

    def table_get(cmd: str, table) -> Dict[str, Any]:
        res = cli_get("mifi_health_cli", cmd)
        if not res:
            raise unavailable("Health monitor settings are not available on this unit")
        return {k: res.get_int(label) for k, label, _lo, _hi in table}

    def table_put(req, cmd: str, get_cmd: str, table, what: str) -> Dict[str, Any]:
        data = validate.only_keys(req.json, [k for k, _l, _lo, _hi in table])
        cur = table_get(get_cmd, table)
        new = dict(cur)
        for k, _label, lo, hi in table:
            if k in data:
                new[k] = validate.integer(k, data[k], lo, hi)
        missing = [k for k, _l, _lo, _hi in table if new[k] is None]
        if missing:
            raise unavailable("Current value of %s could not be read; refusing a partial write" % missing[0])
        if new != cur:
            run_setter(app, "mifi_health_cli", cmd, [new[k] for k, _l, _lo, _hi in table], what)
        app.audit(req, "device.health.%s" % cmd, {"keys": sorted(data)})
        return table_get(get_cmd, table)

    @r.route("GET", "/api/device/health/lowmem")
    def get_lowmem(req):
        """Low-memory automatic reboot (threshold, check interval, minimum uptime, time window)."""
        return table_get("get_lowmem_reboot_settings", LOWMEM)

    @r.route("PUT", "/api/device/health/lowmem")
    def put_lowmem(req):
        """Change the low-memory reboot settings."""
        return table_put(req, "set_lowmem_reboot_settings", "get_lowmem_reboot_settings", LOWMEM,
                         "Saving low-memory reboot settings")

    @r.route("GET", "/api/device/health/memdiag")
    def get_memdiag(req):
        """Memory diagnostics logging (zones, poll intervals, log size)."""
        return table_get("get_mem_diag_settings", MEMDIAG)

    @r.route("PUT", "/api/device/health/memdiag")
    def put_memdiag(req):
        """Change memory diagnostics logging."""
        return table_put(req, "set_mem_diag_settings", "get_mem_diag_settings", MEMDIAG, "Saving memory diagnostics")

    # ------------------------------------------------------------------ features
    app.features.add("device.display", lambda: app.cli_try("devui_cli", "get_screen_timeout_settings") is not None)
    app.features.add("device.led", lambda: app.cli_try("led_cli", "get_enabled") is not None)
    app.features.add("device.powersave", lambda: app.cli_try("powersave_cli", "get_settings") is not None
                     or app.rest_try("PowerSaveSettings") is not None)
