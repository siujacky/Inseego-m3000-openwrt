"""Mock for the SIM / APN / SMS / data-usage area: a test SIM with PIN lock, carrier-lock state, multi-APN
profiles, modem profiles, an SMS inbox/sent box with address book, and a growing data-usage meter.

State is module level (`STATE`) because both FakeCli handlers (sms_cli, modem2_cli …) and FakeRest handlers
(SMSList/Inbox, APNProfiles/3GPP/active …) must see the same messages / profiles.  Tests call
`reset_state(sim_present=…)` in setUp.  With `sim_present=False` (or M3UI_MOCK_NO_SIM=1 for the mock server) the
SIM-dependent reads return the real captured fixture text (the reference unit has no SIM), so the same parsers run
on the real shapes.
Output shapes follow the captures in fixtures/cli/ and the printf formats found in the binaries.
"""
import copy
import json
import os
import threading
import time

from ..adapters.mock import cli_handler, rest_handler

LOCK = threading.RLock()
STATE = {}
M2 = "MDM_MAIN"
SIM_ENUM = {3: "SIM_READY", 4: "SIM_UNLOCKED", 5: "SIM_PIN_LOCKED", 6: "SIM_PUK_LOCKED", 7: "SIM_ERROR"}
PRESETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "apn_presets.json")
LIMIT_TEXT = ["0 GB", "20 MB", "25 MB", "100 MB", "250 MB", "500 MB", "1 GB", "2 GB", "3 GB", "5 GB", "8 GB", "10 GB",
              "12 GB", "15 GB", "20 GB"]


def _profile(apn, name="", pdp=3, auth=0, user="", pw="", manual_dns=1):
    return {"apn": apn, "user": user, "pw": pw, "name": name, "locked": 0, "auth": auth, "manual_dns": manual_dns,
            "dns1": "0.0.0.0", "dns2": "0.0.0.0", "pdp": pdp, "apn_disabled": 0, "timeout": 0, "v4": "0.0.0.0",
            "v6": "::", "data_mode": 0, "pdp2": 0}


def reset_state(sim_present=True, multi_sim=False, carrier_locked=False):
    now = int(time.time())
    imsi, iccid = "001010123456789", "8900101234567890123"
    with LOCK:
        STATE.clear()
        STATE.update({
            "sim": {"present": sim_present, "pin": "1234", "puk": "12345678", "pin_enabled": False, "verified": False,
                    "pin_retries": 3, "puk_retries": 10, "imsi": imsi, "iccid": iccid, "gid1": [0xFF], "gid2": [],
                    "mnc_len": 2, "carrier": "SIM_CARRIER_UNKNOWN(0)"},
            "carrier": {"state": 1 if carrier_locked else 0, "block": 0, "verify": 10, "unblock": 0, "code": "13572468",
                        "nv": b"\x01" if carrier_locked else b"\x00"},
            "multi": multi_sim, "active_slot": 1, "switch_mode": 0, "switch_status": 0, "prio": {},
            "mgr": {"conn_monitor": 1, "sim_monitor": 0, "conn_recovery": 1, "conn_interval": 30, "conn_retries": 5,
                    "sim_interval": 1800, "period": 120, "mdm_disconnect_timeout": 2, "max_sim_switch_cnt": 6,
                    "max_wan_dns_fail_cnt": 3, "max_modem_reset_cnt": 3, "max_device_reset_cnt": 1, "system_reset_cnt": 3},
            "apn_active": {"APN": "internet.mock", "Name": "", "PDPAuthenticationType": "None", "Password": "", "UserName": ""},
            "multi_apn": [{"id": 1, "name": "Mobile Broadband", "imsi": imsi, "iccid": iccid, "active": 1, "default": 1,
                           "apn": "internet.mock", "username": "", "password": "", "auth": 0, "pdp": 3}],
            "next_apn_id": 2, "failback": 0,
            "profiles": {1: _profile("ims", "IMS"), 2: _profile("admin.mock", "DM", pdp=0),
                         3: _profile("internet.mock", "Internet"), 4: _profile("apps.mock", "Apps", pdp=0),
                         5: _profile("", "")},
            "act_tech": 3,
            "pri": {0: _profile("internet.mock"), 1: _profile("internet.mock"), 2: _profile("")},
            "cust": {0: _profile("ims"), 1: _profile(""), 2: _profile("")},
            "pdn": {i: {"pdp": 3, "clat": 0, "max": 1, "wait": 0, "conn": 0} for i in range(1, 5)},
            "sms": {"inbox": [
                {"id": 1, "read": True, "address": "+15550100123", "body": "Welcome! Your data plan is active.", "t": now - 86400 * 2},
                {"id": 2, "read": False, "address": "5550100", "body": "You have used 80% of your monthly data.", "t": now - 3600},
            ], "sent": [
                {"id": 3, "read": True, "address": "+15550100777", "body": "On my way", "t": now - 7200},
            ], "outbox": []},
            "next_sms_id": 4,
            "contacts": [{"id": 0, "name": "Support", "address": "5550100"}],
            "usage": {"cycle_day": 1, "limit": 11, "threshold": 80, "t0": time.time(), "base_rx": 3_400_000_000,
                      "base_tx": 420_000_000},
        })


# M3UI_MOCK_NO_SIM=1 starts the mock without a SIM (the state of the reference unit) for browser checks.
reset_state(sim_present=os.environ.get("M3UI_MOCK_NO_SIM", "") not in ("1", "true", "yes"))


def state():
    return STATE


def _rc(fn, rc=0, msg="success.", module=M2):
    return "%s returned %d (%s: %s)\n" % (fn, rc, module, msg)


def _sim():
    return STATE["sim"]


def _fixture(store, cli, cmd):
    t = store.text(cli, cmd)
    return t if t is not None else _rc("cmd_" + cmd, 1, "general error.")


# ----------------------------------------------------------------------------- SIM status / PIN
def _sim_code():
    s = _sim()
    if s["puk_retries"] <= 0:
        return 7
    if s["pin_retries"] <= 0:
        return 6
    if s["pin_enabled"] and not s["verified"]:
        return 5
    if s["pin_enabled"]:
        return 4
    return 3


def _pin_code():
    s = _sim()
    if s["puk_retries"] <= 0:
        return 5
    if s["pin_retries"] <= 0:
        return 4
    if s["pin_enabled"]:
        return 2 if s["verified"] else 1
    return 3


@cli_handler("modem2_cli", "sim_get_status")
def _sim_get_status(store, answers, args):
    with LOCK:
        if not _sim()["present"]:
            return _fixture(store, "modem2_cli", "sim_get_status")
        c = _sim_code()
        return (_rc("cmd_sim_get_status") + "status is:[%d] (%s)\nretries is:[%d]\nunblocks is:[%d]\n"
                % (c, SIM_ENUM.get(c, "SIM_ERROR"), _sim()["pin_retries"], _sim()["puk_retries"]))


