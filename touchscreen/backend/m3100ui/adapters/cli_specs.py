"""Which vendor CLI commands may run, and how setters are fed.

Policy (fail closed):
- "get":      read-only by name (get_*, *_get*, is_*, …) or listed in READ_EXTRA → always allowed.
- "excluded": can brick/lock the unit, fakes daemon events, or talks to carrier/cloud
              back-ends → never run (see design §8).
- "setter":   everything else (set_*, actions such as connect/reboot/send).  Only runs with a
              Spec from adapters/specs/<area>.py giving its exact prompt sequence.  A spec's
              `source` says how it was established: "static" (disassembly), "sandbox" (prompts
              confirmed on the unit in tools/device/cli_sandbox.py: no daemon reachable,
              read-only FS, reboot/kill/socket blocked) or "roundtrip" (no-op write confirmed).

Adding a setter = add a Spec to your area's module + a unit test that the service's answer
list matches the prompt list.  tools/device/verify_specs.py upgrades sources on a real unit.
"""
import re
from typing import Dict, List, Optional

READ_RE = re.compile(r"^(get_.*|get|is_.*|get[A-Z].*|is[A-Z].*|.*_get_.*|.*_get|.*_is_enabled|settings_get|settings_list_get|"
                     r"sim_get_.*|rc_to_str|help|error_codes|print_error_codes|is_running)$")

# read-only commands whose names do not look like reads
READ_EXTRA = {
    "modem2_cli": {"active_tech_get", "enabled_tech_get", "active_band_get", "active_channel_get", "cai_get",
                   "ddtm_get", "lbs_get", "check_lte_ca_status", "mns_get_info", "mns_get_list", "call_get_status",
                   "uim_get_physical_slot_status", "sim_mgr_sim_switch_status", "validate_home",
                   "loc_survey_is_installed", "lifetime_counters_get", "radio_is_enabled", "5g_radio_is_enabled"},
    "gps_cli": {"gps_status"},
    "fota_cli": {"update_status", "revert_status", "last_success", "last_update_check", "full_history",
                 "current_record", "max_pkg_size", "sideload_max_pkg_size", "install_progress_status"},
    "nua_cli": {"status", "installation_status", "progress"},
    "cc_cli": {"carrier_count", "carrier_enable", "carrier_data", "carrier_label_count", "carrier_label_list",
               "current_carrier_id", "cc_folder"},
    "ccm2_cli": {"apps_call_state", "dm_call_state"},
    "thermal_monitor_cli": {"report"},
    "msgbus_cli": {"print_channels", "MsgBusGet"},
    "sms_cli": {"read"},
    "xmldata_cli": {"getstring"},
}

