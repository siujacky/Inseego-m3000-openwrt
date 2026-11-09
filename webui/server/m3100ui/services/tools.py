"""Tools: network tests, modem & network info, field test, lab test, carrier (PCO) events, Internet sessions, audit log.

Stock apps replaced: status_diagnostics (About > Diagnostics, incl. every row Verizon hid), field_test_info (hidden),
lab_test_info (hidden, URL-only), pco (carrier interstitial) and session_log_info (About > Internet sessions).
Feature map: docs/features/tools.md.
"""
import csv
import io
import re
import time
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import ApiError, field_error, unavailable, vendor_error
from ..adapters.system import valid_host
from ..httpd import Response

LOG_LIMITS = {"files": (1, 3), "total_mb": (10, 75)}       # stock lab_test_info <Config> Min/MaxLogFiles(Size)
ROAM = {1: "home", 2: "roaming", 3: "partial"}               # session_log_info MapToRoamingType @0x9374
PCO_TEXT = {
    0: ("none", "The carrier reports no account restriction."),
    2: ("restricted", "The carrier restricted this account (PCO 2)."),
    3: ("out_of_credit", "The account is out of credit (PCO 3): add a data plan or top up."),
    5: ("not_active", "The account is not active (PCO 5): the line has not been activated."),
}
TECH_BITS = [(0x01, "GSM"), (0x02, "UMTS"), (0x04, "CDMA"), (0x08, "EVDO"), (0x10, "LTE"), (0x20, "NR5G")]
SENSITIVE_LABEL_RE = re.compile(r"(MDN|IMSI|\bMIN\b|ESN|MEID|IMEI|ICCID|phone)", re.I)
_PING_SUM_RE = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received(?:, \+?\d+ errors)?, (\d+(?:\.\d+)?)% packet loss")
_PING_RTT_RE = re.compile(r"(?:round-trip|rtt) min/avg/max(?:/mdev)? = ([\d.]+)/([\d.]+)/([\d.]+)")
_HOP_RE = re.compile(r"^\s*(\d+)\s+(\S+)(?:\s+\(([^)]+)\))?(?:\s+(.*))?$")
_SESSION_RE = re.compile(r"^list\[(\d+)\]\.(\w+):\[(.*)\]\s*$")
_SESSION_RC_RE = re.compile(r"sys_getSessionLogStats returned (-?\d+), count:\[(\d+)\]")


# -- helpers (module level so tests can use them on fixtures) ---------------------------------------------------
def mask(value: Any, keep: int = 4) -> str:
    s = "" if value is None else str(value).strip()
    if not s or s.upper() in ("N/A", "0", "REDACTED", "<REDACTED>"):
        return s
    return s if len(s) <= keep else "•" * (len(s) - keep) + s[-keep:]


def _int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(str(v).strip(), 0)
    except (TypeError, ValueError):
        try:
            return int(float(str(v).strip()))
        except (TypeError, ValueError):
            return default


def tech_names(mask_value: Optional[int]) -> List[str]:
    if not mask_value:
        return []
    return [n for bit, n in TECH_BITS if mask_value & bit]