@cli_handler("modem2_cli", "sim_pin_get_status")
def _sim_pin_get_status(store, answers, args):
    with LOCK:
        if not _sim()["present"]:
            return _fixture(store, "modem2_cli", "sim_pin_get_status")
        return (_rc("cmd_sim_pin_get_status") + "status is:[%d]\nretries is:[%d]\nunlocks is:[%d]\n"
                % (_pin_code(), _sim()["pin_retries"], _sim()["puk_retries"]))


def _simple(cmd, line_fmt, key):
    @cli_handler("modem2_cli", cmd)
    def _h(store, answers, args):
        with LOCK:
            if not _sim()["present"]:
                return _fixture(store, "modem2_cli", cmd)
            return _rc("cmd_" + cmd) + line_fmt % key()
    return _h


_simple("get_imsi", "imsi : %s\n", lambda: _sim()["imsi"])
_simple("sim_get_iccid", "iccid : %s\n", lambda: _sim()["iccid"])
_simple("sim_get_carrier", "carrier is:[%s]\n", lambda: _sim()["carrier"])
_simple("sim_get_mnc_length", "Length of MNC is:[%d]\n", lambda: _sim()["mnc_len"])


def _gid(cmd, key):
    @cli_handler("modem2_cli", cmd)
    def _h(store, answers, args):
        with LOCK:
            if not _sim()["present"]:
                return _fixture(store, "modem2_cli", cmd)
            g = _sim()[key]
            return _rc("cmd_" + cmd) + "gid_length : %d\n" % len(g) + "".join(
                "%s [%d] : 0x%2x\n" % (key, i + 1, b) for i, b in enumerate(g))
    return _h


_gid("sim_get_gid1", "gid1")
_gid("sim_get_gid2", "gid2")


def _pin_fail(fn):
    s = _sim()
    s["pin_retries"] = max(0, s["pin_retries"] - 1)
    return _rc(fn, 1001, "authentication failed.")


@cli_handler("modem2_cli", "sim_unlock_pin")
def _unlock_pin(store, answers, args):
    with LOCK:
        s, head = _sim(), "What is SIM PIN:  "
        if not s["present"] or _pin_code() != 1:
            return head + _rc("cmd_sim_unlock_pin", 1, "general error.")
        if answers[0] != s["pin"]:
            return head + _pin_fail("cmd_sim_unlock_pin")
        s.update(verified=True, pin_retries=3)
        return head + _rc("cmd_sim_unlock_pin")


@cli_handler("modem2_cli", "sim_unlock_puk")
def _unlock_puk(store, answers, args):
    with LOCK:
        s, head = _sim(), "What is SIM PUK:  What is SIM PIN:  "
        if not s["present"] or _pin_code() != 4:
            return head + _rc("cmd_sim_unlock_puk", 1, "general error.")
        if answers[0] != s["puk"]:
            s["puk_retries"] = max(0, s["puk_retries"] - 1)
            return head + _rc("cmd_sim_unlock_puk", 1001, "authentication failed.")
        s.update(pin=answers[1], pin_retries=3, puk_retries=10, verified=True, pin_enabled=True)
        return head + _rc("cmd_sim_unlock_puk")


@cli_handler("modem2_cli", "sim_enable_pin")
def _enable_pin(store, answers, args):
    with LOCK:
        s, head = _sim(), "enable sim pin lock [1/0]: What is SIM PIN:  "
        if not s["present"] or _pin_code() not in (2, 3):
            return head + _rc("cmd_sim_enable_pin", 1, "general error.")
        if answers[1] != s["pin"]:
            return head + _pin_fail("cmd_sim_enable_pin")
        s.update(pin_enabled=answers[0] == "1", verified=True, pin_retries=3)
        return head + _rc("cmd_sim_enable_pin")


@cli_handler("modem2_cli", "sim_change_pin")
def _change_pin(store, answers, args):
    with LOCK:
        s, head = _sim(), "What is old SIM PIN:  What is new SIM PIN:  "
        if not s["present"] or _pin_code() != 2:
            return head + _rc("cmd_sim_change_pin", 1, "general error.")
        if answers[0] != s["pin"]:
            return head + _pin_fail("cmd_sim_change_pin")
        s.update(pin=answers[1], pin_retries=3)
        return head + _rc("cmd_sim_change_pin")


@rest_handler("GET", "SimLockStatus")
def _rest_lock(rest, body):
    with LOCK:
        s = _sim()
        if not s["present"]:
            return copy.deepcopy(rest.data.get("SimLockStatus", {}))
        return {"SimPinLockCounter": s["pin_retries"], "SimPinLockEnabled": s["pin_enabled"],
                "SimPukLockCounter": s["puk_retries"]}


# ------------------------------------------------------------------------------- carrier lock
@cli_handler("modem2_cli", "get_carrier_unlock")
def _get_carrier_unlock(store, answers, args):
    with LOCK:
        c = STATE["carrier"]
        _sync_efs(store)
        return (_rc("cmd_get_carrier_unlock") + "\nState:[%d]\nCarrier block:[%d]\nVerify retries:[%d]\nUnblock retries:[%d]\n"
                % (c["state"], c["block"], c["verify"], c["unblock"]))


@cli_handler("modem2_cli", "unlock_carrier")
def _unlock_carrier(store, answers, args):
    with LOCK:
        c, head = STATE["carrier"], "\npin:\n"
        if c["block"]:
            return head + _rc("cmd_unlock_carrier_lock", 1, "general error.")
        if answers[0] != c["code"]:
            c["verify"] = max(0, c["verify"] - 1)
            if c["verify"] == 0:
                c["block"] = 1
            return head + _rc("cmd_unlock_carrier_lock", 1, "general error.")
        c.update(state=0, nv=b"\x00")
        _sync_efs(store)
        return head + _rc("cmd_unlock_carrier_lock")


@rest_handler("GET", "SimCarrierLockStatus")
def _rest_carrier(rest, body):
    with LOCK:
        c = STATE["carrier"]
        return {"SimCarrierLockEnabled": bool(c["state"]), "SimCarrierUnblockCounter": c["unblock"],
                "SimCarrierUnlockCounter": c["verify"]}


def _sync_efs(store):
    """NV 60002 is read through the shared `nwcli qmi_idl read_file` mock in mock_handlers/tools.py (it serves
    store.state["efs"] overrides and leaves the bytes in store.state["vfs"]); keep it in line with the lock state.
    The PCO event / accept mocks live there too (store.state["pco"] = (value, pdn))."""
    store.state.setdefault("efs", {})["/nvm/num/60002"] = STATE["carrier"]["nv"]


