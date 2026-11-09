"""WAN: current connection and statistics, WAN manager failover (priority, auto-switch, failback, monitor,
manual WAN, self-recovery) and the two lower-level priority lists.

Three vendor components each keep a WAN priority:
- wan_mgr_cli (WAN manager, the stock wan_failover app; hidden on this carrier, wan_mgr_supported=0),
- mifi_qcmap_cli wan_get_prio/wan_set_prio (QCMAP routing),
- ccm2_cli get_wan_prio/set_wan_prio (connection manager: which WAN is brought up at L3).
The failover page edits the WAN manager; the other two are expert settings.
"""
import ipaddress
import re
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import field_error, unavailable, vendor_error

RESET_RE = re.compile(r"reset is required|requires reset|reboot the device", re.I)
WAN_BY_LABEL = {"WWAN": "wwan", "WiFi WAN": "wifi", "Wifi WAN": "wifi", "Ethernet WAN": "ethernet",
                "USB WAN": "usb", "No WAN": "none"}
WAN_MGR_CODES = {"wwan": 1, "wifi": 2, "ethernet": 3, "usb": 4}      # wan_mgr_cli prompts
CCM_CODES = {"wwan": 1, "wifi": 2, "ethernet": 3}                      # ccm2_cli "Index:[n]"
QCMAP_CODES = {"wwan": 1, "ethernet": 3}                               # mifi_qcmap_cli wan_set_prio
WAN_TEXT = {"wwan": "Mobile network", "wifi": "Wi-Fi (as a client)", "ethernet": "Ethernet", "usb": "USB",
            "none": "None"}
HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                     r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$")


def wan_name(label: Optional[str]) -> str:
    s = (label or "").strip()
    s = re.sub(r"^\d+=", "", s)
    return WAN_BY_LABEL.get(s, s.lower().replace(" ", "_") or "none")


def on_off(v) -> Optional[bool]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("1", "enabled", "enable", "true"):
        return True
    if s in ("0", "disabled", "disable", "false"):
        return False
    return None


def parse_conn_blocks(res) -> List[Dict[str, Any]]:
    """ccm2_cli get_conn_state / get_curr_wan: repeated Wan:[..] V4:[..] V4 IF:[..] V6:[..] V6 IF:[..]."""
    out: List[Dict[str, Any]] = []
    if res is None:
        return out
    cur = None
    for k, v, _n in res.pairs:
        if k == "wan":
            cur = {"wan": wan_name(v), "label": v, "v4": "", "v4_if": "", "v6": "", "v6_if": ""}
            out.append(cur)
        elif cur is not None and k in ("v4", "v4 if", "v6", "v6 if"):
            cur[k.replace(" ", "_")] = v
    for c in out:
        c["connected"] = c["v4"].lower() == "connected" or c["v6"].lower() == "connected"
    return out


def parse_wan_stats(res) -> Dict[str, Any]:
    """ccm2_cli get_curr_wan_stats (answer: 1=WWAN 2=WiFi WAN 3=Ethernet WAN)."""
    if res is None:
        return {}
    names = [v for v in res.all("Wan") if not re.match(r"^\d+=", v)]

    def num(label):
        v = res.get(label)
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    out = {"wan": wan_name(names[-1] if names else ""), "ok": res.ok}
    for fam in ("v4", "v6"):
        out[fam] = {"state": res.get("%s Connection State" % fam.upper(), ""), "rx_bytes": num("%s Bytes Rx" % fam.upper()),
                    "tx_bytes": num("%s Bytes Tx" % fam.upper()), "duration_s": num("%s Duration" % fam.upper()),
                    "interface": res.get("%s ifc" % fam.upper(), ""), "ip": res.get("%s IP Addr" % fam.upper(), ""),
                    "gateway": res.get("%s Gateway" % fam.upper(), "")}
    out["v4"]["subnet"] = res.get("V4 Subnet", "")
    return out


