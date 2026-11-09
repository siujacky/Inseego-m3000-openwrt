"""Router-network area: LAN/DHCP, names, reservations, IP passthrough, DNS, Ethernet, WAN, USB,
static routes, time.  Parsers run against the real captures in fixtures/cli; setters are checked
against their Spec (answer count and positional values, reset prompt always answered "N")."""
import glob
import os
import re
import unittest

from tests.helpers import FIXTURES, ROOT, MockClient, read_fixture

from m3100ui.adapters import cli_specs
from m3100ui.adapters.cli import CliResult
from m3100ui.adapters.specs import core as core_specs
from m3100ui.adapters.specs import network as net_specs
from m3100ui.errors import ApiError
from m3100ui.services import dns as dns_svc
from m3100ui.services import ethernet as eth_svc
from m3100ui.services import lan as lan_svc
from m3100ui.services import routes as routes_svc
from m3100ui.services import timesvc
from m3100ui.services import usb as usb_svc
from m3100ui.services import wan as wan_svc


def fx(cli, cmd):
    return CliResult(cli, cmd, read_fixture(cli, cmd), 0)


class Base(unittest.TestCase):
    def setUp(self):
        self.c = MockClient()
        self.calls = self.c.app.cli.store.calls

    def tearDown(self):
        self.c.cleanup()

    def setter_calls(self, cli, cmd):
        return [x for x in self.calls if x["cli"] == cli and x["cmd"] == cmd]

    def last(self, cli, cmd):
        got = self.setter_calls(cli, cmd)
        self.assertTrue(got, "no %s %s call" % (cli, cmd))
        return got[-1]

    def assert_matches_spec(self, call):
        sp = cli_specs.spec(call["cli"], call["cmd"])
        self.assertIsNotNone(sp, "no spec for %s %s" % (call["cli"], call["cmd"]))
        values = call["args"] if sp.mode == "argv" else call["answers"]
        self.assertEqual(len(values), sp.expected_count(values), call)
        self.assertNotIn(net_specs.RESET_PROMPT, sp.prompts, "the conditional reset prompt is never answered")