# ---------------------------------------------------------------------------------- SIM slots
@cli_handler("sim_mgr_cli", "get_is_multi_sim_supported")
def _multi(store, answers, args):
    with LOCK:
        m = 1 if STATE["multi"] else 0
    return ("\n------------------------\n--[get] is device supports multi sim features--\n"
            "cmd_get_is_multi_sim_supported returned 0 (SIM_MGR: success.)\nis_multi_sim_supported:[%d][%s]\n\n"
            "------------------------\n" % (m, "multi sim" if m else "single sim"))


@cli_handler("modem2_cli", "sim_mgr_get_num_sims")
def _num_sims(store, answers, args):
    with LOCK:
        if not STATE["multi"]:
            return _fixture(store, "modem2_cli", "sim_mgr_get_num_sims")
        return _rc("cmd_sim_mgr_get_num_sims") + "Num of Sims:[2]\n"


@cli_handler("modem2_cli", "sim_mgr_get_active_slot")
def _active_slot(store, answers, args):
    with LOCK:
        return _rc("cmd_sim_mgr_get_active_slot") + "Active Slot: [%d]\n\n" % STATE["active_slot"]


def _slot_block(slot):
    active = STATE["active_slot"] == slot
    s = _sim()
    imsi = s["imsi"] if slot == 1 else "001010987654321"
    iccid = s["iccid"] if slot == 1 else "8900109876543210987"
    return ("[slot-0]\n\ncard_state:2[PRESENT]\n sim_status:[%d]:[%s]\nsim_state:[3]:[SIM_READY]\n"
            "sim_carrier:[0]:[SIM_CARRIER_INFO_NOT_AVAIL]\niccid:[%s]\nimsi:[%s]\nmdn:[]\noper_name:[%s]\n"
            "wwan_state:[%d][%s]\n\n" % (1 if active else 0, "ACTIVE" if active else "INACTIVE", iccid, imsi,
                                       "Mock Mobile" if active else "", 5 if active else 3,
                                       "MODEM2_WWAN_STATE_CONNECTED" if active else "MODEM2_WWAN_STATE_IDLE"))


@cli_handler("modem2_cli", "sim_mgr_get_info")
def _slot_info(store, answers, args):
    with LOCK:
        if not STATE["multi"]:
            return _fixture(store, "modem2_cli", "sim_mgr_get_info")
        return _rc("cmd_sim_mgr_get_info") + "Successful\n" + _slot_block(1) + _slot_block(2)


@cli_handler("modem2_cli", "sim_mgr_switch_slot")
def _switch_slot(store, answers, args):
    with LOCK:
        slot = int((args or answers or ["0"])[0])
        if not STATE["multi"] or slot not in (1, 2):
            return _rc("cmd_sim_mgr_switch_slot", 1, "general error.") + "Failed\n"
        STATE["active_slot"] = slot
        STATE["switch_status"] = 3
        return _rc("cmd_sim_mgr_switch_slot") + "Slot Switch Successful\n"


@cli_handler("modem2_cli", "sim_mgr_sim_switch_status")
def _switch_status(store, answers, args):
    with LOCK:
        return _rc("cmd_sim_mgr_sim_switch_status") + "SIM Switching Status: %d\n" % STATE["switch_status"]


@cli_handler("modem2_cli", "sim_mgr_set_imsi_priority")
def _set_prio(store, answers, args):
    with LOCK:
        vals = args or answers
        STATE["prio"][vals[0]] = int(vals[1])
        return "\nEnter the imsi: \nEnter priority for this imsi\n" + _rc("cmd_sim_mgr_set_imsi_priority") + "Success: \n\n"


def _mgr_get(cmd, title, line):
    @cli_handler("sim_mgr_cli", cmd)
    def _h(store, answers, args):
        with LOCK:
            return line(STATE["mgr"]) if title is None else (
                "\n------------------------\n%s\n%s------------------------\n" % (title, line(STATE["mgr"])))
    return _h


def _mode(v):
    return "Enabled" if v else "Disabled"


_mgr_get("get_switch_mode", "--[get] sim switch mode--",
         lambda m: "cmd_get_switch_mode returned 0 (SIM_MGR: success.)\nswitch_mode:[%d][%s]\n\n"
                   % (STATE["switch_mode"], "Automatic" if STATE["switch_mode"] else "Manual"))
_mgr_get("get_conn_monitor_mode", "--[get] connection monitor--",
         lambda m: "cmd_get_connection_monitor_mode returned 0 (SIM_MGR: success.)\n\nconn mode: [%s]\n" % _mode(m["conn_monitor"]))
_mgr_get("get_sim_monitor_mode", "--[get] sim monitor--",
         lambda m: "cmd_get_sim_monitor_mode returned 0 (SIM_MGR: success.)\n\nsim mode: [%s]\n" % _mode(m["sim_monitor"]))
_mgr_get("get_conn_recovery_mode", "--[recovery] [get] connection recovery mode--",
         lambda m: "cmd_get_connection_recovery_mode: [recovery] returned 0 (SIM_MGR: success.)\n\n"
                   "[recovery] conn recovery mode: [%s]\n" % _mode(m["conn_recovery"]))
_mgr_get("get_conn_configs", "--[get] connection configs--",
         lambda m: "cmd_get_conn_configs returned 0 (SIM_MGR: success.)\nconnection interval:[%d] connection retriies: [%d]\n\n\n"
                   % (m["conn_interval"], m["conn_retries"]))
_mgr_get("get_sim_configs", "--[get] sim configs--",
         lambda m: "cmd_get_sim_configs returned 0 (SIM_MGR: success.)\nsim interval:[%d] \n" % m["sim_interval"])
_mgr_get("get_all_recovery_configs", "--[recovery] [get] connection recovery all configs--",
         lambda m: "cmd_get_recovery_all_configs: [recovery] returned 0 (SIM_MGR: success.)\n"
                   "[recovery] period\t\t\t: [%d]\n[recovery] mdm_disconnect_timeout\t: [%d]\n"
                   "[recovery] max_sim_switch_cnt\t\t: [%d]\n[recovery] max_wan_dns_fail_cnt\t\t: [%d]\n"
                   "[recovery] max_modem_reset_cnt\t\t: [%d]\n[recovery] max_device_reset_cnt\t\t: [%d]\n"
                   "[recovery] system_reset_cnt\t\t: [%d]\n\n"
                   % (m["period"], m["mdm_disconnect_timeout"], m["max_sim_switch_cnt"], m["max_wan_dns_fail_cnt"],
                      m["max_modem_reset_cnt"], m["max_device_reset_cnt"], m["system_reset_cnt"]))
