"""Expert tools: AT console, vendor CLI explorer (read-only commands only), message-bus viewer, NV & EFS viewer.

Every /api/expert/* route also requires the UI's expert mode (defence in depth: the pages are hidden otherwise).
Nothing here writes to the modem's EFS/NV: EFS files are read with `nwcli qmi_idl read_file` into
/dev/shm/m3ui-*, read back and deleted; named NV items with `nwnvitem -r -e <NAME>`.
Feature map: docs/features/tools.md.
"""
import base64
import hashlib
import io
import json
import os
import re
import secrets
import threading
import time
import zipfile
import zlib
from typing import Any, Dict, List, Optional, Tuple

from .. import validate
from ..adapters import at as at_mod
from ..adapters import cli_specs
from ..adapters.cli import NAME_RE
from ..errors import ApiError, field_error, forbidden, unavailable, vendor_error
from ..httpd import Response

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cli_commands.json")
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
EFS_RE = re.compile(r"^/[A-Za-z0-9_./$-]{1,200}$")
MAX_EFS = 262144

# Known EFS files (read-only presets).  decoder: lte_bands | lte_bands_ext | nr_bands | flag | zlib | text | auto
EFS_PRESETS: List[Dict[str, Any]] = [
    {"path": "/nv/item_files/modem/mmode/lte_bandpref", "label": "LTE band preference, bands 1-64",
     "decoder": "lte_bands", "max": 64,
     "help": "8-byte little-endian bit mask: bit n enables LTE band n+1. Change bands under Cellular > Bands."},
    {"path": "/nv/item_files/modem/mmode/lte_bandpref_extn_65_256", "label": "LTE band preference, bands 65-256",
     "decoder": "lte_bands_ext", "max": 64, "help": "24-byte bit mask: bit n enables LTE band 65+n (bit 1 = B66)."},
    {"path": "/nv/item_files/modem/mmode/nr5g_band_pref", "label": "5G NR band preference (if present)",
     "decoder": "nr_bands", "max": 128, "help": "Bit mask: bit n enables NR band n+1. May not exist on this firmware."},
    {"path": "/nv/item_files/modem/mmode/nr5g_sa_band_pref", "label": "5G NR SA band preference (if present)",
     "decoder": "nr_bands", "max": 128, "help": "Bit mask: bit n enables NR band n+1. May not exist on this firmware."},
    {"path": "/nvm/num/60002", "label": "Carrier-lock flag (NV 60002)", "decoder": "flag", "max": 64,
     "help": "00 = no carrier-lock flag set. Third-party unlock ROMs write 00 here; this viewer never writes."},
    {"path": "/nvm/num/60076", "label": "Carrier-lock flag 2 (NV 60076)", "decoder": "flag", "max": 64,
     "help": "Second lock flag used by unlock ROMs; absent on the reference unit."},
    {"path": "/factory_backup/item_list.save", "label": "Factory-reset restore list", "decoder": "auto", "max": 16384,
     "help": "Items the modem restores after a factory reset (about 2.4 KB)."},
    {"path": "/novatel/$$NW_FACTORY_BACKUP$$", "label": "Vendor factory backup", "decoder": "auto", "max": 131072,
     "help": "Vendor backup used for factory recovery (about 100 KB)."},
    {"path": "/nv/item_files/rfnv/00029002", "label": "RF NV 29002 (band enablement)", "decoder": "zlib", "max": 65536,
     "help": "Compressed RF calibration item. Read and back up only; payloads from other models must never be written."},
    {"path": "/nv/item_files/rfnv/00028967", "label": "RF NV 28967 (carrier aggregation)", "decoder": "zlib", "max": 65536,
     "help": "Compressed RF item for carrier-aggregation combinations. Read and back up only."},
]
# NV items whose content is a secret: never read, by EFS path (/nvm/num/<id>) or by name.
BLOCKED_NV = {85: "the service programming code", 60004: "the carrier unlock code", 60253: "the Linux root password"}
# Named NV items nwnvitem understands (its table at .data file-off 0x2008, 14 x {char name[52]; u32 id}).
# `-e` is matched by name prefix only (numeric ids print "NV Item not supported"); the root-password item is left out.
NV_ITEMS: List[Tuple[str, int, str, str]] = [
    ("NW_NV_MAC_ID_I", 60254, "Wi-Fi MAC address, first interface", "mac"),
    ("NW_NV_MAC_ID_2_I", 60302, "Wi-Fi MAC address, second interface", "mac"),
    ("NW_NV_MAC_ID_3_I", 60305, "Wi-Fi MAC address, third interface", "mac"),
    ("NW_NV_MAC_ID_4_I", 60306, "Wi-Fi MAC address, fourth interface", "mac"),
    ("NW_NV_ETHERNET_MAC_ID_I", 550, "Ethernet MAC address (as HI16/LO32 register values)", "text"),
    ("NW_NV_USB_MAC_ID_I", 550, "USB network MAC address", "text"),
    ("NW_NV_LINUX_RUN_LEVEL_I", 60255, "Linux run level (flag, level)", "runlevel"),
    ("NW_NV_USB_DEFAULT_MODE_I", 60256, "Default USB mode (EUM = normal, DEBUG = debug ports)", "usbmode"),
    ("NW_NV_PSM_DEFAULT_MODE_I", 60258, "Power-save default mode", "number"),
    ("NW_NV_PRI_INFORMATION_I", 60044, "Carrier configuration (PRI) information", "text"),
    ("NW_NV_CARRIER_INFO_I", 60111, "Carrier information", "text"),
    ("NW_NV_ASYNC_DATA_ORIG_STR_I", 60104, "Async data origination string (printed as 6 bytes)", "text"),
]
NV_BY_NAME = {n[0]: n for n in NV_ITEMS}
NV_ERRORS = ("NV item inactive or read error", "NV Item not supported", "Failed to open QMI", "Error:")


