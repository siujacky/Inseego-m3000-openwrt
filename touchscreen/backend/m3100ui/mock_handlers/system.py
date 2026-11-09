"""Mock: System (admin/login, log settings, formats, backup/restore, power, reset button),
Software update (REST SoftwareUpdate/*, webui_cli/nua_cli settings) and sample data for the
log/about pages.

Handlers rewrite the matching get_* fixture text in the vendor output shape, so the services
parse mock and real output with the same code.  The sample_* helpers are used by services
only when app.mock is true (the files they stand in for do not exist on a PC).
"""
import hashlib
import json
import random
import time

from ..adapters.cli import CliResult
from ..adapters.mock import cli_handler, rest_handler
from ..errors import ApiError

QUESTIONS = ["What is your Birth date?", "What is your Mothers maiden name?", "Where is the street you grew up?"]


def _cur(store, cli, cmd):
    txt = store.text(cli, cmd) or ""
    return CliResult(cli, cmd, txt, 0)


# -- webui_cli login settings ---------------------------------------------------------------
def _login_text(cmd, d):
    return ("command [%s]\n\nwebui_%s returned 0 (WEBUI: success.)\nretries:[%s]\nauto_logout_period(secs):[%s]\n"
            "password:[%s]\ndefault_password:[%s]\nsecurity.question: %s\nsecurity.answer:[%s]\n"
            % (cmd, cmd, d["retries"], d["auto_logout"], d["password"], d["default_password"], d["question"], d["answer"]))


@cli_handler("webui_cli", "set_login_settings")
def _set_login(store, answers, args):
    r = _cur(store, "webui_cli", "get_login_settings")
    d = {"retries": r.get("retries", "5"), "auto_logout": r.get("auto_logout_period(secs)", "600"),
         "password": r.get("password", ""), "default_password": r.get("default_password", ""),
         "question": r.get("security.question", QUESTIONS[0]), "answer": r.get("security.answer", "")}
    q = int(answers[0])
    reset = int(answers[2])
    out = "command [set_login_settings]\n\nChoose the security question (0: Birth-date, 1: Mothers-maiden-name, 2: Grownup-street): "
    out += "Answer(MM/DD/YYYY): " if q == 0 else "Answer: "
    out += "Do you want to reset password [0/1]: "
    if reset == 1:
        out += "Enter new password: "
    if reset > 1 or q > 2:
        return out + "webui_set_login_settings returned 2 (WEBUI: one or more bad parameters passed into a function.)\n"
    d["question"] = QUESTIONS[q]
    d["answer"] = answers[1].strip()
    if reset == 1:
        d["password"] = answers[3]
    store.set_text("webui_cli", "get_login_settings", _login_text("get_login_settings", d))
    store.set_text("webui_cli", "get_modified_login_settings", _login_text("get_modified_login_settings", d))
    return out + "webui_set_login_settings returned 0 (WEBUI: success.)\n"


@cli_handler("webui_cli", "set_log_settings")
def _set_log(store, answers, args):
    store.set_text("webui_cli", "get_log_settings",
                   "command [get_log_settings]\n\nwebui_get_log_settings returned 0 (WEBUI: success.)\n"
                   "log_enabled:[%d]\nlog_life_time(in days):[%d]\n" % (int(answers[0]), int(answers[1])))
    return ("command [set_log_settings]\n\nIs log enabled [0/1]: What is log life time ([1 - 4] days): "
            "webui_set_log_settings returned 0 (WEBUI: success.)\n")


@cli_handler("webui_cli", "set_i18n_settings")
def _set_i18n(store, answers, args):
    lang, df, hf, tf, dist, num = answers[0], int(answers[1]), int(answers[2]), int(answers[3]), int(answers[4]), int(answers[5])
    store.set_text("webui_cli", "get_i18n_settings",
                   "command [get_i18n_settings]\n\nwebui_get_i18n_settings returned 0 (WEBUI: success.)\n"
                   "lang:[%s]\ndate_fmt:[%d](0 => mm/dd/yyyy, 1 => dd.mm.yyyy, 2 => dd/mm/yyyy, 3 => yyyy/mm/dd)\n"
                   "hour_fmt:[%d](0 => 12hr, 1 => 24hr)\ntemp_fmt:[%d](0 => Fahrenheit, 1 => Celsius)\n"
                   "distance_fmt:[%d](0 => US, 1 => Metric)\nnumeric_fmt:[%d](0 => period, 1 => comma)\n"
                   % (lang, df, hf, tf, dist, num))
    return "command [set_i18n_settings]\n\nwebui_set_i18n_settings returned 0 (WEBUI: success.)\n"


