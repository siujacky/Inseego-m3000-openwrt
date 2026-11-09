"""Data usage: current session, billing-cycle plan (size, cycle reset day, alert threshold), usage this cycle,
modem lifetime counters, per-call byte counters and per-interface counters.

Sources:  REST CellularDataConnectionStatus (session), DataUsage / DataUsageSettings (only when the firmware
          enables them; this unit answers 404 -> the dua_vzw_cli path is used)
          dua_vzw_cli get_data_usage_settings_info / set_data_usage_settings_info / get_interface_data_usage_info /
          get_data_usage_info / get_active_interface_level_info (iface 0 = SIM, 1 = Ethernet WAN)
          modem2_cli lifetime_counters_get, get_bytes_counters (0 IPv4 / 2 IPv6);  /proc/net/dev (app.system.net_dev)
Not exposed: dua_vzw_cli trigger_data_usage_sync (excluded: talks to the carrier's server).
"""
import re
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import ApiError, unavailable
from .sim import cli_get, cli_set, identity_raw, rc_of, rest_get, sim_status, to_int

LIMITS = [("none", "No limit", 0), ("20MB", "20 MB", 20 * 2 ** 20), ("25MB", "25 MB", 25 * 2 ** 20),
          ("100MB", "100 MB", 100 * 2 ** 20), ("250MB", "250 MB", 250 * 2 ** 20), ("500MB", "500 MB", 500 * 2 ** 20),
          ("1GB", "1 GB", 2 ** 30), ("2GB", "2 GB", 2 * 2 ** 30), ("3GB", "3 GB", 3 * 2 ** 30), ("5GB", "5 GB", 5 * 2 ** 30),
          ("8GB", "8 GB", 8 * 2 ** 30), ("10GB", "10 GB", 10 * 2 ** 30), ("12GB", "12 GB", 12 * 2 ** 30),
          ("15GB", "15 GB", 15 * 2 ** 30), ("20GB", "20 GB", 20 * 2 ** 30)]
LIMIT_KEYS = [k for k, _l, _b in LIMITS]
UNIT = {"B": 1, "KB": 2 ** 10, "MB": 2 ** 20, "GB": 2 ** 30, "TB": 2 ** 40}
IFACE_KIND = [(re.compile(r"^rmnet"), "cellular"), (re.compile(r"^bridge|^br"), "lan"), (re.compile(r"^wlan"), "wifi"),
              (re.compile(r"^eth"), "ethernet"), (re.compile(r"^usb|^rndis|^ecm"), "usb"), (re.compile(r"^lo$"), "loopback")]


def size_bytes(text: str) -> Optional[int]:
    """'20 GB' / '20.00GB' / '500 MB' -> bytes (binary units, as the vendor tables use)."""
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B)\s*$", text or "", re.I)
    if not m:
        return None
    return int(float(m.group(1)) * UNIT[m.group(2).upper()])


def limit_key(text: str) -> Optional[str]:
    b = size_bytes(text)
    if b is None:
        return None
    for k, _label, lb in LIMITS:
        if lb == b:
            return k
    return None


def parse_usage_info(res) -> Dict[str, Any]:
    g = res.get
    status = res.get_int("status")
    tx, rx = to_int(g("tx_bytes"), 0) or 0, to_int(g("rx_bytes"), 0) or 0
    return {"valid": status == 0, "plan_type": g("plan_type") or "", "cycle_start": g("cycle_start_date") or "",
            "cycle_end": g("cycle_end_date") or "", "updated": g("usage_timestamp") or "", "plan_limit": g("plan_limit") or "",
            "line_usage": g("line_usage") or "", "shared_usage": g("shared_usage") or "", "tx_bytes": tx, "rx_bytes": rx,
            "total_bytes": tx + rx, "plan_limit_bytes": to_int(g("plan_limit_bytes"), 0) or 0, "unit": g("unit") or "",
            "prepaid": g("prepay_indicator") or "", "data_available": bool(res.get_int("is_data_available", 0))}