def parse_wan_mgr(all_res, fb_res=None) -> Dict[str, Any]:
    """wan_mgr_cli get_all_wan_info (+ get_wan_failback for the data-check values)."""
    if all_res is None:
        return {}
    types = all_res.all("WAN Type")
    prios = all_res.all("WAN Priority")
    pr = []
    for i, t in enumerate(types):
        try:
            p = int(prios[i])
        except (IndexError, ValueError):
            p = i + 1
        pr.append({"wan": wan_name(t), "priority": p})
    pr.sort(key=lambda x: x["priority"])
    g = all_res.get_int
    out = {
        "supported_count": g("Suppoted WAN Count"), "auto_switch": on_off(all_res.get("Auto-Switch Status")),
        "manual_wan": wan_name(all_res.get("Manual WAN")), "priorities": pr,
        "failback_enabled": on_off(all_res.get("Failback Status")), "failback_min": g("Failback Timer"),
        "monitor_enabled": on_off(all_res.get("WAN Monitor Feature Status")),
        "monitor_client_state": on_off(all_res.get("WAN use client connected state")),
        "retry_count": g("WAN Retry Count"), "routed_interval_s": g("Routed WAN monitor interval"),
        "online_interval_min": g("Online WAN monitor interval"), "failed_interval_s": g("Failed WAN monitor interval"),
        "dns_urls": [all_res.get("DNS URL-1", ""), all_res.get("DNS URL-2", ""), all_res.get("DNS URL-3", "")],
        "data_check_min": None, "data_limit_kb": None,
    }
    if fb_res is not None:
        out["data_check_min"] = fb_res.get_int("WAN manager WWAN data traffic check interval")
        out["data_limit_kb"] = fb_res.get_int("WAN manager WWAN data traffic check limit")
        if out["failback_enabled"] is None:
            out["failback_enabled"] = on_off(fb_res.get("WAN manager failback enable"))
        if out["failback_min"] is None:
            out["failback_min"] = fb_res.get_int("WAN manager failback interval")
    return out


def parse_qcmap_prio(res) -> List[str]:
    if res is None:
        return []
    out = []
    for label in ("First priority wan is", "Second priority wan is"):
        v = res.get(label)
        if v:
            out.append(wan_name(v))
    return out


def parse_ccm_prio(res) -> List[str]:
    if res is None:
        return []
    prios = res.all("Prio")
    wans = res.all("Wan")
    rows = []
    for i, w in enumerate(wans):
        try:
            p = int(prios[i])
        except (IndexError, ValueError):
            p = i + 1
        rows.append((p, wan_name(w)))
    return [w for _p, w in sorted(rows)]


def run_set(app, cli, cmd, answers, what) -> bool:
    res = app.cli.run(cli, cmd, answers)
    if res.rc is None and cli == "wan_mgr_cli":
        # set_manual_wan / set_wan_priority_list read the current settings first and exit without an RC line
        # ("Failed to get wan priority settings.") when wan_mgrd is not running: never report that as saved
        lines = [ln.strip() for ln in (res.text or "").splitlines() if ln.strip() and not ln.startswith("command [")]
        raise vendor_error("%s failed: %s" % (what, lines[-1] if lines else "no answer from the WAN manager"),
                           {"cli": cli, "command": cmd})
    if res.ok:
        return bool(RESET_RE.search(res.text or ""))
    if res.rc == 6 or (res.message and "reset is required" in res.message.lower()):
        return True
    res.raise_for_error(what)
    return False


def monitor_host(field: str, v: Any) -> str:
    s = "" if v is None else str(v).strip()
    if not s:
        raise field_error(field, "Enter a host name or IP address to look up")
    try:
        ipaddress.ip_address(s)
        return s
    except ValueError:
        pass
    if not HOST_RE.match(s):
        raise field_error(field, "Enter a host name such as www.example.com")
    return s


