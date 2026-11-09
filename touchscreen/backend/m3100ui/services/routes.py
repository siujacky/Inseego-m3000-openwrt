"""Static routes (vendor router2 feature with no stock UI) and the kernel routing tables.

- mifi_qcmap_cli get/set_static_routes (feature on/off) and get/set_static_route_list (the whole list
  is rewritten on every change: N records of family, network/prefix, gateway, WAN index, metric).
- `ip route` / `ip -6 route` (read-only) for the tables the kernel actually uses.
"""
import hashlib
import ipaddress
import re
from typing import Any, Dict, List

from .. import validate
from ..errors import ApiError, field_error, unavailable

MAX_ROUTES = 25                       # argv form of set_static_route_list accepts at most 25 records
# router2_utils_get_wan_type_str indexes (prompted as "Interface Type Index")
WAN_INDEX = {"No WAN": 0, "WWAN": 1, "WiFi WAN": 2, "Ethernet WAN": 3, "USB WAN": 4, "APPs WAN": 5,
             "DM WAN": 6, "WGVPN_0": 7, "WGVPN_1": 8, "WGVPN_2": 9}
IFACES = {"wwan": 1, "wifi": 2, "ethernet": 3, "usb": 4, "wireguard1": 7, "wireguard2": 8, "wireguard3": 9}
IFACE_BY_INDEX = {v: k for k, v in IFACES.items()}
IFACE_TEXT = {"wwan": "Mobile network", "wifi": "Wi-Fi client WAN", "ethernet": "Ethernet WAN", "usb": "USB WAN",
              "wireguard1": "WireGuard tunnel 1", "wireguard2": "WireGuard tunnel 2", "wireguard3": "WireGuard tunnel 3"}
ROUTE_RE = re.compile(r"RouteType\[(IPV4|IPV6)\]\s*Network\[([^\]]*)\]\s*Gateway\[([^\]]*)\]\s*"
                      r"InterfaceType\[([^\]]*)\]\s*Metric\[(-?\d+)\]")

MOCK_V4 = ("default dev rmnet_data0 scope link\n100.64.12.0/24 dev rmnet_data0 proto kernel scope link src 100.64.12.34\n"
           "192.168.1.0/24 dev bridge0 proto kernel scope link src 192.168.1.1\n")
MOCK_V6 = ("2001:db8:1234:5678::/64 dev rmnet_data0 proto kernel metric 256 pref medium\n"
           "fe80::/64 dev bridge0 proto kernel metric 256 pref medium\ndefault via fe80::1 dev rmnet_data0 metric 1024 pref medium\n")