# ---------------------------------------------------------------------------------------------- parsers
class FixtureParsing(unittest.TestCase):
    def test_lan_cli(self):
        d = lan_svc.parse_lan_cli(fx("mifi_qcmap_cli", "get_lan_dhcp_settings"))
        self.assertEqual(d["ip"], "192.168.1.1")
        self.assertEqual(d["subnet_mask"], "255.255.255.0")
        self.assertEqual(d["mask_format"], 0)
        self.assertFalse(d["dhcp_enabled"])
        self.assertEqual((d["dhcp_start"], d["dhcp_end"]), ("192.168.1.2", "192.168.1.102"))
        self.assertEqual(d["dhcp_lease_s"], 86400)
        self.assertTrue(d["link_local"].startswith("fe80::"))

    def test_ippt(self):
        d = lan_svc.parse_ippt(fx("mifi_qcmap_cli", "get_ippt"))
        self.assertEqual(d, {"enabled": False, "interface_code": 2, "interface": "ethernet", "client": ""})
        d = lan_svc.parse_ippt(CliResult("mifi_qcmap_cli", "get_ippt", "cmd_router2_get_ippt returned 0 (MIFI_QCMAP: success.)\n"
                                                                       "Is active:       [1]\nInterface:       [1]\nClient Identity (my-pc)\n", 0))
        self.assertEqual((d["enabled"], d["interface"], d["client"]), (True, "usb", "my-pc"))

    def test_dns(self):
        d = dns_svc.parse_dns_filter(fx("mifi_qcmap_cli", "get_dns_filter"))
        self.assertEqual(d, {"enabled": False, "mode": "allow", "domains": [], "last_operation": "deleted"})
        self.assertEqual(dns_svc.parse_active_dns(fx("mifi_qcmap_cli", "get_active_dns")), {"v4": [], "v6": []})
        r = fx("mifi_qcmap_cli", "get_dns_rebinding_status")
        self.assertEqual(r.get_int("DNS Rebinding Protection Status"), 0)
        r = fx("mifi_qcmap_cli", "get_manual_dns")
        self.assertEqual(r.get("Manual DNS status is"), "Disabled")

    def test_names(self):
        self.assertEqual(fx("mifi_qcmap_cli", "get_device_name").get("Device name"), "Inseego")
        self.assertEqual(fx("mifi_qcmap_cli", "get_dns_name").get("Host DNS name"), "my.jetpack")

    def test_ethernet(self):
        d = eth_svc.parse_ethernet_info(read_fixture("mifi_qcmap_cli", "get_ethernet_info"))
        self.assertEqual(d["status"], 1)
        self.assertEqual(d["port_count"], 1)
        self.assertEqual(d["ports"], [{"name": "eth0", "type_code": 2, "role": "lan", "link": True, "mac": "02:00:00:00:00:01"}])
        self.assertEqual(fx("mifi_qcmap_cli", "get_iface_type").get("Ethernet (eth0) interface type is"), "LAN")
        self.assertFalse(fx("mifi_qcmap_cli", "get_iface1_link").ok)        # unsupported on this model

    def test_wan(self):
        blocks = wan_svc.parse_conn_blocks(fx("ccm2_cli", "get_conn_state"))
        self.assertEqual([b["wan"] for b in blocks], ["wwan", "wifi", "ethernet"])
        self.assertEqual(blocks[0]["v4"], "Waiting")
        self.assertFalse(any(b["connected"] for b in blocks))
        cur = wan_svc.parse_conn_blocks(fx("ccm2_cli", "get_curr_wan"))
        self.assertEqual(cur[0]["wan"], "none")
        st = wan_svc.parse_wan_stats(fx("ccm2_cli", "get_curr_wan_stats"))      # captured with EOF: rc 2
        self.assertFalse(st["ok"])
        self.assertEqual(st["wan"], "none")
        self.assertEqual(st["v4"]["state"], "NotAvailable")
        m = wan_svc.parse_wan_mgr(fx("wan_mgr_cli", "get_all_wan_info"), fx("wan_mgr_cli", "get_wan_failback"))
        self.assertEqual(m["supported_count"], 2)
        self.assertTrue(m["auto_switch"])
        self.assertEqual(m["manual_wan"], "wwan")
        self.assertEqual(m["priorities"], [{"wan": "wwan", "priority": 1}, {"wan": "ethernet", "priority": 2}])
        self.assertEqual((m["failback_enabled"], m["failback_min"], m["data_check_min"], m["data_limit_kb"]), (True, 120, 1, 10))
        self.assertEqual((m["monitor_enabled"], m["monitor_client_state"], m["retry_count"]), (True, False, 3))
        self.assertEqual((m["routed_interval_s"], m["online_interval_min"], m["failed_interval_s"]), (30, 3, 10))
        self.assertEqual(m["dns_urls"][0], "www.google.com")
        self.assertEqual(wan_svc.parse_qcmap_prio(fx("mifi_qcmap_cli", "wan_get_prio")), ["wwan", "ethernet"])
        self.assertEqual(wan_svc.parse_ccm_prio(fx("ccm2_cli", "get_wan_prio")), ["ethernet", "wifi", "wwan"])
        self.assertEqual(fx("wan_mgr_cli", "get_waiting_timeout").get_int("The waiting timeout value is"), 30)
        self.assertEqual(fx("wan_mgr_cli", "get_self_recovery_status").get_int("The Self Recovery Status is"), 1)
        self.assertFalse(fx("wan_mgr_cli", "is_running").ok)

    def test_usb(self):
        s = usb_svc.parse_usb_state(fx("usb_cli", "get_state"))
        self.assertEqual((s["default_mode_code"], s["default_mode_label"]), (0, "Debug"))
        self.assertEqual(s["functions"], ["rndis", "dun", "diag", "adb", "hid"])
        self.assertEqual(s["net_if"], "rndis0")
        self.assertTrue(s["configured"])
        c = usb_svc.parse_usb_config(fx("usb_cli", "get_config"))
        self.assertEqual(c["standard"], ["mass_storage"])
        self.assertIn("adb", c["debug"])
        self.assertEqual(c["network_mode"], "AUTO")
        self.assertEqual(fx("dsm_cli", "get_default_usb_mode").get_int("USB mode"), 0)
        self.assertEqual(fx("ccm2_cli", "get_static_ip").get("ip"), "0.0.0.0")

    def test_time(self):
        n = timesvc.parse_ntp(fx("ccm2_cli", "get_ntp_url"))
        self.assertEqual(n, {"servers": ["time.google.com", "pool.ntp.org", "time.nist.gov"], "min_delay_s": 604800, "retries": 60})
        t = timesvc.parse_network_time(fx("modem2_cli", "get_network_time"))
        self.assertEqual((t["available"], t["timestamp"], t["tz_offset_min"], t["dst"]), (True, 1766091681, 0, False))

    def test_routes(self):
        self.assertEqual(routes_svc.parse_route_list(read_fixture("mifi_qcmap_cli", "get_static_route_list")), [])
        txt = ("cmd_get_static_route_list returned 0 (MIFI_QCMAP: success.)\n\n Static Route List... \n"
               "RouteType[IPV4] Network[10.0.0.0/8] Gateway[192.168.1.254] InterfaceType[Ethernet WAN] Metric[5]\n"
               "RouteType[IPV6] Network[2001:db8::/32] Gateway[fe80::1] InterfaceType[WGVPN_0] Metric[1]\n")
        rows = routes_svc.parse_route_list(txt)
        self.assertEqual([(r["family"], r["interface"], r["metric"]) for r in rows], [(4, "ethernet", 5), (6, "wireguard1", 1)])
        self.assertEqual(len({r["id"] for r in rows}), 2)
        self.assertEqual(fx("mifi_qcmap_cli", "get_static_routes").get("Static Routes Feature is"), "Disable")
        k = routes_svc.parse_ip_route("default via 10.0.0.1 dev rmnet_data0 proto static metric 100\n"
                                      "unreachable 10.9.0.0/16 metric 5\n")
        self.assertEqual((k[0]["dest"], k[0]["via"], k[0]["dev"], k[0]["metric"]), ("default", "10.0.0.1", "rmnet_data0", "100"))
        self.assertEqual((k[1]["type"], k[1]["dest"]), ("unreachable", "10.9.0.0/16"))

    def test_sysintcli_shapes(self):
        out = ("Starting sysint client library\nsys_getDHCPReservedAddrList returned 7, count:[1]\n"
               "sys_getDHCPReservedAddrList returned 0, count:[1]\nlist[0].type:[3]\nlist[0].mac:[02:aa:00:00:00:01]\n"
               "list[0].ip:[192.168.1.50]\nlist[0].host_name:[nas]\nStopping sysint client library 0\n")
        self.assertEqual(lan_svc.parse_reservations(out), [{"mac": "02:AA:00:00:00:01", "ip": "192.168.1.50", "name": "nas", "type": 3}])
        self.assertEqual(lan_svc.sysint_rc(out), 0)
        self.assertEqual(lan_svc.sysint_rc("sys_setLanMTU returned 2\n"), 2)
        g = lan_svc.parse_gateway("sys_getGatewaysettings returned 0\nsettings.mode:[2]\nsettings.gateway_address:[10.0.0.1]\n"
                                  "settings.netmask:[255.255.255.0]\n")
        self.assertEqual((g["mode"], g["address"], g["netmask"]), ("manual", "10.0.0.1", "255.255.255.0"))

    def test_sysintcli_device_captures(self):
        def cap(name):
            with open(os.path.join(FIXTURES, "cli-param", name), encoding="utf-8") as f:
                return f.read()
        g = lan_svc.parse_gateway(cap("sysintcli.getGatewaySettings.txt"))
        self.assertEqual((g["mode"], g["address"], g["netmask"]), ("default", "", ""))
        self.assertEqual(lan_svc.sysint_rc(cap("sysintcli.getLanMTU.txt")), 0)
        self.assertIn("mtu:[1428]", cap("sysintcli.getLanMTU.txt"))
        txt = cap("sysintcli.getReservedIPList.txt")
        self.assertEqual((lan_svc.sysint_rc(txt), lan_svc.parse_reservations(txt)), (0, []))
        with open(os.path.join(FIXTURES, "sandbox", "sysintcli", "getLanMTU.txt"), encoding="utf-8") as f:
            self.assertEqual(lan_svc.sysint_rc(f.read()), 10)        # sandbox: no daemon -> error code

    def test_every_network_fixture_parses(self):
        for cli in ("mifi_qcmap_cli", "wan_mgr_cli", "usb_cli", "ccm2_cli", "dsm_cli"):
            for f in glob.glob(os.path.join(FIXTURES, "cli", cli, "*.txt")):
                with open(f, encoding="utf-8") as fh:
                    r = CliResult(cli, os.path.basename(f)[:-4], fh.read(), 0)
                wan_svc.parse_conn_blocks(r)
                lan_svc.parse_ippt(r)
                dns_svc.parse_dns_filter(r)
                eth_svc.parse_ethernet_info(r.text)

    def test_mask_parsing(self):
        self.assertEqual(lan_svc.parse_mask("m", "/24"), "255.255.255.0")
        self.assertEqual(lan_svc.parse_mask("m", "16"), "255.255.0.0")
        self.assertEqual(lan_svc.parse_mask("m", "255.255.255.128"), "255.255.255.128")
        for bad in ("/31", "/7", "255.0.255.0", "abc"):
            with self.assertRaises(ApiError):
                lan_svc.parse_mask("m", bad)


