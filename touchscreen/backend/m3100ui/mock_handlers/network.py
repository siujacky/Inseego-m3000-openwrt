"""Mock implementations for the router-network area (LAN, DNS, IP passthrough, Ethernet, WAN, USB,
static routes, time).  State lives in FixtureStore.state["network"]; every setter re-renders the matching
get_* output in the real vendor shape (fixtures/cli/...) so the same parsers run in mock and on a unit.

Setters that the vendor answers with "a device reset is required" (router2/wan_mgr rc 6) answer the
same way here, and record whether the reset prompt was answered "Y" (tests assert it never is).
"""
import copy

from ..adapters.mock import cli_handler, rest_handler
from ..errors import ApiError

Q = "MIFI_QCMAP"
RESET_MSG = "a device reset is required for the operation to take effect."
IPPT_IFACE_PROMPT = {1: "Enter hostname of the client:", 2: "Enter client MAC address : "}
WAN_LABEL = {0: "No WAN", 1: "WWAN", 2: "WiFi WAN", 3: "Ethernet WAN", 4: "USB WAN", 5: "APPs WAN", 6: "DM WAN",
             7: "WGVPN_0", 8: "WGVPN_1", 9: "WGVPN_2"}
ETH_LABEL = {2: "LAN", 3: "DHCP WAN", 4: "Static WAN", 5: "PPPoE WAN"}
WAN_PRIO_RESET = "To the new WAN Priority setting Changes to take effect requires reset of device:"

DEFAULTS = {
    "lan": {"ip": "192.168.1.1", "mask": "255.255.255.0", "fmt": 0, "ll": "fe80::3ca0:97ff:fe28:1e01", "dhcp": 0,
            "start": "192.168.1.2", "end": "192.168.1.102", "lease": 86400},
    "ippt": {"active": 0, "iface": 2, "client": ""}, "ippt_mode": 0, "rebinding": 0,
    "dns_filter": {"status": 0, "list": 0, "op": 0, "urls": ""},
    "device_name": "Inseego", "dns_name": "my.jetpack", "iface_type": 2, "static_routes": 0, "routes": [],
    "qcmap_prio": [1, 3],
    "wan_mgr": {"auto": 1, "manual": 1, "prio": [[1, 1], [3, 2]], "fb": [1, 120, 1, 10],
                "mon": [1, 0, 3, 30, 3, 10, "www.google.com", "www.yahoo.com", "www.inseego.com"],
                "recovery": 1, "timeout": 30},
    "usb_tether": 1, "wifi_teth": 1, "default_usb": 0, "usb_mode": 1, "rndis_disabled": 0, "static_ip": "0.0.0.0",
    "ntp": [604800, 60, "time.google.com", "pool.ntp.org", "time.nist.gov"], "ccm_prio": [3, 2, 1],
    "wan_connected": True, "reservations": [], "mtu": 1428, "gateway": [0, "", ""], "reboots": 0,
}


def S(store):
    with store.lock:
        if "network" not in store.state:
            store.state["network"] = copy.deepcopy(DEFAULTS)
        return store.state["network"]


def rc_line(fn, rc=0, module=Q, msg="success."):
    return "%s returned %d (%s: %s)\n" % (fn, rc, module, msg)


def reset_tail(st, answers, consumed, what):
    """Vendor text after rc 6.  The reset prompt reads the answer after the `consumed` ones (EOF when there is
    none, which the real handlers treat as "no"); a leading 'Y' would reboot the unit (counted, never expected)."""
    ans = (answers[consumed] if len(answers) > consumed else "").strip()
    line = what if what.endswith(":") else "%s Change requires reset of device:" % what
    out = "%s\nDo you want to reset the device Y/N:" % line
    if ans[:1].upper() == "Y":
        st["reboots"] += 1
        out += "sys_reboot system call returned 0 \n"
    return out + "\n"


def _int(v, default=0):
    try:
        return int(str(v).strip(), 0)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ renderers (real output shapes)
def render_lan(store):
    l = S(store)["lan"]
    store.set_text("mifi_qcmap_cli", "get_lan_dhcp_settings",
                   rc_line("cmd_router2_get_lan_dhcp_settings") +
                   "LAN IP address is: [%s]\nLAN subnet mask is: [%s]\nLAN mask format is: [%d]\n"
                   "LAN IPv6 link local address is: [%s]\nDHCP status is: [%s]\nDHCP IP range: [%s] - [%s]\n"
                   "DHCP lease (in seconds): [%d]\n" % (l["ip"], l["mask"], l["fmt"], l["ll"],
                                                         "Enabled" if l["dhcp"] else "Disabled", l["start"], l["end"],
                                                         l["lease"]))


