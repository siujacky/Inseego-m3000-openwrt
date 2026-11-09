"""Mock: router2 firewall commands (mifi_qcmap_cli) and the VPN client (vpn_cli).

Outputs reproduce the vendor printf formats decoded from the binaries (see
adapters/specs/firewall.py), so the same parsers run in --mock and on the device.  State lives
in store.state["fw"] / store.state["vpn"] (per App instance).  The router2 library validation
(name length/charset, protocol, ports, range type) is mirrored so invalid input fails the same
way (rc 2).
"""
import ipaddress
import os
import time

from ..adapters.mock import cli_handler

QC = "mifi_qcmap_cli"
VPN = "vpn_cli"
OK_QC = "MIFI_QCMAP: success."
BAD_QC = "MIFI_QCMAP: one or more bad parameters passed into a function."
PROTO_TXT = {1: "TCP", 2: "UDP"}
VPN_RC = {0: "VPN: success.", 1: "VPN: general error.", 2: "VPN: one or more bad parameters passed into a function.",
          4: "VPN: the feature is unsupported.",
          6: "VPN: a device reset is required for the operation to take effect.",
          1001: "VPN: OpenVPN uploaded does not contain configuration file.",
          1002: "VPN: OpenVPN uploaded contain invalid configuration file.",
          1003: "VPN: OpenVPN uploaded contains missed configuration files."}
P_TYPE = ("Enter server type (0-NONE, 1-PPTP, 2-OpenVPN, 3-L2TP Over IPSec, 4-IPSec, 5-OpenVPN with config files "
          "6- WiredGuard VPN): ")


def _fw(store):
    with store.lock:
        st = store.state.get("fw")
        if st is None:
            st = store.state["fw"] = {
                "vpn_passthrough": True, "dns_passthrough": True, "http_redirect": True, "wan_ping": False,
                "ipv6_firewall": True, "allowed_ports": False, "severity": "Medium",
                "fwd": [], "filter": [], "allowed": [], "pf_enabled": False, "pfl_enabled": False}
        return st


def _head(cmd, fn, rc=0, msg=OK_QC):
    return "command [%s]\n\n%s returned %d (%s)\n" % (cmd, fn, rc, msg)


def _word(v, yes="Enable", no="Disable"):
    return yes if v else no


def _int(s, default=None):
    try:
        return int(str(s), 0)
    except (TypeError, ValueError):
        return default


def _name_ok(name):
    return 0 < len(name) <= 27 and all(0x20 <= ord(c) <= 0x7e for c in name)


# ---- simple switches -----------------------------------------------------------------------
_GETS = {
    "get_vpnpassthrough": ("cmd_router2_get_vpnpassthrough_status", "VPN passthrough is:   [%s]", "vpn_passthrough"),
    "get_dnspassthrough": ("cmd_router2_get_dnspassthrough_status", "DNS passthrough is:   [%s]", "dns_passthrough"),
    "get_http_redirect": ("cmd_router2_get_http_redirect_status", "HTTP redirect is:   [%s]", "http_redirect"),
    "get_icmp_response": ("cmd_router2_get_icmp_response", "ICMP status on the WAN [%s", "wan_ping"),
    "get_ipv6_firewall": ("cmd_router2_get_ipv6_firewall_status", "Ipv6 firewall status is:  [%s]", "ipv6_firewall"),
}
_SETS = {
    "set_vpnpassthrough": ("cmd_router2_set_vpnpassthrough_status", "vpn_passthrough"),
    "set_http_redirect": ("cmd_router2_set_http_redirect_status", "http_redirect"),
    "set_icmp_response": ("cmd_router2_set_icmp_response", "wan_ping"),
    "set_ipv6_firewall": ("cmd_router2_set_ipv6_firewall_status", "ipv6_firewall"),
    "set_allowed_ports": ("cmd_router2_set_allowed_ports_status", "allowed_ports"),
    "set_port_forwarding": ("cmd_router2_set_port_forwarding_status", "pf_enabled"),
    "set_port_filter": ("cmd_router2_set_port_filtering_status", "pfl_enabled"),
}


