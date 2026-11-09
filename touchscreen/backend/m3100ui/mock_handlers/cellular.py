"""Mock: cellular radio (signal getters, band/tech/NR setters, MNS scan, CA, antenna, ccm2 switches).

Signal getters simulate a connected LTE-A + 5G NSA unit (B66+B2+B13 / n77, consistent with
live_status.py) while `store.state["cellular.live"]` is true (default).  Set it to False to get the
real no-SIM captures from fixtures/cli instead (tests do that to exercise the parsers end to end).
Radio off / airplane mode also fall back to the no-service captures.
Setters rewrite the matching getter text in the vendor format (FixtureStore.set_text), so reads
reflect writes.  argv-mode specs deliver their values in `args`, stdin specs in `answers`.
"""
import math
import random
import time

from ..adapters.mock import CLI_HANDLERS, cli_handler, ok_text

M2 = "modem2_cli"
CCM = "ccm2_cli"
WEBUI = "webui_cli"
T0 = time.time()
TECH_BITS = [("GSM", 0x01), ("UMTS", 0x02), ("CDMA", 0x04), ("EVDO", 0x08), ("LTE", 0x10), ("NR5G", 0x20)]
AC_TEXT = {0: "Manual connect, autoconnect disabled", 1: "Autoconnect when clients are connected",
           2: "Autoconnect when service is available", 3: "Autoconnect when service is available and not roaming"}
MOCK_NETWORKS = [(2, "311480", "Mock Mobile", 7, 0), (1, "310410", "Other Network", 7, 1),
                 (1, "312530", "Third Network", 7, 1), (1, "310260", "Fourth Network", 2, 1)]
RG_LABELS = ["Domestic_roam_enabled", "Domestic_roam_forced", "Domestic_roam_guard_enabled", "Domestic_roam_guard_forced",
             "International_roam_enabled", "International_roam_forced", "International_roam_guard_enabled",
             "International_roam_guard_forced"]


def _wave(base, amp, period, noise):
    t = time.time() - T0
    return int(round(base + amp * math.sin(2 * math.pi * t / period) + random.uniform(-noise, noise)))


def _st(store):
    with store.lock:
        s = store.state.get("cellular")
        if s is None:
            s = store.state["cellular"] = {
                "tech": 0x32, "radio": 1, "radio5g": 1, "air": 0, "connected": True,
                "mns_selected": 0, "mns_oper": "", "scan_started": 0.0, "scan_ready": False,
                "ca_combos": {}, "lte_bands": None,
            }
        return s


def _live(store):
    s = _st(store)
    return bool(store.state.get("cellular.live", True)) and s["radio"] and not s["air"]


def _fixture(store, cli, cmd):
    txt = store.text(cli, cmd)
    if txt is None:
        return "cmd_%s returned 5 (MOCK: no fixture captured for this command.)\n" % cmd
    return txt


def _rc(fn, lines, module="MDM_MAIN", blank=False):
    return "%s returned 0 (%s: success.)\n%s%s\n" % (fn, module, "\n" if blank else "", "\n".join(lines))


def _band_text(fn, bands):
    return _rc(fn, ["count: [%d]" % len(bands)] + ["band[%d]: [%d]" % (i, b) for i, b in enumerate(bands)])


def _mask_names(mask):
    return " ".join(n for n, b in TECH_BITS if mask & b)


def _arg(values, i, default=0):
    try:
        return int(str(values[i]).strip(), 0)
    except (IndexError, ValueError):
        return default


def _live_getter(cmd, cli=M2):
    """Register a getter that returns simulated live data, or the capture when not live."""
    def deco(fn):
        def handler(store, answers, args):
            if not _live(store):
                return _fixture(store, cli, cmd)
            return fn(store, answers, args)
        CLI_HANDLERS[(cli, cmd)] = handler
        return fn
    return deco


# ---- live signal getters ---------------------------------------------------------------------

def _lte(store):
    return {"rsrp": _wave(-92, 9, 90, 2), "rsrq": _wave(-11, 2, 60, 1), "sinr": _wave(14, 6, 70, 2),
            "rssi": _wave(-67, 6, 90, 2), "tx": _wave(6, 4, 40, 1)}


