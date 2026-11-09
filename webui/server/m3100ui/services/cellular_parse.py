"""Cellular area: parsers, enums and radio tables (pure functions, no routes).

Every parser here is tested against the real (no-SIM) captures in fixtures/cli/ and against the
shapes the mock handlers emit.  Output formats come from the modem2_cli/ccm2_cli format strings
(docs/recon/modem2-cli.md section 7 and the disassembly notes in docs/features/cellular.md).
"""
import re
from typing import Any, Dict, List, Optional

# -- generic helpers -------------------------------------------------------------------------


def to_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    if v is None:
        return default
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    s = str(v).strip()
    if not s:
        return default
    try:
        return int(s, 0) if s.lower().startswith(("0x", "-0x")) else int(float(s))
    except ValueError:
        return default


def to_float(v: Any) -> Optional[float]:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def nz(v: Any) -> Optional[float]:
    """A radio measurement where the vendor uses 0 for "not available"."""
    f = to_float(v)
    if f is None or f == 0:
        return None
    return int(f) if f == int(f) else round(f, 1)


def tenths(v: Any) -> Optional[float]:
    """QMI cell-location values are in 1/10 dB(m): -998 -> -99.8.  0 means "no value"."""
    i = to_int(v)
    if not i:
        return None
    return round(i / 10.0, 1)


# -- technology masks (enabled_tech_get / get_sup_tech / active_tech_get / enabled_tech_set) ----

TECH_BITS = [("GSM", 0x01), ("UMTS", 0x02), ("CDMA", 0x04), ("EVDO", 0x08), ("LTE", 0x10), ("NR5G", 0x20)]
TECH_LABEL = {"GSM": "2G (GSM)", "UMTS": "3G (UMTS)", "CDMA": "CDMA 1x", "EVDO": "EV-DO", "LTE": "4G LTE", "NR5G": "5G NR"}
ALL_TECH_MASK = 0x3F

# Presets offered on the Network mode page.  50/18/32 are the options the vendor settings
# list (webui_cli get_network_technology_options); 48/16 come from the hidden lab page
# (LTE only) and the obvious "no 3G" variant; 2 is the stock MnsTechMode (3G only).
TECH_PRESETS = [
    {"id": "auto", "mask": 0x32, "label": "Automatic: 5G, 4G LTE and 3G",
     "help": "Recommended. The modem uses the best available network."},
    {"id": "5g_lte", "mask": 0x30, "label": "5G and 4G LTE (no 3G)",
     "help": "Same as automatic but never falls back to 3G."},
    {"id": "lte_3g", "mask": 0x12, "label": "4G LTE and 3G (5G off)",
     "help": "Turns 5G off. Useful when 5G coverage is poor or unstable."},
    {"id": "lte", "mask": 0x10, "label": "4G LTE only",
     "help": "No 5G and no 3G fallback."},
    {"id": "5g", "mask": 0x20, "label": "5G only",
     "help": "Only 5G standalone works without LTE. Without 5G SA coverage you will have no service."},
    {"id": "3g", "mask": 0x02, "label": "3G only (UMTS)",
     "help": "Many operators have switched 3G off. You will probably have no service.", "expert": True},
]


def mask_names(mask: Optional[int]) -> List[str]:
    if mask is None:
        return []
    return [n for n, b in TECH_BITS if mask & b]


def names_mask(names: List[str]) -> int:
    m = 0
    for n, b in TECH_BITS:
        if n in names:
            m |= b
    return m


def preset_for(mask: Optional[int]) -> str:
    for p in TECH_PRESETS:
        if p["mask"] == mask:
            return p["id"]
    return "custom"


# -- service / signal enums ------------------------------------------------------------------

SERVICE_TECH = {0: "None", 1: "GPRS", 2: "1xRTT", 3: "EV-DO", 4: "UMTS", 5: "HSDPA", 6: "EDGE", 7: "EV-DO Rev A",
                8: "HSUPA", 9: "HSPA+", 10: "LTE", 11: "DC-HSPA+", 12: "EV-DO Rev B", 13: "eHRPD", 14: "LTE-A",
                15: "5G NR"}