_mgr_get("get_priority_sim", None,
         lambda m: "cmd_get_priority_sim returned 0 (SIM_MGR: success.)\npriority sim imsi:[%s]\n"
                   % (_sim()["imsi"] if STATE["multi"] else ""))


def _mgr_set(cmd, key, prompt, fn_name, recovery=False):
    @cli_handler("sim_mgr_cli", cmd)
    def _h(store, answers, args):
        with LOCK:
            vals = [int(a) for a in answers]
            if key == "switch_mode":
                STATE["switch_mode"] = vals[0]
            elif isinstance(key, tuple):
                for k, v in zip(key, vals):
                    STATE["mgr"][k] = v
            else:
                STATE["mgr"][key] = vals[0]
        line = "%s: [recovery] returned 0 (SIM_MGR: success.)\n" if recovery else "%s returned 0 (SIM_MGR: success.)\n"
        return "\n------------------------\n" + prompt + "\r [%d]\n" % vals[0] + line % fn_name + "\n------------------------\n"
    return _h


_mgr_set("set_switch_mode", "switch_mode", "\nswitch mode:", "cmd_set_switch_mode")
_mgr_set("set_conn_monitor_mode", "conn_monitor", "\nconn mode:", "cmd_set_connection_monitor_mode")
_mgr_set("set_sim_monitor_mode", "sim_monitor", "\nsim mode:", "cmd_set_sim_monitor_mode")
_mgr_set("set_conn_recovery_mode", "conn_recovery", "\n[recovery] conn recovery mode:", "cmd_set_connection_recovery_mode", True)
_mgr_set("set_conn_configs", ("conn_interval", "conn_retries"), "connection interval:connection retriies:", "cmd_set_conn_configs")
_mgr_set("set_sim_configs", "sim_interval", "sim interval:", "cmd_set_sim_configs")
_mgr_set("set_recovery_period", "period", "\n[recovery] conn recovery period:", "cmd_set_recovery_period", True)
_mgr_set("set_recovery_max_sim_switches", "max_sim_switch_cnt", "\n[recovery] conn recovery max sim switch cnt:",
         "cmd_set_recovery_max_sim_switches", True)
_mgr_set("set_recovery_max_DNS_fail_cnt", "max_wan_dns_fail_cnt", "\n[recovery] conn recovery max DNS fail cnt:",
         "cmd_set_recovery_max_DNS_fail_cnt", True)
_mgr_set("set_recovery_max_mdm_disconnect_timeout", "mdm_disconnect_timeout",
         "\n[recovery] conn recovery max mdm disconnect timeout:", "cmd_set_recovery_max_mdm_disconnect_timeout", True)
_mgr_set("set_recovery_max_mdm_reset_cnt", "max_modem_reset_cnt", "\n[recovery] conn recovery max mdm reset cnt:",
         "cmd_set_recovery_max_mdm_reset_cnt", True)
_mgr_set("set_recovery_max_device_reset_cnt", "max_device_reset_cnt", "\n[recovery] conn recovery max device reset cnt:",
         "cmd_set_recovery_max_device_reset_cnt", True)
_mgr_set("set_recovery_max_system_reset_cnt", "system_reset_cnt", "\n[recovery] conn recovery max system_reset_cnt:",
         "cmd_set_recovery_max_system_reset_cnt", True)


# ---------------------------------------------------------------------------------- APN (REST)
@rest_handler("GET", "APNProfiles/3GPP/active")
def _rest_apn_get(rest, body):
    with LOCK:
        return copy.deepcopy(STATE["apn_active"])


@rest_handler("PUT", "APNProfiles/3GPP/active")
def _rest_apn_put(rest, body):
    with LOCK:
        for k in ("APN", "PDPAuthenticationType", "UserName", "Password", "Name"):
            if isinstance(body, dict) and k in body:
                STATE["apn_active"][k] = body[k]
    return None


# ------------------------------------------------------------------------------ multi-APN (CLI)
def _apn_lines(p, i=None):
    pre = "profile[%d]." % i if i is not None else "profile."
    out = "\n%sid:[%d]\n" % (pre, p["id"])
    for k in ("name", "imsi", "iccid"):
        out += "%s%s:[%s]\n" % (pre, k, p[k])
    for k in ("active", "default"):
        out += "%s%s:[%d]\n" % (pre, k, p[k])
    for k in ("apn", "username", "password"):
        out += "%s%s:[%s]\n" % (pre, k, p[k])
    for k in ("auth", "pdp"):
        out += "%s%s:[%d]\n" % (pre, k, p[k])
    return out


def _apply_active(p):
    STATE["apn_active"].update({"APN": p["apn"], "UserName": p["username"], "Password": p["password"],
                                "PDPAuthenticationType": {0: "None", 1: "PAP", 2: "CHAP", 3: "CHAP"}.get(p["auth"], "None")})


@cli_handler("modem2_cli", "get_multi_apn_list")
def _apn_list(store, answers, args):
    with LOCK:
        rows = STATE["multi_apn"] if _sim()["present"] else []
        return ("Enter a filter? [0/1]: " + _rc("cmd_multi_apn_get_list") + "count : %d\n" % len(rows)
                + "".join(_apn_lines(p, i) for i, p in enumerate(rows)))


def _apn_from_answers(a, pid):
    return {"id": pid, "name": a[0], "imsi": a[1], "iccid": a[2], "active": int(a[3] or 0), "default": int(a[4] or 0),
            "apn": a[5], "username": a[6], "password": a[7], "auth": int(a[8] or 0), "pdp": int(a[9] or 0)}


PROMPTS = ("Enter Name: Enter IMSI: Enter ICCID: Enter Active [0 or 1]: Enter Default [0 or 1]: Enter APN: "
           "Enter username: Enter password: Enter Auth [0 none, 1 pap, 2 chap, 3 both]: Enter PDP [0 ipv4, 2 ipv6, 3 ipv4v6]: ")


@cli_handler("modem2_cli", "multi_apn_add_profile")
def _apn_add(store, answers, args):
    with LOCK:
        p = _apn_from_answers(answers, STATE["next_apn_id"])
        STATE["next_apn_id"] += 1
        if p["active"]:
            for q in STATE["multi_apn"]:
                q["active"] = 0
            _apply_active(p)
        STATE["multi_apn"].append(p)
        return PROMPTS + _rc("cmd_multi_apn_add_profile")