def route_id(rt: Dict[str, Any]) -> str:
    key = "%s|%s|%s|%s|%s" % (rt["family"], rt["network"], rt["gateway"], rt["interface"], rt["metric"])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def parse_route_list(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in ROUTE_RE.finditer(text or ""):
        idx = WAN_INDEX.get(m.group(4).strip())
        iface = IFACE_BY_INDEX.get(idx, "wan%s" % idx if idx is not None else m.group(4).strip())
        rt = {"family": 4 if m.group(1) == "IPV4" else 6, "network": m.group(2).strip(), "gateway": m.group(3).strip(),
              "interface": iface, "interface_label": IFACE_TEXT.get(iface, m.group(4).strip()), "metric": int(m.group(5))}
        rt["id"] = route_id(rt)
        out.append(rt)
    return out


def parse_ip_route(text: str) -> List[Dict[str, Any]]:
    """`ip route` lines -> dicts (dest, via, dev, proto, scope, src, metric)."""
    out = []
    for line in (text or "").splitlines():
        f = line.split()
        if not f or f[0].startswith("("):
            continue
        rt: Dict[str, Any] = {"dest": f[0], "raw": line.strip()}
        if f[0] in ("unreachable", "blackhole", "prohibit", "local", "broadcast", "multicast", "throw") and len(f) > 1:
            rt["type"], rt["dest"] = f[0], f[1]
        for i, w in enumerate(f[:-1]):
            if w in ("via", "dev", "proto", "scope", "src", "metric", "table", "pref"):
                rt[w] = f[i + 1]
        out.append(rt)
    return out


def run_set(app, cli, cmd, answers, what) -> None:
    res = app.cli.run(cli, cmd, answers)
    if not res.ok and res.rc != 6:
        res.raise_for_error(what)


def register(app):
    r = app.router

    def enabled_state():
        res = app.cli_try("mifi_qcmap_cli", "get_static_routes")
        if res is None:
            raise unavailable("Static routes are not available on this unit")
        v = (res.get("Static Routes Feature is") or "").lower()
        return v.startswith("enable")

    def routes() -> List[Dict[str, Any]]:
        res = app.cli_try("mifi_qcmap_cli", "get_static_route_list")
        if res is None:
            raise unavailable("The static route list is not available right now")
        return parse_route_list(res.text)

    def write(lst: List[Dict[str, Any]]) -> None:
        answers: List[Any] = [len(lst)]
        for rt in lst:
            iface = IFACES.get(rt["interface"])
            if iface is None:
                m = re.match(r"^wan(\d+)$", str(rt["interface"]))
                iface = int(m.group(1)) if m else 1
            answers += [rt["family"], rt["network"], rt["gateway"], iface, rt["metric"]]
        run_set(app, "mifi_qcmap_cli", "set_static_route_list", answers, "Saving the static routes")

    def check(data: Dict[str, Any]) -> Dict[str, Any]:
        fam = data.get("family", 4)
        if isinstance(fam, str) and fam.strip() in ("4", "6"):
            fam = int(fam.strip())
        fam = validate.choice("family", fam, [4, 6])
        net_s = validate.text("network", data.get("network", ""), 1, 50).strip()
        try:
            net = ipaddress.ip_network(net_s if "/" in net_s else net_s + ("/32" if fam == 4 else "/128"), strict=False)
        except ValueError:
            raise field_error("network", "Enter a network such as 10.0.0.0/8 (or a single address)")
        if net.version != fam:
            raise field_error("network", "This is not an IPv%d network" % fam)
        if net.prefixlen == 0:
            raise field_error("network", "The default route cannot be changed here")
        gw_s = validate.text("gateway", data.get("gateway", ""), 1, 45).strip()
        try:
            gw = ipaddress.ip_address(gw_s)
        except ValueError:
            raise field_error("gateway", "Enter the gateway address")
        if gw.version != fam:
            raise field_error("gateway", "The gateway must be an IPv%d address" % fam)
        iface = validate.choice("interface", data.get("interface", "wwan"), list(IFACES))
        metric = validate.integer("metric", data.get("metric", 0), 0, 65535)
        rt = {"family": fam, "network": str(net), "gateway": str(gw), "interface": iface,
              "interface_label": IFACE_TEXT[iface], "metric": metric}
        rt["id"] = route_id(rt)
        return rt

    @r.route("GET", "/api/routes")
    def get_routes(req):
        """Static routes feature state, the configured routes and the choices for new ones."""
        return {"enabled": enabled_state(), "routes": routes(), "max": MAX_ROUTES,
                "interfaces": [[k, IFACE_TEXT[k]] for k in IFACES]}

    @r.route("PUT", "/api/routes")
    def put_routes(req):
        """Turn the static routes feature on/off."""
        data = validate.only_keys(req.json, ["enabled"])
        en = validate.boolean("enabled", data.get("enabled"))
        run_set(app, "mifi_qcmap_cli", "set_static_routes", [1 if en else 0], "Changing static routes")
        app.audit(req, "routes.enabled", {"enabled": en})
        return get_routes(req)

    @r.route("GET", "/api/routes/static")
    def list_static(req):
        """Configured static routes (list form)."""
        return routes()

    @r.route("POST", "/api/routes/static")
    def add_static(req):
        """Add a static route."""
        data = validate.only_keys(req.json, ["family", "network", "gateway", "interface", "metric"])
        rt = check(data)
        lst = routes()
        if len(lst) >= MAX_ROUTES:
            raise field_error("network", "At most %d static routes" % MAX_ROUTES)
        for e in lst:
            if e["family"] == rt["family"] and e["network"] == rt["network"] and e["metric"] == rt["metric"]:
                raise field_error("network", "A route to %s with this metric already exists" % rt["network"])
        write(lst + [rt])
        app.audit(req, "routes.add", {"network": rt["network"]})
        return routes()

    @r.route("PUT", "/api/routes/static/{rid}")
    def put_static(req):
        """Change a static route."""
        data = validate.only_keys(req.json, ["family", "network", "gateway", "interface", "metric", "id",
                                             "interface_label"])
        lst = routes()
        idx = next((i for i, e in enumerate(lst) if e["id"] == req.params["rid"]), None)
        if idx is None:
            raise ApiError(404, "not_found", "This route no longer exists (the list changed); reload the page")
        merged = dict(lst[idx])
        merged.update({k: v for k, v in data.items() if k not in ("id", "interface_label")})
        rt = check(merged)
        for i, e in enumerate(lst):
            if i != idx and e["family"] == rt["family"] and e["network"] == rt["network"] and e["metric"] == rt["metric"]:
                raise field_error("network", "A route to %s with this metric already exists" % rt["network"])
        lst[idx] = rt
        write(lst)
        app.audit(req, "routes.update", {"network": rt["network"]})
        return routes()

    @r.route("DELETE", "/api/routes/static/{rid}")
    def del_static(req):
        """Delete a static route."""
        lst = routes()
        keep = [e for e in lst if e["id"] != req.params["rid"]]
        if len(keep) == len(lst):
            raise ApiError(404, "not_found", "This route no longer exists (the list changed); reload the page")
        write(keep)
        app.audit(req, "routes.delete")
        return routes()

    @r.route("GET", "/api/routes/table")
    def kernel_table(req):
        """Kernel routing tables (IPv4 and IPv6, read-only)."""
        if app.mock:
            r4 = {"ok": True, "output": MOCK_V4}
            r6 = {"ok": True, "output": MOCK_V6}
        else:
            r4 = app.system.ip("route")
            r6 = app.system.ip("-6", "route")
        return {"v4": parse_ip_route(r4.get("output", "")) if r4.get("ok") else [],
                "v6": parse_ip_route(r6.get("output", "")) if r6.get("ok") else [],
                "raw_v4": r4.get("output", ""), "raw_v6": r6.get("output", ""),
                "ok": bool(r4.get("ok") or r6.get("ok"))}
