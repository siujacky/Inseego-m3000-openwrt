"""GPS: engine on/off (start/stop), start at boot, location-privacy agreement, A-GPS mode,
NMEA-over-Wi-Fi TCP server, last fix, XTRA download.

REST GPSSettings mirrors the stock GPS page (GPSEnabled = sys_setGPSEnabled -> gps_start/gps_stop after
sys_setGPSPrivacyAgreementAccepted; NMEA fields = gps_set_nmea_over_tcp).  REST GPSStatus is disabled on
this firmware (403), so status/fix come from gps_cli.  No map tiles are loaded: the page shows the
coordinates and offers a link the user may open.
"""
from typing import Any, Dict

from .. import validate
from ..errors import ApiError, field_error, unavailable
from .device import b01, flag, mock_link, run_setter, to_float, to_int

MODES = {"XTRA": "xtra", "MS-A": "msa", "MSA": "msa", "MS-B": "msb", "MSB": "msb"}
MODE_ANSWER = {"xtra": 0, "msa": 1, "msb": 2}            # gps_cli agps_mode_set: XTRA(0), MSA(1), MSB(2)
FIX_FIELDS = ["alt_ellipsoid", "fix_quality", "fixed", "heading", "horizontal_conf", "horizontal_dop", "horizontal_speed",
              "horiz_speed_unc", "horiz_unc_cir", "latitude", "longitude", "num_locked_prn", "position_dop", "timestamp",
              "first_fix_timestamp", "vertical_dop", "vertical_speed", "vert_speed_unc", "vertical_unc"]


def parse_fix(res) -> Dict[str, Any]:
    """gps_cli get_last_fix -> normalised fix (units: metres, m/s, degrees)."""
    raw = {k: res.get(k) for k in FIX_FIELDS}
    lat, lon = to_float(raw["latitude"]), to_float(raw["longitude"])
    ts = to_int(raw["timestamp"])
    fixed = flag(raw["fixed"])
    valid = bool(fixed) and lat is not None and lon is not None and not (lat == 0 and lon == 0) \
        and -90 <= lat <= 90 and -180 <= lon <= 180
    return {
        "valid": valid, "fixed": bool(fixed), "latitude": lat, "longitude": lon,
        "altitude_m": to_float(raw["alt_ellipsoid"]), "accuracy_m": to_float(raw["horiz_unc_cir"]),
        "vertical_accuracy_m": to_float(raw["vertical_unc"]), "heading_deg": to_float(raw["heading"]),
        "speed_mps": to_float(raw["horizontal_speed"]), "vertical_speed_mps": to_float(raw["vertical_speed"]),
        "satellites": to_int(raw["num_locked_prn"]), "quality": to_int(raw["fix_quality"]),
        "confidence_pct": to_int(raw["horizontal_conf"]), "hdop": to_float(raw["horizontal_dop"]),
        "pdop": to_float(raw["position_dop"]), "vdop": to_float(raw["vertical_dop"]),
        "timestamp": ts, "time": ts if ts and ts > 1000000000 else None,
        "geo_uri": "geo:%.6f,%.6f" % (lat, lon) if valid else "",
    }


