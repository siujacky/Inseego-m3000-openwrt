"""Ethernet port: on/off, LAN or WAN role, link state/speed, MAC and the second port when present.

- REST EthernetSettings/Enabled (+ /Details) is the on/off path (stock device_preferences app).
- mifi_qcmap_cli get/set_iface_type (role, reset required), get_iface_link, get_ethernet_info,
  get_ethernet_macaddr, get_iface_type1 / get_iface1_link (eth1; unsupported on this model).
- Kernel link details from /sys/class/net/<if>.
"""
import os
import re
from typing import Any, Dict, Optional

from .. import validate
from ..errors import field_error, unavailable

RESET_RE = re.compile(r"reset is required|requires reset|reboot the device", re.I)
ROLE_BY_LABEL = {"LAN": "lan", "DHCP WAN": "wan_dhcp", "Static WAN": "wan_static", "PPPoE WAN": "wan_pppoe"}
ROLE_BY_CODE = {2: "lan", 3: "wan_dhcp", 4: "wan_static", 5: "wan_pppoe"}
ROLE_CODES = {"lan": 2, "wan_dhcp": 3}           # the only roles the vendor CLI can set
ROLE_TEXT = {"lan": "LAN (local devices)", "wan_dhcp": "WAN (internet from a modem/router, DHCP)",
             "wan_static": "WAN (static IP)", "wan_pppoe": "WAN (PPPoE)"}