@_live_getter("get_signal")
def _signal(store, answers, args):
    v = _lte(store)
    return _rc("cmd_get_signal", [
        "tech:[14] (LTE CA)", "roam:[0]", "rssi:[%d]" % v["rssi"], "dbm:[%d]" % v["rsrp"], "bars:[4]", "ecio:[0]",
        "sinr:[%d]" % v["sinr"], "rsrq:[%d]" % v["rsrq"], "snr:[%d]" % v["sinr"], "rsrp:[%d]" % v["rsrp"],
        "tx power:[%d]" % v["tx"], "radio temp[41]", "oper name:[Mock Mobile]", "oper id:[311480]",
        "cell id:[0A1B2C3D]", "lac:[]", "tac:[12345]", "femto:[0]", "p_rev:[0]", "sid:[0]", "nid:[0]", "pci:[262]",
        "band:[66]", "bandwidth:[20]", "channel:[66786]", "rsrp:[%d]" % v["rsrp"]], blank=True)


@_live_getter("get_service_info")
def _service(store, answers, args):
    return _rc("cmd_get_service_info", ["Service:[14] (LTE CA)", "Roam:[0]"], blank=True)


@_live_getter("get_oper_info")
def _oper(store, answers, args):
    s = _st(store)
    name, mccmnc = "Mock Mobile", "311480"
    if s["mns_selected"] and s["mns_oper"]:
        for _stat, mm, nm, _act, _roam in MOCK_NETWORKS:
            if mm == s["mns_oper"]:
                name, mccmnc = nm, mm
    return _rc("cmd_get_oper_info", ["Operator:[%s]" % name, "Mcc Mnc:[%s]" % mccmnc, "Freq band:[66]"], blank=True)


CA_CELLS = [(262, 66, 66786, 2, 5, 0), (262, 2, 1050, 2, 3, -4), (17, 13, 5230, 1, 3, -9)]
BW_TEXT = {0: "1.4MHZ", 1: "3MHZ", 2: "5MHZ", 3: "10MHZ", 4: "15MHZ", 5: "20MHZ"}