def register(app):
    r = app.router
    mock_link(app)

    def cli(cmd):
        return app.cli_try("gps_cli", cmd)

    def state() -> Dict[str, Any]:
        rest = app.rest_try("GPSSettings")
        st = cli("gps_status")
        act = cli("get_active")
        prv = cli("get_privacy")
        mode = cli("get_mode")
        nmea = cli("get_nmea_tcp")
        if not any((rest is not None, st, act, nmea)):
            raise unavailable("GPS is not available on this unit")
        status = (st.get("status") if st else "") or ""
        running = bool(rest.get("GPSEnabled")) if isinstance(rest, dict) else (status.lower() not in ("", "disabled"))
        out = {
            "supported": True, "rest": isinstance(rest, dict), "running": running, "status": status or ("On" if running else "Off"),
            "start_at_boot": (act.get("default_status") or "").lower() == "on" if act else None,
            "privacy_accepted": bool(prv.get_int("accepted", 0)) if prv else None,
            "mode": MODES.get((mode.get("mode") or "").upper(), (mode.get("mode") or "").lower() or None) if mode else None,
            "nmea_enabled": None, "nmea_port": None, "nmea_active": None,
            "service_running": bool(cli("is_running")),
        }
        if nmea:
            out["nmea_enabled"] = bool(nmea.get_int("enabled", 0))
            out["nmea_port"] = nmea.get_int("port")
        if isinstance(rest, dict):
            out["nmea_active"] = bool(rest.get("NMEAOverWiFiEnabled"))
            if out["nmea_enabled"] is None:
                out["nmea_enabled"] = out["nmea_active"]
            if out["nmea_port"] is None:
                out["nmea_port"] = rest.get("NMEAOverWiFiPortNumber")
        lan = app.cache.get("gps:lan", 60, lambda: app.rest_try("RouterSettings", {}) or {})
        out["lan_ip"] = lan.get("RouterIPv4Address", "")
        return out

    @r.route("GET", "/api/gps")
    def get_gps(req):
        """GPS state: engine, start at boot, privacy, A-GPS mode, NMEA server."""
        return state()

    def write_rest(changes: Dict[str, Any], cur: Dict[str, Any]) -> None:
        # always send the NMEA fields from the raw setting so a masked REST value is never written back
        body = {"NMEAOverWiFiEnabled": bool(cur.get("nmea_enabled")),
                "NMEAOverWiFiPortNumber": int(cur.get("nmea_port") or 11010)}
        body.update(changes)
        app.rest.merge_put("GPSSettings", body)

    def set_nmea(en: bool, port: int, cur: Dict[str, Any]) -> None:
        if cur["rest"]:
            write_rest({"NMEAOverWiFiEnabled": en, "NMEAOverWiFiPortNumber": port}, cur)
        else:
            run_setter(app, "gps_cli", "set_nmea_tcp", [b01(en), port], "Saving the NMEA server")

    @r.route("PUT", "/api/gps")
    def put_gps(req):
        """Change GPS settings (start at boot, privacy agreement, A-GPS mode, NMEA server)."""
        data = validate.only_keys(req.json, ["start_at_boot", "privacy_accepted", "mode", "nmea_enabled", "nmea_port"])
        cur = state()
        if "privacy_accepted" in data:
            v = validate.boolean("privacy_accepted", data["privacy_accepted"])
            if v != cur["privacy_accepted"]:
                if not v and cur["running"]:
                    stop(cur)
                run_setter(app, "gps_cli", "set_privacy", [b01(v)], "Saving the location privacy agreement")
        if "start_at_boot" in data:
            v = validate.boolean("start_at_boot", data["start_at_boot"])
            if v != cur["start_at_boot"]:
                run_setter(app, "gps_cli", "set_active", [b01(v)], "Saving GPS start at boot")
        if "mode" in data:
            m = validate.choice("mode", data["mode"], list(MODE_ANSWER))
            if m != cur["mode"]:
                run_setter(app, "gps_cli", "agps_mode_set", [MODE_ANSWER[m]], "Saving the A-GPS mode")
        if "nmea_enabled" in data or "nmea_port" in data:
            en = validate.boolean("nmea_enabled", data["nmea_enabled"]) if "nmea_enabled" in data else bool(cur["nmea_enabled"])
            port = validate.integer("nmea_port", data["nmea_port"], 1024, 65535) if "nmea_port" in data else cur["nmea_port"]
            if port is None or not 1024 <= int(port) <= 65535:
                raise field_error("nmea_port", "Enter a port between 1024 and 65535")
            if en != cur["nmea_enabled"] or port != cur["nmea_port"]:
                set_nmea(en, int(port), cur)
        app.audit(req, "gps.update", {"keys": sorted(data)})
        return state()

    def stop(cur: Dict[str, Any]) -> None:
        if cur["rest"]:
            write_rest({"GPSEnabled": False}, cur)
        else:
            run_setter(app, "gps_cli", "gps_stop", [], "Stopping GPS")

    @r.route("POST", "/api/gps/start")
    def start_gps(req):
        """Start the GPS engine (accepts the location privacy agreement, like the stock page)."""
        data = validate.only_keys(req.json, ["accept_privacy"])
        cur = state()
        if not cur["privacy_accepted"] and data.get("accept_privacy") is not True:
            raise field_error("accept_privacy", "Accept the location privacy notice to turn GPS on")
        if cur["rest"]:
            write_rest({"GPSEnabled": True}, cur)
        else:
            if not cur["privacy_accepted"]:
                run_setter(app, "gps_cli", "set_privacy", ["1"], "Accepting the location privacy agreement")
            run_setter(app, "gps_cli", "gps_start", [], "Starting GPS")
        app.audit(req, "gps.start")
        return state()

    @r.route("POST", "/api/gps/stop")
    def stop_gps(req):
        """Stop the GPS engine."""
        stop(state())
        app.audit(req, "gps.stop")
        return state()

    @r.route("POST", "/api/gps/xtra")
    def force_xtra(req):
        """Download fresh XTRA assistance data now (needs a mobile data connection)."""
        run_setter(app, "gps_cli", "force_xtra", [], "Requesting XTRA data")
        app.audit(req, "gps.xtra")
        return {"ok": True}

    @r.route("GET", "/api/gps/fix")
    def last_fix(req):
        """Most recent position fix (coordinates, accuracy, satellites, time)."""
        try:
            res = app.cli.run("gps_cli", "get_last_fix")
        except ApiError:
            res = None
        if res is None:
            return {"available": False, "reason": "unavailable", "message": "The GPS service did not answer."}
        if not res.ok:
            privacy = res.rc == 1001 or "privacy" in (res.message or "").lower()
            return {"available": False, "reason": "privacy" if privacy else "no_fix",
                    "message": "Accept the location privacy notice and turn GPS on to get a position." if privacy
                    else (res.message or "No position yet.")}
        fix = parse_fix(res)
        return {"available": fix["valid"], "reason": "" if fix["valid"] else "no_fix",
                "message": "" if fix["valid"] else "No position yet. Outdoors with a clear sky view a first fix can take a few minutes.",
                "fix": fix}

    app.features.add("gps", lambda: app.cli_try("gps_cli", "get_active") is not None
                     or app.rest_try("GPSSettings") is not None)
