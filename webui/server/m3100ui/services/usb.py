"""USB: tethering, Wi-Fi while tethered, default USB mode (standard / debug with ADB), immediate mode
switch, and the connection manager's fixed IP for the tethered/Ethernet host.

- usb_cli get_usb_tethering_feature / get_tether_enabled / set_tether_enabled, get_state, get_config,
  mode_switch (only modes that keep the USB network interface: 0 standard, 1 debug).
- ccm2_cli get/set_wifi_while_usbteth, get/set_static_ip.
- dsm_cli get/set_default_usb_mode (0 = debug, 1 = end user); the mod ROMs set 0 to keep ADB.
The stock device_preferences "USB mode" select maps to tethering + Wi-Fi-while-tethered:
charge only = tethering off; USB + Wi-Fi = on/on; USB only = on/off.
"""
import ipaddress
import re
from typing import Any, Dict, List

from .. import validate
from ..errors import field_error, unavailable

RESET_RE = re.compile(r"reset is required|requires reset|reboot the device", re.I)
MODES = {"debug": 0, "standard": 1}               # dsm_cli default USB mode
SWITCH_CODES = {"standard": 0, "debug": 1}        # usb_cli mode_switch (0=EUM, 1=DBG)
MODE_TEXT = {"debug": "Debug: network + ADB, diagnostics, serial", "standard": "Standard: network + USB storage"}


def funcs(v: str) -> List[str]:
    return [f for f in (v or "").split(",") if f]


def parse_usb_state(res) -> Dict[str, Any]:
    """usb_cli get_state: key=[value][label] lines."""
    if res is None:
        return {}
    vals = res.all("usb_default_mode")
    start = res.all("start_mode")
    return {
        "default_mode_code": int(vals[0]) if vals and vals[0].isdigit() else None,
        "default_mode_label": vals[1] if len(vals) > 1 else "",
        "start_mode": start[1] if len(start) > 1 else (start[0] if start else ""),
        "net_if": res.get("net_if_name", ""), "net_function": res.get("net_if_func_name", ""),
        "pid": res.get("pid", ""), "functions": funcs(res.get("function_list", "")),
        "configured": res.get("usb_gadget_configured") == "1",
        "tether_change_pending": res.get("usb_tether_change_pending") == "1",
        "enterprise_mode": res.get("enterprise_mode_enabled") == "1",
        "mac_host": res.get("mac_addr_host", ""), "mac_device": res.get("mac_addr_dev", ""),
    }


def parse_usb_config(res) -> Dict[str, Any]:
    """usb_cli get_config: per-mode function lists (leading ',' = the network function is inserted)."""
    if res is None:
        return {}
    nw = res.all("nwif_mode")
    return {"debug": funcs(res.get("dm_olm_nt_func", "") or res.get("dm_olm_func", "")),
            "standard": funcs(res.get("eum_olm_func", "")), "enterprise": funcs(res.get("ent_olm_func", "")),
            "network_mode": nw[-1] if nw else ""}


def run_set(app, cli, cmd, answers, what) -> bool:
    res = app.cli.run(cli, cmd, answers)
    if res.ok:
        return bool(RESET_RE.search(res.text or ""))
    if res.rc == 6 or (res.message and "reset is required" in res.message.lower()):
        return True
    res.raise_for_error(what)
    return False