def _make_get(cmd, fn, fmt, key):
    @cli_handler(QC, cmd)
    def _h(store, answers, args):
        return _head(cmd, fn) + (fmt % _word(_fw(store)[key])) + "\n"
    return _h


def _make_set(cmd, fn, key):
    @cli_handler(QC, cmd)
    def _h(store, answers, args):
        vals = list(args) or list(answers)
        v = _int(vals[0] if vals else None)
        if v not in (0, 1):
            return _head(cmd, fn, 2, BAD_QC)
        _fw(store)[key] = bool(v)
        return _head(cmd, fn)
    return _h


for _c, (_fn, _fmt, _k) in _GETS.items():
    _make_get(_c, _fn, _fmt, _k)
for _c, (_fn, _k) in _SETS.items():
    _make_set(_c, _fn, _k)


@cli_handler(QC, "get_allowed_ports")
def _get_allowed_status(store, answers, args):
    return (_head("get_allowed_ports", "cmd_router2_get_allowed_ports_status") +
            "Allowed Port Status:   [%s]\n" % _word(_fw(store)["allowed_ports"], "Enabled", "Disabled"))


@cli_handler(QC, "get_fw_severity")
def _get_severity(store, answers, args):
    return _head("get_fw_severity", "cmd_get_firewall_severity") + "Firewall Severity is:   [%s]\n" % _fw(store)["severity"]


@cli_handler(QC, "set_dmz")
def _set_dmz(store, answers, args):
    return _head("set_dmz", "cmd_router2_set_dmz_settings")


# ---- port forwarding list ------------------------------------------------------------------
@cli_handler(QC, "get_port_fwd_list")
def _get_fwd(store, answers, args):
    out = _head("get_port_fwd_list", "cmd_router2_get_port_fwd_app_list") + "\nPort forwarding List...\n"
    for e in _fw(store)["fwd"]:
        out += ("fwd rule name=[%s], enabled=[%d], ip=[%s], type=[%s], local port=[%d], remote port=[%d]\n"
                % (e["name"], e["enable"], e["ip"], PROTO_TXT.get(e["proto"], "TCP & UDP"), e["local"], e["remote"]))
    return out


@cli_handler(QC, "set_port_fwd_list")
def _set_fwd(store, answers, args):
    fn = "cmd_router2_set_port_fwd_app_list"
    n = _int(args[0]) if args else None
    if n is None or n < 0 or n > 30 or len(args) != 1 + 7 * n:
        return "command [set_port_fwd_list]\n\nEnter the number of records: " + "%s returned 2 (%s)\n" % (fn, BAD_QC)
    rows, out = [], "command [set_port_fwd_list]\n\n"
    for i in range(n):
        en, name, ip, rtype, remote, local, proto = args[1 + 7 * i:8 + 7 * i]
        rec = {"enable": _int(en), "name": name, "ip": ip, "range": _int(rtype), "remote": _int(remote),
               "local": _int(local), "proto": _int(proto)}
        out += ("Record[%d]: Enable:[%s] instance:[%d] name:[%s] ipaddr:[%s] remote:[%s] Local:[%s] type:[%s]\n"
                % (i + 1, en, i + 1, name, ip, remote, local, proto))
        try:
            ipaddress.IPv4Address(ip)
            ip_ok = True
        except ValueError:
            ip_ok = False
        if (rec["enable"] not in (0, 1) or not _name_ok(name) or not ip_ok or rec["proto"] not in (1, 2, 3)
                or not (0 <= (rec["remote"] or -1) < 65536) or not (0 <= (rec["local"] or -1) < 65536)
                or rec["range"] not in (0, 1, 2) or (rec["range"] == 0 and rec["remote"] > rec["local"])):
            return out + "%s returned 2 (%s)\n" % (fn, BAD_QC)
        rows.append(rec)
    _fw(store)["fwd"] = rows
    return out + "%s returned 0 (%s)\n" % (fn, OK_QC)