@cli_handler("modem2_cli", "multi_apn_update_profile")
def _apn_update(store, answers, args):
    with LOCK:
        pid = int(answers[0])
        for i, q in enumerate(STATE["multi_apn"]):
            if q["id"] == pid:
                p = _apn_from_answers(answers[1:], pid)
                if p["active"]:
                    for other in STATE["multi_apn"]:
                        other["active"] = 0
                    _apply_active(p)
                STATE["multi_apn"][i] = p
                return "Enter index: " + PROMPTS + _rc("cmd_multi_apn_update_profile")
        return "Enter index: " + PROMPTS + _rc("cmd_multi_apn_update_profile", 2,
                                               "one or more bad parameters passed into a function.")


@cli_handler("modem2_cli", "multi_apn_delete_profile")
def _apn_delete(store, answers, args):
    with LOCK:
        pid = int(answers[0])
        before = len(STATE["multi_apn"])
        STATE["multi_apn"] = [q for q in STATE["multi_apn"] if q["id"] != pid]
        if len(STATE["multi_apn"]) == before:
            return "Enter profile id: " + _rc("cmd_multi_apn_delete_profile", 2, "one or more bad parameters passed into a function.")
        if not any(q["active"] for q in STATE["multi_apn"]):
            for q in STATE["multi_apn"]:
                if q["default"]:
                    q["active"] = 1
                    _apply_active(q)
        return "Enter profile id: " + _rc("cmd_multi_apn_delete_profile")


@cli_handler("modem2_cli", "multi_apn_restore_default_profile")
def _apn_restore(store, answers, args):
    with LOCK:
        for q in STATE["multi_apn"]:
            if q["id"] == int(answers[0]) and q["default"]:
                q.update(name="Mobile Broadband", apn="internet.mock", username="", password="", auth=0, pdp=3)
                if q["active"]:
                    _apply_active(q)
                return "Enter profile id: " + _rc("cmd_multi_apn_restore_default_profile")
        return "Enter profile id: " + _rc("cmd_multi_apn_restore_default_profile", 2,
                                          "one or more bad parameters passed into a function.")


@cli_handler("modem2_cli", "multi_apn_get_default_profile")
def _apn_default(store, answers, args):
    with LOCK:
        imsi = (answers or [""])[0]
        for q in STATE["multi_apn"]:
            if q["default"] and q["imsi"] == imsi and _sim()["present"]:
                return "Enter imsi: " + _rc("cmd_multi_apn_get_default_profile") + _apn_lines(q)
        return "Enter imsi: " + _rc("cmd_multi_apn_get_default_profile", 1011, "APN not found in Auto APN Database.")


@cli_handler("modem2_cli", "get_apn_failback_enabled")
def _fb_get(store, answers, args):
    with LOCK:
        return _rc("cmd_get_apn_failback_enabled") + "enabled:[%d]\n" % STATE["failback"]


@cli_handler("modem2_cli", "set_apn_failback_enabled")
def _fb_set(store, answers, args):
    with LOCK:
        STATE["failback"] = int((args or answers)[0])
        return _rc("cmd_set_apn_failback_enabled")


@cli_handler("modem2_cli", "failback_switch_apn")
def _fb_switch(store, answers, args):
    return _rc("cmd_failback_apn_switch")


@cli_handler("modem2_cli", "get_apn_from_database")
def _apn_db(store, answers, args):
    head = "\nEnter the mccmnc: \nEnter the Gid1 (comma separated list of byte values): \nEnter the Gid2 (comma separated list of byte values): "
    mccmnc = (answers or [""])[0]
    gid1 = (answers[1] if len(answers) > 1 else "").split(",")[0]
    try:
        with open(PRESETS, encoding="utf-8") as f:
            rows = json.load(f).get("presets", [])
    except (OSError, ValueError):
        rows = []
    hits = [p for p in rows if p["mccmnc"] == mccmnc and (not p["gid1"] or p["gid1"] == gid1)]
    hits.sort(key=lambda p: p["gid1"] == "")
    if mccmnc == "00101":
        hits = [{"apn": "internet.mock", "username": "", "password": "", "auth": "none"}]
    if not hits:
        return head + _rc("cmd_get_apn_from_database", 1011, "APN not found in Auto APN Database.")
    p = hits[0]
    return head + _rc("cmd_get_apn_from_database") + "apn:[%s]\nusername:[%s]\npassword:[%s]\nauth:[%d]\n" % (
        p["apn"], p["username"], p["password"], {"none": 0, "pap": 1, "chap": 2, "pap_chap": 3}.get(p["auth"], 0))


# ------------------------------------------------------------------------------ modem profiles
def _render_profile(p, with_type_idx=None):
    out = ""
    if with_type_idx is not None:
        out += "\nProfile Type:[%d](3GPP=0, 3GPP2=1)\nidx:[%d]\n" % with_type_idx
    else:
        out += "\n"
    out += ("APN:[%s]\nUser Name:[%s]\nPassword:[%s]\nName:[%s]\nLocked:[%d] (none=0, enabled=1, auto=2)\n"
            "Authenticate:[%d] (none=0, PAP=1, CHAP=2)\nManual DNS:[%d] (none=0, enabled=1)\nDNS1:[%s]\nDNS2:[%s]\n"
            "PDP Type:[%d] (ipv4=0, ppp=1, ipv6=2, ipv4v6=3)\nAPN Disabled:[%d] (enabled=0, disabled=1)\n"
            "PDN Inactivity Timeout:[%d]\nIPV4 address:[%s]\nIPV6 address:[%s]\nData Mode:[%d] (CDMA or HDR=0, CDMA=1, HDR=2)\n"
            "PDP Type 3GPP2:[%d] (ipv4=0, ipv6=1, ipv4v6=2, no pref=3)\n"
            % (p["apn"], p["user"], p["pw"], p["name"], p["locked"], p["auth"], p["manual_dns"], p["dns1"], p["dns2"],
               p["pdp"], p["apn_disabled"], p["timeout"], p["v4"], p["v6"], p["data_mode"], p["pdp2"]))
    return out


def _profile_from(a):
    """answers after type/index (or tech): apn user pw name locked auth manual_dns [dns1 dns2] pdp disabled timeout v4 v6 mode pdp2"""
    a = list(a)
    p = {"apn": a[0], "user": a[1], "pw": a[2], "name": a[3], "locked": int(a[4]), "auth": int(a[5]),
         "manual_dns": int(a[6])}
    rest = a[7:]
    if p["manual_dns"]:
        p["dns1"], p["dns2"], rest = rest[0], rest[1], rest[2:]
    else:
        p["dns1"], p["dns2"] = "0.0.0.0", "0.0.0.0"
    p.update({"pdp": int(rest[0]), "apn_disabled": int(rest[1]), "timeout": int(rest[2]), "v4": rest[3], "v6": rest[4],
              "data_mode": int(rest[5]), "pdp2": int(rest[6])})
    return p