def parse_ethernet_info(text: str) -> Dict[str, Any]:
    """mifi_qcmap_cli get_ethernet_info (plain 'label: value' lines, per port)."""
    out: Dict[str, Any] = {"ports": []}
    m = re.search(r"Ethernet Interface Status:\s*(\d+)", text or "")
    out["status"] = int(m.group(1)) if m else None
    m = re.search(r"Support Count For \S+ is\s*(\d+)", text or "")
    out["port_count"] = int(m.group(1)) if m else None
    for name in sorted(set(re.findall(r"^(eth\d+) Interface Type", text or "", re.M))):
        t = re.search(r"^%s Interface Type:\s*(\d+)" % name, text, re.M)
        lk = re.search(r"^%s Link Status:\s*(\d+)" % name, text, re.M)
        mac = re.search(r"^%s MAC Address:\s*([0-9A-Fa-f:]{17})" % name, text, re.M)
        code = int(t.group(1)) if t else None
        out["ports"].append({"name": name, "type_code": code, "role": ROLE_BY_CODE.get(code or 0, "unknown"),
                             "link": bool(int(lk.group(1))) if lk else None,
                             "mac": mac.group(1).upper() if mac else ""})
    return out


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

    def kernel_link(ifname: str) -> Optional[Dict[str, Any]]:
        if app.mock:
            if ifname != "eth0":
                return None
            return {"operstate": "up", "carrier": True, "speed_mbps": 1000, "duplex": "full", "mtu": 1500}
        base = app.system.p("/sys/class/net/%s" % ifname)
        if not os.path.isdir(base):
            return None

        def rd(name):
            try:
                with open(os.path.join(base, name), "r") as f:
                    return f.read().strip()
            except OSError:
                return None
        speed = rd("speed")
        try:
            speed_n = int(speed) if speed is not None else None
            if speed_n is not None and speed_n <= 0:
                speed_n = None
        except ValueError:
            speed_n = None
        carrier = rd("carrier")
        mtu = rd("mtu")
        return {"operstate": rd("operstate"), "carrier": carrier == "1" if carrier is not None else None,
                "speed_mbps": speed_n, "duplex": rd("duplex"), "mtu": int(mtu) if mtu and mtu.isdigit() else None}

    def role_of(res) -> Optional[str]:
        if res is None:
            return None
        label = None
        for k, v, _n in res.pairs:
            if k.startswith("ethernet (eth0) interface type") or k.startswith("ethernet (eth1) interface type"):
                label = v
        return ROLE_BY_LABEL.get(label or "", "unknown") if label else None

    def state() -> Dict[str, Any]:
        en = app.rest_try("EthernetSettings/Enabled")
        det = app.rest_try("EthernetSettings/Details")
        t0 = app.cli_try("mifi_qcmap_cli", "get_iface_type")
        lk = app.cli_try("mifi_qcmap_cli", "get_iface_link")
        info_res = app.cli_try("mifi_qcmap_cli", "get_ethernet_info")
        if en is None and t0 is None and info_res is None:
            raise unavailable("The Ethernet port is not available on this unit")
        info = parse_ethernet_info(info_res.text if info_res else "")
        role = role_of(t0)
        port0 = next((p for p in info["ports"] if p["name"] == "eth0"), {})
        if role is None and port0:
            role = port0.get("role")
        link = None
        if lk is not None:
            link = (lk.get("Ethernet interface link status is") or "").lower().startswith("enable")
        elif port0:
            link = port0.get("link")
        mac = port0.get("mac", "")
        active = None
        if isinstance(det, list) and det:
            active = bool(det[0].get("Active"))
            mac = mac or str(det[0].get("Mac", "")).upper()
        if not mac:
            mr = app.cli_try("mifi_qcmap_cli", "get_ethernet_macaddr")
            m = re.search(r"mac\s+is:\s*([0-9A-Fa-f:]{17})", mr.text) if mr else None
            mac = m.group(1).upper() if m else ""
        cnt = app.rest_try("ConnectedDevicesStatus", {}) or {}
        eth1 = None
        t1 = app.cli_try("mifi_qcmap_cli", "get_iface_type1")
        # single-port models print "returned 65535 For (---Bradshaw---) ... general error" (no value line)
        if t1 is not None and role_of(t1) is not None:
            l1 = app.cli_try("mifi_qcmap_cli", "get_iface1_link")
            eth1 = {"role": role_of(t1),
                    "link": (l1.get("Ethernet interface link status is") or "").lower().startswith("enable") if l1 else None,
                    "kernel": kernel_link("eth1")}
        enabled = bool(en.get("Enabled")) if isinstance(en, dict) else (info.get("status") == 1 if info.get("status") is not None else None)
        return {"enabled": enabled, "role": role, "role_text": ROLE_TEXT.get(role or "", role or ""),
                "settable_roles": list(ROLE_CODES), "link": link, "active": active, "mac": mac,
                "port_count": info.get("port_count"), "kernel": kernel_link("eth0"),
                "clients": cnt.get("ConnectedDevicesCountEthernet"), "eth1": eth1}

    @r.route("GET", "/api/ethernet")
    def get_eth(req):
        """Ethernet port state, role and link."""
        return state()

    @r.route("PUT", "/api/ethernet")
    def put_eth(req):
        """Turn the Ethernet port on/off or switch it between LAN and WAN (role change needs a restart)."""
        data = validate.only_keys(req.json, ["enabled", "role"])
        cur = state()
        restart = False
        if "enabled" in data:
            en = validate.boolean("enabled", data["enabled"])
            if en != cur.get("enabled"):
                app.rest.merge_put("EthernetSettings/Enabled", {"Enabled": 1 if en else 0})
        if "role" in data:
            role = validate.choice("role", data["role"], list(ROLE_CODES))
            if role != cur.get("role"):
                if role != "lan":
                    ippt = app.cli_try("mifi_qcmap_cli", "get_ippt")
                    if ippt is not None and ippt.get_int("Is active", 0):
                        raise field_error("role", "Turn IP passthrough off before using the port as a WAN")
                restart = run_set(app, "mifi_qcmap_cli", "set_iface_type", [ROLE_CODES[role]],
                                  "Changing the Ethernet role")
        app.cache.invalidate("status")
        app.audit(req, "ethernet.update", {"keys": sorted(data)})
        out = state()
        out["restart_required"] = restart
        return out

    app.features.add("network.ethernet", lambda: (app.rest_try("EthernetSettings/Enabled") is not None or
                                                  app.cli_try("mifi_qcmap_cli", "get_iface_type") is not None))