# ---- port filter list ----------------------------------------------------------------------
@cli_handler(QC, "get_port_filter_list")
def _get_filter(store, answers, args):
    out = _head("get_port_filter_list", "cmd_router2_get_port_filter_app_list")
    for e in _fw(store)["filter"]:
        out += ("filter rule name=[%s], enabled=[%d], type=[%s], start port=[%d], end port=[%d]\n"
                % (e["name"], e["enable"], PROTO_TXT.get(e["proto"], "TCP & UDP"), e["start"], e["end"]))
    return out


@cli_handler(QC, "set_port_filter_list")
def _set_filter(store, answers, args):
    fn = "cmd_router2_set_port_filter_app_list"
    n = _int(args[0]) if args else None
    if n is None or n < 0 or n > 25 or len(args) != 1 + 5 * n:
        return "command [set_port_filter_list]\n\nEnter the number of records: %s returned 2 (%s)\n" % (fn, BAD_QC)
    rows, out = [], "command [set_port_filter_list]\n\n"
    for i in range(n):
        name, en, start, end, proto = args[1 + 5 * i:6 + 5 * i]
        rec = {"name": name, "enable": _int(en), "start": _int(start, -1), "end": _int(end, -1), "proto": _int(proto)}
        out += "Record[%d]: Enable:[%s] name:[%s] start:[%s] end:[%s] type:[%s]\n" % (i + 1, en, name, start, end, proto)
        if (rec["enable"] not in (0, 1) or not _name_ok(name) or rec["proto"] not in (1, 2, 3)
                or not 0 <= rec["start"] < 65536 or not 0 <= rec["end"] < 65536
                or (rec["end"] != 0 and rec["start"] > rec["end"])):
            return out + "%s returned 2 (%s)\n" % (fn, BAD_QC)
        rows.append(rec)
    _fw(store)["filter"] = rows
    return out + "%s returned 0 (%s)\n" % (fn, OK_QC)


# ---- allowed ports list --------------------------------------------------------------------
@cli_handler(QC, "get_allowed_port_list")
def _get_allowed(store, answers, args):
    out = _head("get_allowed_port_list", "cmd_router2_get_allowed_port_list") + "\nAllowed Port List...\n"
    for e in _fw(store)["allowed"]:
        out += ("Allowed Port rule : enabled=[%d], interface name=[%s], port list=[%s], type=[%s], range=[%s], "
                "start/ext port=[%d], end/int port=[%d]\n"
                % (e["enable"], e["ifaces"], e["list"], PROTO_TXT.get(e["proto"], "TCP & UDP"),
                   "List" if e["type"] == 1 else "Range", e["start"], e["end"]))
    return out


@cli_handler(QC, "set_allowed_port_list")
def _set_allowed(store, answers, args):
    fn = "cmd_router2_set_allowed_port_list"
    n = _int(args[0]) if args else None
    head = "command [set_allowed_port_list]\n\nTotal Args:%d, Records Count:%s\n" % (len(args) + 2, args[0] if args else "")
    if n is None or n < 0 or n > 10 or len(args) != 1 + 7 * n:
        return head + "Enter the number of records: %s returned 2 (%s)\n" % (fn, BAD_QC)
    rows = []
    for i in range(n):
        en, ifaces, ptype, a, b, proto, _inst = args[1 + 7 * i:8 + 7 * i]
        t = _int(ptype)
        rec = {"enable": _int(en), "ifaces": ifaces, "type": t, "proto": _int(proto),
               "start": _int(a, 0) if t == 0 else 0, "end": _int(b, 0) if t == 0 else 0, "list": a if t == 1 else ""}
        if rec["enable"] not in (0, 1) or t not in (0, 1) or rec["proto"] not in (1, 2, 3) or len(ifaces) > 50:
            return head + "%s returned 2 (%s)\n" % (fn, BAD_QC)
        rows.append(rec)
    _fw(store)["allowed"] = rows
    return head + "%s returned 0 (%s)\n" % (fn, OK_QC)


# ================================== vpn_cli =================================================
def _vpn(store):
    with store.lock:
        st = store.state.get("vpn")
        if st is None:
            st = store.state["vpn"] = {"auto_connect": 0, "ipsec": 0, "wguard": 0, "openvpn": None, "wg": {},
                                       "ipsec_t": {}, "conn": {}}
        return st


