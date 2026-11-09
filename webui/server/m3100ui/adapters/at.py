"""AT commands to the modem.

Transports, in order of preference:

  smd     ``read_atcmd "<AT command>" /dev/smd7`` - the vendor's own AT tool (``fota_pmode.sh`` uses it for
          ``AT$NWPREFMODE2?``).  It writes the command + CR to the SMD AT channel and prints every line until a
          final result code (OK / ERROR / +CME ERROR / ...).  Used only when the CLI policy has an argv rule for
          ``read_atcmd`` (cli_specs.ARGV_RULES key ("read_atcmd", "")) - see docs/features/tools.md.
  modem2  ``modem2_cli run_raw_command`` (stdin: command, prompt, reply, timeout) - the documented interface
          (docs/recon/modem2-cli.md).  NOTE: on this firmware the QMI backend's ``modem2_modem_run_raw_command``
          (libmal_qct.so @0x1cd50, 8 bytes) is a stub that always returns 0xC0001, so a real unit answers
          "general error" - the result then carries a hint instead of a response.

``/dev/at_mdm0`` is owned by port_bridge (USB AT bridge), so it is never opened.

Every command passes the classifier first (prefix match on the upper-cased command, per ;-chained part):
  deny   never sent (design §8 exclusions, the T99W368 "red line" list, Qualcomm/Quectel/Sierra equivalents)
  risky  radio / network / data-session / SMS state -> tier 2 confirmation
  write  any other setting or action (SIM PIN entry, dialling, unknown writes) -> tier 3 (design §8 "AT writes")
  ok     queries (``...?``, ``...=?``), known read-only execute commands and harmless session settings
"""
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

from ..errors import ApiError, forbidden

# -- policy --------------------------------------------------------------------------------------------------
# (prefix, reason).  Prefixes are matched against each upper-cased, space-free ;-part with any leading "AT" removed.
DENY_RULES: List[Tuple[str, str]] = [
    # design §8 deny-list
    ("+USBSWITCH", "USB mode switch"), ("+MODESWITCH", "USB / modem mode switch"), ("+DIAG", "diagnostic (DIAG) mode"),
    ("+EDL", "emergency download mode"), ("+EFS", "modem file-system access"), ("$QCPWRDN", "modem power-down"),
    ("+EGMR", "IMEI / identity write"), ("$NV", "NV item access"), ("^NV", "NV item access"),
    ('+QCFG="USBCFG"', "USB composition change"),
    # T99W368 red line (reference/t99w368-openwrt-tools/modem5g-plugin/MODULE_INFO.md, README.md)
    ("+RESET", "module reset"), ("+FASTBOOT", "boot-loader mode"), ("+FFOTADL", "firmware download"),
    ("+FFOTAUPGRADE", "firmware upgrade"),
    # equivalents of the excluded operations on Qualcomm / Quectel / Sierra / SIMCom / Telit firmware
    ('+QCFG="PCIE', "PCIe mode change"), ('+QCFG="DATA_INTERFACE', "data interface mode change"),
    ("+QPRTPARA", "factory parameter restore"), ("+QADBKEY", "ADB key"), ("+CMUX", "multiplexer (takes over the AT channel)"),
    ("$QCDMG", "switch to DIAG mode"), ("+QFASTBOOT", "boot-loader mode"), ("+SYSCMD", "shell command"),
    ("+QLINUXCMD", "shell command"), ("+QFOTADL", "firmware download"), ("+QPOWD", "power down"),
    ("+CPWROFF", "power down"), ("+CPOF", "power off"), ("!BOOTHOLD", "boot hold"), ("!RESET", "reset"),
    ("!USBCOMP", "USB composition change"), ("#USBCFG", "USB composition change"), ("+CUSBPIDSWITCH", "USB composition change"),
    ("+CRSM=214", "SIM file write (UPDATE BINARY)"), ("+CRSM=220", "SIM file write (UPDATE RECORD)"),
    ("+CSIM", "raw SIM command"), ("+CCHO", "SIM logical channel"), ("+CGLA", "raw SIM command"), ("+CRLA", "raw SIM command"),
    ("+QNVW", "NV write"), ("+QNVFW", "NV file write"), ("+QNVFD", "NV file delete"), ("+NVW", "NV write"),
    ("$QCNV", "NV item access"), ("$QCEFS", "modem file-system access"), ("$EFS", "modem file-system access"),
    ("+QFDEL", "module file delete"), ("+QFUPL", "module file upload"), ("+QFWRITE", "module file write"),
    ("+QFOPEN", "module file access"), ("+WIMEI", "IMEI write"), ("^IMEI", "IMEI write"), ("$IMEI", "IMEI write"),
    ("$QCMDN", "MDN write"), ("$QCMIN", "MIN write"), ("$MDN", "MDN write"), ("$MIN", "MIN write"),
    ("$SPC", "service programming code"), ("$QCSPC", "service programming code"), ("+SPC", "service programming code"),
    ("$QCMIP", "Mobile IP profile"), ("+ODIS=", "device identity write (vendor LwM2M files)"),
]
DENY_PREFIXES: List[str] = [p for p, _r in DENY_RULES]

