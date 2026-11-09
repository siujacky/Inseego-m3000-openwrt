"""DNS: manual upstream servers, servers in use, DNS rebinding protection and the DNS filter.

- REST ManualDNSSettings (stock manual_dns app) is the read/write path for manual DNS.
- mifi_qcmap_cli get_active_dns: the servers the router uses on the internet connection.
- mifi_qcmap_cli get/set_dns_rebinding_status and get/set_dns_filter: no stock UI at all.
DNS passthrough belongs to the firewall area.
"""
import ipaddress
import re
from typing import Any, Dict, List

from .. import validate
from ..errors import field_error, unavailable

RESET_RE = re.compile(r"reset is required|requires reset|reboot the device", re.I)
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
                       r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
MAX_FILTER_CHARS = 500       # the CLI reads the list with fgets(511) and stores 512 bytes
MAX_FILTER_DOMAINS = 50


def parse_active_dns(res) -> Dict[str, List[str]]:
    if res is None:
        return {"v4": [], "v6": []}
    v4 = [res.get("Primary v4 DNS", ""), res.get("Secondary v4 DNS", "")]
    v6 = [res.get("Primary v6 DNS", ""), res.get("Secondary v6 DNS", "")]
    return {"v4": [x for x in v4 if x], "v6": [x for x in v6 if x]}


def parse_dns_filter(res) -> Dict[str, Any]:
    """mifi_qcmap_cli get_dns_filter (note the vendor spells the white list 'WhileList')."""
    if res is None:
        return {}
    status = (res.get("DNS Filter status is") or "").lower()
    lst = (res.get("DNS Filter List Configured") or "").lower()
    urls = res.get("DNS Filter URL List") or ""
    domains = [u.strip() for u in re.split(r"[,\s]+", urls) if u.strip()]
    return {"enabled": status.startswith("enable"), "mode": "block" if lst.startswith("black") else "allow",
            "domains": domains, "last_operation": (res.get("DNS Filter Last Operation") or "").lower() or None}


def run_set(app, cli, cmd, answers, what) -> bool:
    res = app.cli.run(cli, cmd, answers)
    if res.ok:
        return bool(RESET_RE.search(res.text or ""))
    if res.rc == 6 or (res.message and "reset is required" in res.message.lower()):
        return True
    res.raise_for_error(what)
    return False


def dns_server(field: str, v: Any, required: bool) -> str:
    s = "" if v is None else str(v).strip()
    if not s:
        if required:
            raise field_error(field, "Enter a DNS server address")
        return ""
    try:
        a = ipaddress.ip_address(s)
    except ValueError:
        raise field_error(field, "Enter a valid IPv4 or IPv6 address")
    if a.is_unspecified or a.is_multicast or (a.version == 4 and str(a) == "255.255.255.255"):
        raise field_error(field, "This address cannot be a DNS server")
    return str(a)