EXCLUDED_RE = re.compile(r"^(update_.*|send_.*|.*_simulate.*|trigger_.*|test_.*|.*_test_stubs)$")
EXCLUDED: Dict[str, set] = {
    "modem2_cli": {"efs_write", "efs_write_large", "efs_delete", "efs_read", "efs_read_large", "mdn_min_set",
                   "validate_spc", "set_sim_lock", "loc_golden_record_set_info", "factory_reset", "modem_init",
                   "lifetime_counters_update", "prof_set_pri_tech", "sd_config_set_setting", "run_raw_command_unsafe",
                   "loc_survey_course_start", "loc_survey_course_stop", "loc_survey_complete",
                   "loc_survey_location_selected", "loc_survey_stop", "set_custom_apn_to_database",
                   "set_custom_apn_to_db_using_imsi", "set_custom_apn_to_db_using_iccid", "set_lte_qam_status",
                   "set_lte_tm9", "set_lte_band_option", "set_ims_enable", "ims_reg_set_delay", "ims_set_sip_timer",
                   "ims_set_sip_data", "ims_pres_set_config", "ims_lvc_set_enabled", "mip_set_settings",
                   "mip_set_profile", "cai_set", "ehrpd_set_enabled", "ehrpd_set_state", "1xrtt_set_ext_timer",
                   "bsr_set_timers", "ddtm_set", "lbs_set", "band_class_set_enabled", "network_attach",
                   "set_5g_icon_mode", "enable_data_call", "powersave"},
    "nwcli": {"*"},          # only the argv rule below (qmi_idl read_file) is allowed
    "nwnvitem": {"-w"},
    "dsm_cli": {"set_persistent_mode", "set_persistent_rl", "devui_safe_shutdown_ack", "lpm_safe_exit_ack",
                "set_display_state", "settings_changed", "set_suspend_wakeup_time", "set_usb_device_type",
                "set_usb_typec_pwr_role_pref", "set_battery_health_capability", "set_settings"},
    "nua_cli": {"set_imei", "iot", "upload_mifi_health_log", "upload_file", "upload_custom_file",
                "upload_file_to_bucket", "upload_custom_file_to_bucket", "factory_reset", "su_condition", "timeout",
                "save_temp_files", "set_alarm", "delete_alarm", "register_dsm_handoff", "listen"},
    "fota_cli": {"sideload_prepare", "fops", "decrypt", "factory_reset", "stop_in_recovery", "new_session",
                 "clear_session", "cfg_save_new_record", "set_last_update_check", "abort_session",
                 "incr_defer_count", "modify_record", "verify_pkg", "install", "install_wait", "sideload_install",
                 "sua_update_start"},
    "msgbus_cli": {"MsgBusSetInt32", "MsgBusSetInt64", "MsgBusSetFloat", "MsgBusSetDouble", "MsgBusSetString",
                   "MsgBusSetBinary", "MsgBusPublishInt32", "MsgBusPublishInt64", "MsgBusPublishFloat",
                   "MsgBusPublishDouble", "MsgBusPublishString", "MsgBusPublishBinary", "MsgBus_Subscribe",
                   "MsgBusDelete"},
    "config_module_cli": {"*"},
    "com_subsystem_cli": {"*"},
    "cloud_engine_cli": {"*"},
    "deviceagent_cli": {"*"},
    "agentm_cli": {"*"},
    "longship_cli": {"*"},
    "dmdb_cli": {"*"},
    "tz_securefs_cli": {"*"},
    "mifi_debug_cli": {"*"},
    "usb_serial_cli": {"*"},       # menu CLI, loops forever on EOF
    "omadm_cli": {"*"}, "omadm_ipl_cli": {"*"}, "omadm_ipl_vzw_cli": {"*"}, "omadm_ipl_att_cli": {"*"},
    "omadm_ipl_bell_cli": {"*"}, "omadm_ipl_sprint_cli": {"*"}, "dua_insg_cli": {"*"}, "dua_telstra_cli": {"*"},
    "watchdog_cli": {"application_register", "application_send_keep_alive"},
    "mifi_health_cli": {"wd_app_crash_ind", "send_ab_failsafe_msg_bus_event"},
    "powersave_cli": {"wakelock_acquire", "wakelock_release", "dsm_state_change", "charger_det_change",
                      "battery_det_change", "battery_state_change", "battery_temperature_state_change",
                      "pwr_button_state_change", "ip_client_change"},
    "cbsd_cli": {"register", "grant", "spectrum", "heartbeat", "relinquish", "deregister", "conn_timer_expired",
                 "runtime_config", "validate_cbsd_details"},
    "tr069_cli": {"tr069_factory_reset", "add_tr069_mon_service", "set_tr069_config_modifed"},
    "webui_cli": {"dm_need_hfa_session", "dm_start_hfa_session", "dm_cancel_hfa_session", "dm_retry_hfa_operation",
                  "dm_start_profile_update", "dm_cancel_profile_update", "dm_user_confirmed_ok",
                  "dm_perform_sim_unlock", "su_check_for_update", "su_start_download_and_install",
                  "su_defer_download_and_install", "su_start_download", "su_potpone_download", "su_start_install",
                  "su_potpone_install", "su_mdip_defer_restart", "set_diagnostic_alert_choice",
                  "get_external_uri_data"},
    "ccm2_cli": {"set_wwan_ac_client_count", "update_roam_state", "update_usb_iface", "set_network_time",
                 "apps_call_start", "apps_call_stop", "dm_call_start", "dm_call_stop", "set_wifi_while_usbteth_temp"},
    "gps_cli": {"update_wan_connection", "enable_powersave_mode"},
    "sim_mgr_cli": set(),
    "wan_mgr_cli": {"register_wan", "deregister_wan", "send_wan_status_info", "log"},
    "vpn_cli": {"update_wan_connection_status", "update_connected_clients_count", "list_vpn_mon_service"},
    "file_sharing_cli": {"change_usb_state", "set_file_system_type", "service_monitor_add", "service_monitor_list"},
    "led_cli": {"update_system_state"},
    "devui_cli": {"set_hfa_activated", "set_initial_bootup_optimization_settings"},
    "ans_cli": set(),
    "sar_cli": {"set_sar_settings"},
    "thermal_monitor_cli": {"cycle"},
    "dua_vzw_cli": {"trigger_data_usage_sync"},
    "mifi_qcmap_cli": {"setup_cust_apn_routing", "tear_cust_apn_routing"},
    "usb_cli": {"set_tether_enabled_temp"},
    "wifi_cli": {"log"},
}