def register(app):
    r = app.router

    def state() -> Dict[str, Any]:
        feat = app.cli_try("usb_cli", "get_usb_tethering_feature")
        teth = app.cli_try("usb_cli", "get_tether_enabled")
        if feat is None and teth is None:
            raise unavailable("USB settings are not available right now")
        wwt = app.cli_try("ccm2_cli", "get_wifi_while_usbteth")
        dm = app.cli_try("dsm_cli", "get_default_usb_mode")
        sip = app.cli_try("ccm2_cli", "get_static_ip")
        mac = app.cli_try("dsm_cli", "get_usb_mac_address")
        cnt = app.rest_try("ConnectedDevicesStatus", {}) or {}
        code = dm.get_int("USB mode") if dm else None
        st = parse_usb_state(app.cli_try("usb_cli", "get_state"))
        static_ip = sip.get("ip", "") if sip else None
        return {
            "tethering_supported": bool(feat.get_int("usb_tethering_feature", 0)) if feat else None,
            "tethering_enabled": bool(teth.get_int("tether_enabled", 0)) if teth else None,
            "wifi_while_tethered": bool(wwt.get_int("enabled", 0)) if wwt else None,
            "default_mode": {0: "debug", 1: "standard"}.get(code) if code is not None else None,
            "modes": MODE_TEXT,
            "state": st,
            "compositions": parse_usb_config(app.cli_try("usb_cli", "get_config")),
            "static_ip": "" if static_ip == "0.0.0.0" else static_ip,
            "mac_host": (mac.get("USB_Host", "") if mac else "") or st.get("mac_host", ""),
            "mac_device": (mac.get("USB_Device", "") if mac else "") or st.get("mac_device", ""),
            "clients": cnt.get("ConnectedDevicesCountUSB"),
        }

    @r.route("GET", "/api/usb")
    def get_usb(req):
        """USB tethering, USB mode and gadget state."""
        return state()

    @r.route("PUT", "/api/usb")
    def put_usb(req):
        """USB tethering on/off, keep Wi-Fi on while tethered, fixed host IP."""
        data = validate.only_keys(req.json, ["tethering_enabled", "wifi_while_tethered", "static_ip"])
        cur = state()
        if "tethering_enabled" in data:
            v = validate.boolean("tethering_enabled", data["tethering_enabled"])
            if cur.get("tethering_supported") is False and v:
                raise field_error("tethering_enabled", "USB tethering is not supported on this unit")
            if v != cur.get("tethering_enabled"):
                run_set(app, "usb_cli", "set_tether_enabled", [1 if v else 0], "Changing USB tethering")
        if "wifi_while_tethered" in data:
            v = validate.boolean("wifi_while_tethered", data["wifi_while_tethered"])
            if v != cur.get("wifi_while_tethered"):
                run_set(app, "ccm2_cli", "set_wifi_while_usbteth", [1 if v else 0], "Changing Wi-Fi while tethered")
        if "static_ip" in data:
            raw = data["static_ip"]
            ip = "0.0.0.0" if raw in ("", None) else validate.ipv4("static_ip", raw)
            if ip != "0.0.0.0":
                a = ipaddress.IPv4Address(ip)
                if a.is_multicast or a.is_loopback or a.is_unspecified or str(a) == "255.255.255.255":
                    raise field_error("static_ip", "This address cannot be used")
            if ip != (cur.get("static_ip") or "0.0.0.0"):
                run_set(app, "ccm2_cli", "set_static_ip", [ip], "Saving the fixed host IP")
        app.cache.invalidate("status")
        app.audit(req, "usb.update", {"keys": sorted(data)})
        return state()

    @r.route("PUT", "/api/usb/default-mode")
    def put_default_mode(req):
        """USB mode used after every start: standard, or debug with ADB/diagnostic ports (tier 2)."""
        app.require_tier(req, 2)
        data = validate.only_keys(req.json, ["mode"])
        mode = validate.choice("mode", data.get("mode"), list(MODES))
        cur = state()
        if cur.get("default_mode") is not None:
            app.state.snapshot("usb_default_mode", cur["default_mode"])
        run_set(app, "dsm_cli", "set_default_usb_mode", [MODES[mode]], "Saving the default USB mode")
        app.audit(req, "usb.default_mode", {"mode": mode})
        out = state()
        out["restart_required"] = True
        return out

    @r.route("POST", "/api/usb/mode-switch")
    def mode_switch(req):
        """Switch the USB mode now without a restart (USB devices reconnect; tier 2)."""
        app.require_tier(req, 2)
        data = validate.only_keys(req.json, ["mode"])
        mode = validate.choice("mode", data.get("mode"), list(SWITCH_CODES))
        run_set(app, "usb_cli", "mode_switch", [SWITCH_CODES[mode], 0], "Switching the USB mode")
        app.audit(req, "usb.mode_switch", {"mode": mode})
        return state()

    app.features.add("network.usb", lambda: app.cli_try("usb_cli", "get_tether_enabled") is not None)
