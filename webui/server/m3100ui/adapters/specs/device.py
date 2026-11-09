"""Setter specs for the Device and Services areas (power, display, LED, buzzer, GPS, file sharing,
notifications, TR-069, watchdog, health) — established by static analysis of the stock binaries in
reference/stock-fs/opt/nvtl/bin (objdump -M force-thumb + register tracking, scratch scripts in
%TEMP%/claude/area-device).  Evidence = handler address (from the {name, handler, help} table in
.data) and the .rodata offsets of the prompt strings (`strings -t x`).

Conventions found in these CLIs:
- handlers are `int h(int argc, char **argv)`; most test argc first (argv form) and otherwise print a
  prompt and read stdin with scanf("%d"/"%u"/"%s") or fgets().  We use the stdin form unless noted.
- sysintcli is the vendor "system interface" CLI (libsysintclient sys_* calls).  Its sys_set* helpers
  do get-modify-set on the vendor structs, so they preserve fields the dedicated CLIs would zero
  (see notes on powersave_cli set_settings / file_sharing_cli settings_set in docs/features/device.md).
  Its dispatch table holds inline 100-byte names + handler pointers; the boolean setters are thunks to
  one helper @0x289a4 that prints "Enable ? ( 1 or 0 ): " and reads fgets()/strtol(base 10).
"""
from typing import List, Optional

from ..cli_specs import Spec


class GroupRepeatSpec(Spec):
    """Counted list whose items are *groups* of prompts (the core Spec repeats a single prompt).

    prompts = fixed prompts + one group; `group` = (count_index, group_start, group_size): the answer
    at count_index is N and prompts[group_start:group_start+group_size] repeat N times.
    `group_no_spaces` = offsets inside a group that are read with scanf("%s") or must not hold spaces.
    For sandbox verification `sample` holds a one-item list, so every prompt is seen once in order.
    """

    def __init__(self, prompts: List[str], group: tuple, group_no_spaces: Optional[List[int]] = None, **kw):
        super().__init__(prompts, **kw)
        self.group = group
        self.group_no_spaces = group_no_spaces or []

    def expected_count(self, values: List[str]) -> int:
        ci, start, size = self.group
        try:
            n = int(str(values[ci]), 0)
        except (IndexError, ValueError):
            raise ValueError("count answer missing or not a number")
        if n < max(1, self.min_items):
            raise ValueError("at least %d item(s) required" % max(1, self.min_items))
        return len(self.prompts) - size + n * size

    def check(self, values: List[str]) -> None:
        super().check(values)
        from ...errors import ApiError
        _ci, start, size = self.group
        for i in range(start, len(values)):
            if (i - start) % size in self.group_no_spaces and (" " in str(values[i]) or "\t" in str(values[i])):
                raise ApiError(400, "invalid_value", "This value may not contain spaces")


ENABLE_PROMPT = "Enable ? ( 1 or 0 ): "   # sysintcli .rodata 0x2e6ac, helper @0x289a4 (fgets 79 + strtol base 10)


def _sysint_bool(handler: str, api: str, tier: int = 1, notes: str = "") -> Spec:
    return Spec([ENABLE_PROMPT], tier=tier, source="static", sample=["1"],
                evidence="sysintcli handler %s: thunk -> helper 0x289a4 with GOT %s; prompt .rodata 0x2e6ac" % (handler, api),
                notes=notes)