def parse_ping(output: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    m = _PING_SUM_RE.search(output or "")
    if m:
        out.update(sent=int(m.group(1)), received=int(m.group(2)), loss_pct=float(m.group(3)))
    m = _PING_RTT_RE.search(output or "")
    if m:
        out.update(rtt_min_ms=float(m.group(1)), rtt_avg_ms=float(m.group(2)), rtt_max_ms=float(m.group(3)))
    elif output and "time=" in output:
        times = [float(x) for x in re.findall(r"time=([\d.]+)", output)]
        if times:
            out.update(rtt_min_ms=min(times), rtt_avg_ms=round(sum(times) / len(times), 1), rtt_max_ms=max(times))
    return out


def parse_traceroute(output: str) -> List[Dict[str, Any]]:
    hops = []
    for line in (output or "").splitlines():
        if line.lower().startswith("traceroute"):
            continue
        m = _HOP_RE.match(line)
        if not m:
            continue
        times = [float(x) for x in re.findall(r"([\d.]+)\s*ms", m.group(4) or "")]
        hops.append({"hop": int(m.group(1)), "host": m.group(2) if m.group(2) != "*" else "",
                     "address": m.group(3) or (m.group(2) if m.group(2) != "*" else ""),
                     "rtt_ms": times[0] if times else None, "timeout": m.group(2) == "*"})
    return hops


def parse_nslookup(output: str, host: str) -> List[str]:
    addrs, after_name = [], False
    for line in (output or "").splitlines():
        s = line.strip()
        if s.lower().startswith("name:"):
            after_name = True
            continue
        if after_name:
            m = re.match(r"^Address(?:es)?(?:\s+\d+)?:\s*(\S+)", s)
            if m:
                addrs.append(m.group(1))
    return addrs


def parse_diag_info(text: str, reveal: bool = False) -> Dict[str, Any]:
    """modem2_cli get_diag_info: RC line, then one '[...]' block of 'Label: value' lines."""
    body = text or ""
    m = re.search(r"returned -?\d+ \([^)]*\)\s*\n", body)
    if m:
        body = body[m.end():]
    i, j = body.find("["), body.rfind("]")
    inner = body[i + 1:j] if i >= 0 and j > i else body
    title, items, lines = "", [], []
    for raw in inner.split("\n"):
        line = raw.rstrip("\r")
        s = line.strip()
        if not s or set(s) <= {"="}:
            lines.append(line)
            continue
        if ":" not in s:
            title = title or s
            lines.append(line)
            continue
        label, value = s.split(":", 1)
        label, value = label.strip(), value.strip()
        sensitive = bool(SENSITIVE_LABEL_RE.search(label)) and value not in ("", "N/A")
        shown = value if reveal or not sensitive else mask(value)
        item: Dict[str, Any] = {"label": label, "value": shown}
        if sensitive:
            item["sensitive"] = True
        groups = re.findall(r"\[([^\]]*)\]", value)
        if groups and re.match(r"^(\s*\[[^\]]*\]\s*)+$", value):
            item["values"] = groups if reveal or not sensitive else [mask(g) for g in groups]
        items.append(item)
        lines.append(line if shown == value else line.replace(value, shown))
    return {"title": title, "items": items, "text": "\n".join(lines).strip("\n"), "masked": not reveal}


def parse_sessions(text: str) -> Dict[str, Any]:
    rc, count = None, None
    for m in _SESSION_RC_RE.finditer(text or ""):
        rc, count = int(m.group(1)), int(m.group(2))
    rows: Dict[int, Dict[str, str]] = {}
    for line in (text or "").splitlines():
        m = _SESSION_RE.match(line.strip())
        if m:
            rows.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
    sessions = []
    for idx in sorted(rows):
        r = rows[idx]
        rx4, tx4 = _int(r.get("rx"), 0), _int(r.get("tx"), 0)
        rx6, tx6 = _int(r.get("rxv6"), 0), _int(r.get("txv6"), 0)
        roam = _int(r.get("roam_status"), 0)
        sessions.append({
            "start": _int(r.get("time"), 0), "duration_s": _int(r.get("duration"), 0),
            "rx_bytes": rx4 + rx6, "tx_bytes": tx4 + tx6, "total_bytes": rx4 + rx6 + tx4 + tx6,
            "rx_ipv4": rx4, "tx_ipv4": tx4, "rx_ipv6": rx6, "tx_ipv6": tx6,
            "roaming": ROAM.get(roam, "home"), "roam_status": roam,
            "ipv4": r.get("ipv4", ""), "ipv6": r.get("ipv6", ""),
        })
    sessions.sort(key=lambda s: -(s["start"] or 0))
    return {"rc": rc, "count": count if count is not None else len(sessions), "sessions": sessions}


def pco_info(value: Optional[int]) -> Dict[str, str]:
    code, text = PCO_TEXT.get(value or 0, ("other", "The carrier sent account status code %s." % value))
    return {"status": code, "text": text}


def modem_logging_from(res) -> Dict[str, Any]:
    en = res.get_int("modem_logging_enabled", 0) if res else 0
    size = res.get_int("log_files_size", 0) if res else 0
    count = res.get_int("number_of_files", 0) if res else 0
    return {"enabled": bool(en), "file_size_mb": size, "file_count": count, "total_mb": size * count,
            "limits": {"files": list(LOG_LIMITS["files"]), "total_mb": list(LOG_LIMITS["total_mb"])}}


def register(app):
    r = app.router

    # -- network tests -----------------------------------------------------------------------------------------
    def _host(data, key="host"):
        h = data.get(key)
        if not isinstance(h, str) or not h.strip():
            raise field_error(key, "Enter a host name or IP address")
        return h.strip()

    def _run_tool(name, fn, host):
        try:
            res = fn()
        except ApiError as e:
            if e.status == 400:
                fld = e.details.get("field", "host") if isinstance(e.details, dict) else "host"
                raise field_error(fld, e.message)
            raise
        out = {"tool": name, "host": host, "ok": bool(res.get("ok")), "rc": res.get("rc"),
               "timed_out": bool(res.get("timed_out")), "elapsed_s": res.get("elapsed_s"),
               "output": res.get("output", "")}
        return out

    @r.route("POST", "/api/tools/ping")
    def ping(req):
        """Ping a host from the router (1-20 echo requests, optional IPv6)."""
        data = validate.only_keys(req.json, ["host", "count", "ipv6"])
        host = _host(data)
        count = validate.integer("count", data.get("count", 4), 1, 20)
        v6 = validate.boolean("ipv6", data.get("ipv6", False))
        out = _run_tool("ping", lambda: app.system.ping(host, count, v6), host)
        out["summary"] = parse_ping(out["output"])
        out["count"], out["ipv6"] = count, v6
        return out

    @r.route("POST", "/api/tools/traceroute")
    def traceroute(req):
        """Trace the route to a host (numeric, 1-30 hops)."""
        data = validate.only_keys(req.json, ["host", "max_hops"])
        host = _host(data)
        hops = validate.integer("max_hops", data.get("max_hops", 20), 1, 30)
        out = _run_tool("traceroute", lambda: app.system.traceroute(host, hops), host)
        out["hops"] = parse_traceroute(out["output"])
        return out

    @r.route("POST", "/api/tools/nslookup")
    def nslookup(req):
        """Resolve a name with the router's resolver or a given DNS server."""
        data = validate.only_keys(req.json, ["host", "server"])
        host = _host(data)
        server = data.get("server") or None
        if server is not None and not isinstance(server, str):
            raise field_error("server", "Enter a DNS server address")
        server = server.strip() if server else None
        for fld, val in (("host", host), ("server", server)):
            if val:
                try:
                    valid_host(val)
                except ApiError as e:
                    raise field_error(fld, e.message)
        res = app.system.nslookup(host, server)
        return {"tool": "nslookup", "host": host, "server": server or "", "ok": bool(res.get("ok")),
                "rc": res.get("rc"), "timed_out": bool(res.get("timed_out")), "elapsed_s": res.get("elapsed_s"),
                "output": res.get("output", ""), "addresses": parse_nslookup(res.get("output", ""), host)}

    # -- modem & network info (stock status_diagnostics) ------------------------------------------------------
    def identity_raw() -> Dict[str, str]:
        info = app.cache.get("tools:deviceinfo", 60, lambda: app.rest_try("DeviceInfo", {}) or {})
        acct = app.cache.get("tools:accountinfo", 30, lambda: app.rest_try("AccountInfo", {}) or {})
        gi = app.cache.get("tools:get_info", 60, lambda: app.cli_try("modem2_cli", "get_info"))

        def pick(*vals):
            for v in vals:
                s = "" if v is None else str(v).strip()
                if s and s.lower() not in ("redacted", "<redacted>"):
                    return s
            return ""
        g = (lambda k: gi.get(k)) if gi else (lambda k: None)
        return {
            "phone_number": pick(acct.get("AccountInfoMDN"), g("mdn")),
            "imei": pick(info.get("DeviceInfoIMEI"), g("imei")),
            "imei_sv": pick(info.get("DeviceInfoIMEISV"), g("imei sv")),
            "meid": pick(info.get("DeviceInfoMEID"), g("meid")),
            "esn": pick(info.get("DeviceInfoESN") if str(info.get("DeviceInfoESN", "")) not in ("0",) else "", g("esn")),
            "imsi": pick(acct.get("AccountInfoSimIMSI"), g("imsi")),
            "iccid": pick(acct.get("AccountInfoSimICCID"), g("iccid")),
            "msid": pick(acct.get("AccountInfoMIN"), g("min")),
        }

    def modem_info() -> Dict[str, Any]:
        info = app.cache.get("tools:deviceinfo", 60, lambda: app.rest_try("DeviceInfo", {}) or {})
        acct = app.cache.get("tools:accountinfo", 30, lambda: app.rest_try("AccountInfo", {}) or {})
        gi = app.cache.get("tools:get_info", 60, lambda: app.cli_try("modem2_cli", "get_info"))
        sim = app.rest_try("SimStatus", {}) or {}
        lock = app.rest_try("SimLockStatus", {}) or {}
        svc = app.rest_try("CellularServiceStatus", {}) or {}
        data = app.rest_try("CellularDataConnectionStatus", {}) or {}
        cset = app.rest_try("CellularServiceSettings", {}) or {}
        sig = app.cli_try("modem2_cli", "get_signal")
        nr = app.cli_try("modem2_cli", "get_5g_status")
        air = app.cli_try("ccm2_cli", "get_air_mode_enabled")
        tech = app.cache.get("tools:enabled_tech", 30, lambda: app.cli_try("modem2_cli", "enabled_tech_get"))
        ecgi = app.cli_try("modem2_cli", "get_ecgi")
        home = app.cli_try("modem2_cli", "validate_home")
        ids = identity_raw()
        masked = {k: mask(v) for k, v in ids.items()}
        sim_status = str(sim.get("CurrentSimStatus", "Unknown"))
        radio_off = bool(air and air.get_int("enabled", 0))
        wan = str(data.get("CurrentStatus", ""))
        service = str(svc.get("CurrentServiceStatus", ""))
        if sim_status.lower() in ("notpresent", "absent"):
            status, status_text = "no_sim", "No SIM card"
        elif radio_off:
            status, status_text = "radio_off", "Turned off (airplane mode)"
        elif wan.lower() == "connected":
            status, status_text = "connected", "Connected"
        elif service.lower() in ("inservice", "limitedservice"):
            status, status_text = "available", "Available, not connected"
        else:
            status, status_text = "not_available", "Not available"
        mask_v = _int(tech.get("enabled tech modes")) if tech else None
        nr_on = bool(nr and nr.get_int("service_available", 0))
        nr_mode = (nr.note("mode") or "").strip("() ") if nr else ""
        gnum = (lambda k, d=None: _int(gi.get(k), d)) if gi else (lambda k, d=None: d)
        sget = (lambda k: sig.get(k)) if sig else (lambda k: None)
        roam_state = str(svc.get("CurrentRoamingState", ""))
        return {
            "modem": dict(masked, home_sid=str(acct.get("AccountInfoHomeSID") or (gi.get("home_sid") if gi else "") or ""),
                          firmware=str(info.get("DeviceInfoModemFwVersion") or (gi.get("FW Version") if gi else "") or ""),
                          hardware=str(info.get("DeviceInfoHwVersion", "")),
                          eri_version=_int(info.get("DeviceInfoERIVersion"), gnum("eri version", 0)),
                          prl_version=gnum("prl version", 0),
                          pri_version=str(info.get("DeviceInfoPRIVersionDetailed") or info.get("DeviceInfoPRIVersion") or ""),
                          sim_status=sim_status, sim_pin_enabled=bool(lock.get("SimPinLockEnabled", False)),
                          sim_pin_retries=lock.get("SimPinLockCounter"), sim_puk_retries=lock.get("SimPukLockCounter")),
            "network": {
                "status": status, "status_text": status_text, "data_enabled": bool(cset.get("CellularDataEnabled", True)),
                "radio_on": not radio_off, "technology": str(svc.get("CurrentWirelessTechnology", "") or ""),
                "enabled_tech_mask": mask_v, "enabled_tech": tech_names(mask_v),
                "band": str(svc.get("CurrentBand", "") or ""), "bandwidth_mhz": svc.get("CurrentBandwidth"),
                "operator": str(svc.get("CurrentCarrierNetworkName", "") or ""),
                "rsrp": svc.get("CurrentSignalStrengthInRSRP"), "rsrq": svc.get("CurrentStrengthInRSRQ"),
                "sinr": svc.get("CurrentSignalToNoiseLevelInSINR"), "snr": svc.get("CurrentSignalToNoiseLevelInSNR"),
                "rssi": svc.get("CurrentSignalStrengthInRSSI"), "bars": svc.get("CurrentSignalStrengthInBars"),
                "roaming": roam_state not in ("", "NotRoaming"), "roaming_state": roam_state,
                "pci": svc.get("PCI"), "cell_id": str(svc.get("CurrentCellID", "") or ""),
                "ecgi": str((ecgi.get("ecgi") if ecgi else None) or svc.get("CurrentECGI") or ""),
                "ecgi_hex": (ecgi.get("ecgi_hex") if ecgi else "") or "",
                "tac": sget("tac") or "", "channel": _int(sget("channel"), None),
                "nr": {"available": nr_on, "mode": nr_mode, "band": nr.get_int("band", 0) if nr else 0,
                       "bandwidth_mhz": nr.get_int("bandwidth", 0) if nr else 0, "pci": nr.get_int("pci", 0) if nr else 0,
                       "rsrp": _int(nr.get("rsrp")) if nr else None, "rsrq": _int(nr.get("rsrq")) if nr else None,
                       "snr": _int(nr.get("snr")) if nr else None, "channel": nr.get_int("channel", 0) if nr else 0},
            },
            "legacy": {  # the 3G CDMA/EVDO and GSM/UMTS/HSPA sections the stock page never showed
                "technology": (sig.note("tech") or "").strip("() ") if sig else "",
                "rssi": _int(sget("rssi")), "ecio": _int(sget("ecio")), "sid": _int(sget("sid")),
                "nid": _int(sget("nid")), "lac": sget("lac") or "", "cell_id": sget("cell id") or "",
                "p_rev": _int(sget("p_rev")), "femto": bool(_int(sget("femto"), 0)),
                "operator_id": sget("oper id") or "",
                "home_network": (home.get_int("home network") == 1) if home else None,
            },
            "masked": True,
        }

    @r.route("GET", "/api/tools/modem-info")
    def get_modem_info(req):
        """Modem identity and network status (stock Diagnostics page incl. hidden rows); identifiers masked."""
        return app.cache.get("tools:modem_info", 3, modem_info)

    @r.route("GET", "/api/tools/modem-info/identifiers")
    def get_identifiers(req):
        """Unmasked modem/SIM identifiers (IMEI, IMSI, ICCID, MDN ...) for the reveal button."""
        return identity_raw()

    # -- field test (stock field_test_info) --------------------------------------------------------------------
    @r.route("GET", "/api/tools/field-test")
    def field_test(req):
        """Modem diagnostics buffer (modem2_cli get_diag_info); add ?reveal=1 to unmask identifiers."""
        reveal = str(req.query.get("reveal", "")) in ("1", "true", "yes")
        res = app.cli_try("modem2_cli", "get_diag_info", timeout=15)
        if not res:
            raise unavailable("The modem did not return diagnostic data (the modem service may be starting).")
        out = parse_diag_info(res.text, reveal=reveal)
        out["captured"] = int(time.time())
        return out

    # -- lab test (stock lab_test_info) -------------------------------------------------------------------------
    def logging_settings():
        res = app.cli_try("settings_cli", "get_modem_logging_settings")
        if not res:
            raise unavailable("Modem logging settings are not available on this unit.")
        return modem_logging_from(res)

    @r.route("GET", "/api/tools/modem-logging")
    def get_logging(req):
        """Modem (QXDM-style) logging: enabled, size per file (MB), number of files (hidden in stock)."""
        return logging_settings()

    @r.route("PUT", "/api/tools/modem-logging")
    def put_logging(req):
        """Change modem logging (1-3 files, total size 10-75 MB)."""
        data = validate.only_keys(req.json, ["enabled", "file_size_mb", "file_count"])
        cur = logging_settings()
        enabled = validate.boolean("enabled", data["enabled"]) if "enabled" in data else cur["enabled"]
        size = validate.integer("file_size_mb", data.get("file_size_mb", cur["file_size_mb"] or 25), 1, 75)
        count = validate.integer("file_count", data.get("file_count", cur["file_count"] or 1), *LOG_LIMITS["files"])
        lo, hi = LOG_LIMITS["total_mb"]
        if not lo <= size * count <= hi:
            raise field_error("file_size_mb", "Files x size must be between %d and %d MB (now %d MB)" % (lo, hi, size * count))
        res = app.cli.run("settings_cli", "set_modem_logging_settings", [str(int(enabled)), str(size), str(count)])
        res.raise_for_error("Saving the modem logging settings")
        app.audit(req, "tools.modem_logging.update", {"enabled": enabled, "file_size_mb": size, "file_count": count})
        return logging_settings()

    @r.route("POST", "/api/tools/modem-logging/clear")
    def clear_logs(req):
        """Delete the stored modem logs (tier 2)."""
        app.require_tier(req, 2)
        res = app.cli.run("settings_cli", "clear_modem_logs", [])
        res.raise_for_error("Clearing the modem logs")
        app.audit(req, "tools.modem_logs.clear")
        return {"ok": True, "cleared": int(time.time())}

    @r.route("GET", "/api/tools/lab-test")
    def lab_test(req):
        """Hidden stock lab-test page: versions, connection policy, technology, IP passthrough, modem logging."""
        info = app.cache.get("tools:deviceinfo", 60, lambda: app.rest_try("DeviceInfo", {}) or {})
        data = app.rest_try("CellularDataConnectionStatus", {}) or {}
        cset = app.rest_try("CellularServiceSettings", {}) or {}
        ac = app.cli_try("ccm2_cli", "get_wwan_ac")
        tech = app.cli_try("modem2_cli", "enabled_tech_get")
        sup = app.cache.get("tools:sup_tech", 300, lambda: app.cli_try("modem2_cli", "get_sup_tech"))
        opts = app.cache.get("tools:tech_opts", 300, lambda: app.cli_try("webui_cli", "get_network_technology_options"))
        ippt = app.cli_try("mifi_qcmap_cli", "get_ippt")
        ippt_mode = app.cli_try("mifi_qcmap_cli", "get_ippt_feature_mode")
        options = []
        if opts:
            for m in re.finditer(r"Technology value\((\d+)\) label\((.*)\)[ \t]*$", opts.text, re.M):
                options.append({"value": int(m.group(1)), "label": m.group(2)})
        mask_v = _int(tech.get("enabled tech modes")) if tech else None
        sup_v = _int(sup.get("available tech modes")) if sup else None
        label = next((o["label"] for o in options if o["value"] == mask_v), "")
        try:
            logging = logging_settings()
        except ApiError:
            logging = None
        return {
            "software": {"version": str(info.get("DeviceInfoSwVersion", "")),
                         "modem_firmware": str(info.get("DeviceInfoModemFwVersion", "")),
                         "web_ui": str(info.get("DeviceInfoWebUISwVersion", "")),
                         "pri": str(info.get("DeviceInfoPRIVersionDetailed", ""))},
            "connection": {"status": str(data.get("CurrentStatus", "Unknown")),
                           "connected": str(data.get("CurrentStatus", "")).lower() == "connected"},
            "auto_connect": {"type": ac.get_int("autoconnect type") if ac else None,
                             "label": (ac.note("autoconnect type") or "") if ac else "",
                             "enabled": bool(cset.get("AutoConnectEnabled", False)),
                             "roaming_allowed": bool(cset.get("DomesticRoamingEnabled", False))},
            "technology": {"mask": mask_v, "names": tech_names(mask_v), "label": label,
                           "supported_mask": sup_v, "supported": tech_names(sup_v), "options": options},
            "ippt": {"active": bool(ippt.get_int("is active", 0)) if ippt else None,
                     "interface": ippt.get_int("interface") if ippt else None,
                     "feature_mode": ippt_mode.get_int("ippt feature mode is") if ippt_mode else None},
            "modem_logging": logging,
        }

    # -- carrier PCO events (stock pco) ---------------------------------------------------------------------------
    def pco_state():
        res = app.cli_try("modem2_cli", "get_pco_event")
        if not res:
            raise unavailable("The modem did not report carrier (PCO) events.")
        value = res.get_int("pco_value", 0)
        data = app.rest_try("CellularDataConnectionStatus", {}) or {}
        connected = str(data.get("CurrentStatus", "")).lower() == "connected"
        out = {"pco_value": value, "pdn": res.get_int("pdn", 0), "connected": connected,
               "can_allow_access": value in (3, 5) and not connected}
        out.update(pco_info(value))
        return out

    @r.route("GET", "/api/tools/pco")
    def get_pco(req):
        """Latest carrier account-status (PCO) event and whether limited access can be requested."""
        return pco_state()

    @r.route("POST", "/api/tools/pco/accept")
    def accept_pco(req):
        """Temporarily restore data so the carrier account can be topped up or activated (stock 'Yes')."""
        res = app.cli.run("ccm2_cli", "accept_pco_event", [])
        res.raise_for_error("Requesting limited access")
        app.audit(req, "tools.pco.accept")
        return pco_state()

    # -- Internet sessions (stock session_log_info) ------------------------------------------------------------
    def sessions():
        def fetch():
            res = app.cli.run("sysintcli", "getSessionLogStats", [], timeout=15)
            out = parse_sessions(res.text)
            if out["rc"] not in (0, None) or (out["rc"] is None and not res.ok):
                raise unavailable("The session history is not available (vendor answered %s)." % out["rc"])
            return out
        try:
            return app.cache.get("tools:sessions", 10, fetch)
        except ApiError as e:
            if e.code in ("unverified_setter", "excluded"):
                raise unavailable("Reading the session history is not enabled on this unit yet: " + e.message)
            raise

    @r.route("GET", "/api/tools/sessions")
    def get_sessions(req):
        """Past Internet sessions: start, duration, data received/sent, roaming, IPv4/IPv6 address."""
        out = sessions()
        totals = {"rx_bytes": sum(s["rx_bytes"] for s in out["sessions"]),
                  "tx_bytes": sum(s["tx_bytes"] for s in out["sessions"]),
                  "duration_s": sum(s["duration_s"] for s in out["sessions"])}
        return {"sessions": out["sessions"], "count": len(out["sessions"]), "totals": totals}

    @r.route("GET", "/api/tools/sessions/export")
    def export_sessions(req):
        """Session history as a CSV file."""
        out = sessions()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["start (UTC)", "duration (s)", "received (bytes)", "sent (bytes)", "total (bytes)", "roaming",
                    "IPv4 address", "IPv6 address"])
        for s in out["sessions"]:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(s["start"] or 0)), s["duration_s"], s["rx_bytes"],
                        s["tx_bytes"], s["total_bytes"], s["roaming"], s["ipv4"], s["ipv6"]])
        return Response(buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="internet-sessions.csv"'})

    # -- audit log ---------------------------------------------------------------------------------------------
    @r.route("GET", "/api/tools/audit")
    def audit(req):
        """Recent changes made through this UI (newest first); ?limit=1-2000, ?q=text filter."""
        try:
            limit = int(req.query.get("limit", 300))
        except ValueError:
            raise field_error("limit", "Must be a whole number")
        limit = max(1, min(limit, 2000))
        q = str(req.query.get("q", "")).strip().lower()
        entries = list(reversed(app.state.audit_tail(limit if not q else 2000)))
        if q:
            entries = [e for e in entries if q in (e.get("action", "") + " " + e.get("actor", "")).lower()
                       or q in str(e.get("details", "")).lower()][:limit]
        return {"entries": entries, "count": len(entries), "limit": limit}

    @r.route("GET", "/api/tools/audit/export")
    def audit_export(req):
        """The whole audit log (up to 5000 entries) as a CSV or JSON file (?format=csv|json)."""
        fmt_ = str(req.query.get("format", "csv")).lower()
        if fmt_ not in ("csv", "json"):
            raise field_error("format", "Must be csv or json")
        entries = list(reversed(app.state.audit_tail(5000)))
        if fmt_ == "json":
            import json
            return Response(json.dumps(entries, indent=1).encode("utf-8"), "application/json; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="audit-log.json"'})
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time (UTC)", "from", "action", "details"])
        for e in entries:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(e.get("t", 0))), e.get("actor", ""),
                        e.get("action", ""), "" if not e.get("details") else
                        "; ".join("%s=%s" % (k, v) for k, v in sorted(e["details"].items()))])
        return Response(buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'})