# -- parsers / decoders (module level for tests) -------------------------------------------------------------------
def band_bits(data: bytes, base: int = 1) -> List[int]:
    return [base + i * 8 + b for i, byte in enumerate(bytearray(data)) for b in range(8) if (byte >> b) & 1]


def _printable(data: bytes) -> bool:
    body = data.rstrip(b"\x00")
    return bool(body) and all(32 <= c < 127 or c in (9, 10, 13) for c in bytearray(body))


def decode_efs(data: bytes, decoder: str = "auto", path: str = "") -> Dict[str, Any]:
    """Human-readable views of an EFS file: band lists, flags, zlib payloads, text."""
    kind = decoder or "auto"
    if kind == "auto":
        if path.endswith("lte_bandpref"):
            kind = "lte_bands"
        elif path.endswith("lte_bandpref_extn_65_256"):
            kind = "lte_bands_ext"
        elif "nr5g" in path and path.endswith("band_pref"):
            kind = "nr_bands"
        elif len(data) >= 2 and data[0] == 0x78 and data[1] in (0x01, 0x5E, 0x9C, 0xDA):
            kind = "zlib"
        elif len(data) == 1:
            kind = "flag"
        elif _printable(data):
            kind = "text"
        else:
            kind = "hex"
    out: Dict[str, Any] = {"kind": kind}
    if kind == "lte_bands":
        out["bands"] = band_bits(data, 1)
        out["summary"] = "LTE bands enabled: " + (", ".join("B%d" % b for b in out["bands"]) or "none")
    elif kind == "lte_bands_ext":
        out["bands"] = band_bits(data, 65)
        out["summary"] = "LTE bands 65-256 enabled: " + (", ".join("B%d" % b for b in out["bands"]) or "none")
    elif kind == "nr_bands":
        out["bands"] = band_bits(data, 1)
        out["summary"] = "NR bands enabled: " + (", ".join("n%d" % b for b in out["bands"]) or "none")
    elif kind == "flag":
        v = bytearray(data)[0] if data else None
        out["value"] = v
        out["summary"] = ("Flag value %02x" % v) + (" (not set)" if v == 0 else " (set)") if v is not None else "Empty file"
    elif kind == "zlib":
        try:
            inflated = zlib.decompress(data)
            out.update(inflated_size=len(inflated), inflated_b64=base64.b64encode(inflated[:65536]).decode("ascii"),
                       summary="zlib-compressed: %d bytes -> %d bytes" % (len(data), len(inflated)))
        except zlib.error as e:
            out["summary"] = "Not a valid zlib stream (%s)" % e
    elif kind == "text":
        out["text"] = data.rstrip(b"\x00").decode("latin-1")
        out["summary"] = "Text, %d lines" % (out["text"].count("\n") + 1)
    else:
        out["summary"] = "Binary data, %d bytes" % len(data)
    return out


