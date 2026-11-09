"""System administration: admin password and security question (shared with the stock UI),
sessions of this UI, language and display formats, vendor web-server login/HTTPS info,
backup and restore, restart / shut down / factory reset, hardware reset button, scheduled
restart, and logs (system journal, vendor event logs, Internet sessions, kernel, audit,
support bundle).

Vendor interfaces (field-level map: docs/features/system.md):
  webui_cli get/set_login_settings, get_advanced_settings, get_https_*, get/set_log_settings,
            get/set_i18n_settings (fallback), get_supported_languages (fallback)
  REST      DisplayFormatSettings (+/SupportedLanguages), DeviceState {"Operation": Reboot |
            Shutdown | ResetToFactoryDefaults}  (restapi.fcgi DeviceStateHandler::HandlePutRequest)
  settings_cli backup / restore (appconfig create|upload of /tmp/nvtlconfig.bin)
  factory_reset_cli do_factory_reset (fallback), get/set_hw_reset_sw_enabled
  dsm_cli   reset_device / shutdown_device (fallbacks), get_state, get_ui_controls_shutdown_enabled
  files     /var/log/debug_log* ({mifios_wanevent}), /opt/nvtl/log/system_log* ({mifios_sysevent}),
            /opt/nvtl/data/dmdb/dmdb.db (DataSessions, opened read-only), journalctl, dmesg,
            /opt/nvtl/bin/storelogs.sh (support bundle)
"""
import glob
import gzip
import hashlib
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import zlib
from typing import Any, Dict, List, Optional, Tuple

from .. import validate
from ..errors import ApiError, conflict, field_error, not_found, unavailable, vendor_error
from ..httpd import Response

log = logging.getLogger("m3100ui.system")

# -- admin password / security question ------------------------------------------------------
QUESTIONS = [  # (text printed by webui_cli, label shown in this UI)
    ("What is your Birth date?", "Your date of birth (MM/DD/YYYY)"),
    ("What is your Mothers maiden name?", "Your mother's maiden name"),
    ("Where is the street you grew up?", "The street where you grew up"),
]
PW_MIN, PW_MAX = 8, 32           # stock set_passcode: MinimumPasscodeLength 8, maxPasscodeLen 32
_PW_CHARS = re.compile(r"^[\x21-\x5a\x5c\x5e-\x7e]+$")   # printable ASCII, no space, no [ ]


def check_new_password(pw: Any, current: Optional[str] = None, field: str = "new_password") -> str:
    """Password rules: what the stock page enforces (8-32) plus what the vendor tool can store
    (scanf %255s: no whitespace; the label:[value] output format: no square brackets)."""
    if not isinstance(pw, str) or not pw:
        raise field_error(field, "Enter a new password")
    if len(pw) < PW_MIN:
        raise field_error(field, "Use at least %d characters" % PW_MIN)
    if len(pw) > PW_MAX:
        raise field_error(field, "Use at most %d characters" % PW_MAX)
    if not _PW_CHARS.match(pw):
        raise field_error(field, "Use letters, digits and symbols only (no spaces, square brackets or accented letters)")
    kinds = sum([any(c.islower() for c in pw), any(c.isupper() for c in pw), any(c.isdigit() for c in pw),
                 any(not c.isalnum() for c in pw)])
    if kinds < 2:
        raise field_error(field, "Mix at least two kinds of characters: lower case, upper case, digits, symbols")
    if len(set(pw)) < 4:
        raise field_error(field, "Too easy to guess: use more different characters")
    if current is not None and pw == current:
        raise field_error(field, "The new password must differ from the current one")
    return pw


def check_answer(a: Any) -> str:
    if not isinstance(a, str):
        raise field_error("security_answer", "Must be text")
    a = a.strip()
    if not a:
        raise field_error("security_answer", "Enter an answer")
    if any(ord(c) < 32 or ord(c) == 127 for c in a):
        raise field_error("security_answer", "Line breaks and control characters are not allowed")
    if "[" in a or "]" in a:
        raise field_error("security_answer", "Square brackets are not allowed")
    if len(a.encode("utf-8")) > 255:
        raise field_error("security_answer", "Use at most 255 bytes")
    return a


def bracket_value(text: str, label: str) -> Optional[str]:
    """`label:[value]` where value may itself contain ']' (takes the last ']' on the line)."""
    m = re.search(r"^\s*" + re.escape(label) + r"\s*:\s*\[(.*)\]\s*$", text, re.M)
    return m.group(1) if m else None


def parse_login(text: str) -> Dict[str, Any]:
    from ..adapters.cli import CliResult
    r = CliResult("webui_cli", "get_login_settings", text, 0)
    qtext = (r.get("security.question") or "").strip()
    qid = None
    for i, (t, _label) in enumerate(QUESTIONS):
        if t.lower() == qtext.lower():
            qid = i
    return {"retries": r.get_int("retries"), "auto_logout_s": r.get_int("auto_logout_period(secs)"),
            "password": bracket_value(text, "password") or "",
            "default_password": bracket_value(text, "default_password") or "",
            "question_id": qid, "question_text": qtext, "answer": bracket_value(text, "security.answer") or ""}


# -- display formats -----------------------------------------------------------------------
FORMATS = {  # key -> (REST field, webui_cli label, [(api value, REST value, label)])  list index = CLI number
    "date_format": ("DisplayDateFormat", "date_fmt", [
        ("mdy_slash", "MMDDYYYYSlashSeparated", "mm/dd/yyyy"), ("dmy_dot", "DDMMYYYYDotSeparated", "dd.mm.yyyy"),
        ("dmy_slash", "DDMMYYYYSlashSeparated", "dd/mm/yyyy"), ("ymd_slash", "YYYYMMDDSlashSeparated", "yyyy/mm/dd")]),
    "time_format": ("DisplayTimeFormat", "hour_fmt", [("12h", "12Hr", "12-hour (2:30 PM)"), ("24h", "24Hr", "24-hour (14:30)")]),
    "temperature_unit": ("DisplayTemperatureFormat", "temp_fmt", [("fahrenheit", "Fahrenheit", "Fahrenheit (°F)"),
                                                                  ("celsius", "Celsius", "Celsius (°C)")]),
    "distance_unit": ("DisplayDistanceFormat", "distance_fmt", [("us", "USA", "Feet and miles"),
                                                               ("metric", "Metric", "Metres and kilometres")]),
    "decimal_separator": ("DisplayNumericSeparator", "numeric_fmt", [("period", "Period", "Period (1.5)"),
                                                                     ("comma", "Comma", "Comma (1,5)")]),
}
LANG_LABELS = {"en_US": "English (US)", "es_MX": "Spanish (Mexico)", "fr_CA": "French (Canada)", "es": "Spanish",
               "de": "German", "it": "Italian", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "nl": "Dutch",
               "sv": "Swedish", "hi": "Hindi", "ar_EG": "Arabic (Egypt)"}