def render_ippt(store):
    st = S(store)
    p = st["ippt"]
    store.set_text("mifi_qcmap_cli", "get_ippt", rc_line("cmd_router2_get_ippt") +
                   "Is active:       [%d]\nInterface:       [%d]\nClient Identity (%s)\n" % (p["active"], p["iface"], p["client"]))
    store.set_text("mifi_qcmap_cli", "get_ippt_feature_mode", rc_line("cmd_get_ippt_feature_mode") +
                   "IPPT Feature Mode is: [%d]\n" % st["ippt_mode"])


def render_dns(store):
    st = S(store)
    store.set_text("mifi_qcmap_cli", "get_dns_rebinding_status", rc_line("cmd_get_dns_rebinding_status") +
                   "DNS Rebinding Protection Status:[%d]\n" % st["rebinding"])
    f = st["dns_filter"]
    store.set_text("mifi_qcmap_cli", "get_dns_filter",
                   "DNS Filter status is: [%s]\nDNS Filter List Configured: [%s]\nDNS Filter Last Operation: [%s]\n"
                   "DNS Filter URL List: [%s]\n" % ("Enabled" if f["status"] else "Disabled",
                                                     "BlackList" if f["list"] else "WhileList",
                                                     "Added" if f["op"] else "Deleted", f["urls"]))


def render_names(store):
    st = S(store)
    store.set_text("mifi_qcmap_cli", "get_device_name", rc_line("cmd_get_device_name") + "Device name:[%s]\n" % st["device_name"])
    store.set_text("mifi_qcmap_cli", "get_dns_name", rc_line("cmd_router2_get_dns_name") +
                   "Host DNS name:       [%s]\n" % st["dns_name"])


def render_eth(store):
    t = S(store)["iface_type"]
    store.set_text("mifi_qcmap_cli", "get_iface_type", rc_line("cmd_get_ethernet_iface_type") +
                   "Ethernet (eth0) interface type is :   [%s]\n" % ETH_LABEL.get(t, "Invalid"))
    store.set_text("mifi_qcmap_cli", "get_ethernet_info",
                   "Ethernet Interface Status: 1\nTotal No of Ethernet Support Count For Bradshaw is 1\n"
                   "_________________________________________________________________ \n"
                   "eth0 Interface Type: %d\neth0 Link Status:1\neth0 MAC Address: 02:00:00:00:00:01\n"
                   "-------------------------------------------------------------------\n" % t)


def render_routes(store):
    st = S(store)
    store.set_text("mifi_qcmap_cli", "get_static_routes", rc_line("cmd_get_static_routes_status") +
                   "Static Routes Feature is:   [%s]\n" % ("Enable" if st["static_routes"] else "Disable"))
    lines = ["RouteType[%s] Network[%s] Gateway[%s] InterfaceType[%s] Metric[%d]" %
             ("IPV4" if r[0] == 4 else "IPV6", r[1], r[2], WAN_LABEL.get(r[3], "Invalid WAN Type"), r[4])
             for r in st["routes"]]
    store.set_text("mifi_qcmap_cli", "get_static_route_list", rc_line("cmd_get_static_route_list") +
                   "\n Static Route List... \n" + "".join(l + "\n" for l in lines))


def render_qcmap_prio(store):
    p = S(store)["qcmap_prio"]
    store.set_text("mifi_qcmap_cli", "wan_get_prio", "First priority wan is [%s]\nSecond priority wan is [%s]\n" %
                   (WAN_LABEL[p[0]], WAN_LABEL[p[1]]) + rc_line("cmd_mifi_qcmap_get_wan_prio"))