# radio / network / data session / SMS -> tier 2
RISKY_RULES: List[Tuple[str, str]] = [
    ("+CFUN", "radio power (airplane mode / reset)"), ("+COPS=", "network selection"), ("+CGATT=", "packet attach"),
    ("+CGACT=", "data session"), ("+CGDCONT=", "APN / PDP context"), ("+CGDSCONT=", "APN / PDP context"),
    ("+CGAUTH=", "APN authentication"), ("+CGTFT=", "traffic flow template"), ("+CGEQOS=", "QoS"), ("+CGQREQ=", "QoS"),
    ("+CGQMIN=", "QoS"), ("+CREG=", "registration reporting"), ("+CGREG=", "registration reporting"),
    ("+CEREG=", "registration reporting"), ("+C5GREG=", "registration reporting"), ("+QNWPREFCFG=", "band / network mode"),
    ("+QNWLOCK", "cell / band lock"), ('+QCFG="BAND', "band selection"), ('+QCFG="NWSCANMODE', "network mode"),
    ("$QCBANDPREF", "band selection"), ("^SYSCFG", "network mode"), ("+CNMP=", "network mode"), ("+CNBP=", "band selection"),
    ("$NWPREFMODE2=", "network mode"), ("$QCPDPP", "data profile authentication"), ("$QCPRFMOD", "data profile"),
    ("$QCPRFCRT", "data profile"), ("$QCRMCALL", "data call"), ("+CMGD", "SMS delete"), ("+CMGS", "SMS send"),
    ("+CMSS", "SMS send"), ("+CMGW", "SMS store"), ("+CSCA=", "SMS centre"), ("+CNMI=", "SMS notification routing"),
    ("+CPMS=", "SMS storage"), ("+CUSD", "USSD request"), ("+CHUP", "call control"), ("+CHLD", "call control"),
]
RISKY_PREFIXES: List[str] = [p for p, _r in RISKY_RULES]

# tier 3 with a specific reason (everything else that is not a query falls back to "write" as well)
SENSITIVE_RULES: List[Tuple[str, str]] = [
    ("+CPIN=", "SIM PIN / PUK entry - wrong codes lock the SIM"), ("+CPWD", "SIM password change"),
    ("D", "a phone call"), ("Z", "the AT profile (reset)"), ("&F", "the AT profile (factory restore)"),
    ("&W", "the stored AT profile"),
]

# execute-form commands that only read (exact part match)
READ_EXACT = {
    "", "I", "I0", "I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I9", "&V",
    "+CGMR", "+CGMI", "+CGMM", "+GMI", "+GMM", "+GMR", "+GSN", "+CGSN", "+CIMI", "+CCID", "+ICCID", "+QCCID", "+CNUM",
    "+CSQ", "+CESQ", "+CPAS", "+CEER", "+CLAC", "+COPN", "+CGPADDR", "+CGCONTRDP", "+CGSCONTRDP", "+CGEQOSRDP",
    "+CGTFTRDP", "+CSUB", "+CMTE", "+TEMP", "+QTEMP", "+QNWINFO", "+QSPN", "+QCAINFO", "+QRSRP", "+QSINR", "+QRSRQ",
    "+QCSQ", "+QGMR", "+QINISTAT", "+QSIMSTAT", "$QCSQ", "$QCRSRP", "$QCRSRQ", "$QCSYSMODE", "$QCSIMSTAT",
    "^SYSINFO", "^SYSINFOEX", "!GSTATUS", "!LTEINFO", "!NRINFO", "!PCTEMP",
}
# execute-form reads that take arguments (prefix match)
READ_PREFIXES = (
    "+CGPADDR=", "+CGCONTRDP=", "+CGSCONTRDP=", "+CGEQOSRDP=", "+CGTFTRDP=", "+CGSN=", '+QENG="SERVINGCELL"',
    '+QENG="NEIGHBOURCELL"', "+CPBR=", "+CMGL=", "+CMGR=", "+CRSM=176", "+CRSM=178", "+CRSM=192", "+CRSM=242", "+CEER=",
)
# settings that only affect this AT session (echo, verbose errors, SMS text mode, character set)
SESSION_SAFE = ("E", "E0", "E1", "Q0", "V", "V1", "+CMEE=", "+CMGF=", "+CSCS=", "+CSDH=")

