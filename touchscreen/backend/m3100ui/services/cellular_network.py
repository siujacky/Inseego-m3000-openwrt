"""Cellular > Network selection: automatic vs manual (MNS), operator scan, select, validate.

GET  /api/cellular/network            mode, current operator, MNS state, last scan results
PUT  /api/cellular/network            {"mode": "automatic"} (tier 2: reverts a manual lock) | {"mode": "manual"}
POST /api/cellular/network/scan       start an operator scan (tier 2; runs in the background, up to 180 s)
GET  /api/cellular/network/scan       scan progress + results (poll every few seconds)
POST /api/cellular/network/select     {"mccmnc": "311480", "act": 7}   lock to an operator (tier 2)
POST /api/cellular/network/validate   ask the modem whether the manual selection is allowed (tier 2: may revert)
POST /api/cellular/network/clear      clear the stored scan results

Backend: webui_cli get/set_automatic_network_selection (the vendor UI setting), modem2_cli mns_get_info,
mns_start_scan, mns_get_list, mns_set_oper, mns_validate, mns_clear_list.  The scan list is the
`+COPS: (stat,"mccmnc","name",act,roaming)` text built by libmal_qct (see cellular_parse.parse_mns_list).
"""
import threading
import time
from typing import Any, Dict, Optional

from .. import validate
from ..errors import ApiError, conflict
from . import cellular_parse as P
from .cellular import M2, WEBUI, get, get_any, invalidate, run_set, sim_present

SCAN_BUDGET_S = 180.0
LAST = "cellular/network-last.json"


class ScanState:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = "idle"          # idle | starting | scanning | complete | failed | timeout
        self.started = 0.0
        self.finished = 0.0
        self.error = ""
        self.thread: Optional[threading.Thread] = None
        self.networks = []

    def to_json(self) -> Dict[str, Any]:
        now = time.time()
        return {"state": self.state, "started": int(self.started) or None,
                "elapsed_s": int((self.finished or now) - self.started) if self.started else 0,
                "budget_s": int(SCAN_BUDGET_S), "error": self.error, "networks": list(self.networks)}


