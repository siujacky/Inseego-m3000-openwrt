# Sandbox verification (re-scored from transcripts 2026-11-01 00:01; unit 76fe1623)

Totals: BLOCKED 3, PASS 194
PASS = prompts confirmed on the device with no daemon reachable; BLOCKED = CLI reads current values before prompting (needs a supervised no-op round trip); NONE = no transcript yet.

| CLI | command | mode | result | detail |
|---|---|---|---|---|
| ans_cli | acknowledge_notification | stdin | PASS | prompts match |
| ans_cli | set_audio_alerts_enabled | stdin | PASS | prompts match |
| ans_cli | set_notification_settings | stdin | PASS | prompts match |
| ans_cli | set_software_update_notification_settings | stdin | PASS | prompts match |
| ans_cli | set_stealth_mode_enabled | stdin | PASS | prompts match |
| buzzer_cli | off | argv | PASS | argv path taken |
| buzzer_cli | on | stdin | PASS | prompts match |
| ccm2_cli | accept_pco_event | argv | PASS | argv path taken |
| ccm2_cli | set_air_mode_enabled | argv | PASS | argv path taken |
| ccm2_cli | set_cell_data_enabled | argv | PASS | argv path taken |
| ccm2_cli | set_ntp_url | stdin | PASS | prompts match |
| ccm2_cli | set_static_ip | stdin | PASS | prompts match |
| ccm2_cli | set_wan_prio | stdin | PASS | prompts match |
| ccm2_cli | set_wifi_ap_enabled | stdin | PASS | prompts match |
| ccm2_cli | set_wifi_while_usbteth | stdin | PASS | prompts match |
| ccm2_cli | set_wwan_ac | argv | PASS | argv path taken |
| ccm2_cli | wan_connect | argv | PASS | argv path taken |
| ccm2_cli | wan_disconnect | argv | PASS | argv path taken |
| ccm2_cli | wwan_connect | stdin | PASS | prompts match |
| ccm2_cli | wwan_disconnect | stdin | PASS | prompts match |
| devui_cli | set_admin_password_enabled | stdin | PASS | prompts match |
| devui_cli | set_data_usage_touchscreen_enabled | stdin | PASS | prompts match |
| devui_cli | set_display_ethernet_port_enabled | stdin | PASS | prompts match |
| devui_cli | set_display_setting_enabled | stdin | PASS | prompts match |
| devui_cli | set_file_sharing_display_setting_enabled | stdin | PASS | prompts match |
| devui_cli | set_guest_wifi_info_display_settings | stdin | PASS | prompts match |
| devui_cli | set_notification_display_wakeup_setting_enabled | stdin | PASS | prompts match |
| devui_cli | set_primary_wifi_info_display_settings | stdin | PASS | prompts match |
| devui_cli | set_quickstart_enabled | stdin | PASS | prompts match |
| devui_cli | set_screen_timeout_settings | stdin | PASS | prompts match |
| devui_cli | set_usb_connection_prompt_enabled | stdin | PASS | prompts match |
| devui_cli | set_vital_update_prompt_enabled | stdin | PASS | prompts match |
| devui_cli | set_wifi_display_setting_enabled | stdin | PASS | prompts match |
| dsm_cli | reset_device | stdin | PASS | prompts match |
| dsm_cli | set_battery_health_enabled | stdin | PASS | prompts match |
| dsm_cli | set_default_usb_mode | argv | PASS | argv path taken |
| dsm_cli | shutdown_device | stdin | PASS | prompts match |
| dua_vzw_cli | set_data_usage_settings_info | stdin | PASS | prompts match |
| factory_reset_cli | do_factory_reset | argv | PASS | argv path taken |
| factory_reset_cli | set_hw_reset_sw_enabled | stdin | PASS | prompts match |
| file_sharing_cli | mount_umount_partition | argv | PASS | argv path taken |
| file_sharing_cli | settings_list_set | stdin | PASS | prompts match |
| file_sharing_cli | settings_set | stdin | PASS | prompts match |
| file_sharing_cli | start_stop_share | argv | PASS | argv path taken |
| gps_cli | agps_mode_set | stdin | PASS | prompts match |
| gps_cli | force_xtra | argv | PASS | argv path taken |
| gps_cli | gps_start | argv | PASS | argv path taken |
| gps_cli | gps_stop | argv | PASS | argv path taken |
| gps_cli | set_active | stdin | PASS | prompts match |
| gps_cli | set_nmea_tcp | stdin | PASS | prompts match |
| gps_cli | set_privacy | stdin | PASS | prompts match |
| led_cli | set_enabled | stdin | PASS | prompts match |
| mifi_health_cli | set_log_settings | stdin | PASS | prompts match |
| mifi_health_cli | set_lowmem_reboot_settings | stdin | PASS | prompts match |
| mifi_health_cli | set_mem_diag_settings | stdin | PASS | prompts match |
| mifi_qcmap_cli | set_allowed_port_list | argv | PASS | argv path taken |
| mifi_qcmap_cli | set_allowed_ports | argv | PASS | argv path taken |
| mifi_qcmap_cli | set_device_name | stdin | PASS | prompts match |
| mifi_qcmap_cli | set_dmz | argv | PASS | argv path taken |
| mifi_qcmap_cli | set_dns_filter | stdin | PASS | prompts match |
| mifi_qcmap_cli | set_dns_name | stdin | PASS | prompts match |
| mifi_qcmap_cli | set_dns_rebinding_status | stdin | PASS | prompts match |
| mifi_qcmap_cli | set_http_redirect | argv | PASS | argv path taken |
| mifi_qcmap_cli | set_icmp_response | argv | PASS | argv path taken |
| mifi_qcmap_cli | set_iface_type | stdin | PASS | prompts match |
| mifi_qcmap_cli | set_ippt | stdin | PASS | prompts match |
| mifi_qcmap_cli | set_ippt_feature_mode | stdin | PASS | prompts match |
| mifi_qcmap_cli | set_ipv6_firewall | argv | PASS | argv path taken |
| mifi_qcmap_cli | set_lan_dhcp_settings | stdin | PASS | prompts match |
| mifi_qcmap_cli | set_port_filter | argv | PASS | argv path taken |
| mifi_qcmap_cli | set_port_filter_list | argv | PASS | argv path taken |
| mifi_qcmap_cli | set_port_forwarding | argv | PASS | argv path taken |
| mifi_qcmap_cli | set_port_fwd_list | argv | PASS | argv path taken |
| mifi_qcmap_cli | set_static_route_list | stdin | PASS | prompts match |
| mifi_qcmap_cli | set_static_routes | stdin | PASS | prompts match |
| mifi_qcmap_cli | set_vpnpassthrough | argv | PASS | argv path taken |
| mifi_qcmap_cli | wan_set_prio | stdin | PASS | prompts match |
| modem2_cli | 5g_radio_set_enabled | argv | PASS | argv path taken |
| modem2_cli | ca_bands_set_enabled | argv | PASS | argv path taken |
| modem2_cli | ca_set_enabled | argv | PASS | argv path taken |
| modem2_cli | ca_tri_bands_set_enabled | argv | PASS | argv path taken |
| modem2_cli | enabled_tech_set | stdin | PASS | prompts match |
| modem2_cli | failback_switch_apn | argv | PASS | argv path taken |
| modem2_cli | mns_clear_list | argv | PASS | argv path taken |
| modem2_cli | mns_set_oper | argv | PASS | argv path taken |
| modem2_cli | mns_start_scan | argv | PASS | argv path taken |
| modem2_cli | mns_validate | argv | PASS | argv path taken |
| modem2_cli | multi_apn_add_profile | stdin | PASS | prompts match |
| modem2_cli | multi_apn_delete_profile | stdin | PASS | prompts match |
| modem2_cli | multi_apn_restore_default_profile | stdin | PASS | prompts match |
| modem2_cli | multi_apn_update_profile | stdin | PASS | prompts match |
| modem2_cli | pdn_set_ext_params | argv | PASS | argv path taken |
| modem2_cli | pdn_set_override_param | argv | PASS | argv path taken |
| modem2_cli | prof_set_act_tech | argv | PASS | argv path taken |
| modem2_cli | prof_set_cust_tech | stdin | PASS | prompts match |
| modem2_cli | prof_set_tech | stdin | PASS | prompts match |
| modem2_cli | radio_set_enabled | argv | PASS | argv path taken |
| modem2_cli | roam_set_enabled | argv | PASS | argv path taken |
| modem2_cli | roam_set_intl_enabled | argv | PASS | argv path taken |
| modem2_cli | run_raw_command | stdin | PASS | prompts match |
| modem2_cli | set_5g_tuwb | argv | PASS | argv path taken |
| modem2_cli | set_apn_failback_enabled | argv | PASS | argv path taken |
| modem2_cli | set_autonomous_gap_enabled | argv | PASS | argv path taken |
| modem2_cli | set_configured_band_priority_list | stdin | PASS | prompts match |
| modem2_cli | set_configured_nr5g_band_priority_list | stdin | PASS | prompts match |
| modem2_cli | set_enabled_lte_bands | stdin | PASS | prompts match |
| modem2_cli | set_enabled_nr5g_nsa_bands | stdin | PASS | prompts match |
| modem2_cli | set_enabled_nr5g_sa_bands | stdin | PASS | prompts match |
| modem2_cli | set_ext_ant_enabled | argv | PASS | argv path taken |
| modem2_cli | set_lte_wifi_coex | argv | PASS | argv path taken |
| modem2_cli | set_nr5g_disable_mode | stdin | PASS | prompts match |
| modem2_cli | set_roam_guard_settings | argv | PASS | argv path taken |
| modem2_cli | sim_change_pin | stdin | PASS | prompts match |
| modem2_cli | sim_enable_pin | stdin | PASS | prompts match |
| modem2_cli | sim_mgr_set_imsi_priority | argv | PASS | argv path taken |
| modem2_cli | sim_mgr_switch_slot | argv | PASS | argv path taken |
| modem2_cli | sim_unlock_pin | stdin | PASS | prompts match |
| modem2_cli | sim_unlock_puk | stdin | PASS | prompts match |
| modem2_cli | unlock_carrier | stdin | PASS | prompts match |
| nua_cli | set_poll_interval | argv | PASS | argv path taken |
| nua_cli | set_settings | argv | PASS | argv path taken |
| settings_cli | backup | argv | PASS | argv path taken |
| settings_cli | clear_modem_logs | argv | PASS | argv path taken |
| settings_cli | restore | stdin | PASS | prompts match |
| settings_cli | set_modem_logging_settings | stdin | PASS | prompts match |
| sim_mgr_cli | set_conn_configs | stdin | PASS | prompts match |
| sim_mgr_cli | set_conn_monitor_mode | stdin | PASS | prompts match |
| sim_mgr_cli | set_conn_recovery_mode | stdin | PASS | prompts match |
| sim_mgr_cli | set_recovery_max_DNS_fail_cnt | stdin | PASS | prompts match |
| sim_mgr_cli | set_recovery_max_device_reset_cnt | stdin | PASS | prompts match |
| sim_mgr_cli | set_recovery_max_mdm_disconnect_timeout | stdin | PASS | prompts match |
| sim_mgr_cli | set_recovery_max_mdm_reset_cnt | stdin | PASS | prompts match |
| sim_mgr_cli | set_recovery_max_sim_switches | stdin | PASS | prompts match |
| sim_mgr_cli | set_recovery_max_system_reset_cnt | stdin | PASS | prompts match |
| sim_mgr_cli | set_recovery_period | stdin | PASS | prompts match |
| sim_mgr_cli | set_sim_configs | stdin | PASS | prompts match |
| sim_mgr_cli | set_sim_monitor_mode | stdin | PASS | prompts match |
| sim_mgr_cli | set_switch_mode | stdin | PASS | prompts match |
| sms_cli | ab_add_entry | stdin | PASS | prompts match |
| sms_cli | ab_del_entry | stdin | PASS | prompts match |
| sms_cli | ab_edit_entry | stdin | PASS | prompts match |
| sms_cli | delete | stdin | PASS | prompts match |
| sms_cli | send | stdin | PASS | prompts match |
| sms_cli | set_state | stdin | PASS | prompts match |
| sysintcli | getGatewaySettings | argv | PASS | argv path taken |
| sysintcli | getLanMTU | argv | PASS | argv path taken |
| sysintcli | getReservedIPList | argv | PASS | argv path taken |
| sysintcli | setAutoShutdownEnabled | stdin | PASS | prompts match |
| sysintcli | setDeepSleepEnabled | stdin | PASS | prompts match |
| sysintcli | setDeviceName | stdin | PASS | prompts match |
| sysintcli | setGatewaySettings | stdin | PASS | prompts match |
| sysintcli | setLanMTU | stdin | PASS | prompts match |
| sysintcli | setMacFilterWhiteListEnabled | stdin | PASS | prompts match |
| sysintcli | setPowerSaveEnabled | stdin | PASS | prompts match |
| sysintcli | setPowerSaveSettings | stdin | PASS | prompts match |
| sysintcli | setReservedIPList | stdin | PASS | prompts match |
| sysintcli | setSambaEnabled | stdin | PASS | prompts match |
| sysintcli | setUniversalChargingEnabled | stdin | PASS | prompts match |
| sysintcli | setUniversalChargingSettings | stdin | PASS | prompts match |
| tr069_cli | set_acs_client_config | stdin | PASS | prompts match |
| tr069_cli | set_acs_server_config | stdin | PASS | prompts match |
| tr069_cli | set_preserve_tr069_config | stdin | PASS | prompts match |
| tr069_cli | set_status | stdin | PASS | prompts match |
| tr069_cli | set_xmpp_config | stdin | PASS | prompts match |
| tr069_cli | tr069_restart | argv | PASS | argv path taken |
| usb_cli | ippt_settings | stdin | PASS | prompts match |
| usb_cli | mode_switch | stdin | PASS | prompts match |
| usb_cli | set_tether_enabled | stdin | PASS | prompts match |
| vpn_cli | connect_vpn | stdin | PASS | prompts match |
| vpn_cli | disconnect_vpn | stdin | PASS | prompts match |
| vpn_cli | set_auto_connect | argv | PASS | argv path taken |
| vpn_cli | set_ipsec_vpn | argv | PASS | argv path taken |
| vpn_cli | set_settings | stdin | PASS | prompts match |
| vpn_cli | set_wguard_vpn | argv | PASS | argv path taken |
| vpn_cli | validate_openvpn_config_files | stdin | PASS | prompts match |
| wan_mgr_cli | set_auto_switch | stdin | PASS | prompts match |
| wan_mgr_cli | set_manual_wan | stdin | BLOCKED | no prompt before the CLI stopped (command [set_manual_wan] Failed to get wan priority settings.) - verify with a device round-trip |
| wan_mgr_cli | set_self_recovery_status | stdin | PASS | prompts match |
| wan_mgr_cli | set_waiting_timeout | stdin | PASS | prompts match |
| wan_mgr_cli | set_wan_failback | stdin | PASS | prompts match |
| wan_mgr_cli | set_wan_monitor_properties | stdin | PASS | prompts match |
| wan_mgr_cli | set_wan_priority_list | stdin | BLOCKED | no prompt before the CLI stopped (command [set_wan_priority_list] Failed to get the supported WAN count.) - verify with a device round-trip |
| watchdog_cli | set_settings | stdin | PASS | prompts match |
| webui_cli | set_automatic_network_selection | argv | PASS | argv path taken |
| webui_cli | set_i18n_settings | stdin | PASS | prompts match |
| webui_cli | set_log_settings | stdin | PASS | prompts match |
| webui_cli | set_login_settings | stdin | BLOCKED | no prompt before the CLI stopped (command [set_login_settings] webui_get_modified_login_settings) - verify with a device round-trip |
| webui_cli | set_software_update_settings | stdin | PASS | prompts match |
| wifi_cli | add_mac_blist | stdin | PASS | prompts match |
| wifi_cli | set_all_profiles | argv | PASS | argv path taken |
| wifi_cli | set_ap_profile | stdin | PASS | prompts match |
| wifi_cli | set_ap_settings | stdin | PASS | prompts match |
| wifi_cli | set_profile_status | stdin | PASS | prompts match |
| wifi_cli | set_settings | stdin | PASS | prompts match |
| wifi_cli | set_wifi_ps | stdin | PASS | prompts match |
| wifi_cli | start_wps | stdin | PASS | prompts match |
| wifi_cli | stop_wps | stdin | PASS | prompts match |
