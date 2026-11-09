"""VPN client: OpenVPN (uploaded .ovpn profile), WireGuard (3 tunnels), IPsec (6 tunnels) - vpn_cli.

Facts from the binaries (vpn_cli, libvpn_api.so, vpnd, stock vpn app.so):
- server types: 0 none, 1 PPTP, 2 OpenVPN (manual), 3 L2TP, 4 IPsec, 5 OpenVPN with config files,
  6 WireGuard.  The stock UI only ever used 5.  vpn_get_setting() rejects 1 and 3, every
  vpn_cli prompt refuses empty answers (so the optional certificate fields of type 2 cannot
  be left blank): types 1-3 are not offered.
- `get_settings` and `get_connection_statistics` prompt for the type (and a tunnel index for
  4/6) in fgets loops that spin on EOF, so they are always fed answers (+1 spare line).
- the stock UI stages uploaded OpenVPN files in /tmp/vpnFiles; vpnd copies "<path>/*" into
  /opt/nvtl/data/vpn/openvpn/ and runs `openvpn --cd /opt/nvtl/tmp/vpn/openvpn/ --config <name>`.
  vpnd rejects a profile without exactly one .ovpn, without client/dev tun/remote, without a CA,
  with a missing referenced file, or with a forbidden directive (regex copied below).
- WireGuard keys live in a directory given per tunnel; vpnd writes the private key there and
  uses "<dir>/<keyfile>_psk" as pre-shared key when that file exists.  vpnd builds shell
  commands from these values, so every field is validated against a strict pattern here.
- IPsec/WireGuard service switches answer rc 6 ("a device reset is required"); vpn_cli then
  offers a reboot prompt that reads EOF in argv mode - this UI reports it and never reboots.
"""
import base64
import ipaddress
import os
import re
import shutil
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import ApiError, field_error, unavailable, vendor_error

VPN = "vpn_cli"
T_IPSEC, T_OPENVPN, T_WG = 4, 5, 6
WG_TUNNELS, IPSEC_TUNNELS = 3, 6
STAGING_DEVICE = "/tmp/vpnFiles"                 # stock vpn app.so .rodata 0xf588
WG_KEYS_DEVICE = "/opt/nvtl/data/vpn/wguard"     # vpnd .rodata 0x12a80
LOG_FILES = {"openvpn": "/var/log/openvpn_log", "wireguard": "/opt/nvtl/tmp/vpn/wguard/wgvpn-log",
             "ipsec": "/opt/nvtl/tmp/vpn/ipsec/ipsec_conn%d_log", "charon": "/var/log/charon.log"}
MAX_LOG_LINES = 500                              # stock vpn app <Config><MaxLinesShown>

STATES = {0: "not_connected", 1: "connecting", 2: "connected", 3: "disconnected", 4: "failed", 5: "stale"}
FAIL_REASONS = {0: "Unknown error", 1: "No Internet connection", 2: "The server could not be reached",
                3: "Authentication failed (check the user name and password)", 4: "Certificate error",
                5: "Key error", 6: "No devices are connected to the router", 7: "The peer did not answer",
                8: "No matching IPsec proposal"}
VENDOR_RC = {1: "general error", 2: "invalid value", 3: "buffer too small", 4: "not supported on this device",
             5: "the VPN service is not running", 6: "a restart is needed", 7: "not implemented",
             1000: "the uploaded files could not be opened", 1001: "no .ovpn configuration file was uploaded",
             1002: "the .ovpn configuration file is not valid", 1003: "a file referenced by the profile is missing",
             1004: "the .ovpn configuration file could not be opened"}

# vpnd .rodata 0xeddc (not allowed) and 0xef6c (handled) - lines are matched with a prefix regex
OVPN_DENY = re.compile(r"^(local|remote-random-hostname|show|http-proxy|socks-proxy|ipchange|topology|tun-ipv6|"
                       r"dev-node|lladdr|iproute|ifconfig|route|client-nat|redirect|up|down|setenv|user|group|cd|"
                       r"chroot|section|daemon|syslog|errors-to-stderr|log|writepid|nice|remap-usr1|status|mute|"
                       r"management|plugin|server|test|capath|tls-verify|tls-export-cert|askpass|auth-nocache|"
                       r"crl-verify)")
INLINE_TAGS = ("ca", "cert", "key", "tls-auth", "tls-crypt", "extra-certs", "pkcs12", "secret", "dh",
               "connection", "http-proxy-user-pass", "auth-user-pass")
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 10, 512 * 1024, 2 * 1024 * 1024

WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=$")
HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}"
                     r"[A-Za-z0-9])?)*$")
TUNNEL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,15}$")
IPSEC_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
IPSEC_ID_RE = re.compile(r"^[A-Za-z0-9@._:%=/+-]{1,128}$")
SECRET_RE = re.compile(r'^[\x20-\x21\x23-\x5b\x5d-\x7e]+$')        # printable ASCII without " and backslash
PROPOSAL_RE = re.compile(r"^[a-z0-9_]+(-[a-z0-9_]+){1,3}(,[a-z0-9_]+(-[a-z0-9_]+){1,3}){0,15}$")


# -- X25519 (RFC 7748) - pure Python, used only to show the router's own WireGuard public key ---
_P = 2 ** 255 - 19


