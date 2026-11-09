"""Wi-Fi and connected-device setters (area: wifi).

All prompts were established by static analysis of the stock binaries in
reference/stock-fs/opt/nvtl/bin/ (arm-linux-gnueabihf-objdump -d -M force-thumb, literal pools resolved to
.rodata strings, scratch scripts in %TEMP%/claude/area-wifi/).  Addresses are virtual addresses in the
named binary; "dispatch" is the {name, handler, help} entry in .data.  Prompt texts are recorded as printed
(printf %d already substituted with the constant the handler passes).

Read-only commands that need an answer (wifi_cli get_ap_profile <1-4>, get_mac <0/1>, get_pindex <0/1>) are
"get" by name and need no spec.  wifi_cli set_ap_settings is specced in core.py (sandbox verified).
"""
from ..cli_specs import Spec

_WIFI = "wifi_cli (34544 B)"

SPECS = {
    # ---- wifi_cli ---------------------------------------------------------------------------------------
    ("wifi_cli", "set_ap_profile"): Spec(
        ["Profile index (1-5): ",
         "Profile status (0/1): ",
         "Profile name (1 - 20 characters): ",
         "SSID (1 - 32 characters):",
         "Broadcast SSID  (broadcast=0 /no broadcast=1): ",
         "WPS status (0/1): ",
         "Wifi privacy (0/1): ",
         "Interface Feature Mask (1=BL, 2=WL, 3=BL/WL 4=II, 5=II/BL, 6=II/WL, 7=BL/WL/II, 8=GF, 16=GW): ",
         "Mode (1=BGN, 5=GN_2.4, 10=AN, 11=AC_5, 12=AC_ONLY_5, 13=BGN_PLUS_AX_2.4, 14=ACN_PLUS_AX_5): ",
         "Channel (1-13=2.4 GHz., x-y=5 GHz., 0=ACS): ",
         "Channel Width (1=20 MHz, 2=40 MHz, 3=80 MHz): ",
         "Security type (0=None, 1=WEP, 2=WPA, 3=WPA2, 4=WPA2MIX, 5=WPA3, 6=WPA3 Transition, 7=OWE, 8=OWE Transition): ",
         "Enryption Mode (1=64 bit, 2=128 bit, 3=TKIP, 4=AES, 5=TKIP & AES): ",
         "Passphrase Key for WPA,WPA2, WPA2Mix Mode or WPA3 Mode (8 - 64 characters): "],
        tier=1, source="static",
        evidence=(_WIFI + ": dispatch .data 0x18044 -> handler 0x2e28. argc<=2 -> prompt path 0x2ff2. "
                  "Prompt literals .rodata 0x60f4, 0x6138, 0x617c(%d=20, getc loop, max 20), 0x61a0(%d=32, getc "
                  "loop, max 32), 0x61bc, 0x6220, 0x6234, 0x624c, 0x62e0, 0x6340, 0x6370, 0x63cc, 0x6468, "
                  "0x64dc(%d=64, getc loop, 64th char = error). Numbers: fgets(10)+strtol with end check. "
                  "Branches after 'Security type': 0x35c0 sec==0 -> wifi_set_ap_profile (12 answers read); "
                  "0x363e sec-2<=4 (WPA..WPA3 Transition) -> passphrase (14 read); 0x3a52 sec!=1 (OWE/OWE "
                  "Transition) -> call after encryption (13 read); sec==1 (WEP) -> auth 0x652c, index 0x658c, "
                  "4 keys 0x65a0 (19 read, never used here). wifi_get_capabilities must succeed first."),
        notes=("The prompt list is the WPA-family flow (14 answers). For security 0 the CLI stops after answer "
               "12 and for 7/8 after answer 13; the remaining answers stay unread in the pipe (harmless). "
               "WEP (1) is refused by services/wifi.py. Values are wifi_api enums (mode 1-14, width 1-3, "
               "security 0-8, encryption 1-5), not the sys_* enums of the web UI. Apply with set_all_profiles."),
        sample=["1", "1", "PRIMARY_2_4G", "Router-5G", "0", "1", "0", "1", "13", "0", "1", "6", "4", "changeme123"]),

    ("wifi_cli", "set_all_profiles"): Spec(
        [], tier=1, source="static", mode="argv", argc=0,
        evidence=(_WIFI + ": dispatch 0x180d4 -> handler 0x4160: cmp argc,#2 (more args -> 'Invalid number of "
                  "arguments.'), no prompts, calls wifi_ap_apply_profiles, prints 0x67ec / 0x6818."),
        notes="Applies the stored AP profiles (same as sys_applyWiFiProfiles). No input."),

    ("wifi_cli", "set_profile_status"): Spec(
        ["Enter the profile index (1-4):", "Enter the profile status (0=Disable, 1=Enable):"],
        tier=1, source="static",
        evidence=(_WIFI + ": dispatch 0x180e0 -> handler 0x23d0; argc<=2 -> prompts .rodata 0x5794 (fgets 9, "
                  "strtol) then 0x57b4 (fgets 9, strtol) -> 0x2436: wifi_get_ap_profile, status overwritten, "
                  "wifi_set_ap_profile (0x24f8)."),
        notes="Band state fallback when the REST AdvancedSettings object cannot be round-tripped. Apply afterwards.",
        sample=["3", "0"]),

    ("wifi_cli", "start_wps"): Spec(
        ["Enter the AP profile index (1-5):", "Enter the WPS methos PIN or PBC (1-2):", "Enter WPS Pin:"],
        tier=1, source="static",
        evidence=(_WIFI + ": dispatch 0x18050 -> handler 0x3d80; argc<=2 -> 0x3e84: prompt 0x6628 (%d=5, fgets 9, "
                  "index-1<=4), prompt 0x668c (fgets 9, strtol), 0x3f3a method==1 -> prompt 0x66b4 (fgets 20, "
                  "PIN copied with strncpy) else method 2 -> wifi_start_wps_session (0x3e32)."),
        notes=("answers: [profile index, 1=PIN|2=PBC, PIN]. For PBC the third answer is not read (send ''). "
               "Sandbox run 2026-10-31 with method 2 showed prompts 1-2 then the call (PIN prompt correctly absent); "
               "the sample now uses the PIN flow so verification sees all three prompts."),
        sample=["1", "1", "12345670"]),

    ("wifi_cli", "stop_wps"): Spec(
        ["Enter the Wifi AP profile index (1-5):"], tier=1, source="static",
        evidence=(_WIFI + ": dispatch 0x1805c -> handler 0x1e68; argc<=2 -> prompt .rodata 0x548c (%d=5 at 0x1ebc), "
                  "fgets, strtol, index-1<=4 -> wifi_stop_wps_session (0x1f12)."),
        sample=["1"]),

    ("wifi_cli", "set_settings"): Spec(
        ["Wifi status (0/1): ", "Country Code (US/JP/GB/AU/CA): "], tier=3, source="static",
        evidence=(_WIFI + ": dispatch 0x18110 -> handler 0x2bdc; argc<=2 -> prompt 0x5cf0 (fgets 10, strtol with "
                  "end check) then 0x5d04 (getc loop, max 31 chars) -> wifi_set_wlan_settings (0x2c56)."),
        notes=("Country / regulatory domain. Changing it makes wifi.sh reload the driver with a country-specific "
               "board-data file (see docs/recon/apps-wifi.md Risks) - tier 3. First answer = current 'Wifi is' "
               "value from get_settings so Wi-Fi is not switched."),
        sample=["1", "US"]),

    ("wifi_cli", "set_wifi_ps"): Spec(
        ["Enter Wifi status due to powersave (0=Disable, 1=Enable):"], tier=2, source="static",
        evidence=(_WIFI + ": dispatch 0x18134 -> handler 0x17b4; prompt 0x5170, fgets, strtol -> "
                  "wifi_set_power_save_enabled. libwifi_api 0x617c: value is the Wi-Fi radio state used by the "
                  "power-save daemon (powersave_notify 0x62e8: NORMAL -> 1 'starting Wifi', DEEPSLEEP -> 0 "
                  "'turning off Wifi'); 1 is ignored when the user switched Wi-Fi off."),
        notes="0 = pause the Wi-Fi radio now (temporary, not saved), 1 = resume.",
        sample=["1"]),

    ("wifi_cli", "add_mac_blist"): Spec(
        ["Enter blacklist MAC address:", "Enter the Reason for blacklist(1=WL,2=BL,3=Both):",
         "Enter the interface name <wlan0/wlan1>:"], tier=1, source="static", no_spaces=[0, 2],
        evidence=(_WIFI + ": dispatch 0x1811c -> handler 0x28ec; argc<=2 -> prompt 0x5bc0 (fgets 24, sscanf "
                  "%02X:x6), 0x5be0 (fgets 24, strtol), 0x5c30 (fgets 24, strncpy 17) -> "
                  "wifi_update_blacklist_mac (0x29ee)."),
        notes=("Runtime deny-list update in wifid (hostapd update_acl deny + deauthenticate); not persistent. "
               "Used only as fallback when REST BlockedDevices is unavailable."),
        sample=["02:00:00:00:00:09", "2", "wlan0"]),

    # ---- ccm2_cli -------------------------------------------------------------------------------------------
    # ("ccm2_cli", "set_wifi_while_usbteth") is specced in network.py with the identical prompt (handler 0x289c,
    # prompt .rodata 0x4b40, confirmed here independently); a second copy would make cli_specs drop both.
    ("ccm2_cli", "set_wifi_ap_enabled"): Spec(
        ["What is enabled status [0|1]:  "], tier=1, source="static",
        evidence=("ccm2_cli: dispatch 0x1708c -> handler 0x2c08; prompt .rodata 0x4b40 (fgets 60, strtol) -> "
                  "ccm2_set_wifi_ap_mode_enabled (0x2c6a) - the same call sys_setWiFiEnabled makes for REST "
                  "WiFi/AdvancedSettings.WiFiEnabled."),
        notes="Fallback for Wi-Fi on/off when the REST object cannot be round-tripped. Turning off = tier 2 in the service.",
        sample=["1"]),

    # ---- sysintcli (no command-name convention: every command is 'setter' by policy) -------------------------
    ("sysintcli", "setMacFilterWhiteListEnabled"): Spec(
        ["Enable ? ( 1 or 0 ): "], tier=1, source="static",
        evidence=("sysintcli (300084 B): main 0xad10 bsearches the 104-byte command table, calls handler(argc, "
                  "argv); entry 'setMacFilterWhiteListEnabled' -> 0x28ce4 -> generic 0x289a4: prompt .rodata "
                  "0x2e6ac (fgets 79, strtol base 10) -> sys_setMacFilterWhiteListEnabled, prints '%s returned %d'. "
                  "libsysintclient 0x26de4: dmdb_set_mac_filter_type + wifi_get/set_ap_profile."),
        notes=("1 = allow-list mode (only listed devices may join Wi-Fi), 0 = block-list mode. The tool always "
               "exits 0: services parse 'sys_setMacFilterWhiteListEnabled returned <rc>'."),
        sample=["0"]),
    ("sysintcli", "setDeviceName"): Spec(
        ["Enter the mac address(xx:xx:xx:xx:xx:xx):", "Enter device name :"], tier=1, source="static",
        no_spaces=[0],
        evidence=("sysintcli: entry 'setDeviceName' -> handler 0x200c4: prompt .rodata 0x345b4 (fgets 79, strncpy "
                  "18), prompt 0x34600 (fgets 79) -> sys_setDeviceName (0x20194); prints 'sys_setDeviceType "
                  "returned %d' (vendor copy/paste)."),
        notes="Friendly name in the vendor device database (shown as ConnectedDeviceFriendlyName).",
        sample=["02:00:00:00:00:01", "Laptop"]),
}