def render_wan_mgr(store):
    w = S(store)["wan_mgr"]
    M = "WAN_MGR"
    ed = lambda v: "Enabled" if v else "Disabled"  # noqa: E731
    fb, mon = w["fb"], w["mon"]
    prio = sorted(w["prio"], key=lambda x: x[1])
    store.set_text("wan_mgr_cli", "get_auto_switch", rc_line("cmd_get_auto_switch", module=M) +
                   "The Auto Switch Value is [%d]\n" % w["auto"])
    store.set_text("wan_mgr_cli", "get_manual_wan", rc_line("cmd_get_manual_wan", module=M) +
                   "The default wan is [%s]\n" % WAN_LABEL.get(w["manual"], "WWAN"))
    store.set_text("wan_mgr_cli", "get_wan_priority_list", rc_line("cmd_get_wan_priority_list", module=M) +
                   "Supported WAN count : [%d]\n" % len(prio) +
                   "_________________________________________________________________ \n" +
                   "".join("WAN manager wan wan_type : [%s]\nWAN manager priority     : [%d]\n"
                           "-------------------------------------------------------------------\n" %
                           (WAN_LABEL.get(t, "WWAN"), p) for t, p in prio))
    store.set_text("wan_mgr_cli", "get_wan_failback", rc_line("cmd_get_wan_failback", module=M) +
                   "WAN manager failback enable : [%d]\nWAN manager failback interval : [%d]\n"
                   "WAN manager WWAN data traffic check interval : [%d]\nWAN manager WWAN data traffic check limit : [%d]\n"
                   % tuple(fb))
    store.set_text("wan_mgr_cli", "get_wan_monitor_properties", rc_line("cmd_get_wan_monitor_properties", module=M) +
                   "WAN monitor Enabled  : [%d]\nWAN use client connected state : [%d]\nWAN monitor retry count : [%d]\n"
                   "WAN monitor routed monitor interval : [%d] sec\nWAN monitor online monitor interval : [%d] min\n"
                   "WAN monitor failed monitor interval : [%d] sec\nWAN monitor dns url1 : [%s]\n"
                   "WAN monitor dns url2 : [%s]\nWAN monitor dns url3 : [%s]\n" % tuple(mon))
    store.set_text("wan_mgr_cli", "get_self_recovery_status", rc_line("cmd_get_self_recovery_status", module=M) +
                   "The Self Recovery Status is [%d]\n" % w["recovery"])
    store.set_text("wan_mgr_cli", "get_waiting_timeout", rc_line("cmd_get_waiting_timeout", module=M) +
                   "The waiting timeout value  is [%d]\n" % w["timeout"])
    store.set_text("wan_mgr_cli", "get_all_wan_info", rc_line("cmd_get_all_wan_info", module=M) +
                   "Suppoted WAN Count: [%d]\nAuto-Switch Status: [%s]\nManual WAN: [%s]\nSuppoted WAN Priorities:\n"
                   % (len(prio), ed(w["auto"]), WAN_LABEL.get(w["manual"], "WWAN")) +
                   "".join("WAN Type [%s]\nWAN Priority [%d]\n" % (WAN_LABEL.get(t, "WWAN"), p) for t, p in prio) +
                   "Failback Status : [%s]\nFailback Timer :  [%d] min\nWAN Monitor Feature Status : [%s]\n"
                   "WAN use client connected state : [%s]\nWAN Retry Count: [%d]\nRouted WAN monitor interval: [%d] sec\n"
                   "Online WAN monitor interval: [%d] min\nFailed WAN monitor interval: [%d] sec\n"
                   "DNS URL-1: [%s]\nDNS URL-2: [%s]\nDNS URL-3: [%s]\n" %
                   (ed(fb[0]), fb[1], ed(mon[0]), ed(mon[1]), mon[2], mon[3], mon[4], mon[5], mon[6], mon[7], mon[8]))


def render_usb(store):
    st = S(store)
    store.set_text("usb_cli", "get_tether_enabled", "usb_get_tether_enabled returned 0 (USB: success.)\n"
                   "tether_enabled=[%d]\n" % st["usb_tether"])
    debug = st["usb_mode"] == 1
    store.set_text("usb_cli", "get_state",
                   "usb_get_state returned 0 (USB: success.)\nusb_default_mode=[%d][%s]\nenterprise_mode_enabled=[0]\n"
                   "mac_addr_host=[02:00:00:00:00:01]\nmac_addr_dev=[02:00:00:00:00:01]\nstart_mode=[3][Online]\n"
                   "net_if_name=[rndis0]\nnet_if_func_name=[rndis]\npid=[%s]\nfunction_list=[%s]\n"
                   "usb_gadget_configured=[1]\nauto_detect_running=[0]\nusb_resumed_not_configured=[0]\n"
                   "usb_tether_change_pending=[0]\ntemp_tether=[0][Invalid]\n" %
                   (st["default_usb"], "Debug" if st["default_usb"] == 0 else "End User",
                    "0xB023" if debug else "0xB020", "rndis,dun,diag,adb,hid" if debug else "rndis,mass_storage"))
    store.set_text("dsm_cli", "get_default_usb_mode", "cmd_get_default_usb_mode returned 0 (DSM: success.)\n"
                   "USB mode=[%d]\n" % st["default_usb"])
    store.set_text("ccm2_cli", "get_wifi_while_usbteth", "ccm_get_wifi_while_usbteth returned 0 (CCM: success.)\n"
                   "enabled:[%d]\n" % st["wifi_teth"])
    store.set_text("ccm2_cli", "get_static_ip", "ccm_get_static_ip returned 0 (CCM: success.)\nip:[%s]\n" % st["static_ip"])