def _rc(fn, rc):
    return "%s returned %d (%s)\n" % (fn, rc, VPN_RC.get(rc, "VPN: unknown error."))


@cli_handler(VPN, "get_auto_connect")
def _get_auto(store, answers, args):
    return ("command [get_auto_connect]\n\nvpn_get_auto_connect returned 0 (VPN: success.)\n"
            "VPN auto connect status (0=Disable, 1=Enable) is: [%d]\n" % _vpn(store)["auto_connect"])


@cli_handler(VPN, "get_ipsec_vpn")
def _get_ipsec_svc(store, answers, args):
    return ("command [get_ipsec_vpn]\n\nvpn_get_ipsec_vpn_enabled returned 0x0 (VPN: success.)\n"
            "VPN IPSec VPN Service status (0=Disable, 1=Enable) is: [%d]\n" % _vpn(store)["ipsec"])


@cli_handler(VPN, "get_wguard_vpn")
def _get_wg_svc(store, answers, args):
    return ("command [get_wguard_vpn]\n\nvpn_get_wguard_vpn_enabled returned 0x0 (VPN: success.)\n"
            "VPN WGuard VPN Service status (0=Disable, 1=Enable) is: [%d]\n" % _vpn(store)["wguard"])


@cli_handler(VPN, "set_auto_connect")
def _set_auto(store, answers, args):
    v = _int((list(args) or list(answers) or [None])[0])
    if v not in (0, 1):
        return "command [set_auto_connect]\n\n" + _rc("vpn_set_auto_connect", 2)
    _vpn(store)["auto_connect"] = v
    return "command [set_auto_connect]\n\n" + _rc("vpn_set_auto_connect", 0)


def _make_service(cmd, fn, key):
    @cli_handler(VPN, cmd)
    def _h(store, answers, args):
        v = _int((list(args) or list(answers) or [None])[0])
        st = _vpn(store)
        if v not in (0, 1):
            return "command [%s]\n\n%s" % (cmd, _rc(fn, 2))
        changed = st[key] != v
        st[key] = v
        if changed:   # saved, but needs a restart: vpn_cli then asks Y/N and reads EOF (argv mode)
            return ("command [%s]\n\n%sThis VPN settings requires reset of device:\nDo you want to reset the device Y/N:"
                    % (cmd, _rc(fn, 6)))
        return "command [%s]\n\n%s" % (cmd, _rc(fn, 0))
    return _h


_make_service("set_ipsec_vpn", "vpn_set_ipsec_vpn_enabled", "ipsec")
_make_service("set_wguard_vpn", "vpn_set_wguard_vpn_enabled", "wguard")


def _openvpn_block(cfg):
    return ("\nvpn_get_openvpn_config_file_settings returned 0 (VPN: success.)\n"
            "OpenVPN uploaded config file name: [%s]\nOpenVPN server URL: [%s]\nOpenVPN server username: [%s]\n"
            "OpenVPN server password: [%s]\n" % (cfg["name"], cfg["url"], cfg["user"], cfg["password"]))


def _wg_block(i, t):
    t = t or {}
    g = lambda k, d="": t.get(k, d)   # noqa: E731
    return ("\nvpn_get_wguard_settings returned 0 (VPN: success.)\nWGuard VPN Tunnel Index[%d] Details:\n"
            "WGuard VPN Tunnel Name:%s\nTunnel Connection(0-Disable, 1-Enable):%s\n"
            "Tunnel Startup(0-Manually, 1-Automatically):%s\nconnection retries:%s\nKeys Path:%s\n"
            "Private Key file name:%s\nPrivate Key content:%s\nPublic Key content:%s\nTunnel IP:%s\nPeer IP:%s\n"
            "Allowed IPs/Subnets:%s\nEndPoint IP:%s\nListen Port:%s\nEndPoint Port:%s\nKeepAlive :%s\nTunnel State: %d\n"
            % (i, g("name"), g("enable", 0), g("startup", 0), g("retries", 0), g("path"), g("keyfile"), g("pkey"),
               g("peer"), g("tun_ip"), g("peer_ip"), g("allowed"), g("endpoint"), g("listen", 0), g("eport", 0),
               g("keepalive", 0), 0))


