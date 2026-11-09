"""LAN & DHCP, device/DNS names, LAN MTU, DHCP reservations, DHCP leases and IP passthrough.

Data sources (docs/features/network.md has the full field map):
- REST RouterSettings: LAN IPv4/mask, DHCP on/off/range/lease (seconds), IPv6 on/off and transparent mode.
  It is the write path; mifi_qcmap_cli set_lan_dhcp_settings (sandbox-verified core spec) is the fallback
  when the REST family is unavailable and the only way to change the stored subnet notation.
- mifi_qcmap_cli get/set_device_name, get/set_dns_name, get/set_ippt, get/set_ippt_feature_mode.
- sysintcli getReservedIPList/setReservedIPList (sys_* layer of the stock reserved_ip app; dmdb_cli is
  excluded by policy), getLanMTU/setLanMTU and getGatewaySettings/setGatewaySettings (hidden in stock).
- usb_cli ippt_settings (RNDIS off for USB passthrough, expert).
The REST IPPassthroughSettings family answers 501 on this firmware, so passthrough uses the CLI.
"""
import ipaddress
import re
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import ApiError, field_error, unavailable, vendor_error

RESET_RE = re.compile(r"reset is required|requires reset|reboot the device", re.I)
NAME_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
DNS_NAME_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                         r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,63}$")
SYSINT_RC_RE = re.compile(r"\breturned (-?\d+)")
MAX_LEASE_MIN = 71582788          # stock DHCPLeaseTimeMaxLimit (u32 seconds / 60)
MAX_RESERVATIONS = 10             # stock MaxAllowedDevices
IPPT_IFACES = {"usb": 1, "ethernet": 2, "ethernet2": 4}
IPPT_IFACE_NAMES = {v: k for k, v in IPPT_IFACES.items()}
GW_MODES = {"default": 0, "modem": 1, "manual": 2}
GW_MODE_NAMES = {v: k for k, v in GW_MODES.items()}
ETH_WAN_TYPES = ("DHCP WAN", "Static WAN", "PPPoE WAN")


# ---------------------------------------------------------------------------------- parsing helpers
def on_off(v: Any) -> Optional[bool]:
    """Vendor booleans: Enabled/Disabled, Enable/Disable, 1/0, true/false."""
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("1", "enabled", "enable", "true", "on", "yes"):
        return True
    if s in ("0", "disabled", "disable", "false", "off", "no"):
        return False
    return None


def parse_lan_cli(res) -> Dict[str, Any]:
    """mifi_qcmap_cli get_lan_dhcp_settings -> dict (empty when the command failed)."""
    if res is None:
        return {}
    rng = res.all("DHCP IP range")
    return {
        "ip": res.get("LAN IP address is", ""),
        "subnet_mask": res.get("LAN subnet mask is", ""),
        "mask_format": res.get_int("LAN mask format is"),
        "link_local": res.get("LAN IPv6 link local address is", ""),
        "dhcp_enabled": on_off(res.get("DHCP status is")),
        "dhcp_start": rng[0] if len(rng) > 0 else "",
        "dhcp_end": rng[1] if len(rng) > 1 else "",
        "dhcp_lease_s": res.get_int("DHCP lease (in seconds)"),
    }


def parse_ippt(res) -> Dict[str, Any]:
    """mifi_qcmap_cli get_ippt: 'Is active: [0]', 'Interface: [2]', 'Client Identity (x)'."""
    if res is None:
        return {}
    m = re.search(r"Client Identity \((.*)\)", res.text)
    code = res.get_int("Is active")
    iface = res.get_int("Interface")
    return {"enabled": bool(code) if code is not None else None, "interface_code": iface,
            "interface": IPPT_IFACE_NAMES.get(iface or 0, "unknown"), "client": (m.group(1).strip() if m else "")}


def sysint_rc(text: str) -> Optional[int]:
    """Last 'xxx returned N' code printed by sysintcli (its lines have no (MODULE: msg) part)."""
    codes = SYSINT_RC_RE.findall(text or "")
    return int(codes[-1]) if codes else None