SERVICE_GEN = {0: "", 1: "2G", 2: "2G", 3: "3G", 4: "3G", 5: "3G", 6: "2G", 7: "3G", 8: "3G", 9: "3G", 10: "4G",
               11: "3G", 12: "3G", 13: "3G", 14: "4G", 15: "5G"}
NR_MODE = {0: "none", 1: "SA", 2: "NSA"}
NR_BEARER = {-1: "invalid", 0: "not allocated", 1: "mmWave", 2: "sub-6 GHz"}
NR_ICON = {0: "none", 1: "5G", 2: "5G UW"}
ICON_MODES = {0: "Configuration A", 1: "Configuration B", 2: "Configuration C", 3: "Configuration D",
              4: "Chipset default"}
NR5G_DISABLE = {0: "sa_nsa", 1: "sa", 2: "nsa"}          # get/set_nr5g_disable_mode
NR5G_DISABLE_REV = {v: k for k, v in NR5G_DISABLE.items()}
NR5G_MODE_LABEL = {"sa_nsa": "5G SA and NSA (automatic)", "sa": "5G SA only (standalone)",
                   "nsa": "5G NSA only (non-standalone, LTE anchor)"}
AUTOCONNECT = {0: "manual", 1: "clients", 2: "always", 3: "not_roaming"}   # ccm2 get/set_wwan_ac
AUTOCONNECT_REV = {v: k for k, v in AUTOCONNECT.items()}
AUTOCONNECT_LABEL = {"always": "Whenever service is available", "not_roaming": "Only when not roaming",
                     "clients": "Only while devices are connected", "manual": "Never (connect manually)"}
MNS_SCAN = {0: "idle", 1: "scanning", 2: "complete"}
MNS_STAT = {0: "unknown", 1: "available", 2: "current", 3: "forbidden"}
MNS_ACT = {0: "GSM", 2: "UMTS", 7: "LTE"}
CA_STATE = {0: "deconfigured", 1: "configured (inactive)", 2: "active"}
CA_BW = {0: 1.4, 1: 3, 2: 5, 3: 10, 4: 15, 5: 20}

# EMM cause values (3GPP TS 24.301 annex A) for get_reject_cause_code
REJECT_CAUSES = {
    0: "None", 2: "IMSI unknown in HSS", 3: "Illegal UE", 5: "IMEI not accepted", 6: "Illegal ME",
    7: "EPS services not allowed", 8: "EPS and non-EPS services not allowed",
    9: "UE identity cannot be derived by the network", 10: "Implicitly detached", 11: "PLMN not allowed",
    12: "Tracking area not allowed", 13: "Roaming not allowed in this tracking area",
    14: "EPS services not allowed in this PLMN", 15: "No suitable cells in tracking area",
    16: "MSC temporarily not reachable", 17: "Network failure", 18: "CS domain not available", 19: "ESM failure",
    20: "MAC failure", 21: "Synch failure", 22: "Congestion", 23: "UE security capabilities mismatch",
    24: "Security mode rejected, unspecified", 25: "Not authorized for this CSG",
    26: "Non-EPS authentication unacceptable", 31: "Redirection to 5GCN required",
    35: "Requested service option not authorized in this PLMN", 39: "CS service temporarily not available",
    40: "No EPS bearer context activated", 42: "Severe network failure", 95: "Semantically incorrect message",
    96: "Invalid mandatory information", 97: "Message type non-existent or not implemented",
    98: "Message type not compatible with the protocol state", 99: "Information element non-existent",
    100: "Conditional IE error", 101: "Message not compatible with the protocol state",
    111: "Protocol error, unspecified",
}

# -- frequency tables ------------------------------------------------------------------------