# ---------------------------------------------------------------------------------------------- specs
class SpecTests(unittest.TestCase):
    def test_specs_sane(self):
        merged = cli_specs.all_specs()
        self.assertFalse(set(net_specs.SPECS) & set(core_specs.SPECS), "redefines a core spec")
        for key, sp in net_specs.SPECS.items():
            self.assertEqual(cli_specs.classify(*key), "setter", key)
            self.assertIn(key, merged, "%s dropped (duplicate?)" % (key,))
            self.assertIn(sp.source, ("static", "sandbox"), key)
            self.assertIn(sp.tier, (1, 2, 3))
            self.assertTrue(sp.prompts or sp.mode == "argv", key)
            self.assertTrue(sp.evidence, key)
        self.assertFalse([k for k in cli_specs.SPEC_ERRORS if "network" in str(cli_specs.SPEC_ERRORS[k])])

    def test_sandbox_records_match(self):
        """fixtures/sandbox/verified.json (device sandbox run): prompts recorded there equal ours."""
        import json
        with open(os.path.join(FIXTURES, "sandbox", "verified.json"), encoding="utf-8") as f:
            recs = {(r["cli"], r["cmd"]): r for r in json.load(f)}
        verified = [k for k in net_specs.SPECS if k in recs]
        self.assertGreaterEqual(len(verified), 20)
        for key in verified:
            self.assertEqual(recs[key]["prompts"], net_specs.SPECS[key].prompts, key)
            self.assertEqual(cli_specs.spec(*key).source, "sandbox", key)
        for key in [("mifi_qcmap_cli", "set_dns_filter"), ("mifi_qcmap_cli", "set_static_route_list"),
                    ("sysintcli", "setReservedIPList"), ("usb_cli", "mode_switch"), ("dsm_cli", "set_default_usb_mode"),
                    ("wan_mgr_cli", "set_wan_monitor_properties"), ("ccm2_cli", "set_ntp_url")]:
            self.assertIn(key, verified)

    def test_tiers(self):
        for key in [("mifi_qcmap_cli", "set_ippt"), ("mifi_qcmap_cli", "set_ippt_feature_mode"), ("usb_cli", "mode_switch"),
                    ("usb_cli", "ippt_settings"), ("dsm_cli", "set_default_usb_mode"), ("sysintcli", "setGatewaySettings")]:
            self.assertEqual(net_specs.SPECS[key].tier, 2, key)

    def test_record_spec(self):
        sp = net_specs.SPECS[("mifi_qcmap_cli", "set_static_route_list")]
        self.assertEqual(sp.expected_count(["0"]), 1)                              # empty list clears every route
        self.assertEqual(sp.expected_count(["2"] + ["x"] * 10), 11)
        with self.assertRaises(ValueError):
            sp.expected_count(["26"])
        with self.assertRaises(ApiError):
            sp.check(["2", "4", "10.0.0.0/8", "1.1.1.1", "1", "0"])                # one record short
        rs = net_specs.SPECS[("sysintcli", "setReservedIPList")]
        self.assertEqual(rs.expected_count(["1", "a", "0", "m", "i"]), 5)
        with self.assertRaises(ValueError):
            rs.expected_count(["0"])                                                 # never an empty list

    def test_excluded_neighbours_stay_excluded(self):
        for cli, cmd in [("ccm2_cli", "set_wifi_while_usbteth_temp"), ("usb_cli", "set_tether_enabled_temp"),
                         ("mifi_qcmap_cli", "setup_cust_apn_routing"), ("ccm2_cli", "set_network_time"),
                         ("wan_mgr_cli", "register_wan"), ("dmdb_cli", "get_reserved_ip_list")]:
            self.assertEqual(cli_specs.classify(cli, cmd), "excluded", (cli, cmd))

    def test_static_specs_refused_on_a_real_unit(self):
        c = MockClient()
        sp = cli_specs.spec("mifi_qcmap_cli", "set_dns_rebinding_status")
        old = sp.source
        try:
            c.app.cli.allow_static_specs = False          # what a real unit does
            sp.source = "static"                          # as if the sandbox had not confirmed the prompts
            r = c.put("/api/dns/rebinding", {"enabled": True}, expect=403)
            self.assertEqual(r["error"]["code"], "unverified_setter")
            sp.source = "sandbox"
            c.put("/api/dns/rebinding", {"enabled": True})
        finally:
            sp.source = old
            c.cleanup()

    def test_sample_answers_fit_specs(self):
        for key, sp in net_specs.SPECS.items():
            vals = sp.sample_answers()
            self.assertEqual(len(vals), sp.expected_count(vals), key)

    def test_sysintcli_reads_need_no_spec(self):
        for cmd in ("getReservedIPList", "getLanMTU", "getGatewaySettings"):
            self.assertEqual(cli_specs.classify("sysintcli", cmd), "get")
            self.assertNotIn(("sysintcli", cmd), net_specs.SPECS)