# -- logs -----------------------------------------------------------------------------------
LOG_SOURCES = [
    ("system", "System journal", "Messages from all system services (journalctl)."),
    ("wwan", "Mobile network events", "Connection, registration and SIM events recorded by the vendor software. "
                                      "Kept in memory: cleared when the router restarts."),
    ("device", "Device events", "Power, battery, update and settings events recorded by the vendor software "
                                "(kept on flash)."),
    ("sessions", "Internet sessions", "Past mobile data sessions: start, duration, addresses and data used."),
    ("kernel", "Kernel messages", "The Linux kernel log (dmesg)."),
    ("audit", "Changes in this interface", "Every change made through this web interface (no passwords)."),
]
VENDOR_LOGS = {  # source -> (file, tag, stock clear marker)
    "wwan": ("/var/log/debug_log", "{mifios_wanevent}:", "cleared_wan_log"),
    "device": ("/opt/nvtl/log/system_log", "{mifios_sysevent}:", "cleared_system_log"),
}
DMDB = "/opt/nvtl/data/dmdb/dmdb.db"
LOG_READ_BUDGET = 8 * 1024 * 1024
_TS_RE = re.compile(r"^(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:[+-]\d\d:?\d\d|Z)?|"
                    r"[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d|\[\s*\d+\.\d+\])\s+(.*)$")
ROAM = {1: "home", 2: "roaming", 3: "partial"}   # stock session_log_info MapToRoamingType

# -- backup / power ---------------------------------------------------------------------------
BACKUP_MIN = 32
BACKUP_MAX = 4 * 1024 * 1024
_BACKUP_PATH = re.compile(r"^/(tmp|var/tmp|opt/nvtl/tmp|opt/nvtl/data|data)/[A-Za-z0-9._/-]{1,128}$")
PENDING_TTL = 600
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SCHED_FILE = "system_schedule.json"
MARKS_FILE = "system_logmarks.json"
BACKUPS_FILE = "system_backups.json"
POWER_FALLBACK = {
    "Reboot": ("dsm_cli", "reset_device", ["0"]),
    "Shutdown": ("dsm_cli", "shutdown_device", ["0"]),
    "ResetToFactoryDefaults": ("factory_reset_cli", "do_factory_reset", []),
}


def split_time(line: str) -> Tuple[Optional[str], str]:
    m = _TS_RE.match(line)
    return (m.group(1), m.group(2)) if m else (None, line)


def rotated_files(base: str) -> List[str]:
    """Oldest first: base.N[.gz] with the highest N first, then base itself."""
    found = []
    for p in glob.glob(base + ".*"):
        m = re.search(r"\.(\d+)(?:\.gz)?$", p)
        if m:
            found.append((int(m.group(1)), p))
    return [p for _n, p in sorted(found, reverse=True)] + ([base] if os.path.isfile(base) else [])


def read_tail_lines(path: str, budget: int, max_unpacked: int = 64 * 1024 * 1024) -> Tuple[List[str], int]:
    """Lines of a (possibly gzip'ed) text file: at most `budget` bytes from its end, never a
    partial first line.  Returns (lines, bytes used)."""
    cut = False
    try:
        with open(path, "rb") as f:
            head = f.read(2)
            if head == b"\x1f\x8b":
                f.seek(0)
                buf = bytearray()
                total = 0
                with gzip.GzipFile(fileobj=f) as gz:
                    while total < max_unpacked:
                        chunk = gz.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        buf += chunk
                        if len(buf) > 2 * budget + 1:
                            del buf[:len(buf) - budget - 1]
                if len(buf) > budget:
                    cut = buf[-budget - 1:-budget] != b"\n"
                    raw = bytes(buf[-budget:])
                else:
                    raw = bytes(buf)
            else:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                start = max(0, size - budget)
                if start > 0:
                    f.seek(start - 1)
                    cut = f.read(1) != b"\n"
                else:
                    f.seek(0)
                raw = f.read(budget)
    except (OSError, EOFError, ValueError, zlib.error):
        return [], 0
    lines = raw.decode("utf-8", "replace").splitlines()
    if cut and lines:
        lines = lines[1:]
    return lines, len(raw)



def vendor_event_lines(raw_lines: List[str], tag: str, stock_marker: str, own_marker: Optional[str]) -> List[str]:
    """Apply the stock 'cleared' marker and this UI's marker, then keep the tagged lines."""
    cut = 0
    full_marker = tag + stock_marker
    for i, ln in enumerate(raw_lines):
        if full_marker in ln:
            cut = i + 1
    if own_marker:
        for i in range(len(raw_lines) - 1, cut - 1, -1):
            if raw_lines[i] == own_marker:
                cut = i + 1
                break
    return [ln for ln in raw_lines[cut:] if tag in ln and full_marker not in ln]


def parse_history_date(v: Optional[str]) -> Optional[str]:
    """fota/webui dates 'MM-DD-YYYY-hh:mm:ss' -> 'YYYY-MM-DD hh:mm:ss' (None when unset)."""
    m = re.match(r"^(\d\d)-(\d\d)-(\d{4})-(\d\d:\d\d:\d\d)$", (v or "").strip())
    if not m or m.group(3) in ("0000", "1970"):
        return None
    return "%s-%s-%s %s" % (m.group(3), m.group(1), m.group(2), m.group(4))


