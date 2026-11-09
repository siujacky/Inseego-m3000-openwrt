"""Mock Wi-Fi: one model behind the wifi_cli / ccm2_cli / sysintcli outputs AND the vendor REST views.

The real captures (fixtures/cli/wifi_cli) were taken without answering the prompting read commands
(get_ap_profile, get_mac, get_pindex print "Invalid ... index"), so their answered shapes are rebuilt here
from the printf formats found by static analysis (see adapters/specs/wifi.py, docs/features/wifi.md).
REST views emulate the vendor quirks that matter to the service: WPA3/OWE read back as "None", PUT
validation of the merged object (SSID rules, SSID clash, password length, WPS needs a visible name, only the
stock 802.11 modes/bandwidths), and one profile per band.

services/wifi.py calls link(store, rest) in mock mode so the CLI handlers (FixtureStore) and the REST
handlers (FakeRest) share the model.
"""
import copy
import json
import os
import re
import weakref

from ..adapters.mock import cli_handler, rest_handler
from ..errors import ApiError

_LINKS = weakref.WeakKeyDictionary()   # FakeRest -> FixtureStore
_ATTR = "_m3ui_wifi_model"

MBH_HEAD = "WIFI_CLI_MBH: Init MsgBus donecommand [%s]\n\n"
MBH_TAIL = "WIFI_CLI_MBH: Disable Message bus interface\n"
SYSINT_HEAD = "Starting sysint client library\n"
SYSINT_TAIL = "Stopping sysint client library 0\nsysinterfaceShutdown complete 0\n"

SEC_FROM_REST = {"None": 0, "WEP64": 1, "WEP128": 1, "WPAPSK": 2, "WPA2PSK": 3, "WPA2MIX": 4, "WPA3TRANSITION": 6}
SEC_TO_REST = {0: "None", 1: "WEP64", 2: "WPAPSK", 3: "WPA2PSK", 4: "WPA2MIX", 6: "WPA3TRANSITION"}  # else "None"
SEC_LABEL = {0: "None", 1: "WEP", 2: "WPA", 3: "WPA2", 4: "WPA2 MIX", 5: "WPA3", 6: "WPA3 Transition", 7: "OWE",
             8: "OWE Transition"}
ENC_LABEL = {0: "None", 1: "64 bit", 2: "128 bit", 3: "TKIP", 4: "AES", 5: "TKIP & AES"}
MODE_LABEL = {1: "BGN", 2: "BG", 3: "B", 4: "G", 5: "GN", 6: "N2", 7: "ACN2", 8: "A", 9: "N5", 10: "AN", 11: "ACN5.",
              12: "AC5ONLY", 13: "BGNPLUSAX", 14: "ACNPLUSAX"}
MODE_TO_REST = {1: "802.11bgn", 2: "802.11bg", 3: "802.11b", 4: "802.11g", 5: "802.11bgn", 6: "802.11n", 7: "802.11bgn",
                8: "802.11a", 9: "802.11n 5GHz", 10: "802.11an", 11: "802.11ac", 12: "802.11ac only",
                13: "802.11bgn/ax", 14: "802.11acn/ax"}
REST_MODE_2G = {"802.11bgn": 1, "802.11bgn/ax": 13}            # the only values the vendor PUT accepts
REST_MODE_5G = {"802.11ac": 11, "802.11ac only": 12, "802.11acn/ax": 14}
MODES_2G = {1, 2, 3, 4, 5, 6, 7, 13}
WIDTH_LABEL = {1: "20 MHz.", 2: "40 MHz.", 3: "80 MHz."}
WIDTH_REST = {1: "20 MHz", 2: "40 MHz", 3: "80 MHz"}
CHANNELS = {"US": ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], [36, 40, 44, 48, 149, 153, 157, 161]),
            "GB": ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], [36, 40, 44, 48]),
            "AU": ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], [36, 40, 44, 48, 149, 153, 157, 161]),
            "CA": ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], [36, 40, 44, 48, 149, 153, 157, 161])}
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def link(store, rest) -> None:
    store.state["wifi.rest"] = rest
    _LINKS[rest] = store