def parse_efs_read(text: str) -> Dict[str, Any]:
    """nwcli qmi_idl read_file output (handler @0x3cfc / read_efs_file @0x2604)."""
    m = re.search(r"File read success, \(Total bytes read:(\d+)\)", text or "")
    if m:
        return {"ok": True, "size": int(m.group(1))}
    q = re.search(r"File read QMI failure:(-?\d+)", text or "")
    err = "QMI error %s" % q.group(1) if q else None
    for pat in (r"Cannot allocate memory[^\n]*", r"read_efs_file\(\) failed, rc=\[-?\d+\]", r"nwqmi_\w+\(\) failed[^\n]*",
                r"Usage:[^\n]*", r"command \[[^\]]*\] not found"):
        mm = re.search(pat, text or "")
        if mm and not err:
            err = mm.group(0)
    return {"ok": False, "error": err or "no answer from the modem file service"}


def parse_msgbus(text: str) -> Dict[str, Any]:
    """msgbus_cli MsgBusGet output (handler @0x2564): RC line, last updated time, data type, data."""
    out: Dict[str, Any] = {"ok": False, "rc": None, "type": None, "updated": "", "value": None}
    rcs = re.findall(r"MsgBusGet returned (-?\d+) \(([^)]*)\)", text or "")
    if rcs:
        out["rc"], out["message"] = int(rcs[-1][0]), rcs[-1][1]
        out["ok"] = out["rc"] == 0
    m = re.search(r"last updated time:\[([^\]]*)\]", text or "")
    if m:
        out["updated"] = m.group(1).strip()
    m = re.search(r"data type:\[([^\]\n]*)\]?", text or "")
    if m:
        out["type"] = m.group(1).strip().rstrip("]")
    t = out["type"] or ""
    if t == "binary":
        n = re.search(r"len:\[(\d+)\]", text)
        out["length"] = int(n.group(1)) if n else 0
        i = text.find("\ndata:")
        hexpart = text[i + 6:] if i >= 0 else ""
        out["hex"] = " ".join(re.findall(r"\b[0-9a-fA-F]{2}\b", hexpart))[: out["length"] * 3]
        try:
            raw = bytes(bytearray(int(x, 16) for x in out["hex"].split()))
            out["value"] = raw.decode("latin-1") if _printable(raw) else None
        except ValueError:
            pass
    elif t == "string":
        i = text.find("data:[", text.find("data type:"))
        j = text.rfind("]")
        out["value"] = text[i + 6:j] if i >= 0 and j > i else ""
    elif t:
        m = re.search(r"\ndata:\[([^\]]*)\]", text)
        if m:
            v = m.group(1).strip()
            try:
                out["value"] = int(v) if t in ("int32", "int64") else float(v)
            except ValueError:
                out["value"] = v
    return out