def _ipsec_block(i, t):
    t = t or {}
    g = lambda k, d="": t.get(k, d)   # noqa: E731
    out = ("\nvpn_get_ipsec_settings returned 0 (VPN: success.)\n"
           "IPSec VPN Automatically(1) or Manually(0): %s\nIPsec VPN Tunnel Enabled(0-Disable, 1-Enable): %s\n"
           "IPSec VPN Left Firewall Enabled(0-Disable, 1-Enable): %s\nIPSec VPN Tunnel Name:[%s]\nIPSec IKE Version:%s\n"
           "IPSec VPN Anonymous mode (0-Disable, 1-Enable): %s\nIPSec VPN Local ID:[%s]\nIPSec VPN Remote ID:[%s]\n"
           "IPSec IKE Client Authentication Type(0-None 1-Pre shared key, 2-EAP_MSCHAPv2, 3-Pub Key/RSA "
           "certificates): [%s]\n"
           "IPSec IKE Server Authentication Type(0- None, 1-Pre shared key, 2-EAP_MSCHAPv2, 3-Pub Key/RSA "
           "certificates): [%s]\n"
           "IPSec Local IP address: %s\nIPSec Local Network Subnet: %s\nIPSec Remote IP address: %s\n"
           "IPSec Remote Network Subnet: %s\nIPSec IKE Lifetime: %s (default in seconds)\nIPSec Phase1 Proposal: %s\n"
           "IPSec Authentication Key Lifetime: %s (default in seconds)\nIPSec Phase2 Proposal: %s\n"
           "DPD Enabled(0-Disable, 1-Enable: %s)\nIPSec VPN State: %d\n"
           % (g("startup", 0), g("enable", 0), g("lfw", 0), g("name"), g("ike", 0), g("anon", 0), g("lid"), g("rid"),
              g("cauth", 0), g("sauth", 0), g("lip"), g("lsub"), g("rip"), g("rsub"), g("ike_lt"), g("p1"), g("key_lt"),
              g("p2"), g("dpd", 0), 0))
    if str(g("dpd", 0)) == "1":
        out += ("DPD Action(0-None, 1-CLEAR, 2-HOLD, 3-RESTART): %s\nDPD Delay: %s seconds\nDPD Timeout: %s seconds\n"
                % (g("dpd_action", 0), g("dpd_delay", 0), g("dpd_timeout", 0)))
    return out


@cli_handler(VPN, "get_settings")
def _get_settings(store, answers, args):
    st = _vpn(store)
    t = _int(answers[0] if answers else None)
    out = ("command [get_settings]\n\nVPN server type is(0-NONE, 1-PPTP, 2-OpenVPN, 3-L2TP over IPsec, 4-IPSec, "
           "5-OpenVPN with config files, 6- WiredGuard VPN):")
    if t is None or not 1 <= t <= 6:
        return out + "Invalid VPN Server Type:%s\n" % t
    if t == 6:
        i = _int(answers[1] if len(answers) > 1 else None)
        if i is None or not 0 <= i <= 2:
            return out + "Enter WGuard Tunnel Index[0-2]:Invalid Tunnel Index:%s\n" % i
        return out + "Enter WGuard Tunnel Index[0-2]:" + _wg_block(i, st["wg"].get(i))
    if t in (1, 3):
        return out + "vpn_get_settings returned 0x2 (VPN: one or more bad parameters passed into a function.)\n"
    out += "vpn_get_settings returned 0x0 (VPN: success.)\n"
    if t == 5 and st["openvpn"]:
        return out + _openvpn_block(st["openvpn"])
    if t == 4 and st["ipsec_t"]:
        i = _int(answers[1] if len(answers) > 1 else None)
        if i is None or not 0 <= i <= 5:
            return out + "Enter IPSec Tunnel Index[0-5]:Invalid Tunnel Index:%s\n" % i
        return out + "Enter IPSec Tunnel Index[0-5]:" + _ipsec_block(i, st["ipsec_t"].get(i))
    return out          # TunnelType not set yet -> nothing configured for this type