def parse_reservations(text: str) -> List[Dict[str, Any]]:
    """sysintcli getReservedIPList: list[i].type/mac/ip/host_name:[..]."""
    rows: Dict[int, Dict[str, Any]] = {}
    for m in re.finditer(r"list\[(\d+)\]\.(type|mac|ip|host_name):\[([^\]\r\n]*)\]", text or ""):
        r = rows.setdefault(int(m.group(1)), {})
        r[m.group(2)] = m.group(3).strip()
    out = []
    for i in sorted(rows):
        r = rows[i]
        if not r.get("mac"):
            continue
        try:
            t = int(r.get("type", "0") or 0)
        except ValueError:
            t = 0
        out.append({"mac": r["mac"].upper(), "ip": r.get("ip", ""), "name": r.get("host_name", ""), "type": t})
    return out


def parse_gateway(text: str) -> Dict[str, Any]:
    """sysintcli getGatewaySettings: settings.mode/gateway_address/netmask."""
    vals = dict(re.findall(r"settings\.(mode|gateway_address|netmask):\[([^\]\r\n]*)\]", text or ""))
    if "mode" not in vals:
        return {}
    try:
        mode = int(vals["mode"])
    except ValueError:
        mode = -1
    return {"mode": GW_MODE_NAMES.get(mode, "unknown"), "mode_code": mode,
            "address": vals.get("gateway_address", ""), "netmask": vals.get("netmask", "")}


def prefix_len(mask: str) -> Optional[int]:
    try:
        return ipaddress.IPv4Network("0.0.0.0/" + mask).prefixlen
    except ValueError:
        return None


def parse_mask(field: str, v: Any, lo: int = 8, hi: int = 30) -> str:
    """Accept '255.255.255.0', '/24' or '24' (the CIDR form the stock UI hid); return dotted."""
    s = str(v if v is not None else "").strip()
    if re.match(r"^/?\d{1,2}$", s):
        n = int(s.lstrip("/"))
        if not lo <= n <= hi:
            raise field_error(field, "Prefix length must be between /%d and /%d" % (lo, hi))
        return str(ipaddress.IPv4Network("0.0.0.0/%d" % n).netmask)
    mask = validate.netmask(field, s)
    n = prefix_len(mask)
    if n is None or not lo <= n <= hi:
        raise field_error(field, "Subnet mask must be between /%d and /%d" % (lo, hi))
    return mask


def run_set(app, cli: str, cmd: str, answers, what: str) -> bool:
    """Run a setter; return True when the vendor says a restart is needed (router2/wan_mgr rc 6)."""
    res = app.cli.run(cli, cmd, answers)
    if res.ok:
        return bool(RESET_RE.search(res.text or ""))
    if res.rc == 6 or (res.message and "reset is required" in res.message.lower()):
        return True
    res.raise_for_error(what)
    return False


def run_sysint(app, cmd: str, answers=(), what: str = "") -> str:
    """sysintcli command -> output text; raises vendor_error when its rc is not 0."""
    res = app.cli.run("sysintcli", cmd, list(answers))
    rc = sysint_rc(res.text)
    if res.timed_out or (rc is not None and rc != 0) or (rc is None and not res.ok):
        raise vendor_error("%s failed%s" % (what or ("sysintcli " + cmd), (" (code %s)" % rc) if rc is not None else ""),
                           {"cli": "sysintcli", "command": cmd, "rc": rc})
    return res.text


def _net(ip: str, mask: str) -> ipaddress.IPv4Network:
    return ipaddress.IPv4Network("%s/%s" % (ip, mask), strict=False)