@cli_handler("modem2_cli", "prof_get_tech")
def _prof_get(store, answers, args):
    head = "Enter profile type (3GPP=0, 3GPP2=1): Enter profile index: "
    with LOCK:
        try:
            ptype, idx = int(answers[0]), int(answers[1])
        except (IndexError, ValueError):
            return _fixture(store, "modem2_cli", "prof_get_tech")
        p = STATE["profiles"].get(idx) if ptype == 0 else None
        if p is None:
            return head + _rc("cmd_prof_get_tech", 2, "one or more bad parameters passed into a function.")
        return head + _rc("cmd_prof_get_tech") + _render_profile(p, (ptype, idx))


@cli_handler("modem2_cli", "prof_set_tech")
def _prof_set(store, answers, args):
    with LOCK:
        ptype, idx = int(answers[0]), int(answers[1])
        if ptype != 0 or idx not in STATE["profiles"]:
            return _rc("cmd_prof_set_tech", 2, "one or more bad parameters passed into a function.")
        STATE["profiles"][idx] = _profile_from(answers[2:])
        if idx == STATE["act_tech"]:
            p = STATE["profiles"][idx]
            STATE["apn_active"].update({"APN": p["apn"], "UserName": p["user"], "Password": p["pw"],
                                        "PDPAuthenticationType": {0: "None", 1: "PAP", 2: "CHAP"}.get(p["auth"], "None")})
        return _rc("cmd_prof_set_tech")


@cli_handler("modem2_cli", "prof_get_act_tech")
def _act_get(store, answers, args):
    with LOCK:
        return ("Enter profile type (3GPP=0, 3GPP2=1): " + _rc("cmd_prof_get_act_tech")
                + "\nProfile Type:[0](3GPP=0, 3GPP2=1)\nidx:[%d]\n" % STATE["act_tech"])


@cli_handler("modem2_cli", "prof_set_act_tech")
def _act_set(store, answers, args):
    with LOCK:
        vals = args or answers
        idx = int(vals[1])
        if idx not in STATE["profiles"]:
            return _rc("cmd_prof_set_act_tech", 2, "one or more bad parameters passed into a function.")
        STATE["act_tech"] = idx
        return _rc("cmd_prof_set_act_tech")


def _tech_get(cmd, key):
    @cli_handler("modem2_cli", cmd)
    def _h(store, answers, args):
        with LOCK:
            try:
                tech = int(answers[0])
            except (IndexError, ValueError):
                return _fixture(store, "modem2_cli", cmd)
            p = STATE[key].get(tech)
            if p is None:
                return _rc("cmd_" + cmd, 2, "one or more bad parameters passed into a function.")
            return "Enter APN Technology (LTE=0, UMTS=1, eHRPD=2): " + _rc("cmd_" + cmd) + _render_profile(p)
    return _h


_tech_get("prof_get_pri_tech", "pri")
_tech_get("prof_get_cust_tech", "cust")


@cli_handler("modem2_cli", "prof_set_cust_tech")
def _cust_set(store, answers, args):
    with LOCK:
        tech = int(answers[0])
        if tech not in STATE["cust"]:
            return _rc("cmd_prof_set_cust_tech", 2, "one or more bad parameters passed into a function.")
        STATE["cust"][tech] = _profile_from(answers[1:])
        return _rc("cmd_prof_set_cust_tech")


@cli_handler("modem2_cli", "pdn_get_override_param")
def _pdn_o_get(store, answers, args):
    head = "Enter profile number: Enter profile type (0-3GPP, 1-3GPP2): "
    with LOCK:
        try:
            n = int(answers[0])
        except (IndexError, ValueError):
            return _fixture(store, "modem2_cli", "pdn_get_override_param")
        d = STATE["pdn"].get(n)
        if d is None:
            return head + _rc("cmd_pdn_get_override_param", 1, "general error.") + "PDN index:[0]\nPDN type:[0]\nPDP override:[0]\nCLAT enabled:[0]\n"
        return head + _rc("cmd_pdn_get_override_param") + "PDN index:[%d]\nPDN type:[0]\nPDP override:[%d]\nCLAT enabled:[%d]\n" % (
            n, d["pdp"], d["clat"])


@cli_handler("modem2_cli", "pdn_set_override_param")
def _pdn_o_set(store, answers, args):
    with LOCK:
        v = [int(x) for x in (args or answers)]
        if v[0] not in STATE["pdn"]:
            return _rc("cmd_pdn_set_override_param", 1, "general error.")
        STATE["pdn"][v[0]].update(pdp=v[2], clat=v[3])
        return _rc("cmd_pdn_set_override_param")


@cli_handler("modem2_cli", "pdn_get_ext_params")
def _pdn_e_get(store, answers, args):
    head = "Enter profile number: Enter profile type (0-3GPP, 1-3GPP2): "
    with LOCK:
        try:
            n = int(answers[0])
        except (IndexError, ValueError):
            return _fixture(store, "modem2_cli", "pdn_get_ext_params")
        d = STATE["pdn"].get(n)
        if d is None:
            return head + _rc("cmd_pdn_get_ext_params", 1, "general error.")
        return head + _rc("cmd_pdn_get_ext_params") + (
            "Extra PDN index:[%d]\nExtra PDN type:[0]\nExtra PDN max connections:[%d]\nExtra PDN wait timer:[%d]\n"
            "Extra PDN connection timer:[%d]\n" % (n, d["max"], d["wait"], d["conn"]))


@cli_handler("modem2_cli", "pdn_set_ext_params")
def _pdn_e_set(store, answers, args):
    with LOCK:
        v = [int(x) for x in (args or answers)]
        if v[0] not in STATE["pdn"]:
            return _rc("cmd_pdn_set_ext_params", 1, "general error.")
        STATE["pdn"][v[0]].update(max=v[2], wait=v[3], conn=v[4])
        return _rc("cmd_pdn_set_ext_params")


# ------------------------------------------------------------------------------------------- SMS
BOX = {"1": "inbox", "2": "outbox", "3": "sent"}


def _ctime(t):
    """ctime() layout ('Sun Nov  1 02:35:42 2026'), as sms_cli prints smsc/utc/local."""
    g = time.gmtime(t)
    return time.strftime("%a %b ", g) + ("%2d" % g.tm_mday) + time.strftime(" %H:%M:%S %Y", g)


def _box(name):
    return STATE["sms"][name] if _sim()["present"] else []