# LTE downlink: band -> (F_DL_low MHz, N_Offs-DL, N_DL first, N_DL last)  (3GPP TS 36.101 table 5.7.3-1)
LTE_DL = [
    (1, 2110.0, 0, 0, 599), (2, 1930.0, 600, 600, 1199), (3, 1805.0, 1200, 1200, 1949), (4, 2110.0, 1950, 1950, 2399),
    (5, 869.0, 2400, 2400, 2649), (7, 2620.0, 2750, 2750, 3449), (8, 925.0, 3450, 3450, 3799),
    (12, 729.0, 5010, 5010, 5179), (13, 746.0, 5180, 5180, 5279), (14, 758.0, 5280, 5280, 5379),
    (17, 734.0, 5730, 5730, 5849), (18, 860.0, 5850, 5850, 5999), (19, 875.0, 6000, 6000, 6149),
    (20, 791.0, 6150, 6150, 6449), (21, 1495.9, 6450, 6450, 6599), (25, 1930.0, 8040, 8040, 8689),
    (26, 859.0, 8690, 8690, 9039), (28, 758.0, 9210, 9210, 9659), (29, 717.0, 9660, 9660, 9769),
    (30, 2350.0, 9770, 9770, 9869), (32, 1452.0, 9920, 9920, 10359), (34, 2010.0, 36200, 36200, 36349),
    (38, 2570.0, 37750, 37750, 38249), (39, 1880.0, 38250, 38250, 38649), (40, 2300.0, 38650, 38650, 39649),
    (41, 2496.0, 39650, 39650, 41589), (42, 3400.0, 41590, 41590, 43589), (43, 3600.0, 43590, 43590, 45589),
    (46, 5150.0, 46790, 46790, 54539), (48, 3550.0, 55240, 55240, 56739), (66, 2110.0, 66436, 66436, 67335),
    (71, 617.0, 68586, 68586, 68935),
]

LTE_BAND_MHZ = {1: "2100", 2: "1900 PCS", 3: "1800", 4: "1700/2100 AWS", 5: "850", 7: "2600", 8: "900",
                12: "700 a", 13: "700 c", 14: "700 PS", 17: "700 b", 18: "800 Japan", 19: "800 Japan",
                20: "800 EU", 25: "1900+", 26: "850+", 28: "700 APT", 29: "700 SDL", 30: "2300 WCS",
                38: "2600 TDD", 39: "1900 TDD", 40: "2300 TDD", 41: "2500 TDD", 42: "3500 TDD", 43: "3700 TDD",
                46: "5200 LAA", 48: "3600 CBRS", 66: "1700/2100 AWS-3", 71: "600"}
NR_BAND_MHZ = {1: "2100", 2: "1900 PCS", 3: "1800", 5: "850", 7: "2600", 8: "900", 12: "700 a", 14: "700 PS",
               20: "800", 25: "1900+", 26: "850+", 28: "700 APT", 30: "2300", 38: "2600 TDD", 40: "2300 TDD",
               41: "2500 TDD", 48: "3600 CBRS", 66: "1700/2100 AWS", 70: "AWS-4", 71: "600", 75: "1500 SDL",
               77: "3700 C-band", 78: "3500", 79: "4700", 257: "28 GHz", 258: "26 GHz", 260: "39 GHz",
               261: "28 GHz"}


def lte_earfcn_info(earfcn: Any) -> Dict[str, Any]:
    """{band, dl_mhz} for an LTE downlink EARFCN (empty dict when unknown / 0)."""
    n = to_int(earfcn)
    if not n or n < 0:
        return {}
    for band, fdl, off, lo, hi in LTE_DL:
        if lo <= n <= hi:
            return {"band": band, "dl_mhz": round(fdl + 0.1 * (n - off), 1)}
    return {}


def nr_arfcn_mhz(arfcn: Any) -> Optional[float]:
    """NR-ARFCN -> frequency in MHz (3GPP TS 38.104 5.4.2.1)."""
    n = to_int(arfcn)
    if not n or n < 0:
        return None
    if n < 600000:
        f = 0.005 * n
    elif n < 2016667:
        f = 3000 + 0.015 * (n - 600000)
    elif n <= 3279165:
        f = 24250.08 + 0.06 * (n - 2016667)
    else:
        return None
    return round(f, 2)