def render_ccm(store):
    st = S(store)
    n = st["ntp"]
    store.set_text("ccm2_cli", "get_ntp_url", "ccm_get_ntp_url returned 0 (CCM: success.)\nmin_delay:[%d]\nretries:[%d]\n"
                   "url1:[%s]\nurl2:[%s]\nurl3:[%s]\n" % tuple(n))
    store.set_text("ccm2_cli", "get_wan_prio", "ccm_get_wan_prio returned 0 (CCM: success.)\n" +
                   "".join("Prio:[%d] Wan:[%s]\n" % (i + 1, WAN_LABEL[w]) for i, w in enumerate(st["ccm_prio"])))


def render_all(store):
    for fn in (render_lan, render_ippt, render_dns, render_names, render_eth, render_routes, render_qcmap_prio,
               render_wan_mgr, render_usb, render_ccm):
        fn(store)


def ok(store, prompts, fn, module=Q):
    return prompts + rc_line(fn, module=module)


# ------------------------------------------------------------------ mifi_qcmap_cli
@cli_handler("mifi_qcmap_cli", "set_lan_dhcp_settings")
def _set_lan(store, answers, args):
    st = S(store)
    a = list(answers)
    l = st["lan"]
    l.update({"ip": a[0], "mask": a[1], "fmt": _int(a[2]), "dhcp": _int(a[3])})
    if l["dhcp"] and len(a) >= 7:      # the CLI reads the range only when DHCP is on
        l.update({"start": a[4], "end": a[5], "lease": _int(a[6], 86400)})
    render_lan(store)
    return ("Enter LAN IP address: Enter LAN subnet mask: Enter LAN mask format (0=Disable, 1=Enable): "
            "Enter DHCP status (0=Disable, 1=Enable): ") + rc_line("cmd_router2_set_lan_dhcp_settings")


@cli_handler("mifi_qcmap_cli", "set_ippt")
def _set_ippt(store, answers, args):
    st = S(store)
    active, iface, client = _int(answers[0]), _int(answers[1]), answers[2]
    st["ippt"] = {"active": active, "iface": iface, "client": client}
    render_ippt(store)
    return ("Enter active (0,1): Enter Interface (1=USB, 2=Ethernet): %s" % IPPT_IFACE_PROMPT.get(iface, "") +
            rc_line("cmd_router2_set_ippt", 6, msg=RESET_MSG) +
            reset_tail(st, answers, 3 if iface in IPPT_IFACE_PROMPT else 2, "IPPT settings"))


@cli_handler("mifi_qcmap_cli", "set_ippt_feature_mode")
def _set_ippt_mode(store, answers, args):
    st = S(store)
    st["ippt_mode"] = _int(answers[0])
    render_ippt(store)
    return ("Enter IPPT Feature Mode value (0=Disable, 1=Enabled): " + rc_line("cmd_set_ippt_feature_mode", 6, msg=RESET_MSG)
            + reset_tail(st, answers, 1, "IPPT Feature Mode settings"))


@cli_handler("mifi_qcmap_cli", "set_dns_rebinding_status")
def _set_rebinding(store, answers, args):
    st = S(store)
    st["rebinding"] = _int(answers[0])
    render_dns(store)
    return ("Enter DNS Rebinding status (0=Disable, 1=Enabled): " + rc_line("cmd_set_dns_rebinding_status", 6, msg=RESET_MSG)
            + reset_tail(st, answers, 1, "DNS Rebinding settings"))