def parse_nv(text: str) -> Dict[str, Any]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    err = next((ln for ln in lines if ln.startswith(NV_ERRORS)), None)
    if err:
        return {"ok": False, "error": err, "value": ""}
    return {"ok": bool(lines), "value": lines[-1] if lines else "", "lines": lines}


def interpret_nv(kind: str, value: str) -> str:
    v = (value or "").strip()
    if kind == "usbmode":
        return {"EUM": "Normal (end-user) USB mode", "DEBUG": "Debug USB mode (diagnostic / ADB ports)"}.get(v, v)
    if kind == "runlevel":
        parts = v.split()
        if len(parts) == 2:
            return "Flag %s, run level %s" % (parts[0], parts[1])
    if kind == "mac" and re.match(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", v):
        return "MAC address " + v.upper()
    return v


def audit_command(cmd: str) -> str:
    """AT command for the audit log without arguments (PINs, passwords, phone numbers stay out of the log)."""
    return ";".join(re.sub(r"=.*$", "=...", p) for p in cmd.split(";"))


def blocked_reason(path: str) -> Optional[str]:
    m = re.match(r"^/nvm/num/0*(\d{1,6})$", path)
    if m and int(m.group(1)) in BLOCKED_NV:
        return BLOCKED_NV[int(m.group(1))]
    return None


def register(app):
    r = app.router
    efs_lock = threading.Lock()
    catalogue_cache: Dict[str, Any] = {}

    def need_expert():
        if not app.settings().get("expert_mode"):
            raise ApiError(403, "expert_required",
                           "Expert tools are turned off. Turn on expert mode in the UI settings to use them.")

    # -- AT console --------------------------------------------------------------------------------------------
    @r.route("GET", "/api/expert/at")
    def at_info(req):
        """AT console: transport in use, quick (read-only) commands and the command policy."""
        need_expert()
        tr = app.at.transport()
        return {
            "transport": tr,
            "transport_note": ("read_atcmd on /dev/smd7" if tr == "smd" else
                               "modem2_cli run_raw_command (this firmware's modem service may not implement it)"),
            "quick": [{"command": c, "label": lab} for c, lab in at_mod.QUICK],
            "policy": {"deny": [[p, why] for p, why in at_mod.DENY_RULES],
                       "risky": [[p, why] for p, why in at_mod.RISKY_RULES],
                       "sensitive": [[p, why] for p, why in at_mod.SENSITIVE_RULES],
                       "tiers": {"ok": 0, "risky": 2, "write": 3}},
            "max_timeout_s": 60,
        }

    @r.route("POST", "/api/expert/at/classify")
    def at_classify(req):
        """How a command would be treated (read-only / confirm / password / blocked) without sending it."""
        need_expert()
        data = validate.only_keys(req.json, ["command"])
        cmd = at_mod.normalise(data.get("command"))
        out = at_mod.describe(cmd)
        out["command"] = cmd
        return out

    @r.route("POST", "/api/expert/at")
    def at_send(req):
        """Send one AT command (queries free; radio/network changes tier 2; other writes tier 3; red-line commands blocked)."""
        need_expert()
        data = validate.only_keys(req.json, ["command", "timeout"])
        cmd = at_mod.normalise(data.get("command"))
        info = at_mod.describe(cmd)
        if info["classification"] == "deny":
            raise forbidden("This AT command is blocked (%s)." % info["reason"].replace("blocked: ", ""), "at_denied")
        timeout = validate.integer("timeout", data.get("timeout", 10), 1, 60)
        tier = info["tier"] or 0
        if tier >= 2:
            body = req.json if isinstance(req.json, dict) else {}
            if body.get("confirm") is not True:
                raise ApiError(428, "confirm_required", "Send %s? It affects %s." % (cmd, info["reason"]),
                               {"tier": tier, "classification": info["classification"]})
            app.require_tier(req, tier)
        res = app.at.send(cmd, allow_risky=True, timeout=timeout)
        if tier >= 2:
            app.audit(req, "expert.at." + info["classification"], {"command": audit_command(cmd), "ok": bool(res.get("ok"))})
        res["tier"] = tier
        return res

    # -- CLI explorer ------------------------------------------------------------------------------------------
    def catalogue() -> Dict[str, List[Dict[str, Any]]]:
        if "clis" not in catalogue_cache:
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    catalogue_cache["clis"] = json.load(f).get("clis", {})
            except (OSError, ValueError) as e:
                raise unavailable("The CLI command list is missing (%s)" % e)
        return catalogue_cache["clis"]

    def kind_of(cli: str, cmd: str) -> str:
        try:
            return cli_specs.classify(cli, cmd)
        except Exception:  # never let a policy oddity break the listing
            return "excluded"

    @r.route("GET", "/api/expert/cli")
    def cli_list(req):
        """Vendor CLIs with their number of commands and how many are read-only (runnable here)."""
        need_expert()
        out = []
        for cli, rows in sorted(catalogue().items()):
            kinds = [kind_of(cli, row["name"]) for row in rows]
            out.append({"cli": cli, "commands": len(rows), "runnable": kinds.count("get"),
                        "excluded": kinds.count("excluded"), "setters": kinds.count("setter")})
        return {"clis": out}

    @r.route("GET", "/api/expert/cli/{cli}")
    def cli_commands(req):
        """Commands of one vendor CLI with help text, classification (get/setter/excluded) and known prompts."""
        need_expert()
        cli = req.params["cli"]
        rows = catalogue().get(cli)
        if rows is None:
            raise ApiError(404, "not_found", "Unknown CLI %s" % cli)
        out = []
        for row in rows:
            item = dict(row)
            item["kind"] = kind_of(cli, row["name"])
            sp = cli_specs.spec(cli, row["name"])
            item["spec"] = sp is not None
            if sp is not None:
                item["spec_source"] = sp.source
            out.append(item)
        return {"cli": cli, "commands": out}

    @r.route("POST", "/api/expert/cli/run")
    def cli_run(req):
        """Run one read-only vendor CLI command (optional stdin answers) and show raw output + parsed pairs."""
        need_expert()
        data = validate.only_keys(req.json, ["cli", "command", "answers", "timeout"])
        cli = data.get("cli")
        cmd = data.get("command")
        if not isinstance(cli, str) or not NAME_RE.match(cli) or not (cli.endswith("cli") or cli in cli_specs.EXTRA_TOOLS):
            raise field_error("cli", "Choose a vendor CLI")
        if not isinstance(cmd, str) or not NAME_RE.match(cmd):
            raise field_error("command", "Enter a command name (letters, digits, underscores)")
        answers = data.get("answers") or []
        if not isinstance(answers, list) or len(answers) > 8:
            raise field_error("answers", "Up to 8 answers, one per prompt")
        answers = [validate.text("answers", a if isinstance(a, str) else str(a), 0, 256) for a in answers]
        timeout = validate.integer("timeout", data.get("timeout", 15), 1, 60)
        kind = kind_of(cli, cmd)
        if kind != "get":
            raise forbidden("Only read-only commands run here; %s %s is classified as %s." % (cli, cmd, kind),
                            "not_read_only", {"kind": kind})
        t0 = time.time()
        try:
            res = app.cli.run(cli, cmd, answers, timeout=timeout)
        except ValueError as e:
            raise field_error("cli", str(e))
        out = res.to_json()
        out["pairs"] = [[k, v, n] for k, v, n in res.pairs][:1000]
        out["answers"] = answers
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        return out

    # -- message bus -----------------------------------------------------------------------------------------
    def channels() -> List[str]:
        res = app.cli_try("msgbus_cli", "print_channels")
        if not res:
            raise unavailable("The message bus did not answer.")
        return res.indexed("list")

    def channel_value(name: str) -> Dict[str, Any]:
        res = app.cli.run("msgbus_cli", "MsgBusGet", [name], timeout=8)
        out = parse_msgbus(res.text)
        out["channel"] = name
        if res.timed_out:
            out["error"] = "timed out"
        return out

    def _channel(name: Any) -> str:
        if not isinstance(name, str) or not CHANNEL_RE.match(name):
            raise field_error("channel", "Enter a channel name such as modem2.conn_state")
        return name

    @r.route("GET", "/api/expert/msgbus/channels")
    def msgbus_channels(req):
        """Active message-bus channels (msgbus_cli print_channels)."""
        need_expert()
        names = channels()
        return {"channels": names, "count": len(names)}

    @r.route("GET", "/api/expert/msgbus/value")
    def msgbus_value(req):
        """Current value of one channel (?channel=name): type, last update time, data."""
        need_expert()
        return channel_value(_channel(req.query.get("channel")))

    @r.route("GET", "/api/expert/msgbus/values")
    def msgbus_values(req):
        """Values of several channels (?channels=a,b,c or ?channels=all, at most 64)."""
        need_expert()
        raw = str(req.query.get("channels", "")).strip()
        names = channels() if raw == "all" else [n.strip() for n in raw.split(",") if n.strip()]
        if not names:
            raise field_error("channels", "List one or more channels")
        if len(names) > 64:
            raise field_error("channels", "At most 64 channels at a time")
        t0 = time.time()
        values = {n: channel_value(_channel(n)) for n in names}
        return {"values": values, "elapsed_ms": int((time.time() - t0) * 1000)}

    # -- EFS / NV viewer -----------------------------------------------------------------------------------------
    def _efs_path(p: Any) -> str:
        if not isinstance(p, str) or not p.strip():
            raise field_error("path", "Enter an EFS file path")
        p = p.strip()
        if not EFS_RE.match(p) or ".." in p or "//" in p or "/./" in p or p.endswith("/"):
            raise field_error("path", "Enter an absolute EFS file path such as /nv/item_files/modem/mmode/lte_bandpref")
        why = blocked_reason(p)
        if why:
            raise forbidden("This item holds %s and is not shown." % why, "secret_item")
        return p

    def _max(v: Any, default: int) -> int:
        return validate.integer("max", v if v not in (None, "") else default, 1, MAX_EFS)

    def _take_local(local: str) -> Optional[bytes]:
        if app.mock:
            return app.cli.store.state.setdefault("shm", {}).pop(local, None)
        try:
            with open(local, "rb") as f:
                return f.read()
        except OSError:
            return None
        finally:
            try:
                os.unlink(local)
            except OSError:
                pass

    def read_efs(path: str, max_bytes: int) -> Tuple[bytes, Dict[str, Any]]:
        local = "/dev/shm/m3ui-efs-%s" % secrets.token_hex(6)
        with efs_lock:
            t0 = time.time()
            try:
                res = app.cli.run("nwcli", "qmi_idl", (), timeout=25, args=["read_file", local, path, str(max_bytes)])
            finally:
                data = _take_local(local)
        st = parse_efs_read(res.text)
        if not st["ok"]:
            raise unavailable("The modem could not read %s: %s" % (path, st["error"]), {"path": path})
        if data is None or len(data) != st["size"]:
            raise vendor_error("%s was read but the copy could not be collected (%s of %s bytes)"
                               % (path, "no" if data is None else len(data), st["size"]), {"path": path})
        return data, {"elapsed_ms": int((time.time() - t0) * 1000), "possibly_truncated": st["size"] >= max_bytes}

    def preset_for(path: str) -> Optional[Dict[str, Any]]:
        return next((p for p in EFS_PRESETS if p["path"] == path), None)

    @r.route("GET", "/api/expert/efs/presets")
    def efs_presets(req):
        """Known EFS files worth inspecting (band preferences, lock flags, factory backup, RF items)."""
        need_expert()
        return {"presets": EFS_PRESETS, "max_bytes": MAX_EFS,
                "blocked": [{"nv": k, "path": "/nvm/num/%d" % k, "reason": v} for k, v in sorted(BLOCKED_NV.items())]}

    @r.route("GET", "/api/expert/efs")
    def efs_read(req):
        """Read one EFS file (?path=..., ?max=bytes up to 262144) as base64 plus decoded views. Never writes."""
        need_expert()
        path = _efs_path(req.query.get("path"))
        pre = preset_for(path)
        max_bytes = _max(req.query.get("max"), pre["max"] if pre else 65536)
        data, meta = read_efs(path, max_bytes)
        out = {"path": path, "size": len(data), "max": max_bytes, "data_b64": base64.b64encode(data).decode("ascii"),
               "sha256": hashlib.sha256(data).hexdigest(), "decoded": decode_efs(data, pre["decoder"] if pre else "auto", path),
               "preset": pre}
        out.update(meta)
        return out

    @r.route("GET", "/api/expert/efs/download")
    def efs_download(req):
        """Download one EFS file as binary (?path=..., ?max=...)."""
        need_expert()
        path = _efs_path(req.query.get("path"))
        pre = preset_for(path)
        data, _meta = read_efs(path, _max(req.query.get("max"), pre["max"] if pre else 65536))
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.strip("/")).strip("_") + ".bin"
        return Response(data, "application/octet-stream",
                        headers={"Content-Disposition": 'attachment; filename="%s"' % name})

    @r.route("GET", "/api/expert/efs/backup")
    def efs_backup(req):
        """ZIP backup of every preset EFS file that can be read, with a manifest (sizes, SHA-256, errors)."""
        need_expert()
        buf = io.BytesIO()
        manifest = {"created": int(time.time()), "files": []}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in EFS_PRESETS:
                rec = {"path": p["path"], "label": p["label"]}
                try:
                    data, _m = read_efs(p["path"], p["max"])
                    z.writestr(p["path"].lstrip("/"), data)
                    rec.update(ok=True, size=len(data), sha256=hashlib.sha256(data).hexdigest())
                except ApiError as e:
                    rec.update(ok=False, error=e.message)
                manifest["files"].append(rec)
            z.writestr("manifest.json", json.dumps(manifest, indent=1))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return Response(buf.getvalue(), "application/zip",
                        headers={"Content-Disposition": 'attachment; filename="efs-backup-%s.zip"' % stamp})

    @r.route("GET", "/api/expert/nv")
    def nv_list(req):
        """Named NV items that can be read (nwnvitem -r -e NAME)."""
        need_expert()
        return {"items": [{"name": n, "id": i, "label": lab, "kind": k} for n, i, lab, k in NV_ITEMS],
                "by_number": "/nvm/num/<id> through the EFS viewer",
                "blocked": [{"nv": k, "reason": v} for k, v in sorted(BLOCKED_NV.items())]}

    @r.route("GET", "/api/expert/nv/{name}")
    def nv_read(req):
        """Read one named NV item (?index=n for array items)."""
        need_expert()
        name = req.params["name"]
        item = NV_BY_NAME.get(name)
        if item is None:
            raise field_error("name", "Choose one of the listed NV items")
        args = ["-e", name]
        idx = req.query.get("index")
        if idx not in (None, ""):
            args += ["-i", str(validate.integer("index", idx, 0, 255))]
        res = app.cli.run("nwnvitem", "-r", (), timeout=10, args=args)
        st = parse_nv(res.text)
        if not st["ok"]:
            raise unavailable("NV item %s: %s" % (name, st.get("error") or "no value"), {"name": name})
        return {"name": name, "id": item[1], "label": item[2], "value": st["value"], "output": res.text.strip(),
                "meaning": interpret_nv(item[3], st["value"])}