def _stage_files(path):
    files = []
    if path and os.path.isdir(path):
        for n in sorted(os.listdir(path)):
            p = os.path.join(path, n)
            if os.path.isfile(p):
                with open(p, "rb") as fh:
                    files.append({"name": n, "data": fh.read()})
    return files


@cli_handler(VPN, "validate_openvpn_config_files")
def _validate(store, answers, args):
    from ..services.vpn import validate_ovpn_files
    path = answers[0] if answers else ""
    files = _stage_files(path)
    res = validate_ovpn_files(files)
    out = "command [validate_openvpn_config_files]\n\nEnter OpenVPN uploaded config file path: "
    if not files:
        return out + _rc("vpn_validate_openvpn_config_file_settings", 1001)
    if res["problems"]:
        missing = ""
        for p in res["problems"]:
            if "was not uploaded" in p:
                missing = p.split("'")[1]
        rc = 1003 if missing else 1002
        return (out + _rc("vpn_validate_openvpn_config_file_settings", rc) +
                "config file name:[%s]\nconfig URL:[%s]\nMissed configuration file name is: [%s]\n"
                % (res["config_file"], res["server"], missing))
    return (out + _rc("vpn_validate_openvpn_config_file_settings", 0) +
            "config file name:[%s]\nconfig URL:[%s]\nMissed configuration file name is: []\n"
            % (res["config_file"], res["server"]))


@cli_handler(VPN, "set_settings")
def _set_settings(store, answers, args):
    st = _vpn(store)
    a = list(answers)
    out = "command [set_settings]\n\n" + P_TYPE + "Clear/Set server configuration(Set-0, Clear-1): "
    t, clear = _int(a[0] if a else None), _int(a[1] if len(a) > 1 else None)
    if t == 5:
        if clear:
            st["openvpn"] = None
            st["conn"].pop((5, 0), None)
            return out + _rc("vpn_set_settings", 0)
        _retries, path, name, url, user, pw = a[2:8]
        if not os.path.isdir(path) or not os.path.isfile(os.path.join(path, name)):
            return out + _rc("vpn_set_openvpn_config_file_settings", 1004)
        st["openvpn"] = {"name": name, "url": url, "user": user, "password": pw, "retries": _int(_retries, 0)}
        return out + _rc("vpn_set_openvpn_config_file_settings", 0) + _rc("vpn_set_settings", 0)
    if t == 6:
        i = _int(a[2] if len(a) > 2 else None)
        if i is None or not 0 <= i <= 2:
            return out + "Invalid tunnel index or Exceed tunnel limit i.e. 3.\n"
        if clear:
            st["wg"].pop(i, None)
            st["conn"].pop((6, i), None)
            return out + _rc("vpn_set_settings", 0)
        keys = ["idx", "enable", "startup", "name", "retries", "path", "keyfile", "pkey", "peer", "tun_ip", "peer_ip",
                "allowed", "endpoint", "listen", "eport", "keepalive"]
        st["wg"][i] = dict(zip(keys, a[2:18]))
        return out + _rc("vpn_set_wguard_settings", 0)
    if t == 4:
        i = _int(a[2] if len(a) > 2 else None)
        if i is None or not 0 <= i <= 5:
            return out + "Invalid tunnel index or Exceed tunnel limit i.e. 6.\n"
        if clear:
            st["ipsec_t"].pop(i, None)
            st["conn"].pop((4, i), None)
            return out + _rc("vpn_set_settings", 0)
        v = a[2:]
        rec = {"startup": v[1], "enable": v[2], "name": v[3], "lfw": v[4], "anon": v[5], "lid": v[6], "rid": v[7],
               "cauth": v[8]}
        k = 9
        if v[8] == "1":
            rec["cpsk"], k = v[k], k + 1
        else:
            rec["cuser"], rec["cpass"], k = v[k], v[k + 1], k + 2
        rec["sauth"] = v[k]
        k += 1
        if rec["sauth"] == "1":
            rec["spsk"], k = v[k], k + 1
        else:
            rec["suser"], rec["spass"], k = v[k], v[k + 1], k + 2
        rec["ike"] = v[k]
        k += 1
        if v[8] != "2":
            rec["lip"], rec["lsub"], k = v[k], v[k + 1], k + 2
        rec["rip"], rec["rsub"], rec["ike_lt"], rec["p1"], rec["key_lt"], rec["p2"], rec["dpd"] = v[k:k + 7]
        k += 7
        if rec["dpd"] == "1":
            rec["dpd_action"], rec["dpd_delay"], rec["dpd_timeout"] = v[k:k + 3]
        st["ipsec_t"][i] = rec
        return out + _rc("vpn_set_ipsec_settings", 0) + _rc("vpn_set_settings", 0)
    return out + _rc("vpn_set_settings", 2)


