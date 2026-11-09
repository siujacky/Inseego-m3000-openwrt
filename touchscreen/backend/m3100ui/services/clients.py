"""Connected devices (Wi-Fi, USB, Ethernet): list, rename, block/unblock, Wi-Fi connection limit.

Sources: REST ConnectedDevices + ConnectedDevicesStatus (vendor device list and counts), DHCP leases
(app.system.dhcp_leases), `wifi_cli get_sta_list` (stations associated right now), REST BlockedDevices /
WhiteListedDevices.  Names: the vendor device database (`sysintcli setDeviceName`, static spec) plus a
copy kept by this UI in <state>/wifi/client_names.json so names work even while the vendor write is refused.
Shared Wi-Fi helpers (block/unblock, self detection, client limit) live in services/wifi.py (app.wifi).
"""
import re
import time
from typing import Any, Dict, List

from .. import validate
from ..errors import ApiError, field_error, unavailable

NAMES_FILE = "wifi/client_names.json"
SEEN_FILE = "wifi/client_seen.json"     # last name each MAC reported (keeps blocked devices readable)
SEEN_MAX = 300
CONN = {"usb": "usb", "ethernet": "ethernet", "lan": "ethernet", "wifi": "wifi", "wi-fi": "wifi", "wlan": "wifi"}


