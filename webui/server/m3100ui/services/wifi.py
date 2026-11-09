"""Wi-Fi: main and guest networks, radio & advanced settings, WPS, MAC filter.

Sources (full map: docs/features/wifi.md)
- Vendor REST (stock path, vendor-validated): WiFi/PrimarySettings, WiFi/GuestSettings,
  WiFi/AdvancedSettings(+/MaxClientsAllowed), WiFi/DisplaySettings, WhiteListedDevices, BlockedDevices.
- wifi_cli get_ap_profile <1-4>: the real per-band profile (WPA3/OWE security that REST reports as "None",
  per-band SSID, 802.11 mode, channel, width, interface feature flags), get_caps, get_settings,
  get_ap_chlist2_4/5, get_mac, get_ap_settings, get_wps_clients, get_enable/get_ap_enable/get_sta_enable.
- Writes REST cannot express (WPA3, OWE, per-band names, hidden 802.11 modes, 2.4 GHz width, guest
  restrictions) or cannot round-trip (a value REST would reject is already set) go through
  `wifi_cli set_ap_profile` (one call per band profile) + `set_all_profiles`.  Specs: adapters/specs/wifi.py.
Profiles: 1 = main 2.4 GHz, 2 = main 5 GHz, 3 = guest 2.4 GHz, 4 = guest 5 GHz (libsysintclient globals).
"""
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .. import validate
from ..errors import ApiError, field_error, unavailable, vendor_error

NETS = ("primary", "guest")
BANDS = ("2g", "5g")
PROFILE = {("primary", "2g"): 1, ("primary", "5g"): 2, ("guest", "2g"): 3, ("guest", "5g"): 4}
IFNAME = {"2g": "wlan0", "5g": "wlan1"}
IFINDEX = {"2g": "0", "5g": "1"}
REST_NET = {"primary": "WiFi/PrimarySettings", "guest": "WiFi/GuestSettings"}
REST_ADV = "WiFi/AdvancedSettings"
REST_DISPLAY = "WiFi/DisplaySettings"

# ---- security ---------------------------------------------------------------------------------------------
# key: (label, wifi_api sectype, needs password, REST string or None, writable here)
SECURITY = {
    "open": ("Open (no password)", 0, False, "None", True),
    "wep": ("WEP (insecure, legacy)", 1, True, None, False),
    "wpa": ("WPA (TKIP, insecure, legacy)", 2, True, None, False),
    "wpa2": ("WPA2 Personal", 3, True, "WPA2PSK", True),
    "wpa_wpa2": ("WPA/WPA2 mixed", 4, True, "WPA2MIX", True),
    "wpa3": ("WPA3 Personal (SAE)", 5, True, None, True),
    "wpa2_wpa3": ("WPA2/WPA3 mixed (recommended)", 6, True, "WPA3TRANSITION", True),
    "owe": ("Enhanced Open (OWE, encrypted, no password)", 7, False, None, True),
    "owe_transition": ("Enhanced Open transition (open + OWE)", 8, False, None, True),
}
SECURITY_ORDER = ["wpa2_wpa3", "wpa3", "wpa2", "wpa_wpa2", "owe", "owe_transition", "open"]
SEC_BY_NUM = {v[1]: k for k, v in SECURITY.items()}
SEC_FROM_CLI = {"None": "open", "WEP": "wep", "WPA": "wpa", "WPA2": "wpa2", "WPA2 MIX": "wpa_wpa2", "WPA3": "wpa3",
                "WPA3 Transition": "wpa2_wpa3", "OWE": "owe", "OWE Transition": "owe_transition"}
SEC_FROM_REST = {"None": "open", "WEP64": "wep", "WEP128": "wep", "WPAPSK": "wpa", "WPA2PSK": "wpa2",
                 "WPA2MIX": "wpa_wpa2", "WPA3TRANSITION": "wpa2_wpa3"}
REST_SECURITY = {k for k, v in SECURITY.items() if v[3] and v[4]}
ENCRYPTION = {"None": 0, "64 bit": 1, "128 bit": 2, "TKIP": 3, "AES": 4, "TKIP & AES": 5}
DEFAULT_ENC = {"wpa": 3, "wpa2": 4, "wpa_wpa2": 5, "wpa3": 4, "wpa2_wpa3": 4, "owe": 4, "owe_transition": 4, "open": 4}
PSK_SECURITY = {k for k, v in SECURITY.items() if v[2] and k != "wep"}

# ---- 802.11 modes ------------------------------------------------------------------------------------------
# key: (wifi_api mode, wifi_cli label, band, label, REST string written by the stock UI or None, max width MHz)
MODES = {
    "bgn_ax": (13, "BGNPLUSAX", "2g", "802.11b/g/n/ax (Wi-Fi 6)", "802.11bgn/ax", 40),
    "bgn": (1, "BGN", "2g", "802.11b/g/n (Wi-Fi 4)", "802.11bgn", 40),
    "gn": (5, "GN", "2g", "802.11g/n", None, 40),
    "n": (6, "N2", "2g", "802.11n only", None, 40),
    "bg": (2, "BG", "2g", "802.11b/g (legacy)", None, 20),
    "g": (4, "G", "2g", "802.11g only (legacy)", None, 20),
    "b": (3, "B", "2g", "802.11b only (legacy)", None, 20),
    "acn_ax": (14, "ACNPLUSAX", "5g", "802.11a/n/ac/ax (Wi-Fi 6)", "802.11acn/ax", 80),
    "ac": (11, "ACN5.", "5g", "802.11a/n/ac (Wi-Fi 5)", "802.11ac", 80),
    "ac_only": (12, "AC5ONLY", "5g", "802.11ac only", "802.11ac only", 80),
    "an": (10, "AN", "5g", "802.11a/n", None, 40),
    "n_5g": (9, "N5", "5g", "802.11n only (5 GHz)", None, 40),
    "a": (8, "A", "5g", "802.11a only (legacy)", None, 20),
}
MODE_BY_NUM = {v[0]: k for k, v in MODES.items()}
MODE_FROM_CLI = {v[1]: k for k, v in MODES.items()}
MODE_FROM_CLI.update({"ACN5": "ac", "ACN2": "bgn"})          # wifi_api 7 (AC_2G) reads back as b/g/n
MODE_FROM_REST = {"802.11bgn/ax": "bgn_ax", "802.11bgn": "bgn", "802.11gn": "gn", "802.11n": "n", "802.11bg": "bg",
                  "802.11g": "g", "802.11b": "b", "802.11acn/ax": "acn_ax", "802.11ac": "ac",
                  "802.11ac only": "ac_only", "802.11an": "an", "802.11n 5GHz": "n_5g", "802.11a": "a"}
CAPS_MODE = {"BGN": "bgn", "BG": "bg", "B": "b", "G": "g", "GN": "gn", "N2": "n", "A": "a", "N5": "n_5g", "AN": "an",
             "ACN5.": "ac", "ACN5": "ac", "AC5ONLY": "ac_only", "BGNPLUSAX": "bgn_ax", "ACNPLUSAX": "acn_ax"}
WIDTH_NUM = {20: 1, 40: 2, 80: 3}
WIDTH_FROM_NUM = {1: 20, 2: 40, 3: 80}
BAND_STATE_FROM_REST = {"Primary": "primary", "Guest": "guest", "Off": "off"}
BAND_STATE_TO_REST = {v: k for k, v in BAND_STATE_FROM_REST.items()}

# Interface feature mask bits (wifi_cli get_ap_profile "Interface Feature Flags")
IF_BLACKLIST, IF_WHITELIST, IF_ISOLATION, IF_NO_FILES, IF_NO_WEB = 1, 2, 4, 8, 16
IF_FLAG_NAMES = {"BLACKLIST": IF_BLACKLIST, "WHITELIST": IF_WHITELIST, "INTERFACE_ISOLATION": IF_ISOLATION,
                 "NO_GUEST_FILE_SHARING": IF_NO_FILES, "NO_GUEST_WEB_ACCESS": IF_NO_WEB}

COUNTRY_NAMES = {"US": "United States", "GB": "United Kingdom", "AU": "Australia", "CA": "Canada", "JP": "Japan"}
WPS_PIN_RE = re.compile(r"^\d{4}$|^\d{8}$")
MAC_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


# ---- parsing helpers (pure; tested against fixtures and the documented output shapes) -------------------
def _line_value(text: str, label: str) -> Optional[str]:
    """Exact `label : [value]` from wifi_cli output, without stripping spaces inside the brackets."""
    m = re.search(r"^" + re.escape(label) + r"\s*:\s?\[(.*)\]\s*$", text, re.M)
    return m.group(1) if m else None


