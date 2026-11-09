"""Cellular > Signal & cell: full LTE + NR radio detail, carrier aggregation, neighbours, counters.

GET /api/cellular/signal       polled by the page every 5 s while it is open.
    fast group (2 s cache):  get_signal, get_5g_status, get_ca_band_list, get_data_rates, get_tx_rx_info,
                             get_service_info, REST CellularServiceStatus
    slow group (15 s cache): get_neighbor_list, get_arfcn_list, get_oper_info, get_ecgi, get_serving_cell_id,
                             get_earfcn, get_5g_band_pci, active_band_get, active_channel_get, get_bandwidth,
                             get_aggregated_bandwidth, get_rach_fail, get_reject_cause_code, get_network_time,
                             lifetime_counters_get, get_bytes_counters (v4/v6), get_packet_statistics,
                             validate_home, check_lte_ca_status (30 s: the vendor command sleeps 1 s)
GET /api/cellular/signal/diag  modem diagnostics buffer (stock "Field test" page), subscriber ids masked.
"""
import re
import time
from typing import Any, Dict, List

from .cellular import M2, get_any, sim_present
from . import cellular_parse as P

FAST = ["get_signal", "get_5g_status", "get_ca_band_list", "get_data_rates", "get_tx_rx_info", "get_service_info"]
SLOW = ["get_neighbor_list", "get_arfcn_list", "get_oper_info", "get_ecgi", "get_serving_cell_id", "get_earfcn",
        "get_5g_band_pci", "active_band_get", "active_channel_get", "get_bandwidth", "get_aggregated_bandwidth",
        "get_rach_fail", "get_reject_cause_code", "get_network_time", "lifetime_counters_get", "validate_home"]
# getters that need input (fed on stdin; they are read-only)
WITH_INPUT = {"get_bytes_counters_v4": ("get_bytes_counters", ["0"]),
              "get_bytes_counters_v6": ("get_bytes_counters", ["2"]),
              "get_packet_statistics_v4": ("get_packet_statistics", ["1", "0"]),
              "get_packet_statistics_v6": ("get_packet_statistics", ["1", "2"])}

_MASK_LINES = re.compile(r"^([ \t]*(?:Mobile Directory Number \(MDN\)|IMSI_S|MIN|IMSI|MDN)[ \t]*:[ \t]*)(\S[^\n]*)$",
                         re.M | re.I)


def _mask(v: str) -> str:
    v = v.strip()
    if not v or v.upper() == "N/A":
        return v
    return "*" * max(0, len(v) - 4) + v[-4:]


def mask_diag(text: str) -> str:
    return _MASK_LINES.sub(lambda m: m.group(1) + _mask(m.group(2)), text or "")