def connection_kind(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s.startswith("usb"):
        return "usb"
    if s.startswith("eth") or s == "lan":
        return "ethernet"
    if "wi" in s or "wlan" in s or "guest" in s:
        return "wifi"
    return CONN.get(s, "other" if s else "unknown")


def register(app):
    r = app.router

    def names() -> Dict[str, str]:
        d = app.state.read_json(NAMES_FILE, {}) or {}
        return {str(k).upper(): str(v) for k, v in d.items() if isinstance(v, str)}

    def set_name(mac: str, name: str) -> None:
        d = names()
        if name:
            d[mac.upper()] = name
        else:
            d.pop(mac.upper(), None)
        app.state.write_json(NAMES_FILE, d)

    def seen() -> Dict[str, str]:
        d = app.state.read_json(SEEN_FILE, {}) or {}
        return {str(k).upper(): str(v) for k, v in d.items() if isinstance(v, str)}

    def remember(rows: List[Dict[str, Any]]) -> None:
        old = seen()
        new = dict(old)
        for row in rows:
            nm = row.get("vendor_name") or row.get("hostname")
            if nm and new.get(row["mac"]) != nm:
                new.pop(row["mac"], None)
                new[row["mac"]] = nm
        if new != old:
            items = list(new.items())[-SEEN_MAX:]
            app.state.write_json(SEEN_FILE, dict(items))

    def display_names() -> Dict[str, str]:
        out = seen()
        out.update(names())
        return out

    app.client_names = display_names
    app.set_client_name = set_name

    def wifi():
        w = getattr(app, "wifi", None)
        if w is None:
            raise unavailable("Wi-Fi control is not available")
        return w

    def sta_list() -> List[str]:
        def load():
            res = app.cli_try("wifi_cli", "get_sta_list")
            if res is None:
                return None
            return [m.group(1).upper() for m in
                    re.finditer(r"MAC Addr\[\d+\]:\s*\[([0-9A-Fa-f:]{17})\]", res.text)]
        return app.cache.get("clients:sta", 3, load)

    def gather(req) -> Dict[str, Any]:
        w = getattr(app, "wifi", None)
        devices = app.rest_try("ConnectedDevices", None)
        counts = app.rest_try("ConnectedDevicesStatus", {}) or {}
        try:
            leases = app.system.dhcp_leases()
        except Exception:
            leases = []
        stas = sta_list()
        local = names()
        blocked = (w.rest_list("blocked") if w else None) or []
        allowed = (w.rest_list("allowed") if w else None)
        blocked_macs = {b["mac"] for b in blocked}
        allowed_macs = {a["mac"] for a in allowed or []}
        lease_by_mac = {str(l.get("mac", "")).upper(): l for l in leases}
        me_ip = (getattr(req, "client_ip", "") or "").replace("::ffff:", "")
        rows: Dict[tuple, Dict[str, Any]] = {}
        for d in devices if isinstance(devices, list) else []:
            if not isinstance(d, dict):
                continue
            mac = str(d.get("ConnectedDeviceMacAddress", "")).upper()
            if not re.match(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", mac):
                continue
            raw_type = str(d.get("ConnectedDeviceConnectionType", ""))
            kind = connection_kind(raw_type)
            key = (mac, kind)
            row = rows.setdefault(key, {"mac": mac, "vendor_mac": str(d.get("ConnectedDeviceMacAddress")),
                                        "connection": kind, "connection_raw": raw_type,
                                        "network": "guest" if "guest" in raw_type.lower() else None,
                                        "ip": "", "ipv6": "", "hostname": "", "vendor_name": "",
                                        "device_type": "", "online": True, "sources": []})
            row["ip"] = row["ip"] or str(d.get("ConnectedDeviceIPv4Address") or "")
            row["ipv6"] = row["ipv6"] or str(d.get("ConnectedDeviceIPv6Address") or "")
            row["hostname"] = row["hostname"] or str(d.get("ConnectedDeviceHostName") or "")
            row["vendor_name"] = row["vendor_name"] or str(d.get("ConnectedDeviceFriendlyName") or "")
            row["device_type"] = row["device_type"] or str(d.get("ConnectedDeviceApplianceType") or "")
            if "vendor" not in row["sources"]:
                row["sources"].append("vendor")
        known = {k[0] for k in rows}
        for mac in stas or []:
            if mac in known:
                for k, row in rows.items():
                    if k[0] == mac and "wifi" not in row["sources"]:
                        row["sources"].append("wifi")
                continue
            rows[(mac, "wifi")] = {"mac": mac, "vendor_mac": mac, "connection": "wifi", "connection_raw": "WiFi",
                                   "network": None, "ip": "", "ipv6": "", "hostname": "", "vendor_name": "",
                                   "device_type": "", "online": True, "sources": ["wifi"]}
            known.add(mac)
        for mac, l in lease_by_mac.items():
            if not re.match(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", mac):
                continue
            hit = [row for k, row in rows.items() if k[0] == mac]
            if not hit:
                rows[(mac, "unknown")] = {"mac": mac, "vendor_mac": mac, "connection": "unknown", "connection_raw": "",
                                          "network": None, "ip": "", "ipv6": "", "hostname": "", "vendor_name": "",
                                          "device_type": "", "online": False, "sources": []}
                hit = [rows[(mac, "unknown")]]
            for row in hit:
                row["ip"] = row["ip"] or str(l.get("ip") or "")
                row["hostname"] = row["hostname"] or str(l.get("hostname") or "")
                row["lease_expires"] = l.get("expires")
                if "dhcp" not in row["sources"]:
                    row["sources"].append("dhcp")
        out = []
        now = time.time()
        for row in rows.values():
            mac = row["mac"]
            row["custom_name"] = local.get(mac, "")
            row["name"] = row["custom_name"] or row["vendor_name"] or row["hostname"] or ""
            row["blocked"] = mac in blocked_macs
            row["allowed"] = (mac in allowed_macs) if allowed is not None else None
            row["is_self"] = bool(me_ip and row["ip"] == me_ip)
            exp = row.get("lease_expires")
            row["lease_remaining_s"] = int(exp - now) if isinstance(exp, (int, float)) and exp > now else None
            if row["connection"] == "unknown" and not row["online"]:
                row["online"] = mac in (stas or [])
            out.append(row)
        order = {"wifi": 0, "ethernet": 1, "usb": 2, "other": 3, "unknown": 4}
        out.sort(key=lambda x: (not x["online"], order.get(x["connection"], 5), x["name"].lower() or "~", x["mac"]))
        c = {"wifi": int(counts.get("ConnectedDevicesCountWiFi") or 0), "usb": int(counts.get("ConnectedDevicesCountUSB") or 0),
             "ethernet": int(counts.get("ConnectedDevicesCountEthernet") or 0)}
        c["total"] = sum(c.values())
        lim = {}
        if w:
            try:
                adv = w.rest_get("WiFi/AdvancedSettings") or {}
                lim = {"max_clients": adv.get("MaxConnections", w.ap_settings().get("max_clients")),
                       "max_clients_limit": w.max_clients_limit()}
            except ApiError:
                lim = {}
        remember(out)
        known_names = seen()
        blocked_rows = []
        for b in blocked:
            b = dict(b)
            b["name"] = local.get(b["mac"]) or b.get("name") or known_names.get(b["mac"], "")
            blocked_rows.append(b)
        return {"clients": out, "counts": c, "blocked": blocked_rows, "wifi_associated": stas,
                "vendor_list_available": isinstance(devices, list), "allow_list_mode": w.mac_filter_on() if w else None,
                "max_clients": lim.get("max_clients"), "max_clients_limit": lim.get("max_clients_limit")}

    @r.route("GET", "/api/clients")
    def list_clients(req):
        """All connected devices (Wi-Fi, USB, Ethernet) with names, addresses and block state."""
        return gather(req)

    @r.route("GET", "/api/clients/blocked")
    def list_blocked(req):
        """Blocked devices."""
        return {"blocked": gather(req)["blocked"]}

    @r.route("GET", "/api/clients/limit")
    def get_limit(req):
        """Maximum number of simultaneous Wi-Fi clients."""
        w = wifi()
        adv = w.rest_get("WiFi/AdvancedSettings") or {}
        return {"max_clients": adv.get("MaxConnections", w.ap_settings().get("max_clients")),
                "max_clients_limit": w.max_clients_limit()}

    @r.route("PUT", "/api/clients/limit")
    def put_limit(req):
        """Change the maximum number of simultaneous Wi-Fi clients."""
        w = wifi()
        data = validate.only_keys(req.json, ["max_clients"])
        if "max_clients" not in data:
            raise field_error("max_clients", "Enter a number")
        n = validate.integer("max_clients", data["max_clients"], 1, w.max_clients_limit())
        w.set_max_clients(n)
        w.invalidate()
        app.audit(req, "clients.limit", {"max_clients": n})
        return get_limit(req)

    def find(req, raw_mac: str) -> Dict[str, Any]:
        mac = validate.mac("mac", raw_mac)
        data = gather(req)
        for row in data["clients"]:
            if row["mac"] == mac:
                return row
        for b in data["blocked"]:
            if b["mac"] == mac:
                return {"mac": mac, "vendor_mac": b.get("vendor_mac", mac), "blocked": True, "is_self": False,
                        "connection": "wifi", "name": b.get("name", "")}
        raise ApiError(404, "not_found", "No such device")

    @r.route("PUT", "/api/clients/{mac}")
    def rename(req):
        """Rename a device (name shown here and in the vendor device list)."""
        data = validate.only_keys(req.json, ["name"])
        if "name" not in data:
            raise field_error("name", "Enter a name")
        name = validate.text("name", data["name"], 0, 32).strip()
        if re.search(r"[\x00-\x1f\x7f]", name):
            raise field_error("name", "Control characters are not allowed")
        row = find(req, req.params["mac"])
        set_name(row["mac"], name)
        synced = False
        if name:
            try:
                res = app.cli.run("sysintcli", "setDeviceName", [row.get("vendor_mac") or row["mac"], name],
                                  what="Renaming the device")
                m = re.search(r"sys_set\w+ returned (-?\d+)", res.text)
                synced = bool(m and int(m.group(1)) == 0)
            except ApiError:
                synced = False
        app.cache.invalidate("wifi:rest:ConnectedDevices")
        app.audit(req, "clients.rename", {"mac": row["mac"], "vendor": synced})
        out = find(req, row["mac"])
        out["vendor_synced"] = synced
        return out

    @r.route("POST", "/api/clients/{mac}/block")
    def block(req):
        """Block a device from Wi-Fi (it is disconnected and cannot rejoin)."""
        validate.only_keys(req.json, [])
        w = wifi()
        row = find(req, req.params["mac"])
        me = w.self_mac(req)
        if row.get("is_self") or row["mac"] == me:
            raise field_error("mac", "You cannot block the device you are using")
        if row.get("blocked"):
            return row
        how = w.block(row.get("vendor_mac") or row["mac"])
        w.invalidate()
        app.audit(req, "clients.block", {"mac": row["mac"], "how": how})
        out = find(req, row["mac"])
        out["block_method"] = how
        return out

    @r.route("POST", "/api/clients/{mac}/unblock")
    def unblock(req):
        """Unblock a device."""
        validate.only_keys(req.json, [])
        w = wifi()
        mac = validate.mac("mac", req.params["mac"])
        blocked = {b["mac"]: b for b in (w.rest_list("blocked") or [])}
        if mac not in blocked:
            raise ApiError(404, "not_found", "This device is not blocked")
        w.unblock(blocked[mac].get("vendor_mac") or mac)
        w.invalidate()
        app.audit(req, "clients.unblock", {"mac": mac})
        return {"mac": mac, "blocked": False}