def register(app):
    r = app.router
    scan = ScanState()
    app.cellular_scan = scan  # tests join scan.thread

    def mns_info() -> Optional[Dict[str, Any]]:
        res = get_any(app, M2, "mns_get_info")
        if res is None or not res.ok:
            return None
        st = P.to_int(res.get("Scan Status"), 0)
        return {"allowed": bool(P.to_int(res.get("Is Allowed"), 0)), "selected": bool(P.to_int(res.get("Is Selected"), 0)),
                "scan_status": P.MNS_SCAN.get(st, str(st)), "scan_code": st}

    def mns_error() -> str:
        res = get_any(app, M2, "mns_get_info")
        return res.message if res is not None and not res.ok else ""

    def auto_setting() -> Optional[bool]:
        res = get(app, WEBUI, "get_automatic_network_selection")
        if res is None:
            return None
        v = P.to_int(res.get("automatic_network_selection enabled"))
        return None if v is None else bool(v)

    def scan_list():
        res = get_any(app, M2, "mns_get_list", timeout=30)
        if res is None or not res.ok:
            return None
        return P.parse_mns_list(res.text)

    def read() -> Dict[str, Any]:
        info = mns_info()
        auto = auto_setting()
        svc = app.rest_try("CellularServiceStatus", {}) or {}
        oper = get(app, M2, "get_oper_info")
        sig = P.parse_signal(get(app, M2, "get_signal"))
        home = get(app, M2, "validate_home")
        mccmnc = (oper.get("Mcc Mnc") if oper else "") or sig.get("mccmnc") or ""
        if mccmnc.strip("0") == "":
            mccmnc = ""
        selected = bool(info and info["selected"])
        mode = "manual" if selected or auto is False else "automatic"
        notes = []
        if info is None:
            msg = mns_error()
            notes.append("Manual network selection status is not available%s." % (" (%s)" % msg if msg else ""))
        if sim_present(app) is False:
            notes.append("No SIM card: scanning and manual selection need a SIM.")
        with scan.lock:
            sc = scan.to_json()
        if not sc["networks"] and sc["state"] in ("idle",):
            lst = scan_list()
            if lst:
                sc["networks"] = lst
        return {
            "mode": mode, "automatic_setting": auto, "mns": info,
            "current": {"operator": svc.get("CurrentCarrierNetworkName") or sig.get("operator") or
                        (oper.get("Operator") if oper else "") or "",
                        "mccmnc": mccmnc, "tech": sig.get("tech") or svc.get("CurrentWirelessTechnology", ""),
                        "roaming_state": svc.get("CurrentRoamingState", ""), "service": svc.get("CurrentServiceStatus", ""),
                        "home_network": None if home is None else bool(P.to_int(home.get("Home network"), 0))},
            "scan": sc, "last_selection": app.state.read_json(LAST), "notes": notes,
        }

    @r.route("GET", "/api/cellular/network")
    def get_network(req):
        """Automatic / manual network selection state, current operator and last scan results."""
        return read()

    @r.route("PUT", "/api/cellular/network")
    def put_network(req):
        """Switch between automatic and manual network selection."""
        data = validate.only_keys(req.json, ["mode"])
        mode = validate.choice("mode", data.get("mode"), ["automatic", "manual"])
        notes = []
        if mode == "automatic":
            app.require_tier(req, 2)
            run_set(app, WEBUI, "set_automatic_network_selection", [1], "Enabling automatic network selection")
            info = mns_info()
            last = app.state.read_json(LAST) or {}
            try:
                run_set(app, M2, "mns_set_oper", [0, last.get("mccmnc") or "000000", last.get("act", 7)],
                        "Returning to automatic network selection", timeout=60)
            except ApiError as e:
                if info and info["selected"]:
                    raise
                notes.append(e.message)
            try:
                run_set(app, M2, "mns_clear_list", [], "Clearing the network list", timeout=20)
            except ApiError as e:
                notes.append(e.message)
            with scan.lock:
                scan.networks = []
                if scan.state != "scanning":
                    scan.state = "idle"
        else:
            run_set(app, WEBUI, "set_automatic_network_selection", [0], "Disabling automatic network selection")
        invalidate(app)
        app.audit(req, "cellular.network.mode", {"mode": mode})
        out = read()
        out["notes"] = out["notes"] + notes
        return out

    def scan_worker():
        err = ""
        try:
            try:  # drop old results so a stale "scan complete" is not mistaken for this scan
                app.cli.run(M2, "mns_clear_list", [], timeout=20)
            except ApiError:
                pass
            res = app.cli.run(M2, "mns_start_scan", [], timeout=SCAN_BUDGET_S)
            if not res.ok:
                err = "The modem could not start a scan: %s" % (res.message or "no answer")
        except ApiError as e:
            err = e.message
        with scan.lock:
            if err:
                scan.state, scan.error, scan.finished = "failed", err, time.time()
            elif scan.state == "starting":
                scan.state = "scanning"

    @r.route("POST", "/api/cellular/network/scan")
    def start_scan(req):
        """Scan for mobile networks (takes up to 3 minutes; the connection may drop meanwhile)."""
        app.require_tier(req, 2)
        with scan.lock:
            if scan.state in ("starting", "scanning") and time.time() - scan.started < SCAN_BUDGET_S:
                raise conflict("A network scan is already running", scan.to_json())
            scan.state, scan.started, scan.finished, scan.error, scan.networks = "starting", time.time(), 0.0, "", []
            scan.thread = threading.Thread(target=scan_worker, name="mns-scan", daemon=True)
            scan.thread.start()
        app.audit(req, "cellular.network.scan")
        with scan.lock:
            return scan.to_json()

    @r.route("GET", "/api/cellular/network/scan")
    def poll_scan(req):
        """Progress and results of the running / last network scan."""
        with scan.lock:
            state, started = scan.state, scan.started
        if state in ("starting", "scanning"):
            info = mns_info()
            lst = None
            if info and info["scan_code"] == 2:
                lst = scan_list()
            elif info and info["scan_code"] == 0 and state == "scanning" and time.time() - started > 5:
                lst = scan_list()           # finished between two polls (status already back to idle)
            with scan.lock:
                if lst is not None and scan.state in ("starting", "scanning"):
                    scan.networks, scan.state, scan.finished = lst, "complete", time.time()
                elif time.time() - started > SCAN_BUDGET_S and scan.state in ("starting", "scanning"):
                    scan.state, scan.finished = "timeout", time.time()
                    scan.error = "No result after %d seconds" % SCAN_BUDGET_S
        with scan.lock:
            return scan.to_json()

    @r.route("POST", "/api/cellular/network/select")
    def select(req):
        """Lock the modem to one operator (manual network selection)."""
        data = validate.only_keys(req.json, ["mccmnc", "act", "name"])
        mccmnc = validate.text("mccmnc", str(data.get("mccmnc", "")), 5, 6, pattern=r"^[0-9]{5,6}$")
        act = validate.choice("act", P.to_int(data.get("act"), -1), sorted(P.MNS_ACT))
        name = data.get("name") if isinstance(data.get("name"), str) else ""
        app.require_tier(req, 2)
        run_set(app, WEBUI, "set_automatic_network_selection", [0], "Disabling automatic network selection")
        run_set(app, M2, "mns_set_oper", [1, mccmnc, act], "Selecting the network", timeout=90)
        app.state.write_json(LAST, {"mccmnc": mccmnc, "act": act, "name": name[:64], "t": int(time.time())})
        invalidate(app)
        app.audit(req, "cellular.network.select", {"mccmnc": mccmnc, "act": act})
        return read()

    @r.route("POST", "/api/cellular/network/validate")
    def validate_sel(req):
        """Check the manual selection with the modem (it reverts to automatic when not allowed)."""
        app.require_tier(req, 2)
        res = run_set(app, M2, "mns_validate", [], "Checking the manual network selection", timeout=60)
        out = read()
        out["validation"] = P.parse_mns_validate(res.text)
        app.audit(req, "cellular.network.validate")
        return out

    @r.route("POST", "/api/cellular/network/clear")
    def clear(req):
        """Forget the stored scan results."""
        run_set(app, M2, "mns_clear_list", [], "Clearing the network list", timeout=20)
        with scan.lock:
            scan.networks = []
            if scan.state not in ("starting", "scanning"):
                scan.state, scan.error = "idle", ""
        app.audit(req, "cellular.network.clear")
        return read()