def cell_parts(eci: Any) -> Dict[str, Any]:
    """28-bit E-UTRAN cell identity -> eNB id and local cell (sector) id."""
    n = to_int(eci)
    if not n or n < 0:
        return {}
    n &= 0xFFFFFFF
    return {"eci": n, "enb": n >> 8, "sector": n & 0xFF}


def parse_hex_or_dec(v: Any) -> Optional[int]:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        if re.match(r"^[0-9]+$", s):
            return int(s)
        return int(s, 16)
    except ValueError:
        return None


# -- band lists ------------------------------------------------------------------------------


def band_list(res) -> Optional[List[int]]:
    """`count: [n]` + `band[i]: [b]` lists (get_supported_*, get_enabled_*, priority lists)."""
    if res is None:
        return None
    out = []
    for v in res.indexed("band"):
        b = to_int(v)
        if b is not None and b > 0 and b not in out:
            out.append(b)
    return out


def decode_lte_bandpref(data: bytes, base_band: int = 1) -> List[int]:
    """EFS lte_bandpref style bitmask: bit k (LSB first, little endian bytes) = band base_band+k."""
    out = []
    for i, byte in enumerate(bytearray(data)):
        for bit in range(8):
            if byte & (1 << bit):
                out.append(base_band + i * 8 + bit)
    return out


# -- getters with structured / irregular output ----------------------------------------------

_NB_INTRA = re.compile(r"^cells\[(\d+)\] - (pci|rsrp|rsrq|rssi|earfcn|srxlev):\[(-?[0-9.]+)\]", re.M)
_NB_FREQ = re.compile(r"^freq\[(\d+)\], cells\[(\d+)\] - (\w+):\[(-?[0-9.]+)\]", re.M)


def parse_neighbors(text: str) -> Dict[str, List[Dict[str, Any]]]:
    """get_neighbor_list: four sections; LTE values are 1/10 dB(m); empty (all-zero) slots dropped."""
    out = {"intra": [], "inter": [], "wcdma": [], "lte_from_wcdma": []}
    if not text:
        return out
    sections = re.split(r"^(LTE Intra Frequency neigbhor stats:|LTE Inter Frequency neigbhor stats:|"
                        r"LTE Info - Neighboring WCDMA:|WCDMA Info - LTE Neighbor Cell Info Set:)\s*$",
                        text, flags=re.M)
    names = {"LTE Intra Frequency neigbhor stats:": "intra", "LTE Inter Frequency neigbhor stats:": "inter",
             "LTE Info - Neighboring WCDMA:": "wcdma", "WCDMA Info - LTE Neighbor Cell Info Set:": "lte_from_wcdma"}
    i = 1
    while i < len(sections) - 1:
        key = names.get(sections[i].strip())
        body = sections[i + 1]
        i += 2
        if not key:
            continue
        cells: Dict[tuple, Dict[str, Any]] = {}
        if key in ("intra", "lte_from_wcdma"):
            for m in _NB_INTRA.finditer(body):
                cells.setdefault((0, int(m.group(1))), {})[m.group(2)] = m.group(3)
        else:
            for m in _NB_FREQ.finditer(body):
                cells.setdefault((int(m.group(1)), int(m.group(2))), {})[m.group(3)] = m.group(4)
        for (freq, idx), raw in sorted(cells.items()):
            if all(to_float(v) in (0, None) for v in raw.values()):
                continue
            if key == "wcdma":
                row = {"freq": freq, "psc": to_int(raw.get("psc")), "rscp": nz(raw.get("cpich_rscp")),
                       "ecno": nz(raw.get("cpich_ecno")), "srxlev": to_int(raw.get("srxlev"))}
            elif key == "lte_from_wcdma":
                row = {"earfcn": to_int(raw.get("earfcn")), "pci": to_int(raw.get("pci")),
                       "rsrp": nz(raw.get("rsrp")), "rsrq": nz(raw.get("rsrq")), "srxlev": to_int(raw.get("srxlev"))}
            else:
                row = {"pci": to_int(raw.get("pci")), "rsrp": tenths(raw.get("rsrp")), "rsrq": tenths(raw.get("rsrq")),
                       "rssi": tenths(raw.get("rssi"))}
                if key == "inter":
                    row["freq"] = freq
            out[key].append(row)
    return out


