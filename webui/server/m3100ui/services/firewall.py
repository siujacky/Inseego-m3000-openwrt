"""Firewall: general switches, port forwarding, port filtering, allowed (inbound) ports, DMZ.

Data sources (see docs/features/firewall.md for the per-field map):
- vendor REST: PortForwardingSettings, PortFilteringSettings, DMZSettings, RouterSettings (read)
- mifi_qcmap_cli (router2): every switch the REST API lacks, and the three rule lists.  The list
  setters are driven in argv mode (adapters/specs/firewall.py, RecordListSpec) because only the
  argv path can express "translate" port mappings and an empty list.

Semantics established from the binaries (mifi_qcmap_cli, libmifi_qcmap_router2_api, mifi_qcmapd):
- a port-forwarding entry is ONE external port -> ONE internal port (the daemon adds one SNAT
  entry per record).  Ranges are therefore stored as consecutive single-port entries with the
  same name and are grouped back into one rule when read.  Max 30 entries.
- port filtering is an outbound white-list: when on, only listed applications reach the Internet.
- "allowed ports" open INPUT on the router itself (iptables -I INPUT -i <iface> --dport <p> ACCEPT).
- DNS passthrough and firewall severity have no working setter (router2_set_dns_passthrough_enabled
  returns 7 "not implemented"; mifi_qcmap_cli set_fw_severity is `movs r0,#2; bx lr`).
"""
import hashlib
import ipaddress
import re
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import ApiError, conflict, field_error, not_found, unavailable

QC = "mifi_qcmap_cli"
MAX_FWD = 30
MAX_FILTER = 25
MAX_ALLOWED = 10
PROTO_NUM = {"tcp": 1, "udp": 2, "both": 3}
PROTO_FROM_NUM = {1: "tcp", 2: "udp", 3: "both"}
PROTO_FROM_TXT = {"tcp": "tcp", "udp": "udp", "tcp & udp": "both", "tcpudp": "both", "both": "both"}
PROTO_LABEL = {"tcp": "TCP", "udp": "UDP", "both": "TCP and UDP"}
NAME_RE = re.compile(r"^[\x20-\x5a\x5c\x5e-\x7e]+$")      # printable ASCII without [ and ]
IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")

# -- switches read with mifi_qcmap_cli get_* (value printed in [..], ICMP line lacks the "]") --
SWITCHES = {
    "vpn_passthrough": ("get_vpnpassthrough", r"VPN passthrough is:\s*\[([^\]\n]*)", "set_vpnpassthrough"),
    "dns_passthrough": ("get_dnspassthrough", r"DNS passthrough is:\s*\[([^\]\n]*)", None),
    "http_redirect": ("get_http_redirect", r"HTTP redirect is:\s*\[([^\]\n]*)", "set_http_redirect"),
    "wan_ping": ("get_icmp_response", r"ICMP status on the WAN\s*\[([^\]\n]*)", "set_icmp_response"),
    "ipv6_firewall": ("get_ipv6_firewall", r"Ipv6 firewall status is:\s*\[([^\]\n]*)", "set_ipv6_firewall"),
    "allowed_ports": ("get_allowed_ports", r"Allowed Port Status:\s*\[([^\]\n]*)", "set_allowed_ports"),
}
SEVERITY_RE = re.compile(r"Firewall Severity is:\s*\[([^\]\n]*)")
FWD_RE = re.compile(r"fwd rule name=\[(?P<name>.*?)\], enabled=\[(?P<en>-?\d+)\], ip=\[(?P<ip>[^\]]*)\], "
                    r"type=\[(?P<type>[^\]]*)\], local port=\[(?P<local>-?\d+)\], remote port=\[(?P<remote>-?\d+)\]")
FILTER_RE = re.compile(r"filter rule name=\[(?P<name>.*?)\], enabled=\[(?P<en>-?\d+)\], type=\[(?P<type>[^\]]*)\], "
                       r"start port=\[(?P<start>-?\d+)\], end port=\[(?P<end>-?\d+)\]")
ALLOWED_RE = re.compile(r"Allowed Port rule : enabled=\[(?P<en>-?\d+)\], interface name=\[(?P<ifaces>[^\]]*)\], "
                        r"port list=\[(?P<list>[^\]]*)\], type=\[(?P<type>[^\]]*)\], range=\[(?P<range>[^\]]*)\], "
                        r"start/ext port=\[(?P<start>-?\d+)\], end/int port=\[(?P<end>-?\d+)\]")