@rest_handler("GET", "SMSList/Inbox")
def _rest_inbox(rest, body):
    with LOCK:
        return [{"SMSMessageIdentifier": m["id"], "SMSMessageUnreadState": not m["read"],
                 "SMSMessageAddress": m["address"], "SMSMessageContactName": "", "SMSMessageBody": m["body"],
                 "SMSMessageTimestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(m["t"]))} for m in _box("inbox")]


@rest_handler("PUT", "SMSList/Inbox")
def _rest_inbox_put(rest, body):
    with LOCK:
        for m in _box("inbox"):
            if isinstance(body, dict) and m["id"] == body.get("SMSMessageIdentifier"):
                m["read"] = not bool(body.get("SMSMessageUnreadState"))
                return None
    from ..errors import ApiError
    raise ApiError(502, "vendor_error", "Vendor REST PUT SMSList/Inbox: bad request id in", {"vendor_status": 400})


@rest_handler("POST", "SMSList/Outbox")
def _rest_send(rest, body):
    from ..errors import ApiError
    with LOCK:
        if not _sim()["present"]:
            raise ApiError(502, "vendor_error", "Vendor REST POST SMSList/Outbox: Operation failed during execution(set)",
                           {"vendor_status": 500})
        if not isinstance(body, dict) or not body.get("SMSMessageAddress") or "SMSMessageBody" not in body:
            raise ApiError(502, "vendor_error", "Vendor REST POST SMSList/Outbox: bad request passed in", {"vendor_status": 400})
        STATE["sms"]["sent"].append({"id": STATE["next_sms_id"], "read": True, "address": body["SMSMessageAddress"],
                                     "body": body["SMSMessageBody"], "t": int(time.time())})
        STATE["next_sms_id"] += 1
    return None


@rest_handler("GET", "SMSStatus")
def _rest_sms_status(rest, body):
    with LOCK:
        return {"SMSInboxUnreadCount": sum(1 for m in _box("inbox") if not m["read"])}


def _sms(fn, rc=0, msg="success."):
    return _rc(fn, rc, msg, "SMS")


@cli_handler("sms_cli", "get_list")
def _sms_list(store, answers, args):
    with LOCK:
        rows = _box(BOX.get((answers or ["1"])[0], "inbox")) if (answers or ["1"])[0] in BOX else []
        out = "command [get_list]\n\nEnter 0,1, 2 or 3: PreInbox(0), Inbox(1), Outbox(2), Sentbox(3): " + _sms("cmd_get_list")
        out += "count:[%d]\n" % len(rows)
        for i, m in enumerate(rows):
            out += "list[%d].id:[%d]\nlist[%d].state:[%s]\nlist[%d].storage:[1]\n" % (i, m["id"], i, "Read" if m["read"] else "Unread", i)
        return out


@cli_handler("sms_cli", "read")
def _sms_read(store, answers, args):
    with LOCK:
        head = ("command [read]\n\nEnter mailbox id PreInbox(0), Inbox(1), Outbox(2), Sentbox(3): \n"
                "Enter message id 0-120): ")
        box = BOX.get((answers or ["1"])[0])
        mid = int(answers[1]) if len(answers) > 1 else -1
        for m in (_box(box) if box else []):
            if m["id"] == mid:
                return head + _sms("cmd_read") + "smsc:[%s]\nutc:[%s]\nlocal:[%s]\naddress:[%s]\nbody:[%s]\n" % (
                    _ctime(m["t"]), _ctime(m["t"]), _ctime(m["t"]), m["address"], m["body"])
        return head + _sms("cmd_read", 1, "general error.")


@cli_handler("sms_cli", "get_unread")
def _sms_unread(store, answers, args):
    with LOCK:
        return "command [get_unread]\n\n" + _sms("cmd_get_unread") + "count:[%d]\n" % sum(1 for m in _box("inbox") if not m["read"])


@cli_handler("sms_cli", "set_state")
def _sms_state(store, answers, args):
    with LOCK:
        box = BOX.get(answers[0])
        for m in (_box(box) if box else []):
            if m["id"] == int(answers[1]):
                m["read"] = answers[2] == "1"
                return "command [set_state]\n\n" + _sms("cmd_set_state")
        return "command [set_state]\n\n" + _sms("cmd_set_state", 1, "general error.")


@cli_handler("sms_cli", "delete")
def _sms_delete(store, answers, args):
    with LOCK:
        box = BOX.get(answers[0])
        if box and _sim()["present"]:
            before = len(STATE["sms"][box])
            STATE["sms"][box] = [m for m in STATE["sms"][box] if m["id"] != int(answers[1])]
            if len(STATE["sms"][box]) < before:
                return "command [delete]\n\n" + _sms("cmd_delete")
        return "command [delete]\n\n" + _sms("cmd_delete", 1, "general error.")


@cli_handler("sms_cli", "send")
def _sms_send(store, answers, args):
    with LOCK:
        head = "command [send]\n\nEnter phone number: \nEnter message: "
        if not _sim()["present"]:
            return head + _sms("cmd_send", 1, "general error.")
        STATE["sms"]["sent"].append({"id": STATE["next_sms_id"], "read": True, "address": answers[0], "body": answers[1],
                                     "t": int(time.time())})
        STATE["next_sms_id"] += 1
        return head + _sms("cmd_send")


@cli_handler("sms_cli", "ab_get_list")
def _ab_list(store, answers, args):
    with LOCK:
        rows = STATE["contacts"]
        return ("command [ab_get_list]\n\n" + _sms("cmd_ab_get_list") + "count:[%d]\n" % len(rows)
                + "".join("list[%d].id:[%d]\n" % (i, c["id"]) for i, c in enumerate(rows)))


@cli_handler("sms_cli", "ab_get_entry")
def _ab_entry(store, answers, args):
    with LOCK:
        head = "command [ab_get_entry]\n\n\nEnter contact id 0-100: "
        for c in STATE["contacts"]:
            if str(c["id"]) == (answers or [""])[0]:
                return head + _sms("cmd_ab_get_entry") + "id:[%d]\nname:[%s]\naddress:[%s]\n" % (c["id"], c["name"], c["address"])
        return head + _sms("cmd_ab_get_entry", 1, "general error.")


@cli_handler("sms_cli", "ab_add_entry")
def _ab_add(store, answers, args):
    with LOCK:
        used = {c["id"] for c in STATE["contacts"]}
        cid = next(i for i in range(0, 101) if i not in used)
        STATE["contacts"].append({"id": cid, "name": answers[0], "address": answers[1]})
        return "command [ab_add_entry]\n\n\nEnter contact name: \nEnter contact address: " + _sms("cmd_ab_add_entry")


@cli_handler("sms_cli", "ab_edit_entry")
def _ab_edit(store, answers, args):
    with LOCK:
        for c in STATE["contacts"]:
            if c["id"] == int(answers[0]):
                c.update(name=answers[1], address=answers[2])
                return "command [ab_edit_entry]\n\n" + _sms("cmd_ab_edit_entry")
        return "command [ab_edit_entry]\n\n" + _sms("cmd_ab_edit_entry", 1, "general error.")


@cli_handler("sms_cli", "ab_del_entry")
def _ab_del(store, answers, args):
    with LOCK:
        before = len(STATE["contacts"])
        STATE["contacts"] = [c for c in STATE["contacts"] if c["id"] != int(answers[0])]
        ok = len(STATE["contacts"]) < before
        return "command [ab_del_entry]\n\n" + _sms("cmd_ab_del_entry", 0 if ok else 1, "success." if ok else "general error.")


# ------------------------------------------------------------------------------------ data usage
def _used():
    u = STATE["usage"]
    t = time.time() - u["t0"]
    return int(u["base_rx"] + t * 88000), int(u["base_tx"] + t * 9500)


@cli_handler("dua_vzw_cli", "get_data_usage_settings_info")
def _dua_settings(store, answers, args):
    with LOCK:
        if not _sim()["present"]:
            return _fixture(store, "dua_vzw_cli", "get_data_usage_settings_info")
        u = STATE["usage"]
        return ("command [get_data_usage_settings_info]\n\nEnter iface_type[0=INTERFACE_SIM,1=INTERFACE_ETH]: "
                "Enter imsi or leave empty to skip: Enter iccid or leave empty to skip: "
                "dua_get_data_usage_settings_info returned 0 (DUA: success.)\n"
                "cycle_day:[%d]\nusage_limit:[%s]\nlevel1_threshold:[%d]\n" % (u["cycle_day"], LIMIT_TEXT[u["limit"]], u["threshold"]))


@cli_handler("dua_vzw_cli", "set_data_usage_settings_info")
def _dua_set(store, answers, args):
    with LOCK:
        u = STATE["usage"]
        u.update(cycle_day=int(answers[3]), limit=int(answers[4]), threshold=int(answers[5]))
        return ("command [set_data_usage_settings_info]\n\nEnter iface_type[0=INTERFACE_SIM,1=INTERFACE_ETH]: Enter imsi : "
                "Enter iccid:What is cycle_day(1 <> 31): What is usage_limit [ ... ]:  What is level1_threshold(1 <> 100): "
                "dua_set_data_usage_settings_info returned 0 (DUA: success.)\n")


def _usage_body(start=True):
    rx, tx = _used()
    u = STATE["usage"]
    lim = [0, 20, 25, 100, 250, 500, 1024, 2048, 3072, 5120, 8192, 10240, 12288, 15360, 20480][u["limit"]] * 2 ** 20
    now = time.gmtime()
    day = min(u["cycle_day"], 28)
    out = "status:[0](0 => DATA_USAGE_INFO_STATUS_VALID, 1 => DATA_USAGE_INFO_STATUS_INVALID)\nplan_type:[LINE]\n"
    if start:
        out += "cycle_start_date:[%04d-%02d-%02d]\n" % (now.tm_year, now.tm_mon, day)
    out += ("cycle_end_date:[%04d-%02d-%02d]\nusage_timestamp:[%s]\nplan_limit:[%s]\nline_usage:[%.2f GB]\n"
            "shared_usage:[]\ntx_bytes:[%d]\nrx_bytes:[%d]\nplan_limit_bytes:[%d]\ntx:[%.2f]\nrx:[%.2f]\nunit:[GB]\n"
            "prepay_indicator:[N]\nmdn:[]\nis_data_available:[1]\n"
            % (now.tm_year + (1 if now.tm_mon == 12 else 0), now.tm_mon % 12 + 1, day,
               time.strftime("%Y-%m-%d %H:%M:%S", now), LIMIT_TEXT[u["limit"]], (rx + tx) / 2.0 ** 30, tx, rx, lim,
               tx / 2.0 ** 30, rx / 2.0 ** 30))
    return out


@cli_handler("dua_vzw_cli", "get_interface_data_usage_info")
def _dua_iface(store, answers, args):
    with LOCK:
        if not _sim()["present"] or (answers or ["0"])[0] != "0":
            return _fixture(store, "dua_vzw_cli", "get_interface_data_usage_info")
        return ("command [get_interface_data_usage_info]\n\nEnter iface_type[0=INTERFACE_SIM,1=INTERFACE_ETH]: "
                "Enter imsi or leave empty to skip: Enter iccid or leave empty to skp: "
                "dua_get_interface_data_usage_info returned 0 (DUA: success.)\n" + _usage_body(False))


@cli_handler("dua_vzw_cli", "get_data_usage_info")
def _dua_info(store, answers, args):
    with LOCK:
        if not _sim()["present"]:
            return _fixture(store, "dua_vzw_cli", "get_data_usage_info")
        return "command [get_data_usage_info]\n\ndua_get_data_usage_info returned 0 (DUA: success.)\n" + _usage_body(True)


@cli_handler("dua_vzw_cli", "get_active_interface_level_info")
def _dua_level(store, answers, args):
    with LOCK:
        if not _sim()["present"]:
            return _fixture(store, "dua_vzw_cli", "get_active_interface_level_info")
        rx, tx = _used()
        lim = [0, 20, 25, 100, 250, 500, 1024, 2048, 3072, 5120, 8192, 10240, 12288, 15360, 20480][STATE["usage"]["limit"]] * 2 ** 20
        level = "LEVELFULL" if not lim or (rx + tx) < lim * STATE["usage"]["threshold"] / 100.0 else "LEVEL1"
        return ("command [get_active_interface_level_info]\n\nEnter iface_type[0=INTERFACE_SIM,1=INTERFACE_ETH]: "
                "Enter imsi Enter iccid or leave empty to skip: dua_get_level_info0 (DUA: success.)\n"
                "the data usage is at level : %s\n\nrun from cli\n" % level)


@cli_handler("modem2_cli", "lifetime_counters_get")
def _lifetime(store, answers, args):
    with LOCK:
        if not _sim()["present"]:
            return _fixture(store, "modem2_cli", "lifetime_counters_get")
        rx, tx = _used()
        return _rc("cmd_lifetime_counters_get") + "Rx = %d KB, Tx = %d KB\n" % ((rx * 7) // 1024, (tx * 7) // 1024)


@cli_handler("modem2_cli", "get_bytes_counters")
def _bytes(store, answers, args):
    with LOCK:
        head = "\nEnter data call type (0 - IPv4, 2 - IPV6): "
        if not _sim()["present"] or (answers or [""])[0] not in ("0", "2"):
            return head + _rc("cmd_get_bytes_counters", 1, "general error.")
        rx, tx = _used()
        f = 0.8 if answers[0] == "0" else 0.2
        return head + _rc("cmd_get_bytes_counters") + "tx_bytes:[%d]\nrx_bytes:[%d]\n" % (int(tx * f / 3), int(rx * f / 3))