def register(app):
    r = app.router

    def sim_ids() -> List[str]:
        ident = identity_raw(app)
        return [ident.get("imsi", "")[:31], ident.get("iccid", "")[:31]]

    def settings() -> Dict[str, Any]:
        imsi, iccid = sim_ids()
        res = cli_get(app, "dua_vzw_cli", "get_data_usage_settings_info", ["0", imsi, iccid])
        if res is None or not res.ok:
            return {"available": False, "reason": (res.message if res is not None else "") or "Data usage service not running"}
        raw = (res.get("usage_limit") or "").strip()
        if raw and not re.match(r"^[0-9. A-Za-z]{1,16}$", raw):
            raw = ""                      # uninitialised buffer (seen with EOF answers)
        key = limit_key(raw) if raw else "none"
        thr = res.get_int("level1_threshold")
        return {"available": True, "cycle_day": res.get_int("cycle_day"), "limit": key, "limit_text": raw,
                "limit_bytes": size_bytes(raw) if raw else 0, "alert_percent": thr if thr and 1 <= thr <= 100 else None,
                "sim_bound": bool(imsi)}

    def usage_info() -> Dict[str, Any]:
        imsi, iccid = sim_ids()
        res = cli_get(app, "dua_vzw_cli", "get_interface_data_usage_info", ["0", imsi, iccid])
        if res is None or not res.ok:
            res = cli_get(app, "dua_vzw_cli", "get_data_usage_info")
        if res is None or not res.ok:
            return {"available": False}
        out = parse_usage_info(res)
        out["available"] = True
        if not out["cycle_start"] and res.command != "get_data_usage_info":
            whole = cli_get(app, "dua_vzw_cli", "get_data_usage_info")     # only this one prints cycle_start_date
            if whole is not None and whole.ok:
                out["cycle_start"] = whole.get("cycle_start_date") or ""
        lvl = cli_get(app, "dua_vzw_cli", "get_active_interface_level_info", ["0", imsi, iccid])
        m = re.search(r"level\s*:\s*(\w+)", lvl.text) if lvl is not None else None
        out["level"] = m.group(1).lower().replace("level", "") if m else ""
        return out

    def ethernet_usage() -> Optional[Dict[str, Any]]:
        res = cli_get(app, "dua_vzw_cli", "get_interface_data_usage_info", ["1", ""])
        if res is None or not res.ok:
            return None
        d = parse_usage_info(res)
        return d if d["valid"] or d["total_bytes"] else None

    def lifetime() -> Dict[str, Any]:
        res = cli_get(app, "modem2_cli", "lifetime_counters_get")
        m = re.search(r"Rx\s*=\s*(\d+)\s*KB,\s*Tx\s*=\s*(\d+)\s*KB", res.text) if res is not None else None
        if not m or rc_of(res) not in (0, None):
            return {"available": False}
        return {"available": True, "rx_bytes": int(m.group(1)) * 1024, "tx_bytes": int(m.group(2)) * 1024}

    def call_counters() -> Dict[str, Any]:
        out = {}
        for fam, code in (("ipv4", "0"), ("ipv6", "2")):
            res = cli_get(app, "modem2_cli", "get_bytes_counters", [code])
            if res is not None and res.ok:
                out[fam] = {"rx_bytes": res.get_int("rx_bytes", 0), "tx_bytes": res.get_int("tx_bytes", 0)}
        return out

    def interfaces() -> List[Dict[str, Any]]:
        try:
            dev = app.system.net_dev() or {}
        except OSError:
            dev = {}
        rows = []
        for name in sorted(dev):
            d = dev[name]
            kind = next((k for rx, k in IFACE_KIND if rx.search(name)), "other")
            if kind == "loopback":
                continue
            rows.append({"name": name, "kind": kind, "rx_bytes": d.get("rx_bytes", 0), "tx_bytes": d.get("tx_bytes", 0),
                         "rx_packets": d.get("rx_packets", 0), "tx_packets": d.get("tx_packets", 0),
                         "errors": (d.get("rx_errors", 0) or 0) + (d.get("tx_errors", 0) or 0)})
        return rows

    def session() -> Dict[str, Any]:
        d = rest_get(app, "CellularDataConnectionStatus") or {}
        rx, tx = to_int(d.get("CurrentCallBytesReceived"), 0) or 0, to_int(d.get("CurrentCallBytesTransmitted"), 0) or 0
        return {"available": bool(d), "status": d.get("CurrentStatus", ""), "duration_s": to_int(d.get("CurrentCallDuration"), 0),
                "rx_bytes": rx, "tx_bytes": tx, "total_bytes": rx + tx}

    def rest_usage() -> Optional[Dict[str, Any]]:
        d = rest_get(app, "DataUsage")
        return d if isinstance(d, (dict, list)) else None

    app.features.add("usage.reset", lambda: rest_usage() is not None)

    @r.route("GET", "/api/usage")
    def get_usage(req):
        """Session, billing cycle plan and usage, lifetime and per-interface counters."""
        plan = settings()
        info = usage_info()
        if plan.get("available") and info.get("available") and plan.get("limit_bytes"):
            info["percent"] = round(100.0 * info["total_bytes"] / plan["limit_bytes"], 1)
        st = sim_status(app)
        return {
            "sim_present": bool(st.get("present")), "session": session(), "plan": plan, "cycle": info,
            "lifetime": lifetime(), "call": call_counters(), "ethernet": ethernet_usage(), "interfaces": interfaces(),
            "limits": [{"key": k, "label": label} for k, label, _b in LIMITS],
            "reset_supported": rest_usage() is not None, "vendor": rest_usage(),
        }

    @r.route("GET", "/api/usage/plan")
    def get_plan(req):
        """Billing-cycle plan settings."""
        out = settings()
        out["limits"] = [{"key": k, "label": label} for k, label, _b in LIMITS]
        return out

    @r.route("PUT", "/api/usage/plan")
    def put_plan(req):
        """Change plan size, the day the cycle restarts and the alert threshold (percent of the plan)."""
        data = validate.only_keys(req.json, ["cycle_day", "limit", "alert_percent"])
        cur = settings()
        if not cur.get("available"):
            raise unavailable("The data usage service is not available: %s" % cur.get("reason", ""))
        imsi, iccid = sim_ids()
        if not imsi:
            raise unavailable("Insert a SIM first: the plan is stored per SIM")
        day = validate.integer("cycle_day", data.get("cycle_day", cur.get("cycle_day") or 1), 1, 31)
        lim = data.get("limit", cur.get("limit"))
        if lim is None:
            raise ApiError(400, "invalid_field", "Choose a plan size; the current one (%s) is not one of the sizes this "
                           "router can set" % (cur.get("limit_text") or "?"), {"field": "limit"})
        lim = validate.choice("limit", lim, LIMIT_KEYS)
        thr = validate.integer("alert_percent", data.get("alert_percent", cur.get("alert_percent") or 80), 1, 100)
        cli_set(app, "dua_vzw_cli", "set_data_usage_settings_info",
                ["0", imsi, iccid, str(day), str(LIMIT_KEYS.index(lim)), str(thr)], "Saving the data plan")
        app.audit(req, "usage.plan.update", {"keys": sorted(data)})
        return get_plan(req)

    @r.route("POST", "/api/usage/reset")
    def reset(req):
        """Reset the usage statistics (only where the firmware enables the vendor DataUsage resource)."""
        validate.only_keys(req.json, [])
        if rest_usage() is None:
            raise unavailable("This firmware does not allow resetting the usage counter")
        app.rest.delete("DataUsage", None)
        app.audit(req, "usage.reset")
        return get_usage(req)