# argv-style tools: (cli, command) -> validator(args) ; anything else with args is refused
EXTRA_TOOLS = {"nwnvitem", "read_atcmd"}
_EFS_PATH_RE = re.compile(r"^/[A-Za-z0-9_./$-]{1,200}$")
_SHM_RE = re.compile(r"^/dev/shm/m3ui-[A-Za-z0-9_.-]{1,64}$")


def _nwcli_read_file(args: List[str]) -> bool:
    # nwcli qmi_idl read_file <local /dev/shm/m3ui-*> <efs path> <max bytes>
    return (len(args) == 4 and args[0] == "read_file" and bool(_SHM_RE.match(args[1]))
            and bool(_EFS_PATH_RE.match(args[2])) and ".." not in args[2] and args[3].isdigit()
            and 0 < int(args[3]) <= 262144)


def _nwnvitem_read(args: List[str]) -> bool:
    # nwnvitem -r -e <NV id or NW_NV_* name> [-i <index>]
    if len(args) not in (2, 4) or args[0] != "-e":
        return False
    if not re.match(r"^([0-9]{1,6}|NW_NV_[A-Z0-9_]{1,48})$", args[1]):
        return False
    return len(args) == 2 or (args[2] == "-i" and args[3].isdigit())


def _read_atcmd(args: List[str]) -> bool:
    # read_atcmd "<AT command>" /dev/smd7  — the vendor's own AT tool (used by fota_pmode.sh).
    # The AT gateway (adapters/at.py) applies the deny/risky/write tiers before calling; this rule
    # only guarantees the shape: one printable AT command that is not on the deny list, fixed channel.
    if len(args) != 2 or args[1] != "/dev/smd7":
        return False
    cmd = args[0]
    if not re.match(r"^[Aa][Tt][\x20-\x7e]{0,500}$", cmd):
        return False
    from . import at as at_mod
    try:
        return at_mod.classify(cmd) != "deny"
    except Exception:
        return False


ARGV_RULES = {
    ("nwcli", "qmi_idl"): _nwcli_read_file,
    ("nwnvitem", "-r"): _nwnvitem_read,
    ("read_atcmd", ""): _read_atcmd,
}


def check_argv(cli: str, command: str, args: List[str]) -> None:
    rule = ARGV_RULES.get((cli, command))
    if not rule or not rule(args):
        from ..errors import ApiError
        raise ApiError(403, "excluded", "Arguments not allowed for %s %s" % (cli, command))


class Spec:
    """Specification of one setter/action.

    prompts   literal stdin prompt texts in order (as printed by the CLI)
    mode      "stdin" (answers to prompts) or "argv" (values as command-line args; exactly `argc`)
    repeat    (count_index, item_index): answers[count_index] = N and prompts[item_index] repeats N times
    min_items minimum N for a repeated list (bands: 1 — an empty list would disable every band)
    source    "static" (binary analysis only), "sandbox" (prompts confirmed in tools/device/cli_sandbox.py),
              "roundtrip" (a no-op write was confirmed on a real unit)
    tier      design §8 tier; no_spaces = answer indexes read with scanf("%s")
    """

    def __init__(self, prompts: List[str], tier: int = 1, source: str = "static", mode: str = "stdin",
                 argc: Optional[int] = None, repeat: Optional[tuple] = None, min_items: int = 0,
                 evidence: str = "", notes: str = "", no_spaces: Optional[List[int]] = None,
                 sample: Optional[List[str]] = None):
        if source not in ("static", "sandbox", "roundtrip"):
            raise ValueError("bad source")
        if mode not in ("stdin", "argv"):
            raise ValueError("bad mode")
        if mode == "argv" and argc is None:
            raise ValueError("argv specs need argc")
        self.prompts = prompts
        self.tier = tier
        self.source = source
        self.mode = mode
        self.argc = argc
        self.repeat = repeat
        self.min_items = min_items
        self.evidence = evidence
        self.notes = notes
        self.no_spaces = no_spaces or []
        self.sample = sample            # realistic answers for sandbox verification (optional)

    def sample_answers(self) -> List[str]:
        if self.sample:
            return [str(x) for x in self.sample]
        if self.mode == "argv":
            return ["1"] * int(self.argc)
        if self.repeat:
            ci, _ii = self.repeat
            vals = ["1"] * (len(self.prompts) - 1)
            vals.insert(ci, "1")
            return vals[:len(self.prompts)] if len(vals) >= len(self.prompts) else vals + ["1"]
        return ["1"] * len(self.prompts)

    def expected_count(self, values: List[str]) -> int:
        if self.mode == "argv":
            return int(self.argc)
        if self.repeat:
            ci, _ii = self.repeat
            try:
                n = int(str(values[ci]), 0)
            except (IndexError, ValueError):
                raise ValueError("count answer missing or not a number")
            if n < self.min_items:
                raise ValueError("at least %d item(s) required" % self.min_items)
            return len(self.prompts) - 1 + n
        return len(self.prompts)

    def check(self, values: List[str]) -> None:
        """Refuse anything that would make the CLI read EOF (= zero/garbage values)."""
        from ..errors import ApiError
        try:
            want = self.expected_count(values)
        except ValueError as e:
            raise ApiError(400, "bad_answers", "Refusing to run setter: %s" % e)
        if len(values) != want:
            raise ApiError(500, "bad_answers", "Refusing to run setter: %d values given, %d expected" % (len(values), want))
        for i in self.no_spaces:
            if i < len(values) and (" " in str(values[i]) or "	" in str(values[i])):
                raise ApiError(400, "invalid_value", "This value may not contain spaces")
        for v in values:
            if str(v) == "" and self.mode == "argv":
                raise ApiError(400, "invalid_value", "Empty values are not allowed")