@cli_handler("webui_cli", "set_software_update_settings")
def _set_su(store, answers, args):
    store.set_text("webui_cli", "get_software_update_settings",
                   "command [get_software_update_settings]\n\nwebui_get_software_update_settings returned 0 (WEBUI: success.)\n"
                   "fumoEnabled:[%d]\nautoPollEnabled:[%d]\nlowBatteryThreshold:[%d]\n"
                   % (int(answers[0]), int(answers[1]), int(answers[2])))
    return ("command [set_software_update_settings]\n\nFUMO (0: Disable, 1: Enable): Auto Polling (0: Disable, 1: Enable): "
            "Low Battery Threshold (1-99): webui_get_software_update_settings returned 0 (WEBUI: success.)\n")


# -- settings_cli backup / restore --------------------------------------------
def mock_backup_bytes(store):
    """Bytes of the most recent mock backup (an opaque blob like the real encrypted archive)."""
    return store.state.get("system.backup_blob")


@cli_handler("settings_cli", "backup")
def _backup(store, answers, args):
    seed = ("%s|%s" % (time.time(), random.random())).encode()
    body = b"".join(hashlib.sha256(seed + bytes([i])).digest() for i in range(96))
    store.state["system.backup_blob"] = b"Salted__" + body[:8] + body
    return ("settings_backup returned 3 (SETTINGS: one or more buffer type parameters passed into API is too small.)\n"
            "settings_backup returned 0 (SETTINGS: success.)\nbackup_file:[/tmp/nvtlconfig.bin]\n")


@cli_handler("settings_cli", "restore")
def _restore(store, answers, args):
    path = answers[0] if answers else (args[0] if args else "")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return "What is the path:  settings_restore returned 6 (SETTINGS: No such file or directory.)\n"
    store.state["system.restored"] = {"size": len(data), "t": time.time()}
    return "What is the path:  settings_restore returned 0 (SETTINGS: success.)\n"


# -- factory reset / reset button / power -----------------------------------------------------
@cli_handler("factory_reset_cli", "do_factory_reset")
def _factory(store, answers, args):
    store.state["system.factory_reset"] = time.time()
    return "cmd_do_factory_reset returned 0 (FACTORY_RESET: success.)\n"


@cli_handler("factory_reset_cli", "set_hw_reset_sw_enabled")
def _hw_reset(store, answers, args):
    v = int(answers[0]) if answers else int(args[0])
    store.set_text("factory_reset_cli", "get_hw_reset_sw_enabled",
                   "command [get_hw_reset_sw_enabled]\n\ncmd_get_hw_reset_sw_enabled returned 0 (FACTORY_RESET: success.)\n"
                   "HW reset SW enabled:   %d\n" % (1 if v else 0))
    return ("Enter HW reset status (0=Disable, 1=Enable): cmd_set_hw_reset_sw_enabled returned 0 (FACTORY_RESET: success.)\n"
            "\nHW reset SW status Complete.\n")


@cli_handler("dsm_cli", "reset_device")
def _reset_device(store, answers, args):
    store.state["system.power"] = ("reboot", time.time())
    return "What is forced (1-forced, 0-wait for ack):  cmd_reset_device returned 0 (DSM: success.)\n"


@cli_handler("dsm_cli", "shutdown_device")
def _shutdown_device(store, answers, args):
    store.state["system.power"] = ("shutdown", time.time())
    return "What is forced (1-forced, 0-wait for ack):  cmd_shutdown_device returned 0 (DSM: success.)\n"


@cli_handler("dsm_cli", "get_wifi_mac_address")
def _wifi_macs(store, answers, args):
    # success path prints only the two lines (dsm_cli handler @0x2620; no status line on success)
    return "What is the no of WiFi Macs:WiFi1=[02:00:00:00:00:02]\nWiFi2=[02:00:00:00:00:03]\n"


