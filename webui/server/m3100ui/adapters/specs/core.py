"""Setters verified in the device sandbox on 2026-10-31 (unit 76fe1623) while building the core."""
from ..cli_specs import Spec

SPECS = {
    ("modem2_cli", "set_enabled_lte_bands"): Spec(
        ["Enter number of bands to enable(max 512): ", "Enter band number: "], tier=2, source="sandbox",
        repeat=(0, 1), min_items=1, evidence="sandbox transcript 2026-10-31 (docs/recon/modem2-cli.md)",
        notes="answers: [count, band1, ..., bandN]; never send an empty list"),
    ("modem2_cli", "set_enabled_nr5g_sa_bands"): Spec(
        ["Enter number of bands to enable(max 512): ", "Enter band number: "], tier=2, source="roundtrip",
        repeat=(0, 1), min_items=1, evidence="../m3100-openwrt/analysis/band-settings-backup.json"),
    ("modem2_cli", "set_enabled_nr5g_nsa_bands"): Spec(
        ["Enter number of bands to enable(max 512): ", "Enter band number: "], tier=2, source="roundtrip",
        repeat=(0, 1), min_items=1, evidence="../m3100-openwrt/analysis/band-settings-backup.json"),
    ("modem2_cli", "set_nr5g_disable_mode"): Spec(
        ["Enter mode (0-Disable None, 1-Disable NSA, 2-Disable SA):"], tier=2, source="sandbox",
        evidence="sandbox transcript 2026-10-31", notes="0 = SA+NSA, 1 = SA only (NSA disabled), 2 = NSA only (SA disabled)"),
    ("modem2_cli", "enabled_tech_set"): Spec(
        ["Enter list of new tech modes (GSM,UMTS,CDMA,EVDO,LTE,NR5G):"], tier=2, source="sandbox", no_spaces=[0],
        evidence="sandbox transcript 2026-10-31", notes="comma separated names, e.g. LTE,NR5G"),
    ("wifi_cli", "set_ap_settings"): Spec(
        ["AP status (0/1): ", "Max number of clients: "], tier=1, source="sandbox", evidence="sandbox transcript 2026-10-31"),
    ("mifi_qcmap_cli", "set_lan_dhcp_settings"): Spec(
        ["Enter LAN IP address: ", "Enter LAN subnet mask: ", "Enter LAN mask format (0=Disable, 1=Enable): ",
         "Enter DHCP status (0=Disable, 1=Enable): ", "Enter DHCP start IP address: ", "Enter DHCP end IP address: ",
         "Enter DHCP lease time (in seconds): "], tier=1, source="sandbox", no_spaces=[0, 1, 4, 5],
        evidence="sandbox transcript 2026-10-31"),
}