def _load_specs() -> Dict[tuple, Spec]:
    import importlib
    import pkgutil
    from . import specs as specs_pkg
    import logging
    log = logging.getLogger("m3100ui.specs")
    merged: Dict[tuple, Spec] = {}
    owners: Dict[tuple, str] = {}
    conflicts = set()
    for m in sorted(pkgutil.iter_modules(specs_pkg.__path__), key=lambda m: m.name):
        try:
            mod = importlib.import_module(specs_pkg.__name__ + "." + m.name)
        except Exception as e:  # a broken spec module just contributes nothing (fail closed)
            SPEC_ERRORS[m.name] = "%s: %s" % (type(e).__name__, e)
            log.error("spec module %s failed to load: %s", m.name, e)
            continue
        for key, sp in getattr(mod, "SPECS", {}).items():
            if key in merged:
                conflicts.add(key)
                SPEC_ERRORS["%s %s" % key] = "defined in both %s and %s; both dropped" % (owners[key], m.name)
                log.error("duplicate setter spec %s %s in %s and %s: dropping both", key[0], key[1], owners[key], m.name)
                continue
            merged[key] = sp
            owners[key] = m.name
    for key in conflicts:
        merged.pop(key, None)
    # verification results recorded on a real unit upgrade the source
    import json
    import os
    vpath = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                         "fixtures", "sandbox", "verified.json")
    try:
        with open(vpath, "r", encoding="utf-8") as f:
            for rec in json.load(f):
                key = (rec["cli"], rec["cmd"])
                if key in merged and rec.get("source") in ("sandbox", "roundtrip") and rec.get("prompts") == merged[key].prompts:
                    merged[key].source = rec["source"]
    except (OSError, ValueError, KeyError):
        pass
    return merged


_SPECS: Optional[Dict[tuple, Spec]] = None
SPEC_ERRORS: Dict[str, str] = {}


def all_specs() -> Dict[tuple, Spec]:
    global _SPECS
    if _SPECS is None:
        _SPECS = _load_specs()
    return _SPECS


def classify(cli: str, command: str, args: Optional[List[str]] = None) -> str:
    rule = ARGV_RULES.get((cli, command))
    if rule is not None and args:
        return "get" if rule(list(args)) else "excluded"
    if cli in EXTRA_TOOLS:
        return "excluded"          # argv-only tools: nothing allowed outside their ARGV rule
    ex = EXCLUDED.get(cli, set())
    if "*" in ex or command in ex:
        return "excluded"
    if command in READ_EXTRA.get(cli, set()):
        return "get"             # explicit read allow-list wins over the pattern exclusion below
    if EXCLUDED_RE.match(command or ""):
        return "excluded"
    if READ_RE.match(command or "help") or command in READ_EXTRA.get(cli, set()):
        return "get"
    return "setter"


def spec(cli: str, command: str) -> Optional[Spec]:
    return all_specs().get((cli, command))


def is_verified(cli: str, command: str) -> bool:
    return (cli, command) in all_specs()