_CA_LINE = re.compile(r"^ca_bands\[(\d+)\] - (pci|band|earfcn|status|bandwidth|rsrp|rsrq|rssi|snr):\[(-?\d+)\]", re.M)


def _ca_level(v: Any, limit: float) -> Optional[float]:
    """CA cell measurements: plain dB(m) or 1/10 units depending on the source; normalise."""
    i = to_int(v)
    if not i:
        return None
    return round(i / 10.0, 1) if abs(i) > limit else i


def parse_ca_bands(text: str) -> List[Dict[str, Any]]:
    """get_ca_band_list: per component carrier pci/band/earfcn/status/bandwidth(enum)/rsrp/rsrq/rssi/snr."""
    cells: Dict[int, Dict[str, str]] = {}
    for m in _CA_LINE.finditer(text or ""):
        cells.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
    out = []
    for idx in sorted(cells):
        raw = cells[idx]
        band = to_int(raw.get("band"))
        earfcn = to_int(raw.get("earfcn"))
        if not band and not earfcn and not to_int(raw.get("pci")):
            continue
        bw = to_int(raw.get("bandwidth"))
        info = lte_earfcn_info(earfcn)
        out.append({"index": idx, "role": "primary" if idx == 0 else "secondary", "pci": to_int(raw.get("pci")),
                    "band": band, "earfcn": earfcn, "dl_mhz": info.get("dl_mhz"),
                    "status": CA_STATE.get(to_int(raw.get("status"), -1), raw.get("status")),
                    "status_code": to_int(raw.get("status")), "bandwidth_mhz": CA_BW.get(bw),
                    "rsrp": _ca_level(raw.get("rsrp"), 200), "rsrq": _ca_level(raw.get("rsrq"), 40),
                    "rssi": _ca_level(raw.get("rssi"), 150), "snr": _ca_level(raw.get("snr"), 60)})
    return out


_ARFCN = re.compile(r"^lte_scell_earfcn\[(\d+)\] - (dl|ul)_earfcn:\[(-?\d+)\]", re.M)


def parse_arfcn_list(text: str) -> List[Dict[str, Any]]:
    rows: Dict[int, Dict[str, int]] = {}
    for m in _ARFCN.finditer(text or ""):
        rows.setdefault(int(m.group(1)), {})[m.group(2)] = int(m.group(3))
    out = []
    for i in sorted(rows):
        dl, ul = rows[i].get("dl", 0), rows[i].get("ul", 0)
        if not dl and not ul:
            continue
        info = lte_earfcn_info(dl)
        out.append({"index": i, "dl_earfcn": dl or None, "ul_earfcn": ul or None, "band": info.get("band"),
                    "dl_mhz": info.get("dl_mhz")})
    return out


_RATES = re.compile(r"cur tx rate: \[([0-9A-Fa-f]+)\] Kb/s, cur rx rate \[([0-9A-Fa-f]+)\] Kb/s, "
                    r"max tx rate \[(\d+)\] Kb/s, max rx rate \[(\d+)\] Kb/s")


def parse_data_rates(text: str) -> Optional[Dict[str, int]]:
    """get_data_rates; NB: the vendor prints the *current* rates with %X (hexadecimal)."""
    m = _RATES.search(text or "")
    if not m:
        return None
    return {"cur_tx_kbps": int(m.group(1), 16), "cur_rx_kbps": int(m.group(2), 16),
            "max_tx_kbps": int(m.group(3)), "max_rx_kbps": int(m.group(4))}