TIERS = {"ok": 0, "risky": 2, "write": 3}
# read_atcmd copies argv[1] into a 255-byte buffer (__stpcpy_chk): keep commands well below that.
AT_RE = re.compile(r"^[Aa][Tt][\x20-\x7e]{0,248}$")
_CLCK_QUERY_RE = re.compile(r'^\+CLCK=(\?|"?[A-Z0-9]{2}"?,2(,.*)?)$')

# safe queries offered as quick buttons in the AT console
QUICK: List[Tuple[str, str]] = [
    ("ATI", "Modem identification"), ("AT+CGMR", "Modem firmware version"), ("AT+CGMM", "Model"),
    ("AT+CSQ", "Signal quality (RSSI, BER)"), ("AT+CESQ", "Extended signal quality"), ("AT+CFUN?", "Radio state"),
    ("AT+CPIN?", "SIM state"), ("AT+COPS?", "Current operator"), ("AT+CREG?", "Circuit-switched registration"),
    ("AT+CEREG?", "LTE registration"), ("AT+C5GREG?", "5G registration"), ("AT+CGATT?", "Packet attach state"),
    ("AT+CGDCONT?", "APN / PDP contexts"), ("AT+CGACT?", "Active data contexts"), ("AT+CGPADDR", "Context IP addresses"),
    ("AT+CGCONTRDP", "Live data context (IP, DNS)"), ("AT+CCLK?", "Modem clock"), ("AT+CEER", "Last error report"),
    ("AT$NWPREFMODE2?", "Preferred network mode (vendor)"), ("AT+TEMP?", "Temperature (if supported)"),
]

FINAL_CODES = ("OK", "ERROR", "BUSY", "NO ANSWER", "NO CARRIER", "NO DIALTONE")


def normalise(cmd: str) -> str:
    c = (cmd or "").strip() if isinstance(cmd, str) else ""
    if not AT_RE.match(c):
        raise ApiError(400, "invalid_at", "AT command must start with AT and contain printable ASCII only",
                       {"field": "command"})
    return c


def _parts(cmd: str) -> List[str]:
    body = cmd[2:].upper().replace(" ", "").replace("\t", "")
    out = []
    for p in body.split(";"):
        if p.startswith("AT"):
            p = p[2:]
        out.append(p)
    return [p for p in out if p] or [""]


def describe(cmd: str) -> Dict:
    """Classification of a (normalised) command: {classification, tier, reason}."""
    flat = cmd.upper().replace(" ", "").replace("\t", "")
    if "+ODIS" in flat and flat != "AT+ODIS?":
        # read_atcmd emulates AT+ODIS itself (strstr on the raw command): anything but the exact query rewrites
        # /opt/nvtl/data/lwm2m/portfolio_* files (read_atcmd @0x112c).
        return {"classification": "deny", "tier": None, "reason": "blocked: device identity write (vendor LwM2M files)"}
    worst, reason = "ok", "read-only query"
    rank = {"ok": 0, "risky": 1, "write": 2}
    for p in _parts(cmd):
        for pre, why in DENY_RULES:
            if p.startswith(pre):
                return {"classification": "deny", "tier": None, "reason": "blocked: " + why}
        if p.startswith("+CLCK"):
            if _CLCK_QUERY_RE.match(p):
                continue
            return {"classification": "deny", "tier": None, "reason": "blocked: SIM / network lock change"}
        if p.endswith("?"):
            continue
        if p in READ_EXACT or p.startswith(READ_PREFIXES):
            continue
        if p in SESSION_SAFE or (p.startswith(SESSION_SAFE) and "=" in p and p.split("=", 1)[0] + "=" in SESSION_SAFE):
            continue
        kind, why = "write", "modem settings"
        for pre, r in RISKY_RULES:
            if p.startswith(pre):
                kind, why = "risky", r
                break
        if kind == "write":
            for pre, r in SENSITIVE_RULES:
                if p.startswith(pre) and (len(pre) > 1 or p[:1].isalpha()):
                    why = r
                    break
            if p in ("H", "H0", "A"):
                kind, why = "risky", "call control"
        if rank[kind] > rank[worst]:
            worst, reason = kind, why
        elif rank[kind] == rank[worst] and worst != "ok" and reason == "modem settings":
            reason = why
    return {"classification": worst, "tier": TIERS[worst], "reason": reason}


def classify(cmd: str) -> str:
    """'deny' | 'risky' | 'write' | 'ok' for the whole (;-chained) command."""
    return describe(cmd)["classification"]