def check_lan(cfg: Dict[str, Any]) -> None:
    """Stock lan app rules (server side) applied to the merged configuration."""
    ip, mask = cfg["ip"], cfg["subnet_mask"]
    net = _net(ip, mask)
    addr = ipaddress.IPv4Address(ip)
    if addr.is_multicast or addr.is_loopback or addr.is_unspecified or addr.is_reserved or addr.is_link_local:
        raise field_error("ip", "This address cannot be used for the router")
    if addr in (net.network_address, net.broadcast_address):
        raise field_error("ip", "The router address cannot be the network or broadcast address")
    if not cfg.get("dhcp_enabled"):
        return
    start, end = cfg.get("dhcp_start") or "", cfg.get("dhcp_end") or ""
    for key, val in (("dhcp_start", start), ("dhcp_end", end)):
        try:
            a = ipaddress.IPv4Address(val)
        except ValueError:
            raise field_error(key, "Enter a valid IPv4 address")
        if a not in net:
            raise field_error(key, "Must be inside the LAN subnet %s" % net)
        if a in (net.network_address, net.broadcast_address):
            raise field_error(key, "Cannot be the network or broadcast address")
    a0, a1 = ipaddress.IPv4Address(start), ipaddress.IPv4Address(end)
    if a1 < a0:
        raise field_error("dhcp_end", "The end address must not be lower than the start address")
    if a0 <= addr <= a1:
        raise field_error("dhcp_start", "The router address %s must not be inside the DHCP range" % ip)