def run_setter(app, cli: str, cmd: str, answers=(), what: str = "", timeout: Optional[float] = None):
    """Setter call with friendly errors: a statically analysed spec that the device has not
    verified yet (refused by the CLI runner on a real unit) becomes a 503 the pages explain."""
    try:
        return app.cli.run(cli, cmd, list(answers), timeout=timeout, check=True, what=what)
    except ApiError as e:
        if e.code == "unverified_setter":
            raise unavailable("%s is not enabled on this router yet: the vendor command (%s %s) has only been checked "
                              "offline and must be verified on the device first." % (what or "This action", cli, cmd),
                              {"cli": cli, "command": cmd, "reason": "unverified_setter"})
        raise


def schedule_due(sched: Dict[str, Any], now: float, uptime_s: float) -> bool:
    """True when a scheduled restart should run now (guards: sane clock, uptime, once a day)."""
    if not sched.get("enabled"):
        return False
    lt = time.localtime(now)
    if lt.tm_year < 2024 or uptime_s < 900:
        return False
    last = sched.get("last_run") or 0
    if now - last < 20 * 3600:
        return False
    if DAYS[lt.tm_wday] not in (sched.get("days") or []):
        return False
    try:
        hh, mm = [int(x) for x in str(sched.get("time", "")).split(":")]
    except ValueError:
        return False
    delta = (lt.tm_hour * 60 + lt.tm_min) - (hh * 60 + mm)
    return 0 <= delta < 3