@cli_handler("mifi_qcmap_cli", "set_dns_filter")
def _set_dns_filter(store, answers, args):
    st = S(store)
    op = _int(answers[0])
    if op == 0:
        st["dns_filter"] = {"status": 0, "list": st["dns_filter"]["list"], "op": 0, "urls": ""}
        prompts = "Enter DNS Filter Operation (0=Delete, 1=Add): "
    else:
        st["dns_filter"] = {"status": _int(answers[1]), "list": _int(answers[2]), "op": 1, "urls": answers[3]}
        prompts = ("Enter DNS Filter Operation (0=Delete, 1=Add): Enter DNS Filter status (0=Disable, 1=Enable): "
                   "Enter DNS Filter for Black or White List (0=White, 1=Black): Enter URLs(separated by ','): ")
    render_dns(store)
    return prompts + "DNS filter: " + rc_line("cmd_set_dns_filter_settings")


@cli_handler("mifi_qcmap_cli", "set_device_name")
def _set_device_name(store, answers, args):
    st = S(store)
    st["device_name"] = answers[0]
    render_names(store)
    return ("Enter DEVICE name: " + rc_line("cmd_set_device_name", 6, msg=RESET_MSG) +
            "Please reboot the device to take effect in all cases.\n")


@cli_handler("mifi_qcmap_cli", "set_dns_name")
def _set_dns_name(store, answers, args):
    st = S(store)
    st["dns_name"] = answers[0]
    render_names(store)
    return "Enter DNS name: " + rc_line("cmd_router2_set_dns_name")


@cli_handler("mifi_qcmap_cli", "set_iface_type")
def _set_iface_type(store, answers, args):
    st = S(store)
    st["iface_type"] = _int(answers[0], 2)
    render_eth(store)
    return ("Enter Ethernet eth0 interface type(2=LAN, 3=DHCP WAN): " +
            rc_line("cmd_set_ethernet_iface_type", 6, msg=RESET_MSG) + reset_tail(st, answers, 1, "Ethernet Type settings"))


@cli_handler("mifi_qcmap_cli", "set_static_routes")
def _set_static_routes(store, answers, args):
    st = S(store)
    st["static_routes"] = _int(answers[0])
    render_routes(store)
    return "Enter static routes feature status (0=Disable, 1=Enable): " + rc_line("cmd_set_static_routes_status")


@cli_handler("mifi_qcmap_cli", "set_static_route_list")
def _set_static_route_list(store, answers, args):
    st = S(store)
    n = _int(answers[0])
    rows = []
    for i in range(n):
        f = answers[1 + i * 5:1 + i * 5 + 5]
        rows.append([_int(f[0], 4), f[1], f[2], _int(f[3], 1), _int(f[4])])
    st["routes"] = rows
    render_routes(store)
    return "Enter the number of records: " + rc_line("cmd_set_static_route_list")


@cli_handler("mifi_qcmap_cli", "wan_set_prio")
def _wan_set_prio(store, answers, args):
    st = S(store)
    st["qcmap_prio"] = [_int(answers[0], 1), _int(answers[1], 3)]
    render_qcmap_prio(store)
    return ("Enter the first priority WAN: WWAN(1)/Etherenet(3) :Enter the second priority WAN: WWAN(1)/Etherenet(3) :" +
            rc_line("cmd_mifi_qcmap_set_wan_prio", 6, msg=RESET_MSG) + reset_tail(st, answers, 2, WAN_PRIO_RESET))


# ------------------------------------------------------------------ wan_mgr_cli
def _wm(store, fn, answers, mutate, prompt):
    st = S(store)
    mutate(st["wan_mgr"], answers)
    render_wan_mgr(store)
    return prompt + rc_line(fn, module="WAN_MGR")


@cli_handler("wan_mgr_cli", "set_auto_switch")
def _wm_auto(store, answers, args):
    return _wm(store, "cmd_set_auto_switch", answers, lambda w, a: w.update(auto=_int(a[0])),
               "Enter WAN Auto Switch Value(0/1): ")


@cli_handler("wan_mgr_cli", "set_manual_wan")
def _wm_manual(store, answers, args):
    return _wm(store, "cmd_set_manual_wan", answers, lambda w, a: w.update(manual=_int(a[0], 1)),
               "make sure auto_switch is disabled before configuring manual_wan \n"
               "Enter the manual WAN Type (1=WWAN, 2=Wifi, 3=Ethernet 4=USB): ")