def register(app):
    r = app.router

    def manual_state() -> Dict[str, Any]:
        d = app.rest_try("ManualDNSSettings")
        if isinstance(d, dict):
            return {"enabled": bool(d.get("ManualDNSEnabled")), "primary": d.get("ManualDNSAddress1", ""),
                    "secondary": d.get("ManualDNSAddress2", ""), "source": "rest"}
        res = app.cli_try("mifi_qcmap_cli", "get_manual_dns")
        if res is None:
            raise unavailable("Manual DNS settings are not available right now")
        return {"enabled": (res.get("Manual DNS status is") or "").lower().startswith("enable"),
                "primary": res.get("Manual DNS IP1", ""), "secondary": res.get("Manual DNS IP2", ""),
                "source": "cli", "read_only": True}

    @r.route("GET", "/api/dns/manual")
    def get_manual(req):
        """Manual (static) upstream DNS servers."""
        return manual_state()

    @r.route("PUT", "/api/dns/manual")
    def put_manual(req):
        """Use your own DNS servers instead of the mobile network's."""
        data = validate.only_keys(req.json, ["enabled", "primary", "secondary"])
        cur = manual_state()
        if cur.get("read_only"):
            raise unavailable("Manual DNS can only be changed through the vendor REST service, which is not available")
        enabled = validate.boolean("enabled", data.get("enabled", cur["enabled"]))
        primary = dns_server("primary", data.get("primary", cur["primary"]), enabled)
        secondary = dns_server("secondary", data.get("secondary", cur["secondary"]), False)
        if secondary and secondary == primary:
            raise field_error("secondary", "The second server must differ from the first")
        if secondary and not primary:
            raise field_error("primary", "Enter the first server before the second")
        app.rest.merge_put("ManualDNSSettings", {"ManualDNSEnabled": enabled, "ManualDNSAddress1": primary,
                                                 "ManualDNSAddress2": secondary})
        app.audit(req, "dns.manual.update", {"keys": sorted(data)})
        return manual_state()

    @r.route("GET", "/api/dns/active")
    def get_active(req):
        """DNS servers in use on the internet connection (and those offered by the mobile network)."""
        res = app.cli_try("mifi_qcmap_cli", "get_active_dns")
        out = parse_active_dns(res)
        out["available"] = res is not None
        data = app.rest_try("CellularDataConnectionStatus", {}) or {}
        out["mobile_v4"] = [x for x in (data.get("CurrentPrimaryDNSIPv4Address"),
                                         data.get("CurrentSecondaryDNSIPv4Address")) if x]
        return out

    @r.route("GET", "/api/dns/rebinding")
    def get_rebinding(req):
        """DNS rebinding protection (blocks answers that point public names at private addresses)."""
        res = app.cli_try("mifi_qcmap_cli", "get_dns_rebinding_status")
        if res is None:
            raise unavailable("DNS rebinding protection is not available on this unit")
        return {"enabled": bool(res.get_int("DNS Rebinding Protection Status", 0))}

    @r.route("PUT", "/api/dns/rebinding")
    def put_rebinding(req):
        """Turn DNS rebinding protection on/off (takes effect after a restart)."""
        data = validate.only_keys(req.json, ["enabled"])
        enabled = validate.boolean("enabled", data.get("enabled"))
        restart = run_set(app, "mifi_qcmap_cli", "set_dns_rebinding_status", [1 if enabled else 0],
                          "Saving DNS rebinding protection")
        app.audit(req, "dns.rebinding", {"enabled": enabled})
        out = get_rebinding(req)
        out["restart_required"] = restart
        return out

    def filter_state() -> Dict[str, Any]:
        res = app.cli_try("mifi_qcmap_cli", "get_dns_filter")
        # on failure the vendor prints "<fn> DNS Filter: returned N (...)", which the core RC parser
        # does not recognise (the word before "returned" ends in ':'), so check it here
        m = re.search(r"DNS Filter: returned (-?\d+)", res.text) if res is not None else None
        if res is None or (m and int(m.group(1)) != 0):
            raise unavailable("The DNS filter is not available on this unit")
        out = parse_dns_filter(res)
        out["max_domains"] = MAX_FILTER_DOMAINS
        return out

    @r.route("GET", "/api/dns/filter")
    def get_filter(req):
        """DNS filter: an allow list (only these domains resolve) or a block list."""
        return filter_state()

    @r.route("PUT", "/api/dns/filter")
    def put_filter(req):
        """Replace the DNS filter (list type, domains, on/off). dnsmasq restarts briefly."""
        data = validate.only_keys(req.json, ["enabled", "mode", "domains"])
        cur = filter_state()
        enabled = validate.boolean("enabled", data.get("enabled", cur.get("enabled", False)))
        mode = validate.choice("mode", data.get("mode", cur.get("mode", "block")), ["allow", "block"])
        raw = data.get("domains", cur.get("domains", []))
        if isinstance(raw, str):
            raw = re.split(r"[,\s]+", raw)
        if not isinstance(raw, list):
            raise field_error("domains", "Must be a list of domain names")
        domains: List[str] = []
        for d in raw:
            if not isinstance(d, str):
                raise field_error("domains", "Must be a list of domain names")
            d = d.strip().lower().rstrip(".")
            if d.startswith("*."):
                d = d[2:]            # dnsmasq server=/x/ already covers every sub-domain
            if not d:
                continue
            if not DOMAIN_RE.match(d) or re.match(r"^[\d.]+$", d):
                raise field_error("domains", "Not a valid domain name: %s" % d[:64])
            if d not in domains:
                domains.append(d)
        if len(domains) > MAX_FILTER_DOMAINS:
            raise field_error("domains", "At most %d domains" % MAX_FILTER_DOMAINS)
        joined = ",".join(domains)
        if len(joined) > MAX_FILTER_CHARS:
            raise field_error("domains", "The list is too long (at most %d characters)" % MAX_FILTER_CHARS)
        if enabled and not domains:
            raise field_error("domains", "Add at least one domain")
        if domains:
            run_set(app, "mifi_qcmap_cli", "set_dns_filter",
                    [1, 1 if enabled else 0, 1 if mode == "block" else 0, joined], "Saving the DNS filter")
        else:
            run_set(app, "mifi_qcmap_cli", "set_dns_filter", [0, 0, 0, ""], "Clearing the DNS filter")
        app.audit(req, "dns.filter.update", {"enabled": enabled, "mode": mode, "count": len(domains)})
        return filter_state()

    @r.route("DELETE", "/api/dns/filter")
    def del_filter(req):
        """Remove the DNS filter completely."""
        run_set(app, "mifi_qcmap_cli", "set_dns_filter", [0, 0, 0, ""], "Clearing the DNS filter")
        app.audit(req, "dns.filter.clear")
        return filter_state()
