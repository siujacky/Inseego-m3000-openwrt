"""Mock: Tools area - AT console, modem logging, PCO, Internet sessions, message bus, EFS / NV reads.

Output shapes follow the real binaries (see adapters/specs/tools.py and services/expert.py for the addresses).
Tests and the UI can steer the state through store.state: "at_stub" (True = behave like the real firmware whose
run_raw_command is a stub), "pco" ((value, pdn)), "efs" (path -> bytes overrides), "sessions" (list of dicts).

Shared mocks: other areas also read EFS files with `nwcli qmi_idl read_file` (Cellular: lte_bandpref built from its
band state; SIM: NV 60002).  The handler below chains to a handler registered earlier for the band-preference files
and leaves the copied bytes under both store.state["shm"] and store.state["vfs"] so every reader finds them.
"""
import random
import time
import zlib

from ..adapters.mock import CLI_HANDLERS, cli_handler

T0 = time.time()
AT_PROMPTS = ("\nEnter the command string: \nEnter the prompt string(enter for none): "
              "\nEnter the reply string(enter for none): \nEnter the timeout value: ")


def _at_lines(cmd):
    """Canned modem answers: list of response lines (final result code last)."""
    c = cmd.strip().upper().replace(" ", "")
    now = time.localtime()
    table = {
        "AT": [],
        "ATI": ["Manufacturer: QUALCOMM INCORPORATED", "Model: M3100", "Revision: THN-1.54  1  [2024-01-24 03:21:03]",
                "IMEI: 350000000000001", "+GCAP: +CGSM"],
        "AT+CGMR": ["THN-1.54  1  [2024-01-24 03:21:03]"],
        "AT+CGMM": ["M3100"],
        "AT+CGMI": ["QUALCOMM INCORPORATED"],
        "AT+CGSN": ["350000000000001"],
        "AT+CIMI": ["001010000000001"],
        "AT+CSQ": ["+CSQ: %d,99" % random.randint(18, 26)],
        "AT+CESQ": ["+CESQ: 99,99,255,255,%d,%d" % (random.randint(20, 30), random.randint(50, 64))],
        "AT+CFUN?": ["+CFUN: 1"],
        "AT+CPIN?": ["+CPIN: READY"],
        "AT+COPS?": ['+COPS: 0,0,"Mock Mobile",13'],
        "AT+CREG?": ["+CREG: 0,1"],
        "AT+CEREG?": ["+CEREG: 0,1"],
        "AT+C5GREG?": ["+C5GREG: 0,1"],
        "AT+CGATT?": ["+CGATT: 1"],
        "AT+CGDCONT?": ['+CGDCONT: 1,"IPV4V6","internet","0.0.0.0",0,0,0,0',
                        '+CGDCONT: 2,"IPV4V6","ims","0.0.0.0",0,0,0,0',
                        '+CGDCONT: 3,"IPV4V6","sos","0.0.0.0",0,0,0,1'],
        "AT+CGACT?": ["+CGACT: 1,1", "+CGACT: 2,1", "+CGACT: 3,0"],
        "AT+CGPADDR": ['+CGPADDR: 1,"100.64.12.34","2001:db8:1234:5678::1"', '+CGPADDR: 2,"10.0.0.2"'],
        "AT+CGCONTRDP": ['+CGCONTRDP: 1,5,"internet","100.64.12.34.255.255.255.0","100.64.12.1","198.51.100.53",'
                         '"198.51.100.54"'],
        "AT+CCLK?": ['+CCLK: "%s-20"' % time.strftime("%y/%m/%d,%H:%M:%S", now)],
        "AT+CEER": ["+CEER: No cause information available"],
        "AT$NWPREFMODE2?": ["Mode_Pref: 50"],
        "AT+CGMR;+CSQ": ["THN-1.54  1  [2024-01-24 03:21:03]", "+CSQ: 22,99"],
        "AT+CMEE=2": [],
        "ATE0": [], "ATE1": [],
    }
    if c in table:
        return table[c] + ["OK"]
    if c.startswith(("AT+CFUN=", "AT+COPS=", "AT+CGATT=", "AT+CGACT=", "AT+CGDCONT=", "AT+CREG=", "AT+CEREG=",
                     "AT+C5GREG=", "AT+CMGF=", "AT+CSCS=")):
        return ["OK"]
    if c.startswith("AT+CPIN="):
        return ["+CME ERROR: incorrect password"]
    if c.endswith("=?"):
        return ["OK"]
    return ["ERROR"]


@cli_handler("modem2_cli", "run_raw_command")
def _run_raw(store, answers, args):
    cmd = answers[0] if answers else (args[0] if args else "")
    if store.state.get("at_stub"):
        return AT_PROMPTS + "cmd_run_raw_command returned 1 (MDM_MAIN: general error.)\n"
    body = "\r\n".join(["", ] + _at_lines(cmd) + [""])
    return AT_PROMPTS + "cmd_run_raw_command returned 0 (MDM_MAIN: success.)\nresponse:[%s]\n" % body


@cli_handler("read_atcmd", "")
def _read_atcmd(store, answers, args):
    cmd = args[0] if args else ""
    return "\n".join([cmd] + _at_lines(cmd)) + "\n"