# ---------------------------------------------------------------------------------------------- routes
class ReadRoutes(Base):
    def test_all_reads(self):
        expect = {
            "/api/lan": ["ip", "subnet_mask", "prefix_length", "dhcp_enabled", "dhcp_start", "dhcp_end", "dhcp_lease_min",
                         "ipv6_enabled", "ipv6_transparent", "mask_format", "link_local", "client_on_lan"],
            "/api/lan/names": ["device_name", "dns_name", "local_url"], "/api/lan/advanced": ["mtu", "mask_format"],
            "/api/lan/ippt": ["enabled", "interface", "client", "feature_mode", "gateway", "ethernet_wan_active", "stock"],
            "/api/dns/manual": ["enabled", "primary", "secondary"], "/api/dns/active": ["v4", "v6", "mobile_v4"],
            "/api/dns/rebinding": ["enabled"], "/api/dns/filter": ["enabled", "mode", "domains"],
            "/api/ethernet": ["enabled", "role", "link", "mac", "kernel", "eth1"],
            "/api/wan": ["current", "connections", "manager", "routing_priority", "cm_priority"],
            "/api/usb": ["tethering_enabled", "wifi_while_tethered", "default_mode", "state", "compositions", "static_ip"],
            "/api/routes": ["enabled", "routes", "max", "interfaces"], "/api/routes/table": ["v4", "v6", "raw_v4"],
            "/api/time": ["now", "utc", "timezone", "ntp", "network_time", "sync"],
        }
        for path, keys in expect.items():
            d = self.c.get(path)
            for k in keys:
                self.assertIn(k, d, "%s lacks %s" % (path, k))
        self.assertIsInstance(self.c.get("/api/lan/reservations"), list)
        self.assertIsInstance(self.c.get("/api/lan/leases"), list)
        self.assertIsInstance(self.c.get("/api/routes/static"), list)

    def test_values_on_fixtures(self):
        lan = self.c.get("/api/lan")
        self.assertEqual((lan["ip"], lan["prefix_length"], lan["dhcp_lease_min"]), ("192.168.1.1", 24, 1440))
        self.assertEqual(lan["source"], "rest")
        self.assertEqual(self.c.get("/api/lan/names")["device_name"], "Inseego")
        eth = self.c.get("/api/ethernet")
        self.assertEqual((eth["enabled"], eth["role"], eth["link"], eth["eth1"]), (True, "lan", True, None))
        w = self.c.get("/api/wan")
        self.assertFalse(w["manager"]["running"])
        self.assertIs(w["manager"]["stock_supported"], False)
        self.assertEqual(w["routing_priority"], ["wwan", "ethernet"])
        usb = self.c.get("/api/usb")
        self.assertEqual((usb["tethering_enabled"], usb["default_mode"], usb["static_ip"]), (True, "debug", ""))
        ip = self.c.get("/api/lan/ippt")
        self.assertIs(ip["stock"]["ippt_editable"], False)          # the carrier made it read-only
        self.assertIs(ip["stock"]["gateway_shown"], False)

    def test_stats_and_telemetry(self):
        s = self.c.get("/api/wan/stats", query={"wan": "wwan"})
        self.assertTrue(s["available"])
        self.assertEqual(s["v4"]["ip"], "100.64.12.34")
        self.c.get("/api/wan/stats", query={"wan": "bogus"}, expect=400)
        t = self.c.get("/api/wan/telemetry")                         # wan_mgrd is not running in the capture
        self.assertFalse(t["available"])
        self.assertIn("not running", t["message"])

    def test_unavailable(self):
        st = self.c.app.cli.store
        st.set_text("mifi_qcmap_cli", "get_ippt", "cmd_router2_get_ippt returned 5 (MIFI_QCMAP: process not running.)\n")
        self.assertEqual(self.c.get("/api/lan/ippt", expect=503)["error"]["code"], "unavailable")
        st.set_text("mifi_qcmap_cli", "get_dns_filter", "cmd_get_dns_filter_settings DNS Filter: returned 5 (MIFI_QCMAP: process not running.)\n")
        self.c.get("/api/dns/filter", expect=503)
        st.set_text("mifi_qcmap_cli", "get_static_routes", "cmd_get_static_routes_status returned 5 (MIFI_QCMAP: process not running.)\n")
        self.c.get("/api/routes", expect=503)

    def test_features_and_pages(self):
        feats = self.c.get("/api/meta/full")["features"]
        for f in ("network.ippt", "network.ethernet", "network.usb"):
            self.assertTrue(feats.get(f), f)
        pages = self.c.get("/api/ui/pages")
        for p in ("lan", "dns", "ippt", "ethernet", "wan", "usb", "routes", "time"):
            self.assertIn("/app/pages/network/%s.js" % p, pages)
        self.assertNotIn("/app/pages/network/_net.js", pages)