def _conn_key(answers):
    t = _int(answers[0] if answers else None)
    i = _int(answers[1]) if t in (4, 6) and len(answers) > 1 else 0
    return t, i


def _configured(st, t, i):
    return bool(st["openvpn"]) if t == 5 else (i in st["wg"]) if t == 6 else (i in st["ipsec_t"]) if t == 4 else False


@cli_handler(VPN, "connect_vpn")
def _connect(store, answers, args):
    st = _vpn(store)
    t, i = _conn_key(answers)
    out = "command [connect_vpn]\n\n" + P_TYPE + ("Enter Tunnel Index:" if t in (4, 6) else "")
    if not _configured(st, t, i):
        return out + _rc("connect_vpn", 1)
    st["conn"][(t, i)] = {"since": time.time()}
    return out + _rc("connect_vpn", 0)


@cli_handler(VPN, "disconnect_vpn")
def _disconnect(store, answers, args):
    st = _vpn(store)
    t, i = _conn_key(answers)
    st["conn"].pop((t, i), None)
    return "command [disconnect_vpn]\n\n" + P_TYPE + ("Enter Tunnel Index:" if t in (4, 6) else "") + _rc("disconnect_vpn", 0)


@cli_handler(VPN, "get_connection_statistics")
def _stats(store, answers, args):
    st = _vpn(store)
    t, i = _conn_key(answers)
    out = "command [get_connection_statistics]\n\n" + P_TYPE
    if t not in (2, 4, 5, 6):
        return out + "cmd_get_connection_statistics:1345 Unsupported Server type[%s]\n" % t
    if t in (4, 6):
        out += "Enter Tunnel Index:cmd_get_connection_statistics:1336 Tunnel type:%d, idx=%d\n" % (t, i)
    c = st["conn"].get((t, i))
    state = 2 if c else (3 if _configured(st, t, i) else 0)
    out += ("Server Type :-\n\t0-NONE\n\t1-PPTP\n\t2-OpenVPN\n\t3-L2TP Over IPSec\n\t4-IPSec VPN\n"
            "\t5-OpenVPN with configuration\n\t6-WGuard VPN\nconnection state :-\n\t0-VPN_STATE_NOTCONNECTED\n"
            "\t1-VPN_STATE_CONNECTING\n\t2-VPN_STATE_CONNECTED\n\t3-VPN_STATE_DISCONNECTED\n\t4-VPN_STATE_CONNECT_FAILED\n"
            "\t5-VPN_STATE_STALE_HANDSHAKE\n\nVpn server type : [%d]\nConnection state: [%d]\n" % (t, state))
    if state == 2:
        up = int(time.time() - c["since"])
        out += ("Connection IPv4 address: [10.8.%d.6]\nConnection IPv4 subnet address: [255.255.255.0]\n"
                "Connection IPv4 Tx Bytes: [%d]\nConnection IPv4 Rx Bytes: [%d]\n"
                "Connection Up Time: [%d days %02d:%02d:%02d]\n"
                % (t, 4096 + up * 1200, 8192 + up * 5400, up // 86400, up % 86400 // 3600, up % 3600 // 60, up % 60))
    return out