# Stock "predefined applications" (decoded from the stock port_forward / port_filtering app.so)
FWD_PRESETS = [
    {"id": "dns", "label": "DNS server", "members": [["DNS", 53, "both"]]},
    {"id": "ftp", "label": "FTP server", "members": [["FTP Data", 20, "tcp"], ["FTP Control", 21, "tcp"]]},
    {"id": "web", "label": "Web server (HTTP and HTTPS)", "members": [["HTTP", 80, "tcp"], ["HTTPS", 443, "tcp"]]},
    {"id": "nntp", "label": "News server (NNTP)", "members": [["NNTP", 119, "tcp"]]},
    {"id": "pop3", "label": "Mail retrieval (POP3 and POP3S)", "members": [["POP3", 110, "tcp"], ["POP3S", 995, "tcp"]]},
    {"id": "smtp", "label": "Mail server (SMTP)",
     "members": [["SMTP", 25, "tcp"], ["Secure SMTP", 587, "tcp"], ["Secure SMTP (SSMTP)", 465, "tcp"]]},
    {"id": "snmp", "label": "SNMP", "members": [["SNMP", 161, "udp"]]},
    {"id": "telnet", "label": "Telnet", "members": [["Telnet", 23, "tcp"]]},
    {"id": "tftp", "label": "TFTP", "members": [["TFTP", 69, "udp"]]},
]
FILTER_STD = [
    {"id": "email", "label": "Email (POP3, IMAP, SMTP)", "members": [
        ["POP", 110, 110], ["SMTP", 25, 25], ["Secure SMTP", 587, 587], ["IMAP1", 143, 143], ["IMAP2", 220, 220],
        ["Secure SMTP (SSMTP)", 465, 465], ["Secure IMAP (IMAP4-SSL)", 585, 585], ["IMAP4 over SSL (IMAPS)", 993, 993],
        ["Secure POP3 (SSL-POP)", 995, 995], ["SMTP2", 2525, 2525]]},
    {"id": "ftp", "label": "File transfer (FTP)", "members": [["FTP", 20, 21]]},
    {"id": "http", "label": "Web (HTTP)", "members": [["HTTP", 80, 80]]},
    {"id": "https", "label": "Secure web (HTTPS)", "members": [["HTTPS", 443, 443]]},
    {"id": "telnet", "label": "Telnet", "members": [["Telnet", 23, 23]]},
]
_STD_MEMBER = {m[0].lower(): app["id"] for app in FILTER_STD for m in app["members"]}
_STD_GROUP = {"email (pop3, imap, smtp)": "email"}      # stock group row name (seen in the stock app.so table)


# -- parsing helpers (pure; tested against the real fixtures) ---------------------------------
def on_off(word: Optional[str]) -> Optional[bool]:
    w = (word or "").strip().lower()
    if w in ("enable", "enabled", "1", "on", "true", "yes"):
        return True
    if w in ("disable", "disabled", "0", "off", "false", "no"):
        return False
    return None            # e.g. "redacted" in the captures, or an unexpected word


def parse_switch(text: str, rx: str) -> Optional[bool]:
    m = re.search(rx, text or "")
    return on_off(m.group(1)) if m else None


def parse_severity(text: str) -> Optional[str]:
    m = SEVERITY_RE.search(text or "")
    if not m:
        return None
    v = m.group(1).strip().lower()
    return v if v in ("high", "medium", "low") else (v or None)


def _proto_txt(t: str) -> str:
    return PROTO_FROM_TXT.get((t or "").strip().lower(), "both")