class LanWrites(Base):
    def test_lan_rest_write_and_validation(self):
        d = self.c.put("/api/lan", {"dhcp_enabled": True, "dhcp_lease_min": 720, "subnet_mask": "/24"})
        self.assertEqual(self.c.app.rest.calls[-1]["body"], {"DHCPEnabled": True, "DHCPLeaseTime": 43200})
        self.assertTrue(d["dhcp_enabled"])
        self.assertEqual(d["dhcp_lease_min"], 720)
        bad = [({"dhcp_start": "192.168.1.1"}, "dhcp_start"),                  # router inside the range
               ({"dhcp_end": "192.168.1.1"}, "dhcp_end"),                       # end < start
               ({"ip": "10.0.0.1"}, "dhcp_start"),                              # range outside the new subnet
               ({"subnet_mask": "/31"}, "subnet_mask"), ({"ip": "192.168.1.0"}, "ip"), ({"ip": "300.1.1.1"}, "ip"),
               ({"dhcp_lease_min": 0}, "dhcp_lease_min"), ({"bogus": 1}, "bogus")]
        for body, field in bad:
            r = self.c.put("/api/lan", body, expect=400)
            self.assertEqual(r["error"]["details"]["field"], field, body)

    def test_lan_move(self):
        d = self.c.put("/api/lan", {"ip": "10.10.0.1", "subnet_mask": "255.255.255.0", "dhcp_enabled": True,
                                    "dhcp_start": "10.10.0.100", "dhcp_end": "10.10.0.200"})
        self.assertTrue(d["address_changed"])
        self.assertEqual(d["network"], "10.10.0.0/24")

    def test_lan_cli_fallback_uses_core_spec(self):
        del self.c.app.rest.data["RouterSettings"]
        self.assertEqual(self.c.get("/api/lan")["source"], "cli")
        self.c.put("/api/lan", {"dhcp_enabled": True, "dhcp_lease_min": 60})
        call = self.last("mifi_qcmap_cli", "set_lan_dhcp_settings")
        self.assertEqual(call["answers"], ["192.168.1.1", "255.255.255.0", "0", "1", "192.168.1.2", "192.168.1.102", "3600"])
        self.assertEqual(len(call["answers"]), len(core_specs.SPECS[("mifi_qcmap_cli", "set_lan_dhcp_settings")].prompts))
        self.c.put("/api/lan", {"ipv6_enabled": False}, expect=503)

    def test_names(self):
        d = self.c.put("/api/lan/names", {"device_name": "router1", "dns_name": "router.home"})
        self.assertEqual((d["device_name"], d["dns_name"]), ("router1", "router.home"))
        self.assertTrue(d["restart_required"])
        self.assertEqual(self.last("mifi_qcmap_cli", "set_device_name")["answers"], ["router1"])
        self.assertEqual(self.last("mifi_qcmap_cli", "set_dns_name")["answers"], ["router.home"])
        for body in ({"device_name": "bad name"}, {"device_name": "a.b"}, {"dns_name": "1.2.3.4"}, {"dns_name": "-x.home"}):
            self.c.put("/api/lan/names", body, expect=400)

    def test_advanced(self):
        r = self.c.put("/api/lan/advanced", {"mask_format": "bits"}, expect=400)       # DHCP off: would wipe the range
        self.assertEqual(r["error"]["details"]["field"], "mask_format")
        self.c.put("/api/lan/advanced", {"mtu": 100}, expect=400)
        self.c.put("/api/lan", {"dhcp_enabled": True})
        d = self.c.put("/api/lan/advanced", {"mtu": 1400, "mask_format": "bits"})
        self.assertEqual((d["mtu"], d["mask_format"]), (1400, "bits"))
        self.assertEqual(self.last("sysintcli", "setLanMTU")["answers"], ["1400"])
        call = self.last("mifi_qcmap_cli", "set_lan_dhcp_settings")
        self.assertEqual(call["answers"][2:4], ["1", "1"])

    def test_reservations(self):
        r = self.c.post("/api/lan/reservations", {"name": "nas", "mac": "02:00:00:00:00:01", "ip": "192.168.1.50"}, expect=400)
        self.assertIn("DHCP", r["error"]["message"])
        self.c.put("/api/lan", {"dhcp_enabled": True})
        d = self.c.post("/api/lan/reservations", {"name": "nas", "mac": "02-00-00-00-00-01", "ip": "192.168.1.50"})
        self.assertEqual(d["reservations"], [{"mac": "02:00:00:00:00:01", "ip": "192.168.1.50", "name": "nas", "type": 0}])
        self.assertTrue(d["restart_required"])
        call = self.last("sysintcli", "setReservedIPList")
        self.assertEqual(call["answers"], ["1", "nas", "0", "02:00:00:00:00:01", "192.168.1.50"])
        self.assert_matches_spec(call)
        for body, field in [({"name": "x", "mac": "02:00:00:00:00:02", "ip": "192.168.1.50"}, "ip"),     # taken
                            ({"name": "x", "mac": "02:00:00:00:00:02", "ip": "192.168.1.200"}, "ip"),    # outside range
                            ({"name": "nas", "mac": "02:00:00:00:00:02", "ip": "192.168.1.51"}, "name"),  # duplicate name
                            ({"name": "y", "mac": "02:00:00:00:00:01", "ip": "192.168.1.52"}, "mac"),    # MAC reserved
                            ({"name": "y", "mac": "nope", "ip": "192.168.1.52"}, "mac")]:
            r = self.c.post("/api/lan/reservations", body, expect=400)
            self.assertEqual(r["error"]["details"]["field"], field, body)
        d = self.c.put("/api/lan/reservations/02:00:00:00:00:01", {"ip": "192.168.1.60", "name": "nas2"})
        self.assertEqual(d["reservations"][0]["ip"], "192.168.1.60")
        self.assertTrue(any(x["reserved"] for x in self.c.get("/api/lan/leases")))
        d = self.c.delete("/api/lan/reservations/02:00:00:00:00:01")
        self.assertEqual(d["reservations"], [])
        self.assertEqual(self.last("sysintcli", "setReservedIPList")["answers"][-1], "")
        self.c.delete("/api/lan/reservations/02:00:00:00:00:09", expect=404)

    def test_reservation_limit(self):
        self.c.put("/api/lan", {"dhcp_enabled": True})
        for i in range(10):
            self.c.post("/api/lan/reservations", {"name": "d%d" % i, "mac": "02:00:00:00:01:%02X" % i, "ip": "192.168.1.%d" % (10 + i)})
        r = self.c.post("/api/lan/reservations", {"name": "d10", "mac": "02:00:00:00:01:10", "ip": "192.168.1.30"}, expect=400)
        self.assertIn("10", r["error"]["message"])


class IpptWrites(Base):
    def test_tier2_and_answers(self):
        body = {"enabled": True, "interface": "ethernet", "client": "02-00-00-00-00-09"}
        r = self.c.put("/api/lan/ippt", body, expect=428)
        self.assertEqual(r["error"]["details"]["tier"], 2)
        self.assertFalse(self.setter_calls("mifi_qcmap_cli", "set_ippt"))
        d = self.c.put("/api/lan/ippt", self.c.confirm(body))
        self.assertEqual((d["enabled"], d["interface"], d["client"]), (True, "ethernet", "02:00:00:00:00:09"))
        self.assertTrue(d["restart_required"])                         # vendor rc 6 = reset required
        call = self.last("mifi_qcmap_cli", "set_ippt")
        self.assertEqual(call["answers"], ["1", "2", "02:00:00:00:00:09"])
        self.assert_matches_spec(call)
        self.assertEqual(self.c.app.state.last_snapshot("ippt"), {"enabled": False, "interface": "ethernet", "client": ""})
        d = self.c.post("/api/lan/ippt/restore", self.c.confirm({}))
        self.assertFalse(d["enabled"])
        self.c.post("/api/lan/ippt/restore", {}, expect=428)

    def test_usb_client_and_validation(self):
        d = self.c.put("/api/lan/ippt", self.c.confirm({"enabled": True, "interface": "usb", "client": "my-laptop"}))
        self.assertEqual((d["interface"], d["client"]), ("usb", "my-laptop"))
        self.assertEqual(self.last("mifi_qcmap_cli", "set_ippt")["answers"], ["1", "1", "my-laptop"])
        for body, field in [({"enabled": True, "interface": "usb", "client": "bad name!"}, "client"),
                            ({"enabled": True, "interface": "ethernet", "client": "my-laptop"}, "client"),
                            ({"enabled": True, "interface": "ethernet2", "client": "02:00:00:00:00:09"}, "interface"),
                            ({"gateway_mode": "manual", "gateway_address": "x", "gateway_netmask": "255.255.255.0"}, "gateway_address")]:
            r = self.c.put("/api/lan/ippt", self.c.confirm(body), expect=400)
            self.assertEqual(r["error"]["details"]["field"], field, body)

    def test_feature_mode_gateway_rndis(self):
        d = self.c.put("/api/lan/ippt", self.c.confirm({"feature_mode": True, "gateway_mode": "manual",
                                                         "gateway_address": "100.64.0.1", "gateway_netmask": "/24"}))
        self.assertTrue(d["feature_mode"])
        self.assertEqual(d["gateway"]["mode"], "manual")
        self.assertEqual(self.last("mifi_qcmap_cli", "set_ippt_feature_mode")["answers"], ["1"])
        call = self.last("sysintcli", "setGatewaySettings")
        self.assertEqual(call["answers"], ["2", "100.64.0.1", "255.255.255.0"])
        self.assert_matches_spec(call)
        self.c.post("/api/lan/ippt/usb-rndis", {"disabled": True}, expect=428)
        self.c.post("/api/lan/ippt/usb-rndis", self.c.confirm({"disabled": True}))
        self.assertEqual(self.last("usb_cli", "ippt_settings")["answers"], ["1"])

    def test_ethernet_wan_blocks_passthrough(self):
        self.c.put("/api/ethernet", {"role": "wan_dhcp"})
        r = self.c.put("/api/lan/ippt", self.c.confirm({"enabled": True, "interface": "usb", "client": "pc"}), expect=400)
        self.assertEqual(r["error"]["details"]["field"], "enabled")