# -- lab test: modem logging -------------------------------------------------------------------------------------
@cli_handler("settings_cli", "set_modem_logging_settings")
def _set_logging(store, answers, args):
    en, size, count = (list(answers) + ["0", "0", "0"])[:3]
    store.set_text("settings_cli", "get_modem_logging_settings",
                   "command [get_modem_logging_settings]\n\n"
                   "settings_get_modem_logging_settings returned 0 (SETTINGS: success.)\n"
                   "modem_logging_enabled[%d]\nlog_files_size[%d]\nnumber_of_files[%d]\n" % (int(en), int(size), int(count)))
    return ("command [set_modem_logging_settings]\n\nEnter logging enabled (0/1):Enter log files size:"
            "Enter number of files (1-3):settings_set_modem_logging_settings returned 0 (SETTINGS: success.)\n")


@cli_handler("settings_cli", "clear_modem_logs")
def _clear_logs(store, answers, args):
    store.state["modem_logs_cleared"] = time.time()
    return "command [clear_modem_logs]\n\nsettings_clear_modem_logs returned 0 (SETTINGS: success.)\n"


# -- carrier PCO --------------------------------------------------------------------------------------------------
@cli_handler("modem2_cli", "get_pco_event")
def _get_pco(store, answers, args):
    value, pdn = store.state.get("pco", (0, 0))
    return "cmd_get_pco_event returned 0 (MDM_MAIN: success.)\npco_value:[%d]\npdn:[%d]\n" % (value, pdn)


@cli_handler("ccm2_cli", "accept_pco_event")
def _accept_pco(store, answers, args):
    store.state["pco_accepted"] = time.time()
    return "ccm_accept_pco_event returned 0 (CCM: success.)\n"


# -- Internet sessions ---------------------------------------------------------------------------------------------
def _default_sessions():
    now = int(time.time())
    return [
        {"ipv4": "100.64.12.34", "ipv6": "2001:db8:1234:5678::1", "roam_status": 1, "time": now - 5400,
         "duration": 5400, "rx": 1250000000, "tx": 96000000, "rxv6": 210000000, "txv6": 12000000},
        {"ipv4": "100.64.7.201", "ipv6": "", "roam_status": 1, "time": now - 3 * 86400, "duration": 7260,
         "rx": 482000000, "tx": 31000000, "rxv6": 0, "txv6": 0},
        {"ipv4": "100.70.1.9", "ipv6": "2001:db8:99::7", "roam_status": 2, "time": now - 9 * 86400, "duration": 1800,
         "rx": 52000000, "tx": 4100000, "rxv6": 9000000, "txv6": 800000},
    ]


@cli_handler("sysintcli", "getSessionLogStats")
def _sessions(store, answers, args):
    rows = store.state.get("sessions")
    if rows is None:
        rows = _default_sessions()
    head = "sys_getSessionLogStats returned 0, count:[%d]\n" % len(rows)
    lines = ["command [getSessionLogStats]", "", head.strip(), head.strip()]
    for i, s in enumerate(rows):
        for k in ("ipv4", "ipv6", "roam_status", "time", "duration", "rx", "tx", "rxv6", "txv6"):
            lines.append("list[%d].%s:[%s]" % (i, k, s.get(k, "")))
    return "\n".join(lines) + "\n"


# -- message bus -----------------------------------------------------------------------------------------------------
_MSGBUS = {
    "modem2.conn_state": ("int32", 5), "modem2.device_state": ("int32", 1), "modem2.operating.mode": ("int32", 0),
    "modem2.registration_status": ("int32", 1), "modem2.roam_state": ("int32", 0), "modem2.plmn_mode": ("int32", 1),
    "modem2.uim_state": ("int32", 3), "modem2.pin1_state": ("int32", 3), "modem2.sim.pco_event": ("int32", 0),
    "modem2.operator_change": ("string", "Mock Mobile"), "modem2.connection_prerequisite_status": ("int32", 1),
    "modem2.data_signal_change": ("binary", bytes([0x05, 0x00, 0x00, 0x00, 0xA4, 0xFF, 0xFF, 0xFF, 0x0E, 0x00, 0x00, 0x00])),
    "modem2.5g_data_signal_change": ("binary", bytes([0x04, 0x00, 0x00, 0x00, 0xA8, 0xFF, 0xFF, 0xFF])),
    "ccm2.wan_stats": ("binary", bytes(range(1, 25))), "wifi.2ghz.enabled": ("int32", 1), "wifi.5ghz.enabled": ("int32", 1),
    "wifi.wlan0.if_up": ("int32", 1), "hostapd.ap.enabled": ("int32", 1), "eth.state_up": ("int32", 0),
    "eth.wan_status": ("int32", 0), "gps.engine.status": ("int32", 0), "fota.upgrade_status": ("string", "idle"),
    "webui.softwareupdate.status": ("string", "none"), "usb.attached_to_bridge": ("int32", 1),
    "nvtl.dua.usage.info": ("string", "{}"),
}


