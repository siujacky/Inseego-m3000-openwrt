"""Dashboard / status-bar aggregate: GET /api/status (polled every few seconds).

Reference service: shows the conventions (cache everything polled, normalise vendor
names, degrade gracefully when a source is missing — e.g. no SIM, no 5G).
"""
import time
from typing import Any, Dict, Optional

TECH_LABELS = {
    "": "none", "unknown": "none", "none": "none", "lte": "LTE", "lte-a": "LTE-A", "ltea": "LTE-A",
    "lte ca": "LTE-A", "5g": "5G", "5g nr": "5G", "nr5g": "5G", "nr": "5G", "umts": "3G", "hspa": "3G",
    "hspa+": "3G", "wcdma": "3G", "gsm": "2G", "edge": "2G", "gprs": "2G", "cdma": "2G", "evdo": "3G",
}


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def nr_mode(nr: Dict[str, Any], lte_tech: str) -> str:
    """'SA', 'NSA' or 'none' from modem2 get_5g_status (mode 0/1/2 = NONE / STAND ALONE / NON-STAND ALONE)."""
    if not nr or not nr.get("service_available"):
        return "none"
    mode = nr.get("mode")
    if mode == 1:
        return "SA"
    if mode == 2:
        return "NSA"
    note = (nr.get("mode_note") or "").upper()
    if "NON-STAND" in note:
        return "NSA"
    if "STAND ALONE" in note:
        return "SA"
    return "NSA" if nr.get("endc") or lte_tech not in ("none",) else "SA"


def register(app):
    r = app.router

    def nr_status() -> Dict[str, Any]:
        res = app.cli_try("modem2_cli", "get_5g_status")
        if not res:
            return {}
        g = res.get_int
        return {
            "service_available": bool(g("service_available", 0)), "data_connected": bool(g("data_connected", 0)),
            "bars": g("bars", 0), "snr": _num(res.get("snr")), "rsrp": _num(res.get("rsrp")), "rsrq": _num(res.get("rsrq")),
            "endc": bool(g("endc", 0)), "dcnr": bool(g("dcnr", 0)), "pci": g("pci", 0), "band": g("band", 0),
            "bandwidth": g("bandwidth", 0), "channel": g("channel", 0), "mode": g("mode", 0),
            "mode_note": res.note("mode") or "", "tx_power": _num(res.get("tx_power")), "num_cc": g("num_cc", 0),
        }

    def gather() -> Dict[str, Any]:
        svc = app.rest_try("CellularServiceStatus", {}) or {}
        data = app.rest_try("CellularDataConnectionStatus", {}) or {}
        cset = app.rest_try("CellularServiceSettings", {}) or {}
        bat = app.rest_try("BatteryStatus", {}) or {}
        cnt = app.rest_try("ConnectedDevicesStatus", {}) or {}
        sim = app.rest_try("SimStatus", {}) or {}
        sms = app.rest_try("SMSStatus", {}) or {}
        adv = app.cache.get("status:wifi_adv", 30, lambda: app.rest_try("WiFi/AdvancedSettings", {}) or {})
        prim = app.cache.get("status:wifi_prim", 30, lambda: app.rest_try("WiFi/PrimarySettings", {}) or {})
        guest = app.cache.get("status:wifi_guest", 30, lambda: app.rest_try("WiFi/GuestSettings", {}) or {})
        nr = nr_status()

        tech_raw = str(svc.get("CurrentWirelessTechnology", "") or "")
        tech = TECH_LABELS.get(tech_raw.strip().lower(), tech_raw or "none")
        nrm = nr_mode(nr, tech)
        if nrm == "SA":
            tech = "5G SA"
        elif nrm == "NSA":
            tech = "5G NSA"
        sim_status = str(sim.get("CurrentSimStatus", "Unknown"))
        wan_state = str(data.get("CurrentStatus", "Unknown"))

        def air():
            r = app.cli_try("ccm2_cli", "get_air_mode_enabled")
            return bool(r and r.get_int("enabled", 0))
        airplane = app.cache.get("status:airplane", 10, air)
        if airplane:
            state = "airplane"
        elif sim_status.lower() in ("notpresent", "absent"):
            state = "no_sim"
        elif wan_state.lower() == "connected":
            state = "connected"
        elif str(svc.get("CurrentServiceStatus", "")).lower() not in ("inservice", "limitedservice"):
            state = "no_service"
        else:
            state = "disconnected"
        temps = sorted(app.system.thermal(), key=lambda z: -z["celsius"])[:4]
        mem = app.system.meminfo()
        clients = {"wifi": cnt.get("ConnectedDevicesCountWiFi", 0), "usb": cnt.get("ConnectedDevicesCountUSB", 0),
                   "ethernet": cnt.get("ConnectedDevicesCountEthernet", 0)}
        clients["total"] = sum(int(v or 0) for v in clients.values())
        dns = [d for d in (data.get("CurrentPrimaryDNSIPv4Address"), data.get("CurrentSecondaryDNSIPv4Address")) if d]
        return {
            "time": int(time.time()),
            "uptime_s": int(app.system.uptime()),
            "connection": {
                "state": state, "tech": tech, "tech_raw": tech_raw, "nr_mode": nrm,
                "operator": svc.get("CurrentCarrierNetworkName", ""), "roaming": svc.get("CurrentRoamingState", "") not in ("", "NotRoaming"),
                "service": svc.get("CurrentServiceStatus", ""), "data_enabled": bool(cset.get("CellularDataEnabled", True)),
            },
            "signal": {
                "bars": svc.get("CurrentSignalStrengthInBars", 0), "rssi": svc.get("CurrentSignalStrengthInRSSI"),
                "rsrp": svc.get("CurrentSignalStrengthInRSRP"), "rsrq": svc.get("CurrentStrengthInRSRQ"),
                "sinr": svc.get("CurrentSignalToNoiseLevelInSINR"), "band": svc.get("CurrentBand", ""),
                "bandwidth_mhz": svc.get("CurrentBandwidth"), "pci": svc.get("PCI"), "cell_id": svc.get("CurrentCellID", ""),
            },
            "nr": nr,
            "wan": {"status": wan_state, "ipv4": data.get("CurrentExternalIPv4Address", ""),
                    "ipv6": data.get("CurrentExternalIPv6Address", ""), "gateway": data.get("CurrentGatewayIPv4Address", ""),
                    "dns": dns, "duration_s": data.get("CurrentCallDuration", 0),
                    "rx_bytes": data.get("CurrentCallBytesReceived", 0), "tx_bytes": data.get("CurrentCallBytesTransmitted", 0)},
            "sim": {"status": sim_status},
            "battery": {"percent": bat.get("BatteryChargeLevelPercentage"), "charging": bool(bat.get("BatteryChargingState")),
                        "source": bat.get("BatteryChargingSource", ""), "present": bool(bat.get("BatteryPresent", True))},
            "clients": clients,
            "wifi": {"enabled": bool(adv.get("WiFiEnabled", False)), "primary_ssid": prim.get("WiFiSSID", ""),
                     "guest_ssid": guest.get("WiFiSSID", ""),
                     "bands": {"2g": adv.get("WiFi2Dot4GBandState", ""), "5g": adv.get("WiFi5GBandState", "")}},
            "sms_unread": sms.get("SMSInboxUnreadCount", 0),
            "temps": temps,
            "memory": {"total": mem["total"], "available": mem["available"]},
            "load": app.system.loadavg(),
        }

    @r.route("GET", "/api/status")
    def status(req):
        """Dashboard + status bar aggregate (cached 2.5 s)."""
        return app.cache.get("status", 2.5, gather)