def register(app):
    r = app.router

    def mgr_state() -> Dict[str, Any]:
        all_res = app.cli_try("wan_mgr_cli", "get_all_wan_info")
        fb = app.cli_try("wan_mgr_cli", "get_wan_failback")
        out = parse_wan_mgr(all_res, fb)
        out["available"] = all_res is not None
        run = app.cli_try("wan_mgr_cli", "is_running")
        out["running"] = run is not None
        sr = app.cli_try("wan_mgr_cli", "get_self_recovery_status")
        out["self_recovery"] = bool(sr.get_int("The Self Recovery Status is", 0)) if sr else None
        wt = app.cli_try("wan_mgr_cli", "get_waiting_timeout")
        out["waiting_timeout_s"] = wt.get_int("The waiting timeout value is") if wt else None
        feats = app.cache.get("wan:features", 300, lambda: app.cli_try("settings_cli", "get_features"))
        out["stock_supported"] = on_off(feats.get("wan_mgr_supported")) if feats else None
        return out

    def wan_state() -> Dict[str, Any]:
        cur = parse_conn_blocks(app.cli_try("ccm2_cli", "get_curr_wan"))
        conns = parse_conn_blocks(app.cli_try("ccm2_cli", "get_conn_state"))
        return {
            "current": cur[0] if cur else None,
            "connections": conns,
            "manager": mgr_state(),
            "routing_priority": parse_qcmap_prio(app.cli_try("mifi_qcmap_cli", "wan_get_prio")),
            "cm_priority": parse_ccm_prio(app.cli_try("ccm2_cli", "get_wan_prio")),
            "labels": WAN_TEXT,
        }

    @r.route("GET", "/api/wan")
    def get_wan(req):
        """Current WAN, connection state of every WAN, failover settings and priorities."""
        return wan_state()

    @r.route("GET", "/api/wan/stats")
    def get_stats(req):
        """Traffic and addresses of one WAN (?wan=wwan|wifi|ethernet)."""
        w = (req.query or {}).get("wan", "wwan")
        if w not in CCM_CODES:
            raise field_error("wan", "Must be one of: %s" % ", ".join(CCM_CODES))
        res = app.cli_try("ccm2_cli", "get_curr_wan_stats", [CCM_CODES[w]])
        if res is None:
            return {"wan": w, "available": False}
        out = parse_wan_stats(res)
        out["available"] = True
        out["wan"] = w if out.get("wan") in ("none", "") else out["wan"]
        return out

    @r.route("GET", "/api/wan/telemetry")
    def get_telemetry(req):
        """WAN manager counters (switches, routed time ...); needs the WAN manager service."""
        res = app.cli_try("wan_mgr_cli", "get_wan_telemetry_info")
        if res is None:      # 200 + available:false: an expected state on this carrier, not an error
            return {"available": False, "items": [],
                    "message": "WAN statistics need the WAN manager service, which is not running on this unit."}
        return {"available": True, "items": [[k, v + ((" " + n) if n else "")] for k, v, n in res.pairs if k != "command"]}

    FAILOVER_KEYS = ["auto_switch", "manual_wan", "failback_enabled", "failback_min", "data_check_min",
                     "data_limit_kb", "monitor_enabled", "monitor_client_state", "retry_count", "routed_interval_s",
                     "online_interval_min", "failed_interval_s", "dns_urls", "self_recovery", "waiting_timeout_s"]

    @r.route("PUT", "/api/wan/failover")
    def put_failover(req):
        """WAN manager: automatic switching, manual WAN, failback, connectivity monitor, self-recovery."""
        data = validate.only_keys(req.json, FAILOVER_KEYS)
        cur = mgr_state()
        if not cur.get("available"):
            raise unavailable("The WAN manager is not available on this unit")
        new = dict(cur)
        b = validate.boolean
        ints = {"failback_min": (1, 1440), "data_check_min": (1, 1440), "data_limit_kb": (1, 10240),
                "retry_count": (0, 10), "routed_interval_s": (30, 86400), "online_interval_min": (1, 1440),
                "failed_interval_s": (10, 600), "waiting_timeout_s": (1, 3600)}
        for k in data:
            if k in ("auto_switch", "failback_enabled", "monitor_enabled", "monitor_client_state", "self_recovery"):
                new[k] = b(k, data[k])
            elif k in ints:
                new[k] = validate.integer(k, data[k], *ints[k])
            elif k == "manual_wan":
                supported = [p["wan"] for p in cur.get("priorities") or []] or ["wwan"]
                new[k] = validate.choice(k, data[k], supported)
            elif k == "dns_urls":
                urls = data[k]
                if not isinstance(urls, list) or len(urls) != 3:
                    raise field_error("dns_urls", "Enter three host names to look up")
                new[k] = [monitor_host("dns_urls", u) for u in urls]
        changed = {k for k in data if new.get(k) != cur.get(k)}
        if "manual_wan" in changed and new.get("auto_switch"):
            raise field_error("manual_wan", "Turn automatic switching off to choose the WAN yourself")
        restart = False
        if "auto_switch" in changed and not new["auto_switch"]:
            restart |= run_set(app, "wan_mgr_cli", "set_auto_switch", [0], "Turning automatic switching off")
        if "manual_wan" in changed:
            restart |= run_set(app, "wan_mgr_cli", "set_manual_wan", [WAN_MGR_CODES[new["manual_wan"]]],
                               "Setting the WAN")
        if changed & {"failback_enabled", "failback_min", "data_check_min", "data_limit_kb"}:
            for k in ("failback_enabled", "failback_min", "data_check_min", "data_limit_kb"):
                if new.get(k) is None:
                    raise field_error(k, "Required (the current value could not be read)")
            restart |= run_set(app, "wan_mgr_cli", "set_wan_failback",
                               [1 if new["failback_enabled"] else 0, new["failback_min"], new["data_check_min"],
                                new["data_limit_kb"]], "Saving the failback settings")
        mon = {"monitor_enabled", "monitor_client_state", "retry_count", "routed_interval_s", "online_interval_min",
               "failed_interval_s", "dns_urls"}
        if changed & mon:
            for k in sorted(mon - {"dns_urls"}):
                if new.get(k) is None:
                    raise field_error(k, "Required (the current value could not be read)")
            urls = new.get("dns_urls") or []
            if len(urls) != 3 or not all(urls):
                raise field_error("dns_urls", "Enter three host names to look up")
            restart |= run_set(app, "wan_mgr_cli", "set_wan_monitor_properties",
                               [1 if new["monitor_enabled"] else 0, 1 if new["monitor_client_state"] else 0,
                                new["retry_count"], new["routed_interval_s"], new["online_interval_min"],
                                new["failed_interval_s"]] + list(urls), "Saving the connectivity monitor")
        if "self_recovery" in changed:
            restart |= run_set(app, "wan_mgr_cli", "set_self_recovery_status", [1 if new["self_recovery"] else 0],
                               "Saving self-recovery")
        if "waiting_timeout_s" in changed:
            restart |= run_set(app, "wan_mgr_cli", "set_waiting_timeout", [new["waiting_timeout_s"]],
                               "Saving the no-connectivity timeout")
        if "auto_switch" in changed and new["auto_switch"]:
            restart |= run_set(app, "wan_mgr_cli", "set_auto_switch", [1], "Turning automatic switching on")
        app.audit(req, "wan.failover.update", {"keys": sorted(changed)})
        out = mgr_state()
        out["restart_required"] = restart
        return out

    @r.route("PUT", "/api/wan/priority")
    def put_priority(req):
        """WAN manager priority order (first = preferred). The router must restart to apply it."""
        data = validate.only_keys(req.json, ["order"])
        cur = mgr_state()
        if not cur.get("available"):
            raise unavailable("The WAN manager is not available on this unit")
        supported = [p["wan"] for p in cur.get("priorities") or []]
        if cur.get("supported_count") != 2 or len(supported) != 2:
            raise unavailable("Changing the priority is only supported with two WANs (this unit reports %s)"
                              % cur.get("supported_count"))
        order = data.get("order")
        if not isinstance(order, list) or sorted(order) != sorted(supported):
            raise field_error("order", "List each WAN once: %s" % ", ".join(supported))
        answers = []
        for i, w in enumerate(order):
            answers += [WAN_MGR_CODES[w], i + 1]
        run_set(app, "wan_mgr_cli", "set_wan_priority_list", answers, "Saving the WAN priority")
        app.audit(req, "wan.priority", {"order": order})
        out = mgr_state()
        out["restart_required"] = True      # the vendor applies a new priority only after a restart
        return out

    @r.route("PUT", "/api/wan/routing-priority")
    def put_routing_priority(req):
        """Routing (QCMAP) WAN priority: which of mobile/Ethernet is used first. Needs a restart."""
        data = validate.only_keys(req.json, ["first"])
        first = validate.choice("first", data.get("first"), list(QCMAP_CODES))
        second = "ethernet" if first == "wwan" else "wwan"
        run_set(app, "mifi_qcmap_cli", "wan_set_prio", [QCMAP_CODES[first], QCMAP_CODES[second]],
                "Saving the routing priority")
        app.audit(req, "wan.routing_priority", {"first": first})
        return {"routing_priority": parse_qcmap_prio(app.cli_try("mifi_qcmap_cli", "wan_get_prio")),
                "restart_required": True}   # the CLI says the change needs a device reset

    @r.route("PUT", "/api/wan/cm-priority")
    def put_cm_priority(req):
        """Connection-manager WAN priority (order of mobile, Wi-Fi client and Ethernet)."""
        data = validate.only_keys(req.json, ["order"])
        order = data.get("order")
        if not isinstance(order, list) or sorted(order) != sorted(CCM_CODES):
            raise field_error("order", "List each of %s once" % ", ".join(CCM_CODES))
        run_set(app, "ccm2_cli", "set_wan_prio", [CCM_CODES[w] for w in order], "Saving the connection priority")
        app.audit(req, "wan.cm_priority", {"order": order})
        return {"cm_priority": parse_ccm_prio(app.cli_try("ccm2_cli", "get_wan_prio"))}

    @r.route("POST", "/api/wan/connect")
    def connect(req):
        """Connect the highest-priority available WAN."""
        run_set(app, "ccm2_cli", "wan_connect", [], "Connecting the WAN")
        app.cache.invalidate("status")
        app.audit(req, "wan.connect")
        return wan_state()

    @r.route("POST", "/api/wan/disconnect")
    def disconnect(req):
        """Disconnect the WAN (internet access stops; the local network keeps working)."""
        run_set(app, "ccm2_cli", "wan_disconnect", [], "Disconnecting the WAN")
        app.cache.invalidate("status")
        app.audit(req, "wan.disconnect")
        return wan_state()