def parse_fwd_entries(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in FWD_RE.finditer(text or ""):
        out.append({"name": m.group("name"), "enabled": m.group("en") == "1", "ip": m.group("ip").strip(),
                    "protocol": _proto_txt(m.group("type")), "external": int(m.group("remote")),
                    "internal": int(m.group("local"))})
    return out


def parse_filter_entries(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in FILTER_RE.finditer(text or ""):
        out.append({"name": m.group("name"), "enabled": m.group("en") == "1", "protocol": _proto_txt(m.group("type")),
                    "start": int(m.group("start")), "end": int(m.group("end"))})
    return out


def parse_allowed_entries(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in ALLOWED_RE.finditer(text or ""):
        mode = "list" if m.group("range").strip().lower() == "list" else "range"
        ports = [int(p) for p in re.findall(r"\d+", m.group("list"))] if mode == "list" else []
        out.append({"enabled": m.group("en") == "1",
                    "interfaces": [i for i in (x.strip() for x in m.group("ifaces").split(",")) if i],
                    "mode": mode, "start": int(m.group("start")), "end": int(m.group("end")), "ports": ports,
                    "protocol": _proto_txt(m.group("type"))})
    return out


def _rid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


def group_fwd(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Consecutive single-port entries with the same name/ip/protocol/state and a constant
    external->internal offset become one range rule (that is how ranges are written)."""
    rules: List[Dict[str, Any]] = []
    for e in entries:
        last = rules[-1] if rules else None
        if (last and last["name"] == e["name"] and last["ip"] == e["ip"] and last["protocol"] == e["protocol"]
                and last["enabled"] == e["enabled"] and e["external"] == last["external_end"] + 1
                and e["internal"] - e["external"] == last["internal"] - last["external"]):
            last["external_end"] = e["external"]
            last["entries"] += 1
            continue
        rules.append({"name": e["name"], "enabled": e["enabled"], "ip": e["ip"], "protocol": e["protocol"],
                      "external": e["external"], "external_end": e["external"], "internal": e["internal"],
                      "entries": 1})
    seen = {}
    for r in rules:
        rid = _rid(r["name"], r["ip"], r["protocol"], r["external"], r["external_end"], r["internal"], r["enabled"])
        seen[rid] = seen.get(rid, 0) + 1
        r["id"] = rid if seen[rid] == 1 else "%s-%d" % (rid, seen[rid])
        r["internal_end"] = r["internal"] + (r["external_end"] - r["external"])
        r["app"] = next((p["id"] for p in FWD_PRESETS for mbr in p["members"] if mbr[0] == r["name"]), None)
    return rules


def expand_fwd(rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    n = rule["external_end"] - rule["external"] + 1
    return [{"name": rule["name"], "enabled": rule["enabled"], "ip": rule["ip"], "protocol": rule["protocol"],
             "external": rule["external"] + i, "internal": rule["internal"] + i} for i in range(n)]


def split_filter(entries: List[Dict[str, Any]]):
    """-> (standard app states {id: bool}, custom apps)"""
    have: Dict[str, List[bool]] = {}
    group: Dict[str, bool] = {}
    custom = []
    for e in entries:
        low = e["name"].lower()
        sid = _STD_MEMBER.get(low)
        if sid:
            have.setdefault(sid, []).append(e["enabled"])
        elif low in _STD_GROUP:
            group[_STD_GROUP[low]] = e["enabled"]
        else:
            custom.append(dict(e))
    std = {}
    for app in FILTER_STD:
        states = have.get(app["id"], [])
        std[app["id"]] = (len(states) >= len(app["members"]) and all(states)) or group.get(app["id"], False)
    seen = {}
    for c in custom:
        rid = _rid(c["name"], c["protocol"], c["start"], c["end"], c["enabled"])
        seen[rid] = seen.get(rid, 0) + 1
        c["id"] = rid if seen[rid] == 1 else "%s-%d" % (rid, seen[rid])
    return std, custom


def _overlap(a1, a2, b1, b2) -> bool:
    return a1 <= b2 and b1 <= a2


def _proto_overlap(p, q) -> bool:
    return p == q or "both" in (p, q)


def register(app):
    r = app.router

    # ---- shared readers -------------------------------------------------------------------
    def qc(cmd: str):
        return app.cli_try(QC, cmd)

    def switch(key: str) -> Optional[bool]:
        cmd, rx, _set = SWITCHES[key]
        res = qc(cmd)
        return parse_switch(res.text, rx) if res else None

    def lan() -> Dict[str, str]:
        def load():
            res = qc("get_lan_dhcp_settings")
            ip = res.get("LAN IP address is") if res else None
            mask = res.get("LAN subnet mask is") if res else None
            if not ip or not mask:
                rs = app.rest_try("RouterSettings", {}) or {}
                ip = ip or rs.get("RouterIPv4Address")
                mask = mask or rs.get("RouterSubnetMask")
            ip = ip or "192.168.1.1"
            mask = mask or "255.255.255.0"
            try:
                net = ipaddress.IPv4Network("%s/%s" % (ip, mask), strict=False)
            except ValueError:
                net = ipaddress.IPv4Network("192.168.1.0/24")
            return {"ip": ip, "mask": mask, "network": str(net)}
        return app.cache.get("firewall:lan", 30, load)

    def lan_host(field: str, value: Any) -> str:
        ip = validate.ipv4(field, value)
        info = lan()
        net = ipaddress.IPv4Network(info["network"])
        a = ipaddress.IPv4Address(ip)
        if a not in net:
            raise field_error(field, "Enter an address inside the router's network (%s)" % net)
        if a in (net.network_address, net.broadcast_address):
            raise field_error(field, "The network and broadcast addresses cannot be used")
        if ip == info["ip"]:
            raise field_error(field, "This is the router's own address")
        return ip

    def ippt_active() -> bool:
        def load():
            res = qc("get_ippt")
            return bool(res and res.get_int("Is active", 0) == 1)
        return app.cache.get("firewall:ippt", 10, load)

    def rest_enabled(resource: str, cli_cmd: str, rx: str) -> Optional[bool]:
        d = app.rest_try(resource)
        if isinstance(d, dict) and "Enabled" in d:
            return bool(d["Enabled"])
        res = qc(cli_cmd)
        return parse_switch(res.text, rx) if res else None

    def fwd_enabled() -> Optional[bool]:
        return rest_enabled("PortForwardingSettings", "get_port_forwarding", r"Port forwarding is:\s*\[([^\]\n]*)")

    def filter_enabled() -> Optional[bool]:
        return rest_enabled("PortFilteringSettings", "get_port_filter", r"Port filtering is:\s*\[([^\]\n]*)")

    def dmz() -> Dict[str, Any]:
        d = app.rest_try("DMZSettings")
        if isinstance(d, dict) and "DMZEnabled" in d:
            return {"enabled": bool(d.get("DMZEnabled")), "ip": d.get("DMZIpAddress") or "", "source": "rest"}
        res = qc("get_dmz")
        if not res:
            raise unavailable("The router service does not answer; DMZ settings cannot be read right now.")
        return {"enabled": parse_switch(res.text, r"DMZ status:\s*\[([^\]\n]*)") is True,
                "ip": res.get("DMZ IP") or "", "source": "cli"}

    def set_rest_enabled(resource: str, value: bool, cli_cmd: str, what: str) -> None:
        try:
            app.rest.merge_put(resource, {"Enabled": bool(value)})
            return
        except ApiError as e:
            if e.code not in ("unsupported", "disabled"):
                raise
        app.cli.run(QC, cli_cmd, ["1" if value else "0"], check=True, what=what)

    def notes() -> Dict[str, Any]:
        return {"ippt_active": ippt_active(), "lan": lan()}

    def done(req, action: str, details: Optional[Dict[str, Any]] = None) -> None:
        app.cache.invalidate("firewall:")
        app.cache.invalidate("status")
        app.audit(req, action, details)

    # ---- general --------------------------------------------------------------------------
    @r.route("GET", "/api/firewall/general")
    def get_general(req):
        """Firewall switches: VPN passthrough, WAN ping, IPv6 firewall, HTTP redirect (+ read-only state)."""
        out = {k: switch(k) for k in ("vpn_passthrough", "wan_ping", "ipv6_firewall", "http_redirect",
                                      "dns_passthrough")}
        rs = app.rest_try("RouterSettings", {}) or {}
        if out["vpn_passthrough"] is None and "VPNPasshthroughEnabled" in rs:
            out["vpn_passthrough"] = bool(rs["VPNPasshthroughEnabled"])
        out["ipv6_transparent"] = bool(rs["IPv6TransparentModeEnabled"]) if "IPv6TransparentModeEnabled" in rs else None
        out["ipv6_enabled"] = bool(rs["IPv6Enabled"]) if "IPv6Enabled" in rs else None
        res = qc("get_fw_severity")
        out["severity"] = parse_severity(res.text) if res else None
        return out

    @r.route("PUT", "/api/firewall/general")
    def put_general(req):
        """Change firewall switches (each one is a separate router setting)."""
        data = validate.only_keys(req.json, ["vpn_passthrough", "wan_ping", "ipv6_firewall", "http_redirect"])
        for key in ("vpn_passthrough", "wan_ping", "ipv6_firewall", "http_redirect"):
            if key in data:
                v = validate.boolean(key, data[key])
                app.cli.run(QC, SWITCHES[key][2], ["1" if v else "0"], check=True,
                            what="Changing %s" % key.replace("_", " "))
        done(req, "firewall.general.update", {"keys": sorted(data)})
        return get_general(req)

    # ---- port forwarding -------------------------------------------------------------------
    def fwd_entries() -> List[Dict[str, Any]]:
        def load():
            res = qc("get_port_fwd_list")
            if res is None:
                raise unavailable("The router service does not answer; port forwarding rules cannot be read right now.")
            return parse_fwd_entries(res.text)
        return [dict(e) for e in app.cache.get("firewall:fwd", 3, load)]

    def write_fwd(rules: List[Dict[str, Any]]) -> None:
        entries = [e for rule in rules for e in expand_fwd(rule)]
        if len(entries) > MAX_FWD:
            raise field_error("external_end", "The router holds at most %d forwarded ports in total (ranges count "
                                              "one per port); this change needs %d." % (MAX_FWD, len(entries)))
        ans = [str(len(entries))]
        for i, e in enumerate(entries):
            # range type 2 = "Translate" (the only type the stock UI offers); the daemon maps
            # remote(external) -> local(internal) one port per record and ignores the type.
            ans += ["1" if e["enabled"] else "0", e["name"], e["ip"], "2", str(e["external"]), str(e["internal"]),
                    str(PROTO_NUM[e["protocol"]])]
        app.cli.run(QC, "set_port_fwd_list", ans, check=True, what="Saving port forwarding rules")
        app.cache.invalidate("firewall:")

    def fwd_rule(data: Dict[str, Any], others: List[Dict[str, Any]], partial: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
        base = dict(partial or {})
        if partial and "external" in data and "external_end" not in data and partial["external_end"] == partial["external"]:
            base["external_end"] = None          # a single-port rule stays single when only its port changes
        base.update(data)
        name = validate.text("name", str(base.get("name", "")).strip(), 1, 24)
        if not NAME_RE.match(name):
            raise field_error("name", "Use letters, digits, spaces and common punctuation (no [ or ])")
        if any(o["name"].lower() == name.lower() for o in others):
            raise field_error("name", "Another rule already uses this name")
        ip = lan_host("ip", base.get("ip"))
        proto = validate.choice("protocol", base.get("protocol", "tcp"), list(PROTO_NUM))
        ext = validate.port("external", base.get("external"))
        ext_end = base.get("external_end")
        ext_end = ext if ext_end in (None, "", 0) else validate.port("external_end", ext_end)
        if ext_end < ext:
            raise field_error("external_end", "The last port must not be lower than the first port")
        internal = base.get("internal")
        internal = ext if internal in (None, "", 0) else validate.port("internal", internal)
        if internal + (ext_end - ext) > 65535:
            raise field_error("internal", "The internal range would end above port 65535")
        for o in others:
            if _proto_overlap(o["protocol"], proto) and _overlap(ext, ext_end, o["external"], o["external_end"]):
                raise field_error("external", "Port %s is already forwarded by the rule '%s'"
                                  % (ext if ext == ext_end else "%d-%d" % (ext, ext_end), o["name"]))
        return {"name": name, "enabled": validate.boolean("enabled", base.get("enabled", True)), "ip": ip,
                "protocol": proto, "external": ext, "external_end": ext_end, "internal": internal}

    def fwd_state(req=None) -> Dict[str, Any]:
        rules = group_fwd(fwd_entries())
        try:
            dz_on = dmz()["enabled"]
        except ApiError:
            dz_on = None
        return {"enabled": fwd_enabled(), "rules": rules, "used_entries": sum(x["entries"] for x in rules),
                "max_entries": MAX_FWD, "dmz_enabled": dz_on, "presets": FWD_PRESETS,
                "protocols": [[k, PROTO_LABEL[k]] for k in ("tcp", "udp", "both")], **notes()}

    @r.route("GET", "/api/firewall/forwarding")
    def get_forwarding(req):
        """Port forwarding: on/off, rules (ranges grouped), limits, presets, conflicts."""
        return fwd_state(req)

    @r.route("PUT", "/api/firewall/forwarding")
    def put_forwarding(req):
        """Turn port forwarding on or off (DMZ must be off: the two cannot be combined)."""
        data = validate.only_keys(req.json, ["enabled"])
        if "enabled" in data:
            on = validate.boolean("enabled", data["enabled"])
            if on and dmz()["enabled"]:
                raise conflict("Turn off the DMZ host first: DMZ and port forwarding cannot be used together.",
                               {"field": "enabled"})
            set_rest_enabled("PortForwardingSettings", on, "set_port_forwarding", "Changing port forwarding")
        done(req, "firewall.forwarding.update", {"keys": sorted(data)})
        return fwd_state(req)

    @r.route("GET", "/api/firewall/forwarding/rules")
    def list_fwd(req):
        """Port forwarding rules."""
        return group_fwd(fwd_entries())

    FWD_KEYS = ["name", "enabled", "ip", "protocol", "external", "external_end", "internal", "preset"]

    @r.route("POST", "/api/firewall/forwarding/rules")
    def add_fwd(req):
        """Add a rule, or every member of a preset application (preset + ip)."""
        data = validate.only_keys(req.json, FWD_KEYS + ["id", "entries", "internal_end", "app"])
        rules = group_fwd(fwd_entries())
        new = []
        preset = data.get("preset")
        if preset:
            p = next((x for x in FWD_PRESETS if x["id"] == preset), None)
            if not p:
                raise field_error("preset", "Unknown application")
            for name, port, proto in p["members"]:
                new.append(fwd_rule({"name": name, "ip": data.get("ip"), "protocol": proto, "external": port,
                                     "internal": port, "enabled": data.get("enabled", True)}, rules + new))
        else:
            new.append(fwd_rule({k: v for k, v in data.items() if k in FWD_KEYS}, rules))
        write_fwd(rules + new)
        done(req, "firewall.forwarding.add", {"count": len(new)})
        return group_fwd(fwd_entries())

    def find(rules, rid):
        for i, x in enumerate(rules):
            if x["id"] == rid:
                return i
        raise not_found("This rule no longer exists (the list changed); reload the page")

    @r.route("PUT", "/api/firewall/forwarding/rules/{rid}")
    def put_fwd(req):
        """Change one port forwarding rule."""
        data = validate.only_keys(req.json, FWD_KEYS + ["id", "entries", "internal_end", "app"])
        rules = group_fwd(fwd_entries())
        i = find(rules, req.params["rid"])
        others = rules[:i] + rules[i + 1:]
        rules[i] = fwd_rule({k: v for k, v in data.items() if k in FWD_KEYS and k != "preset"}, others, rules[i])
        write_fwd(rules)
        done(req, "firewall.forwarding.update_rule")
        return group_fwd(fwd_entries())

    @r.route("DELETE", "/api/firewall/forwarding/rules/{rid}")
    def del_fwd(req):
        """Delete one port forwarding rule."""
        rules = group_fwd(fwd_entries())
        i = find(rules, req.params["rid"])
        del rules[i]
        write_fwd(rules)
        done(req, "firewall.forwarding.delete_rule")
        return group_fwd(fwd_entries())

    # ---- port filtering --------------------------------------------------------------------
    def filter_entries() -> List[Dict[str, Any]]:
        def load():
            res = qc("get_port_filter_list")
            if res is None:
                raise unavailable("The router service does not answer; the port filter list cannot be read right now.")
            return parse_filter_entries(res.text)
        return [dict(e) for e in app.cache.get("firewall:filter", 3, load)]

    def write_filter(std: Dict[str, bool], custom: List[Dict[str, Any]]) -> None:
        entries = []
        for sapp in FILTER_STD:
            if std.get(sapp["id"]):
                entries += [{"name": n, "enabled": True, "start": a, "end": b, "protocol": "tcp"}
                            for n, a, b in sapp["members"]]
        entries += custom
        if len(entries) > MAX_FILTER:
            raise field_error("name", "The router holds at most %d filter entries (the Email group uses 10); this "
                                      "change needs %d." % (MAX_FILTER, len(entries)))
        ans = [str(len(entries))]
        for e in entries:
            ans += [e["name"], "1" if e["enabled"] else "0", str(e["start"]), str(e["end"]), str(PROTO_NUM[e["protocol"]])]
        app.cli.run(QC, "set_port_filter_list", ans, check=True, what="Saving the port filter list")
        app.cache.invalidate("firewall:")

    def filter_app(data: Dict[str, Any], others: List[Dict[str, Any]], partial: Optional[Dict[str, Any]] = None):
        base = dict(partial or {})
        if partial and "start" in data and "end" not in data and partial["end"] == partial["start"]:
            base["end"] = None
        base.update(data)
        name = validate.text("name", str(base.get("name", "")).strip(), 1, 24)
        if not NAME_RE.match(name):
            raise field_error("name", "Use letters, digits, spaces and common punctuation (no [ or ])")
        if name.lower() in _STD_MEMBER or name.lower() in _STD_GROUP:
            raise field_error("name", "This name is reserved for a standard application")
        if any(o["name"].lower() == name.lower() for o in others):
            raise field_error("name", "Another application already uses this name")
        start = validate.port("start", base.get("start"))
        end = base.get("end")
        end = start if end in (None, "", 0) else validate.port("end", end)
        if end < start:
            raise field_error("end", "The last port must not be lower than the first port")
        return {"name": name, "enabled": validate.boolean("enabled", base.get("enabled", True)), "start": start,
                "end": end, "protocol": validate.choice("protocol", base.get("protocol", "tcp"), list(PROTO_NUM))}

    def filter_state() -> Dict[str, Any]:
        std, custom = split_filter(filter_entries())
        used = sum(len(a["members"]) for a in FILTER_STD if std.get(a["id"])) + len(custom)
        return {"enabled": filter_enabled(), "standard": std,
                "standard_apps": [{"id": a["id"], "label": a["label"], "entries": len(a["members"]),
                                   "ports": ", ".join(str(m[1]) if m[1] == m[2] else "%d-%d" % (m[1], m[2])
                                                      for m in a["members"])} for a in FILTER_STD],
                "custom": custom, "used_entries": used, "max_entries": MAX_FILTER,
                "allowed_count": sum(1 for v in std.values() if v) + sum(1 for c in custom if c["enabled"]),
                **notes()}

    @r.route("GET", "/api/firewall/filtering")
    def get_filtering(req):
        """Port filtering (outbound white-list): on/off, standard applications, custom applications."""
        return filter_state()

    @r.route("PUT", "/api/firewall/filtering")
    def put_filtering(req):
        """Turn port filtering on/off and choose the standard applications that stay allowed."""
        data = validate.only_keys(req.json, ["enabled", "standard"])
        if "standard" in data:
            if not isinstance(data["standard"], dict):
                raise field_error("standard", "Expected an object")
            std, custom = split_filter(filter_entries())
            for k, v in data["standard"].items():
                if k not in std:
                    raise field_error("standard", "Unknown application: %s" % k)
                std[k] = validate.boolean("standard", v)
            write_filter(std, [{k: c[k] for k in ("name", "enabled", "start", "end", "protocol")} for c in custom])
        if "enabled" in data:
            set_rest_enabled("PortFilteringSettings", validate.boolean("enabled", data["enabled"]), "set_port_filter",
                             "Changing port filtering")
        done(req, "firewall.filtering.update", {"keys": sorted(data)})
        return filter_state()

    @r.route("GET", "/api/firewall/filtering/apps")
    def list_filter(req):
        """Custom applications of the port filter."""
        return split_filter(filter_entries())[1]

    APP_KEYS = ["name", "enabled", "start", "end", "protocol"]

    def custom_plain(custom):
        return [{k: c[k] for k in APP_KEYS} for c in custom]

    @r.route("POST", "/api/firewall/filtering/apps")
    def add_filter(req):
        """Add a custom application (port range) to the white-list."""
        data = validate.only_keys(req.json, APP_KEYS + ["id"])
        std, custom = split_filter(filter_entries())
        new = filter_app({k: v for k, v in data.items() if k in APP_KEYS}, custom)
        write_filter(std, custom_plain(custom) + [new])
        done(req, "firewall.filtering.add")
        return split_filter(filter_entries())[1]

    @r.route("PUT", "/api/firewall/filtering/apps/{rid}")
    def put_filter(req):
        """Change a custom application."""
        data = validate.only_keys(req.json, APP_KEYS + ["id"])
        std, custom = split_filter(filter_entries())
        i = find(custom, req.params["rid"])
        others = custom[:i] + custom[i + 1:]
        custom[i] = filter_app({k: v for k, v in data.items() if k in APP_KEYS}, others, custom[i])
        write_filter(std, custom_plain(custom))
        done(req, "firewall.filtering.update_app")
        return split_filter(filter_entries())[1]

    @r.route("DELETE", "/api/firewall/filtering/apps/{rid}")
    def del_filter(req):
        """Delete a custom application."""
        std, custom = split_filter(filter_entries())
        i = find(custom, req.params["rid"])
        del custom[i]
        write_filter(std, custom_plain(custom))
        done(req, "firewall.filtering.delete_app")
        return split_filter(filter_entries())[1]

    # ---- allowed (inbound) ports -----------------------------------------------------------
    def allowed_entries() -> List[Dict[str, Any]]:
        def load():
            res = qc("get_allowed_port_list")
            if res is None:
                raise unavailable("The router service does not answer; the allowed-port list cannot be read right now.")
            rows = parse_allowed_entries(res.text)
            for row in rows:
                row["id"] = _rid(",".join(row["interfaces"]), row["mode"], row["start"], row["end"],
                                 ",".join(str(p) for p in row["ports"]), row["protocol"], row["enabled"])
            return rows
        return [dict(e) for e in app.cache.get("firewall:allowed", 3, load)]

    def allowed_rule(data: Dict[str, Any], partial: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        base = dict(partial or {})
        base.update(data)
        ifaces = base.get("interfaces")
        if isinstance(ifaces, str):
            ifaces = [x.strip() for x in ifaces.split(",") if x.strip()]
        if not isinstance(ifaces, list) or not ifaces:
            raise field_error("interfaces", "Enter at least one interface name")
        for i in ifaces:
            if not isinstance(i, str) or not IFACE_RE.match(i):
                raise field_error("interfaces", "Interface names use letters, digits, '.', '_' or '-' (max 15)")
        if len(",".join(ifaces)) > 50:
            raise field_error("interfaces", "At most 50 characters of interface names")
        mode = validate.choice("mode", base.get("mode", "range"), ["range", "list"])
        out = {"enabled": validate.boolean("enabled", base.get("enabled", True)), "interfaces": ifaces, "mode": mode,
               "protocol": validate.choice("protocol", base.get("protocol", "tcp"), list(PROTO_NUM)),
               "start": 0, "end": 0, "ports": []}
        if mode == "range":
            out["start"] = validate.port("start", base.get("start"))
            end = base.get("end")
            out["end"] = out["start"] if end in (None, "", 0) else validate.port("end", end)
            if out["end"] < out["start"]:
                raise field_error("end", "The last port must not be lower than the first port")
        else:
            ports = base.get("ports")
            if isinstance(ports, str):
                ports = [p for p in re.split(r"[,\s]+", ports) if p]
            if not isinstance(ports, list) or not ports:
                raise field_error("ports", "Enter one or more ports separated by commas")
            out["ports"] = []
            for p in ports:
                n = validate.port("ports", p)
                if n not in out["ports"]:
                    out["ports"].append(n)
            if len(",".join(str(p) for p in out["ports"])) > 63:
                raise field_error("ports", "The port list is too long (at most 63 characters)")
        return out

    def write_allowed(rules: List[Dict[str, Any]]) -> None:
        if len(rules) > MAX_ALLOWED:
            raise field_error("interfaces", "At most %d allowed-port rules" % MAX_ALLOWED)
        ans = [str(len(rules))]
        for i, x in enumerate(rules):
            if x["mode"] == "list":
                ans += ["1" if x["enabled"] else "0", ",".join(x["interfaces"]), "1",
                        ",".join(str(p) for p in x["ports"]), "0", str(PROTO_NUM[x["protocol"]]), str(i + 1)]
            else:
                ans += ["1" if x["enabled"] else "0", ",".join(x["interfaces"]), "0", str(x["start"]), str(x["end"]),
                        str(PROTO_NUM[x["protocol"]]), str(i + 1)]
        app.cli.run(QC, "set_allowed_port_list", ans, check=True, what="Saving the allowed-port list")
        app.cache.invalidate("firewall:")

    def allowed_state() -> Dict[str, Any]:
        return {"enabled": switch("allowed_ports"), "rules": allowed_entries(), "max_rules": MAX_ALLOWED,
                "suggested_interfaces": [["rmnet_data0", "Mobile WAN"], ["bridge0", "LAN"]], **notes()}

    AP_KEYS = ["enabled", "interfaces", "mode", "start", "end", "ports", "protocol"]

    @r.route("GET", "/api/firewall/allowed")
    def get_allowed(req):
        """Allowed ports: open ports on the router itself for chosen interfaces."""
        return allowed_state()

    @r.route("PUT", "/api/firewall/allowed")
    def put_allowed(req):
        """Turn the allowed-port rules on or off."""
        data = validate.only_keys(req.json, ["enabled"])
        if "enabled" in data:
            v = validate.boolean("enabled", data["enabled"])
            app.cli.run(QC, "set_allowed_ports", ["1" if v else "0"], check=True, what="Changing allowed ports")
        done(req, "firewall.allowed.update", {"keys": sorted(data)})
        return allowed_state()

    @r.route("GET", "/api/firewall/allowed/rules")
    def list_allowed(req):
        """Allowed-port rules."""
        return allowed_entries()

    @r.route("POST", "/api/firewall/allowed/rules")
    def add_allowed(req):
        """Add an allowed-port rule."""
        data = validate.only_keys(req.json, AP_KEYS + ["id"])
        rules = allowed_entries()
        rules.append(allowed_rule({k: v for k, v in data.items() if k in AP_KEYS}))
        write_allowed(rules)
        done(req, "firewall.allowed.add")
        return allowed_entries()

    @r.route("PUT", "/api/firewall/allowed/rules/{rid}")
    def put_allowed_rule(req):
        """Change an allowed-port rule."""
        data = validate.only_keys(req.json, AP_KEYS + ["id"])
        rules = allowed_entries()
        i = find(rules, req.params["rid"])
        rules[i] = allowed_rule({k: v for k, v in data.items() if k in AP_KEYS}, rules[i])
        write_allowed(rules)
        done(req, "firewall.allowed.update_rule")
        return allowed_entries()

    @r.route("DELETE", "/api/firewall/allowed/rules/{rid}")
    def del_allowed(req):
        """Delete an allowed-port rule."""
        rules = allowed_entries()
        del rules[find(rules, req.params["rid"])]
        write_allowed(rules)
        done(req, "firewall.allowed.delete_rule")
        return allowed_entries()

    # ---- DMZ ------------------------------------------------------------------------------
    def dmz_state() -> Dict[str, Any]:
        d = dmz()
        return {"enabled": d["enabled"], "ip": d["ip"], "forwarding_enabled": fwd_enabled(), **notes()}

    @r.route("GET", "/api/firewall/dmz")
    def get_dmz(req):
        """DMZ host (IPv4 only): every unsolicited inbound connection goes to this LAN device."""
        return dmz_state()

    @r.route("PUT", "/api/firewall/dmz")
    def put_dmz(req):
        """Set the DMZ host and turn it on/off (port forwarding must be off)."""
        data = validate.only_keys(req.json, ["enabled", "ip"])
        cur = dmz()
        on = validate.boolean("enabled", data.get("enabled", cur["enabled"]))
        ip = data.get("ip", cur["ip"])
        ip = "" if ip in (None, "") else lan_host("ip", ip)
        if on and not ip:
            raise field_error("ip", "Enter the address of the device that should receive all inbound traffic")
        if on and fwd_enabled():
            raise conflict("Turn off port forwarding first: DMZ and port forwarding cannot be used together.",
                           {"field": "enabled"})
        changes = {"DMZEnabled": on}
        if ip:
            changes["DMZIpAddress"] = ip
        try:
            app.rest.merge_put("DMZSettings", changes)
        except ApiError as e:
            if e.code not in ("unsupported", "disabled"):
                raise
            if not ip:
                raise field_error("ip", "Enter the DMZ host address")
            app.cli.run(QC, "set_dmz", ["1" if on else "0", ip], check=True, what="Saving the DMZ host")
        done(req, "firewall.dmz.update", {"enabled": on})
        return dmz_state()

    app.features.add("firewall", lambda: True)