SPECS = {
    # ---------------------------------------------------------------- power (sysintcli, get-modify-set)
    ("sysintcli", "setPowerSaveEnabled"): _sysint_bool(
        "@0x28dc4", "sys_setPowerSaveEnabled", notes="fallback when REST PowerSaveSettings is unavailable"),
    ("sysintcli", "setDeepSleepEnabled"): _sysint_bool(
        "@0x28de0", "sys_setDeepSleepEnabled (GOT+0x914; the CLI's log label wrongly says sys_getDeepSleepEnabled)"),
    ("sysintcli", "setAutoShutdownEnabled"): _sysint_bool("@0x28dfc", "sys_setAutoShutdownEnabled (GOT+0x988)"),
    ("sysintcli", "setPowerSaveSettings"): Spec(
        ["Enter deep sleep idle duration ( in seconds ) : ", "Enter deep sleep max secs ( in seconds ) :",
         "Enter auto shutdown time ( in seconds ) :"], tier=1, source="static", sample=["300", "7200", "7200"],
        evidence="sysintcli setPowerSaveSettings @0x18ba8: 3x printf+fgets(79)+strtol(10); prompts .rodata 0x30668/0x3069c/0x306c8; "
                 "sys_setPowerSaveSettings @libsysintclient 0x33fac reads powersave_get_settings and overwrites only "
                 "deep_sleep_idle_time/deep_sleep_wakeup_time/shutdown_idle_time",
        notes="answers: [deep_sleep_idle_s, deep_sleep_wakeup_s (\"max secs\"), auto_shutdown_idle_s]"),
    ("sysintcli", "setUniversalChargingSettings"): Spec(
        ["Enter threshold ( percentage ) : "], tier=1, source="static", sample=["25"],
        evidence="sysintcli @0x18d9c: printf .rodata 0x307ac + fgets(79) + strtol(10); sys_setUniversalChargingSettings "
                 "(libsysintclient 0x34584) = dsm_get_settings, overwrite battery_pack_low_battery_threshold, dsm_set_settings",
        notes="stock UI offers 5/25/50"),
    ("sysintcli", "setUniversalChargingEnabled"): _sysint_bool(
        "@0x28e18", "sys_setUniversalChargingEnabled (GOT+0x938; dsm get-modify-set of battery_pack_active)"),
    # (hardware reset button: owned by the System area via factory_reset_cli set_hw_reset_sw_enabled)
    ("dsm_cli", "set_battery_health_enabled"): Spec(
        ["Battery_life_extenstion mode (1=on, 0=off): "], tier=1, source="static", sample=["1"],
        evidence="dsm_cli handler @0x1830: argc==3 -> strtol(argv[2],10) else printf .rodata 0x2ddc + scanf(\"%d\") -> "
                 "dsm_set_battery_health_enabled",
        notes="fallback when REST BatteryPreservationMode/Enabled is unavailable"),

    # ---------------------------------------------------------------- display (devui_cli, one int each)
    ("devui_cli", "set_screen_timeout_settings"): Spec(
        ["(4 => SCREEN_TIMEOUT_TEN_MINUTE, 5 => SCREEN_TIMEOUT_EIGHT_HOURS, 6 => SCREEN_TIMEOUT_NEVER): "],
        tier=1, source="static", sample=["0"],
        evidence="devui_cli @0x2660: puts .rodata 0x38ac + 0x3908, printf 0x394c, scanf(\"%d\"); index 0..6 -> table "
                 "@0x4994 [30,60,120,300,600,28800,0] seconds; >6 sends -1",
        notes="answer = index into [30, 60, 120, 300, 600, 28800, 0] (get returns seconds)"),
    ("devui_cli", "set_display_setting_enabled"): Spec(
        ["display setting enabled (0 => disabled, 1 => enabled): "], tier=1, source="static", sample=["1"],
        evidence="devui_cli @0x27a0: argc==3 argv / else printf .rodata 0x3a34 + scanf(\"%d\")"),
    ("devui_cli", "set_quickstart_enabled"): Spec(
        ["quickstart enabled (0 => disabled, 1 => enabled): "], tier=1, source="static", sample=["0"],
        evidence="devui_cli @0x2560: printf .rodata 0x37f4 + scanf(\"%d\")"),
    ("devui_cli", "set_usb_connection_prompt_enabled"): Spec(
        ["usb connection prompt enabled (0 => disabled, 1 => enabled): "], tier=1, source="static", sample=["1"],
        evidence="devui_cli @0x2460: printf .rodata 0x3710 + scanf(\"%d\")"),
    ("devui_cli", "set_file_sharing_display_setting_enabled"): Spec(
        ["file sharing enabled (0 => disabled, 1 => enabled): "], tier=1, source="static", sample=["1"],
        evidence="devui_cli @0x20a4: printf .rodata 0x34c4 + scanf(\"%d\")"),
    ("devui_cli", "set_wifi_display_setting_enabled"): Spec(
        ["wifi setting enabled (0 => disabled, 1 => enabled): "], tier=1, source="static", sample=["1"],
        evidence="devui_cli @0x1fa4: printf .rodata 0x33e4 + scanf(\"%d\")",
        notes="fallback for REST WiFi/DisplaySettings.AllowDeviceUIWiFiEnabled"),
    ("devui_cli", "set_admin_password_enabled"): Spec(
        ["web ui admin password enabled (0 => disabled, 1 => enabled): "], tier=1, source="static", sample=["0"],
        evidence="devui_cli @0x1ea4: printf .rodata 0x330c + scanf(\"%d\")",
        notes="shows the admin password on the touchscreen"),
    ("devui_cli", "set_notification_display_wakeup_setting_enabled"): Spec(
        ["notification display wakeup setting enabled (0 => disabled, 1 => enabled): "], tier=1, source="static",
        sample=["1"], evidence="devui_cli @0x1da4: printf .rodata 0x31e8 + scanf(\"%d\")"),
    ("devui_cli", "set_data_usage_touchscreen_enabled"): Spec(
        ["display datausage on touchscreen enabled (0 => disabled, 1 => enabled): "], tier=1, source="static",
        sample=["1"], evidence="devui_cli @0x1ca4: printf .rodata 0x30e8 + scanf(\"%d\")"),
    ("devui_cli", "set_display_ethernet_port_enabled"): Spec(
        ["ethernet show/hide on touchscreen (0 => disabled, 1 => enabled): "], tier=1, source="static", sample=["1"],
        evidence="devui_cli @0x19c0: printf .rodata 0x2e80 + scanf(\"%d\")"),
    ("devui_cli", "set_vital_update_prompt_enabled"): Spec(
        ["vital update prompt enabled (0 => disabled, 1 => enabled): "], tier=1, source="static", sample=["0"],
        evidence="devui_cli @0x18c0: printf .rodata 0x2da4 + scanf(\"%d\")"),
    ("devui_cli", "set_primary_wifi_info_display_settings"): Spec(
        ["show wifi name enabled (0 => disabled, 1 => enabled): ", "show wifi key enabled (0 => disabled, 1 => enabled): "],
        tier=1, source="static", sample=["1", "1"],
        evidence="devui_cli @0x2a10: argc==4 argv / else printf .rodata 0x3c70 + scanf(\"%hhu\"), printf 0x3ca8 + scanf(\"%hhu\")",
        notes="fallback for REST WiFi/DisplaySettings (Hide*Primary, inverted)"),
    ("devui_cli", "set_guest_wifi_info_display_settings"): Spec(
        ["show wifi name enabled (0 => disabled, 1 => enabled): ", "show wifi key enabled (0 => disabled, 1 => enabled): "],
        tier=1, source="static", sample=["1", "1"],
        evidence="devui_cli @0x2b5c: printf .rodata 0x3c70 + scanf(\"%hhu\"), printf 0x3ca8 + scanf(\"%hhu\")",
        notes="fallback for REST WiFi/DisplaySettings (Hide*Guest, inverted)"),

    # ---------------------------------------------------------------- LED and buzzer
    ("led_cli", "set_enabled"): Spec(
        ['Select LED enabled setting [0("not enabled"), 1("Enabled")]'], tier=1, source="static", sample=["1"],
        evidence="led_cli @0xeb4: argc==3 -> strtol(argv[2],10) else printf .rodata 0x1178 + scanf(\"%d\") -> led_set_enabled"),
    ("buzzer_cli", "on"): Spec(
        ["What is on time( in millsecs ): ", "What is off time( in millsecs ): ", "What is repeat count: "],
        tier=1, source="static", sample=["10", "10", "1"],
        evidence="buzzer_cli @0x974: argc==5 -> strtol(argv[2..4]) else printf .rodata 0xb78/0xba0/0xbc4 + 3x scanf(\"%u\") -> "
                 "buzzer_on(on_ms, off_ms, repeat)",
        notes="the mod ROMs call `buzzer_cli on 10 10 3` (M2000, same 3-value form)"),
    ("buzzer_cli", "off"): Spec(
        [], tier=1, source="static", mode="argv", argc=0,
        evidence="buzzer_cli @0x900: no input, calls buzzer_off()"),

    # ---------------------------------------------------------------- GPS
    ("gps_cli", "gps_start"): Spec([], tier=1, source="static", mode="argv", argc=0,
                                   evidence="gps_cli @0x1718: no input -> gps_start()"),
    ("gps_cli", "gps_stop"): Spec([], tier=1, source="static", mode="argv", argc=0,
                                  evidence="gps_cli @0x16e4: no input -> gps_stop()"),
    ("gps_cli", "force_xtra"): Spec([], tier=1, source="static", mode="argv", argc=0,
                                    evidence="gps_cli @0x198c: no input -> gps_force_xtra()"),
    ("gps_cli", "set_active"): Spec(
        ["Enter 1 or 0, On(1), Off(0): "], tier=1, source="static", sample=["1"],
        evidence="gps_cli @0x15b4: argc>2 -> strtol(argv[2],0) else printf .rodata 0x1e58 + scanf(\"%d\") -> gps_set_default_status"),
    ("gps_cli", "set_privacy"): Spec(
        ["Enter 1 or 0, Accept(1), Decline(0): "], tier=1, source="static", sample=["1"],
        evidence="gps_cli @0x1338: argc>2 argv / else printf .rodata 0x1dbc + scanf(\"%d\") -> gps_set_privacy_setting"),
    ("gps_cli", "set_nmea_tcp"): Spec(
        ["Enter 1 or 0, Enabled(1), Disabled(0): ", "Enter a port number, (1024-65535): "], tier=1, source="static",
        sample=["1", "11010"],
        evidence="gps_cli @0x1450: argc>3 argv / else printf .rodata 0x1df4 + scanf(\"%d\"), printf 0x1e1c(1024,65535) + "
                 "scanf(\"%d\") -> gps_set_nmea_over_tcp(enabled, port)",
        notes="prompt as printed (format 'Enter a port number, (%d-%d): ' with 1024, 65535); fallback for REST "
              "GPSSettings NMEA fields. Sandbox 2026-10-31 failed only on the unexpanded format text"),
    ("gps_cli", "agps_mode_set"): Spec(
        ["Enter 0,1 or 2: XTRA(0), MSA(1), MSB(2): "], tier=1, source="static", sample=["0"],
        evidence="gps_cli @0x19c0: argc>2 argv / else printf .rodata 0x2024 + scanf(\"%d\") -> gps_set_supl_mode"),

    # ---------------------------------------------------------------- file sharing
    ("file_sharing_cli", "settings_set"): Spec(
        ["What is file sharing enabled- 1(enabled), 0(disabled): ", "What is file sharing samba host name: ",
         "Full partition/drive share enabled - 1(share full partition), 0(share selected folders): ",
         "Full partition share read or Write - 1(full partition share is RW), 0(full partition share is RO) : "],
        tier=1, source="static", no_spaces=[1], sample=["1", "ROUTER", "1", "1"],
        evidence="file_sharing_cli @0x1cf0: scanf(\"%d\") enabled (.rodata 0x2974); enabled==0 -> set_settings at once "
                 "(host/option/rw ZERO); else scanf(\"%s\") host into a 16-byte field (0x29ac), scanf(\"%d\") option (0x29d4); "
                 "option==1 -> scanf(\"%d\") rw (0x2a30); option==0 -> no 4th prompt",
        notes="only called with enabled=1 (disable goes through sysintcli setSambaEnabled, which preserves the rest); "
              "host name max 15 chars, no spaces; a 4th answer is ignored when option=0"),
    ("file_sharing_cli", "settings_list_set"): GroupRepeatSpec(
        ["Enter number of items you want to set:", "Enter Entry[0],share_folder_name:",
         "Enter list[0],share_folder_type (0=rw, 1=ro):"], group=(0, 1, 2), group_no_spaces=[0],
        tier=1, source="static", sample=["1", "Public", "1"],
        evidence="file_sharing_cli @0x1a84: scanf(\"%d\") N (.rodata 0x27f0), calloc(260,N); per item printf 0x281c + "
                 "scanf(\"%s\") name, printf 0x2844 + scanf(\"%d\") type (>1 aborts); "
                 "file_sharing_set_folder_settings_list(list, N)",
        notes="answers: [N, name1, type1, ..., nameN, typeN]; type 0 = read-write, 1 = read-only; names without spaces. "
              "Prompts are recorded as printed for item 0 (format 'Enter Entry[%d],...': the index counts up)"),
    ("file_sharing_cli", "start_stop_share"): Spec(
        [], tier=1, source="static", mode="argv", argc=1, sample=["stop"],
        evidence="file_sharing_cli @0x15d8: argc==3 -> strcmp(argv[2], \"start\"|\"stop\") else usage"),
    ("file_sharing_cli", "mount_umount_partition"): Spec(
        [], tier=1, source="static", mode="argv", argc=1, sample=["unmount"],
        evidence="file_sharing_cli @0x1474: argc==3 -> strcmp(argv[2], \"mount\"|\"unmount\") else usage"),
    ("sysintcli", "setSambaEnabled"): _sysint_bool(
        "@0x28a60", "sys_setSambaEnabled (GOT+0x8ec; libsysintclient 0x17800 file_sharing_get_settings -> set enabled -> "
                    "file_sharing_set_settings)"),

    # ---------------------------------------------------------------- notifications (ans_cli)
    ("ans_cli", "set_stealth_mode_enabled"): Spec(
        ['Select Stealth mode enabled setting [0("not enabled"), 1("Enabled")]'], tier=1, source="static", sample=["0"],
        evidence="ans_cli @0x14c0: argc==3 argv / else printf .rodata 0x2094 + scanf(\"%d\")"),
    ("ans_cli", "set_audio_alerts_enabled"): Spec(
        ['Select audio alerts enabled setting [0("not enabled"), 1("Enabled")]'], tier=1, source="static", sample=["1"],
        evidence="ans_cli @0x13ac: argc==3 argv / else printf .rodata 0x1fc8 + scanf(\"%d\")"),
    ("ans_cli", "set_software_update_notification_settings"): Spec(
        ['Set the initial update notification enabled [0("not enabled"), 1("Enabled")]',
         'Set the vital update notification enabled [0("not enabled"), 1("Enabled")]'], tier=1, source="static",
        sample=["0", "0"], evidence="ans_cli @0x1720: printf .rodata 0x21b0 + scanf(\"%d\"), printf 0x2200 + scanf(\"%d\")"),
    ("ans_cli", "acknowledge_notification"): Spec(
        ["What is id of the notification to acknowledge:  "], tier=1, source="static", no_spaces=[0], sample=["simNone"],
        evidence="ans_cli @0x1990: argc==3 -> strcpy argv[2] else printf .rodata 0x240c + fgets(30) + trim -> "
                 "ans_acknowledge_notification"),
    ("ans_cli", "set_notification_settings"): GroupRepeatSpec(
        ["Enter number of notifications you want to set:", "Enter notification id:", "Enable notification (1,0)?:",
         "Enable audio alert notification (1,0)?:", "Enable wake display notification (1,0)?:",
         "Enter display surfaces (0 : None, 1 : Browser, 2 : LCD, 3 : All) :"], group=(0, 1, 5), group_no_spaces=[0],
        tier=1, source="static", sample=["1", "startup", "1", "1", "0", "0"],
        evidence="ans_cli @0x1a44: argc==2 -> scanf(\"%d\") N (.rodata 0x2484), calloc(48,N); per item printf 0x24b4, "
                 "getc() eats the newline, fgets(99)+trim id, scanf(\"%d\") x4 (0x24cc/0x24e8/0x2510/0x253c); "
                 "ans_set_notification_settings(list, N).  libans_api 0x2090 deletes /Settings/ANS/Notifications and "
                 "re-inserts the N items, so the FULL list must always be sent",
        notes="answers: [N, id, enabled, audio, wake, surfaces] * N; read-modify-write of the complete list only"),

    # ---------------------------------------------------------------- TR-069 (tr069_cli, fgets lines)
    ("tr069_cli", "set_status"): Spec(
        ["Enter TR069 status (0=Disable, 1=Enable): "], tier=2, source="static", sample=["0"],
        evidence="tr069_cli @0x16a0: argc==3 argv / else printf .rodata 0x2874 + fgets(99) + strtol(0) -> tr069_set_enabled_status"),
    ("tr069_cli", "set_acs_server_config"): Spec(
        ["Enter Username: ", "Enter Password: ", "Enter URL: ", "Enter Port number: ",
         "Enter Cellular-WAN Interface Type (1=Cellular(rmnet_data0), 0=USB(eth0)): ", "Enter IP Type (1=IPV4, 0=IPV6): "],
        tier=1, source="static", sample=["user", "pass", "acs.example.net", "7547", "1", "1"],
        evidence="tr069_cli @0x2000: 6x printf + fgets(99) (.rodata 0x2940/0x2954/0x2968/0x2988/0x2da4/0x2df0); strings: "
                 "TAB/CR/LF truncate, then strcpy into 256-byte fields; numbers strtol(10)",
        notes="text answers max 96 characters (fgets 99); the CLI echoes the password to stdout - never return its output"),
    ("tr069_cli", "set_acs_client_config"): Spec(
        ["Enter Username: ", "Enter Password: ", "Enter Port number: ",
         "Enter Connection Request Authentication Type (0=Disable, 1=Enable): ",
         "Enter Authentication Mode (0=Basic, 1=Digest): ", "Enter Periodic Inform Interval (PII) time(0:600)Sec: ",
         "Enter CWMP version: ", "Enter Log Level (0=CRITICAL, 1=WARNING, 2=NOTICE, 3=INFO, 4=DEBUG): "],
        tier=1, source="static", sample=["user", "pass", "7547", "1", "0", "100", "4", "0"],
        evidence="tr069_cli @0x1be4: 8x printf + fgets (.rodata 0x2940/0x2954/0x2988/0x2ae4/0x2b2c/0x2b5c/0x2b94/0x2bac)",
        notes="the CLI echoes the password to stdout - never return its output"),
    ("tr069_cli", "set_xmpp_config"): Spec(
        ["Enter Username: ", "Enter Password: ", "Enter URL: ", "Enter Jabber ID: ", "Enter Port number: ",
         "Enter XMPP status (0=Disable, 1=Enable): "], tier=1, source="static",
        sample=["", "", "", "", "0", "0"],
        evidence="tr069_cli @0x1880: 6x printf + fgets(99) (.rodata 0x2940/0x2954/0x2968/0x2974/0x2988/0x299c) -> "
                 "tr069_set_xmpp_config_list"),
    ("tr069_cli", "set_preserve_tr069_config"): Spec(
        ["Enter TR069 Restore settings (0=Disable, 1=Enable): "], tier=1, source="static", sample=["0"],
        evidence="tr069_cli @0x15c8: argc==3 argv / else printf .rodata 0x283c + fgets(99) + strtol(0) -> tr069_set_preserve_config"),
    ("tr069_cli", "tr069_restart"): Spec(
        [], tier=2, source="static", mode="argv", argc=0, evidence="tr069_cli @0x1418: no input -> tr069_restart()"),

    # ---------------------------------------------------------------- watchdog and health
    ("watchdog_cli", "set_settings"): Spec(
        ["What is enabled ( 0 or 1 ): ", "What is interval ( seconds ):  "], tier=2, source="static", sample=["1", "60"],
        evidence="watchdog_cli @0xce4: argc==4 argv / else printf .rodata 0x114c + scanf(\"%d\"), printf 0x1170 + "
                 "scanf(\"%u\") -> watchdog_set_settings",
        notes="tier 2 because it can switch the application watchdog off"),
    ("mifi_health_cli", "set_lowmem_reboot_settings"): Spec(
        ["Enter Low Memory Threshold [Value >= 15 in MB]: ", "Enter Check Interval (In Sec) : ",
         "Enter Minimum Device Uptime before Reboot(In Hours) :",
         "Enter Reboot Time Window Open (Only Hour in 24H format [0-23] ):",
         "Enter Reboot Time Window End (Only Hour in 24H format [0-23] ):"], tier=1, source="static",
        sample=["15", "60", "168", "2", "4"],
        evidence="mifi_health_cli @0x1258: 5x printf + scanf(\"%u\") (.rodata 0x1cac/0x1ce0/0x1d04/0x1d3c/0x1d80)"),
    ("mifi_health_cli", "set_mem_diag_settings"): Spec(
        ["Enter Max log file size (max size 2048) : ", "Enter Max number of backups : ", "Enter Safe zone least memory : ",
         "Enter Critical zone start memory :", "Enter Safe zone poll interval seconds :",
         "Enter Moderate zone poll interval seconds :", "Enter Critical zone poll interval seconds :",
         "Enter Safe zone store memory stats interval mins :", "Enter Moderate zone store memory stats interval mins :",
         "Enter Critical zone store memory stats interval mins :", "Enter Enable debug mode :",
         "Enter Top CPU usage threshold :"], tier=1, source="static",
        sample=["1024", "10", "15", "5", "60", "30", "15", "3", "2", "1", "0", "5"],
        evidence="mifi_health_cli @0x172c: 12x printf + scanf(\"%u\") (.rodata 0x21e0..0x23b0)"),
    ("mifi_health_cli", "set_log_settings"): Spec(
        ["Enter Logs Upload Enabled [0,1]: ", "Enter Upload Interval (In Sec) : "], tier=1, source="static",
        sample=["1", "86400"],
        evidence="mifi_health_cli @0x10f8: argc==4 argv / else printf .rodata 0x1bd8 + scanf(\"%u\"), printf 0x1c00 + scanf(\"%u\")",
        notes="health/diagnostic log upload to the vendor back-end"),
}