@cli_handler("msgbus_cli", "MsgBusGet")
def _msgbus_get(store, answers, args):
    ch = (answers[0] if answers else (args[0] if args else "")).strip()
    head = "command [MsgBusGet]\n\nWhat is channel: "
    known = store.text("msgbus_cli", "print_channels") or ""
    if ch not in _MSGBUS and ("[%s]" % ch) not in known:
        return head + "MsgBusGet returned 1 (MSGBUS: unknown error.)\n"
    kind, value = _MSGBUS.get(ch, ("int32", 1 if ch.endswith((".ready", ".initialized")) else 0))
    stamp = time.asctime(time.localtime(T0 - 3600 + (sum(map(ord, ch)) % 3000)))
    out = head + "MsgBusGet returned 0 (MSGBUS: success.)\nlast updated time:[%s]\ndata type:[" % stamp
    if kind == "binary":
        rows = [" ".join("%02x" % b for b in value[i:i + 10]) + " " for i in range(0, len(value), 10)]
        return out + "binary]\nlen:[%d] (bytes)\ndata:\n%s\n" % (len(value), "\n".join(rows))
    if kind == "string":
        return out + "string]\ndata:[%s]\n" % value
    if ch == "modem2.data_signal_change":
        value = random.randint(-100, -85)
    return out + "int32]\ndata:[%d]\n" % value


# -- EFS / NV ------------------------------------------------------------------------------------------------------
def _efs_files():
    rnd = random.Random(7)
    return {
        "/nv/item_files/modem/mmode/lte_bandpref": bytes.fromhex("5f18090800a00000"),
        "/nv/item_files/modem/mmode/lte_bandpref_extn_65_256": bytes([0x02] + [0] * 23),
        "/nvm/num/60002": b"\x00",
        "/factory_backup/item_list.save": ("# mock restore list\n/nv/item_files/modem/mmode/lte_bandpref\n"
                                           "/nv/item_files/modem/mmode/lte_bandpref_extn_65_256\n/nvm/num/60002\n"
                                           "/nv/item_files/modem/uim/mmgsdi/features_status_list\n").encode("ascii"),
        "/novatel/$$NW_FACTORY_BACKUP$$": bytes(bytearray(rnd.randrange(256) for _ in range(4096))),
        "/nv/item_files/rfnv/00029002": zlib.compress(bytes(bytearray([0, 1, 2, 3, 0, 0, 0, 1]) * 64)),
        "/nv/item_files/rfnv/00028967": zlib.compress(bytes(bytearray(range(256))) * 4),
    }


_PREV_NWCLI = CLI_HANDLERS.get(("nwcli", "qmi_idl"))


@cli_handler("nwcli", "qmi_idl")
def _nwcli(store, answers, args):
    if len(args) != 4 or args[0] != "read_file":
        return ("command [%s]\n\nUsage:\n nwcli qmi_idl read_file <local file path> <modem efs file path> "
                "<Max expected file size in bytes>\n" % (args[0] if args else ""))
    local, path, max_bytes = args[1], args[2], int(args[3])
    data = None
    if _PREV_NWCLI is not None and path.startswith("/nv/item_files/modem/mmode/lte_bandpref"):
        _PREV_NWCLI(store, answers, args)               # band state kept by the Cellular mock
        data = store.state.get("shm", {}).pop(local, None)
    if data is None:
        files = _efs_files()
        files.update(store.state.get("efs", {}))
        data = files.get(path)
    head = "command [read_file]\n\nAttempting to read file:[%s]\n" % path
    if data is None:
        return head + "File read QMI failure:1\nread_efs_file() failed, rc=[1]\n"
    data = data[:max_bytes]
    for key in ("shm", "vfs"):                          # readers pop their copy; keep the other bounded
        d = store.state.setdefault(key, {})
        if len(d) > 32:
            d.clear()
        d[local] = data
    return head + "File read success, (Total bytes read:%d)\nSuccess\n" % len(data)


_NV = {
    "NW_NV_MAC_ID_I": "02:00:00:00:00:01", "NW_NV_MAC_ID_2_I": "02:00:00:00:00:02",
    "NW_NV_MAC_ID_3_I": "00:00:00:00:00:00", "NW_NV_MAC_ID_4_I": "00:00:00:00:00:00",
    "NW_NV_ETHERNET_MAC_ID_I": "MAC_ADDR_LO32=0x00000003\nMAC_ADDR_HI16=0x0200",
    "NW_NV_USB_MAC_ID_I": "02:00:00:00:00:04", "NW_NV_LINUX_RUN_LEVEL_I": "1 5", "NW_NV_USB_DEFAULT_MODE_I": "EUM",
    "NW_NV_PSM_DEFAULT_MODE_I": "0", "NW_NV_PRI_INFORMATION_I": "188", "NW_NV_CARRIER_INFO_I": "generic",
    "NW_NV_ASYNC_DATA_ORIG_STR_I": "00:00:00:00:00:00",
}


@cli_handler("nwnvitem", "-r")
def _nwnvitem(store, answers, args):
    name = args[1] if len(args) >= 2 else ""
    if name not in _NV:
        return "NV Item not supported\n"
    return _NV[name] + "\n"