def parse_profile(text: str) -> Optional[Dict[str, Any]]:
    """`wifi_cli get_ap_profile` output -> dict (None when the profile could not be read)."""
    if not re.search(r"wifi_get_ap_profile returned 0 \(", text):
        return None
    status = _line_value(text, "Profile status")
    if status is None:
        return None
    flags_raw = _line_value(text, "Interface Feature Flags") or ""
    mask = 0
    for part in flags_raw.split(","):
        mask |= IF_FLAG_NAMES.get(part.strip(), 0)
    sec_label = (_line_value(text, "Security type") or "").strip()
    mode_label = (_line_value(text, "Mode") or "").strip()
    width_label = (_line_value(text, "Channel width") or "").strip()
    width = None
    m = re.match(r"(\d+)\s*MHz", width_label)
    if m:
        width = int(m.group(1))

    def num(label: str, default: int = 0) -> int:
        v = _line_value(text, label)
        try:
            return int((v or "").strip())
        except ValueError:
            return default

    sec = SEC_FROM_CLI.get(sec_label)
    return {
        "enabled": num("Profile status") == 1,
        "name": _line_value(text, "Profile name") or "",
        "ssid": _line_value(text, "SSID") or "",
        "hidden": num("Ignore broadcast SSID") == 1,
        "privacy": num("Wifi privacy") == 1,
        "ifmask": mask,
        "wps": num("WPS status") == 1,
        "channel": num("Channel"),
        "width": width,
        "mode": MODE_FROM_CLI.get(mode_label),
        "mode_label": mode_label,
        "security": sec,
        "security_label": sec_label,
        "encryption": ENCRYPTION.get((_line_value(text, "Encryption type") or "").strip(), 0),
        "psk": _line_value(text, "WPA Pre-Shared Key") or "",
    }


def parse_caps(text: str) -> Dict[str, Any]:
    """`wifi_cli get_caps` -> modes, client limits, countries and per-country channel lists."""
    out: Dict[str, Any] = {"wps": None, "modes": [], "max_clients": None, "system_max_clients": None,
                           "interfaces": None, "countries": [], "channels": {}}
    v = _line_value(text, "WPS Available")
    if v is not None:
        out["wps"] = v.strip() == "1"
    v = _line_value(text, "Allowed Wifi modes")
    if v:
        out["modes"] = [CAPS_MODE[x.strip()] for x in v.split(",") if x.strip() in CAPS_MODE]
    for key, label in (("max_clients", "Wifi support max clients"),
                       ("system_max_clients", "Wifi system max clients allowed"),
                       ("interfaces", "Wifi Max number of interface")):
        v = _line_value(text, label)
        if v is not None and v.strip().isdigit():
            out[key] = int(v.strip())
    v = _line_value(text, "Countries Supported")
    if v:
        out["countries"] = [c.strip() for c in v.split(",") if re.match(r"^[A-Z]{2}$", c.strip())]
    for m in re.finditer(r"^Wifi ([A-Z]{2}) (2\.4|5) GHz supported channels\s*:\s*\[(.*)\]\s*$", text, re.M):
        chans = [int(x) for x in re.findall(r"\d+", m.group(3))]
        out["channels"].setdefault(m.group(1), {})["2g" if m.group(2) == "2.4" else "5g"] = chans
    return out