class DnsWrites(Base):
    def test_manual(self):
        d = self.c.put("/api/dns/manual", {"enabled": True, "primary": "1.1.1.1", "secondary": "2606:4700:4700::1111"})
        self.assertEqual((d["enabled"], d["primary"]), (True, "1.1.1.1"))
        self.assertEqual(self.c.app.rest.calls[-1]["path"], "ManualDNSSettings")
        for body, field in [({"enabled": True, "primary": ""}, "primary"), ({"primary": "1.1.1.1", "secondary": "1.1.1.1"}, "secondary"),
                            ({"enabled": True, "primary": "0.0.0.0"}, "primary"), ({"primary": "dns.google"}, "primary")]:
            r = self.c.put("/api/dns/manual", body, expect=400)
            self.assertEqual(r["error"]["details"]["field"], field, body)

    def test_rebinding(self):
        d = self.c.put("/api/dns/rebinding", {"enabled": True})
        self.assertEqual(d, {"enabled": True, "restart_required": True})
        self.assert_matches_spec(self.last("mifi_qcmap_cli", "set_dns_rebinding_status"))
        self.assertTrue(self.c.get("/api/dns/rebinding")["enabled"])

    def test_filter(self):
        d = self.c.put("/api/dns/filter", {"enabled": True, "mode": "block", "domains": ["Ads.Example.com.", "*.tracker.net", "ads.example.com"]})
        self.assertEqual((d["enabled"], d["mode"], d["domains"]), (True, "block", ["ads.example.com", "tracker.net"]))
        call = self.last("mifi_qcmap_cli", "set_dns_filter")
        self.assertEqual(call["answers"], ["1", "1", "1", "ads.example.com,tracker.net"])
        self.assert_matches_spec(call)
        d = self.c.put("/api/dns/filter", {"mode": "allow", "enabled": False})
        self.assertEqual(self.last("mifi_qcmap_cli", "set_dns_filter")["answers"], ["1", "0", "0", "ads.example.com,tracker.net"])
        for body in ({"domains": ["evil.com;reboot"]}, {"domains": ["$(id).x"]}, {"domains": ["a b"]}, {"domains": "x.com,`id`"},
                     {"enabled": True, "domains": []}, {"mode": "both"}, {"domains": ["d%d.example.com" % i for i in range(51)]},
                     {"domains": ["%s.example.com" % ("a" * 60 + str(i)) for i in range(8)]}):
            self.c.put("/api/dns/filter", body, expect=400)
        d = self.c.delete("/api/dns/filter")
        self.assertEqual((d["domains"], d["last_operation"]), ([], "deleted"))
        self.assertEqual(self.last("mifi_qcmap_cli", "set_dns_filter")["answers"], ["0", "0", "0", ""])


class EthernetWrites(Base):
    def test_enable_and_role(self):
        d = self.c.put("/api/ethernet", {"enabled": False})
        self.assertFalse(d["enabled"])
        self.assertEqual(self.c.app.rest.calls[-1], {"method": "PUT", "path": "EthernetSettings/Enabled", "body": {"Enabled": 0}})
        d = self.c.put("/api/ethernet", {"role": "wan_dhcp"})
        self.assertEqual(d["role"], "wan_dhcp")
        self.assertTrue(d["restart_required"])
        call = self.last("mifi_qcmap_cli", "set_iface_type")
        self.assertEqual(call["answers"], ["3"])
        self.assert_matches_spec(call)
        self.c.put("/api/ethernet", {"role": "wan_pppoe"}, expect=400)          # the CLI cannot set PPPoE

    def test_role_blocked_by_passthrough(self):
        self.c.put("/api/lan/ippt", self.c.confirm({"enabled": True, "interface": "usb", "client": "pc"}))
        r = self.c.put("/api/ethernet", {"role": "wan_dhcp"}, expect=400)
        self.assertEqual(r["error"]["details"]["field"], "role")