@cli_handler("wan_mgr_cli", "set_wan_priority_list")
def _wm_prio(store, answers, args):
    st = S(store)
    st["wan_mgr"]["prio"] = [[_int(answers[0]), _int(answers[1])], [_int(answers[2]), _int(answers[3])]]
    render_wan_mgr(store)
    return ("You can configure the priorities for the following WANs:\n" +
            rc_line("cmd_set_wan_priority_list", 6, module="WAN_MGR", msg=RESET_MSG) +
            reset_tail(st, answers, 4, WAN_PRIO_RESET))


@cli_handler("wan_mgr_cli", "set_wan_failback")
def _wm_failback(store, answers, args):
    return _wm(store, "cmd_set_wan_failback", answers, lambda w, a: w.update(fb=[_int(x) for x in a[:4]]),
               "Enter the failback feature enable or disable (0:disable, 1:enable): ")


@cli_handler("wan_mgr_cli", "set_wan_monitor_properties")
def _wm_monitor(store, answers, args):
    return _wm(store, "cmd_set_wan_monitor_properties", answers,
               lambda w, a: w.update(mon=[_int(x) for x in a[:6]] + list(a[6:9])),
               "Enter the WAN monitor feature enable or disable (0:disable, 1:enable): ")


@cli_handler("wan_mgr_cli", "set_self_recovery_status")
def _wm_recovery(store, answers, args):
    return _wm(store, "cmd_set_self_recovery_status", answers, lambda w, a: w.update(recovery=_int(a[0])),
               "Enter WAN Self Recovery Status(0/1): ")


@cli_handler("wan_mgr_cli", "set_waiting_timeout")
def _wm_timeout(store, answers, args):
    return _wm(store, "cmd_set_waiting_timeout", answers, lambda w, a: w.update(timeout=_int(a[0], 30)),
               "Enter the no connectivity timeout value")


# ------------------------------------------------------------------ usb / dsm / ccm2
@cli_handler("usb_cli", "set_tether_enabled")
def _usb_tether(store, answers, args):
    st = S(store)
    st["usb_tether"] = _int(answers[0])
    render_usb(store)
    return "What is tether_enabled (1=on, 0=off): usb_set_tether_enabled returned 0 (USB: success.)\n"


@cli_handler("usb_cli", "mode_switch")
def _usb_mode(store, answers, args):
    st = S(store)
    st["usb_mode"] = _int(answers[0])
    render_usb(store)
    return ("What is switch mode (0=EUM, 1=DBG, 2=ENT 3=P_ENT): What is reboot (0 | 1): "
            "usb_mode_switch returned 0 (USB: success.)\n")


@cli_handler("usb_cli", "ippt_settings")
def _usb_ippt(store, answers, args):
    st = S(store)
    st["rndis_disabled"] = _int(answers[0])
    return "What is rndis_disabled (1=on, 0=off): usb_control_ippt_settings returned 0 (USB: success.)\n"


@cli_handler("dsm_cli", "set_default_usb_mode")
def _dsm_usb(store, answers, args):
    st = S(store)
    st["default_usb"] = _int((args or answers or ["0"])[0])
    render_usb(store)
    return "cmd_set_default_usb_mode returned 0 (DSM: success.)\n"


@cli_handler("ccm2_cli", "set_wifi_while_usbteth")
def _ccm_wifi_teth(store, answers, args):
    st = S(store)
    st["wifi_teth"] = _int(answers[0])
    render_usb(store)
    return "What is enabled status [0|1]:  ccm_set_wifi_while_usbteth returned 0 (CCM: success.)\n"


@cli_handler("ccm2_cli", "set_static_ip")
def _ccm_static_ip(store, answers, args):
    st = S(store)
    st["static_ip"] = answers[0]
    render_usb(store)
    return "What is ip address [x.x.x.x]:  ccm_set_static_ip returned 0 (CCM: success.)\n"


@cli_handler("ccm2_cli", "set_ntp_url")
def _ccm_ntp(store, answers, args):
    st = S(store)
    st["ntp"] = [_int(answers[0], 604800), _int(answers[1], 60), answers[2], answers[3], answers[4]]
    render_ccm(store)
    return ("What is ntp min delay (seconds):  What is ntp retry count:  What is ntp url1:  \nWhat is ntp url2:  "
            "\nWhat is ntp url3:  ccm_set_ntp_url returned 0 (CCM: success.)\n")