def x25519(k: bytes, u: bytes) -> bytes:
    kb = bytearray(k)
    kb[0] &= 248
    kb[31] &= 127
    kb[31] |= 64
    n = int.from_bytes(bytes(kb), "little")
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        bit = (n >> t) & 1
        if swap ^ bit:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % _P, (x2 - z2) % _P
        aa, bb = a * a % _P, b * b % _P
        e = (aa - bb) % _P
        c, d = (x3 + z3) % _P, (x3 - z3) % _P
        da, cb = d * a % _P, c * b % _P
        x3 = (da + cb) * (da + cb) % _P
        z3 = x1 * (da - cb) * (da - cb) % _P
        x2 = aa * bb % _P
        z2 = e * (aa + 121665 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def wg_public_key(private_b64: str) -> Optional[str]:
    try:
        raw = base64.b64decode(private_b64, validate=True)
    except (ValueError, TypeError):
        return None
    if len(raw) != 32:
        return None
    return base64.b64encode(x25519(raw, (9).to_bytes(32, "little"))).decode("ascii")


def wg_generate_private() -> str:
    kb = bytearray(os.urandom(32))
    kb[0] &= 248
    kb[31] &= 127
    kb[31] |= 64
    return base64.b64encode(bytes(kb)).decode("ascii")


# -- parsers (pure; tested against the real fixtures and the vendor printf formats) ----------
def _rx(text: str, pattern: str, default: Optional[str] = None) -> Optional[str]:
    m = re.search(pattern, text or "", re.M)
    return m.group(1) if m else default


def _ival(v: Optional[str], default: Optional[int] = None) -> Optional[int]:
    try:
        return int(str(v).strip(), 0)
    except (TypeError, ValueError):
        return default


def parse_service_flag(text: str) -> Optional[bool]:
    v = _rx(text, r"\(0=Disable, 1=Enable\) is: \[(\d+)\]")
    return None if v is None else v == "1"


def parse_stats(text: str) -> Optional[Dict[str, Any]]:
    state = _ival(_rx(text, r"^Connection state: \[(-?\d+)\]"))
    if state is None:
        return None
    up = re.search(r"^Connection Up Time: \[(\d+) days (\d+):(\d+):(\d+)\]", text or "", re.M)
    reason = _ival(_rx(text, r"^Connection failed reason: \[(-?\d+)\]"))
    return {"server_type": _ival(_rx(text, r"^Vpn server type : \[(-?\d+)\]")), "state": STATES.get(state, "unknown"),
            "state_code": state, "failed_reason": reason,
            "failed_text": FAIL_REASONS.get(reason) if state == 4 and reason is not None else None,
            "ip": _rx(text, r"^Connection IPv4 address: \[([^\]]*)\]") or "",
            "subnet": _rx(text, r"^Connection IPv4 subnet address: \[([^\]]*)\]") or "",
            "tx_bytes": _ival(_rx(text, r"^Connection IPv4 Tx Bytes: \[(\d+)\]"), 0),
            "rx_bytes": _ival(_rx(text, r"^Connection IPv4 Rx Bytes: \[(\d+)\]"), 0),
            "uptime_s": (int(up.group(1)) * 86400 + int(up.group(2)) * 3600 + int(up.group(3)) * 60 + int(up.group(4)))
            if up else 0}


def parse_openvpn_cfg(text: str) -> Dict[str, Any]:
    name = _rx(text, r"^OpenVPN uploaded config file name: \[(.*)\]\s*$")
    if name is None:
        return {"configured": False, "config_file": "", "server": "", "username": "", "password": ""}
    return {"configured": bool(name.strip()), "config_file": name.strip(),
            "server": (_rx(text, r"^OpenVPN server URL: \[(.*)\]\s*$") or "").strip(),
            "username": _rx(text, r"^OpenVPN server username: \[(.*)\]\s*$") or "",
            "password": _rx(text, r"^OpenVPN server password: \[(.*)\]\s*$") or ""}


def parse_wg(text: str) -> Optional[Dict[str, Any]]:
    if not re.search(r"^WGuard VPN Tunnel Name:", text or "", re.M):
        return None

    def f(label):
        return (_rx(text, r"^%s(.*)$" % re.escape(label)) or "").strip()
    allowed = [a.strip() for a in f("Allowed IPs/Subnets:").split(",") if a.strip()]
    return {"name": f("WGuard VPN Tunnel Name:"), "enabled": f("Tunnel Connection(0-Disable, 1-Enable):") == "1",
            "startup": "auto" if f("Tunnel Startup(0-Manually, 1-Automatically):") == "1" else "manual",
            "retries": _ival(f("connection retries:"), 0), "keys_path": f("Keys Path:"),
            "key_file": f("Private Key file name:"), "private_key": f("Private Key content:"),
            "peer_public_key": f("Public Key content:"), "tunnel_ip": f("Tunnel IP:"), "peer_ip": f("Peer IP:"),
            "allowed_ips": allowed, "endpoint": f("EndPoint IP:"), "listen_port": _ival(f("Listen Port:"), 0),
            "endpoint_port": _ival(f("EndPoint Port:"), 0), "keepalive": _ival(f("KeepAlive :"), 0),
            "state_code": _ival(f("Tunnel State:"), 0)}


AUTH_NAMES = {0: "none", 1: "psk", 2: "eap", 3: "certificate"}


def parse_ipsec(text: str) -> Optional[Dict[str, Any]]:
    if not re.search(r"^IPSec VPN Tunnel Name:", text or "", re.M):
        return None

    def f(pattern, default=""):
        v = _rx(text, pattern)
        return default if v is None else v.strip()
    dpd = f(r"^DPD Enabled\(0-Disable, 1-Enable: (\d+)\)") == "1"
    return {"name": f(r"^IPSec VPN Tunnel Name:\[(.*)\]\s*$"),
            "startup": "auto" if f(r"^IPSec VPN Automatically\(1\) or Manually\(0\): (\d+)") == "1" else "manual",
            "enabled": f(r"^IPsec VPN Tunnel Enabled\(0-Disable, 1-Enable\): (\d+)") == "1",
            "left_firewall": f(r"^IPSec VPN Left Firewall Enabled\(0-Disable, 1-Enable\): (\d+)") == "1",
            "anonymous": f(r"^IPSec VPN Anonymous mode \(0-Disable, 1-Enable\): (\d+)") == "1",
            "ike_version": _ival(f(r"^IPSec IKE Version:(\d+)"), 2),
            "local_id": f(r"^IPSec VPN Local ID:\[(.*)\]\s*$"), "remote_id": f(r"^IPSec VPN Remote ID:\[(.*)\]\s*$"),
            "client_auth": AUTH_NAMES.get(_ival(f(r"^IPSec IKE Client Authentication Type\(.*\): \[(\d+)\]"), 0), "none"),
            "server_auth": AUTH_NAMES.get(_ival(f(r"^IPSec IKE Server Authentication Type\(.*\): \[(\d+)\]"), 0), "none"),
            "local_ip": f(r"^IPSec Local IP address: (.*)$"), "local_subnet": f(r"^IPSec Local Network Subnet: (.*)$"),
            "remote_ip": f(r"^IPSec Remote IP address: (.*)$"),
            "remote_subnet": f(r"^IPSec Remote Network Subnet: (.*)$"),
            "ike_lifetime": f(r"^IPSec IKE Lifetime: (.*?) \(default in seconds\)"),
            "phase1": f(r"^IPSec Phase1 Proposal: (.*)$"),
            "key_lifetime": f(r"^IPSec Authentication Key Lifetime: (.*?) \(default in seconds\)"),
            "phase2": f(r"^IPSec Phase2 Proposal: (.*)$"),
            "dpd": {"enabled": dpd, "action": _ival(f(r"^DPD Action\(.*\): (\d+)"), 0),
                    "delay": _ival(f(r"^DPD Delay: (\d+) seconds"), 0),
                    "timeout": _ival(f(r"^DPD Timeout: (\d+) seconds"), 0)},
            "state_code": _ival(f(r"^IPSec VPN State: (-?\d+)"), 0)}


def parse_validation(text: str) -> Dict[str, str]:
    return {"config_file": (_rx(text, r"config file name:\[(.*)\]\s*$") or "").strip(),
            "url": (_rx(text, r"config URL:\[(.*)\]\s*$") or "").strip(),
            "missing": (_rx(text, r"Missed configuration file name is: \[(.*)\]\s*$") or "").strip()}


def validate_ovpn_files(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Our copy of vpnd's checks (validate_openvpn_configuration) so problems are reported per line
    before anything reaches the device.  files: [{name, data: bytes}] -> {config_file, server, problems}."""
    problems: List[str] = []
    names = [f["name"] for f in files]
    ovpn = [f for f in files if f["name"].lower().endswith(".ovpn")]
    if len(ovpn) != 1:
        problems.append("Upload exactly one .ovpn profile (found %d)." % len(ovpn))
        return {"config_file": "", "server": "", "problems": problems}
    prof = ovpn[0]
    try:
        text = prof["data"].decode("utf-8")
    except UnicodeDecodeError:
        return {"config_file": prof["name"], "server": "", "problems": ["The .ovpn profile is not a text file."]}
    seen = {"client": False, "dev": None, "remote": None, "ca": False}
    inline = None
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if inline:
            if line.lower() == "</%s>" % inline:
                inline = None
            continue
        m = re.match(r"^<([a-z0-9-]+)>$", line.lower())
        if m:
            inline = m.group(1)
            if inline == "ca":
                seen["ca"] = True
            continue
        if OVPN_DENY.match(line):
            problems.append("Line %d: '%s' is not allowed by the router." % (no, line.split()[0]))
            continue
        parts = line.split()
        key = parts[0].lower()
        if key == "client":
            seen["client"] = True
        elif key == "dev":
            seen["dev"] = parts[1] if len(parts) > 1 else ""
        elif key == "remote" and seen["remote"] is None:
            seen["remote"] = parts[1] if len(parts) > 1 else ""
        elif key in ("ca", "cert", "key", "tls-auth", "tls-crypt"):
            ref = parts[1] if len(parts) > 1 else ""
            if key == "ca":
                seen["ca"] = True
            if ref and ref != "[inline]" and os.path.basename(ref) not in names:
                problems.append("Line %d: the file '%s' is referenced but was not uploaded." % (no, ref))
        elif key == "comp-lzo" and len(parts) > 1 and parts[1].lower() not in ("yes", "no", "adaptive"):
            problems.append("Line %d: unknown comp-lzo mode '%s'." % (no, parts[1]))
    if inline:
        problems.append("The inline <%s> block is not closed." % inline)
    if not seen["client"]:
        problems.append("The profile has no 'client' line.")
    if seen["dev"] is None:
        problems.append("The profile has no 'dev' line.")
    elif not seen["dev"].startswith("tun"):
        problems.append("Only 'dev tun' profiles are supported (found 'dev %s')." % seen["dev"])
    if not seen["remote"]:
        problems.append("The profile has no 'remote' server line.")
    if not seen["ca"]:
        problems.append("The profile defines no CA certificate ('ca <file>' or an inline <ca> block).")
    return {"config_file": prof["name"], "server": seen["remote"] or "", "problems": problems}


def register(app):
    r = app.router

    def run_get(cmd: str, answers=()):
        return app.cli_try(VPN, cmd, list(answers))

    def staging_dir() -> str:
        return os.path.join(app.cfg["state_dir"], "mock-vpnFiles") if app.mock else STAGING_DEVICE

    def wg_keys_dir() -> str:
        return os.path.join(app.cfg["state_dir"], "mock-wguard") if app.mock else WG_KEYS_DEVICE

    def vendor_fail(res, what: str):
        if res.ok:
            return
        rc = res.rc
        msg = VENDOR_RC.get(rc or -1) or res.message or ("timed out" if res.timed_out else "no answer")
        raise vendor_error("%s failed: %s" % (what, msg), {"rc": rc, "module": res.module})

    def features() -> Dict[str, Optional[bool]]:
        def load():
            res = app.cli_try("settings_cli", "get_features")
            if not res:
                return {"vpn": None, "ipsec": None}
            return {"vpn": res.get("vpn_supported") == "1" if res.get("vpn_supported") else None,
                    "ipsec": res.get("ipsec_supported") == "1" if res.get("ipsec_supported") else None}
        return app.cache.get("vpn:features", 300, load)

    def stats(server_type: int, index: Optional[int] = None) -> Optional[Dict[str, Any]]:
        ans = [str(server_type), str(index if index is not None else 0), "0"]
        res = run_get("get_connection_statistics", ans)
        return parse_stats(res.text) if res else None

    def openvpn_cfg() -> Dict[str, Any]:
        res = run_get("get_settings", [str(T_OPENVPN), "0"])
        if not res:
            raise unavailable("The VPN service does not answer.")
        return parse_openvpn_cfg(res.text)

    def wg_tunnel(i: int) -> Optional[Dict[str, Any]]:
        res = run_get("get_settings", [str(T_WG), str(i), "0"])
        return parse_wg(res.text) if res else None

    def ipsec_tunnel(i: int) -> Optional[Dict[str, Any]]:
        res = run_get("get_settings", [str(T_IPSEC), str(i), "0"])
        return parse_ipsec(res.text) if res else None

    def service_flags() -> Dict[str, Optional[bool]]:
        out = {}
        for key, cmd in (("auto_connect", "get_auto_connect"), ("ipsec", "get_ipsec_vpn"), ("wireguard", "get_wguard_vpn")):
            res = run_get(cmd)
            out[key] = parse_service_flag(res.text) if res else None
        return out

    def done(req, action, details=None):
        app.cache.invalidate("vpn:")
        app.audit(req, action, details)

    # ---- status / service switches -----------------------------------------------------------
    def status() -> Dict[str, Any]:
        def load():
            running = run_get("is_running") is not None
            if not running:
                return {"running": False, "auto_connect": None, "services": {"ipsec": None, "wireguard": None},
                        "openvpn": None, "connected": False, "summary": "VPN service not running",
                        "supported": features()}
            flags = service_flags()
            try:
                ov = openvpn_cfg()
            except ApiError:                  # vendor refused the read: treat as "no profile"
                ov = parse_openvpn_cfg("")
            ov["password_set"] = bool(ov.pop("password", ""))
            st = stats(T_OPENVPN) if ov["configured"] else None
            ov["stats"] = st
            connected = bool(st and st["state"] == "connected")
            summary = ("Connected" if connected else "Connecting" if st and st["state"] == "connecting" else
                       "Not configured" if not ov["configured"] else "Not connected")
            return {"running": True, "auto_connect": flags["auto_connect"],
                    "services": {"ipsec": flags["ipsec"], "wireguard": flags["wireguard"]}, "openvpn": ov,
                    "connected": connected, "summary": summary, "supported": features()}
        return app.cache.get("vpn:status", 3, load)

    @r.route("GET", "/api/vpn/status")
    def get_status(req):
        """VPN overview: service, auto-connect, IPsec/WireGuard services, OpenVPN profile and connection."""
        return status()

    @r.route("PUT", "/api/vpn/settings")
    def put_settings(req):
        """Auto-connect and the IPsec / WireGuard service switches (the last two may need a restart)."""
        data = validate.only_keys(req.json, ["auto_connect", "ipsec", "wireguard"])
        reboot = False
        for key, cmd in (("auto_connect", "set_auto_connect"), ("ipsec", "set_ipsec_vpn"), ("wireguard", "set_wguard_vpn")):
            if key in data:
                v = "1" if validate.boolean(key, data[key]) else "0"
                res = app.cli.run(VPN, cmd, [v])
                if res.rc == 6 and key != "auto_connect":
                    reboot = True              # saved; takes effect after a restart (never triggered here)
                else:
                    vendor_fail(res, "Changing the %s setting" % key.replace("_", " "))
        done(req, "vpn.settings.update", {"keys": sorted(data)})
        out = dict(status())
        out["reboot_required"] = reboot
        return out

    # ---- OpenVPN ---------------------------------------------------------------------------
    def decode_files(raw: Any) -> List[Dict[str, Any]]:
        if not isinstance(raw, list) or not raw:
            raise field_error("files", "Choose the .ovpn profile and the certificate/key files it uses")
        if len(raw) > MAX_FILES:
            raise field_error("files", "At most %d files" % MAX_FILES)
        out, total = [], 0
        for item in raw:
            if not isinstance(item, dict):
                raise field_error("files", "Bad file entry")
            name = item.get("name")
            if not isinstance(name, str) or not FILE_NAME_RE.match(name) or name in (".", ".."):
                raise field_error("files", "File names may use letters, digits, '.', '_' and '-' only (got %r)"
                                  % (name if isinstance(name, str) else "?"))
            if any(o["name"] == name for o in out):
                raise field_error("files", "The file %s was chosen twice" % name)
            try:
                data = base64.b64decode(str(item.get("data", "")), validate=True)
            except (ValueError, TypeError):
                raise field_error("files", "The file %s could not be read" % name)
            if len(data) > MAX_FILE_BYTES:
                raise field_error("files", "The file %s is too large" % name)
            total += len(data)
            out.append({"name": name, "data": data})
        if total > MAX_TOTAL_BYTES:
            raise field_error("files", "The files are too large together")
        return out

    def stage(files: List[Dict[str, Any]]) -> str:
        d = staging_dir()
        if os.path.islink(d):
            os.unlink(d)
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d, mode=0o700)
        if os.path.islink(d):                         # paranoia: never write through a link
            raise vendor_error("Staging directory is a link")
        for f in files:
            path = os.path.join(d, f["name"])
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(f["data"])
        return d

    def check_profile(files):
        local = validate_ovpn_files(files)
        if local["problems"]:
            raise ApiError(400, "invalid_field", local["problems"][0], {"field": "files", "problems": local["problems"]})
        d = stage(files)
        res = app.cli.run(VPN, "validate_openvpn_config_files", [d])
        vendor = parse_validation(res.text)
        if not res.ok:
            msg = VENDOR_RC.get(res.rc or -1) or res.message or "the router rejected the profile"
            if vendor["missing"]:
                msg += " (%s)" % vendor["missing"]
            raise ApiError(400, "invalid_field", "The router rejected the profile: %s" % msg,
                           {"field": "files", "rc": res.rc, "missing": vendor["missing"]})
        return d, {"config_file": vendor["config_file"] or local["config_file"],
                   "url": vendor["url"] or local["server"], "server": local["server"]}

    @r.route("POST", "/api/vpn/openvpn/validate", upload=True)
    def validate_profile(req):
        """Check an OpenVPN profile (.ovpn + certificate files) without saving it."""
        data = validate.only_keys(req.json, ["files"])
        files = decode_files(data.get("files"))
        _d, info = check_profile(files)
        return {"ok": True, "config_file": info["config_file"], "server": info["url"]}

    @r.route("POST", "/api/vpn/openvpn", upload=True)
    def save_profile(req):
        """Save an OpenVPN profile (uploaded files + user name/password) and optionally connect."""
        data = validate.only_keys(req.json, ["files", "username", "password", "retries", "connect"])
        user = validate.text("username", data.get("username", ""), 1, 128)
        pw = validate.text("password", data.get("password", ""), 1, 128)
        for k, v in (("username", user), ("password", pw)):
            if "\t" in v or not v.strip():
                raise field_error(k, "Enter a value without tabs")
        retries = validate.integer("retries", data.get("retries", 3), 0, 100)
        files = decode_files(data.get("files"))
        d, info = check_profile(files)
        res = app.cli.run(VPN, "set_settings", [str(T_OPENVPN), "0", str(retries), d, info["config_file"],
                                                info["url"], user, pw])
        vendor_fail(res, "Saving the OpenVPN profile")
        done(req, "vpn.openvpn.save", {"config_file": info["config_file"], "files": len(files)})
        if data.get("connect") is True:
            vendor_fail(app.cli.run(VPN, "connect_vpn", [str(T_OPENVPN)]), "Connecting")
            app.cache.invalidate("vpn:")
        return status()

    @r.route("DELETE", "/api/vpn/openvpn")
    def clear_profile(req):
        """Remove the OpenVPN profile (vendor 'Clear all': files, settings and log)."""
        vendor_fail(app.cli.run(VPN, "set_settings", [str(T_OPENVPN), "1"]), "Removing the OpenVPN profile")
        done(req, "vpn.openvpn.clear")
        return status()

    @r.route("GET", "/api/vpn/openvpn/secret")
    def openvpn_secret(req):
        """Reveal the stored OpenVPN password."""
        app.audit(req, "vpn.openvpn.reveal")
        cfg = openvpn_cfg()
        return {"username": cfg["username"], "password": cfg["password"]}

    def connect_action(req, server_type: int, index: Optional[int], up: bool):
        ans = [str(server_type)] + ([str(index)] if index is not None else [])
        res = app.cli.run(VPN, "connect_vpn" if up else "disconnect_vpn", ans)
        vendor_fail(res, "Connecting" if up else "Disconnecting")
        done(req, "vpn.%s" % ("connect" if up else "disconnect"), {"type": server_type, "index": index})

    @r.route("POST", "/api/vpn/openvpn/connect")
    def openvpn_connect(req):
        """Connect the OpenVPN profile."""
        connect_action(req, T_OPENVPN, None, True)
        return status()

    @r.route("POST", "/api/vpn/openvpn/disconnect")
    def openvpn_disconnect(req):
        """Disconnect the OpenVPN profile."""
        connect_action(req, T_OPENVPN, None, False)
        return status()

    # ---- WireGuard ---------------------------------------------------------------------------
    def tunnel_index(req, limit: int) -> int:
        try:
            i = int(req.params["index"])
        except (KeyError, ValueError):
            raise field_error("index", "Bad tunnel number")
        if not 0 <= i < limit:
            raise field_error("index", "Tunnel number must be 0..%d" % (limit - 1))
        return i

    def psk_path(i: int) -> str:
        return os.path.join(wg_keys_dir(), "wg%d.key_psk" % i)

    def wg_public(t: Dict[str, Any], i: int) -> Dict[str, Any]:
        configured = bool(t and t.get("name"))
        out = {"index": i, "configured": configured}
        if not configured:
            out.update({"name": "wg%d" % i, "enabled": False, "startup": "manual", "retries": 3, "private_key_set": False,
                        "public_key": "", "peer_public_key": "", "tunnel_ip": "", "peer_ip": "", "allowed_ips": [],
                        "endpoint": "", "endpoint_port": 51820, "listen_port": 51820, "keepalive": 25,
                        "psk_set": False, "stats": None})
            return out
        out.update({k: t[k] for k in ("name", "enabled", "startup", "retries", "peer_public_key", "tunnel_ip",
                                      "peer_ip", "allowed_ips", "endpoint", "endpoint_port", "listen_port", "keepalive")})
        out["private_key_set"] = bool(t.get("private_key"))
        out["public_key"] = (wg_public_key(t["private_key"]) or "") if t.get("private_key") else ""
        out["psk_set"] = os.path.isfile(psk_path(i))
        out["stats"] = stats(T_WG, i)
        return out

    def wg_list():
        def load():
            if run_get("is_running") is None:
                raise unavailable("The VPN service is not running.")
            flags = service_flags()
            return {"service_enabled": flags["wireguard"], "auto_connect": flags["auto_connect"],
                    "tunnels": [wg_public(wg_tunnel(i), i) for i in range(WG_TUNNELS)]}
        return app.cache.get("vpn:wg", 4, load)

    @r.route("GET", "/api/vpn/wireguard")
    def get_wg(req):
        """WireGuard tunnels (private keys are never returned here)."""
        return wg_list()

    WG_KEYS = ["name", "enabled", "startup", "retries", "private_key", "generate_key", "peer_public_key", "tunnel_ip",
               "peer_ip", "allowed_ips", "endpoint", "endpoint_port", "listen_port", "keepalive", "psk", "psk_clear"]

    def wg_answers(i: int, data: Dict[str, Any], cur: Optional[Dict[str, Any]]) -> List[str]:
        cur = cur if cur and cur.get("name") else {}

        def g(k, d=None):
            if k in data:
                return data[k]
            v = cur.get(k)
            unset = v is None or v == "" or v == [] or (type(v) is int and v == 0)
            return d if unset else v      # the vendor prints 0/"" for unset fields
        name = validate.text("name", str(g("name", "wg%d" % i) or "wg%d" % i), 1, 15)
        if not TUNNEL_NAME_RE.match(name):
            raise field_error("name", "Interface name: letters, digits, '_' or '-' (max 15), e.g. wg%d" % i)
        if data.get("generate_key") is True:
            pkey = wg_generate_private()
        else:
            pkey = str(g("private_key", "") or "").strip()
        if not WG_KEY_RE.match(pkey):
            raise field_error("private_key", "Enter the 44-character private key (or generate one)")
        peer = str(g("peer_public_key", "") or "").strip()
        if not WG_KEY_RE.match(peer):
            raise field_error("peer_public_key", "Enter the server's 44-character public key")
        tun_ip = validate.ipv4("tunnel_ip", g("tunnel_ip"))
        peer_ip = validate.ipv4("peer_ip", g("peer_ip"))
        allowed = g("allowed_ips", [])
        if isinstance(allowed, str):
            allowed = [a for a in re.split(r"[,\s]+", allowed) if a]
        if not isinstance(allowed, list) or not allowed:
            raise field_error("allowed_ips", "Enter at least one network, e.g. 0.0.0.0/0 to send everything")
        nets = []
        for a in allowed:
            try:
                nets.append(str(ipaddress.ip_network(str(a).strip(), strict=False)))
            except ValueError:
                raise field_error("allowed_ips", "%s is not a valid network (use a.b.c.d/nn)" % a)
        if len(",".join(nets)) > 1000:
            raise field_error("allowed_ips", "Too many networks")
        endpoint = str(g("endpoint", "") or "").strip()
        try:
            endpoint = str(ipaddress.IPv4Address(endpoint))
        except ValueError:
            if not HOST_RE.match(endpoint):
                raise field_error("endpoint", "Enter the server's IPv4 address or host name")
        startup = validate.choice("startup", g("startup", "manual"), ["manual", "auto"])
        return [str(T_WG), "0", str(i), "1" if validate.boolean("enabled", g("enabled", True)) else "0",
                "1" if startup == "auto" else "0", name, str(validate.integer("retries", g("retries", 3), 0, 100)),
                wg_keys_dir(), "wg%d.key" % i, pkey, peer, tun_ip, peer_ip, ",".join(nets), endpoint,
                str(validate.port("listen_port", g("listen_port", 51820))),
                str(validate.port("endpoint_port", g("endpoint_port", 51820))),
                str(validate.integer("keepalive", g("keepalive", 25), 1, 65535))]

    @r.route("PUT", "/api/vpn/wireguard/{index}")
    def put_wg(req):
        """Create or change a WireGuard tunnel (the stored private key is kept when none is entered)."""
        i = tunnel_index(req, WG_TUNNELS)
        data = validate.only_keys(req.json, WG_KEYS + ["index", "configured", "private_key_set", "public_key",
                                                       "psk_set", "stats"])
        data = {k: v for k, v in data.items() if k in WG_KEYS}
        if data.get("private_key") in ("", None):
            data.pop("private_key", None)
        psk = str(data.pop("psk", "") or "").strip()          # empty = keep the current pre-shared key
        psk_clear = validate.boolean("psk_clear", data.pop("psk_clear", False))
        if psk and not WG_KEY_RE.match(psk):
            raise field_error("psk", "A pre-shared key is 44 characters (wg genpsk)")
        cur = wg_tunnel(i)
        ans = wg_answers(i, data, cur)
        res = app.cli.run(VPN, "set_settings", ans)
        vendor_fail(res, "Saving the WireGuard tunnel")
        if psk:
            # vpnd uses "<keys path>/<key file>_psk" when it exists (wg set ... preshared-key, .rodata 0x106f8)
            os.makedirs(wg_keys_dir(), exist_ok=True)
            fd = os.open(psk_path(i), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(psk)
        elif psk_clear and os.path.isfile(psk_path(i)):
            os.unlink(psk_path(i))
        done(req, "vpn.wireguard.save", {"index": i})
        return wg_list()

    @r.route("DELETE", "/api/vpn/wireguard/{index}")
    def del_wg(req):
        """Remove a WireGuard tunnel."""
        i = tunnel_index(req, WG_TUNNELS)
        vendor_fail(app.cli.run(VPN, "set_settings", [str(T_WG), "1", str(i)]), "Removing the WireGuard tunnel")
        if os.path.isfile(psk_path(i)):
            os.unlink(psk_path(i))
        done(req, "vpn.wireguard.clear", {"index": i})
        return wg_list()

    @r.route("GET", "/api/vpn/wireguard/{index}/secret")
    def wg_secret(req):
        """Reveal the private key of a WireGuard tunnel."""
        i = tunnel_index(req, WG_TUNNELS)
        app.audit(req, "vpn.wireguard.reveal", {"index": i})
        t = wg_tunnel(i) or {}
        return {"private_key": t.get("private_key", "")}

    @r.route("POST", "/api/vpn/wireguard/{index}/connect")
    def wg_connect(req):
        """Bring a WireGuard tunnel up."""
        i = tunnel_index(req, WG_TUNNELS)
        connect_action(req, T_WG, i, True)
        return wg_list()

    @r.route("POST", "/api/vpn/wireguard/{index}/disconnect")
    def wg_disconnect(req):
        """Take a WireGuard tunnel down."""
        i = tunnel_index(req, WG_TUNNELS)
        connect_action(req, T_WG, i, False)
        return wg_list()

    @r.route("POST", "/api/vpn/wireguard/keygen")
    def wg_keygen(req):
        """Generate a WireGuard key pair (nothing is stored)."""
        priv = wg_generate_private()
        return {"private_key": priv, "public_key": wg_public_key(priv)}

    @r.route("POST", "/api/vpn/wireguard/pubkey")
    def wg_pubkey(req):
        """Public key for a private key (to give to the server)."""
        data = validate.only_keys(req.json, ["private_key"])
        k = str(data.get("private_key", "")).strip()
        if not WG_KEY_RE.match(k):
            raise field_error("private_key", "Enter the 44-character private key")
        return {"public_key": wg_public_key(k)}

    # ---- IPsec -------------------------------------------------------------------------------
    def ipsec_public(t: Optional[Dict[str, Any]], i: int) -> Dict[str, Any]:
        configured = bool(t and t.get("name"))
        if not configured:
            return {"index": i, "configured": False, "name": "ipsec%d" % i, "enabled": False, "startup": "manual",
                    "left_firewall": True, "anonymous": False, "ike_version": 2, "local_id": "", "remote_id": "",
                    "client_auth": "psk", "server_auth": "psk", "local_ip": "", "local_subnet": "", "remote_ip": "",
                    "remote_subnet": "", "ike_lifetime": "28800", "phase1": "aes256-sha2_256-modp2048",
                    "key_lifetime": "3600", "phase2": "aes256-sha2_256",
                    "dpd": {"enabled": True, "action": 3, "delay": 30, "timeout": 120}, "stats": None}
        out = dict(t)
        out.update({"index": i, "configured": True})
        out.pop("state_code", None)
        out["stats"] = stats(T_IPSEC, i)
        return out

    def ipsec_list():
        def load():
            if run_get("is_running") is None:
                raise unavailable("The VPN service is not running.")
            flags = service_flags()
            return {"service_enabled": flags["ipsec"], "supported": features()["ipsec"],
                    "tunnels": [ipsec_public(ipsec_tunnel(i), i) for i in range(IPSEC_TUNNELS)]}
        return app.cache.get("vpn:ipsec", 4, load)

    @r.route("GET", "/api/vpn/ipsec")
    def get_ipsec(req):
        """IPsec tunnels (pre-shared keys and passwords are write-only: the router never returns them)."""
        return ipsec_list()

    IPSEC_KEYS = ["name", "enabled", "startup", "left_firewall", "anonymous", "local_id", "remote_id", "client_auth",
                  "client_psk", "client_username", "client_password", "server_auth", "server_psk", "server_username",
                  "server_password", "ike_version", "local_ip", "local_subnet", "remote_ip", "remote_subnet",
                  "ike_lifetime", "phase1", "key_lifetime", "phase2", "dpd"]

    def secret(field: str, v: Any, lo: int = 1) -> str:
        s = validate.text(field, v if isinstance(v, str) else "", lo, 128)
        if not SECRET_RE.match(s):
            raise field_error(field, "Use printable characters without quotes or backslashes")
        return s

    def ipsec_answers(i: int, d: Dict[str, Any]) -> List[str]:
        name = validate.text("name", str(d.get("name") or "ipsec%d" % i), 1, 32)
        if not IPSEC_NAME_RE.match(name):
            raise field_error("name", "Connection name: letters, digits, '_' or '-' (max 32)")
        anon = validate.boolean("anonymous", d.get("anonymous", False))

        def ident(field):
            v = str(d.get(field) or "%any").strip()
            if not IPSEC_ID_RE.match(v):
                raise field_error(field, "Use an IP address, host name, user@host or %any (no spaces)")
            return v
        ca = validate.choice("client_auth", d.get("client_auth", "psk"), ["psk", "eap"])
        sa = validate.choice("server_auth", d.get("server_auth", "psk"), ["psk", "eap"])
        ans = [str(T_IPSEC), "0", str(i), "1" if validate.choice("startup", d.get("startup", "manual"),
                                                                 ["manual", "auto"]) == "auto" else "0",
               "1" if validate.boolean("enabled", d.get("enabled", True)) else "0", name,
               "1" if validate.boolean("left_firewall", d.get("left_firewall", True)) else "0", "1" if anon else "0",
               ident("local_id"), ident("remote_id")]
        if ca == "psk":
            ans += ["1", secret("client_psk", d.get("client_psk"), 8)]
        else:
            ans += ["2", secret("client_username", d.get("client_username")), secret("client_password", d.get("client_password"))]
        if sa == "psk":
            ans += ["1", secret("server_psk", d.get("server_psk") or d.get("client_psk"), 8)]
        else:
            ans += ["2", secret("server_username", d.get("server_username")), secret("server_password", d.get("server_password"))]
        ans.append(str(validate.choice("ike_version", validate.integer("ike_version", d.get("ike_version", 2)), [1, 2])))

        def net(field, blank):
            v = str(d.get(field) or "").strip()
            if not v:
                if blank is None:
                    raise field_error(field, "Required")
                return blank
            try:
                return str(ipaddress.IPv4Network(v, strict=False))
            except ValueError:
                raise field_error(field, "Enter a network like 192.168.1.0/24")
        if ca != "eap":
            lip = str(d.get("local_ip") or "").strip()
            if lip in ("%defaultroute", "%any"):
                lip = ""
            ans += [validate.ipv4("local_ip", lip) if lip else "%defaultroute", net("local_subnet", None)]
        rip = str(d.get("remote_ip") or "").strip()
        if not rip:
            if not anon:
                raise field_error("remote_ip", "Enter the server address")
            rip = "%any"
        elif rip != "%any":
            try:
                rip = str(ipaddress.IPv4Address(rip))
            except ValueError:
                if not HOST_RE.match(rip):
                    raise field_error("remote_ip", "Enter an IPv4 address or host name")
        ans += [rip, net("remote_subnet", "0.0.0.0/0" if anon else None)]
        def lifetime(field, default):
            v = str(d.get(field) or default).strip()
            if not re.match(r"^[1-9][0-9]{0,5}$", v):      # read with fgets(buf, 7): at most 6 characters
                raise field_error(field, "Enter seconds (1-999999)")
            return v

        def proposal(field):
            v = str(d.get(field) or "").strip().lower()
            if not PROPOSAL_RE.match(v):
                raise field_error(field, "Use proposals like aes256-sha2_256-modp2048 (comma separated, no spaces)")
            return v
        ans += [lifetime("ike_lifetime", "28800"), proposal("phase1"), lifetime("key_lifetime", "3600"),
                proposal("phase2")]
        dpd = d.get("dpd") if isinstance(d.get("dpd"), dict) else {}
        if validate.boolean("dpd", dpd.get("enabled", True)):
            ans += ["1", str(validate.integer("dpd", dpd.get("action", 3), 0, 3)),
                    str(validate.integer("dpd", dpd.get("delay", 30), 1, 3600)),
                    str(validate.integer("dpd", dpd.get("timeout", 120), 1, 3600))]
        else:
            ans.append("0")
        return ans

    @r.route("PUT", "/api/vpn/ipsec/{index}")
    def put_ipsec(req):
        """Create or change an IPsec tunnel (keys/passwords must be entered on every save)."""
        i = tunnel_index(req, IPSEC_TUNNELS)
        data = validate.only_keys(req.json, IPSEC_KEYS + ["index", "configured", "stats"])
        res = app.cli.run(VPN, "set_settings", ipsec_answers(i, {k: v for k, v in data.items() if k in IPSEC_KEYS}))
        vendor_fail(res, "Saving the IPsec tunnel")
        done(req, "vpn.ipsec.save", {"index": i})
        return ipsec_list()

    @r.route("DELETE", "/api/vpn/ipsec/{index}")
    def del_ipsec(req):
        """Remove an IPsec tunnel."""
        i = tunnel_index(req, IPSEC_TUNNELS)
        vendor_fail(app.cli.run(VPN, "set_settings", [str(T_IPSEC), "1", str(i)]), "Removing the IPsec tunnel")
        done(req, "vpn.ipsec.clear", {"index": i})
        return ipsec_list()

    @r.route("POST", "/api/vpn/ipsec/{index}/connect")
    def ipsec_connect(req):
        """Bring an IPsec tunnel up."""
        i = tunnel_index(req, IPSEC_TUNNELS)
        connect_action(req, T_IPSEC, i, True)
        return ipsec_list()

    @r.route("POST", "/api/vpn/ipsec/{index}/disconnect")
    def ipsec_disconnect(req):
        """Take an IPsec tunnel down."""
        i = tunnel_index(req, IPSEC_TUNNELS)
        connect_action(req, T_IPSEC, i, False)
        return ipsec_list()

    # ---- logs --------------------------------------------------------------------------------
    @r.route("GET", "/api/vpn/log")
    def get_log(req):
        """Last lines of the VPN log (source=openvpn|wireguard|ipsec|charon, index for ipsec)."""
        src = validate.choice("source", req.query.get("source", "openvpn"), list(LOG_FILES))
        path = LOG_FILES[src]
        if src == "ipsec":
            path = path % validate.integer("index", req.query.get("index", 0), 0, IPSEC_TUNNELS - 1)
        if app.mock:
            st = status()
            lines = ["(demo) %s log" % src, "(demo) state: %s" % st.get("summary", "")]
            return {"source": src, "path": path, "lines": lines, "truncated": False}
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 256 * 1024))
                raw = fh.read().decode("utf-8", "replace")
        except OSError:
            return {"source": src, "path": path, "lines": [], "truncated": False, "missing": True}
        lines = raw.splitlines()
        # scrub anything that looks like a credential before it reaches the browser
        lines = [re.sub(r"(?i)(password|passwd|psk|private[-_ ]?key)(\S*\s*[:=]\s*)\S+", r"\1\2***", ln)
                 for ln in lines]
        return {"source": src, "path": path, "lines": lines[-MAX_LOG_LINES:], "truncated": len(lines) > MAX_LOG_LINES}

    def probe():
        f = features()
        if f.get("vpn") is False:
            return False
        return run_get("is_running") is not None or f.get("vpn") is True

    app.features.add("vpn", probe)
