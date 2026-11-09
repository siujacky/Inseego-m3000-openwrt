"""Cellular > Antenna & advanced: external antenna ports, LTE-U / Wi-Fi 5 GHz coexistence, autonomous gaps,
plus read-only radio status (call throttling, world mode, test SIM, emergency mode, BSR timers, PCO, eMBMS).

GET /api/cellular/antenna                  all values
PUT /api/cellular/antenna                  {"external": bool, "mhb": bool, "uhb": bool, "lte_u": bool,
                                            "autonomous_gap": bool}   every key is radio-affecting: tier 2
GET /api/cellular/antenna/throttle?code=N  is a call-failure code currently throttled (modem2, read-only)

external        REST Antenna/External/Enabled (vendor-validated) — fallback modem2 set_ext_ant_enabled
mhb / uhb       modem2 get/set_ext_ant_enabled (mid/high band and ultra-high band ports separately)
lte_u           modem2 set_lte_wifi_coex (the modem has no getter: the value this UI last set is shown)
autonomous_gap  modem2 get/set_autonomous_gap_enabled
"""
import time
from typing import Any, Dict, Optional

from .. import validate
from ..errors import field_error, unavailable
from . import cellular_parse as P
from .cellular import CCM, M2, get, get_any, invalidate, run_set

COEX_FILE = "cellular/lte-u.json"


def register(app):
    r = app.router

    def ext_ports() -> Optional[Dict[str, bool]]:
        res = get(app, M2, "get_ext_ant_enabled")
        if res is None:
            return None
        m, u = P.to_int(res.get("mhb enabled")), P.to_int(res.get("uhb enabled"))
        if m is None or u is None:
            return None
        return {"mhb": bool(m), "uhb": bool(u)}

    def read_flag(cmd: str, label: str) -> Dict[str, Any]:
        res = get_any(app, M2, cmd)
        if res is None or not res.ok:
            return {"value": None, "message": res.message if res is not None else "not available"}
        v = P.to_int(res.get(label))
        return {"value": None if v is None else bool(v), "message": ""}

    def read() -> Dict[str, Any]:
        rest = app.rest_try("Antenna/External/Enabled")
        ports = ext_ports()
        gap = read_flag("get_autonomous_gap_enabled", "Autonomous gap enabled")
        coex = app.state.read_json(COEX_FILE)
        thr = get(app, CCM, "get_call_throttle_state")
        bsr = get(app, M2, "bsr_get_timers")
        pco = get(app, M2, "get_pco_event")
        embms = get(app, M2, "get_lte_embms_info")
        dss = get(app, M2, "get_dss")
        external = None
        if isinstance(rest, dict) and "Enabled" in rest:
            external = bool(P.to_int(rest.get("Enabled"), 0))
        elif ports is not None:
            external = ports["mhb"] or ports["uhb"]
        return {
            "external": external, "external_source": "rest" if isinstance(rest, dict) else ("cli" if ports else None),
            "mhb": ports["mhb"] if ports else None, "uhb": ports["uhb"] if ports else None,
            "lte_u": coex.get("enabled") if coex else None,
            "lte_u_set_at": coex.get("t") if coex else None,
            "autonomous_gap": gap["value"],
            "status": {
                "autonomous_gap_message": gap["message"],
                "call_throttle": None if thr is None else {
                    "ipv4": bool(P.to_int(thr.get("IPv4 Throttled"), 0)),
                    "ipv6": bool(P.to_int(thr.get("IPv6 Throttled"), 0))},
                "world_mode": read_flag("get_world_mode_enabled", "enabled")["value"],
                "test_sim": read_flag("get_test_sim_status", "test_sim")["value"],
                "emergency_mode": read_flag("emergency_get_mode", "Emergency Mode enabled")["value"],
                "home_network": read_flag("validate_home", "Home network")["value"],
                "dss": P.to_int(dss.get("dss")) if dss is not None else None,
                "bsr": None if bsr is None else {"bsr_time_s": P.to_int(bsr.get("BSR time")),
                                                  "max_time_s": P.to_int(bsr.get("Max_time")),
                                                  "next_lte_scan_s": P.to_int(bsr.get("Next lte scan"))},
                "pco": None if pco is None else {"value": P.to_int(pco.get("pco_value")), "pdn": P.to_int(pco.get("pdn"))},
                "embms": None if embms is None else ("none" if "length is Zero" in (embms.text or "") else "present"),
            },
        }

    @r.route("GET", "/api/cellular/antenna")
    def get_antenna(req):
        """External antenna ports, LTE-U coexistence, autonomous gaps and read-only radio status."""
        return read()

    @r.route("PUT", "/api/cellular/antenna")
    def put_antenna(req):
        """Change antenna / coexistence settings (tier 2: affects reception)."""
        data = validate.only_keys(req.json, ["external", "mhb", "uhb", "lte_u", "autonomous_gap"])
        if not data:
            raise field_error("external", "Nothing to change")
        vals = {k: validate.boolean(k, v) for k, v in data.items()}
        app.require_tier(req, 2)
        done = []
        if "external" in vals:
            rest = app.rest_try("Antenna/External/Enabled")
            if isinstance(rest, dict) and "Enabled" in rest:
                app.rest.merge_put("Antenna/External/Enabled", {"Enabled": int(vals["external"])})
            else:
                if ext_ports() is None:
                    raise unavailable("The external antenna setting is not available on this device")
                run_set(app, M2, "set_ext_ant_enabled", [int(vals["external"])] * 2, "Switching the external antenna")
            done.append("external")
        if "mhb" in vals or "uhb" in vals:
            cur = ext_ports()
            if cur is None:
                raise unavailable("The antenna port settings are not available on this device")
            mhb = vals.get("mhb", cur["mhb"])
            uhb = vals.get("uhb", cur["uhb"])
            run_set(app, M2, "set_ext_ant_enabled", [int(mhb), int(uhb)], "Switching the antenna ports")
            done += [k for k in ("mhb", "uhb") if k in vals]
        if "lte_u" in vals:
            run_set(app, M2, "set_lte_wifi_coex", [int(vals["lte_u"])], "Setting LTE-U / Wi-Fi coexistence")
            app.state.write_json(COEX_FILE, {"enabled": vals["lte_u"], "t": int(time.time())})
            done.append("lte_u")
        if "autonomous_gap" in vals:
            run_set(app, M2, "set_autonomous_gap_enabled", [int(vals["autonomous_gap"])], "Setting autonomous gaps")
            done.append("autonomous_gap")
        invalidate(app)
        app.audit(req, "cellular.antenna.update", {"keys": done})
        return read()

    @r.route("GET", "/api/cellular/antenna/throttle")
    def throttle(req):
        """Whether calls failing with the given network cause code are currently throttled."""
        code = validate.integer("code", req.query.get("code", "0"), 0, 65535)
        res = get_any(app, M2, "get_call_throttle_status", [code])
        if res is None or not res.ok:
            raise unavailable("Call throttle status is not available: %s" % (res.message if res is not None else "no answer"))
        return {"code": code, "throttled": bool(P.to_int(res.get("Throttle"), 0))}