# ---- model ------------------------------------------------------------------------------------------------
def _seed(data):
    prim = data.get("WiFi/PrimarySettings") or {}
    guest = data.get("WiFi/GuestSettings") or {}
    adv = data.get("WiFi/AdvancedSettings") or {}
    pssid = str(prim.get("WiFiSSID") or "Router")
    gssid = str(guest.get("WiFiSSID") or "Router-Guest")
    if gssid == pssid:      # redacted fixture: both names identical; the vendor never allows that
        gssid = (pssid + "-Guest")[:32]
    mode2 = {"802.11bgn": 1, "802.11bgn/ax": 13}.get(adv.get("WiFi2Dot4G802Dot11Mode"), 13)
    mode5 = {"802.11ac": 11, "802.11ac only": 12, "802.11acn/ax": 14}.get(adv.get("WiFi5G802Dot11Mode"), 14)

    def chan(v):
        return int(v) if str(v).isdigit() else 0
    ch2, ch5 = chan(adv.get("WiFi2Dot4GChannel")), chan(adv.get("WiFi5GChannel"))
    w5 = {"20 MHz": 1, "40 MHz": 2, "80 MHz": 3}.get(adv.get("WiFi5GBandwidth"), 3)
    b2, b5 = adv.get("WiFi2Dot4GBandState", "Primary"), adv.get("WiFi5GBandState", "Primary")

    def prof(src, ssid, name, enabled, mode, ch, width, mask):
        sec = SEC_FROM_REST.get(str(src.get("WiFiSecurityMode")), 3)
        return {"status": 1 if enabled else 0, "name": name, "ssid": ssid,
                "ignore_bcast": 0 if src.get("WiFiSSIDBroadcastEnabled", True) else 1,
                "privacy": 1 if src.get("WiFiPrivacySeparationEnabled") else 0, "ifmask": mask,
                "wps": 1 if src.get("WPSEnabled") else 0, "channel": ch, "width": width, "mode": mode,
                "security": sec, "encryption": 5 if sec == 4 else 4, "psk": str(src.get("WiFiPassword") or "")}
    return {
        "profiles": {1: prof(prim, pssid, "PRIMARY_2_4G", b2 == "Primary", mode2, ch2, 1, 1),
                     2: prof(prim, pssid, "PRIMARY_5G", b5 == "Primary", mode5, ch5, w5, 1),
                     3: prof(guest, gssid, "GUEST_2_4G", b2 == "Guest", mode2, ch2, 1, 1 | 4),
                     4: prof(guest, gssid, "GUEST_5G", b5 == "Guest", mode5, ch5, w5, 1 | 4)},
        "wifi_enabled": bool(adv.get("WiFiEnabled", True)), "ap_mode": 1,
        "max_clients": int(adv.get("MaxConnections") or 32), "country": "US", "settings_enabled": 1,
        "while_tethered": 1, "radio_on": 1, "sta_enabled": 0,
        "macs": ["02:00:00:00:10:01", "02:00:00:00:10:02"],
        "stations": [{"mac": "02:00:00:00:00:02", "ip": "192.168.1.101", "host": "phone", "profile": 2},
                     {"mac": "02:00:00:00:00:03", "ip": "192.168.1.103", "host": "tablet", "profile": 1}],
        "kicked": [], "wps_clients": [], "wps_sessions": {}, "allowed": [], "blocked": [], "whitelist": False,
        "names": {}, "runtime_deny": [],
    }


def _model_rest(rest):
    m = getattr(rest, _ATTR, None)
    if m is None:
        m = _seed(rest.data)
        setattr(rest, _ATTR, m)
    return m


