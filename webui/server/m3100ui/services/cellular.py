"""Cellular area: shared helpers, feature probes and connection actions.

Pages (web/app/pages/cellular/*) and their services:
  Signal & cell        cellular_signal.py    GET  /api/cellular/signal[/diag]
  Network mode         cellular_mode.py      GET/PUT /api/cellular/mode, POST .../mode/restore
  Bands                cellular_bands.py     GET/PUT /api/cellular/bands, priority, CA, EFS decode
  Network selection    cellular_network.py   GET/PUT /api/cellular/network, scan/select/validate/clear
  Antenna & advanced   cellular_antenna.py   GET/PUT /api/cellular/antenna, call throttle check
This module: POST /api/cellular/connect|disconnect|reconnect, GET /api/cellular/overview.

Data sources follow design section 5: vendor REST first (CellularServiceSettings, CellularServiceStatus,
Antenna/External/Enabled), modem2_cli / ccm2_cli / webui_cli for everything REST lacks.
Setters only run through the Specs in adapters/specs/cellular.py (and core.py for bands/tech/NR mode).
"""
from typing import Any, Dict, List, Optional

from ..errors import ApiError, unavailable
from . import cellular_parse as P

M2 = "modem2_cli"
CCM = "ccm2_cli"
WEBUI = "webui_cli"


# -- helpers shared by the cellular_* service modules ------------------------------------------

def get(app, cli: str, cmd: str, answers=(), timeout: Optional[float] = None):
    """Read-only CLI call -> CliResult, or None when the command failed / is unavailable."""
    return app.cli_try(cli, cmd, [str(a) for a in answers], timeout=timeout)


def get_any(app, cli: str, cmd: str, answers=(), timeout: Optional[float] = None):
    """Like get() but also returns failed results (rc != 0) so callers can show the vendor message."""
    try:
        return app.cli.run(cli, cmd, [str(a) for a in answers], timeout=timeout)
    except ApiError:
        return None


def run_set(app, cli: str, cmd: str, values: List[Any], what: str, timeout: Optional[float] = None):
    """Run a setter through its Spec; vendor failures become 502 with the vendor message."""
    return app.cli.run(cli, cmd, [str(v) for v in values], timeout=timeout, check=True, what=what)


def flag(res, label: str) -> Optional[bool]:
    if res is None:
        return None
    v = P.to_int(res.get(label))
    return None if v is None else bool(v)


def int_of(res, label: str) -> Optional[int]:
    if res is None:
        return None
    return P.to_int(res.get(label))


def sim_present(app) -> Optional[bool]:
    sim = app.rest_try("SimStatus", {}) or {}
    st = str(sim.get("CurrentSimStatus", "")).lower()
    if not st:
        return None
    return st not in ("notpresent", "absent", "not present")


def snapshot(app, name: str, value: Any) -> None:
    app.state.snapshot("cellular." + name, value)


def last_snapshot(app, name: str) -> Optional[Dict[str, Any]]:
    h = app.state.snapshots("cellular." + name)
    return h[-1] if h else None


def require_modem(app) -> None:
    if get(app, M2, "is_running") is None:
        raise unavailable("The modem service is not running, so radio settings cannot be read right now.")


def invalidate(app) -> None:
    app.cache.invalidate("status")
    app.cache.invalidate("cellular:")


def register(app):
    r = app.router

    app.features.add("cellular.radio", lambda: get(app, M2, "is_running") is not None)
    app.features.add("cellular.connection", lambda: get(app, CCM, "is_running") is not None)
    app.features.add("cellular.ext_antenna", lambda: app.rest_try("Antenna/External/Enabled") is not None
                     or get(app, M2, "get_ext_ant_enabled") is not None)

    def conn_state() -> Dict[str, Any]:
        data = app.rest_try("CellularDataConnectionStatus", {}) or {}
        res = get(app, CCM, "get_conn_state")
        v4 = res.get("V4") if res else None
        v6 = res.get("V6") if res else None
        return {"status": data.get("CurrentStatus", ""), "ipv4_state": v4, "ipv6_state": v6,
                "ipv4": data.get("CurrentExternalIPv4Address", ""), "ipv6": data.get("CurrentExternalIPv6Address", ""),
                "duration_s": data.get("CurrentCallDuration", 0)}

    @r.route("GET", "/api/cellular/overview")
    def overview(req):
        """Short cellular summary: SIM, service, connection, radio switches."""
        svc = app.rest_try("CellularServiceStatus", {}) or {}
        cset = app.rest_try("CellularServiceSettings", {}) or {}
        return {"sim_present": sim_present(app), "service": svc.get("CurrentServiceStatus", ""),
                "operator": svc.get("CurrentCarrierNetworkName", ""), "tech": svc.get("CurrentWirelessTechnology", ""),
                "connection": conn_state(), "data_enabled": cset.get("CellularDataEnabled"),
                "radio": flag(get(app, M2, "radio_is_enabled"), "Radio enabled: (disabled=0, enabled=1)"),
                "airplane": flag(get(app, CCM, "get_air_mode_enabled"), "enabled")}

    def action(req, cmds, what):
        for cmd in cmds:
            run_set(app, CCM, cmd, [""], what, timeout=30)
        invalidate(app)
        app.audit(req, "cellular." + what.lower().replace(" ", "_"))
        return {"ok": True, "connection": conn_state()}

    @r.route("POST", "/api/cellular/connect")
    def connect(req):
        """Start the mobile data connection now (ccm2 wwan_connect)."""
        return action(req, ["wwan_connect"], "Connect")

    @r.route("POST", "/api/cellular/disconnect")
    def disconnect(req):
        """Stop the mobile data connection now (ccm2 wwan_disconnect)."""
        return action(req, ["wwan_disconnect"], "Disconnect")

    @r.route("POST", "/api/cellular/reconnect")
    def reconnect(req):
        """Disconnect and connect again (renews the IP address)."""
        return action(req, ["wwan_disconnect", "wwan_connect"], "Reconnect")