@rest_handler("PUT", "DeviceState")
def _device_state(rest, body):
    op = (body or {}).get("Operation") if isinstance(body, dict) else None
    if op not in ("Reboot", "Shutdown", "ResetToFactoryDefaults"):
        raise ApiError(502, "vendor_error", "Vendor REST PUT DeviceState: bad request passed in",
                       {"vendor_status": 400, "resource": "DeviceState"})
    rest.data["_mock_device_state"] = {"op": op, "t": time.time()}
    return None


# -- software update (REST SoftwareUpdate/* + fota_cli/nua_cli) ------------------------------
_SU_IDLE = {"IsUserInitiated": False, "Mdip": 0, "Status": 0, "SuiValue": 0, "Value": 0, "Version": ""}


def _su(rest):
    return rest.data.setdefault("_mock_su", {"phase": "idle", "t": 0.0})


@rest_handler("GET", "SoftwareUpdate/Status")
def _su_status(rest, body):
    st = dict(rest.data.get("SoftwareUpdate/Status") or _SU_IDLE)
    s = _su(rest)
    age = time.time() - s.get("t", 0)
    phase = s["phase"]
    if phase == "checking":
        if age < 5:
            st.update(Status=1, Value=0, IsUserInitiated=True)
        else:
            s["phase"] = phase = "available" if rest.data.get("_mock_su_offer") else "none"
    if phase == "none":
        st.update(Status=3, Value=0)
    elif phase == "available":
        st.update(Status=4, Value=0, Version=s.get("version", "THN-1.60-5.4-2.700"))
    elif phase == "downloading":
        pct = min(100, int(age * 12))
        if pct >= 100:
            s.update(phase="ready")
            st.update(Status=6, Value=0, Version=s.get("version", "THN-1.60-5.4-2.700"))
        else:
            st.update(Status=5, Value=pct, Version=s.get("version", "THN-1.60-5.4-2.700"))
    elif phase == "ready":
        st.update(Status=6, Version=s.get("version", "THN-1.60-5.4-2.700"))
    elif phase == "deferred":
        st.update(Status=10, Version=s.get("version", "THN-1.60-5.4-2.700"))
    elif phase == "installing":
        st.update(Status=7, Version=s.get("version", "THN-1.60-5.4-2.700"))
    return st


def _su_action(name, allowed, new_phase):
    def handler(rest, body):
        s = _su(rest)
        if s["phase"] not in allowed:
            raise ApiError(502, "vendor_error", "Vendor REST PUT SoftwareUpdate/%s: Operation failed during execution(set)" % name,
                           {"vendor_status": 500, "resource": "SoftwareUpdate/" + name})
        s.update(phase=new_phase, t=time.time())
        return None
    return handler


rest_handler("PUT", "SoftwareUpdate/CheckUpdate")(
    _su_action("CheckUpdate", ("idle", "none", "available", "deferred", "checking"), "checking"))
rest_handler("PUT", "SoftwareUpdate/Download")(_su_action("Download", ("available",), "downloading"))
rest_handler("PUT", "SoftwareUpdate/DownloadDefer")(_su_action("DownloadDefer", ("available",), "available"))
rest_handler("PUT", "SoftwareUpdate/Install")(_su_action("Install", ("available", "ready", "deferred"), "installing"))
rest_handler("PUT", "SoftwareUpdate/InstallDefer")(_su_action("InstallDefer", ("ready", "available"), "deferred"))


@cli_handler("fota_cli", "cfg_get_current_record")
def _cfg_record(store, answers, args):
    return ("fota_cfg_get_current_record returned 0 (FOTA: success.)\nConfiguration version:[188]\n"
            "Update date:[02-29-2024-07:36:54]\nfw version:[THN-1.54-5.4-2.609]\n")