_TXRX = re.compile(r"tx_pwr:\[(-?\d+)\] - avg_rx_pwr:\[(-?\d+)\]")


def parse_tx_rx(text: str) -> Optional[Dict[str, Optional[float]]]:
    m = _TXRX.search(text or "")
    if not m:
        return None
    return {"tx_power": nz(m.group(1)), "avg_rx_power": nz(m.group(2))}


_LIFETIME = re.compile(r"Rx = (\d+) KB, Tx = (\d+) KB")


def parse_lifetime(text: str) -> Optional[Dict[str, int]]:
    m = _LIFETIME.search(text or "")
    if not m:
        return None
    return {"rx_kb": int(m.group(1)), "tx_kb": int(m.group(2))}


_MNS_LIST = re.compile(r"Mns list output:\[(.*)\]", re.S)
_COPS = re.compile(r'\((\d+),"(\d{5,6})","([^"]*)",(\d+)(?:,(\d+))?\)')


def parse_mns_list(text: str) -> List[Dict[str, Any]]:
    """mns_get_list: libmal_qct builds `+COPS: (<stat>,"<mccmnc>","<name>",<act>,<roaming>)` lines
    (format strings at libmal_qct 0x2b560/0x2b578/0x2b590): stat 1 available / 2 current,
    act 0 GSM / 2 UMTS / 7 LTE, roaming 0/1."""
    m = _MNS_LIST.search(text or "")
    body = m.group(1) if m else (text or "")
    out = []
    seen = set()
    for c in _COPS.finditer(body):
        stat, mccmnc, name, act = int(c.group(1)), c.group(2), c.group(3).strip(), int(c.group(4))
        key = (mccmnc, act)
        if key in seen:
            continue
        seen.add(key)
        out.append({"status": MNS_STAT.get(stat, "unknown"), "mccmnc": mccmnc,
                    "mcc": mccmnc[:3], "mnc": mccmnc[3:], "name": name or mccmnc,
                    "act": act, "tech": MNS_ACT.get(act, "act %d" % act),
                    "roaming": c.group(5) == "1"})
    return out


_MNS_VALIDATE = re.compile(r"MNS:\[(-?\d+)\]\(([^)]*)\)")


def parse_mns_validate(text: str) -> Optional[Dict[str, Any]]:
    m = _MNS_VALIDATE.search(text or "")
    if not m:
        return None
    return {"code": int(m.group(1)), "message": m.group(2).strip().rstrip("."),
            "reverted": "revert" in m.group(2).lower()}


def parse_air_disable(res) -> Dict[str, Any]:
    """ccm2 get_airplane_mode_disable_status: mask + named lines."""
    if res is None:
        return {"mask": None, "wifi": None, "cellular": None}
    mask = to_int(res.get("Radio Disable status"))
    text = res.text or ""
    wifi = "WiFi Disabled" in text if mask is None else bool(mask & 0x1)
    cell = "Cellular Disabled" in text if mask is None else bool(mask & 0x2)
    return {"mask": mask, "wifi": wifi, "cellular": cell}


def parse_signal(res) -> Dict[str, Any]:
    """modem2_cli get_signal (also the first block of get_state)."""
    if res is None:
        return {}
    g = res.get
    tech = to_int(g("tech"), 0)
    cell = g("cell id") or ""
    return {
        "tech_code": tech, "tech": SERVICE_TECH.get(tech, "Unknown"), "generation": SERVICE_GEN.get(tech, ""),
        "roam": to_int(g("roam"), 0), "rssi": nz(g("rssi")), "dbm": nz(g("dbm")), "bars": to_int(g("bars"), 0),
        "ecio": nz(g("ecio")), "sinr": nz(g("sinr")), "rsrq": nz(g("rsrq")), "snr": nz(g("snr")),
        "rsrp": nz(g("rsrp")), "tx_power": nz(g("tx power")), "radio_temp": nz(g("radio temp")),
        "operator": g("oper name") or "", "mccmnc": "" if (g("oper id") or "").strip("0") == "" else g("oper id"),
        "cell_id": cell, "lac": g("lac") or "", "tac": g("tac") or "", "femto": bool(to_int(g("femto"), 0)),
        "pci": to_int(g("pci"), 0) or None, "band": to_int(g("band"), 0) or None,
        "bandwidth_mhz": to_int(g("bandwidth"), 0) or None, "channel": to_int(g("channel"), 0) or None,
    }


