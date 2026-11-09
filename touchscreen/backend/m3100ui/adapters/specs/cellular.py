"""Cellular area setters (modem2_cli radio/roaming/MNS/CA/priority/antenna, ccm2_cli air mode / cell data /
auto-connect / connect, webui_cli automatic network selection).

All prompts were established by static analysis of the stock binaries in reference/stock-fs/opt/nvtl/bin/
(sha256 modem2_cli 5e88fc07..., ccm2_cli faeb9990..., webui_cli 33eaf0f6...), using objdump -M force-thumb
plus the string-annotation scripts (scratch: area-cellular/show_handler.py).  Handler addresses come from the
dispatch tables ({name, handler, help} in .data: modem2_cli 0x39008, ccm2_cli 0x17008, webui_cli 0x19008).
"argc==N" below is the C argc the handler compares (argv[0]=cli, argv[1]=command), so Spec.argc = N - 2.
In argv mode no prompt is printed; the stdin prompts are recorded for documentation / sandbox checks.

Specs owned elsewhere (do not redefine): set_enabled_*_bands, set_nr5g_disable_mode, enabled_tech_set (core.py).
"""
from ..cli_specs import Spec

M2 = "modem2_cli"
CCM = "ccm2_cli"
WEBUI = "webui_cli"

SPECS = {
    # ---- radio --------------------------------------------------------------------------------
    (M2, "radio_set_enabled"): Spec(
        ["Enable radio (disabled=0, enabled=1): "], tier=2, mode="argv", argc=1, sample=["1"],
        evidence="handler 0x13ce0: cmp r0,#3 -> beq 0x13d5c strtol(argv[2],0,0) -> modem2_set_radio_enabled; "
                 "stdin path prints .rodata 0x206d0 then fgets/strtol",
        notes="0 = cellular radio off (modem low power), 1 = on"),
    (M2, "5g_radio_set_enabled"): Spec(
        ["Enable radio (disabled=0, enabled=1): "], tier=2, mode="argv", argc=1, sample=["1"],
        evidence="handler 0x13c34: cmp r0,#3 -> argv strtol -> modem2_set_5g_radio_enabled; stdin prompt 0x206d0",
        notes="UI offers 0/1 only; 3 (thermal disable with restore marker) is reserved for the vendor tempread.sh"),

    # ---- roaming (fallback when REST CellularServiceSettings is unavailable) ------------------
    (M2, "roam_set_enabled"): Spec(
        ["enable roaming [1/0]: "], tier=1, mode="argv", argc=1, sample=["1"],
        evidence="handler 0xad7c: cmp r0,#3 -> argv strtol; stdin prompt 0x1d7c0 + scanf(\"%d\") 0x1c668",
        notes="modem2_roaming_set_enabled(1 = domestic, v)"),
    (M2, "roam_set_intl_enabled"): Spec(
        ["enable international roaming [1/0]: "], tier=1, mode="argv", argc=1, sample=["1"],
        evidence="handler 0xace4: cmp r0,#3 -> argv strtol; stdin prompt 0x1d798 + scanf(\"%d\")",
        notes="modem2_roaming_set_enabled(2 = international, v)"),
    (M2, "set_roam_guard_settings"): Spec(
        ["Enter domestic roam enabled [0/1]:", "Enter domestic roam forced [0/1]:",
         "Enter domestic_roam_guard_enabled [0/1]:", "Enter domestic_roam_guard_forced [0/1]:",
         "Enter international_roam_enabled [0/1]:", "Enter international_roam_forced [0/1]:",
         "Enter international_roam_guard_enabled [0/1]:", "Enter international_roam_guard_forced [0/1]:"],
        tier=1, mode="argv", argc=8, sample=["0", "0", "0", "0", "0", "0", "0", "0"],
        evidence="handler 0x14660: cmp r0,#10 -> beq.w 0x14808 (8 x strtol argv[2..9]); stdin prompts "
                 "0x20a5c 0x20a80 0x20aa4 0x20ad0 0x20af8 0x20b20 0x20b48 0x20b78 in this order",
        notes="order: dom enabled, dom forced, dom guard enabled, dom guard forced, intl enabled, intl forced, "
              "intl guard enabled, intl guard forced"),

    # ---- manual network selection ---------------------------------------------------------------
    (M2, "mns_start_scan"): Spec(
        [], tier=2, mode="argv", argc=0,
        evidence="handler 0xab3c: no argc test, no input; calls modem2_start_network_operator_scan only",
        notes="no parameters; starts the scan thread in modem2d (results via mns_get_info / mns_get_list)"),
    (M2, "mns_clear_list"): Spec(
        [], tier=1, mode="argv", argc=0,
        evidence="handler 0xaa7c: no input; calls modem2_clear_network_operator_list"),
    (M2, "mns_validate"): Spec(
        [], tier=2, mode="argv", argc=0,
        evidence="handler 0xa9ec: no input; modem2_validate_manual_network_selection, prints \"\\nMNS:[%d](%s)\" "
                 "0x1d670 with \"validated.\" 0x1d664 or \"disallowed, reverting to automatic network selection.\" 0x1d62c",
        notes="may revert to automatic selection when the manual network is not allowed"),
    (M2, "mns_set_oper"): Spec(
        ["Enable MNS? [1/0]: ", "Enter MCCMNC: ", "Enter access tech (GSM=0, UMTS=2, LTE=7): "],
        tier=2, mode="argv", argc=3, sample=["0", "000000", "7"],
        evidence="handler 0x13e38: cmp r0,#5 -> beq 0x13f12 (strtol argv[2], strncpy argv[3], strtol argv[4]) -> "
                 "modem2_select_manual_network; stdin prompts 0x20724 0x20738 0x20748. libmal_qct "
                 "modem2_modem_select_network 0x1b0a6: enable==0 takes the 'Automatic' path and ignores mccmnc",
        notes="enable 1 = lock to <mccmnc> with access tech 0 GSM / 2 UMTS / 7 LTE; enable 0 = automatic"),

    # ---- carrier aggregation --------------------------------------------------------------------
    (M2, "ca_set_enabled"): Spec(
        ["Enable CA (disabled=0, enabled=1): "], tier=2, mode="argv", argc=1, sample=["1"],
        evidence="handler 0x17ae0: cmp r0,#3 -> argv strtol -> modem2_set_ca_enabled; stdin prompt 0x214dc"),
    (M2, "ca_bands_set_enabled"): Spec(
        ["Enter CA band1: ", "Enter CA band2: ", "Enter enabled status: "], tier=2, mode="argv", argc=3,
        sample=["2", "66", "1"],
        evidence="handler 0x178b8: cmp r0,#5 -> argv strtol x3 -> modem2_set_ca_bands_enabled; stdin prompts "
                 "0x214a0 0x214b4 0x20fa4"),
    (M2, "ca_tri_bands_set_enabled"): Spec(
        ["Enter CA band1: ", "Enter CA band2: ", "Enter CA band3: ", "Enter enabled status: "], tier=2,
        mode="argv", argc=4, sample=["2", "66", "13", "1"],
        evidence="handler 0x17740: cmp r0,#6 -> argv strtol x4 -> modem2_set_ca_tri_bands_enabled; stdin prompts "
                 "0x214a0 0x214b4 0x214c8 0x20fa4"),
    (M2, "set_autonomous_gap_enabled"): Spec(
        ["Enable Autonomous Gap (disabled=0, enabled=1): "], tier=2, mode="argv", argc=1, sample=["0"],
        evidence="handler 0x17544: cmp r0,#3 -> argv strtol -> modem2_set_autonomous_gap_enabled; stdin prompt 0x21434"),

    # ---- band priority lists (no argv form: stdin only) ----------------------------------------
    (M2, "set_configured_band_priority_list"): Spec(
        ["Enter number of bands to enable(max 512): ", "Enter band number: "], tier=2, repeat=(0, 1), min_items=0,
        sample=["1", "66"],
        evidence="handler 0xfcf4: printf 0x1f3bc (\"...(max %d): \", 512), fgets/strtol count; loop printf 0x1f3e8 + "
                 "fgets/strtol while i < count && i != 512 (0xfd90); then modem2_set_configured_band_priority_list",
        notes="answers [count, band1..bandN]; count 0 clears the list (the factory state on this unit)"),
    (M2, "set_configured_nr5g_band_priority_list"): Spec(
        ["Enter number of bands to enable(max 512): ", "Enter band number: "], tier=2, repeat=(0, 1), min_items=0,
        sample=["1", "77"],
        evidence="handler 0xfbe4: same flow as 0xfcf4 (prompts 0x1f3bc / 0x1f3e8, cap 512 at 0xfc80) -> "
                 "modem2_set_configured_nr5g_band_priority_list",
        notes="answers [count, band1..bandN]; count 0 clears the list"),

    # ---- antenna / coexistence / 5G icon timer ---------------------------------------------------
    (M2, "set_ext_ant_enabled"): Spec(
        ["Enter the state (0 - mhb diabled (Ant 0), 1 - mhb enabled (Ant 1)): ",
         "Enter the state (0 - uhb diabled (Ant 0), 1 - uhb enabled (Ant 1)): "], tier=2, mode="argv", argc=2,
        sample=["0", "0"],
        evidence="handler 0x11a94: cmp r0,#4 -> beq 0x11b5c (strtol argv[2], argv[3]) -> modem2_set_ext_antenna_enabled; "
                 "stdin prompts 0x1ffc0 0x20008",
        notes="mhb = mid/high band port, uhb = ultra-high band port; 1 = external connector"),
    (M2, "set_lte_wifi_coex"): Spec(
        ["Enter 1 to enable LTE-U, 0 to disable it: "], tier=2, mode="argv", argc=1, sample=["0"],
        evidence="handler 0x11df8: cmp r0,#3 -> argv strtol -> modem2_set_lte_wifi_5ghz_coex; stdin prompt 0x20134",
        notes="no getter exists; the UI remembers the last value it set"),
    (M2, "set_5g_tuwb"): Spec(
        ["Enter the tuwb (range 0-30): "], tier=1, mode="argv", argc=1, sample=["10"],
        evidence="handler 0x11d40: cmp r0,#3 -> argv strtol -> modem2_set_5g_uwb_timer; stdin prompt 0x20114",
        notes="5G UW icon hold timer 0-30 (API rejects > 30)"),

    # ---- ccm2: airplane mode, cellular data, auto-connect, connect/disconnect -------------------
    (CCM, "set_air_mode_enabled"): Spec(
        ["What is enabled status [0|1]: "], tier=2, mode="argv", argc=1, sample=["0"],
        evidence="ccm2_cli handler 0x29c0: cmp r0,#3 -> argv strtol -> ccm2_set_airplane_mode_enabled; stdin prompt "
                 "0x4b40 + fgets/strtol; RC line 0x4bd0",
        notes="airplane mode switches off the radios listed by get_airplane_mode_disable_status (Wi-Fi and cellular)"),
    (CCM, "set_cell_data_enabled"): Spec(
        ["What is enabled status [0|1]: "], tier=1, mode="argv", argc=1, sample=["1"],
        evidence="ccm2_cli handler 0x2e50: cmp r0,#3 -> argv strtol -> ccm2_set_cellular_data_enabled; prompt 0x4b40",
        notes="fallback only: REST CellularServiceSettings.CellularDataEnabled is the primary path"),
    (CCM, "set_wwan_ac"): Spec(
        ["What is Autoconnect Index: "], tier=1, mode="argv", argc=1, sample=["2"],
        evidence="ccm2_cli handler 0x23a4: cmp r0,#3 -> beq 0x2452 strtol(argv[2],0,0) -> ccm2_wwan_set_autoconnect; "
                 "stdin path first lists indexes 0-3 (helper 0x4360, tbb: 0 manual, 1 clients connected, 2 service "
                 "available, 3 service available and not roaming) then prompt 0x4914",
        notes="0 manual, 1 when clients are connected, 2 when service is available, 3 ... and not roaming"),
    (CCM, "wwan_connect"): Spec(
        ["Enter an optional call id (<enter> for default):  "], tier=1, sample=[""],
        evidence="ccm2_cli handler 0x415c: stdin prompt 0x5378, fgets(60), strip CR/LF/TAB, empty -> "
                 "ccm2_wwan_connect(NULL) (0x41f0); argv form needs a non-empty call id so stdin is used",
        notes="answer an empty line = default internet call"),
    (CCM, "wwan_disconnect"): Spec(
        ["Enter an optional call id (<enter> for default):  "], tier=1, sample=[""],
        evidence="ccm2_cli handler 0x4064: same prompt 0x5378 / empty-line default -> ccm2_wwan_disconnect(NULL)",
        notes="answer an empty line = default internet call"),

    # ---- webui setting: automatic network selection ---------------------------------------------
    (WEBUI, "set_automatic_network_selection"): Spec(
        ["automatic_network_selection (0 => disabled, 1 => enabled): "], tier=1, mode="argv", argc=1, sample=["1"],
        evidence="webui_cli handler 0x2e58: cmp r0,#3 -> beq 0x2eba strtol(argv[2],0,0) -> "
                 "webui_set_automatic_network_selection; stdin prompt 0x5b0c + scanf(\"%d\") 0x56d4"),
}