@cli_handler("nua_cli", "set_settings")
def _nua_set(store, answers, args):
    vals = dict(zip(args[0::2], args[1::2]))
    r = _cur(store, "nua_cli", "get_settings")
    model, cust = r.get("model string", "M3100"), r.get("customer id", "vzw")
    store.set_text("nua_cli", "get_settings",
                   "command [get_settings]\n\nsu_api_get_settings returned 0 (NUA: success.)\nNUA Settings:\n"
                   "FUMO enabled=[%s]\nPOLL enabled=[%s]\nFOTA Opt Out Disabled=[%s]\nLow Battery Threshold=[%s]\n"
                   "Low Battery Threshold with Charger=[%s]\nModel String=[%s]\nCustomer ID=[%s]\n"
                   % (vals.get("-f"), vals.get("-p"), vals.get("-s"), vals.get("-l"), vals.get("-w"), model, cust))
    return ("su_api_get_settings returned 0 (NUA: success.)\nfumo_enabled[%s] su_checkin[%s] su_fota_opt_out_disabled[%s] "
            "low_battery_threshold[%s] low_battery_threshold_with_charger[%s]\nsu_api_set_settings returned 0 (NUA: success.)\n"
            % (vals.get("-f"), vals.get("-p"), vals.get("-s"), vals.get("-l"), vals.get("-w")))


@cli_handler("nua_cli", "set_poll_interval")
def _nua_poll(store, answers, args):
    secs = int(args[0])
    store.set_text("nua_cli", "get_poll_interval", "command [get_poll_interval]\n\nPoll internval, seconds=[%d]\n" % secs)
    return ("su_api_test_set_poll_interval returned 0 (NUA: success.)\nPoll internval set, seconds=[%d]\n"
            "type \"nua.sh restart \" or restart device to take effect\n" % secs)


# -- sample data for files that only exist on the device ------------------------------------
_WWAN = ["modem state: searching", "SIM status changed: not present", "network registration: no service",
         "radio technology changed: none", "data call: disconnected (no SIM)", "modem state: limited service",
         "signal: no measurement (no serving cell)", "airplane mode: off"]
_SYS = ["power on cause: reboot", "battery: charging complete (100%)", "USB tethering: connected (RNDIS)",
        "web interface: admin login", "software update check: no update available", "Wi-Fi: 2.4 GHz and 5 GHz up",
        "settings changed: notifications", "thermal: normal"]


def _stamp(t):
    lt = time.localtime(t)
    return "%s %2d %s" % (time.strftime("%b", lt), lt.tm_mday, time.strftime("%H:%M:%S", lt))


def sample_vendor_log(kind):
    """Raw syslog-style lines in the shape the vendor writes (marker grep done by the service)."""
    tag = "{mifios_wanevent}:" if kind == "wwan" else "{mifios_sysevent}:"
    msgs = _WWAN if kind == "wwan" else _SYS
    now = time.time()
    out = []
    for i in range(60):
        t = now - (60 - i) * 97
        out.append("%s mifi user.notice MIFI_DBG[%d]: [WEBUI]:[NOTICE] - %s%s" % (_stamp(t), 600 + i, tag, msgs[i % len(msgs)]))
        if i % 7 == 3:
            out.append("%s mifi user.info MDM_MAIN[812]: [MDM_MAIN]:[INFO] - periodic housekeeping" % _stamp(t + 3))
    return out


def sample_dmesg():
    return "\n".join("[%8.6f] %s" % (i * 1.37, m) for i, m in enumerate([
        "Booting Linux on physical CPU 0x0", "Linux version 5.4.161-perf (mock) armv7l",
        "Machine model: Qualcomm Technologies, Inc. SDX65", "Memory: 677MB available", "rndis0: link up",
        "bridge0: port 1(rndis0) entered forwarding state", "wlan0: AP mode started", "thermal: zone0 41C"]))


def sample_sessions():
    now = int(time.time())
    rows = []
    for i in range(6):
        start = now - (i + 1) * 86400 + 3600
        dur = 3600 * (i + 1) + 120 * i
        rows.append({"start": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(start)),
                     "end": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(start + dur)),
                     "duration_s": dur, "ipv4": "100.64.%d.%d" % (i, 10 + i), "ipv6": "2001:db8::%x" % (i + 1),
                     "roaming": "home" if i != 3 else "roaming", "rx_bytes": 250000000 * (i + 1), "tx_bytes": 21000000 * (i + 1),
                     "interface": "mobile"})
    return rows


def sample_bundle():
    """A tiny gzip'ed tar standing in for storelogs.sh output."""
    import gzip
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = json.dumps({"mock": True, "created": int(time.time())}).encode()
        info = tarfile.TarInfo("log/README.txt")
        info.size = len(data)
        info.mtime = int(time.time())
        tf.addfile(info, io.BytesIO(data))
    return gzip.compress(buf.getvalue())