@_live_getter("get_ca_band_list")
def _ca_list(store, answers, args):
    v = _lte(store)
    lines = []
    for i, (pci, band, earfcn, status, bw, off) in enumerate(CA_CELLS):
        lines += ["ca_bands[%d] - pci:[%d]" % (i, pci), "ca_bands[%d] - band:[%d]" % (i, band),
                  "ca_bands[%d] - earfcn:[%d]" % (i, earfcn), "ca_bands[%d] - status:[%d]" % (i, status),
                  "ca_bands[%d] - bandwidth:[%d] Bandwidth is %s " % (i, bw, BW_TEXT[bw]),
                  "ca_bands[%d] - rsrp:[%d]" % (i, v["rsrp"] + off), "ca_bands[%d] - rsrq:[%d]" % (i, v["rsrq"] - 1),
                  "ca_bands[%d] - rssi:[%d]" % (i, v["rssi"] + off), "ca_bands[%d] - snr:[%d]" % (i, v["sinr"] + off // 2),
                  ""]
    return _rc("cmd_get_ca_band_list", lines)


@_live_getter("get_arfcn_list")
def _arfcn(store, answers, args):
    return _rc("cmd_get_arfcn_list", ["lte_scell_earfcn[0] - dl_earfcn:[1050]", "lte_scell_earfcn[0] - ul_earfcn:[19050]",
                                      "lte_scell_earfcn[1] - dl_earfcn:[5230]", "lte_scell_earfcn[1] - ul_earfcn:[23230]"])


@_live_getter("get_data_rates")
def _rates(store, answers, args):
    rx = max(0, _wave(185000, 60000, 45, 15000))
    tx = max(0, _wave(22000, 8000, 35, 3000))
    return _rc("cmd_get_data_rates", ["cur tx rate: [%X] Kb/s, cur rx rate [%X] Kb/s, max tx rate [%u] Kb/s, "
                                      "max rx rate [%u] Kb/s" % (tx, rx, 75000, 1200000)])


@_live_getter("get_tx_rx_info")
def _txrx(store, answers, args):
    v = _lte(store)
    return _rc("cmd_get_tx_rx_info", ["tx_pwr:[%d] - avg_rx_pwr:[%d]" % (v["tx"], v["rssi"] - 3)])


@_live_getter("get_5g_band_pci")
def _nrbp(store, answers, args):
    return _rc("cmd_get_5g_active_band_pci", ["Active band:[77]", "pci:[505]"])


@_live_getter("active_band_get")
def _aband(store, answers, args):
    return _rc("cmd_active_band_get", ["Active band:[66]"])


@_live_getter("active_channel_get")
def _achan(store, answers, args):
    return _rc("cmd_active_channel_get", ["Frequency(Active channel):[66786]"])


@_live_getter("get_earfcn")
def _earfcn(store, answers, args):
    return _rc("cmd_get_earfcn", ["earfcn:[66786]"])


@_live_getter("get_serving_cell_id")
def _scid(store, answers, args):
    return _rc("cmd_get_serving_cell_id", ["serving_cell_id:[262]"])


@_live_getter("get_ecgi")
def _ecgi(store, answers, args):
    ecgi = (0x311 << 40) | (0x480 << 28) | 0xA1B2C3D
    return _rc("cmd_get_ecgi", ["ecgi:[%d]" % ecgi, "ecgi_hex:[%x]" % ecgi])


@_live_getter("check_lte_ca_status")
def _cacheck(store, answers, args):
    return _rc("cmd_check_lte_ca_status", ["WAN_TECH_LTE_CA"])


@_live_getter("get_bandwidth")
def _bw(store, answers, args):
    return _rc("cmd_get_bandwidth", ["bandwidth :[20]"])


@_live_getter("get_aggregated_bandwidth")
def _abw(store, answers, args):
    return _rc("cmd_get_aggregated_bandwidth", ["bandwidth :[40]"])


@_live_getter("get_neighbor_list")
def _neighbors(store, answers, args):
    v = _lte(store)
    intra = [(262, v["rsrp"] * 10 - 3, v["rsrq"] * 10, v["rssi"] * 10), (263, -1012, -145, -700), (17, -1080, -170, -722)]
    lines = ["LTE Intra Frequency neigbhor stats:"]
    for i in range(8):
        pci, p, q, s = intra[i] if i < len(intra) else (0, 0, 0, 0)
        lines += ["cells[%d] - pci:[%d]" % (i, pci), "cells[%d] - rsrp:[%d]" % (i, p),
                  "cells[%d] - rsrq:[%d]" % (i, q), "cells[%d] - rssi:[%d]" % (i, s), ""]
    lines.append("LTE Inter Frequency neigbhor stats:")
    inter = [(0, 0, 262, -985, -120, -690), (0, 1, 88, -1043, -160, -705), (1, 0, 17, -1101, -190, -730)]
    for f, c, pci, p, q, s in inter:
        lines += ["freq[%d], cells[%d] - pci:[%d]" % (f, c, pci), "freq[%d], cells[%d] - rsrp:[%d]" % (f, c, p),
                  "freq[%d], cells[%d] - rsrq:[%d]" % (f, c, q), "freq[%d], cells[%d] - rssi:[%d]" % (f, c, s)]
    lines += ["", "LTE Info - Neighboring WCDMA:", "", "", "WCDMA Info - LTE Neighbor Cell Info Set:"]
    return _rc("cmd_get_neighbor_list", lines)


@_live_getter("lifetime_counters_get")
def _lifetime(store, answers, args):
    t = int(time.time() - T0)
    return _rc("cmd_lifetime_counters_get", ["Rx = %d KB, Tx = %d KB" % (48200000 + t * 180, 3900000 + t * 15)])


@_live_getter("get_bytes_counters")
def _bytes(store, answers, args):
    t = int(time.time() - T0) + 5400
    fam = _arg(answers or args, 0)
    k = 1 if fam == 0 else 0.08
    return ("\nEnter data call type (0 - IPv4, 2 - IPV6): " +
            _rc("cmd_get_bytes_counters", ["tx_bytes:[%d]" % int((96000000 + t * 15500) * k),
                                           "rx_bytes:[%d]" % int((1250000000 + t * 182000) * k)]))


@_live_getter("get_packet_statistics")
def _packets(store, answers, args):
    t = int(time.time() - T0) + 5400
    fam = _arg(answers or args, 1)
    k = 1 if fam == 0 else 0.08
    return ("Internet = 1\nDevice Management = 2\nEnter APN: Enter ip_family (V4=0, V6=2): " +
            _rc("cmd_get_packet_statistics", ["BytesSent :[%d]" % int((96000000 + t * 15500) * k),
                                              "BytesReceived :[%d]" % int((1250000000 + t * 182000) * k),
                                              "Packet Received :[%d]" % int((900000 + t * 140) * k),
                                              "ErrorsSent :[0]", "ErrorReceived :[0]"]))


@_live_getter("get_network_time")
def _ntime(store, answers, args):
    now = time.time()
    return _rc("cmd_get_network_time", ["Time:[%s]" % time.strftime("%m/%d/%y %I:%M:%S %p", time.gmtime(now)).lower(),
                                        "Timestamp:[%d]" % int(now), "Time zone:[0]", "DST:[0]"], blank=True)


# ---- manual network selection (scan state machine) -------------------------------------------

def _scan_ready(store):
    s = _st(store)
    if s["scan_started"] and not s["scan_ready"]:
        if time.time() - s["scan_started"] >= float(store.state.get("cellular.scan_s", 3.0)):
            s["scan_ready"] = True
    return s["scan_ready"]


@_live_getter("mns_get_info")
def _mns_info(store, answers, args):
    s = _st(store)
    ready = _scan_ready(store)
    status = 2 if ready else (1 if s["scan_started"] else 0)
    return _rc("cmd_get_mns_info", ["Is Allowed:[1]", "Is Selected:[%d]" % s["mns_selected"], "Scan Status:[%d]" % status],
               blank=True)


@_live_getter("mns_get_list")
def _mns_list(store, answers, args):
    if not _scan_ready(store):
        return "cmd_get_mns_list returned 1008 (MDM_MAIN: No scan results available.)\n"
    s = _st(store)
    items = []
    for stat, mm, nm, act, roam in MOCK_NETWORKS:
        if s["mns_selected"] and s["mns_oper"]:
            stat = 2 if mm == s["mns_oper"] else 1
        items.append('+COPS: (%d,"%s","%s",%d,%d)' % (stat, mm, nm, act, roam))
    return _rc("cmd_get_mns_list", ["Mns list output:[%s\n]" % "\n".join(items)], blank=True)


@cli_handler(M2, "mns_start_scan")
def _mns_start(store, answers, args):
    s = _st(store)
    s["scan_started"], s["scan_ready"] = time.time(), False
    return ok_text("cmd_start_mns_scan", "MDM_MAIN")


@cli_handler(M2, "mns_clear_list")
def _mns_clear(store, answers, args):
    s = _st(store)
    s["scan_started"], s["scan_ready"] = 0.0, False
    return ok_text("cmd_clear_mns_list", "MDM_MAIN")


@cli_handler(M2, "mns_set_oper")
def _mns_set(store, answers, args):
    s = _st(store)
    vals = args or answers
    s["mns_selected"] = 1 if _arg(vals, 0) else 0
    s["mns_oper"] = str(vals[1]) if s["mns_selected"] and len(vals) > 1 else ""
    return ok_text("cmd_set_mns_oper", "MDM_MAIN")


@cli_handler(M2, "mns_validate")
def _mns_validate(store, answers, args):
    s = _st(store)
    ok = s["mns_oper"] in [n[1] for n in MOCK_NETWORKS] if s["mns_selected"] else True
    if not ok:
        s["mns_selected"], s["mns_oper"] = 0, ""
    return ok_text("cmd_validate_mns", "MDM_MAIN", [
        "MNS:[%d](%s)" % (1 if ok else 0, "validated." if ok else "disallowed, reverting to automatic network selection.")])


@cli_handler(WEBUI, "set_automatic_network_selection")
def _webui_auto(store, answers, args):
    v = _arg(args or answers, 0)
    store.set_text(WEBUI, "get_automatic_network_selection",
                   "command [get_automatic_network_selection]\n\nwebui_get_automatic_network_selection returned 0 "
                   "(WEBUI: success.)\nautomatic_network_selection enabled:[%d]\n" % (1 if v else 0))
    return "command [set_automatic_network_selection]\n\nwebui_get_automatic_network_selection returned 0 (WEBUI: success.)\n"


# ---- technology / 5G mode / radios (core specs + this area's specs) ---------------------------

def _set_tech(store, mask):
    s = _st(store)
    s["tech"] = mask
    s["radio5g"] = 1 if mask & 0x20 else 0
    store.set_text(M2, "enabled_tech_get", _rc("cmd_get_enabled_tech", ["Enabled tech modes:[%d] ( %s)" % (mask, _mask_names(mask))],
                                               blank=True))
    store.set_text(M2, "active_tech_get", _rc("cmd_get_active_tech", ["Active tech modes:[%d] ( %s)" % (mask, _mask_names(mask))],
                                              blank=True))
    store.set_text(M2, "5g_radio_is_enabled", _rc("cmd_5g_radio_is_enabled",
                                                  ["Radio enabled: (disabled=0, enabled=1):[%d]" % s["radio5g"]]))


@cli_handler(M2, "enabled_tech_set")
def _tech_set(store, answers, args):
    text = str((answers or args or [""])[0]).upper()
    mask = 0
    for n, b in TECH_BITS:
        if n in text.split(","):
            mask |= b
    if not mask:
        return "Enabling Tech Modes...\ncmd_set_enabled_tech returned 2 (MDM_MAIN: one or more bad parameters passed into a function.)\n"
    _set_tech(store, mask)
    return "Enabling Tech Modes...\n" + ok_text("cmd_set_enabled_tech", "MDM_MAIN")


@cli_handler(M2, "set_nr5g_disable_mode")
def _nr_mode(store, answers, args):
    v = _arg(answers or args, 0)
    if v > 2:
        return "cmd_set_nr5g_disable_mode returned 2 (MDM_MAIN: one or more bad parameters passed into a function.)\n"
    store.set_text(M2, "get_nr5g_disable_mode", _rc("cmd_get_nr5g_disable_mode", ["disable mode:[%d]" % v]))
    return ok_text("cmd_set_nr5g_disable_mode", "MDM_MAIN")


@cli_handler(M2, "5g_radio_set_enabled")
def _radio5g(store, answers, args):
    v = 1 if _arg(args or answers, 0) else 0
    s = _st(store)
    _set_tech(store, (s["tech"] | 0x20) if v else (s["tech"] & ~0x20))
    return ok_text("cmd_5g_radio_set_enabled", "MDM_MAIN")


@cli_handler(M2, "radio_set_enabled")
def _radio(store, answers, args):
    v = 1 if _arg(args or answers, 0) else 0
    _st(store)["radio"] = v
    store.set_text(M2, "radio_is_enabled", _rc("cmd_radio_is_enabled", ["Radio enabled: (disabled=0, enabled=1):[%d]" % v]))
    return ok_text("cmd_radio_set_enabled", "MDM_MAIN")


@cli_handler(CCM, "set_air_mode_enabled")
def _air(store, answers, args):
    v = 1 if _arg(args or answers, 0) else 0
    _st(store)["air"] = v
    store.set_text(CCM, "get_air_mode_enabled", _rc("ccm_get_air_mode_enabled", ["enabled:[%d]" % v], module="CCM"))
    return _rc("ccm_set_air_mode_enabled", [], module="CCM")


@cli_handler(CCM, "set_cell_data_enabled")
def _cell_data(store, answers, args):
    v = 1 if _arg(args or answers, 0) else 0
    store.set_text(CCM, "get_cell_data_enabled", _rc("ccm_get_cell_data_enabled", ["enabled:[%d]" % v], module="CCM"))
    return _rc("ccm_set_cell_data_enabled", [], module="CCM")


@cli_handler(CCM, "set_wwan_ac")
def _wwan_ac(store, answers, args):
    v = _arg(args or answers, 0)
    if v not in AC_TEXT:
        return "ccm_set_wwan_ac returned 2 (CCM: one or more bad parameters passed into a function.)\n"
    store.set_text(CCM, "get_wwan_ac", _rc("ccm_get_wwan_ac", ["Autoconnect Type:[%d] %s" % (v, AC_TEXT[v])], module="CCM"))
    return _rc("ccm_set_wwan_ac", [], module="CCM")


def _conn_text(up):
    st = "Connected" if up else "Idle"
    return _rc("ccm_get_conn_state", ["Wan:[WWAN]", "V4:[%s]" % st, "V4 IF:[%s]" % ("rmnet_data0" if up else ""),
                                      "V6:[%s]" % st, "V6 IF:[%s]" % ("rmnet_data0" if up else ""), ""], module="CCM")


def _set_connected(store, up):
    _st(store)["connected"] = up
    store.set_text(CCM, "get_conn_state", _conn_text(up))
    try:  # the Network area mocks get_conn_state from its own state: keep both views in step
        from . import network as _net
        _net.S(store)["wan_connected"] = up
    except Exception:  # noqa: BLE001  (other area's mock missing or changed: ours still works)
        pass


@cli_handler(CCM, "wwan_connect")
def _connect(store, answers, args):
    _set_connected(store, True)
    return "Enter an optional call id (<enter> for default):  " + _rc("ccm_wwan_connect", [], module="CCM")


@cli_handler(CCM, "wwan_disconnect")
def _disconnect(store, answers, args):
    _set_connected(store, False)
    return "Enter an optional call id (<enter> for default):  " + _rc("ccm_wwan_disconnect", [], module="CCM")


# ---- roaming / roam guard / 5G icon timer ------------------------------------------------------

@cli_handler(M2, "roam_set_enabled")
def _roam(store, answers, args):
    v = 1 if _arg(args or answers, 0) else 0
    store.set_text(M2, "roam_get_enabled", _rc("cmd_roam_get_enabled", ["Roaming enabled:[%d]" % v], blank=True))
    return ok_text("cmd_roam_set_enabled", "MDM_MAIN")


@cli_handler(M2, "roam_set_intl_enabled")
def _roam_intl(store, answers, args):
    v = 1 if _arg(args or answers, 0) else 0
    store.set_text(M2, "roam_get_intl_enabled", _rc("cmd_roam_get_intl_enabled", ["Roaming International:[%d]" % v], blank=True))
    return ok_text("cmd_roam_set_intl_enabled", "MDM_MAIN")


@cli_handler(M2, "set_roam_guard_settings")
def _roam_guard(store, answers, args):
    vals = [1 if _arg(args or answers, i) else 0 for i in range(8)]
    store.set_text(M2, "get_roam_guard_settings", _rc("cmd_get_roam_guard_settings",
                                                      ["%s:[%d]" % (lab, v) for lab, v in zip(RG_LABELS, vals)], blank=True))
    return ok_text("cmd_set_roam_guard_settings", "MDM_MAIN", ["", "Setting Roam_guard_settings"])


@cli_handler(M2, "set_5g_tuwb")
def _tuwb(store, answers, args):
    v = _arg(args or answers, 0)
    if not 0 <= v <= 30:
        return "cmd_set_5g_tuwb returned 2 (MDM_MAIN: one or more bad parameters passed into a function.)\n"
    store.set_text(M2, "get_5g_tuwb", _rc("cmd_get_5g_tuwb", ["tuwb:[%d]" % v]))
    return ok_text("cmd_set_5g_tuwb", "MDM_MAIN")


# ---- bands / priority / CA ----------------------------------------------------------------------

def _bands_setter(cmd, getter):
    def handler(store, answers, args):
        vals = answers or args
        n = _arg(vals, 0, -1)
        bands = []
        for v in vals[1:1 + max(n, 0)]:
            b = _arg([v], 0)
            if b not in bands:
                bands.append(b)
        if n < 1 or len(bands) != n:
            return "cmd_%s returned 2 (MDM_MAIN: one or more bad parameters passed into a function.)\n" % cmd
        store.set_text(M2, getter, _band_text("cmd_" + getter, bands))
        if getter == "get_enabled_lte_bands":
            _st(store)["lte_bands"] = bands
        return ("Enter number of bands to enable(max 512): " + "Enter band number: " * n +
                ok_text("cmd_" + cmd, "MDM_MAIN"))
    CLI_HANDLERS[(M2, cmd)] = handler


_bands_setter("set_enabled_lte_bands", "get_enabled_lte_bands")
_bands_setter("set_enabled_nr5g_sa_bands", "get_enabled_nr5g_sa_bands")
_bands_setter("set_enabled_nr5g_nsa_bands", "get_enabled_nr5g_nsa_bands")


def _priority_setter(cmd, getter):
    def handler(store, answers, args):
        vals = answers or args
        n = _arg(vals, 0, -1)
        bands = [_arg([v], 0) for v in vals[1:1 + max(n, 0)]]
        if n < 0 or len(bands) != n:
            return "cmd_%s returned 2 (MDM_MAIN: one or more bad parameters passed into a function.)\n" % cmd
        store.set_text(M2, getter, _band_text("cmd_" + getter, bands))
        return ok_text("cmd_" + cmd, "MDM_MAIN")
    CLI_HANDLERS[(M2, cmd)] = handler


_priority_setter("set_configured_band_priority_list", "get_configured_band_priority_list")
_priority_setter("set_configured_nr5g_band_priority_list", "get_configured_nr5g_band_priority_list")


@cli_handler(M2, "ca_set_enabled")
def _ca_set(store, answers, args):
    v = 1 if _arg(args or answers, 0) else 0
    store.set_text(M2, "ca_get_enabled", _rc("cmd_ca_get_enabled", ["CA enabled:[%d]" % v]))
    return ok_text("cmd_ca_set_enabled", "MDM_MAIN")


def _combo_key(vals, n):
    return ",".join(str(_arg(vals, i)) for i in range(n))


@cli_handler(M2, "ca_bands_set_enabled")
def _ca2_set(store, answers, args):
    vals = args or answers
    _st(store)["ca_combos"][_combo_key(vals, 2)] = 1 if _arg(vals, 2) else 0
    return ok_text("cmd_ca_bands_set_enabled", "MDM_MAIN")


@cli_handler(M2, "ca_tri_bands_set_enabled")
def _ca3_set(store, answers, args):
    vals = args or answers
    _st(store)["ca_combos"][_combo_key(vals, 3)] = 1 if _arg(vals, 3) else 0
    return ok_text("cmd_ca_tri_bands_set_enabled", "MDM_MAIN")


def _combo_get(cmd, n):
    def handler(store, answers, args):
        vals = answers or args
        key = _combo_key(vals, n)
        combos = _st(store)["ca_combos"]
        if key not in combos and not store.state.get("cellular.live", True):
            return _fixture(store, M2, cmd)
        prompts = "".join("Enter CA band %d: " % (i + 1) for i in range(n))
        return prompts + _rc("cmd_" + cmd, ["LTE band enabled:[%d]" % combos.get(key, 1)])
    CLI_HANDLERS[(M2, cmd)] = handler


_combo_get("ca_bands_get_enabled", 2)
_combo_get("ca_tri_bands_get_enabled", 3)


@cli_handler(M2, "set_autonomous_gap_enabled")
def _gap(store, answers, args):
    v = 1 if _arg(args or answers, 0) else 0
    store.set_text(M2, "get_autonomous_gap_enabled", _rc("cmd_get_autonomous_gap_enabled", ["Autonomous gap enabled:[%d]" % v]))
    return ok_text("cmd_set_autonomous_gap_enabled", "MDM_MAIN")


@cli_handler(M2, "set_ext_ant_enabled")
def _ext(store, answers, args):
    vals = args or answers
    m, u = (1 if _arg(vals, 0) else 0), (1 if _arg(vals, 1) else 0)
    store.set_text(M2, "get_ext_ant_enabled", _rc("cmd_get_ext_antenna_enabled", ["mhb enabled:[%d]" % m, "uhb enabled:[%d]" % u]))
    return ok_text("cmd_set_ext_antenna_enabled", "MDM_MAIN")


@cli_handler(M2, "set_lte_wifi_coex")
def _coex(store, answers, args):
    return ok_text("cmd_set_lte_wifi_coex", "MDM_MAIN")


# ---- EFS band preference read (nwcli qmi_idl read_file <local> <efs> <max>) --------------------

_PREV_NWCLI = CLI_HANDLERS.get(("nwcli", "qmi_idl"))


@cli_handler("nwcli", "qmi_idl")
def _nwcli(store, answers, args):
    if len(args) == 4 and args[0] == "read_file" and args[2].startswith("/nv/item_files/modem/mmode/lte_bandpref"):
        s = _st(store)
        bands = s.get("lte_bands")
        if bands is None:
            from ..adapters.cli import CliResult
            txt = store.text(M2, "get_enabled_lte_bands") or ""
            res = CliResult(M2, "get_enabled_lte_bands", txt, 0)
            bands = [_arg([v], 0) for v in res.indexed("band")]
        extn = args[2].endswith("_65_256")
        size, base = (24, 65) if extn else (8, 1)
        buf = bytearray(size)
        for b in bands:
            k = b - base
            if 0 <= k < size * 8:
                buf[k // 8] |= 1 << (k % 8)
        store.state.setdefault("shm", {})[args[1]] = bytes(buf)
        return "nwqmi_open_one rc=[0]\nread_file %s -> %s (%d bytes) success\n" % (args[2], args[1], size)
    if _PREV_NWCLI:
        return _PREV_NWCLI(store, answers, args)
    return "qmi_idl returned 5 (MOCK: no data for this EFS path.)\n"