def register(app):
    r = app.router
    lock = threading.Lock()
    pending: Dict[str, Dict[str, Any]] = {}      # restore uploads: token -> {data, t, sid, sha256}
    sim = {"down_until": 0.0, "boot": 0}         # mock-only restart simulation
    bundle_lock = threading.Lock()

    def mock_mod():
        from ..mock_handlers import system as m
        return m

    # -- helpers ------------------------------------------------------------------------------
    def setter(cli: str, cmd: str, answers=(), what: str = "", timeout: Optional[float] = None):
        return run_setter(app, cli, cmd, answers, what, timeout)

    def boot_id() -> str:
        try:
            with open("/proc/sys/kernel/random/boot_id", "r") as f:
                return f.read().strip()
        except OSError:
            return ""

    def instance() -> str:
        return hashlib.sha256(("%s|%s|%s" % (boot_id(), app.started, sim["boot"])).encode()).hexdigest()[:16]

    def read_file_text(path: str, limit: int = 4096) -> Optional[str]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(limit).strip()
        except OSError:
            return None

    def login_settings() -> Dict[str, Any]:
        res = app.cli.run("webui_cli", "get_login_settings", check=True, what="Reading the login settings")
        return parse_login(res.text)

    # == Administration ========================================================================
    def admin_view() -> Dict[str, Any]:
        lg = login_settings()
        adv = app.cli_try("webui_cli", "get_advanced_settings")
        st = app.settings()
        qid = lg["question_id"]
        return {
            "password_set": bool(lg["password"]),
            "default_password_in_use": bool(lg["password"]) and lg["password"] == lg["default_password"],
            "security_question": qid,
            "security_question_text": QUESTIONS[qid][1] if qid is not None else lg["question_text"],
            "security_answer_set": bool(lg["answer"]),
            "questions": [[i, label] for i, (_t, label) in enumerate(QUESTIONS)],
            "password_rules": {"min_length": PW_MIN, "max_length": PW_MAX,
                               "text": "%d-%d characters; letters, digits and symbols, at least two kinds; "
                                       "no spaces or square brackets." % (PW_MIN, PW_MAX)},
            "vendor_login": {
                "max_retries": lg["retries"], "auto_logout_s": lg["auto_logout_s"],
                "editable": False,
                "login_required": None if not adv else {
                    "advanced_pages": bool(adv.get_int("login_enabled", 1)),
                    "gps_page": bool(adv.get_int("gps_login_enabled", 1)),
                    "network_pages": bool(adv.get_int("network_settings_login_enabled", 1))},
            },
            "this_ui": {"idle_timeout_s": int(st.get("session_idle_s", 1800)), "max_session_s": int(st.get("session_max_s", 43200)),
                        "lockout": "After 3 wrong passwords each attempt is delayed; after 10 the address is locked out for 5 minutes."},
        }

    @r.route("GET", "/api/system/admin")
    def get_admin(req):
        """Admin password status, security question and vendor login settings."""
        return admin_view()

    @r.route("GET", "/api/system/admin/secret")
    def get_admin_secret(req):
        """Reveal the security answer and the factory default admin password."""
        lg = login_settings()
        app.audit(req, "system.admin.reveal")
        return {"security_answer": lg["answer"], "default_password": lg["default_password"]}

    @r.route("PUT", "/api/system/admin/login")
    def put_admin_login(req):
        """Change the admin password and/or security question (tier 3: current password)."""
        data = validate.only_keys(req.json, ["new_password", "confirm_password", "security_question", "security_answer"])
        new_pw = data.get("new_password") or None
        if new_pw is not None:
            check_new_password(new_pw)
            if "confirm_password" in data and data["confirm_password"] != new_pw:
                raise field_error("confirm_password", "The two passwords do not match")
        q_new = None
        if data.get("security_question") is not None:
            q_new = validate.integer("security_question", data["security_question"], 0, len(QUESTIONS) - 1)
        a_new = None
        if data.get("security_answer") not in (None, ""):
            a_new = check_answer(data["security_answer"])
        app.require_tier(req, 3)
        cur = login_settings()
        if new_pw is not None and new_pw == cur["password"]:
            raise field_error("new_password", "The new password must differ from the current one")
        q = cur["question_id"] if q_new is None else q_new
        a = a_new if a_new is not None else cur["answer"]
        if q is None:
            raise field_error("security_question", "Choose a security question")
        if q_new is not None and q_new != cur["question_id"] and a_new is None:
            raise field_error("security_answer", "Enter the answer to the new question")
        try:
            a = check_answer(a)
        except ApiError:
            raise field_error("security_answer", "Enter a security answer (needed by the vendor software)")
        if new_pw is None and q == cur["question_id"] and a == cur["answer"]:
            raise field_error("new_password", "Nothing to change")
        answers = [str(q), a, "1", new_pw] if new_pw is not None else [str(q), a, "0"]
        setter("webui_cli", "set_login_settings", answers, what="Saving the login settings")
        after = login_settings()
        if new_pw is not None and after["password"] != new_pw:
            raise vendor_error("The vendor software did not store the new password")
        if after["question_id"] != q or after["answer"] != a:
            raise vendor_error("The vendor software did not store the security question")
        if new_pw is not None:
            if app.mock:
                app.state.update_settings({"mock_password": new_pw})
            app.auth.forget_password_cache()
            app.auth.logout_all(except_sid=req.session.sid if req.session else None)
        app.audit(req, "system.admin.login", {"password_changed": new_pw is not None,
                                              "security_question_changed": q != cur["question_id"],
                                              "security_answer_changed": a != cur["answer"]})
        out = admin_view()
        out["password_changed"] = new_pw is not None
        return out

    @r.route("GET", "/api/system/sessions")
    def get_sessions(req):
        """Active sessions of this web interface."""
        now = time.time()
        with app.auth.lock:
            items = list(app.auth.sessions.values())
        cur = req.session.sid if req.session else None
        return [{"id": hashlib.sha256(s.sid.encode()).hexdigest()[:10], "ip": s.ip, "created": int(s.created),
                 "last_active": int(s.last), "idle_s": int(now - s.last), "current": s.sid == cur}
                for s in sorted(items, key=lambda s: -s.last)]

    @r.route("POST", "/api/system/sessions/logout-others")
    def logout_others(req):
        """End every other session of this web interface."""
        app.auth.logout_all(except_sid=req.session.sid if req.session else None)
        app.audit(req, "system.sessions.logout_others")
        return {"ok": True, "sessions": get_sessions(req)}

    @r.route("GET", "/api/system/https")
    def get_https(req):
        """HTTPS settings of the vendor web server (read-only)."""
        en = app.cli_try("webui_cli", "get_https_enabled")
        port = app.cli_try("webui_cli", "get_https_port")
        cert = app.cli_try("webui_cli", "get_https_settings")
        if not (en or port or cert):
            raise unavailable("The vendor HTTPS settings are not available")
        c = {}
        if cert:
            for k in ("certificate_name", "country_name", "state_name", "locality_name", "organization_name", "ou_name",
                      "common_name", "email_address"):
                c[k] = cert.get(k) or ""
            c["validity_days"] = cert.get_int("validity")
        return {"enabled": bool(en.get_int("enabled", 0)) if en else None, "port": port.get_int("port") if port else None,
                "certificate": c, "editable": False,
                "note": "Shown for reference. Changing it is not offered: switching the vendor web server to HTTPS "
                        "would break this interface's connection to the vendor software."}

    # -- language & formats ------------------------------------------------------------------
    def supported_languages() -> List[str]:
        d = app.rest_try("DisplayFormatSettings/SupportedLanguages")
        if isinstance(d, dict) and isinstance(d.get("SupportedLanguages"), list):
            return [str(x) for x in d["SupportedLanguages"]]
        res = app.cli_try("webui_cli", "get_supported_languages")
        langs = re.findall(r"\.locale:\[([^\]]*)\]", res.text) if res else []
        return langs or ["en_US"]

    def formats_view() -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        d = app.rest_try("DisplayFormatSettings")
        if isinstance(d, dict):
            out["source"] = "rest"
            out["language"] = d.get("DisplayLanguage") or ""
            for key, (rk, _cl, opts) in FORMATS.items():
                out[key] = next((o[0] for o in opts if o[1] == d.get(rk)), None)
        else:
            res = app.cli_try("webui_cli", "get_i18n_settings")
            if not res:
                raise unavailable("Language and format settings are not available on this router")
            out["source"] = "cli"
            out["language"] = res.get("lang") or ""
            for key, (_rk, cl, opts) in FORMATS.items():
                n = res.get_int(cl)
                out[key] = opts[n][0] if n is not None and 0 <= n < len(opts) else None
        langs = supported_languages()
        if out["language"] and out["language"] not in langs:
            langs.append(out["language"])
        out["languages"] = [[c, LANG_LABELS.get(c, c)] for c in langs]
        out["options"] = {key: [[o[0], o[2]] for o in opts] for key, (_rk, _cl, opts) in FORMATS.items()}
        return out

    @r.route("GET", "/api/system/formats")
    def get_formats(req):
        """Language and display formats (device screen and vendor pages)."""
        return formats_view()

    @r.route("PUT", "/api/system/formats")
    def put_formats(req):
        """Change language / date / time / unit formats."""
        data = validate.only_keys(req.json, ["language"] + list(FORMATS))
        cur = formats_view()
        new = {k: cur.get(k) for k in ["language"] + list(FORMATS)}
        if "language" in data:
            new["language"] = validate.choice("language", data["language"], [c for c, _l in cur["languages"]])
        for key, (_rk, _cl, opts) in FORMATS.items():
            if key in data:
                new[key] = validate.choice(key, data[key], [o[0] for o in opts])
        changes = {}
        if new["language"] != cur.get("language"):
            changes["DisplayLanguage"] = new["language"]
        for key, (rk, _cl, opts) in FORMATS.items():
            if new[key] != cur.get(key) and new[key] is not None:
                changes[rk] = next(o[1] for o in opts if o[0] == new[key])
        if changes:
            try:
                app.rest.merge_put("DisplayFormatSettings", changes)
            except ApiError as e:
                if e.code not in ("unsupported", "disabled", "vendor_unreachable"):
                    raise
                idx = []
                for key, (_rk, _cl, opts) in FORMATS.items():
                    v = new[key] or opts[0][0]
                    idx.append(str([o[0] for o in opts].index(v)))
                lang = validate.text("language", new["language"] or "en_US", 2, 16, pattern=r"^[A-Za-z_]+$")
                setter("webui_cli", "set_i18n_settings", [lang] + idx, what="Saving the formats")
            app.audit(req, "system.formats.update", {"keys": sorted(data)})
        return formats_view()

    # == Backup & restore ======================================================================
    def known_backups() -> List[Dict[str, Any]]:
        return app.state.read_json(BACKUPS_FILE, []) or []

    def remember_backup(data: bytes) -> Dict[str, Any]:
        rec = {"t": int(time.time()), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        hist = [x for x in known_backups() if x.get("sha256") != rec["sha256"]]
        hist.append(rec)
        app.state.write_json(BACKUPS_FILE, hist[-20:])
        return rec

    def read_backup_file(path: Optional[str]) -> bytes:
        if app.mock:
            data = mock_mod().mock_backup_bytes(app.cli.store)
            if not data:
                raise vendor_error("The mock backup was not created")
            return data
        if not path or ".." in path or not _BACKUP_PATH.match(path):
            raise vendor_error("The vendor software reported an unexpected backup location", {"path": path})
        try:
            size = os.path.getsize(path)
            if size <= 0 or size > BACKUP_MAX:
                raise vendor_error("The backup file has an unexpected size (%d bytes)" % size)
            with open(path, "rb") as f:
                data = f.read(BACKUP_MAX + 1)
        except OSError as e:
            raise vendor_error("The backup file could not be read: %s" % e)
        if path.startswith("/tmp/"):
            try:
                os.remove(path)   # holds every setting incl. passwords; do not leave it in /tmp
            except OSError:
                pass
        return data

    def validate_backup(data: bytes) -> None:
        if not data:
            raise field_error("file", "The file is empty")
        if len(data) < BACKUP_MIN:
            raise field_error("file", "The file is too small to be a settings backup")
        if len(data) > BACKUP_MAX:
            raise field_error("file", "The file is too large to be a settings backup (limit %d MB)" % (BACKUP_MAX // 1048576))
        head = data[:64].lstrip().lower()
        if head.startswith((b"<!doctype", b"<html")):
            raise field_error("file", "This is a web page, not a settings backup")
        if head.startswith(b"\x7felf"):
            raise field_error("file", "This is a program file, not a settings backup")

    def prune_pending() -> None:
        now = time.time()
        for k in [k for k, v in pending.items() if now - v["t"] > PENDING_TTL]:
            pending.pop(k, None)

    @r.route("GET", "/api/system/backup")
    def get_backup(req):
        """What a backup contains, limits, and backups created by this interface."""
        return {"max_bytes": BACKUP_MAX, "file_extension": "bin",
                "contents": "Device, router, Wi-Fi and system settings including passwords (encrypted by the vendor "
                            "software). Modem settings such as band locks are not included.",
                "same_model_only": True,
                "history": [{"t": x.get("t"), "size": x.get("size"), "sha256": (x.get("sha256") or "")[:16]}
                            for x in reversed(known_backups())]}

    @r.route("POST", "/api/system/backup")
    def create_backup(req):
        """Create and download an encrypted settings backup (admin password required)."""
        validate.only_keys(req.json, [])
        pw = req.field("password", "")
        app.auth.reauth(pw if isinstance(pw, str) else None, req.client_ip)
        res = setter("settings_cli", "backup", [], what="Creating the backup", timeout=60)
        data = read_backup_file(res.get("backup_file"))
        rec = remember_backup(data)
        app.audit(req, "system.backup.create", {"size": rec["size"]})
        name = "settings-backup-%s.bin" % time.strftime("%Y%m%d-%H%M")
        return Response(data, "application/octet-stream",
                        headers={"Content-Disposition": 'attachment; filename="%s"' % name, "X-Backup-Sha256": rec["sha256"]})

    @r.route("POST", "/api/system/backup/upload", upload=True)
    def upload_backup(req):
        """Upload a backup file for restore (checked, kept in memory for 10 minutes)."""
        data = req.body or b""
        validate_backup(data)
        digest = hashlib.sha256(data).hexdigest()
        known = next((x for x in known_backups() if x.get("sha256") == digest), None)
        token = secrets.token_urlsafe(18)
        with lock:
            prune_pending()
            if len(pending) >= 4:
                pending.pop(min(pending, key=lambda k: pending[k]["t"]), None)
            pending[token] = {"data": data, "t": time.time(), "sid": req.session.sid if req.session else None, "sha256": digest}
        name = (req.query.get("name") or "")[:120]
        return {"token": token, "size": len(data), "sha256": digest[:16], "name": name,
                "created_here": bool(known), "created_at": known.get("t") if known else None, "expires_s": PENDING_TTL}

    def write_temp(data: bytes) -> str:
        d = tempfile.gettempdir() if app.mock else "/tmp"
        path = os.path.join(d, "m3ui-restore-%s.bin" % secrets.token_hex(6))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return path

    @r.route("POST", "/api/system/backup/restore")
    def restore_backup(req):
        """Restore settings from an uploaded backup, then restart (tier 3)."""
        data = validate.only_keys(req.json, ["token", "restart"])
        token = validate.text("token", data.get("token", ""), 8, 64, pattern=r"^[A-Za-z0-9_-]+$")
        restart = validate.boolean("restart", data.get("restart", True))
        with lock:
            prune_pending()
            item = pending.get(token)
        if not item or (item["sid"] and req.session and item["sid"] != req.session.sid):
            raise field_error("token", "The uploaded file has expired. Choose the file again.")
        app.require_tier(req, 3)
        path = write_temp(item["data"])
        try:
            setter("settings_cli", "restore", [path], what="Restoring the settings", timeout=90)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        with lock:
            pending.pop(token, None)
        app.audit(req, "system.backup.restore", {"size": len(item["data"]), "sha256": item["sha256"][:16], "restart": restart})
        out = {"ok": True, "restarting": False, "instance": instance(), "expected_s": 120}
        if restart:
            out["via"] = power(req, "Reboot", delay=0 if app.mock else 2.0)
            out["restarting"] = True
        return out

    # == Restart, shut down, factory reset ====================================================
    def power_now(op: str) -> str:
        try:
            app.rest.put("DeviceState", {"Operation": op})
            return "rest"
        except ApiError as e:
            rest_err = e
        cli, cmd, answers = POWER_FALLBACK[op]
        try:
            setter(cli, cmd, answers, what={"Reboot": "Restart", "Shutdown": "Shut down"}.get(op, "Factory reset"),
                       timeout=20)
            return "cli"
        except ApiError as e:
            raise ApiError(e.status, e.code, "%s (the vendor REST call failed too: %s)" % (e.message, rest_err.message),
                           e.details)

    def power(req, op: str, delay: float = 0.0) -> str:
        if app.mock:
            sim["down_until"] = time.time() + {"Reboot": 8, "Shutdown": 3600, "ResetToFactoryDefaults": 12}[op]
            sim["boot"] += 1
        if delay > 0:
            def later():
                try:
                    power_now(op)
                except ApiError as e:  # pragma: no cover - device only
                    log.error("delayed %s failed: %s", op, e.message)
            t = threading.Timer(delay, later)
            t.daemon = True
            t.start()
            return "scheduled"
        return power_now(op)

    def hw_reset_enabled() -> Optional[bool]:
        res = app.cli_try("factory_reset_cli", "get_hw_reset_sw_enabled")
        v = res.get_int("hw reset sw enabled") if res else None
        return None if v is None else bool(v)

    def schedule() -> Dict[str, Any]:
        d = app.state.read_json(SCHED_FILE, {}) or {}
        days = [x for x in (d.get("days") or []) if x in DAYS]
        return {"enabled": bool(d.get("enabled")), "days": days if d.get("days") is not None else list(DAYS),
                "time": d.get("time") or "03:30", "last_run": d.get("last_run")}

    @r.route("GET", "/api/system/power")
    def get_power(req):
        """Uptime, power-on cause, reset button and scheduled restart."""
        dsm = app.cli_try("dsm_cli", "get_state")
        ui = app.cli_try("dsm_cli", "get_ui_controls_shutdown_enabled")
        up = app.system.uptime()
        return {"uptime_s": int(up), "boot_time": int(time.time() - up), "instance": instance(),
                "power_on_cause": dsm.get("power_on_cause") if dsm else None,
                "power_state": dsm.get("power_state") if dsm else None,
                "reset_button_enabled": hw_reset_enabled(),
                "ui_shutdown_enabled": bool(ui.get_int("ui_controls_shutdown_enabled", 1)) if ui else None,
                "schedule": schedule(), "router_time": time.strftime("%Y-%m-%d %H:%M"),
                "router_timezone": time.strftime("%Z") or "UTC"}

    @r.route("GET", "/api/system/power/ping", auth=False)
    def ping(req):
        """Liveness for the restart waiting screen (public; returns an opaque instance id)."""
        if app.mock and time.time() < sim["down_until"]:
            raise ApiError(503, "restarting", "The router is restarting")
        return {"ok": True, "instance": instance()}

    @r.route("POST", "/api/system/power/reboot")
    def reboot(req):
        """Restart the router (tier 2)."""
        validate.only_keys(req.json, [])
        app.require_tier(req, 2)
        before = instance()
        app.audit(req, "system.power.reboot")
        via = power(req, "Reboot")
        return {"ok": True, "action": "reboot", "via": via, "instance": before, "expected_s": 120}

    @r.route("POST", "/api/system/power/shutdown")
    def shutdown(req):
        """Turn the router off (tier 3; needs the power button to turn it on again)."""
        validate.only_keys(req.json, [])
        app.require_tier(req, 3)
        before = instance()
        app.audit(req, "system.power.shutdown")
        via = power(req, "Shutdown")
        return {"ok": True, "action": "shutdown", "via": via, "instance": before}

    @r.route("POST", "/api/system/power/factory-reset")
    def factory_reset(req):
        """Erase all settings and restart with factory defaults (tier 3)."""
        validate.only_keys(req.json, [])
        app.require_tier(req, 3)
        before = instance()
        app.audit(req, "system.power.factory_reset")
        via = power(req, "ResetToFactoryDefaults")
        return {"ok": True, "action": "factory_reset", "via": via, "instance": before, "expected_s": 180}

    @r.route("PUT", "/api/system/power/reset-button")
    def put_reset_button(req):
        """Enable or disable the hardware reset button (disabling: tier 2)."""
        data = validate.only_keys(req.json, ["enabled"])
        en = validate.boolean("enabled", data.get("enabled"))
        if not en:
            app.require_tier(req, 2)
        setter("factory_reset_cli", "set_hw_reset_sw_enabled", ["1" if en else "0"], what="Changing the reset button")
        after = hw_reset_enabled()
        if after is not None and after != en:
            raise vendor_error("The vendor software did not apply the reset button setting")
        app.audit(req, "system.power.reset_button", {"enabled": en})
        return get_power(req)

    @r.route("PUT", "/api/system/power/schedule")
    def put_schedule(req):
        """Scheduled restart (days of the week + time, router local time)."""
        data = validate.only_keys(req.json, ["enabled", "days", "time"])
        cur = schedule()
        new = dict(cur)
        if "enabled" in data:
            new["enabled"] = validate.boolean("enabled", data["enabled"])
        if "days" in data:
            if not isinstance(data["days"], list):
                raise field_error("days", "Must be a list")
            days = [validate.choice("days", d, DAYS) for d in data["days"]]
            new["days"] = [d for d in DAYS if d in days]
        if "time" in data:
            new["time"] = validate.text("time", data["time"], 4, 5, pattern=r"^([01]?\d|2[0-3]):[0-5]\d$")
            hh, mm = new["time"].split(":")
            new["time"] = "%02d:%s" % (int(hh), mm)
        if new["enabled"] and not new["days"]:
            raise field_error("days", "Choose at least one day")
        app.state.write_json(SCHED_FILE, {"enabled": new["enabled"], "days": new["days"], "time": new["time"],
                                          "last_run": cur.get("last_run")})
        app.audit(req, "system.power.schedule", {"enabled": new["enabled"], "days": new["days"], "time": new["time"]})
        return get_power(req)

    def scheduler_loop():  # pragma: no cover - runs on the device only
        while True:
            time.sleep(30)
            try:
                sched = schedule()
                if schedule_due(sched, time.time(), app.system.uptime()):
                    rec = app.state.read_json(SCHED_FILE, {}) or {}
                    rec["last_run"] = int(time.time())
                    app.state.write_json(SCHED_FILE, rec)    # first, so a failure cannot loop
                    app.audit(None, "system.power.scheduled_reboot")
                    power_now("Reboot")
            except Exception as e:
                log.warning("scheduled restart check failed: %s", e)

    if not app.mock:
        threading.Thread(target=scheduler_loop, name="m3ui-reboot-schedule", daemon=True).start()

    # == Logs =================================================================================
    def marks() -> Dict[str, Any]:
        return app.state.read_json(MARKS_FILE, {}) or {}

    def vendor_raw(source: str) -> List[str]:
        if app.mock:
            return mock_mod().sample_vendor_log(source)
        base = VENDOR_LOGS[source][0]
        files = rotated_files(base)
        if not files:
            raise unavailable("The vendor log file %s does not exist on this router" % base)
        chunks: List[List[str]] = []
        budget = LOG_READ_BUDGET
        for p in reversed(files):              # newest first until the budget is used
            lines, used = read_tail_lines(p, budget)
            chunks.insert(0, lines)
            budget -= used
            if budget <= 0:
                break
        return [ln for c in chunks for ln in c]

    def text_entries(lines: List[str]) -> List[Dict[str, Optional[str]]]:
        out = []
        for ln in lines:
            t, msg = split_time(ln)
            out.append({"time": t, "text": msg})
        return out

    def sessions_rows(limit: int) -> List[Dict[str, Any]]:
        if app.mock:
            return mock_mod().sample_sessions()[:limit]
        if not os.path.isfile(DMDB):
            raise unavailable("The session history database does not exist on this router")
        rows: List[Dict[str, Any]] = []
        try:
            con = sqlite3.connect("file:%s?mode=ro" % DMDB, uri=True, timeout=2)
        except sqlite3.Error as e:
            raise unavailable("The session history database could not be opened: %s" % e)
        try:
            for table, iface in (("DataSessions", "mobile"), ("EthDataSessions", "ethernet")):
                cols = "StartDate, EndDate, MonoStart, MonoEnd, IPv4, IPv6, %s, BytesTXIPv4, BytesRXIPv4, BytesTXIPv6, " \
                       "BytesRXIPv6" % ("Roaming" if table == "DataSessions" else "0")
                try:
                    cur = con.execute("SELECT %s FROM %s ORDER BY ID DESC LIMIT ?" % (cols, table), (limit,))
                except sqlite3.Error:
                    continue
                for (start, end, m0, m1, v4, v6, roam, tx4, rx4, tx6, rx6) in cur.fetchall():
                    dur = (m1 - m0) if isinstance(m0, int) and isinstance(m1, int) and m1 >= m0 else None
                    rows.append({"start": start, "end": end, "duration_s": dur, "ipv4": v4 or "", "ipv6": v6 or "",
                                 "roaming": ROAM.get(roam, "home") if iface == "mobile" else None,
                                 "rx_bytes": (rx4 or 0) + (rx6 or 0), "tx_bytes": (tx4 or 0) + (tx6 or 0), "interface": iface})
        finally:
            con.close()
        rows.sort(key=lambda x: str(x.get("start") or ""), reverse=True)
        return rows[:limit]

    def log_lines(source: str, lines: int, query: str) -> Dict[str, Any]:
        """Entries of one log source (newest last), filtered by `query`."""
        out: Dict[str, Any] = {"source": source, "title": dict((s, t) for s, t, _d in LOG_SOURCES)[source],
                               "generated": int(time.time())}
        q = query.lower()
        if source == "sessions":
            rows = sessions_rows(min(lines, 500))
            if q:
                rows = [x for x in rows if q in " ".join(str(v) for v in x.values()).lower()]
            out.update(rows=rows, count=len(rows))
            return out
        if source == "audit":
            recs = app.state.audit_tail(lines)
            ents = [{"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(x.get("t", 0))),
                     "text": "%s  %s%s" % (x.get("actor", ""), x.get("action", ""),
                                           ("  " + ", ".join("%s=%s" % (k, v) for k, v in sorted((x.get("details") or {}).items())))
                                           if x.get("details") else "")} for x in recs]
        elif source == "system":
            res = app.system.journal(lines=max(10, min(lines, 2000)))
            if not res.get("ok") and not res.get("output"):
                raise unavailable("The system journal is not available")
            ents = text_entries([ln for ln in res.get("output", "").splitlines() if ln.strip()])
        elif source == "kernel":
            if app.mock:
                text = mock_mod().sample_dmesg()
            else:
                res = app.system.run(["dmesg"], timeout=10, max_bytes=512 * 1024)
                if not res.get("ok"):
                    raise unavailable("The kernel log is not available")
                text = res.get("output", "")
            ents = text_entries([ln for ln in text.splitlines() if ln.strip()])
        else:
            _path, tag, stock_marker = VENDOR_LOGS[source]
            raw = vendor_raw(source)
            mk = marks().get(source)
            ev = vendor_event_lines(raw, tag, stock_marker, mk.get("line") if isinstance(mk, dict) else None)
            ents = []
            for ln in ev:
                t, rest = split_time(ln)
                ents.append({"time": t, "text": rest.split(tag, 1)[1].strip() if tag in rest else rest})
            out["cleared_at"] = mk.get("t") if isinstance(mk, dict) else None
        total = len(ents)
        if q:
            ents = [e for e in ents if q in (e.get("text") or "").lower() or q in (e.get("time") or "").lower()]
        out.update(entries=ents[-lines:], count=min(len(ents), lines), total=total, truncated=len(ents) > lines)
        return out

    def source_arg(req) -> str:
        s = req.params.get("source", "")
        if s not in [x[0] for x in LOG_SOURCES]:
            raise not_found("Unknown log: %s" % s)
        return s

    def query_args(req) -> Tuple[int, str]:
        n = validate.integer("lines", req.query.get("lines", 500), 10, 5000)
        q = validate.text("q", req.query.get("q", ""), 0, 100)
        return n, q

    @r.route("GET", "/api/system/logs")
    def get_logs(req):
        """Available log sources."""
        return {"sources": [{"id": s, "title": t, "description": d, "clearable": s in VENDOR_LOGS,
                             "downloadable": True} for s, t, d in LOG_SOURCES]}

    @r.route("GET", "/api/system/logs/{source}")
    def get_log(req):
        """Entries of one log (query: lines, q)."""
        n, q = query_args(req)
        return log_lines(source_arg(req), n, q)

    @r.route("GET", "/api/system/logs/{source}/download")
    def download_log(req):
        """Download one log as a text (or CSV) file."""
        source = source_arg(req)
        n, q = query_args(req)
        data = log_lines(source, max(n, 2000) if source != "sessions" else 500, q)
        stamp = time.strftime("%Y%m%d-%H%M")
        if source == "sessions":
            cols = ["start", "end", "duration_s", "interface", "roaming", "ipv4", "ipv6", "rx_bytes", "tx_bytes"]
            lines = [",".join(cols)] + [",".join('"%s"' % str("" if row.get(c) is None else row.get(c)).replace('"', "'")
                                                 for c in cols) for row in data["rows"]]
            body, ctype, ext = ("\r\n".join(lines) + "\r\n").encode("utf-8"), "text/csv; charset=utf-8", "csv"
        else:
            body = ("\n".join(("%s  %s" % (e["time"], e["text"])) if e.get("time") else e["text"]
                              for e in data["entries"]) + "\n").encode("utf-8")
            ctype, ext = "text/plain; charset=utf-8", "txt"
        app.audit(req, "system.logs.download", {"source": source})
        return Response(body, ctype, headers={"Content-Disposition": 'attachment; filename="%s-%s.%s"' % (source, stamp, ext)})

    @r.route("POST", "/api/system/logs/{source}/clear")
    def clear_log(req):
        """Hide older vendor event-log entries in this interface (nothing is deleted)."""
        source = source_arg(req)
        if source not in VENDOR_LOGS:
            raise field_error("source", "This log cannot be cleared")
        raw = vendor_raw(source)
        mk = marks()
        mk[source] = {"t": int(time.time()), "line": raw[-1] if raw else ""}
        app.state.write_json(MARKS_FILE, mk)
        app.audit(req, "system.logs.clear", {"source": source})
        return log_lines(source, 500, "")

    @r.route("POST", "/api/system/logs/{source}/unclear")
    def unclear_log(req):
        """Show the entries hidden by 'clear' again."""
        source = source_arg(req)
        mk = marks()
        mk.pop(source, None)
        app.state.write_json(MARKS_FILE, mk)
        return log_lines(source, 500, "")

    # -- log settings -----------------------------------------------------------------------
    def log_settings_view() -> Dict[str, Any]:
        ev = app.cli_try("webui_cli", "get_log_settings")
        return {"event_log": None if not ev else {"enabled": bool(ev.get_int("log_enabled", 0)),
                                                  "life_days": ev.get_int("log_life_time(in days)")}}

    @r.route("GET", "/api/system/log-settings")
    def get_log_settings(req):
        """Vendor event-log settings (stock Logs page)."""
        return log_settings_view()

    @r.route("PUT", "/api/system/log-settings")
    def put_log_settings(req):
        """Vendor event log: enabled and how many days entries are kept."""
        data = validate.only_keys(req.json, ["event_log_enabled", "event_log_days"])
        cur = log_settings_view()["event_log"]
        if cur is None:
            raise unavailable("The vendor log settings are not available")
        en = validate.boolean("event_log_enabled", data.get("event_log_enabled", cur["enabled"]))
        days = validate.integer("event_log_days", data.get("event_log_days", cur["life_days"] or 3), 1, 4)
        setter("webui_cli", "set_log_settings", ["1" if en else "0", str(days)], what="Saving the log settings")
        app.audit(req, "system.log_settings.update", {"enabled": en, "days": days})
        return log_settings_view()

    @r.route("POST", "/api/system/support-bundle")
    def support_bundle(req):
        """Create and download the vendor diagnostic bundle (unencrypted; admin password required)."""
        validate.only_keys(req.json, [])
        pw = req.field("password", "")
        app.auth.reauth(pw if isinstance(pw, str) else None, req.client_ip)
        if not bundle_lock.acquire(blocking=False):
            raise conflict("A support bundle is already being created. Try again in a minute.")
        try:
            if app.mock:
                data = mock_mod().sample_bundle()
            else:
                out = "/tmp/m3ui-support-%s.tgz" % secrets.token_hex(4)
                res = app.system.run(["/opt/nvtl/bin/storelogs.sh", out], timeout=300, max_bytes=64 * 1024)
                try:
                    size = os.path.getsize(out)
                except OSError:
                    raise vendor_error("The support bundle could not be created", {"output": (res.get("output") or "")[-1500:]})
                try:
                    if size > 48 * 1024 * 1024:
                        raise vendor_error("The support bundle is too large to download here (%d MB). Copy %s with adb."
                                           % (size // 1048576, out))
                    with open(out, "rb") as f:
                        data = f.read()
                finally:
                    if size <= 48 * 1024 * 1024:
                        try:
                            os.remove(out)
                        except OSError:
                            pass
        finally:
            bundle_lock.release()
        app.audit(req, "system.support_bundle", {"size": len(data)})
        name = "support-bundle-%s.tgz" % time.strftime("%Y%m%d-%H%M")
        return Response(data, "application/gzip", headers={"Content-Disposition": 'attachment; filename="%s"' % name})