def tier_for(kind: str) -> int:
    return TIERS.get(kind, 3)


def final_code(text: str) -> Optional[str]:
    """The final result code of an AT response (last matching line), or None."""
    for line in reversed([ln.strip() for ln in (text or "").replace("\r", "\n").split("\n")]):
        if not line:
            continue
        if line in FINAL_CODES or line.startswith(("+CME ERROR", "+CMS ERROR")):
            return line
    return None


def _clean(text: str, echo: str = "") -> str:
    """Normalise line ends, trim blank edges and drop the modem's echo of the command."""
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    if echo and lines and lines[0].strip().upper() == echo.strip().upper():
        lines.pop(0)
        while lines and not lines[0]:
            lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def smd_available() -> bool:
    """True when the CLI policy allows `read_atcmd <cmd> <port>` (argv rule present)."""
    from . import cli_specs
    return ("read_atcmd", "") in cli_specs.ARGV_RULES


class ModemAT:
    CLI = "modem2_cli"
    COMMAND = "run_raw_command"
    SMD_TOOL = "read_atcmd"
    SMD_PORT = "/dev/smd7"
    STUB_HINT = ("This firmware's modem service does not implement raw AT commands: modem2_cli run_raw_command "
                 "always answers 'general error' (the QMI back end's run_raw_command is an empty stub). AT commands "
                 "go through the vendor read_atcmd tool on /dev/smd7 instead, when the command policy allows it.")

    def __init__(self, cli_runner):
        self.cli = cli_runner
        self._lock = threading.Lock()

    def transport(self) -> str:
        return "smd" if smd_available() else "modem2"

    def send(self, command: str, allow_risky: bool = False, timeout: float = 10.0) -> Dict:
        cmd = normalise(command)
        info = describe(cmd)
        kind = info["classification"]
        if kind == "deny":
            raise forbidden("This AT command is blocked (%s)" % info["reason"].replace("blocked: ", ""), "at_denied")
        if kind in ("risky", "write") and not allow_risky:
            raise ApiError(428, "confirm_required",
                           "This AT command affects %s; confirm to send it" % info["reason"], {"tier": tier_for(kind)})
        timeout = max(1.0, min(float(timeout or 10.0), 60.0))
        t0 = time.time()
        with self._lock:
            if self.transport() == "smd":
                out = self._via_smd(cmd, timeout)
            else:
                out = self._via_modem2(cmd, timeout)
        out.update({"command": cmd, "classification": kind, "reason": info["reason"],
                    "elapsed_ms": int((time.time() - t0) * 1000)})
        return out

    # -- transports ------------------------------------------------------------------------------------------
    def _via_modem2(self, cmd: str, timeout: float) -> Dict:
        answers = [cmd, "", "", str(int(timeout))]
        try:
            res = self.cli.run(self.CLI, self.COMMAND, answers, timeout=timeout + 5)
        except ApiError as e:
            return {"ok": False, "transport": "modem2", "response": "", "raw": "", "rc": None, "final": None,
                    "error": e.message, "hint": self.STUB_HINT if e.code == "unverified_setter" else None}
        text = res.text or ""
        response = ""
        i = text.find("response:[")
        if i >= 0:
            j = text.rfind("]")
            response = text[i + len("response:["):j if j > i else len(text)]
        response = _clean(response, cmd)
        fc = final_code(response)
        ok = bool(res.ok and response and fc != "ERROR" and not (fc or "").startswith(("+CME", "+CMS")))
        out = {"ok": ok, "transport": "modem2", "response": response, "raw": text, "rc": res.rc, "final": fc,
               "message": res.message, "timed_out": res.timed_out}
        if not res.ok:
            out["error"] = "Modem service: %s" % (res.message or ("timed out" if res.timed_out else "no answer"))
            if res.rc == 1 and not response:
                out["hint"] = self.STUB_HINT
        return out

    def _via_smd(self, cmd: str, timeout: float) -> Dict:
        try:
            res = self.cli.run(self.SMD_TOOL, "", (), timeout=timeout, args=[cmd, self.SMD_PORT])
        except ApiError as e:
            return {"ok": False, "transport": "smd", "response": "", "raw": "", "rc": None, "final": None,
                    "error": e.message}
        text = res.text or ""
        response = _clean(text, cmd)
        fc = final_code(response)
        out = {"ok": fc == "OK", "transport": "smd", "response": response, "raw": text, "rc": res.exit_code,
               "final": fc, "timed_out": res.timed_out}
        if res.timed_out:
            out["error"] = "No final result code from the modem within %d s" % int(timeout)
        elif fc is None:
            out["error"] = "No final result code (%s)" % (response.splitlines()[-1] if response else "no output")
        return out