@cli_handler("ccm2_cli", "set_wan_prio")
def _ccm_prio(store, answers, args):
    st = S(store)
    st["ccm_prio"] = [_int(a, 1) for a in answers[:3]]
    render_ccm(store)
    return ("Index:[1] Wan:[WWAN]\nIndex:[2] Wan:[WiFi WAN]\nIndex:[3] Wan:[Ethernet WAN]\n"
            "What is WAN Priority 1 Index:  What is WAN Priority 2 Index:  What is WAN Priority 3 Index:  "
            "ccm_set_wan_prio returned 0 (CCM: success.)\n")


@cli_handler("ccm2_cli", "wan_connect")
def _ccm_connect(store, answers, args):
    S(store)["wan_connected"] = True
    return "ccm_wan_connect returned 0 (CCM: success.)\n"


@cli_handler("ccm2_cli", "wan_disconnect")
def _ccm_disconnect(store, answers, args):
    S(store)["wan_connected"] = False
    return "ccm_wan_disconnect returned 0 (CCM: success.)\n"


# The fixtures were captured without a SIM ("No WAN"); the mock unit is connected (live_status.py),
# so these reads follow S()["wan_connected"].  Same shape as fixtures/cli/ccm2_cli/*.txt.
def _wan_block(name, v4, v4if, v6, v6if):
    return "Wan:[%s]\nV4:[%s]\nV4 IF:[%s]\nV6:[%s]\nV6 IF:[%s]\n" % (name, v4, v4if, v6, v6if)


@cli_handler("ccm2_cli", "get_curr_wan")
def _ccm_curr_wan(store, answers, args):
    up = S(store)["wan_connected"]
    body = _wan_block("WWAN", "Connected", "rmnet_data0", "Connected", "rmnet_data0") if up else \
        _wan_block("No WAN", "NotAvailable", "", "NotAvailable", "")
    return "ccm_get_curr_wan returned 0 (CCM: success.)\n" + body


@cli_handler("ccm2_cli", "get_conn_state")
def _ccm_conn_state(store, answers, args):
    up = S(store)["wan_connected"]
    return ("ccm_get_conn_state returned 0 (CCM: success.)\n" +
            _wan_block("WWAN", "Connected" if up else "Waiting", "rmnet_data0" if up else "",
                       "Connected" if up else "Waiting", "rmnet_data0" if up else "") + "\n" +
            _wan_block("WiFi WAN", "NotAvailable", "", "NotAvailable", "") + "\n" +
            _wan_block("Ethernet WAN", "NotAvailable", "", "NotAvailable", "") + "\n")


@cli_handler("ccm2_cli", "get_curr_wan_stats")
def _ccm_wan_stats(store, answers, args):
    up = S(store)["wan_connected"]
    which = _int((args or answers or ["0"])[0])
    head = "Wan:[1=WWAN]\nWan:[2=WiFi WAN]\nWan:[3=Ethernet WAN]\nWhat is WAN:  "
    if which not in (1, 2, 3):
        return head + "ccm_get_curr_wan_stats returned 2 (CCM: one or more bad parameters passed into a function.)\n"
    if which == 1 and up:
        return (head + "ccm_get_curr_wan_stats returned 0 (CCM: success.)\nWan:[WWAN]\n"
                "V4 Connection State:[Connected]\nV4 Bytes Rx:[1250000000]\nV4 Bytes Tx:[96000000]\nV4 Duration:[5400]\n"
                "V4 ifc:[rmnet_data0]\nV4 IP Addr:[100.64.12.34]\nV4 Subnet:[255.255.255.0]\nV4 Gateway:[100.64.12.1]\n"
                "V6 Connection State:[Connected]\nV6 Bytes Rx:[25000000]\nV6 Bytes Tx:[3000000]\nV6 Duration:[5400]\n"
                "V6 ifc:[rmnet_data0]\nV6 IP Addr:[2001:db8:1234:5678::1]\nV6 Gateway:[fe80::1]\n")
    return (head + "ccm_get_curr_wan_stats returned 0 (CCM: success.)\nWan:[%s]\n" % WAN_LABEL[which] +
            "V4 Connection State:[NotAvailable]\nV4 Bytes Rx:[0]\nV4 Bytes Tx:[0]\nV4 Duration:[0]\nV4 ifc:[]\n"
            "V4 IP Addr:[]\nV4 Subnet:[]\nV4 Gateway:[]\nV6 Connection State:[NotAvailable]\nV6 Bytes Rx:[0]\n"
            "V6 Bytes Tx:[0]\nV6 Duration:[0]\nV6 ifc:[]\nV6 IP Addr:[]\nV6 Gateway:[]\n")