class WanWrites(Base):
    def test_failover(self):
        d = self.c.put("/api/wan/failover", {"auto_switch": False, "manual_wan": "ethernet", "failback_min": 60,
                                             "retry_count": 5, "dns_urls": ["one.one.one.one", "dns.google", "9.9.9.9"],
                                             "self_recovery": False, "waiting_timeout_s": 45})
        self.assertEqual((d["auto_switch"], d["manual_wan"], d["failback_min"], d["retry_count"]), (False, "ethernet", 60, 5))
        self.assertEqual(d["dns_urls"], ["one.one.one.one", "dns.google", "9.9.9.9"])
        seq = [x["cmd"] for x in self.calls if x["cli"] == "wan_mgr_cli"]
        self.assertEqual(seq[0], "set_auto_switch")                      # switching off comes before the manual WAN
        self.assertLess(seq.index("set_auto_switch"), seq.index("set_manual_wan"))
        self.assertEqual(self.last("wan_mgr_cli", "set_manual_wan")["answers"], ["3"])
        self.assertEqual(self.last("wan_mgr_cli", "set_wan_failback")["answers"], ["1", "60", "1", "10"])
        mon = self.last("wan_mgr_cli", "set_wan_monitor_properties")
        self.assertEqual(mon["answers"], ["1", "0", "5", "30", "3", "10", "one.one.one.one", "dns.google", "9.9.9.9"])
        for name in ("set_auto_switch", "set_manual_wan", "set_wan_failback", "set_wan_monitor_properties",
                     "set_self_recovery_status", "set_waiting_timeout"):
            self.assert_matches_spec(self.last("wan_mgr_cli", name))
        d = self.c.put("/api/wan/failover", {"auto_switch": True})
        self.assertEqual(self.last("wan_mgr_cli", "set_auto_switch")["answers"], ["1"])

    def test_failover_validation(self):
        for body, field in [({"manual_wan": "ethernet"}, "manual_wan"),                 # auto switch still on
                            ({"auto_switch": False, "manual_wan": "usb"}, "manual_wan"),  # not a supported WAN
                            ({"routed_interval_s": 10}, "routed_interval_s"), ({"retry_count": 11}, "retry_count"),
                            ({"dns_urls": ["a.com", "b.com"]}, "dns_urls"), ({"dns_urls": ["a.com", "-b", "c.com"]}, "dns_urls"),
                            ({"data_limit_kb": 0}, "data_limit_kb"), ({"waiting_timeout_s": 0}, "waiting_timeout_s")]:
            r = self.c.put("/api/wan/failover", body, expect=400)
            self.assertEqual(r["error"]["details"]["field"], field, body)

    def test_priorities_and_connect(self):
        d = self.c.put("/api/wan/priority", {"order": ["ethernet", "wwan"]})
        self.assertEqual([p["wan"] for p in d["priorities"]], ["ethernet", "wwan"])
        self.assertTrue(d["restart_required"])
        call = self.last("wan_mgr_cli", "set_wan_priority_list")
        self.assertEqual(call["answers"], ["3", "1", "1", "2"])
        self.assert_matches_spec(call)
        self.c.put("/api/wan/priority", {"order": ["ethernet"]}, expect=400)
        d = self.c.put("/api/wan/routing-priority", {"first": "ethernet"})
        self.assertEqual(d["routing_priority"], ["ethernet", "wwan"])
        self.assert_matches_spec(self.last("mifi_qcmap_cli", "wan_set_prio"))
        self.assertEqual(self.last("mifi_qcmap_cli", "wan_set_prio")["answers"], ["3", "1"])
        d = self.c.put("/api/wan/cm-priority", {"order": ["wwan", "ethernet", "wifi"]})
        self.assertEqual(d["cm_priority"], ["wwan", "ethernet", "wifi"])
        self.assertEqual(self.last("ccm2_cli", "set_wan_prio")["answers"], ["1", "3", "2"])
        self.c.put("/api/wan/cm-priority", {"order": ["wwan", "wwan", "wifi"]}, expect=400)
        self.assertFalse(self.c.post("/api/wan/disconnect")["current"]["connected"])
        self.assertTrue(self.c.post("/api/wan/connect")["current"]["connected"])
        self.assert_matches_spec(self.last("ccm2_cli", "wan_connect"))

    def test_wan_manager_down_is_an_error(self):
        from m3100ui.adapters import mock as mock_mod
        key = ("wan_mgr_cli", "set_manual_wan")
        old = mock_mod.CLI_HANDLERS[key]
        # what the unit prints when wan_mgrd is not running (fixtures/sandbox/wan_mgr_cli/set_manual_wan.txt)
        mock_mod.CLI_HANDLERS[key] = lambda store, answers, args: "Failed to get wan priority settings.\n"
        try:
            r = self.c.put("/api/wan/failover", {"auto_switch": False, "manual_wan": "ethernet"}, expect=502)
            self.assertIn("Failed to get wan priority settings", r["error"]["message"])
        finally:
            mock_mod.CLI_HANDLERS[key] = old

    def test_priority_needs_two_wans(self):
        st = self.c.app.cli.store
        txt = read_fixture("wan_mgr_cli", "get_all_wan_info").replace("Suppoted WAN Count: [2]", "Suppoted WAN Count: [3]")
        st.set_text("wan_mgr_cli", "get_all_wan_info", txt)
        self.c.put("/api/wan/priority", {"order": ["ethernet", "wwan"]}, expect=503)
        self.assertFalse(self.setter_calls("wan_mgr_cli", "set_wan_priority_list"))


class UsbWrites(Base):
    def test_tethering(self):
        d = self.c.put("/api/usb", {"tethering_enabled": False, "wifi_while_tethered": False, "static_ip": "192.168.1.20"})
        self.assertEqual((d["tethering_enabled"], d["wifi_while_tethered"], d["static_ip"]), (False, False, "192.168.1.20"))
        self.assertEqual(self.last("usb_cli", "set_tether_enabled")["answers"], ["0"])
        self.assertEqual(self.last("ccm2_cli", "set_wifi_while_usbteth")["answers"], ["0"])
        self.assertEqual(self.last("ccm2_cli", "set_static_ip")["answers"], ["192.168.1.20"])
        self.c.put("/api/usb", {"static_ip": ""})
        self.assertEqual(self.last("ccm2_cli", "set_static_ip")["answers"], ["0.0.0.0"])
        for body in ({"static_ip": "224.0.0.1"}, {"static_ip": "x"}, {"tethering_enabled": "maybe"}):
            self.c.put("/api/usb", body, expect=400)

    def test_modes_tier2(self):
        self.c.put("/api/usb/default-mode", {"mode": "standard"}, expect=428)
        d = self.c.put("/api/usb/default-mode", self.c.confirm({"mode": "standard"}))
        self.assertEqual(d["default_mode"], "standard")
        call = self.last("dsm_cli", "set_default_usb_mode")
        self.assertEqual((call["args"], call["answers"]), (["1"], []))     # argv form, as the M2000 mod uses it
        self.assert_matches_spec(call)
        self.assertEqual(self.c.app.state.last_snapshot("usb_default_mode"), "debug")
        self.c.post("/api/usb/mode-switch", {"mode": "debug"}, expect=428)
        d = self.c.post("/api/usb/mode-switch", self.c.confirm({"mode": "debug"}))
        self.assertIn("adb", d["state"]["functions"])
        call = self.last("usb_cli", "mode_switch")
        self.assertEqual(call["answers"], ["1", "0"])                          # = (echo 1; echo 0) | usb_cli mode_switch
        self.assert_matches_spec(call)
        for mode in ("ent", "p_ent", "diag", "edl"):
            self.c.post("/api/usb/mode-switch", self.c.confirm({"mode": mode}), expect=400)


