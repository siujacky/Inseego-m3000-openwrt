"""SIM card: status, identity (masked + reveal), PIN/PUK, carrier lock, account (PCO) notice, SIM slots.

Data sources (docs/features/sim.md has the per-field map):
  REST  SimStatus, SimLockStatus, SimCarrierLockStatus, AccountInfo, DeviceInfo, MultiSIM/Enabled
  CLI   modem2_cli sim_get_status / sim_pin_get_status / sim_get_carrier / sim_get_gid1|2 / sim_get_mnc_length /
        get_imsi / sim_get_iccid / get_test_sim_status / get_carrier_unlock / get_sim_lock / get_pco_event /
        sim_mgr_* / uim_get_physical_slot_status;  sim_mgr_cli get_*;  nwcli qmi_idl read_file (NV 60002, read only)
Writes (only through Specs in adapters/specs/sim.py):
  modem2_cli sim_unlock_pin / sim_unlock_puk / sim_enable_pin / sim_change_pin / unlock_carrier   (tier 3)
  modem2_cli sim_mgr_switch_slot (tier 2), sim_mgr_set_imsi_priority, sim_mgr_cli set_*, ccm2_cli accept_pco_event
NV writes (the mod ROMs' "clear NV 60002") are deliberately not implemented.

The module-level helpers are shared with services.apn / services.sms / services.usage.
"""
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import ApiError, field_error, unavailable, vendor_error

# --------------------------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------------------------
RC_ANY_RE = re.compile(r"returned\s+(-?\d+)\s+\(([^:()]+):\s*([^)]*)\)")


def mask(value: Any, keep: int = 4) -> str:
    """'89014103211118510720' -> '••••••••••••••••0720' (empty stays empty)."""
    s = str(value or "")
    if not s:
        return ""
    if len(s) <= keep:
        return "•" * len(s)
    return "•" * (len(s) - keep) + s[-keep:]


def to_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    if isinstance(v, bool):
        return int(v)
    try:
        return int(str(v).strip(), 0)
    except (TypeError, ValueError):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return default


def rc_of(res) -> Optional[int]:
    """Result code, also for CLIs whose RC line the core regex misses (sim_mgr_cli '…: [recovery] returned 0 (…)')."""
    if res is None:
        return None
    if res.rc is not None:
        return res.rc
    m = None
    for m in RC_ANY_RE.finditer(res.text or ""):
        pass
    return int(m.group(1)) if m else None


def ok_of(res) -> bool:
    rc = rc_of(res)
    if rc is not None:
        return rc == 0
    return bool(res is not None and res.ok)


def cli_get(app, cli: str, cmd: str, answers=(), timeout: Optional[float] = None):
    """Run a read-only CLI command: the CliResult (also when the vendor reported an error), None if it did not run."""
    try:
        return app.cli.run(cli, cmd, [str(a) for a in answers], timeout=timeout)
    except ApiError:
        return None


def cli_set(app, cli: str, cmd: str, answers, what: str, timeout: Optional[float] = None, need_rc: bool = False):
    """Run a setter through its Spec; vendor failures become 502 vendor_error (static specs: 403 on a real unit)."""
    res = app.cli.run(cli, cmd, [str(a) for a in answers], timeout=timeout)
    rc = rc_of(res)
    if rc is not None and res.rc is None:
        m = None
        for m in RC_ANY_RE.finditer(res.text):
            pass
        res.message = m.group(3).strip().rstrip(".") if m else res.message
    if need_rc and rc is None:
        raise vendor_error("%s: the vendor command printed no result line" % what, {"cli": cli, "command": cmd})
    if (rc is not None and rc != 0) or (rc is None and not res.ok):
        raise vendor_error("%s failed: %s" % (what, res.message or ("timed out" if res.timed_out else "no status")),
                           {"cli": cli, "command": cmd, "rc": rc, "module": res.module})
    return res


def rest_get(app, path: str) -> Optional[Any]:
    try:
        return app.rest.get(path)
    except ApiError:
        return None


# --------------------------------------------------------------------------------------------
# SIM enums (docs/recon/modem2-cli.md §3)
# --------------------------------------------------------------------------------------------
SIM_STATUS = {0: "unknown", 1: "not_supported", 2: "not_found", 3: "ready", 4: "unlocked", 5: "pin_locked",
              6: "puk_locked", 7: "error", 8: "restricted", 9: "pairing_invalid"}
STATE_LABELS = {
    "unknown": "Unknown", "not_supported": "Not supported", "not_found": "No SIM card", "ready": "Ready",
    "unlocked": "Ready (PIN entered)", "pin_locked": "PIN required", "puk_locked": "Blocked (PUK required)",
    "perm_blocked": "Permanently blocked", "error": "SIM error", "restricted": "SIM restricted",
    "pairing_invalid": "SIM not accepted", "invalid": "SIM not valid",
}
REST_SIM_STATUS = {"notpresent": "not_found", "ready": "ready", "pinlocked": "pin_locked", "puklocked": "puk_locked",
                   "simerror": "error", "notvalid": "invalid", "unlocked": "unlocked", "simrestricted": "restricted",
                   "simlocked": "pairing_invalid"}