def parse_5g(res) -> Dict[str, Any]:
    if res is None:
        return {}
    g = res.get
    gi = res.get_int
    mode = gi("mode", 0)
    bearer = gi("bearer", 0)
    icon = gi("icon", 0)
    # mode text is "(NONE)" / "(STAND ALONE)" / "(NON-STAND ALONE)"; EN-DC always means NSA
    note = (res.note("mode") or "").upper()
    if gi("endc", 0) or "NON-STAND" in note or "NSA" in note:
        mode_s = "NSA"
    elif "STAND ALONE" in note or note.strip("() ") == "SA":
        mode_s = "SA"
    else:
        mode_s = NR_MODE.get(mode, "none")
    if not gi("service_available", 0) and mode_s != "none" and not gi("band", 0):
        mode_s = "none"
    return {
        "service_available": bool(gi("service_available", 0)), "data_connected": bool(gi("data_connected", 0)),
        "bars": gi("bars", 0), "snr": nz(g("snr")), "rsrp": nz(g("rsrp")), "rsrq": nz(g("rsrq")),
        "endc": bool(gi("endc", 0)), "dcnr": bool(gi("dcnr", 0)), "pci": gi("pci", 0) or None,
        "band": gi("band", 0) or None, "bandwidth_mhz": gi("bandwidth", 0) or None,
        "arfcn": gi("channel", 0) or None, "arfcn_ul": gi("channel_ul", 0) or None,
        "dl_mhz": nr_arfcn_mhz(gi("channel", 0)), "bearer_code": bearer, "bearer": NR_BEARER.get(bearer, str(bearer)),
        "mode_code": mode, "mode": mode_s, "cdrx": bool(gi("cdrx_cfg_present", 0)),
        "tx_power": nz(g("tx_power")), "ssb_index": gi("ssb_index", 0), "scs_khz": gi("scs", 0) or None,
        "rlf_count": gi("rlf_count", 0), "num_cc": gi("num_cc", 0), "sb_status": gi("sb_status", 0),
        "icon_code": icon, "icon": NR_ICON.get(icon, str(icon)),
    }


_RAW_LINE = re.compile(r"^(.*)\[([^\[\]]*)\](.{0,30})$")      # label = everything before the last [value]
_INDEX = re.compile(r"\w\[\d+\]")                                 # cells[0] / band[3] style indexes
_STATUS_LINE = re.compile(r"[A-Za-z0-9_]+ returned -?\d+ \([^)]*\)")


def raw_pairs(res) -> List[List[str]]:
    """Every output line of a CLI result as [label, value], for the field-test raw table
    (prompt echoes and the status line removed; lines without brackets kept as text)."""
    if res is None:
        return []
    text = res.text or ""
    m = None
    for m in _STATUS_LINE.finditer(text):
        pass
    if m is not None:
        text = text[m.end():]
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        mm = _RAW_LINE.match(line)
        if mm and mm.group(1).strip() and "[" not in _INDEX.sub("", mm.group(1)):
            out.append([mm.group(1).strip(" -:"), (mm.group(2) + " " + mm.group(3).strip()).strip()])
        else:
            out.append([line, ""])
    return out


def band_combo(lte_bands: List[int], nr_band: Optional[int]) -> str:
    parts = ["B%d" % b for b in lte_bands if b]
    if nr_band:
        parts.append("n%d" % nr_band)
    return " + ".join(parts)