class RouteWrites(Base):
    def test_crud(self):
        self.assertTrue(self.c.put("/api/routes", {"enabled": True})["enabled"])
        rows = self.c.post("/api/routes/static", {"family": 4, "network": "10.1.2.3/8", "gateway": "192.168.1.254",
                                                  "interface": "ethernet", "metric": 5})
        self.assertEqual((rows[0]["network"], rows[0]["interface"]), ("10.0.0.0/8", "ethernet"))
        call = self.last("mifi_qcmap_cli", "set_static_route_list")
        self.assertEqual(call["answers"], ["1", "4", "10.0.0.0/8", "192.168.1.254", "3", "5"])
        self.assert_matches_spec(call)
        rows = self.c.post("/api/routes/static", {"family": "6", "network": "2001:db8::/32", "gateway": "fe80::1",
                                                  "interface": "wireguard1", "metric": 1})
        self.assertEqual(len(rows), 2)
        self.assert_matches_spec(self.last("mifi_qcmap_cli", "set_static_route_list"))
        rid = rows[0]["id"]
        rows = self.c.put("/api/routes/static/" + rid, dict(rows[0], metric=7))
        self.assertEqual(rows[0]["metric"], 7)
        self.c.put("/api/routes/static/" + rid, {"metric": 1}, expect=404)          # id changed with the content
        rows = self.c.delete("/api/routes/static/" + rows[1]["id"])
        self.assertEqual(len(rows), 1)
        rows = self.c.delete("/api/routes/static/" + rows[0]["id"])
        self.assertEqual(rows, [])
        self.assertEqual(self.last("mifi_qcmap_cli", "set_static_route_list")["answers"], ["0"])

    def test_validation(self):
        for body, field in [({"family": 6, "network": "10.0.0.0/8", "gateway": "fe80::1"}, "network"),
                            ({"family": 4, "network": "10.0.0.0/8", "gateway": "fe80::1"}, "gateway"),
                            ({"family": 4, "network": "0.0.0.0/0", "gateway": "1.1.1.1"}, "network"),
                            ({"family": 4, "network": "10.0.0.0/8", "gateway": "1.1.1.1", "interface": "lan"}, "interface"),
                            ({"family": 4, "network": "10.0.0.0/8", "gateway": "1.1.1.1", "metric": -1}, "metric"),
                            ({"family": 5, "network": "10.0.0.0/8", "gateway": "1.1.1.1"}, "family")]:
            r = self.c.post("/api/routes/static", body, expect=400)
            self.assertEqual(r["error"]["details"]["field"], field, body)
        self.c.post("/api/routes/static", {"network": "10.0.0.0/8", "gateway": "1.1.1.1"})
        self.c.post("/api/routes/static", {"network": "10.0.0.0/8", "gateway": "1.1.1.2"}, expect=400)   # same dest+metric

    def test_limit(self):
        for i in range(25):
            self.c.post("/api/routes/static", {"network": "10.%d.0.0/16" % i, "gateway": "192.168.1.254"})
        self.c.post("/api/routes/static", {"network": "10.99.0.0/16", "gateway": "192.168.1.254"}, expect=400)
        self.assert_matches_spec(self.last("mifi_qcmap_cli", "set_static_route_list"))

    def test_kernel_table(self):
        d = self.c.get("/api/routes/table")
        self.assertTrue(d["ok"])
        self.assertEqual(d["v4"][0]["dest"], "default")


class TimeWrites(Base):
    def test_ntp(self):
        d = self.c.put("/api/time/ntp", {"servers": ["pool.ntp.org", "162.159.200.1", ""], "min_delay_s": 86400, "retries": 10})
        self.assertEqual(d["ntp"]["servers"], ["pool.ntp.org", "162.159.200.1", ""])
        call = self.last("ccm2_cli", "set_ntp_url")
        self.assertEqual(call["answers"], ["86400", "10", "pool.ntp.org", "162.159.200.1", ""])
        self.assert_matches_spec(call)
        for body in ({"servers": ["-p", "", ""]}, {"servers": ["a;b", "", ""]}, {"servers": ["", "", ""]},
                     {"servers": ["pool.ntp.org"]}, {"min_delay_s": 1}, {"retries": -1}):
            self.c.put("/api/time/ntp", body, expect=400)

    def test_sync(self):
        seen = []
        real = self.c.app.system.run

        def run(argv, timeout=15.0, max_bytes=128 * 1024):
            seen.append(list(argv))
            return real(argv, timeout=timeout)
        self.c.app.system.run = run
        d = self.c.put("/api/time/sync", {"sync_at_boot": True, "interval_h": 12, "server": "time.google.com"})
        self.assertEqual((d["sync"]["sync_at_boot"], d["sync"]["interval_h"], d["sync"]["server"]), (True, 12, "time.google.com"))
        d = self.c.post("/api/time/sync", {})
        self.assertEqual(seen[-1], ["ntpd", "-n", "-q", "-p", "time.google.com"])
        self.assertTrue(d["result"]["ok"])
        self.assertEqual(self.c.get("/api/time")["sync"]["last"]["server"], "time.google.com")
        for server in ("-d", "pool.ntp.org;reboot", "$(id)", "a b", "x" * 130, "224.0.0.1"):
            self.c.post("/api/time/sync", {"server": server}, expect=400)
        self.assertEqual(len(seen), 1)
        self.c.put("/api/time/sync", {"interval_h": 200}, expect=400)
        self.assertIsNotNone(self.c.app.state.read_json(timesvc.STATE_FILE))


class SafetyTests(Base):
    def test_no_reset_ever_answered_yes(self):
        c = self.c
        c.put("/api/lan/ippt", c.confirm({"enabled": True, "interface": "usb", "client": "Yoga-pc"}))   # starts with Y
        c.put("/api/lan/ippt", c.confirm({"feature_mode": True}))
        c.put("/api/dns/rebinding", {"enabled": True})
        c.put("/api/ethernet", {"role": "lan"})
        c.put("/api/lan/ippt", c.confirm({"enabled": False}))
        c.put("/api/ethernet", {"role": "wan_dhcp"})
        c.put("/api/wan/priority", {"order": ["ethernet", "wwan"]})
        c.put("/api/wan/routing-priority", {"first": "ethernet"})
        self.assertEqual(c.app.cli.store.state["network"]["reboots"], 0)
        for call in self.calls:
            sp = cli_specs.spec(call["cli"], call["cmd"])
            if sp is not None and sp.mode == "stdin":
                # never more answers than the spec lists: nothing is left for the reset prompt but EOF
                self.assertEqual(len(call["answers"]), sp.expected_count(call["answers"]), call)

    def test_no_brand_names_in_pages(self):
        rx = re.compile(r"verizon|inseego|mifi|jetpack|novatel", re.I)
        for f in glob.glob(os.path.join(ROOT, "web", "app", "pages", "network", "*.js")):
            with open(f, encoding="utf-8") as fh:
                self.assertIsNone(rx.search(fh.read()), f)

    def test_audit_has_no_secrets_and_records_writes(self):
        self.c.put("/api/dns/filter", {"enabled": True, "mode": "block", "domains": ["ads.example.com"]})
        actions = [r["action"] for r in self.c.app.state.audit_tail()]
        self.assertIn("dns.filter.update", actions)


if __name__ == "__main__":
    unittest.main()