PIN_STATE = {0: "not_initialized", 1: "enabled_not_verified", 2: "enabled_verified", 3: "disabled", 4: "blocked",
             5: "perm_blocked"}
PCO_LABELS = {2: "The carrier reports this account as restricted.",
              3: "The carrier reports this account is out of credit.",
              5: "The carrier reports this account is not active."}
PIN_RE = r"^[0-9]{4,8}$"
PUK_RE = r"^[0-9]{8}$"
UNLOCK_CODE_RE = r"^[0-9A-Za-z]{4,32}$"
IMSI_RE = r"^[0-9]{6,15}$"
THREE_DIGIT_MNC_MCC = ("302", "310", "311", "312", "313", "316", "334", "338", "342", "344", "346", "348", "354",
                       "356", "358", "360", "365", "376", "405", "708", "722", "732")


def carrier_name(code: str) -> str:
    """SIM carrier enum text from the modem (SIM_CARRIER_*) -> readable name (no operator names in this code)."""
    c = (code or "").strip()
    if not c or c.upper() == "SIM_CARRIER_INFO_NOT_AVAIL" or c.upper().startswith("SIM_CARRIER_UNKNOWN"):
        return ""
    c = re.sub(r"^SIM_CARRIER_", "", c, flags=re.I)
    return " ".join(w if len(w) <= 3 else w.capitalize() for w in c.split("_") if w)


def parse_gid(text: str) -> List[int]:
    """`gid1 [1] : 0x ff` lines (printf "0x%2x") -> byte values."""
    return [int(m.group(2), 16) for m in re.finditer(r"gid[12]\s*\[(\d+)\]\s*:\s*0x\s*([0-9a-fA-F]{1,2})\b", text or "")]


def parse_slot_blocks(text: str) -> List[Dict[str, Any]]:
    """modem2_cli sim_mgr_get_info / sim_mgr_get_slot_info blocks.

    On this firmware the label 'sim_status' carries ACTIVE/INACTIVE and 'sim_state' the SIM status enum
    (swapped relative to the source names), every block says [slot-0] and card_state prints `1[ABSENT]`."""
    out: List[Dict[str, Any]] = []
    parts = re.split(r"\[slot-\d+\]", text or "")
    for n, body in enumerate(parts[1:], start=1):

        def grab(label):
            m = re.search(r"(?m)^[ \t]*%s[ \t]*:[ \t]*(.*)$" % re.escape(label), body)
            return m.group(1).strip() if m else ""

        def num_name(label):
            v = grab(label)
            m = re.match(r"^\[?(-?\d+)\]?\s*:?\s*\[([^\]]*)\]", v)
            return (int(m.group(1)), m.group(2)) if m else (None, "")

        def plain(label):
            v = grab(label)
            m = re.match(r"^\[([^\]]*)\]$", v)
            return (m.group(1) if m else v).strip()

        card_n, card_s = num_name("card_state")
        _st_n, active_s = num_name("sim_status")
        sim_n, _sim_s = num_name("sim_state")
        _car_n, car_s = num_name("sim_carrier")
        wm = re.match(r"^\[(-?\d+)\]\[([^\]]*)\]", grab("wwan_state"))
        out.append({
            "slot": n,
            "present": card_n == 2 or card_s.upper() == "PRESENT",
            "card": (card_s or ("PRESENT" if card_n == 2 else "ABSENT")).lower(),
            "active": active_s.upper() == "ACTIVE",
            "sim_state": SIM_STATUS.get(sim_n if sim_n is not None else -1, "unknown"),
            "carrier": carrier_name(car_s),
            "iccid": plain("iccid"), "imsi": plain("imsi"), "mdn": plain("mdn"), "operator": plain("oper_name"),
            "wwan_state": wm.group(2).replace("MODEM2_WWAN_STATE_", "").lower() if wm else "",
        })
    return out


