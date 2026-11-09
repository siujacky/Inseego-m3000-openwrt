"""Mock: a connected 5G NSA unit with a SIM, moving signal and growing counters.

The real fixtures were captured without a SIM; this makes the dashboard meaningful in --mock.
"""
import math
import random
import time

from ..adapters.mock import cli_handler, ok_text, rest_handler

T0 = time.time()


def _wave(base, amp, period, noise):
    t = time.time() - T0
    return round(base + amp * math.sin(2 * math.pi * t / period) + random.uniform(-noise, noise))


@rest_handler("GET", "CellularServiceStatus")
def _svc(rest, body):
    rsrp = _wave(-92, 9, 90, 2)
    return {"Current5GBandwidth": 100, "Current5GMode": 1, "CurrentBand": "66", "CurrentBandwidth": 20,
            "CurrentBearer": 1, "CurrentCarrierNetworkName": "Mock Mobile", "CurrentCellID": "0A1B2C3D",
            "CurrentConnectionToFemtoCell": False, "CurrentECGI": 311480123456789, "CurrentRoamingIconEffect": "Static",
            "CurrentRoamingState": "NotRoaming", "CurrentSID": 0, "CurrentServiceStatus": "InService",
            "CurrentSignalStrengthInBars": max(1, min(5, int((rsrp + 120) / 8))), "CurrentSignalStrengthInRSRP": rsrp,
            "CurrentSignalStrengthInRSSI": rsrp + 25, "CurrentSignalToNoiseLevelInEcIo": 0,
            "CurrentSignalToNoiseLevelInSINR": _wave(14, 6, 70, 2), "CurrentSignalToNoiseLevelInSNR": 0,
            "CurrentStrengthInRSRQ": _wave(-11, 2, 60, 1), "CurrentWirelessTechnology": "LTE", "PCI": 262}


@rest_handler("GET", "CellularDataConnectionStatus")
def _data(rest, body):
    t = int(time.time() - T0) + 5400
    return {"CurrentCallBytesReceived": 1_250_000_000 + t * 182_000, "CurrentCallBytesTransmitted": 96_000_000 + t * 15_500,
            "CurrentCallDuration": t, "CurrentExternalIPv4Address": "100.64.12.34",
            "CurrentExternalIPv6Address": "2001:db8:1234:5678::1", "CurrentGatewayIPv4Address": "100.64.12.1",
            "CurrentNetworkActivityForIcon": "Both", "CurrentPrimaryDNSIPv4Address": "198.51.100.53",
            "CurrentSecondaryDNSIPv4Address": "198.51.100.54", "CurrentStatus": "Connected"}


@rest_handler("GET", "SimStatus")
def _sim(rest, body):
    return {"CurrentSimStatus": rest.data.get("_mock_sim_status", "Ready")}


@cli_handler("modem2_cli", "get_5g_status")
def _nr(store, answers, args):
    lines = ["service_available:[1]", "data_connected:[1]", "bars:[4]",
             "snr:[%d]" % _wave(18, 5, 80, 2), "rsrp:[%d]" % _wave(-88, 8, 100, 2), "rsrq:[%d]" % _wave(-11, 2, 50, 1),
             "endc:[1]", "dcnr:[1]", "pci:[505]", "band:[77]", "bandwidth:[100]", "channel:[650016]", "bearer:[2]", "bearer:[MODEM2_5G_BEARER_ALLOCATED_SUB6]",
             "mode:[2] (NON-STAND ALONE)", "cdrx_cfg_present:[1]", "tx_power:[12]", "ssb_index:[3]", "scs:[30]", "rlf_count:[0]",
             "num_cc:[1]", "channel_ul:[650016]", "sb_status:[0]", "icon:[2]"]
    return ok_text("cmd_get_5g_status", "MDM_MAIN", lines)