def register(app):
    r = app.router

    def run_group(cmds: List[str]) -> Dict[str, Any]:
        out = {}
        for cmd in cmds:
            out[cmd] = get_any(app, M2, cmd)
        return out

    def fast():
        d = run_group(FAST)
        d["_rest"] = app.rest_try("CellularServiceStatus", {}) or {}
        return d

    def slow():
        d = run_group(SLOW)
        for key, (cmd, answers) in WITH_INPUT.items():
            d[key] = get_any(app, M2, cmd, answers)
        return d

    def ca_check():
        return get_any(app, M2, "check_lte_ca_status", timeout=10)

    def ok(res):
        return res if res is not None and res.ok else None

    def build() -> Dict[str, Any]:
        f = app.cache.get("cellular:signal:fast", 2.0, fast)
        s = app.cache.get("cellular:signal:slow", 15.0, slow)
        cac = app.cache.get("cellular:signal:ca", 30.0, ca_check)
        rest = f.get("_rest") or {}
        sig = P.parse_signal(ok(f.get("get_signal")))
        nr = P.parse_5g(ok(f.get("get_5g_status")))
        ca_cells = P.parse_ca_bands(f["get_ca_band_list"].text) if ok(f.get("get_ca_band_list")) else []
        scells = P.parse_arfcn_list(s["get_arfcn_list"].text) if ok(s.get("get_arfcn_list")) else []
        nb = P.parse_neighbors(s["get_neighbor_list"].text) if ok(s.get("get_neighbor_list")) else P.parse_neighbors("")
        rates = P.parse_data_rates(f["get_data_rates"].text) if ok(f.get("get_data_rates")) else None
        txrx = P.parse_tx_rx(f["get_tx_rx_info"].text) if ok(f.get("get_tx_rx_info")) else None
        svc = ok(f.get("get_service_info"))
        oper = ok(s.get("get_oper_info"))
        ecgi = ok(s.get("get_ecgi"))
        nrbp = ok(s.get("get_5g_band_pci"))
        earfcn = P.to_int(s["get_earfcn"].get("earfcn")) if ok(s.get("get_earfcn")) else None
        active_band = P.to_int(s["active_band_get"].get("Active band")) if ok(s.get("active_band_get")) else None
        active_chan = (P.to_int(s["active_channel_get"].get("Frequency(Active channel)"))
                       if ok(s.get("active_channel_get")) else None)
        bw = P.to_int(s["get_bandwidth"].get("bandwidth")) if ok(s.get("get_bandwidth")) else None
        agg = P.to_int(s["get_aggregated_bandwidth"].get("bandwidth")) if ok(s.get("get_aggregated_bandwidth")) else None
        rach = P.to_int(s["get_rach_fail"].get("LTE RACH Failures")) if ok(s.get("get_rach_fail")) else None
        rej = P.to_int(s["get_reject_cause_code"].get("Reject cause code")) if ok(s.get("get_reject_cause_code")) else None
        home = P.to_int(s["validate_home"].get("Home network")) if ok(s.get("validate_home")) else None
        nt = ok(s.get("get_network_time"))
        life = P.parse_lifetime(s["lifetime_counters_get"].text) if ok(s.get("lifetime_counters_get")) else None
        scid = P.to_int(s["get_serving_cell_id"].get("serving_cell_id")) if ok(s.get("get_serving_cell_id")) else None

        # LTE cell identity
        ecgi_dec = P.to_int(ecgi.get("ecgi")) if ecgi else None
        eci = P.parse_hex_or_dec(sig.get("cell_id") or rest.get("CurrentCellID"))
        if not eci and ecgi_dec:
            eci = ecgi_dec & 0xFFFFFFF           # lower 28 bits of the ECGI = E-UTRAN cell identity
        parts = P.cell_parts(eci)
        lte_earfcn = sig.get("channel") or earfcn or active_chan
        lte_info = P.lte_earfcn_info(lte_earfcn)
        lte_band = sig.get("band") or active_band or lte_info.get("band") or P.to_int(rest.get("CurrentBand")) or None
        lte = {
            "rsrp": sig.get("rsrp") or P.nz(rest.get("CurrentSignalStrengthInRSRP")),
            "rsrq": sig.get("rsrq") or P.nz(rest.get("CurrentStrengthInRSRQ")),
            "sinr": sig.get("sinr") or P.nz(rest.get("CurrentSignalToNoiseLevelInSINR")),
            "rssi": sig.get("rssi") or P.nz(rest.get("CurrentSignalStrengthInRSSI")),
            "snr": sig.get("snr"), "ecio": sig.get("ecio"), "dbm": sig.get("dbm"),
            "tx_power": sig.get("tx_power") or (txrx or {}).get("tx_power"),
            "avg_rx_power": (txrx or {}).get("avg_rx_power"), "radio_temp": sig.get("radio_temp"),
            "band": lte_band, "band_name": P.LTE_BAND_MHZ.get(lte_band or 0, ""),
            "bandwidth_mhz": sig.get("bandwidth_mhz") or (bw if bw and bw > 0 else None) or P.nz(rest.get("CurrentBandwidth")),
            "earfcn": lte_earfcn, "dl_mhz": lte_info.get("dl_mhz"),
            "pci": sig.get("pci") or P.nz(rest.get("PCI")), "serving_cell_id": scid,
            "cell_id": sig.get("cell_id") or rest.get("CurrentCellID") or "", "eci": parts.get("eci"),
            "enb": parts.get("enb"), "sector": parts.get("sector"), "tac": sig.get("tac"), "lac": sig.get("lac"),
            "ecgi": ecgi_dec or None, "ecgi_hex": (ecgi.get("ecgi_hex") if ecgi and ecgi_dec else None),
            "active_band": active_band or None, "active_channel": active_chan or None,
        }
        if nrbp:
            nr["active_band"] = P.to_int(nrbp.get("Active band")) or None
            nr["active_pci"] = P.to_int(nrbp.get("pci")) or None
        nr["band_name"] = P.NR_BAND_MHZ.get(nr.get("band") or 0, "")

        # carrier aggregation
        ca_status = None
        if cac is not None:
            if "WAN_TECH_LTE_CA" in (cac.text or ""):
                ca_status = "LTE-A"
            elif "WAN_TECH_LTE" in (cac.text or ""):
                ca_status = "LTE"
        active_cells = [c for c in ca_cells if c.get("band") and (c["index"] == 0 or c.get("status_code") in (2, None))]
        lte_bands = []
        for c in active_cells:
            if c["band"] not in lte_bands:
                lte_bands.append(c["band"])
        if not lte_bands and lte_band:
            lte_bands = [lte_band]
        ca_active = len(active_cells) > 1 or sig.get("tech_code") == 14 or ca_status == "LTE-A"

        # summary
        sim = sim_present(app)
        nr_on = bool(nr.get("service_available"))
        if nr_on and nr.get("mode") == "SA":
            tech = "5G SA"
        elif nr_on:
            tech = "5G NSA"
        elif sig.get("tech_code"):
            tech = "LTE-A" if ca_active and sig.get("generation") == "4G" else sig.get("tech")
        else:
            raw = str(rest.get("CurrentWirelessTechnology", "") or "")
            tech = raw if raw and raw.lower() != "unknown" else "No service"
        operator = sig.get("operator") or rest.get("CurrentCarrierNetworkName") or (oper.get("Operator") if oper else "") or ""
        mccmnc = (oper.get("Mcc Mnc") if oper and (oper.get("Mcc Mnc") or "").strip("0") else "") or sig.get("mccmnc") or ""
        roam_state = str(rest.get("CurrentRoamingState", "") or "")
        svc_code = P.to_int(svc.get("Service"), 0) if svc else 0
        state = "no_sim" if sim is False else ("in_service" if (sig.get("tech_code") or nr_on or svc_code) else "no_service")
        summary = {
            "state": state, "sim_present": sim, "tech": tech, "operator": operator, "mccmnc": mccmnc,
            "roaming": bool(sig.get("roam")) or roam_state not in ("", "NotRoaming"), "roaming_state": roam_state,
            "home_network": None if home is None else bool(home),
            "service": rest.get("CurrentServiceStatus", ""), "service_tech": P.SERVICE_TECH.get(svc_code, ""),
            "bars": rest.get("CurrentSignalStrengthInBars") or sig.get("bars") or 0,
            "band_combo": P.band_combo(lte_bands, nr.get("band") if nr_on else None),
            "lte_bands": lte_bands, "nr_band": nr.get("band") if nr_on else None,
        }

        def counters(key):
            res = ok(s.get(key))
            if res is None:
                return None
            if key.startswith("get_bytes"):
                return {"tx_bytes": P.to_int(res.get("tx_bytes")), "rx_bytes": P.to_int(res.get("rx_bytes"))}
            return {"bytes_sent": P.to_int(res.get("BytesSent")), "bytes_received": P.to_int(res.get("BytesReceived")),
                    "packets_received": P.to_int(res.get("Packet Received")), "errors_sent": P.to_int(res.get("ErrorsSent")),
                    "errors_received": P.to_int(res.get("ErrorReceived"))}

        raw = []
        for name in FAST + SLOW:
            res = f.get(name) if name in f else s.get(name)
            raw.append({"command": name, "ok": bool(res is not None and res.ok),
                        "message": (res.message if res is not None else "not available"),
                        "values": P.raw_pairs(res) if res is not None and res.ok else []})
        if cac is not None:
            lines = (cac.text or "").strip().splitlines()
            raw.append({"command": "check_lte_ca_status", "ok": cac.ok, "message": cac.message or "",
                        "values": [["status", ca_status or (lines[-1].strip() if lines else "")]]})
        rest_vals = [[k, str(v)] for k, v in sorted(rest.items())]
        if rest_vals:
            raw.append({"command": "REST CellularServiceStatus", "ok": True, "message": "", "values": rest_vals})

        return {
            "time": int(time.time()),
            "summary": summary,
            "lte": lte,
            "nr": nr,
            "ca": {"active": ca_active, "status": ca_status, "cells": ca_cells, "scells": scells,
                   "aggregated_bandwidth_mhz": agg if agg and agg > 0 else None},
            "neighbors": nb,
            "rates": rates,
            "rach_failures": rach,
            "reject_cause": None if rej is None else {"code": rej, "text": P.REJECT_CAUSES.get(rej, "Cause %d" % rej)},
            "network_time": None if nt is None else {"time": nt.get("Time"), "timestamp": P.to_int(nt.get("Timestamp")),
                                                     "timezone": P.to_int(nt.get("Time zone")), "dst": P.to_int(nt.get("DST"))},
            "counters": {"lifetime": life, "session_v4": counters("get_bytes_counters_v4"),
                         "session_v6": counters("get_bytes_counters_v6"),
                         "packets_v4": counters("get_packet_statistics_v4"),
                         "packets_v6": counters("get_packet_statistics_v6")},
            "raw": raw,
        }

    @r.route("GET", "/api/cellular/signal")
    def signal(req):
        """LTE + 5G NR radio detail, carrier aggregation, neighbour cells, counters and raw field-test values."""
        return build()

    @r.route("GET", "/api/cellular/signal/diag")
    def diag(req):
        """Modem diagnostics buffer (field test text), subscriber numbers masked."""
        res = get_any(app, M2, "get_diag_info")
        if res is None or not res.ok:
            return {"ok": False, "text": "", "message": res.message if res is not None else "not available"}
        body = res.text.split(")", 1)[1] if ")" in res.text else res.text
        body = body.strip().lstrip("[").rstrip("]").strip()
        return {"ok": True, "text": mask_diag(body), "message": ""}