def parse_phy_slots(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in re.finditer(r"Phy Slot Status\[(\d+)\]:\s*status=\((\d+)\)(\w+),\s*state=\((\d+)\)(\w+),\s*"
                         r"logical_slot=(\d+),\s*iccid=([0-9a-fA-F]*)", text or ""):
        out.append({"index": int(m.group(1)), "present": m.group(3).upper() == "PRESENT", "status": m.group(3).lower(),
                    "active": m.group(5).upper() == "ACTIVE", "logical_slot": int(m.group(6)),
                    "iccid_set": bool(m.group(7))})
    return out


# --------------------------------------------------------------------------------------------
# SIM state (module level so apn / sms / usage can use it)
# --------------------------------------------------------------------------------------------
def sim_detail(app) -> Dict[str, Any]:
    st = cli_get(app, "modem2_cli", "sim_get_status")
    pin = cli_get(app, "modem2_cli", "sim_pin_get_status")
    rest_sim = rest_get(app, "SimStatus") or {}
    rest_lock = rest_get(app, "SimLockStatus") or {}
    code = st.get_int("status is") if st is not None and st.ok else None
    state = SIM_STATUS.get(code, "unknown") if code is not None else "unknown"
    vendor = str(rest_sim.get("CurrentSimStatus", "") or "")
    if state == "unknown":
        state = REST_SIM_STATUS.get(vendor.lower().replace(" ", ""), "unknown")
    pin_code = pin.get_int("status is") if pin is not None and pin.ok else None
    pin_state = PIN_STATE.get(pin_code if pin_code is not None else 0, "not_initialized")
    known = pin_code not in (None, 0)
    if known:
        retries = pin.get_int("retries is")
        puk_retries = pin.get_int("unlocks is")
        lock_enabled = pin_state in ("enabled_not_verified", "enabled_verified", "blocked", "perm_blocked")
    else:
        retries = to_int(rest_lock.get("SimPinLockCounter")) if rest_lock else (
            st.get_int("retries is") if st is not None and st.ok else None)
        puk_retries = to_int(rest_lock.get("SimPukLockCounter")) if rest_lock else (
            st.get_int("unblocks is") if st is not None and st.ok else None)
        lock_enabled = bool(rest_lock.get("SimPinLockEnabled", False))
    if pin_state == "perm_blocked":
        state = "perm_blocked"
    elif pin_state == "blocked" and state in ("ready", "unknown", "pin_locked"):
        state = "puk_locked"
    elif pin_state == "enabled_not_verified" and state in ("ready", "unknown"):
        state = "pin_locked"
    present = state not in ("not_found", "unknown", "not_supported")
    actions: List[str] = []
    if state == "pin_locked":
        actions = ["verify_pin"]
    elif state == "puk_locked":
        actions = ["unblock_puk"]
    elif state in ("ready", "unlocked"):
        actions = ["disable_lock", "change_pin"] if lock_enabled else ["enable_lock"]
    return {"present": present, "state": state, "state_label": STATE_LABELS.get(state, state), "vendor_status": vendor,
            "pin": {"lock_enabled": lock_enabled, "state": pin_state, "retries": retries, "puk_retries": puk_retries,
                    "known": known},
            "actions": actions}


def identity_raw(app) -> Dict[str, str]:
    """ICCID / IMSI / phone number (REST AccountInfo, CLI fallback) and IMEI (REST DeviceInfo, CLI fallback)."""
    acct = rest_get(app, "AccountInfo") or {}
    dev = app.cache.get("sim:deviceinfo", 300, lambda: rest_get(app, "DeviceInfo") or {})
    out = {"iccid": str(acct.get("AccountInfoSimICCID", "") or ""), "imsi": str(acct.get("AccountInfoSimIMSI", "") or ""),
           "msisdn": str(acct.get("AccountInfoMDN", "") or ""), "imei": str(dev.get("DeviceInfoIMEI", "") or "")}
    if not out["imsi"]:
        res = cli_get(app, "modem2_cli", "get_imsi")
        if res is not None and res.ok:
            out["imsi"] = (res.get("imsi") or "").strip()
    if not out["iccid"]:
        res = cli_get(app, "modem2_cli", "sim_get_iccid")
        if res is not None and res.ok:
            out["iccid"] = (res.get("iccid") or "").strip()
    if not out["imei"] or not out["msisdn"]:
        info = app.cache.get("sim:getinfo", 60, lambda: cli_get(app, "modem2_cli", "get_info"))
        if info is not None and info.ok:
            if not out["imei"]:
                out["imei"] = info.get("imei") or ""
            if not out["msisdn"]:
                out["msisdn"] = info.get("mdn") or ""
    for k in out:
        if not re.match(r"^\+?[0-9A-Fa-f]*$", out[k] or ""):   # drops placeholders such as "redacted"
            out[k] = ""
    return out


def home_network(imsi: str, mnc_length: Optional[int]) -> Dict[str, str]:
    if not imsi or len(imsi) < 5 or not imsi.isdigit():
        return {"mcc": "", "mnc": ""}
    n = mnc_length if mnc_length in (2, 3) else (3 if imsi[:3] in THREE_DIGIT_MNC_MCC else 2)
    return {"mcc": imsi[:3], "mnc": imsi[3:3 + n]}


def sim_extras(app, present: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {"carrier": "", "carrier_code": "", "mnc_length": None, "gid1": "", "gid2": "",
                           "gid1_bytes": [], "gid2_bytes": [], "test_sim": False}
    res = cli_get(app, "modem2_cli", "sim_get_carrier")
    if res is not None:
        out["carrier_code"] = res.get("carrier is") or ""
        out["carrier"] = carrier_name(out["carrier_code"]) if res.ok else ""
    if present:
        for which in ("gid1", "gid2"):
            g = cli_get(app, "modem2_cli", "sim_get_" + which)
            if g is not None and g.ok:
                b = parse_gid(g.text)
                out[which + "_bytes"] = b
                out[which] = "".join("%02X" % x for x in b)
        m = cli_get(app, "modem2_cli", "sim_get_mnc_length")
        if m is not None and m.ok:
            out["mnc_length"] = m.get_int("Length of MNC is")
    t = cli_get(app, "modem2_cli", "get_test_sim_status")
    if t is not None and t.ok:
        out["test_sim"] = bool(t.get_int("test_sim", 0))
    return out


def _status(app) -> Dict[str, Any]:
    d = sim_detail(app)
    d.update(sim_extras(app, d["present"]))
    ident = identity_raw(app)
    d["identity"] = {k: mask(v) for k, v in ident.items()}
    d["identity_set"] = {k: bool(v) for k, v in ident.items()}
    d["home_network"] = home_network(ident.get("imsi", ""), d.get("mnc_length"))
    return d


def sim_status(app) -> Dict[str, Any]:
    """Cached aggregate SIM status (3 s)."""
    return app.cache.get("sim:status", 3, lambda: _status(app))


def nv_read(app, efs_path: str, max_bytes: int = 16) -> Optional[bytes]:
    """Read a small modem EFS/NV file with `nwcli qmi_idl read_file` (argv-whitelisted, read only)."""
    local = "/dev/shm/m3ui-sim-%d-%d" % (os.getpid(), int(time.time() * 1000) % 100000000)
    try:
        res = app.cli.run("nwcli", "qmi_idl", args=["read_file", local, efs_path, str(max_bytes)], timeout=10)
    except ApiError:
        return None
    data = None
    if app.mock:
        store = getattr(app.cli, "store", None)
        if store is not None:
            data = store.state.get("vfs", {}).pop(local, None)
    else:
        try:
            with open(local, "rb") as f:
                data = f.read(max_bytes)
        except OSError:
            data = None
        finally:
            try:
                os.unlink(local)
            except OSError:
                pass
    if data is None or "success" not in (res.text or "").lower():
        return None
    return data


class _Throttle:
    """Carrier-unlock attempts are limited by the modem: one try per 30 s and 3 per hour from this UI."""

    def __init__(self, gap: float = 30.0, per_hour: int = 3):
        self.gap, self.per_hour, self.times, self.lock = gap, per_hour, [], threading.Lock()

    def check(self) -> None:
        now = time.time()
        with self.lock:
            self.times = [t for t in self.times if now - t < 3600]
            if self.times and now - self.times[-1] < self.gap:
                raise ApiError(429, "rate_limited", "Wait %d s before the next unlock attempt"
                               % int(self.gap - (now - self.times[-1]) + 1))
            if len(self.times) >= self.per_hour:
                raise ApiError(429, "rate_limited", "Too many unlock attempts this hour; try again later")
            self.times.append(now)


RECOVERY = [  # key, sim_mgr_cli setter, label in get_all_recovery_configs, lo, hi
    ("period_min", "set_recovery_period", "period", 30, 1440),
    ("modem_disconnect_timeout_min", "set_recovery_max_mdm_disconnect_timeout", "mdm_disconnect_timeout", 1, 15),
    ("max_sim_switches", "set_recovery_max_sim_switches", "max_sim_switch_cnt", 0, 10),
    ("max_dns_failures", "set_recovery_max_DNS_fail_cnt", "max_wan_dns_fail_cnt", 0, 10),
    ("max_modem_resets", "set_recovery_max_mdm_reset_cnt", "max_modem_reset_cnt", 1, 5),
    ("max_device_resets", "set_recovery_max_device_reset_cnt", "max_device_reset_cnt", 0, 5),
    ("max_system_resets", "set_recovery_max_system_reset_cnt", "system_reset_cnt", 0, 10),
]


def register(app):
    r = app.router
    throttle = _Throttle()

    def refresh():
        app.cache.invalidate("sim:")
        app.cache.invalidate("status")

    @r.route("GET", "/api/sim/status")
    def get_status(req):
        """SIM card state, PIN lock and retry counters, carrier, GID1/GID2, masked identifiers."""
        return sim_status(app)

    @r.route("GET", "/api/sim/identity/secret")
    def get_identity_secret(req):
        """Unmasked ICCID / IMSI / IMEI / phone number (audited)."""
        app.audit(req, "sim.identity.reveal")
        return identity_raw(app)

    # ------------------------------------------------------------------------- carrier lock
    def lock_status() -> Dict[str, Any]:
        cu = cli_get(app, "modem2_cli", "get_carrier_unlock")
        rest = rest_get(app, "SimCarrierLockStatus") or {}
        sl = cli_get(app, "modem2_cli", "get_sim_lock")
        good = cu is not None and cu.ok
        state = cu.get_int("State") if good else None
        block = cu.get_int("Carrier block") if good else None
        verify = cu.get_int("Verify retries") if good else None
        unblock = cu.get_int("Unblock retries") if good else None
        out: Dict[str, Any] = {
            "available": good or bool(rest), "state": state, "blocked": bool(block),
            "verify_retries": verify, "unblock_retries": unblock,
            "rest": {"enabled": rest.get("SimCarrierLockEnabled"), "unlock_counter": rest.get("SimCarrierUnlockCounter"),
                     "unblock_counter": rest.get("SimCarrierUnblockCounter")} if rest else None,
        }
        nv = app.cache.get("sim:nv60002", 60, lambda: nv_read(app, "/nvm/num/60002", 16))
        if nv is None:
            out["nv60002"] = {"available": False, "value": None, "hex": "", "unlocked": None}
        else:
            val = nv[0] if nv else 0
            out["nv60002"] = {"available": True, "value": val, "hex": nv.hex(), "unlocked": val == 0}
        pol = sl.get_int("unlock") if sl is not None and sl.ok else None
        out["sim_lock_policy"] = {"value": pol, "label": {0: "Default (no unlock file)", 1: "Unlocked",
                                                          2: "Mode 2 (vendor specific)"}.get(pol, "Unknown")}
        # carrier-managed SIM restriction policy (stock "SIM Restrictions" panel, hidden there); read only.
        # Labels follow the order of the stock texts (current carrier network / + international SIMs / none).
        rs = cli_get(app, "webui_cli", "get_sim_restriction_status")
        rv = rs.get_int("restriction_status") if rs is not None and rs.ok else None
        out["sim_restriction"] = {"value": rv, "label": {
            0: "Carrier network only", 1: "Carrier network and international SIMs", 2: "No restriction"}.get(
            rv, "Unknown" if rv is not None else "")}
        locked = bool(state) or bool(rest.get("SimCarrierLockEnabled")) or bool(out["nv60002"]["value"])
        out["carrier_locked"] = locked
        retries = verify if verify is not None else to_int(rest.get("SimCarrierUnlockCounter"))
        out["attempts_left"] = retries
        if not locked:
            out.update({"can_unlock": False, "reason": "The modem reports no carrier lock; no unlock code is needed."})
        elif block:
            out.update({"can_unlock": False, "reason": "The carrier lock is permanently blocked (too many wrong codes)."})
        elif retries is not None and retries <= 0:
            out.update({"can_unlock": False, "reason": "No unlock attempts are left."})
        else:
            out.update({"can_unlock": True, "reason": ""})
        return out

    @r.route("GET", "/api/sim/lock")
    def get_lock(req):
        """Carrier (network) lock state, attempts, NV 60002 flag (read only) and SIM lock policy."""
        return lock_status()

    # ---------------------------------------------------------------------------- PIN / PUK
    def guard(kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
        d = sim_detail(app)
        if not d["present"]:
            raise unavailable("No SIM card is inserted")
        p = d["pin"]
        ack = validate.boolean("last_attempt", data.get("last_attempt", False))
        if kind == "puk":
            n = p.get("puk_retries")
            if d["state"] == "perm_blocked" or n == 0:
                raise unavailable("The SIM is permanently blocked; ask the carrier for a replacement SIM")
            if (n is None or n <= 3) and not ack:
                raise ApiError(409, "last_attempts",
                               "Only %s PUK attempt(s) left. Every wrong PUK brings the SIM closer to a permanent block. "
                               "Tick the confirmation to try anyway." % ("an unknown number of" if n is None else n),
                               {"field": "puk", "retries": n, "need": "last_attempt"})
        else:
            n = p.get("retries")
            if n == 0 or d["state"] == "puk_locked":
                raise unavailable("No PIN attempts are left; unblock the SIM with the PUK")
            if (n is None or n <= 1) and not ack:
                raise ApiError(409, "last_attempts",
                               "This is the last PIN attempt%s. A wrong PIN blocks the SIM and the PUK will be needed. "
                               "Tick the confirmation to try anyway." % ("" if n is not None else " (counter unknown)"),
                               {"field": "pin", "retries": n, "need": "last_attempt"})
        return d

    def wrong_code(field: str, before: Dict[str, Any], kind: str, exc: ApiError):
        refresh()
        after = sim_detail(app)
        key = "puk_retries" if kind == "puk" else "retries"
        b, a = before["pin"].get(key), after["pin"].get(key)
        if a is not None and (b is None or a < b):
            raise field_error(field, "Wrong %s. %d attempt(s) left." % ("PUK" if kind == "puk" else "PIN", a))
        raise exc

    def pin_action(req, before, kind, field, cmd, answers, what, audit, details=None):
        try:
            cli_set(app, "modem2_cli", cmd, answers, what, timeout=20)
        except ApiError as e:
            if e.code == "vendor_error":
                app.audit(req, audit, dict(details or {}, ok=False))
                wrong_code(field, before, kind, e)
            raise
        app.audit(req, audit, dict(details or {}, ok=True))
        refresh()
        return sim_status(app)

    @r.route("POST", "/api/sim/pin/verify")
    def pin_verify(req):
        """Unlock a PIN-locked SIM (tier 3; a wrong PIN uses up an attempt)."""
        app.require_tier(req, 3)
        data = validate.only_keys(req.json, ["pin", "last_attempt"])
        pin = validate.text("pin", data.get("pin", ""), 4, 8, pattern=PIN_RE)
        before = guard("pin", data)
        if before["state"] != "pin_locked":
            raise ApiError(409, "conflict", "The SIM is not waiting for a PIN")
        return pin_action(req, before, "pin", "pin", "sim_unlock_pin", [pin], "Unlocking the SIM", "sim.pin.verify")

    @r.route("POST", "/api/sim/pin/unblock")
    def pin_unblock(req):
        """Unblock a PUK-locked SIM and set a new PIN (tier 3; wrong PUKs can block the SIM for good)."""
        app.require_tier(req, 3)
        data = validate.only_keys(req.json, ["puk", "new_pin", "last_attempt"])
        puk = validate.text("puk", data.get("puk", ""), 8, 8, pattern=PUK_RE)
        new_pin = validate.text("new_pin", data.get("new_pin", ""), 4, 8, pattern=PIN_RE)
        before = guard("puk", data)
        if before["state"] != "puk_locked":
            raise ApiError(409, "conflict", "The SIM is not blocked; the PUK is not needed")
        return pin_action(req, before, "puk", "puk", "sim_unlock_puk", [puk, new_pin], "Unblocking the SIM",
                          "sim.puk.unblock")

    @r.route("POST", "/api/sim/pin/lock")
    def pin_lock(req):
        """Turn the SIM PIN lock on or off (tier 3; needs the current PIN)."""
        app.require_tier(req, 3)
        data = validate.only_keys(req.json, ["enabled", "pin", "last_attempt"])
        enabled = validate.boolean("enabled", data.get("enabled"))
        pin = validate.text("pin", data.get("pin", ""), 4, 8, pattern=PIN_RE)
        before = guard("pin", data)
        if before["state"] not in ("ready", "unlocked"):
            raise ApiError(409, "conflict", "Unlock the SIM first")
        if before["pin"]["known"] and before["pin"]["lock_enabled"] == enabled:
            raise ApiError(409, "conflict", "The PIN lock is already %s" % ("on" if enabled else "off"))
        return pin_action(req, before, "pin", "pin", "sim_enable_pin", ["1" if enabled else "0", pin], "Changing the PIN lock",
                          "sim.pin.lock", {"enabled": enabled})

    @r.route("POST", "/api/sim/pin/change")
    def pin_change(req):
        """Change the SIM PIN (tier 3; the PIN lock must be on)."""
        app.require_tier(req, 3)
        data = validate.only_keys(req.json, ["pin", "new_pin", "last_attempt"])
        pin = validate.text("pin", data.get("pin", ""), 4, 8, pattern=PIN_RE)
        new_pin = validate.text("new_pin", data.get("new_pin", ""), 4, 8, pattern=PIN_RE)
        before = guard("pin", data)
        if before["state"] not in ("ready", "unlocked") or not before["pin"]["lock_enabled"]:
            raise ApiError(409, "conflict", "Turn the PIN lock on before changing the PIN")
        return pin_action(req, before, "pin", "pin", "sim_change_pin", [pin, new_pin], "Changing the PIN", "sim.pin.change")

    @r.route("POST", "/api/sim/carrier-unlock")
    def carrier_unlock(req):
        """Remove the carrier (network) lock with an unlock code (tier 3; limited attempts)."""
        app.require_tier(req, 3)
        data = validate.only_keys(req.json, ["code", "last_attempt"])
        code = validate.text("code", data.get("code", ""), 4, 32, pattern=UNLOCK_CODE_RE)
        ls = lock_status()
        if not ls["carrier_locked"]:
            raise ApiError(409, "conflict", "The modem reports no carrier lock; the code is not needed")
        if not ls["can_unlock"]:
            raise unavailable(ls["reason"])
        n = ls.get("attempts_left")
        if (n is None or n <= 1) and not validate.boolean("last_attempt", data.get("last_attempt", False)):
            raise ApiError(409, "last_attempts", "Only %s unlock attempt(s) left; after that the lock is permanent."
                           % ("an unknown number of" if n is None else n),
                           {"field": "code", "retries": n, "need": "last_attempt"})
        throttle.check()
        try:
            cli_set(app, "modem2_cli", "unlock_carrier", [code], "Carrier unlock", timeout=30)
        except ApiError as e:
            app.audit(req, "sim.carrier_unlock", {"ok": False})
            app.cache.invalidate("sim:")
            if e.code == "vendor_error":
                left = lock_status().get("attempts_left")
                raise field_error("code", "The unlock code was not accepted.%s"
                                  % ("" if left is None else " %d attempt(s) left." % left))
            raise
        app.audit(req, "sim.carrier_unlock", {"ok": True})
        app.cache.invalidate("sim:")
        return lock_status()

    # ---------------------------------------------------------------------- account (PCO)
    @r.route("GET", "/api/sim/account")
    def get_account(req):
        """Carrier account notice (PCO 2 restricted / 3 out of credit / 5 not active)."""
        res = cli_get(app, "modem2_cli", "get_pco_event")
        if res is None or not res.ok:
            return {"available": False, "pco": None, "pdn": None, "restricted": False, "message": ""}
        pco = res.get_int("pco_value", 0) or 0
        return {"available": True, "pco": pco, "pdn": res.get_int("pdn", 0), "restricted": pco in PCO_LABELS,
                "message": PCO_LABELS.get(pco, "")}

    @r.route("POST", "/api/sim/account/accept")
    def accept_account(req):
        """Allow limited data access so the account can be topped up or activated."""
        validate.only_keys(req.json, [])
        cli_set(app, "ccm2_cli", "accept_pco_event", [], "Allowing limited access")
        app.audit(req, "sim.pco.accept")
        app.cache.invalidate("status")
        return get_account(req)

    # ------------------------------------------------------------------------- SIM slots
    def multi_supported() -> bool:
        res = cli_get(app, "sim_mgr_cli", "get_is_multi_sim_supported")
        if res is not None and ok_of(res) and res.get_int("is_multi_sim_supported", 0):
            return True
        n = cli_get(app, "modem2_cli", "sim_mgr_get_num_sims")
        if n is not None and n.ok and (n.get_int("Num of Sims", 0) or 0) > 1:
            return True
        ms = rest_get(app, "MultiSIM/Enabled") or {}
        return bool(to_int(ms.get("Enabled"), 0))

    app.features.add("sim.multi", multi_supported)

    def mgr_flag(cmd: str, label: str) -> Optional[bool]:
        res = cli_get(app, "sim_mgr_cli", cmd)
        v = (res.get(label) or "") if res is not None and ok_of(res) else ""
        return None if not v else v.lower().startswith("enab")

    def slots() -> Dict[str, Any]:
        out: Dict[str, Any] = {"supported": multi_supported()}
        n = cli_get(app, "modem2_cli", "sim_mgr_get_num_sims")
        out["num_sims"] = n.get_int("Num of Sims") if n is not None and n.ok else None
        a = cli_get(app, "modem2_cli", "sim_mgr_get_active_slot")
        out["active_slot"] = a.get_int("Active Slot") if a is not None and a.ok else None
        info = cli_get(app, "modem2_cli", "sim_mgr_get_info")
        blocks = parse_slot_blocks(info.text) if info is not None and info.ok else []
        for b in blocks:
            for k in ("iccid", "imsi", "mdn"):
                b[k + "_masked"] = mask(b.pop(k))
        out["slots"] = blocks
        phy = cli_get(app, "modem2_cli", "uim_get_physical_slot_status")
        out["physical"] = parse_phy_slots(phy.text) if phy is not None and phy.ok else []
        sw = cli_get(app, "modem2_cli", "sim_mgr_sim_switch_status")
        m = re.search(r"SIM Switching Status:\s*(-?\d+)", sw.text) if sw is not None and sw.ok else None
        out["switch_status"] = ({0: "idle", 1: "in_progress", 2: "failed", 3: "success"}.get(int(m.group(1)), m.group(1))
                                if m else None)
        mode = cli_get(app, "sim_mgr_cli", "get_switch_mode")
        out["auto_switch"] = bool(mode.get_int("switch_mode", 0)) if mode is not None and ok_of(mode) else None
        prio = cli_get(app, "sim_mgr_cli", "get_priority_sim")
        out["priority_imsi_masked"] = mask(prio.get("priority sim imsi") or "") if prio is not None and ok_of(prio) else ""
        out["conn_monitor"] = mgr_flag("get_conn_monitor_mode", "conn mode")
        out["sim_monitor"] = mgr_flag("get_sim_monitor_mode", "sim mode")
        out["conn_recovery"] = mgr_flag("get_conn_recovery_mode", "conn recovery mode")
        cc = cli_get(app, "sim_mgr_cli", "get_conn_configs")
        out["conn_interval"] = cc.get_int("connection interval") if cc is not None and ok_of(cc) else None
        out["conn_retries"] = cc.get_int("connection retriies") if cc is not None and ok_of(cc) else None
        sc = cli_get(app, "sim_mgr_cli", "get_sim_configs")
        out["sim_interval"] = sc.get_int("sim interval") if sc is not None and ok_of(sc) else None
        daemon = cli_get(app, "sim_mgr_cli", "is_running")
        out["manager_running"] = bool(daemon is not None and ok_of(daemon))
        out["previous_slot"] = app.state.last_snapshot("sim_slot")
        return out

    @r.route("GET", "/api/sim/slots")
    def get_slots(req):
        """SIM slots (dual-SIM units): slot states, active slot, switching mode and monitors."""
        return slots()

    def need_multi():
        if not multi_supported():
            raise unavailable("This unit has a single SIM slot")

    @r.route("POST", "/api/sim/slots/switch")
    def switch_slot(req):
        """Switch the active SIM slot (tier 2; the data connection drops while the modem switches)."""
        app.require_tier(req, 2)
        data = validate.only_keys(req.json, ["slot"])
        slot = validate.integer("slot", data.get("slot"), 1, 2)
        need_multi()
        cur = slots().get("active_slot")
        if cur == slot:
            raise ApiError(409, "conflict", "Slot %d is already active" % slot)
        app.state.snapshot("sim_slot", cur)
        cli_set(app, "modem2_cli", "sim_mgr_switch_slot", [str(slot)], "Switching SIM slot", timeout=60)
        app.audit(req, "sim.slot.switch", {"from": cur, "to": slot})
        refresh()
        return slots()

    @r.route("PUT", "/api/sim/slots/settings")
    def put_slot_settings(req):
        """Automatic SIM switching (tier 2), connection/SIM monitors, connection recovery and check intervals."""
        data = validate.only_keys(req.json, ["auto_switch", "conn_monitor", "sim_monitor", "conn_recovery",
                                             "conn_interval", "conn_retries", "sim_interval"])
        if "auto_switch" in data:
            app.require_tier(req, 2)
        vals: Dict[str, Any] = {}
        for key in ("auto_switch", "conn_monitor", "sim_monitor", "conn_recovery"):
            if key in data:
                vals[key] = validate.boolean(key, data[key])
        if "conn_interval" in data:
            vals["conn_interval"] = validate.integer("conn_interval", data["conn_interval"], 5, 3600)
        if "conn_retries" in data:
            vals["conn_retries"] = validate.integer("conn_retries", data["conn_retries"], 1, 20)
        if "sim_interval" in data:
            vals["sim_interval"] = validate.integer("sim_interval", data["sim_interval"], 60, 86400)
        need_multi()
        if "auto_switch" in vals:
            cli_set(app, "sim_mgr_cli", "set_switch_mode", ["1" if vals["auto_switch"] else "0"], "Setting SIM switching")
        for key, cmd in (("conn_monitor", "set_conn_monitor_mode"), ("sim_monitor", "set_sim_monitor_mode"),
                         ("conn_recovery", "set_conn_recovery_mode")):
            if key in vals:
                cli_set(app, "sim_mgr_cli", cmd, ["1" if vals[key] else "0"], "Setting " + key.replace("_", " "))
        if "conn_interval" in vals or "conn_retries" in vals:
            cur = slots()
            iv = vals.get("conn_interval", cur.get("conn_interval") or 30)
            rt = vals.get("conn_retries", cur.get("conn_retries") or 5)
            cli_set(app, "sim_mgr_cli", "set_conn_configs", [str(iv), str(rt)], "Setting connection checks")
        if "sim_interval" in vals:
            cli_set(app, "sim_mgr_cli", "set_sim_configs", [str(vals["sim_interval"])], "Setting the SIM check interval")
        app.audit(req, "sim.slots.settings", {"keys": sorted(vals)})
        return slots()

    @r.route("PUT", "/api/sim/slots/priority")
    def put_priority(req):
        """Priority of the SIM in a slot (used by automatic switching)."""
        data = validate.only_keys(req.json, ["slot", "priority"])
        slot = validate.integer("slot", data.get("slot"), 1, 2)
        prio = validate.integer("priority", data.get("priority"), 0, 10)
        need_multi()
        res = cli_get(app, "modem2_cli", "sim_mgr_get_info")
        blocks = parse_slot_blocks(res.text) if res is not None and res.ok else []
        imsi = blocks[slot - 1]["imsi"] if len(blocks) >= slot else ""
        if not re.match(IMSI_RE, imsi or ""):
            raise unavailable("No SIM with a readable IMSI in slot %d" % slot)
        cli_set(app, "modem2_cli", "sim_mgr_set_imsi_priority", [imsi, str(prio)], "Setting the SIM priority")
        app.audit(req, "sim.slot.priority", {"slot": slot, "priority": prio})
        return slots()

    def recovery() -> Dict[str, Any]:
        res = cli_get(app, "sim_mgr_cli", "get_all_recovery_configs")
        ok = res is not None and ok_of(res)
        out: Dict[str, Any] = {"available": ok}
        for key, _cmd, label, _lo, _hi in RECOVERY:
            out[key] = res.get_int(label) if ok else None
        out["limits"] = {key: [lo, hi] for key, _c, _l, lo, hi in RECOVERY}
        return out

    @r.route("GET", "/api/sim/slots/recovery")
    def get_recovery(req):
        """Connection recovery limits of the SIM manager (modem/device resets, SIM switches)."""
        return recovery()

    @r.route("PUT", "/api/sim/slots/recovery")
    def put_recovery(req):
        """Change connection recovery limits."""
        data = validate.only_keys(req.json, [k for k, _c, _l, _lo, _hi in RECOVERY])
        vals = {}
        for key, cmd, _label, lo, hi in RECOVERY:
            if key in data:
                vals[key] = (cmd, validate.integer(key, data[key], lo, hi))
        need_multi()
        for key, (cmd, v) in vals.items():
            cli_set(app, "sim_mgr_cli", cmd, [str(v)], "Setting " + key.replace("_", " "))
        app.audit(req, "sim.recovery.update", {"keys": sorted(vals)})
        return recovery()