# ---------------------------------------------------------------------------------- service
def register(app):
    r = app.router

    def rest_router() -> Optional[Dict[str, Any]]:
        d = app.rest_try("RouterSettings")
        return d if isinstance(d, dict) else None

    def lan_cli() -> Dict[str, Any]:
        return parse_lan_cli(app.cli_try("mifi_qcmap_cli", "get_lan_dhcp_settings"))

    def lan_state(req=None) -> Dict[str, Any]:
        rs = rest_router()
        cli = lan_cli()
        if rs is None and not cli:
            raise unavailable("The LAN settings are not available right now")
        if rs is not None:
            out = {"ip": rs.get("RouterIPv4Address", ""), "subnet_mask": rs.get("RouterSubnetMask", ""),
                   "dhcp_enabled": bool(rs.get("DHCPEnabled")), "dhcp_start": rs.get("DHCPAddressRangeStart", ""),
                   "dhcp_end": rs.get("DHCPAddressRangeEnd", ""), "dhcp_lease_s": int(rs.get("DHCPLeaseTime") or 0),
                   "ipv6_enabled": bool(rs.get("IPv6Enabled")),
                   "ipv6_transparent": bool(rs.get("IPv6TransparentModeEnabled")), "source": "rest"}
        else:
            out = {"ip": cli["ip"], "subnet_mask": cli["subnet_mask"], "dhcp_enabled": bool(cli["dhcp_enabled"]),
                   "dhcp_start": cli["dhcp_start"], "dhcp_end": cli["dhcp_end"],
                   "dhcp_lease_s": cli["dhcp_lease_s"] or 0, "ipv6_enabled": None, "ipv6_transparent": None,
                   "source": "cli"}
        out["dhcp_lease_min"] = max(1, out["dhcp_lease_s"] // 60) if out["dhcp_lease_s"] else 0
        out["prefix_length"] = prefix_len(out["subnet_mask"]) if out["subnet_mask"] else None
        out["mask_format"] = {0: "dotted", 1: "bits"}.get(cli.get("mask_format"), None) if cli else None
        out["link_local"] = cli.get("link_local", "") if cli else ""
        info = app.cache.get("lan:deviceinfo", 300, lambda: app.rest_try("DeviceInfo", {}) or {})
        out["mac"] = info.get("DeviceInfoMacAddress", "")
        try:
            net = _net(out["ip"], out["subnet_mask"])
            out["network"] = str(net)
            if out["dhcp_start"] and out["dhcp_end"]:
                out["dhcp_pool_size"] = (int(ipaddress.IPv4Address(out["dhcp_end"])) -
                                         int(ipaddress.IPv4Address(out["dhcp_start"])) + 1)
            else:
                out["dhcp_pool_size"] = 0
            client = req.client_ip if req is not None else ""
            try:
                cip = ipaddress.ip_address(client.replace("::ffff:", "")) if client else None
            except ValueError:
                cip = None
            out["client_ip"] = client
            out["client_on_lan"] = bool(cip is not None and cip.version == 4 and cip in net)
        except ValueError:
            out["network"] = ""
            out["dhcp_pool_size"] = 0
            out["client_ip"] = req.client_ip if req is not None else ""
            out["client_on_lan"] = False
        return out

    @r.route("GET", "/api/lan")
    def get_lan(req):
        """LAN address, DHCP server and IPv6 settings."""
        return lan_state(req)

    @r.route("PUT", "/api/lan")
    def put_lan(req):
        """Change LAN address / DHCP / IPv6 (may disconnect clients; the router may need a restart)."""
        data = validate.only_keys(req.json, ["ip", "subnet_mask", "dhcp_enabled", "dhcp_start", "dhcp_end",
                                             "dhcp_lease_min", "ipv6_enabled", "ipv6_transparent"])
        if not data:
            return lan_state(req)
        cur = lan_state(req)
        new = dict(cur)
        if "ip" in data:
            new["ip"] = validate.ipv4("ip", data["ip"])
        if "subnet_mask" in data:
            new["subnet_mask"] = parse_mask("subnet_mask", data["subnet_mask"])
        if "dhcp_enabled" in data:
            new["dhcp_enabled"] = validate.boolean("dhcp_enabled", data["dhcp_enabled"])
        if "dhcp_start" in data:
            new["dhcp_start"] = validate.ipv4("dhcp_start", data["dhcp_start"])
        if "dhcp_end" in data:
            new["dhcp_end"] = validate.ipv4("dhcp_end", data["dhcp_end"])
        if "dhcp_lease_min" in data:
            new["dhcp_lease_min"] = validate.integer("dhcp_lease_min", data["dhcp_lease_min"], 1, MAX_LEASE_MIN)
            new["dhcp_lease_s"] = new["dhcp_lease_min"] * 60
        for k in ("ipv6_enabled", "ipv6_transparent"):
            if k in data:
                new[k] = validate.boolean(k, data[k])
        check_lan(new)
        warnings = []
        moved_subnet = (new["ip"], new["subnet_mask"]) != (cur["ip"], cur["subnet_mask"])
        rs = rest_router()
        if rs is not None:
            ch = {}
            for key, rkey in (("ip", "RouterIPv4Address"), ("subnet_mask", "RouterSubnetMask"),
                              ("dhcp_enabled", "DHCPEnabled"), ("dhcp_start", "DHCPAddressRangeStart"),
                              ("dhcp_end", "DHCPAddressRangeEnd"), ("dhcp_lease_s", "DHCPLeaseTime"),
                              ("ipv6_enabled", "IPv6Enabled"), ("ipv6_transparent", "IPv6TransparentModeEnabled")):
                if new.get(key) != cur.get(key) and rkey in rs:
                    ch[rkey] = new[key]
            if ch:
                app.rest.merge_put("RouterSettings", ch)
        else:
            if any(k in data for k in ("ipv6_enabled", "ipv6_transparent")):
                raise unavailable("IPv6 settings need the vendor REST service, which is not available")
            cli = lan_cli()
            fmt = cli.get("mask_format") or 0
            run_set(app, "mifi_qcmap_cli", "set_lan_dhcp_settings",
                    [new["ip"], new["subnet_mask"], fmt, 1 if new["dhcp_enabled"] else 0, new["dhcp_start"] or "0.0.0.0",
                     new["dhcp_end"] or "0.0.0.0", new["dhcp_lease_s"] or 86400], "Saving the LAN settings")
        # reservations outside the new range no longer work (the stock UI even deletes them all)
        range_changed = (new["dhcp_start"], new["dhcp_end"]) != (cur["dhcp_start"], cur["dhcp_end"])
        if new["dhcp_enabled"] and (range_changed or moved_subnet):
            try:
                for rsv in reservations():
                    if not in_range(rsv["ip"], new):
                        warnings.append("Reserved address %s (%s) is outside the new DHCP range" % (rsv["ip"], rsv["mac"]))
            except ApiError:
                pass
        app.cache.invalidate("lan:")
        app.audit(req, "lan.update", {"keys": sorted(data)})
        out = lan_state(req)
        out["warnings"] = warnings
        out["address_changed"] = moved_subnet
        return out

    # -- names ------------------------------------------------------------------------------
    def names_state() -> Dict[str, Any]:
        dn = app.cli_try("mifi_qcmap_cli", "get_device_name")
        hn = app.cli_try("mifi_qcmap_cli", "get_dns_name")
        if dn is None and hn is None:
            raise unavailable("The device names are not available right now")
        device = dn.get("Device name", "") if dn else ""
        dns = hn.get("Host DNS name", "") if hn else ""
        allowed = [h.lower() for h in (app.cfg.get("allowed_hosts") or [])]
        return {"device_name": device, "dns_name": dns, "local_url": ("http://%s.local/" % device) if device else "",
                "dns_url": ("http://%s/" % dns) if dns else "",
                "dns_name_allowed": bool(dns) and dns.lower() in allowed}

    @r.route("GET", "/api/lan/names")
    def get_names(req):
        """Device (mDNS) name and the DNS name that points at the router."""
        return names_state()

    @r.route("PUT", "/api/lan/names")
    def put_names(req):
        """Change the device name and/or DNS name (takes effect after a restart)."""
        data = validate.only_keys(req.json, ["device_name", "dns_name"])
        restart = False
        if "device_name" in data:
            v = validate.text("device_name", data["device_name"], 1, 63).strip()
            if not NAME_LABEL_RE.match(v):
                raise field_error("device_name", "Use letters, digits and hyphens (no spaces or dots)")
            data["device_name"] = v
        if "dns_name" in data:
            v = validate.text("dns_name", data["dns_name"], 1, 253).strip().rstrip(".")
            if not DNS_NAME_RE.match(v) or re.match(r"^[\d.]+$", v):
                raise field_error("dns_name", "Enter a host name such as router.home")
            data["dns_name"] = v
        if "device_name" in data:
            restart |= run_set(app, "mifi_qcmap_cli", "set_device_name", [data["device_name"]], "Saving the device name")
        if "dns_name" in data:
            restart |= run_set(app, "mifi_qcmap_cli", "set_dns_name", [data["dns_name"]], "Saving the DNS name")
        app.audit(req, "lan.names", {"keys": sorted(data)})
        out = names_state()
        out["restart_required"] = bool(data)   # the vendor applies names after a restart in all cases
        return out

    # -- advanced (MTU, subnet notation) ---------------------------------------------------------
    def advanced_state() -> Dict[str, Any]:
        mtu = None
        try:
            txt = run_sysint(app, "getLanMTU", what="Reading the LAN MTU")
            m = re.search(r"mtu:\[(\d+)\]", txt)
            mtu = int(m.group(1)) if m else None
        except ApiError:
            mtu = None
        cli = lan_cli()
        try:
            dhcp = lan_state().get("dhcp_enabled")
        except ApiError:
            dhcp = cli.get("dhcp_enabled") if cli else None
        return {"mtu": mtu, "mask_format": {0: "dotted", 1: "bits"}.get(cli.get("mask_format")) if cli else None,
                "dhcp_enabled": dhcp}

    @r.route("GET", "/api/lan/advanced")
    def get_advanced(req):
        """LAN MTU and the stored subnet notation (hidden in the stock UI)."""
        return advanced_state()

    @r.route("PUT", "/api/lan/advanced")
    def put_advanced(req):
        """Change the LAN MTU and/or the stored subnet notation."""
        data = validate.only_keys(req.json, ["mtu", "mask_format"])
        restart = False
        mtu = validate.integer("mtu", data["mtu"], 576, 1500) if "mtu" in data else None
        fmt, lan = None, None
        if "mask_format" in data:
            fmt = validate.choice("mask_format", data["mask_format"], ["dotted", "bits"])
            lan = lan_state()
            if not lan.get("dhcp_enabled"):
                # with DHCP off the CLI skips the range prompts and the vendor would store an empty range
                raise field_error("mask_format", "Turn the DHCP server on before changing the notation")
        if mtu is not None:
            run_sysint(app, "setLanMTU", [mtu], "Saving the LAN MTU")
        if fmt is not None:
            restart |= run_set(app, "mifi_qcmap_cli", "set_lan_dhcp_settings",
                               [lan["ip"], lan["subnet_mask"], 1 if fmt == "bits" else 0, 1, lan["dhcp_start"],
                                lan["dhcp_end"], lan["dhcp_lease_s"] or 86400], "Saving the subnet notation")
        app.audit(req, "lan.advanced", {"keys": sorted(data)})
        out = advanced_state()
        out["restart_required"] = restart
        return out

    # -- DHCP reservations -------------------------------------------------------------------
    def reservations() -> List[Dict[str, Any]]:
        txt = run_sysint(app, "getReservedIPList", what="Reading the reserved addresses")
        return parse_reservations(txt)

    def in_range(ip: str, cfg: Dict[str, Any]) -> bool:
        try:
            a = ipaddress.IPv4Address(ip)
            return ipaddress.IPv4Address(cfg["dhcp_start"]) <= a <= ipaddress.IPv4Address(cfg["dhcp_end"])
        except ValueError:
            return False

    def write_reservation(name: str, rtype: int, mac: str, ip: str) -> None:
        run_sysint(app, "setReservedIPList", [1, name, rtype, mac, ip], "Saving the reserved address")

    def check_reservation(data: Dict[str, Any], existing: List[Dict[str, Any]], mac_fixed: Optional[str] = None):
        lan = lan_state()
        if not lan.get("dhcp_enabled"):
            raise field_error("ip", "Turn the DHCP server on to reserve addresses")
        name = validate.text("name", data.get("name", ""), 1, 32).strip()
        if not name:
            raise field_error("name", "Enter a name")
        mac = mac_fixed or validate.mac("mac", data.get("mac", ""))
        ip = validate.ipv4("ip", data.get("ip", ""))
        if not in_range(ip, lan):
            raise field_error("ip", "Must be inside the DHCP range %s - %s" % (lan["dhcp_start"], lan["dhcp_end"]))
        if ip == lan["ip"]:
            raise field_error("ip", "This is the router's own address")
        rtype = validate.integer("type", data.get("type", 0), 0, 10)
        for e in existing:
            if e["mac"] == mac:
                continue
            if e["ip"] == ip:
                raise field_error("ip", "This address is already reserved for %s" % e["mac"])
            if e["name"].lower() == name.lower():
                raise field_error("name", "This name is already used")
        return name, rtype, mac, ip

    @r.route("GET", "/api/lan/reservations")
    def get_reservations(req):
        """DHCP address reservations (MAC -> fixed IP)."""
        return reservations()

    @r.route("POST", "/api/lan/reservations")
    def add_reservation(req):
        """Reserve an address for a device (applied after a restart)."""
        data = validate.only_keys(req.json, ["name", "mac", "ip", "type"])
        existing = reservations()
        mac = validate.mac("mac", data.get("mac", ""))
        if any(e["mac"] == mac for e in existing):
            raise field_error("mac", "This device already has a reserved address")
        if len(existing) >= MAX_RESERVATIONS:
            raise field_error("mac", "At most %d reserved addresses are supported" % MAX_RESERVATIONS)
        name, rtype, mac, ip = check_reservation(data, existing, mac)
        write_reservation(name, rtype, mac, ip)
        app.audit(req, "lan.reservation.add", {"mac": mac})
        return {"reservations": reservations(), "restart_required": True}

    @r.route("PUT", "/api/lan/reservations/{mac}")
    def put_reservation(req):
        """Change a reserved address."""
        mac = validate.mac("mac", req.params["mac"].replace("-", ":"))
        data = validate.only_keys(req.json, ["name", "mac", "ip", "type"])
        existing = reservations()
        old = next((e for e in existing if e["mac"] == mac), None)
        if old is None:
            raise ApiError(404, "not_found", "No reservation for %s" % mac)
        merged = dict(old)
        merged.update({k: v for k, v in data.items() if k != "mac"})
        name, rtype, mac, ip = check_reservation(merged, existing, mac)
        write_reservation(name, rtype, mac, ip)
        app.audit(req, "lan.reservation.update", {"mac": mac})
        return {"reservations": reservations(), "restart_required": True}

    @r.route("DELETE", "/api/lan/reservations/{mac}")
    def del_reservation(req):
        """Remove a reserved address."""
        mac = validate.mac("mac", req.params["mac"].replace("-", ":"))
        existing = reservations()
        old = next((e for e in existing if e["mac"] == mac), None)
        if old is None:
            raise ApiError(404, "not_found", "No reservation for %s" % mac)
        write_reservation(old["name"] or "device", old["type"], mac, "")
        app.audit(req, "lan.reservation.delete", {"mac": mac})
        return {"reservations": reservations(), "restart_required": True}

    @r.route("GET", "/api/lan/leases")
    def get_leases(req):
        """Current DHCP leases with names from the connected-devices list."""
        names = {}
        devs = app.rest_try("ConnectedDevices", []) or []
        for d in devs if isinstance(devs, list) else []:
            mac = str(d.get("ConnectedDeviceMacAddress", "")).upper()
            if mac:
                names[mac] = {"name": d.get("ConnectedDeviceFriendlyName") or d.get("ConnectedDeviceHostName") or "",
                              "connection": d.get("ConnectedDeviceConnectionType", "")}
        try:
            reserved = {e["mac"]: e["ip"] for e in reservations()}
        except ApiError:
            reserved = {}
        out = []
        for lease in app.system.dhcp_leases():
            mac = str(lease.get("mac", "")).upper()
            extra = names.get(mac, {})
            out.append({"mac": mac, "ip": lease.get("ip", ""), "hostname": lease.get("hostname") or extra.get("name", ""),
                        "expires": lease.get("expires", 0), "connection": extra.get("connection", ""),
                        "reserved": mac in reserved})
        return out

    # -- IP passthrough ------------------------------------------------------------------------
    def eth_wan_active() -> bool:
        res = app.cli_try("mifi_qcmap_cli", "get_iface_type")
        t = (res.get("Ethernet (eth0) interface type is") or "") if res else ""
        return t in ETH_WAN_TYPES

    def stock_lan_flags() -> Dict[str, Any]:
        res = app.cache.get("lan:webui_flags", 300, lambda: app.cli_try("webui_cli", "get_lan_settings"))
        feats = app.cache.get("lan:features", 300, lambda: app.cli_try("settings_cli", "get_features"))
        out = {"carrier_supported": on_off(feats.get("ip_passthrough_supported")) if feats else None}
        if res is not None:
            out.update({"ippt_shown": on_off(res.get("IPPT enabled")), "ippt_editable": on_off(res.get("Edit IPPT enabled")),
                        "interface_shown": on_off(res.get("IPPT Interface enabled")),
                        "gateway_shown": on_off(res.get("Gateway settings enabled")),
                        "wifi_off_with_ippt": on_off(res.get("Update WiFi on IPPT enabled"))})
        return out

    def ippt_state() -> Dict[str, Any]:
        res = app.cli_try("mifi_qcmap_cli", "get_ippt")
        if res is None:
            raise unavailable("IP passthrough is not available on this unit")
        out = parse_ippt(res)
        fm = app.cli_try("mifi_qcmap_cli", "get_ippt_feature_mode")
        out["feature_mode"] = bool(fm.get_int("IPPT Feature Mode is", 0)) if fm else None
        try:
            out["gateway"] = parse_gateway(run_sysint(app, "getGatewaySettings", what="Reading the gateway settings")) or None
        except ApiError:
            out["gateway"] = None
        out["ethernet_wan_active"] = eth_wan_active()
        eth = app.rest_try("EthernetSettings/Enabled")
        out["ethernet_enabled"] = bool(eth.get("Enabled")) if isinstance(eth, dict) else None
        orval = app.cache.get("lan:is_orval", 3600, lambda: app.cli_try("dsm_cli", "is_orval"))
        out["second_ethernet"] = bool(orval and re.search(r"isOrval=1", orval.text))
        out["stock"] = stock_lan_flags()
        out["client_kind"] = "hostname" if out.get("interface") == "usb" else "mac"
        return out

    @r.route("GET", "/api/lan/ippt")
    def get_ippt(req):
        """IP passthrough (hand the public IP to one client) and its gateway options."""
        return ippt_state()

    @r.route("PUT", "/api/lan/ippt")
    def put_ippt(req):
        """Change IP passthrough (tier 2: routing changes, other interfaces stop, the router restarts)."""
        app.require_tier(req, 2)
        data = validate.only_keys(req.json, ["enabled", "interface", "client", "feature_mode", "gateway_mode",
                                             "gateway_address", "gateway_netmask"])
        cur = ippt_state()
        restart = False
        main_keys = [k for k in ("enabled", "interface", "client") if k in data]
        if main_keys:
            enabled = validate.boolean("enabled", data.get("enabled", cur.get("enabled")))
            ifaces = ["usb", "ethernet"] + (["ethernet2"] if cur.get("second_ethernet") else [])
            iface = data.get("interface", cur.get("interface"))
            if iface not in ifaces:
                iface = validate.choice("interface", iface, ifaces)
            client = data.get("client", cur.get("client") or "")
            if not isinstance(client, str):
                raise field_error("client", "Must be text")
            client = client.strip()
            if iface == "usb":
                if (enabled or client) and not HOSTNAME_RE.match(client):
                    raise field_error("client", "Enter the host name of the USB computer (letters, digits, - _ .)")
            else:
                if enabled or client:
                    client = validate.mac("client", client)
            if enabled and cur.get("ethernet_wan_active"):
                raise field_error("enabled", "IP passthrough cannot be used while the Ethernet port is a WAN")
            if enabled and iface != "usb" and cur.get("ethernet_enabled") is False:
                raise field_error("interface", "Turn the Ethernet port on first")
            app.state.snapshot("ippt", {"enabled": cur.get("enabled"), "interface": cur.get("interface"),
                                        "client": cur.get("client")})
            restart |= run_set(app, "mifi_qcmap_cli", "set_ippt",
                               [1 if enabled else 0, IPPT_IFACES[iface], client], "Saving IP passthrough")
        if "feature_mode" in data:
            fm = validate.boolean("feature_mode", data["feature_mode"])
            restart |= run_set(app, "mifi_qcmap_cli", "set_ippt_feature_mode", [1 if fm else 0],
                               "Saving the passthrough mode")
        if any(k in data for k in ("gateway_mode", "gateway_address", "gateway_netmask")):
            gw = cur.get("gateway") or {}
            mode = validate.choice("gateway_mode", data.get("gateway_mode", gw.get("mode", "default")),
                                   list(GW_MODES))
            addr, mask = "", ""
            if mode == "manual":
                addr = validate.ipv4("gateway_address", data.get("gateway_address", gw.get("address", "")))
                mask = parse_mask("gateway_netmask", data.get("gateway_netmask", gw.get("netmask", "")), 1, 32)
            run_sysint(app, "setGatewaySettings", [GW_MODES[mode], addr, mask], "Saving the gateway settings")
        app.audit(req, "lan.ippt.update", {"keys": sorted(data)})
        out = ippt_state()
        out["restart_required"] = restart
        return out

    @r.route("POST", "/api/lan/ippt/restore")
    def restore_ippt(req):
        """Restore the passthrough settings saved before the last change (tier 2)."""
        app.require_tier(req, 2)
        prev = app.state.last_snapshot("ippt")
        if not prev:
            raise ApiError(404, "not_found", "No previous passthrough settings saved")
        iface = prev.get("interface") if prev.get("interface") in IPPT_IFACES else "usb"
        restart = run_set(app, "mifi_qcmap_cli", "set_ippt",
                          [1 if prev.get("enabled") else 0, IPPT_IFACES[iface], prev.get("client") or ""],
                          "Restoring IP passthrough")
        app.audit(req, "lan.ippt.restore")
        out = ippt_state()
        out["restart_required"] = restart
        return out

    @r.route("POST", "/api/lan/ippt/usb-rndis")
    def ippt_usb_rndis(req):
        """Turn the USB RNDIS network function off/on for USB passthrough (tier 2, expert)."""
        app.require_tier(req, 2)
        data = validate.only_keys(req.json, ["disabled"])
        disabled = validate.boolean("disabled", data.get("disabled"))
        run_set(app, "usb_cli", "ippt_settings", [1 if disabled else 0], "Changing the USB passthrough setting")
        app.audit(req, "lan.ippt.usb_rndis", {"disabled": disabled})
        return {"rndis_disabled": disabled}

    app.features.add("network.ippt", lambda: app.cli_try("mifi_qcmap_cli", "get_ippt") is not None)