def _model_store(store):
    rest = store.state.get("wifi.rest")
    if rest is not None:
        return _model_rest(rest), rest.lock
    m = store.state.get("wifi.model")
    if m is None:
        try:
            with open(os.path.join(store.root, "rest", "get.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        m = store.state["wifi.model"] = _seed(data)
    return m, store.lock


def _status(fn, rc=0, msg="success"):
    return "%s returned %d (WIFI: %s.)\n" % (fn, rc, msg)


def _visible_stations(m):
    allowed = {a["mac"].upper() for a in m["allowed"]}
    blocked = {b["mac"].upper() for b in m["blocked"]} | {x.upper() for x in m["runtime_deny"]}
    out = []
    for s in m["stations"]:
        mac = s["mac"].upper()
        p = m["profiles"].get(s.get("profile", 1))
        if mac in blocked or not m["wifi_enabled"] or not m["radio_on"] or not p or not p["status"]:
            continue
        if m["whitelist"] and mac not in allowed:
            continue
        out.append(s)
    return out


def _active(m, band):
    """Active profile index on a band (1/3 = 2.4 GHz, 2/4 = 5 GHz) or None."""
    a, b = (1, 3) if band == "2g" else (2, 4)
    return a if m["profiles"][a]["status"] else (b if m["profiles"][b]["status"] else None)


# ---- REST views -------------------------------------------------------------------------------------------
def _net_view(m, net):
    a, b = (1, 2) if net == "primary" else (3, 4)
    p = m["profiles"][a] if m["profiles"][a]["status"] or not m["profiles"][b]["status"] else m["profiles"][b]
    return {"WPSEnabled": bool(p["wps"]), "WiFiPassword": p["psk"], "WiFiPrivacySeparationEnabled": bool(p["privacy"]),
            "WiFiSSID": p["ssid"], "WiFiSSIDBroadcastEnabled": not p["ignore_bcast"],
            "WiFiSecurityMode": SEC_TO_REST.get(p["security"], "None")}


def _adv_view(m):
    out = {"MaxConnections": m["max_clients"], "WiFiEnabled": bool(m["wifi_enabled"])}
    for band, mk, ck, bk in (("2g", "WiFi2Dot4G802Dot11Mode", "WiFi2Dot4GChannel", "WiFi2Dot4GBandState"),
                             ("5g", "WiFi5G802Dot11Mode", "WiFi5GChannel", "WiFi5GBandState")):
        idx = _active(m, band)
        p = m["profiles"][idx or (1 if band == "2g" else 2)]
        out[mk] = MODE_TO_REST.get(p["mode"], "802.11bgn")
        out[ck] = "Automatic" if not p["channel"] else str(p["channel"])
        out[bk] = "Off" if idx is None else ("Primary" if idx in (1, 2) else "Guest")
        if band == "5g":
            out["WiFi5GBandwidth"] = WIDTH_REST.get(p["width"], "80 MHz")
    return out


def _vendor_fail(method, path, msg="bad request passed in"):
    return ApiError(502, "vendor_error", "Vendor REST %s %s: %s (mock)" % (method, path, msg),
                    {"vendor_status": 400, "resource": path})


def _ssid_ok(s):
    return isinstance(s, str) and 1 <= len(s) <= 32 and not (s[0] == " " and s[-1] == " ") and \
        all(32 <= ord(c) <= 126 for c in s)


def _put_net(rest, body, net, path):
    m = _model_rest(rest)
    full = _net_view(m, net)
    full.update(body or {})
    other = m["profiles"][3 if net == "primary" else 1]["ssid"]
    if not _ssid_ok(full.get("WiFiSSID")):
        raise _vendor_fail("PUT", path, "Invalid ssid")
    if full["WiFiSSID"] == other:
        raise _vendor_fail("PUT", path, "Invalid ssid as clashing with %s ssid" % ("guest" if net == "primary" else "primary"))
    sec = SEC_FROM_REST.get(str(full.get("WiFiSecurityMode")))
    if sec is None:
        raise _vendor_fail("PUT", path, "Invalid security type")
    pw = str(full.get("WiFiPassword") or "")
    if sec != 0 and not (8 <= len(pw) <= 63 and all(32 <= ord(c) <= 126 for c in pw)):
        raise _vendor_fail("PUT", path, "Invalid password in case of non None security")
    if full.get("WPSEnabled") and not full.get("WiFiSSIDBroadcastEnabled"):
        raise _vendor_fail("PUT", path, "Invalid broadcast enabled and wps enabled combination")
    for idx in ((1, 2) if net == "primary" else (3, 4)):
        p = m["profiles"][idx]
        p.update({"ssid": full["WiFiSSID"], "security": sec, "psk": pw, "ignore_bcast": 0 if full["WiFiSSIDBroadcastEnabled"] else 1,
                  "wps": 1 if full.get("WPSEnabled") else 0, "privacy": 1 if full.get("WiFiPrivacySeparationEnabled") else 0,
                  "encryption": 5 if sec == 4 else (4 if sec else 0)})
    return None


@rest_handler("GET", "WiFi/PrimarySettings")
def _get_primary(rest, body):
    return _net_view(_model_rest(rest), "primary")


@rest_handler("GET", "WiFi/GuestSettings")
def _get_guest(rest, body):
    return _net_view(_model_rest(rest), "guest")


@rest_handler("PUT", "WiFi/PrimarySettings")
def _put_primary(rest, body):
    return _put_net(rest, body, "primary", "WiFi/PrimarySettings")


@rest_handler("PUT", "WiFi/GuestSettings")
def _put_guest(rest, body):
    return _put_net(rest, body, "guest", "WiFi/GuestSettings")


@rest_handler("GET", "WiFi/AdvancedSettings")
def _get_adv(rest, body):
    return _adv_view(_model_rest(rest))


@rest_handler("GET", "WiFi/AdvancedSettings/MaxClientsAllowed")
def _get_max_allowed(rest, body):
    return {"WiFiMaxClientsAllowed": 32}


@rest_handler("PUT", "WiFi/AdvancedSettings")
def _put_adv(rest, body):
    path = "WiFi/AdvancedSettings"
    m = _model_rest(rest)
    full = _adv_view(m)
    full.update(body or {})
    try:
        mc = int(full["MaxConnections"])
    except (TypeError, ValueError):
        raise _vendor_fail("PUT", path)
    if not 1 <= mc <= 32:
        raise _vendor_fail("PUT", path, "Invalid twifiadvancedmaxconnections")
    if full["WiFi2Dot4G802Dot11Mode"] not in REST_MODE_2G or full["WiFi5G802Dot11Mode"] not in REST_MODE_5G:
        raise _vendor_fail("PUT", path, "Invalid 802.11 mode")
    if full["WiFi5GBandwidth"] not in ("20 MHz", "40 MHz", "80 MHz"):
        raise _vendor_fail("PUT", path, "Invalid twifiadvanced5gbandwidth")
    c2, c5 = CHANNELS.get(m["country"], CHANNELS["US"])
    for key, allowed in (("WiFi2Dot4GChannel", c2), ("WiFi5GChannel", c5)):
        v = str(full[key])
        if v != "Automatic" and (not v.isdigit() or int(v) not in allowed):
            raise _vendor_fail("PUT", path, "Invalid channel")
    for key in ("WiFi2Dot4GBandState", "WiFi5GBandState"):
        if full[key] not in ("Off", "Primary", "Guest"):
            raise _vendor_fail("PUT", path, "Invalid band state")
    m["max_clients"] = mc
    m["wifi_enabled"] = bool(full["WiFiEnabled"])
    for band, (pa, pb), mk, ck, bk in (("2g", (1, 3), "WiFi2Dot4G802Dot11Mode", "WiFi2Dot4GChannel", "WiFi2Dot4GBandState"),
                                       ("5g", (2, 4), "WiFi5G802Dot11Mode", "WiFi5GChannel", "WiFi5GBandState")):
        mode = (REST_MODE_2G if band == "2g" else REST_MODE_5G)[full[mk]]
        ch = 0 if full[ck] == "Automatic" else int(full[ck])
        for idx in (pa, pb):
            p = m["profiles"][idx]
            p["mode"], p["channel"] = mode, ch
            if band == "5g":
                p["width"] = {"20 MHz": 1, "40 MHz": 2, "80 MHz": 3}[full["WiFi5GBandwidth"]]
        m["profiles"][pa]["status"] = 1 if full[bk] == "Primary" else 0
        m["profiles"][pb]["status"] = 1 if full[bk] == "Guest" else 0
    return None


def _list_view(m, key):
    return [{"DeviceApplianceType": "Generic", "DeviceFriendlyName": m["names"].get(e["mac"].upper(), e.get("name", "")),
             "DeviceMacAddress": e["mac"]} for e in m[key]]


def _list_mod(rest, body, key, add, path):
    m = _model_rest(rest)
    mac = str((body or {}).get("DeviceMacAddress") or "")
    if not MAC_RE.match(mac):
        raise _vendor_fail("POST" if add else "DELETE", path, "Incoming data wrong or malformed or incomplete")
    have = [e for e in m[key] if e["mac"].upper() == mac.upper()]
    if add:
        if not have:
            m[key].append({"mac": mac.upper(), "name": ""})
    else:
        if not have:
            raise ApiError(502, "vendor_error", "Vendor REST DELETE %s: Operation failed during execution (mock)" % path,
                           {"vendor_status": 500, "resource": path})
        m[key] = [e for e in m[key] if e["mac"].upper() != mac.upper()]
    return None


@rest_handler("GET", "WhiteListedDevices")
def _get_white(rest, body):
    return _list_view(_model_rest(rest), "allowed")


@rest_handler("GET", "BlockedDevices")
def _get_blocked(rest, body):
    return _list_view(_model_rest(rest), "blocked")


@rest_handler("POST", "WhiteListedDevices")
def _post_white(rest, body):
    return _list_mod(rest, body, "allowed", True, "WhiteListedDevices")


@rest_handler("DELETE", "WhiteListedDevices")
def _del_white(rest, body):
    return _list_mod(rest, body, "allowed", False, "WhiteListedDevices")


@rest_handler("POST", "BlockedDevices")
def _post_blocked(rest, body):
    return _list_mod(rest, body, "blocked", True, "BlockedDevices")


@rest_handler("DELETE", "BlockedDevices")
def _del_blocked(rest, body):
    return _list_mod(rest, body, "blocked", False, "BlockedDevices")


@rest_handler("GET", "ConnectedDevices")
def _get_connected(rest, body):
    m = _model_rest(rest)
    out = copy.deepcopy(rest.data.get("ConnectedDevices") or [])
    for d in out:
        name = m["names"].get(str(d.get("ConnectedDeviceMacAddress", "")).upper())
        if name:
            d["ConnectedDeviceFriendlyName"] = name
    for s in _visible_stations(m):
        out.append({"ConnectedDeviceApplianceType": "Generic", "ConnectedDeviceConnectionType": "WiFi",
                    "ConnectedDeviceFriendlyName": m["names"].get(s["mac"].upper(), ""),
                    "ConnectedDeviceHostName": s.get("host", ""), "ConnectedDeviceIPv4Address": s.get("ip", ""),
                    "ConnectedDeviceIPv6Address": "", "ConnectedDeviceMacAddress": s["mac"]})
    return out


@rest_handler("GET", "ConnectedDevicesStatus")
def _get_connected_status(rest, body):
    m = _model_rest(rest)
    base = dict(rest.data.get("ConnectedDevicesStatus") or {})
    base["ConnectedDevicesCountWiFi"] = int(base.get("ConnectedDevicesCountWiFi") or 0) + len(_visible_stations(m))
    return base


# ---- wifi_cli ---------------------------------------------------------------------------------------------
def _profile_text(p):
    flags = "".join(name for bit, name in ((1, "BLACKLIST, "), (2, "WHITELIST, "), (4, "INTERFACE_ISOLATION, "),
                                           (8, "NO_GUEST_FILE_SHARING, "), (16, "NO_GUEST_WEB_ACCESS"))
                    if p["ifmask"] & bit)
    lines = ["Profile status         : [%d]" % p["status"], "Profile name           : [%s]" % p["name"],
             "SSID                   : [%s]" % p["ssid"], "Ignore broadcast SSID  : [%d]" % p["ignore_bcast"],
             "Wifi privacy           : [%d]" % p["privacy"], "Interface Feature Flags: [%s]" % flags,
             "WPS status             : [%d]" % p["wps"], "Channel                : [%d]" % p["channel"],
             "Channel width          : [%s]" % WIDTH_LABEL.get(p["width"], "INVALID"),
             "Mode                   : [%s]" % MODE_LABEL.get(p["mode"], "INVALID"),
             "Security type          : [%s]" % SEC_LABEL.get(p["security"], "INVALID"),
             "Encryption type        : [%s]" % ENC_LABEL.get(p["encryption"], "INVALID")]
    if p["security"] > 1:
        lines.append("WPA Pre-Shared Key     : [%s]" % p["psk"])
    return "\n".join(lines) + "\n"


@cli_handler("wifi_cli", "get_ap_profile")
def _get_ap_profile(store, answers, args):
    m, lock = _model_store(store)
    head = MBH_HEAD % "get_ap_profile" + "Enter the Wifi profile index (1-4):"
    idx = answers[0].strip() if answers else (args[0] if args else "")
    if not idx.isdigit() or int(idx) not in m["profiles"]:
        return head + "Invalid profile index.\n" + MBH_TAIL
    with lock:
        return head + _status("wifi_get_ap_profile") + _profile_text(m["profiles"][int(idx)]) + MBH_TAIL


PROMPTS_SET_PROFILE = ["Profile index (1-5): ", "Profile status (0/1): ", "Profile name (1 - 20 characters): ",
                       "SSID (1 - 32 characters):", "Broadcast SSID  (broadcast=0 /no broadcast=1): ",
                       "WPS status (0/1): ", "Wifi privacy (0/1): ", "Interface Feature Mask (...): ", "Mode (...): ",
                       "Channel (...): ", "Channel Width (...): ", "Security type (...): ", "Enryption Mode (...): ",
                       "Passphrase Key for WPA,WPA2, WPA2Mix Mode or WPA3 Mode (8 - 64 characters): "]


@cli_handler("wifi_cli", "set_ap_profile")
def _set_ap_profile(store, answers, args):
    m, lock = _model_store(store)
    a = list(answers) + [""] * (14 - len(answers))
    head = MBH_HEAD % "set_ap_profile"

    def fail(msg, n):
        return head + "".join(PROMPTS_SET_PROFILE[:n]) + "%s: error code:2 (invalid parameter)\n" % msg + MBH_TAIL

    def num(i):
        try:
            return int(a[i])
        except ValueError:
            return None
    idx = num(0)
    if idx not in (1, 2, 3, 4):
        return fail("Invalid profile Index", 1)
    for i, msg, lo, hi in ((1, "Invalid profile status", 0, 1), (4, "Invalid broadcast SSID status", 0, 1),
                           (5, "Invalid WPS status", 0, 1), (6, "Invalid Wifi privacy", 0, 1),
                           (7, "Invalid Interface feature Mask", 0, 31), (8, "Invalid Wifi mode", 1, 14),
                           (9, "Invalid Channel", 0, 196), (10, "Invalid channel width", 1, 3),
                           (11, "Invalid Wifi Security", 0, 8)):
        v = num(i)
        if v is None or not lo <= v <= hi:
            return fail(msg, i + 1)
    if not 1 <= len(a[2]) <= 20 or not 1 <= len(a[3]) <= 32:
        return fail("Invalid Profile name or SSID", 4)
    band = "2g" if idx in (1, 3) else "5g"
    mode, ch, width, sec = num(8), num(9), num(10), num(11)
    if (mode in MODES_2G) != (band == "2g"):
        return head + _status("wifi_set_ap_profile", 2, "invalid parameter") + MBH_TAIL
    c2, c5 = CHANNELS.get(m["country"], CHANNELS["US"])
    if ch and ch not in (c2 if band == "2g" else c5):
        return head + _status("wifi_set_ap_profile", 2, "invalid parameter") + MBH_TAIL
    if (band == "2g" and width > 2) or (mode in (2, 3, 4, 8) and width > 1):
        return head + _status("wifi_set_ap_profile", 2, "invalid parameter") + MBH_TAIL
    enc = num(12) if sec else 0
    psk = a[13] if 2 <= sec <= 6 else ""
    if sec and (enc is None or not 1 <= enc <= 5):
        return fail("Invalid Wifi Encryption", 13)
    if 2 <= sec <= 6 and not 8 <= len(psk) <= 63:
        return head + _status("wifi_set_ap_profile", 2, "invalid parameter") + MBH_TAIL
    if sec == 1:
        return head + _status("wifi_set_ap_profile", 2, "invalid parameter") + MBH_TAIL
    with lock:
        m["profiles"][idx].update({"status": num(1), "name": a[2], "ssid": a[3], "ignore_bcast": num(4), "wps": num(5),
                                   "privacy": num(6), "ifmask": num(7), "mode": mode, "channel": ch, "width": width,
                                   "security": sec, "encryption": enc, "psk": psk})
    return head + "".join(PROMPTS_SET_PROFILE) + _status("wifi_set_ap_profile") + MBH_TAIL


@cli_handler("wifi_cli", "set_all_profiles")
def _set_all_profiles(store, answers, args):
    m, lock = _model_store(store)
    head = MBH_HEAD % "set_all_profiles"
    with lock:
        p = m["profiles"]
        if (p[1]["status"] and p[3]["status"]) or (p[2]["status"] and p[4]["status"]):
            return head + _status("wifi_ap_apply_profiles", 2, "invalid parameter") + MBH_TAIL
    return head + _status("wifi_ap_apply_profiles") + "Wifi profiles applied successfully.\n" + MBH_TAIL


@cli_handler("wifi_cli", "set_profile_status")
def _set_profile_status(store, answers, args):
    m, lock = _model_store(store)
    head = MBH_HEAD % "set_profile_status" + "Enter the profile index (1-4):Enter the profile status (0=Disable, 1=Enable):"
    try:
        idx, st = int(answers[0]), int(answers[1])
    except (IndexError, ValueError):
        return head + "Invalid profile index.\n" + MBH_TAIL
    if idx not in m["profiles"] or st not in (0, 1):
        return head + "Invalid profile status.\n" + MBH_TAIL
    with lock:
        m["profiles"][idx]["status"] = st
    return head + _status("wifi_set_ap_profile") + MBH_TAIL


@cli_handler("wifi_cli", "get_settings")
def _get_settings(store, answers, args):
    m, _ = _model_store(store)
    return (MBH_HEAD % "get_settings" + _status("wifi_get_wlan_settings") +
            "Wifi is                     : [%d]\nCountry Code                : [%s]\n" % (m["settings_enabled"], m["country"]) + MBH_TAIL)


@cli_handler("wifi_cli", "set_settings")
def _set_settings(store, answers, args):
    m, lock = _model_store(store)
    head = MBH_HEAD % "set_settings" + "Wifi status (0/1): Country Code (US/JP/GB/AU/CA): "
    if len(answers) < 2 or answers[0] not in ("0", "1"):
        return head + "Invalid Wifi status: error code:2 (invalid parameter)\n" + MBH_TAIL
    if answers[1] not in CHANNELS:
        return head + "Invalid country code is given.\n" + _status("wifi_set_wlan_settings", 2, "invalid parameter") + MBH_TAIL
    with lock:
        m["settings_enabled"], m["country"] = int(answers[0]), answers[1]
        c2, c5 = CHANNELS[answers[1]]
        for idx, p in m["profiles"].items():   # channels not allowed in the new country fall back to automatic
            if p["channel"] and p["channel"] not in (c2 if idx in (1, 3) else c5):
                p["channel"] = 0
    return head + _status("wifi_set_wlan_settings") + MBH_TAIL


@cli_handler("wifi_cli", "get_ap_chlist2_4")
def _chlist24(store, answers, args):
    m, _ = _model_store(store)
    return (MBH_HEAD % "get_ap_chlist2_4" + "Wifi 2.4 Ghz Channel list: %s" %
            ",".join(str(c) for c in CHANNELS.get(m["country"], CHANNELS["US"])[0]) + MBH_TAIL)


@cli_handler("wifi_cli", "get_ap_chlist5")
def _chlist5(store, answers, args):
    m, _ = _model_store(store)
    return (MBH_HEAD % "get_ap_chlist5" + "Wifi 5 Ghz Channel list: %s" %
            ",".join(str(c) for c in CHANNELS.get(m["country"], CHANNELS["US"])[1]) + MBH_TAIL)


@cli_handler("wifi_cli", "get_ap_settings")
def _get_ap_settings(store, answers, args):
    m, _ = _model_store(store)
    return (MBH_HEAD % "get_ap_settings" + _status("wifi_get_ap_settings") +
            "AP mode is             : [%d] \nMax Number of Clients  : [%d]\n" % (m["ap_mode"], m["max_clients"]) + MBH_TAIL)


@cli_handler("wifi_cli", "set_ap_settings")
def _set_ap_settings(store, answers, args):
    m, lock = _model_store(store)
    head = MBH_HEAD % "set_ap_settings" + "AP status (0/1): Max number of clients: "
    try:
        mode, mx = int(answers[0]), int(answers[1])
    except (IndexError, ValueError):
        return head + "Invalid AP status: error code:2 (invalid parameter)\n" + MBH_TAIL
    if mode not in (0, 1) or not 1 <= mx <= 32:
        return head + "Invalid Max number of clients: error code:2 (invalid parameter)\n" + MBH_TAIL
    with lock:
        m["ap_mode"], m["max_clients"] = mode, mx
    return head + _status("wifi_set_ap_settings") + MBH_TAIL


@cli_handler("wifi_cli", "get_ap_enable")
def _get_ap_enable(store, answers, args):
    m, _ = _model_store(store)
    on = 1 if m["wifi_enabled"] and m["radio_on"] else 0
    return MBH_HEAD % "get_ap_enable" + _status("wifi_get_ap_enabled") + "Wifi AP mode is :[%d] \n" % on + MBH_TAIL


@cli_handler("wifi_cli", "set_wifi_ps")
def _set_wifi_ps(store, answers, args):
    m, lock = _model_store(store)
    head = MBH_HEAD % "set_wifi_ps" + "Enter Wifi status due to powersave (0=Disable, 1=Enable):"
    if not answers or answers[0] not in ("0", "1"):
        return head + "Invalid Wifi status due to powersave.\n" + MBH_TAIL
    with lock:
        m["radio_on"] = int(answers[0])
    return head + "wifi_set_power_save_enabled returned 0 (WIFI: success.)\n" + MBH_TAIL


@cli_handler("wifi_cli", "get_mac")
def _get_mac(store, answers, args):
    m, _ = _model_store(store)
    head = MBH_HEAD % "get_mac" + "Enter the Wifi interface index 0/1:"
    i = (answers[0] if answers else "").strip()
    if i not in ("0", "1"):
        return head + "Invalid Wifi interface index.\n" + MBH_TAIL
    return (head + "wifi_get_mac_addr returned 0 (WIFI: success.)\nThe mac address for Wifi interface is :[%s]\n"
            % m["macs"][int(i)].lower() + MBH_TAIL)


@cli_handler("wifi_cli", "get_pindex")
def _get_pindex(store, answers, args):
    m, _ = _model_store(store)
    head = MBH_HEAD % "get_pindex" + "Enter the Wifi interface index 0/1:"
    i = (answers[0] if answers else "").strip()
    if i not in ("0", "1"):
        return head + "Invalid Wifi interface index.\n" + MBH_TAIL
    idx = _active(m, "2g" if i == "0" else "5g") or 0
    return head + _status("wifi_get_pindex_from_intf") + "Profile index on the interface %s is :%d \n" % (i, idx) + MBH_TAIL


@cli_handler("wifi_cli", "get_sta_list")
def _get_sta_list(store, answers, args):
    m, lock = _model_store(store)
    with lock:
        st = _visible_stations(m)
    lines = ["STA associated with the AP are : [%d]" % len(st)]
    lines += [" MAC Addr[%d]: [%s]" % (i, s["mac"].lower()) for i, s in enumerate(st)]
    return MBH_HEAD % "get_sta_list" + _status("wifi_ap_get_associate_sta_list") + "\n".join(lines) + "\n" + MBH_TAIL


@cli_handler("wifi_cli", "get_wps_clients")
def _get_wps_clients(store, answers, args):
    m, _ = _model_store(store)
    lines = ["WPS STA associated with the AP are : [%d]" % len(m["wps_clients"])]
    lines += [" MAC Addr[%d]: [%s]" % (i, mac.lower()) for i, mac in enumerate(m["wps_clients"])]
    return MBH_HEAD % "get_wps_clients" + "\n".join(lines) + "\n" + MBH_TAIL


@cli_handler("wifi_cli", "start_wps")
def _start_wps(store, answers, args):
    m, lock = _model_store(store)
    head = MBH_HEAD % "start_wps" + "Enter the AP profile index (1-5):Enter the WPS methos PIN or PBC (1-2):"
    try:
        idx, method = int(answers[0]), int(answers[1])
    except (IndexError, ValueError):
        return head + "Invalid wifi interface index.\n" + MBH_TAIL
    if idx not in m["profiles"]:
        return head + "Invalid wifi AP index is given.\n" + MBH_TAIL
    if method not in (1, 2):
        return head + "Invalid WPS method.\n" + MBH_TAIL
    p = m["profiles"][idx]
    if not p["status"] or not p["wps"]:
        return head + _status("wifi_start_wps_session", 3, "operation not allowed") + MBH_TAIL
    with lock:   # simulate a device joining with WPS
        mac = "02:00:00:00:00:%02X" % (0x0A + len(m["wps_clients"]))
        if mac not in m["wps_clients"]:
            m["wps_clients"].append(mac)
            m["stations"].append({"mac": mac, "ip": "192.168.1.%d" % (110 + len(m["wps_clients"])), "host": "wps-device",
                                  "profile": idx})
        m["wps_sessions"][idx] = "pin" if method == 1 else "pbc"
    return head + ("Enter WPS Pin:" if method == 1 else "") + _status("wifi_start_wps_session") + MBH_TAIL


@cli_handler("wifi_cli", "stop_wps")
def _stop_wps(store, answers, args):
    m, lock = _model_store(store)
    head = MBH_HEAD % "stop_wps" + "Enter the Wifi AP profile index (1-5):"
    try:
        idx = int(answers[0])
    except (IndexError, ValueError):
        return head + "Invalid AP profile index.\n" + MBH_TAIL
    if idx not in m["profiles"]:
        return head + "Invalid wifi AP profile index.\n" + MBH_TAIL
    with lock:
        m["wps_sessions"].pop(idx, None)
    return head + _status("wifi_stop_wps_session") + MBH_TAIL


@cli_handler("wifi_cli", "add_mac_blist")
def _add_mac_blist(store, answers, args):
    m, lock = _model_store(store)
    head = MBH_HEAD % "add_mac_blist" + "Enter blacklist MAC address:"
    if not answers or not MAC_RE.match(answers[0]):
        return head + "Invalid MAC address.\n" + MBH_TAIL
    with lock:
        if answers[0].upper() not in m["runtime_deny"]:
            m["runtime_deny"].append(answers[0].upper())
    return (head + "Enter the Reason for blacklist(1=WL,2=BL,3=Both):Enter the interface name <wlan0/wlan1>:Mac=[%s]\n"
            % answers[0] + "wifi_update_blacklist_mac returned 0 (WIFI: success.)\n" + MBH_TAIL)


# ---- ccm2_cli ---------------------------------------------------------------------------------------------
@cli_handler("ccm2_cli", "get_wifi_while_usbteth")
def _get_teth(store, answers, args):
    m, _ = _model_store(store)
    return "ccm_get_wifi_while_usbteth returned 0 (CCM: success.)\nenabled:[%d]\n" % m["while_tethered"]


@cli_handler("ccm2_cli", "set_wifi_while_usbteth")
def _set_teth(store, answers, args):
    m, lock = _model_store(store)
    with lock:
        m["while_tethered"] = 1 if (answers and answers[0].strip() == "1") else 0
    return "What is enabled status [0|1]:  ccm_set_wifi_while_usbteth returned 0 (CCM: success.)\n"


@cli_handler("ccm2_cli", "get_wifi_ap_enabled")
def _get_ccm_ap(store, answers, args):
    m, _ = _model_store(store)
    return "ccm_get_wifi_ap_enabled returned 0 (CCM: success.)\nenabled:[%d]\n" % (1 if m["wifi_enabled"] else 0)


@cli_handler("ccm2_cli", "set_wifi_ap_enabled")
def _set_ccm_ap(store, answers, args):
    m, lock = _model_store(store)
    with lock:
        m["wifi_enabled"] = bool(answers and answers[0].strip() == "1")
    return "What is enabled status [0|1]:  ccm_set_wifi_ap_enabled returned 0 (CCM: success.)\n"


# ---- sysintcli --------------------------------------------------------------------------------------------
@cli_handler("sysintcli", "setMacFilterWhiteListEnabled")
def _set_whitelist(store, answers, args):
    m, lock = _model_store(store)
    on = bool(answers and answers[0].strip() == "1")
    with lock:
        m["whitelist"] = on
        for p in m["profiles"].values():   # sys_setMacFilterWhiteListEnabled also updates the profile flags
            p["ifmask"] = (p["ifmask"] | 2) if on else (p["ifmask"] & ~2)
    return SYSINT_HEAD + "Enable ? ( 1 or 0 ): sys_setMacFilterWhiteListEnabled returned 0\n" + SYSINT_TAIL


@cli_handler("sysintcli", "setDeviceName")
def _set_device_name(store, answers, args):
    m, lock = _model_store(store)
    if len(answers) < 2 or not MAC_RE.match(answers[0].strip()):
        return SYSINT_HEAD + "Enter the mac address(xx:xx:xx:xx:xx:xx):Enter device name :sys_setDeviceType returned 4\n" + SYSINT_TAIL
    with lock:
        m["names"][answers[0].strip().upper()] = answers[1]
    return SYSINT_HEAD + "Enter the mac address(xx:xx:xx:xx:xx:xx):Enter device name :sys_setDeviceType returned 0\n" + SYSINT_TAIL