# ------------------------------------------------------------------ sysintcli (no captures exist: shapes from .rodata)
SYS_HEAD = "Starting sysint client library\n"
SYS_TAIL = "Stopping sysint client library 0\nsysinterfaceShutdown complete 0\n"


@cli_handler("sysintcli", "getReservedIPList")
def _sys_get_res(store, answers, args):
    rows = S(store)["reservations"]
    out = SYS_HEAD
    if rows:
        out += "sys_getDHCPReservedAddrList returned 7, count:[%d]\n" % len(rows)
    out += "sys_getDHCPReservedAddrList returned 0, count:[%d]\n" % len(rows)
    for i, r in enumerate(rows):
        out += ("list[%d].type:[%d]\nlist[%d].mac:[%s]\nlist[%d].ip:[%s]\nlist[%d].host_name:[%s]\n" %
                (i, r["type"], i, r["mac"], i, r["ip"], i, r["name"]))
    return out + SYS_TAIL


@cli_handler("sysintcli", "setReservedIPList")
def _sys_set_res(store, answers, args):
    st = S(store)
    n = _int(answers[0])
    for i in range(n):
        name, rtype, mac, ip = answers[1 + i * 4:5 + i * 4]
        mac = mac.upper()
        rows = [r for r in st["reservations"] if r["mac"] != mac]
        if ip:
            rows.append({"name": name, "type": _int(rtype), "mac": mac, "ip": ip})
        st["reservations"] = rows
    return (SYS_HEAD + "Enter number of reserved IP addresses you want to set:Enter host name:"
            "Enter type (0 - 10 (see sys_device_type_e sys_interface_types.h) :Enter mac (xx:xx:xx:xx:xx:xx):"
            "Enter reserved ip address (x.x.x.x):sys_setDHCPReservedAddrList returned 0\n" + SYS_TAIL)


@cli_handler("sysintcli", "getLanMTU")
def _sys_get_mtu(store, answers, args):
    return SYS_HEAD + "sys_getLanMTU returned 0\nmtu:[%d]\n" % S(store)["mtu"] + SYS_TAIL


@cli_handler("sysintcli", "setLanMTU")
def _sys_set_mtu(store, answers, args):
    S(store)["mtu"] = _int(answers[0], 1500)
    return SYS_HEAD + "Enter MTU: sys_setLanMTU returned 0\n" + SYS_TAIL


@cli_handler("sysintcli", "getGatewaySettings")
def _sys_get_gw(store, answers, args):
    g = S(store)["gateway"]
    return (SYS_HEAD + "sys_getGatewaysettings returned 0\nsettings.mode:[%d]\nsettings.gateway_address:[%s]\n"
            "settings.netmask:[%s]\n" % (g[0], g[1], g[2]) + SYS_TAIL)


@cli_handler("sysintcli", "setGatewaySettings")
def _sys_set_gw(store, answers, args):
    st = S(store)
    mode = _int(answers[0])
    st["gateway"] = [mode, answers[1] if mode == 2 else "", answers[2] if mode == 2 else ""]
    return SYS_HEAD + "sys_getGatewaysettings returned 0\n" + SYS_TAIL


# ------------------------------------------------------------------ REST
@rest_handler("GET", "RouterSettings")
def _router_settings(rest, body):
    """The redacted REST capture replaced the router address with 192.168.1.100 (inside the DHCP range);
    the CLI capture shows the real 192.168.1.1.  Correct it once so the mock LAN validates."""
    d = rest.data.get("RouterSettings")
    if not isinstance(d, dict):     # tests remove the resource to exercise the CLI fallback
        raise ApiError(503, "unsupported", "Vendor REST GET RouterSettings: unsupported (mock)",
                       {"vendor_status": 404, "resource": "RouterSettings"})
    if not rest.data.get("_network_seeded"):
        rest.data["_network_seeded"] = True
        if d.get("RouterIPv4Address") == "192.168.1.100":
            d["RouterIPv4Address"] = "192.168.1.1"
    return copy.deepcopy(d)