def parse_chlist(text: str) -> List[int]:
    """`Wifi 2.4 Ghz Channel list: 1,2,...` (no newline before the next line in the real output)."""
    m = re.search(r"Channel list:\s*([0-9][0-9,\s]*)", text)
    return [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []


def parse_mac_list(text: str) -> List[str]:
    """` MAC Addr[0]: [aa:bb:...]` lines of get_sta_list / get_wps_clients."""
    return [m.group(1).upper() for m in re.finditer(r"MAC Addr\[\d+\]:\s*\[([0-9A-Fa-f:]{17})\]", text)]


def wps_pin_ok(pin: str) -> bool:
    """4 digits, or 8 digits with a valid WPS checksum."""
    if not WPS_PIN_RE.match(pin or ""):
        return False
    if len(pin) == 4:
        return True
    d = [int(c) for c in pin]
    acc = 3 * (d[0] + d[2] + d[4] + d[6]) + d[1] + d[3] + d[5] + d[7]
    return acc % 10 == 0


def ssid_suffix(mac: str) -> str:
    """Last two MAC bytes as 4 hex digits (the mod ROMs' `SSID-XXXX` naming)."""
    hexd = re.sub(r"[^0-9A-Fa-f]", "", mac or "")
    return hexd[-4:].upper() if len(hexd) == 12 else ""


def v_ssid(field: str, v: Any) -> str:
    s = validate.text(field, v, 1, 32)
    if any(ord(c) < 32 or ord(c) > 126 for c in s):
        raise field_error(field, "Use letters, digits, spaces and common symbols (no accents or emoji)")
    if s != s.strip():
        raise field_error(field, "The name may not start or end with a space")
    return s


def v_password(field: str, v: Any) -> str:
    s = validate.text(field, v, 0, 63)
    if any(ord(c) < 32 or ord(c) > 126 for c in s):
        raise field_error(field, "Use printable ASCII characters only")
    if len(s) < 8:
        raise field_error(field, "Use 8 to 63 characters")
    return s


def _psk_ok(pw: str) -> bool:
    return 8 <= len(pw or "") <= 63 and all(32 <= ord(c) <= 126 for c in pw)


def profile_answers(index: int, p: Dict[str, Any]) -> List[str]:
    """The 14 answers of `wifi_cli set_ap_profile` (see specs/wifi.py)."""
    sec_key = p["security"]
    sec_num = SECURITY[sec_key][1]
    mode_num = MODES[p["mode"]][0]
    width_num = WIDTH_NUM.get(p.get("width") or 20, 1)
    enc = int(p.get("encryption") or 0)
    if sec_key == "wpa_wpa2":
        enc = 5
    elif sec_key in DEFAULT_ENC and enc not in (3, 4, 5):
        enc = DEFAULT_ENC[sec_key]
    psk = p.get("psk") or "" if sec_key in PSK_SECURITY else ""
    return [str(index), "1" if p["enabled"] else "0", p.get("name") or "", p["ssid"],
            "1" if p["hidden"] else "0", "1" if p["wps"] else "0", "1" if p["privacy"] else "0",
            str(int(p.get("ifmask") or 0)), str(mode_num), str(int(p.get("channel") or 0)), str(width_num),
            str(sec_num), str(enc), psk]


# ---- service --------------------------------------------------------------------------------------------
def register(app):
    r = app.router
    if app.mock and hasattr(app.cli, "store"):
        try:  # mock only: keep the CLI and REST views of the Wi-Fi profiles in one model
            from ..mock_handlers import wifi as wifi_mock
            wifi_mock.link(app.cli.store, app.rest)
        except Exception:  # pragma: no cover - mock module missing/broken
            pass
    w = WifiService(app)
    app.wifi = w            # shared with services/clients.py

    def guard_net(net: str) -> str:
        if net not in NETS:
            raise ApiError(404, "not_found", "Unknown network")
        return net

    for net in NETS:
        def make(net):
            def get_net(req):
                return w.network_view(net)
            get_net.__doc__ = "%s Wi-Fi network: name, security, visibility, WPS, bands." % (
                "Main" if net == "primary" else "Guest")

            def put_net(req):
                return w.put_network(req, net)
            put_net.__doc__ = "Change the %s network (devices on it will reconnect)." % (
                "main" if net == "primary" else "guest")

            def get_secret(req):
                return w.network_secret(req, net)
            get_secret.__doc__ = "Reveal the saved %s Wi-Fi password." % ("main" if net == "primary" else "guest")
            return get_net, put_net, get_secret
        g, p, s = make(net)
        r.add("GET", "/api/wifi/" + net, g)
        r.add("PUT", "/api/wifi/" + net, p)
        r.add("GET", "/api/wifi/" + net + "/secret", s)

    @r.route("GET", "/api/wifi/advanced")
    def get_advanced(req):
        """Radio & advanced: Wi-Fi on/off, band states, 802.11 modes, channels, widths, max clients."""
        return w.advanced_view()

    @r.route("PUT", "/api/wifi/advanced")
    def put_advanced(req):
        """Change radio settings (turning Wi-Fi off needs confirmation)."""
        return w.put_advanced(req)

    @r.route("POST", "/api/wifi/advanced/restore")
    def restore_advanced(req):
        """Restore the radio settings saved before the last change."""
        return w.restore_advanced(req)

    @r.route("GET", "/api/wifi/info")
    def get_info(req):
        """Wi-Fi chip capabilities, MAC addresses, firmware, internal states (read-only)."""
        return w.info_view()

    @r.route("GET", "/api/wifi/country")
    def get_country(req):
        """Wi-Fi country (regulatory domain) and the channels allowed per country."""
        return w.country_view()

    @r.route("PUT", "/api/wifi/country")
    def put_country(req):
        """Change the Wi-Fi country (dangerous: reloads the Wi-Fi driver with other calibration data)."""
        return w.put_country(req)

    @r.route("POST", "/api/wifi/radio")
    def radio(req):
        """Pause (on=false, confirmation) or resume (on=true) the Wi-Fi radio without saving it."""
        return w.radio(req)

    @r.route("GET", "/api/wifi/wps")
    def get_wps(req):
        """WPS state per network/band and the devices that joined with WPS."""
        return w.wps_view()

    @r.route("POST", "/api/wifi/wps/start")
    def wps_start(req):
        """Start a WPS session (push button or PIN) on one network/band."""
        return w.wps_start(req)

    @r.route("POST", "/api/wifi/wps/stop")
    def wps_stop(req):
        """Stop the WPS session on one network/band."""
        return w.wps_stop(req)

    @r.route("GET", "/api/wifi/macfilter")
    def get_macfilter(req):
        """MAC filter mode (allow-list / block-list) and both lists."""
        return w.macfilter_view(req)

    @r.route("PUT", "/api/wifi/macfilter")
    def put_macfilter(req):
        """Switch between allow-list mode and block-list mode."""
        return w.put_macfilter(req)

    @r.route("POST", "/api/wifi/macfilter/allowed")
    def add_allowed(req):
        """Add a device to the allow-list."""
        return w.list_add(req, "allowed")

    @r.route("DELETE", "/api/wifi/macfilter/allowed/{mac}")
    def del_allowed(req):
        """Remove a device from the allow-list."""
        return w.list_remove(req, "allowed", req.params["mac"])

    @r.route("POST", "/api/wifi/macfilter/allow-self")
    def allow_self(req):
        """Add the device you are using to the allow-list."""
        return w.allow_self(req)

    @r.route("POST", "/api/wifi/macfilter/blocked")
    def add_blocked(req):
        """Block a device (it cannot join Wi-Fi)."""
        return w.list_add(req, "blocked")

    @r.route("DELETE", "/api/wifi/macfilter/blocked/{mac}")
    def del_blocked(req):
        """Unblock a device."""
        return w.list_remove(req, "blocked", req.params["mac"])

    app.features.add("wifi.primary", lambda: w.rest_get(REST_NET["primary"]) is not None or w.profiles() is not None)
    app.features.add("wifi.guest", lambda: w.rest_get(REST_NET["guest"]) is not None or w.profiles() is not None)
    app.features.add("wifi.advanced", lambda: w.rest_get(REST_ADV) is not None or w.profiles() is not None)
    app.features.add("wifi.wps", lambda: w.caps().get("wps") is not False)
    app.features.add("wifi.macfilter", lambda: w.rest_list("allowed") is not None or w.rest_list("blocked") is not None)
    app.features.add("wifi.clients", lambda: True)


class WifiService:
    def __init__(self, app):
        self.app = app
        self.radio_paused = False     # last pause/resume action from this UI (no vendor read-back exists)

    # ---- low level reads (cached) -------------------------------------------------------------------
    def rest_get(self, path: str, ttl: float = 5.0) -> Any:
        return self.app.cache.get("wifi:rest:" + path, ttl, lambda: self.app.rest_try(path))

    def cli_text(self, cli: str, cmd: str, answers=(), ttl: float = 10.0) -> Optional[str]:
        key = "wifi:cli:%s:%s:%s" % (cli, cmd, ",".join(answers))

        def run():
            res = self.app.cli_try(cli, cmd, list(answers))
            return res.text if res is not None else None
        return self.app.cache.get(key, ttl, run)

    def profiles(self) -> Optional[Dict[int, Dict[str, Any]]]:
        """All four AP profiles from wifi_cli, or None when they cannot be read."""
        def load():
            out = {}
            for idx in (1, 2, 3, 4):
                txt = self.app.cli_try("wifi_cli", "get_ap_profile", [str(idx)])
                p = parse_profile(txt.text) if txt is not None else None
                if p is None:
                    return None
                out[idx] = p
            return out
        return self.app.cache.get("wifi:profiles", 10, load)

    def caps(self) -> Dict[str, Any]:
        txt = self.cli_text("wifi_cli", "get_caps", ttl=300)
        return parse_caps(txt) if txt else {}

    def chlist(self, band: str) -> List[int]:
        txt = self.cli_text("wifi_cli", "get_ap_chlist2_4" if band == "2g" else "get_ap_chlist5", ttl=60)
        chans = parse_chlist(txt) if txt else []
        if not chans:
            c = self.caps().get("channels", {}).get(self.country() or "US", {})
            chans = c.get(band, [])
        if not chans:  # stock web UI fallbacks (wifi_advanced_settings)
            chans = list(range(1, 12)) if band == "2g" else [36, 40, 44, 48, 149, 153, 157, 161]
        return chans

    def settings(self) -> Dict[str, Any]:
        txt = self.cli_text("wifi_cli", "get_settings", ttl=30)
        if not txt:
            return {}
        en = _line_value(txt, "Wifi is")
        cc = _line_value(txt, "Country Code")
        return {"enabled": (en or "").strip() == "1" if en is not None else None, "country": (cc or "").strip() or None}

    def country(self) -> Optional[str]:
        return self.settings().get("country")

    def ap_settings(self) -> Dict[str, Any]:
        txt = self.cli_text("wifi_cli", "get_ap_settings", ttl=10)
        if not txt:
            return {}
        mode = _line_value(txt, "AP mode is")
        mx = _line_value(txt, "Max Number of Clients")
        out = {}
        if mode is not None and mode.strip().isdigit():
            out["ap_mode"] = int(mode.strip())
        if mx is not None and mx.strip().isdigit():
            out["max_clients"] = int(mx.strip())
        return out

    def max_clients_limit(self) -> int:
        lim = self.rest_get("WiFi/AdvancedSettings/MaxClientsAllowed", 300)
        if isinstance(lim, dict) and isinstance(lim.get("WiFiMaxClientsAllowed"), int) and lim["WiFiMaxClientsAllowed"] > 0:
            return lim["WiFiMaxClientsAllowed"]
        c = self.caps()
        return c.get("system_max_clients") or c.get("max_clients") or 32

    def rest_list(self, which: str) -> Optional[List[Dict[str, Any]]]:
        data = self.rest_get("WhiteListedDevices" if which == "allowed" else "BlockedDevices", 3)
        if data is None:
            return None
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("Devices") or data.get("List") or []
        else:
            items = []
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            mac = it.get("DeviceMacAddress") or it.get("MacAddress") or it.get("ConnectedDeviceMacAddress") or ""
            if not MAC_RE.match(str(mac)):
                continue
            out.append({"mac": str(mac).upper(), "vendor_mac": str(mac),
                        "name": it.get("DeviceFriendlyName") or it.get("FriendlyName") or "",
                        "type": it.get("DeviceApplianceType") or ""})
        return out

    def invalidate(self) -> None:
        self.app.cache.invalidate("wifi:")
        self.app.cache.invalidate("clients:")
        self.app.cache.invalidate("status")
        self.app.cache.invalidate("feature:wifi")

    # ---- CLI write helpers -----------------------------------------------------------------------------
    def run_setter(self, cli: str, cmd: str, answers: List[str], what: str):
        res = self.app.cli.run(cli, cmd, answers, what=what)
        res.raise_for_error(what)
        return res

    def sysint(self, cmd: str, answers: List[str], fn: str, what: str) -> None:
        res = self.app.cli.run("sysintcli", cmd, answers, what=what)
        m = re.search(re.escape(fn) + r" returned (-?\d+)", res.text)
        if res.timed_out or not m or int(m.group(1)) != 0:
            raise vendor_error("%s failed%s" % (what, (" (code %s)" % m.group(1)) if m else ""),
                               {"cli": "sysintcli", "command": cmd})

    def apply_profiles(self) -> None:
        self.run_setter("wifi_cli", "set_all_profiles", [], "Applying the Wi-Fi settings")
        self.app.cache.invalidate("wifi:")   # later steps must see the new profiles

    def write_profile(self, idx: int, p: Dict[str, Any]) -> None:
        if p.get("security") not in SECURITY or not SECURITY[p["security"]][4]:
            raise field_error("security", "This security mode cannot be set here")
        if p.get("mode") not in MODES:
            raise unavailable("The current 802.11 mode of this band is unknown; set it on the Radio page first")
        self.run_setter("wifi_cli", "set_ap_profile", profile_answers(idx, p), "Saving the Wi-Fi profile")

    # ---- network (main / guest) -------------------------------------------------------------------------
    def _net_state(self, net: str) -> Dict[str, Any]:
        rest = self.rest_get(REST_NET[net])
        profs = self.profiles()
        st: Dict[str, Any] = {"net": net, "rest": rest, "profiles": None}
        if profs:
            p2, p5 = profs[PROFILE[(net, "2g")]], profs[PROFILE[(net, "5g")]]
            st["profiles"] = {"2g": p2, "5g": p5}
            main = p2 if (p2["enabled"] or not p5["enabled"]) else p5
            split = p2["ssid"] != p5["ssid"]
            # with separate names "ssid" is always the 2.4 GHz name, whichever band is active
            st.update({"ssid": p2["ssid"] if split else main["ssid"], "ssid_5g": p5["ssid"], "split": split,
                       "security": main["security"], "hidden": main["hidden"],     # None = label not understood
                       "isolation": main["privacy"], "wps": main["wps"], "ifmask": main["ifmask"],
                       "psk": main["psk"], "active": [b for b, p in (("2g", p2), ("5g", p5)) if p["enabled"]]})
        elif isinstance(rest, dict):
            # REST reports WPA3, OWE and OWE transition as "None" too, so "None" is not proof of an open network
            raw = str(rest.get("WiFiSecurityMode", ""))
            sec = None if raw == "None" else SEC_FROM_REST.get(raw)
            ssid = str(rest.get("WiFiSSID", ""))
            st.update({"ssid": ssid, "ssid_5g": ssid, "split": False, "security": sec,
                       "hidden": not rest.get("WiFiSSIDBroadcastEnabled", True),
                       "isolation": bool(rest.get("WiFiPrivacySeparationEnabled", False)),
                       "wps": bool(rest.get("WPSEnabled", False)), "ifmask": None,
                       "psk": str(rest.get("WiFiPassword", "") or ""), "active": None})
        else:
            raise unavailable("Wi-Fi settings are not available on this device right now")
        adv = self.rest_get(REST_ADV)
        if st["active"] is None and isinstance(adv, dict):
            want = "Guest" if net == "guest" else "Primary"
            st["active"] = [b for b, k in (("2g", "WiFi2Dot4GBandState"), ("5g", "WiFi5GBandState")) if adv.get(k) == want]
        return st

    def security_options(self, cli_ok: bool, current: str) -> List[Dict[str, Any]]:
        out = []
        for key in SECURITY_ORDER:
            label, _n, need_pw, rest_str, writable = SECURITY[key]
            if not writable or (not cli_ok and not rest_str):
                continue
            out.append({"value": key, "label": label, "needs_password": need_pw})
        if current and current not in [o["value"] for o in out]:
            out.append({"value": current, "label": SECURITY[current][0] + " (current, cannot be selected again)",
                        "needs_password": SECURITY[current][2], "readonly": True})
        return out

    def wifi_macs(self) -> Dict[str, str]:
        def load():
            out = {}
            for band in BANDS:
                txt = self.app.cli_try("wifi_cli", "get_mac", [IFINDEX[band]])
                m = MAC_RE.search(txt.text) if txt is not None else None
                if not m:   # NV item the mod ROMs read (nwnvitem -r -e NW_NV_MAC_ID_I / _2_I)
                    try:
                        nv = self.app.cli.run("nwnvitem", "-r",
                                              args=["-e", "NW_NV_MAC_ID_I" if band == "2g" else "NW_NV_MAC_ID_2_I"])
                        m = MAC_RE.search(nv.text) if nv.ok or nv.text else None
                    except ApiError:
                        m = None
                if m and m.group(1) != "00:00:00:00:00:00":
                    out[band] = m.group(1).upper()
            if not out:
                info = self.rest_get("DeviceInfo", 300) or {}
                mac = str(info.get("DeviceInfoMacAddress", ""))
                if MAC_RE.match(mac):
                    out["2g"] = mac.upper()
            return out
        return self.app.cache.get("wifi:macs", 300, load)

    def ssid_suggestion(self, net: str) -> str:
        macs = self.wifi_macs()
        mac = macs.get("2g" if net == "primary" else "5g") or macs.get("2g") or ""
        suffix = ssid_suffix(mac)
        base = re.sub(r"[^\x20-\x7e]", "", str(self.app.settings().get("router_name") or "5G Router")).strip() or "Router"
        name = "%s%s" % (base, "-Guest" if net == "guest" else "")
        if suffix:
            name = "%s-%s" % (name[:32 - 5], suffix)
        return name[:32].strip()

    def mac_filter_on(self) -> Optional[bool]:
        profs = self.profiles()
        if not profs:
            return None
        return any(profs[i]["ifmask"] & IF_WHITELIST for i in (1, 2))

    def network_view(self, net: str) -> Dict[str, Any]:
        st = self._net_state(net)
        cli_ok = st["profiles"] is not None
        disp = self.rest_get(REST_DISPLAY) or {}
        suffix = "Primary" if net == "primary" else "Guest"
        other = self._net_state_safe("guest" if net == "primary" else "primary")
        active = st["active"] or []
        out = {
            "network": net,
            "ssid": st["ssid"], "split_ssid": st["split"], "ssid_5g": st["ssid_5g"],
            "security": st["security"],
            "security_label": SECURITY[st["security"]][0] if st["security"] in SECURITY
            else "Unknown (open, WPA3 or Enhanced Open) - choose the mode when you save",
            "password_set": bool(st["psk"]),
            "hidden": st["hidden"], "isolation": st["isolation"], "wps": st["wps"],
            "active_bands": active, "enabled": bool(active),
            "band": "both" if len(active) == 2 else (active[0] if active else "off"),
            "show_ssid_on_screen": (not disp.get("HideSSIDTouchEnabled" + suffix)) if "HideSSIDTouchEnabled" + suffix in disp else None,
            "show_password_on_screen": (not disp.get("HidePasswordTouchEnabled" + suffix)) if "HidePasswordTouchEnabled" + suffix in disp else None,
            "security_options": self.security_options(cli_ok, st["security"]),
            "advanced_available": cli_ok,
            "ssid_suggestion": self.ssid_suggestion(net),
            "other_ssid": other.get("ssid") if other else None,
            "source": "cli" if cli_ok else "rest",
        }
        if net == "guest":
            m = st["ifmask"]
            out.update({"isolate_lan": bool(m & IF_ISOLATION) if m is not None else None,
                        "block_admin": bool(m & IF_NO_WEB) if m is not None else None,
                        "block_file_sharing": bool(m & IF_NO_FILES) if m is not None else None})
        else:
            out["mac_filter_on"] = self.mac_filter_on()
        return out

    def _net_state_safe(self, net: str) -> Optional[Dict[str, Any]]:
        try:
            return self._net_state(net)
        except ApiError:
            return None

    def network_secret(self, req, net: str) -> Dict[str, Any]:
        st = self._net_state(net)
        self.app.audit(req, "wifi.%s.secret" % net)
        out = {"password": st["psk"]}
        if st["profiles"] and st["split"] and st["profiles"]["5g"]["psk"] != st["psk"]:
            out["password_5g"] = st["profiles"]["5g"]["psk"]
        return out

    def put_network(self, req, net: str) -> Dict[str, Any]:
        keys = ["ssid", "security", "password", "hidden", "isolation", "wps", "split_ssid", "ssid_5g",
                "show_ssid_on_screen", "show_password_on_screen"]
        if net == "guest":
            keys += ["enabled", "band", "isolate_lan", "block_admin", "block_file_sharing"]
        data = validate.only_keys(req.json, keys)
        st = self._net_state(net)
        cli_ok = st["profiles"] is not None
        other = self._net_state_safe("guest" if net == "primary" else "primary") or {}
        other_names = {other.get("ssid"), other.get("ssid_5g")} - {None, ""}

        ssid = v_ssid("ssid", data["ssid"]) if "ssid" in data else st["ssid"]
        split = validate.boolean("split_ssid", data["split_ssid"]) if "split_ssid" in data else st["split"]
        ssid5 = v_ssid("ssid_5g", data["ssid_5g"]) if "ssid_5g" in data else (st["ssid_5g"] if split else ssid)
        if not split:
            ssid5 = ssid
        if "ssid" in data and ssid in other_names:
            raise field_error("ssid", "The main and guest networks need different names")
        if split and "ssid_5g" in data and ssid5 in other_names:
            raise field_error("ssid_5g", "The main and guest networks need different names")
        sec = data.get("security", st["security"])
        if "security" in data:
            if sec not in SECURITY or not SECURITY[sec][4]:
                raise field_error("security", "Choose one of the offered security modes")
        if sec not in SECURITY:
            sec = None          # unknown: never guess (guessing "open" would open the network)
        hidden = validate.boolean("hidden", data["hidden"]) if "hidden" in data else st["hidden"]
        isolation = validate.boolean("isolation", data["isolation"]) if "isolation" in data else st["isolation"]
        wps = validate.boolean("wps", data["wps"]) if "wps" in data else st["wps"]
        if hidden and wps:
            if data.get("wps") is True:
                raise field_error("wps", "WPS only works while the network name is visible")
            wps = False
        if wps and sec in ("open", "owe", "owe_transition", "wpa3"):
            if data.get("wps") is True:
                raise field_error("wps", "WPS needs WPA2 or WPA2/WPA3 security")
            wps = False
        if net == "primary" and wps and data.get("wps") is True and self.mac_filter_on():
            raise field_error("wps", "WPS cannot be used while the MAC filter allow-list is on")
        password = v_password("password", data["password"]) if data.get("password") not in (None, "") else None
        mask_change = {k: validate.boolean(k, data[k]) for k in ("isolate_lan", "block_admin", "block_file_sharing")
                       if k in data}
        bands = None
        if net == "guest" and ("enabled" in data or "band" in data):
            en = validate.boolean("enabled", data["enabled"]) if "enabled" in data else bool(st["active"])
            band = validate.choice("band", data.get("band", "both" if not st["active"] or len(st["active"]) == 2
                                                    else st["active"][0]), ["2g", "5g", "both"])
            bands = [] if not en else (["2g", "5g"] if band == "both" else [band])
        disp = {k: validate.boolean(k, data[k]) for k in ("show_ssid_on_screen", "show_password_on_screen") if k in data}

        profile_keys = {"ssid", "security", "password", "hidden", "isolation", "wps", "split_ssid", "ssid_5g",
                        "isolate_lan", "block_admin", "block_file_sharing"}
        touches_profile = any(k in data for k in profile_keys) or (wps != st["wps"])
        if touches_profile and sec is None:
            raise field_error("security", "The current security mode could not be read; choose the security mode to save")
        need_cli = touches_profile and ((sec not in REST_SECURITY) or split or bool(mask_change)
                                        or not isinstance(st["rest"], dict))
        if need_cli and not cli_ok:
            raise unavailable("This option needs direct access to the Wi-Fi profiles, which is not available")
        if touches_profile:
            if need_cli:
                self._put_network_cli(net, st, ssid, ssid5, sec, password, hidden, isolation, wps, mask_change)
            else:
                self._put_network_rest(net, st, ssid, sec, password, hidden, isolation, wps)
        if bands is not None:
            self._set_guest_bands(bands)
        if disp:
            suffix = "Primary" if net == "primary" else "Guest"
            ch = {}
            if "show_ssid_on_screen" in disp:
                ch["HideSSIDTouchEnabled" + suffix] = not disp["show_ssid_on_screen"]
            if "show_password_on_screen" in disp:
                ch["HidePasswordTouchEnabled" + suffix] = not disp["show_password_on_screen"]
            self.app.rest.merge_put(REST_DISPLAY, ch)
        self.invalidate()
        self.app.audit(req, "wifi.%s.update" % net, {"keys": sorted(data), "path": "cli" if need_cli else "rest"})
        return self.network_view(net)

    def _put_network_rest(self, net, st, ssid, sec, password, hidden, isolation, wps) -> None:
        rest = st["rest"]
        if not isinstance(rest, dict):
            raise unavailable("Wi-Fi settings are not available on this device right now")
        ch = {"WiFiSSID": ssid, "WiFiSecurityMode": SECURITY[sec][3], "WiFiSSIDBroadcastEnabled": not hidden,
              "WiFiPrivacySeparationEnabled": isolation, "WPSEnabled": wps}
        if password is not None:
            ch["WiFiPassword"] = password
        elif SECURITY[sec][2] and not _psk_ok(str(rest.get("WiFiPassword") or st.get("psk") or "")):
            raise field_error("password", "Enter a password (8 to 63 characters) for this security mode")
        ch = {k: v for k, v in ch.items() if k in rest}
        self.app.rest.merge_put(REST_NET[net], ch)

    def _put_network_cli(self, net, st, ssid, ssid5, sec, password, hidden, isolation, wps, mask_change) -> None:
        for band in BANDS:
            p = dict(st["profiles"][band])
            p["ssid"] = ssid5 if band == "5g" else ssid
            same_family = p.get("security") == sec
            p["security"] = sec
            if not same_family:
                p["encryption"] = DEFAULT_ENC.get(sec, 4)
            if password is not None:
                p["psk"] = password
            if sec in PSK_SECURITY and not _psk_ok(p.get("psk") or ""):
                raise field_error("password", "Enter a password (8 to 63 characters) for this security mode")
            p["hidden"], p["privacy"], p["wps"] = hidden, isolation, wps
            mask = int(p.get("ifmask") or 0)
            for key, bit in (("isolate_lan", IF_ISOLATION), ("block_admin", IF_NO_WEB),
                             ("block_file_sharing", IF_NO_FILES)):
                if key in mask_change:
                    mask = (mask | bit) if mask_change[key] else (mask & ~bit)
            p["ifmask"] = mask
            if not p.get("name"):
                p["name"] = ("PRIMARY" if net == "primary" else "GUEST") + ("_2_4G" if band == "2g" else "_5G")
            self.write_profile(PROFILE[(net, band)], p)
        self.apply_profiles()

    def _set_guest_bands(self, guest_bands: List[str]) -> None:
        cur = self.band_states()
        new = {}
        for band in BANDS:
            if band in guest_bands:
                new[band] = "guest"
            elif cur.get(band) == "guest":
                new[band] = "primary"
            else:
                new[band] = cur.get(band) or "primary"
        if new != cur:
            self.set_band_states(new)

    # ---- advanced ----------------------------------------------------------------------------------------
    def band_states(self) -> Dict[str, str]:
        adv = self.rest_get(REST_ADV)
        profs = self.profiles()
        out = {}
        for band, key in (("2g", "WiFi2Dot4GBandState"), ("5g", "WiFi5GBandState")):
            if profs:
                if profs[PROFILE[("primary", band)]]["enabled"]:
                    out[band] = "primary"
                elif profs[PROFILE[("guest", band)]]["enabled"]:
                    out[band] = "guest"
                else:
                    out[band] = "off"
            elif isinstance(adv, dict):
                out[band] = BAND_STATE_FROM_REST.get(str(adv.get(key)), "off")
        return out

    def _rest_roundtrip_ok(self, adv: Any) -> bool:
        """Would the vendor accept the current AdvancedSettings object back unchanged?"""
        if not isinstance(adv, dict):
            return False
        try:
            m2 = MODE_FROM_REST.get(str(adv.get("WiFi2Dot4G802Dot11Mode")))
            m5 = MODE_FROM_REST.get(str(adv.get("WiFi5G802Dot11Mode")))
            if m2 not in ("bgn", "bgn_ax") or m5 not in ("ac", "ac_only", "acn_ax"):
                return False
            if str(adv.get("WiFi5GBandwidth")) not in ("20 MHz", "40 MHz", "80 MHz"):
                return False
            for band, key in (("2g", "WiFi2Dot4GChannel"), ("5g", "WiFi5GChannel")):
                v = str(adv.get(key))
                if v != "Automatic" and (not v.isdigit() or int(v) not in self.chlist(band)):
                    return False
            for key in ("WiFi2Dot4GBandState", "WiFi5GBandState"):
                if str(adv.get(key)) not in BAND_STATE_FROM_REST:
                    return False
        except (TypeError, ValueError):
            return False
        return True

    def set_band_states(self, new: Dict[str, str]) -> None:
        if all(new.get(b) == "off" for b in BANDS):
            raise field_error("band_2g", "At least one band must carry a network (turn Wi-Fi off instead)")
        adv = self.rest_get(REST_ADV)
        if self._rest_roundtrip_ok(adv):
            self.app.rest.merge_put(REST_ADV, {"WiFi2Dot4GBandState": BAND_STATE_TO_REST[new["2g"]],
                                               "WiFi5GBandState": BAND_STATE_TO_REST[new["5g"]]})
            return
        if self.profiles() is None:
            raise unavailable("Band settings cannot be changed right now")
        for band in BANDS:  # switch off first, then on (never two networks on one band)
            for net in NETS:
                if new[band] != net:
                    self.run_setter("wifi_cli", "set_profile_status", [str(PROFILE[(net, band)]), "0"],
                                    "Changing the band state")
            if new[band] in NETS:
                self.run_setter("wifi_cli", "set_profile_status", [str(PROFILE[(new[band], band)]), "1"],
                                "Changing the band state")
        self.apply_profiles()

    def _mode_options(self, band: str) -> List[Dict[str, Any]]:
        allowed = set(self.caps().get("modes") or MODES.keys())
        return [{"value": k, "label": v[3], "max_width": v[5], "legacy": v[5] == 20, "rest": bool(v[4])}
                for k, v in MODES.items() if v[2] == band and k in allowed]

    def advanced_view(self) -> Dict[str, Any]:
        adv = self.rest_get(REST_ADV)
        profs = self.profiles()
        if not isinstance(adv, dict) and not profs:
            raise unavailable("Wi-Fi radio settings are not available on this device right now")
        adv = adv if isinstance(adv, dict) else {}
        disp = self.rest_get(REST_DISPLAY) or {}
        states = self.band_states()
        out: Dict[str, Any] = {"enabled": bool(adv.get("WiFiEnabled")) if "WiFiEnabled" in adv else None,
                               "band_2g": states.get("2g"), "band_5g": states.get("5g")}
        for band, mkey, ckey in (("2g", "WiFi2Dot4G802Dot11Mode", "WiFi2Dot4GChannel"),
                                 ("5g", "WiFi5G802Dot11Mode", "WiFi5GChannel")):
            p = None
            if profs:
                st = states.get(band)
                p = profs[PROFILE[(st if st in NETS else "primary", band)]]
            mode = (p and p.get("mode")) or MODE_FROM_REST.get(str(adv.get(mkey)))
            chan = p["channel"] if p else (0 if str(adv.get(ckey, "Automatic")) == "Automatic" else
                                           int(str(adv.get(ckey))) if str(adv.get(ckey, "")).isdigit() else 0)
            width = p.get("width") if p else None
            if width is None and band == "5g":
                m = re.match(r"(\d+)", str(adv.get("WiFi5GBandwidth", "")))
                width = int(m.group(1)) if m else None
            out.update({"mode_" + band: mode, "channel_" + band: chan, "bandwidth_" + band: width,
                        "modes_" + band: self._mode_options(band), "channels_" + band: self.chlist(band)})
        aps = self.ap_settings()
        out["max_clients"] = adv.get("MaxConnections", aps.get("max_clients"))
        out["max_clients_limit"] = self.max_clients_limit()
        out["allow_screen_changes"] = bool(disp["AllowDeviceUIWiFiEnabled"]) if "AllowDeviceUIWiFiEnabled" in disp else None
        teth = self.cli_text("ccm2_cli", "get_wifi_while_usbteth", ttl=10)
        tv = _line_value(teth, "enabled") if teth else None
        out["wifi_while_tethered"] = (tv.strip() == "1") if tv is not None else None
        out["country"] = self.country()
        out["radio_paused"] = self.radio_paused
        out["advanced_available"] = profs is not None
        out["bandwidth_2g_available"] = profs is not None
        out["restore_available"] = bool(self.app.state.snapshots("wifi.advanced"))
        return out

    def put_advanced(self, req, data: Optional[Dict[str, Any]] = None, audit_action: str = "wifi.advanced.update"):
        keys = ["enabled", "band_2g", "band_5g", "mode_2g", "mode_5g", "channel_2g", "channel_5g", "bandwidth_2g",
                "bandwidth_5g", "max_clients", "allow_screen_changes", "wifi_while_tethered"]
        data = validate.only_keys(req.json if data is None else data, keys)
        cur = self.advanced_view()
        ch: Dict[str, Any] = {}
        if "enabled" in data:
            ch["enabled"] = validate.boolean("enabled", data["enabled"])
        for band in BANDS:
            k = "band_" + band
            if k in data:
                ch[k] = validate.choice(k, data[k], ["primary", "guest", "off"])
            k = "mode_" + band
            if k in data:
                opts = [o["value"] for o in cur["modes_" + band]]
                ch[k] = validate.choice(k, data[k], opts)
            k = "channel_" + band
            if k in data:
                n = validate.integer(k, data[k], 0, 196)
                if n and n not in cur["channels_" + band]:
                    raise field_error(k, "Channel %d is not allowed in this country" % n)
                ch[k] = n
            k = "bandwidth_" + band
            if k in data and data[k] is not None:
                ch[k] = validate.choice(k, validate.integer(k, data[k]), [20, 40] if band == "2g" else [20, 40, 80])
        if "max_clients" in data:
            ch["max_clients"] = validate.integer("max_clients", data["max_clients"], 1, cur["max_clients_limit"])
        for k in ("allow_screen_changes", "wifi_while_tethered"):
            if k in data:
                ch[k] = validate.boolean(k, data[k])
        # mode / width combinations the vendor rejects ("Invalid Channel width configured for given mode")
        for band in BANDS:
            mode = ch.get("mode_" + band, cur.get("mode_" + band))
            width = ch.get("bandwidth_" + band, cur.get("bandwidth_" + band))
            if mode in MODES and width and width > MODES[mode][5]:
                if "bandwidth_" + band in ch:
                    raise field_error("bandwidth_" + band, "%s supports at most %d MHz" % (MODES[mode][3], MODES[mode][5]))
                if cur.get("bandwidth_" + band) is not None:
                    ch["bandwidth_" + band] = MODES[mode][5]   # mode change narrows the width automatically
        nb2, nb5 = ch.get("band_2g", cur["band_2g"]), ch.get("band_5g", cur["band_5g"])
        if ("band_2g" in ch or "band_5g" in ch) and nb2 == "off" and nb5 == "off":
            raise field_error("band_2g", "At least one band must carry a network (turn Wi-Fi off instead)")
        if ch.get("enabled") is False and cur.get("enabled") is not False:
            self.app.require_tier(req, 2)
        ch = {k: v for k, v in ch.items() if cur.get(k) != v}
        if not ch:
            return cur
        self.app.state.snapshot("wifi.advanced", {k: cur.get(k) for k in keys if k in cur})
        self._apply_advanced(cur, ch)
        self.invalidate()
        self.app.audit(req, audit_action, {"keys": sorted(ch)})
        return self.advanced_view()

    def _apply_advanced(self, cur: Dict[str, Any], ch: Dict[str, Any]) -> None:
        adv = self.rest_get(REST_ADV)
        rest_ok = self._rest_roundtrip_ok(adv)
        cli_only = ("bandwidth_2g" in ch or any(k in ch and not MODES[ch[k]][4] for k in ("mode_2g", "mode_5g")))
        profs = self.profiles()
        radio_keys = ("mode_2g", "mode_5g", "channel_2g", "channel_5g", "bandwidth_2g", "bandwidth_5g")
        if rest_ok and not cli_only:
            rc: Dict[str, Any] = {}
            if "enabled" in ch:
                rc["WiFiEnabled"] = ch["enabled"]
            if "max_clients" in ch:
                rc["MaxConnections"] = ch["max_clients"]
            for band, bkey, mkey, ckey in (("2g", "WiFi2Dot4GBandState", "WiFi2Dot4G802Dot11Mode", "WiFi2Dot4GChannel"),
                                           ("5g", "WiFi5GBandState", "WiFi5G802Dot11Mode", "WiFi5GChannel")):
                if "band_" + band in ch:
                    rc[bkey] = BAND_STATE_TO_REST[ch["band_" + band]]
                if "mode_" + band in ch:
                    rc[mkey] = MODES[ch["mode_" + band]][4]
                if "channel_" + band in ch:
                    rc[ckey] = "Automatic" if ch["channel_" + band] == 0 else str(ch["channel_" + band])
            if "bandwidth_5g" in ch:
                rc["WiFi5GBandwidth"] = "%d MHz" % ch["bandwidth_5g"]
            if rc:
                self.app.rest.merge_put(REST_ADV, rc)
        else:
            if profs is None:
                raise unavailable("These radio settings need direct access to the Wi-Fi profiles, which is not available")
            if any(k in ch for k in radio_keys):
                for band in BANDS:
                    if not any(k in ch for k in ("mode_" + band, "channel_" + band, "bandwidth_" + band)):
                        continue
                    for net in NETS:   # stock behaviour: radio parameters go to both profiles of a band
                        idx = PROFILE[(net, band)]
                        p = dict(profs[idx])
                        if "mode_" + band in ch:
                            p["mode"] = ch["mode_" + band]
                        if "channel_" + band in ch:
                            p["channel"] = ch["channel_" + band]
                        if "bandwidth_" + band in ch:
                            p["width"] = ch["bandwidth_" + band]
                        elif p.get("mode") in MODES and (p.get("width") or 20) > MODES[p["mode"]][5]:
                            p["width"] = MODES[p["mode"]][5]
                        if p.get("security") not in SECURITY or not SECURITY[p["security"]][4]:
                            raise unavailable("A profile on this band uses legacy security; change it first")
                        self.write_profile(idx, p)
                self.apply_profiles()
            if "band_2g" in ch or "band_5g" in ch:
                self.set_band_states({"2g": ch.get("band_2g", cur["band_2g"]), "5g": ch.get("band_5g", cur["band_5g"])})
            if "max_clients" in ch:
                self.set_max_clients(ch["max_clients"], prefer_rest=False)
            if "enabled" in ch:
                self.run_setter("ccm2_cli", "set_wifi_ap_enabled", ["1" if ch["enabled"] else "0"], "Switching Wi-Fi")
        if "allow_screen_changes" in ch:
            self.app.rest.merge_put(REST_DISPLAY, {"AllowDeviceUIWiFiEnabled": ch["allow_screen_changes"]})
        if "wifi_while_tethered" in ch:
            self.run_setter("ccm2_cli", "set_wifi_while_usbteth", ["1" if ch["wifi_while_tethered"] else "0"],
                            "Changing Wi-Fi while USB tethering")

    def set_max_clients(self, n: int, prefer_rest: bool = True) -> None:
        adv = self.rest_get(REST_ADV)
        if prefer_rest and self._rest_roundtrip_ok(adv):
            self.app.rest.merge_put(REST_ADV, {"MaxConnections": n})
            return
        aps = self.ap_settings()
        self.run_setter("wifi_cli", "set_ap_settings", [str(aps.get("ap_mode", 1)), str(n)],
                        "Changing the Wi-Fi client limit")

    def restore_advanced(self, req) -> Dict[str, Any]:
        snap = self.app.state.last_snapshot("wifi.advanced")
        if not snap:
            raise ApiError(409, "conflict", "There is no previous radio setting to restore")
        data = {k: v for k, v in snap.items() if k in ("enabled", "band_2g", "band_5g", "mode_2g", "mode_5g",
                                                         "channel_2g", "channel_5g", "bandwidth_2g", "bandwidth_5g",
                                                         "max_clients") and v is not None}
        body = dict(data)
        if isinstance(req.json, dict):
            for k in ("confirm", "password"):
                if k in req.json:
                    body[k] = req.json[k]
        return self.put_advanced(_ReqProxy(req, body), audit_action="wifi.advanced.restore")

    # ---- info / country / radio ---------------------------------------------------------------------------
    def info_view(self) -> Dict[str, Any]:
        caps = self.caps()
        info = self.rest_get("DeviceInfo", 300) or {}

        def state(cli, cmd, label):
            txt = self.cli_text(cli, cmd, ttl=10)
            v = _line_value(txt, label) if txt else None
            return None if v is None else v.strip() == "1"
        pidx = {}
        for band in BANDS:
            txt = self.cli_text("wifi_cli", "get_pindex", [IFINDEX[band]], ttl=10)
            m = re.search(r"Profile index on the interface \d+ is :\s*(\d+)", txt or "")
            pidx[IFNAME[band]] = int(m.group(1)) if m else None
        macs = self.wifi_macs()
        return {
            "firmware": info.get("DeviceInfoWiFiFwVesion") or None,
            "caps": {"wps": caps.get("wps"), "modes": caps.get("modes", []), "max_clients": caps.get("max_clients"),
                     "system_max_clients": caps.get("system_max_clients"), "interfaces": caps.get("interfaces"),
                     "countries": caps.get("countries", [])},
            "interfaces": [{"name": IFNAME[b], "band": b, "mac": macs.get(b), "profile": pidx.get(IFNAME[b])} for b in BANDS],
            "states": {"feature": state("wifi_cli", "get_enable", "Wifi feature is"),
                       "access_point": state("wifi_cli", "get_ap_enable", "Wifi AP mode is"),
                       "station": state("wifi_cli", "get_sta_enable", "Wifi STA mode is"),
                       "manager_wifi": state("ccm2_cli", "get_wifi_enabled", "enabled"),
                       "manager_ap": state("ccm2_cli", "get_wifi_ap_enabled", "enabled"),
                       "manager_station": state("ccm2_cli", "get_wifi_stn_enabled", "enabled"),
                       "settings_enabled": self.settings().get("enabled")},
            "daemon_running": self.app.cli_try("wifi_cli", "is_running") is not None,
        }

    def country_view(self) -> Dict[str, Any]:
        caps = self.caps()
        countries = caps.get("countries") or ["US"]
        return {"country": self.country(),
                "options": [{"value": c, "label": "%s (%s)" % (COUNTRY_NAMES.get(c, c), c)} for c in countries],
                "channels": caps.get("channels", {})}

    def put_country(self, req) -> Dict[str, Any]:
        data = validate.only_keys(req.json, ["country"])
        if "country" not in data:
            raise field_error("country", "Choose a country")
        caps = self.caps()
        cc = validate.choice("country", data["country"], caps.get("countries") or ["US"])
        if cc == self.country():
            return self.country_view()
        self.app.require_tier(req, 3)
        st = self.settings()
        self.app.state.snapshot("wifi.country", self.country())
        self.run_setter("wifi_cli", "set_settings", ["0" if st.get("enabled") is False else "1", cc],
                        "Changing the Wi-Fi country")
        self.invalidate()
        self.app.audit(req, "wifi.country.update", {"country": cc})
        return self.country_view()

    def radio(self, req) -> Dict[str, Any]:
        data = validate.only_keys(req.json, ["on"])
        if "on" not in data:
            raise field_error("on", "Say whether the radio should be on")
        on = validate.boolean("on", data["on"])
        if not on:
            self.app.require_tier(req, 2)
        self.run_setter("wifi_cli", "set_wifi_ps", ["1" if on else "0"], "Switching the Wi-Fi radio")
        self.radio_paused = not on
        self.invalidate()
        self.app.audit(req, "wifi.radio", {"on": on})
        return {"radio_paused": self.radio_paused}

    # ---- WPS ---------------------------------------------------------------------------------------------
    def wps_view(self) -> Dict[str, Any]:
        caps = self.caps()
        nets = []
        for net in NETS:
            st = self._net_state_safe(net)
            for band in BANDS:
                active = bool(st and st.get("active") and band in st["active"])
                p = st["profiles"][band] if st and st.get("profiles") else None
                nets.append({"network": net, "band": band, "profile": PROFILE[(net, band)], "active": active,
                             "ssid": (p["ssid"] if p else (st or {}).get("ssid")),
                             "wps_enabled": bool(p["wps"] if p else (st or {}).get("wps")),
                             "hidden": bool(p["hidden"] if p else (st or {}).get("hidden")),
                             "security": (p["security"] if p else (st or {}).get("security"))})
        txt = self.cli_text("wifi_cli", "get_wps_clients", ttl=3)
        return {"available": caps.get("wps"), "entries": nets,
                "clients": [{"mac": m} for m in parse_mac_list(txt or "")],
                "clients_available": txt is not None, "mac_filter_on": self.mac_filter_on()}

    def _wps_target(self, data: Dict[str, Any]) -> Tuple[str, str, int]:
        net = validate.choice("network", data.get("network", "primary"), list(NETS))
        band = validate.choice("band", data.get("band", "2g"), list(BANDS))
        return net, band, PROFILE[(net, band)]

    def wps_start(self, req) -> Dict[str, Any]:
        data = validate.only_keys(req.json, ["network", "band", "method", "pin"])
        net, band, idx = self._wps_target(data)
        method = validate.choice("method", data.get("method", "pbc"), ["pbc", "pin"])
        view = self.wps_view()
        if view.get("available") is False:
            raise unavailable("This Wi-Fi chip does not support WPS")
        entry = [e for e in view["entries"] if e["network"] == net and e["band"] == band][0]
        if not entry["active"]:
            raise field_error("band", "This network is not on that band")
        if not entry["wps_enabled"]:
            raise field_error("network", "Turn on WPS for this network first")
        if view.get("mac_filter_on") and net == "primary":
            raise field_error("network", "WPS cannot be used while the MAC filter allow-list is on")
        pin = ""
        if method == "pin":
            pin = str(data.get("pin", "")).strip()
            if not wps_pin_ok(pin):
                raise field_error("pin", "Enter the 4- or 8-digit PIN shown by the device (8-digit PINs have a check digit)")
        self.run_setter("wifi_cli", "start_wps", [str(idx), "1" if method == "pin" else "2", pin], "Starting WPS")
        self.invalidate()
        self.app.audit(req, "wifi.wps.start", {"network": net, "band": band, "method": method})
        return {"started": True, "network": net, "band": band, "method": method, "expires_s": 120}

    def wps_stop(self, req) -> Dict[str, Any]:
        data = validate.only_keys(req.json, ["network", "band"])
        net, band, idx = self._wps_target(data)
        self.run_setter("wifi_cli", "stop_wps", [str(idx)], "Stopping WPS")
        self.invalidate()
        self.app.audit(req, "wifi.wps.stop", {"network": net, "band": band})
        return {"stopped": True, "network": net, "band": band}

    # ---- MAC filter ----------------------------------------------------------------------------------------
    def client_index(self) -> Dict[str, Dict[str, Any]]:
        """MAC -> {ip, hostname} from the vendor device list and DHCP leases (for self detection)."""
        out: Dict[str, Dict[str, Any]] = {}
        for d in (self.rest_get("ConnectedDevices", 3) or []):
            if isinstance(d, dict) and MAC_RE.match(str(d.get("ConnectedDeviceMacAddress", ""))):
                mac = str(d["ConnectedDeviceMacAddress"]).upper()
                out.setdefault(mac, {}).update({"ip": d.get("ConnectedDeviceIPv4Address") or "",
                                                "name": d.get("ConnectedDeviceFriendlyName") or d.get("ConnectedDeviceHostName") or "",
                                                "connection": str(d.get("ConnectedDeviceConnectionType") or "")})
        try:
            leases = self.app.system.dhcp_leases()
        except Exception:
            leases = []
        for l in leases:
            mac = str(l.get("mac", "")).upper()
            if MAC_RE.match(mac):
                e = out.setdefault(mac, {})
                e.setdefault("ip", l.get("ip", ""))
                if not e.get("ip"):
                    e["ip"] = l.get("ip", "")
                if not e.get("name"):
                    e["name"] = l.get("hostname", "")
        return out

    def self_mac(self, req) -> Optional[str]:
        ip = getattr(req, "client_ip", "") or ""
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        for mac, e in self.client_index().items():
            if e.get("ip") == ip:
                return mac
        return None

    def macfilter_view(self, req) -> Dict[str, Any]:
        allowed = self.rest_list("allowed")
        blocked = self.rest_list("blocked")
        if allowed is None and blocked is None:
            raise unavailable("The MAC filter is not available on this device")
        on = self.mac_filter_on()
        me = self.self_mac(req)
        idx = self.client_index()
        names = getattr(self.app, "client_names", lambda: {})()
        for lst in (allowed or [], blocked or []):
            for e in lst:
                e["name"] = names.get(e["mac"]) or e["name"] or idx.get(e["mac"], {}).get("name", "")
                e["is_self"] = e["mac"] == me
                e["online"] = e["mac"] in idx
        st = self._net_state_safe("primary") or {}
        return {"mode": None if on is None else ("allow" if on else "block"),
                "mode_known": on is not None,
                "allowed": allowed or [], "blocked": blocked or [],
                "allowed_supported": allowed is not None, "blocked_supported": blocked is not None,
                "self_mac": me, "self_allowed": bool(me and any(e["mac"] == me for e in allowed or [])),
                "max_allowed": 100, "max_blocked": 100, "wps_on": bool(st.get("wps"))}

    def put_macfilter(self, req) -> Dict[str, Any]:
        data = validate.only_keys(req.json, ["mode"])
        mode = validate.choice("mode", data.get("mode"), ["allow", "block"])
        view = self.macfilter_view(req)
        if mode == view["mode"]:
            return view
        if mode == "allow":
            if not view["allowed"]:
                raise field_error("mode", "Add at least one device to the allow-list first")
            me = view["self_mac"]
            if me and not view["self_allowed"] and self.client_index().get(me, {}).get("connection", "WiFi").lower().startswith("wi"):
                raise field_error("mode", "Add the device you are using to the allow-list first, or you will be disconnected")
        self.sysint("setMacFilterWhiteListEnabled", ["1" if mode == "allow" else "0"],
                    "sys_setMacFilterWhiteListEnabled", "Changing the MAC filter")
        self.invalidate()
        wps_off = False
        applied = False
        if mode == "allow" and view.get("wps_on"):   # stock behaviour: WPS is switched off on the main network
            try:
                self.put_network(_ReqProxy(req, {"wps": False}), "primary")
                wps_off = applied = True
            except ApiError:
                wps_off = False   # e.g. security unreadable; the filter is on anyway, report it
        if not applied:
            self.apply_profiles()
        self.invalidate()
        self.app.audit(req, "wifi.macfilter.mode", {"mode": mode, "wps_switched_off": wps_off})
        out = self.macfilter_view(req)
        out["wps_switched_off"] = wps_off
        return out

    def list_add(self, req, which: str) -> Dict[str, Any]:
        data = validate.only_keys(req.json, ["mac", "name"])
        if "mac" not in data:
            raise field_error("mac", "Enter a MAC address")
        mac = validate.mac("mac", data["mac"])
        if int(mac[:2], 16) & 1:
            raise field_error("mac", "This is a group (multicast) address, not a device")
        view = self.macfilter_view(req)
        lst = view["allowed" if which == "allowed" else "blocked"]
        if not view["allowed_supported" if which == "allowed" else "blocked_supported"]:
            raise unavailable("This list is not available on this device")
        if any(e["mac"] == mac for e in lst):
            raise field_error("mac", "This device is already on the list")
        if len(lst) >= (view["max_allowed"] if which == "allowed" else view["max_blocked"]):
            raise field_error("mac", "The list is full")
        if which == "blocked" and mac == view["self_mac"]:
            raise field_error("mac", "You cannot block the device you are using")
        name = None
        if data.get("name") not in (None, ""):
            name = validate.text("name", data["name"], 1, 32).strip()
        if which == "blocked":
            self.block(mac)
        else:
            self._rest_mod("WhiteListedDevices", "POST", mac)
        if name and hasattr(self.app, "set_client_name"):
            self.app.set_client_name(mac, name)
        self.invalidate()
        self.app.audit(req, "wifi.macfilter.%s.add" % which, {"mac": mac})
        return self.macfilter_view(req)

    def list_remove(self, req, which: str, raw_mac: str) -> Dict[str, Any]:
        mac = validate.mac("mac", raw_mac)
        view = self.macfilter_view(req)
        lst = view["allowed" if which == "allowed" else "blocked"]
        entry = [e for e in lst if e["mac"] == mac]
        if not entry:
            raise ApiError(404, "not_found", "This device is not on the list")
        if which == "allowed" and view["mode"] == "allow":
            if mac == view["self_mac"]:
                raise field_error("mac", "You would lock yourself out: switch to block-list mode first")
            if len(lst) == 1:
                raise field_error("mac", "The allow-list cannot be empty while it is in use")
        if which == "blocked":
            self.unblock(entry[0]["vendor_mac"])
        else:
            self._rest_mod("WhiteListedDevices", "DELETE", entry[0]["vendor_mac"])
        self.invalidate()
        self.app.audit(req, "wifi.macfilter.%s.remove" % which, {"mac": mac})
        return self.macfilter_view(req)

    def allow_self(self, req) -> Dict[str, Any]:
        me = self.self_mac(req)
        if not me:
            raise unavailable("Your device could not be identified (no DHCP lease or connection entry for your address)")
        view = self.macfilter_view(req)
        if view["self_allowed"]:
            return view
        self._rest_mod("WhiteListedDevices", "POST", me)
        self.invalidate()
        self.app.audit(req, "wifi.macfilter.allowed.add", {"mac": me, "self": True})
        return self.macfilter_view(req)

    def _rest_mod(self, path: str, method: str, mac: str) -> None:
        body = {"DeviceMacAddress": mac}
        if method == "POST":
            self.app.rest.post(path, body)
        else:
            self.app.rest.delete(path, body)

    def block(self, mac: str) -> str:
        """Block a device: vendor device database (persistent); runtime deny-list as fallback."""
        try:
            self._rest_mod("BlockedDevices", "POST", mac)
            return "rest"
        except ApiError as e:
            if e.code not in ("unsupported", "disabled"):
                raise
        for band in BANDS:
            self.run_setter("wifi_cli", "add_mac_blist", [mac, "2", IFNAME[band]], "Blocking the device")
        return "runtime"

    def unblock(self, mac: str) -> None:
        self._rest_mod("BlockedDevices", "DELETE", mac)


class _ReqProxy:
    """A request with another JSON body (used by restore)."""

    def __init__(self, req, body):
        self._req = req
        self.json = body
        self.client_ip = getattr(req, "client_ip", "")
        self.params = getattr(req, "params", {})
