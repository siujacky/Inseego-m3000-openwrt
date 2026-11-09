"""Firewall + VPN area: parsers on the real captures, spec answer lists, routes, validation, mock round trips."""
import base64
import binascii
import unittest

from tests.helpers import MockClient, read_fixture

from m3100ui.adapters import cli_specs
from m3100ui.adapters.cli import CliResult
from m3100ui.adapters.specs import firewall as fwspecs
from m3100ui.errors import ApiError
from m3100ui.services import firewall as fw
from m3100ui.services import vpn

QC = "mifi_qcmap_cli"


def ovpn_files(extra=b"", with_certs=True):
    prof = (b"client\ndev tun\nproto udp\nremote vpn.example.com 1194\nresolv-retry infinite\nca ca.crt\n"
            b"cert client.crt\nkey client.key\nauth-user-pass\n" + extra)
    files = [{"name": "home.ovpn", "data": base64.b64encode(prof).decode()},
             {"name": "ca.crt", "data": base64.b64encode(b"-----BEGIN CERTIFICATE-----\nMII\n-----END CERTIFICATE-----\n").decode()}]
    if with_certs:
        files += [{"name": "client.crt", "data": base64.b64encode(b"crt").decode()},
                  {"name": "client.key", "data": base64.b64encode(b"key").decode()}]
    return files


class FixtureParserTests(unittest.TestCase):
    """The parsers must handle the exact shapes captured from the unit (no SIM, nothing configured)."""

    def sw(self, cmd, key):
        return fw.parse_switch(read_fixture(QC, cmd), fw.SWITCHES[key][1])

    def test_switches(self):
        self.assertIsNone(self.sw("get_vpnpassthrough", "vpn_passthrough"))     # value redacted in the capture
        self.assertIsNone(self.sw("get_dnspassthrough", "dns_passthrough"))
        self.assertTrue(self.sw("get_http_redirect", "http_redirect"))
        self.assertFalse(self.sw("get_icmp_response", "wan_ping"))              # "[Disable" without "]"
        self.assertTrue(self.sw("get_ipv6_firewall", "ipv6_firewall"))
        self.assertFalse(self.sw("get_allowed_ports", "allowed_ports"))
        self.assertFalse(fw.parse_switch(read_fixture(QC, "get_port_forwarding"), r"Port forwarding is:\s*\[([^\]\n]*)"))
        self.assertFalse(fw.parse_switch(read_fixture(QC, "get_port_filter"), r"Port filtering is:\s*\[([^\]\n]*)"))
        self.assertEqual(fw.parse_severity(read_fixture(QC, "get_fw_severity")), "medium")
        self.assertFalse(fw.parse_switch(read_fixture(QC, "get_dmz"), r"DMZ status:\s*\[([^\]\n]*)"))

    def test_empty_lists(self):
        self.assertEqual(fw.parse_fwd_entries(read_fixture(QC, "get_port_fwd_list")), [])
        self.assertEqual(fw.parse_filter_entries(read_fixture(QC, "get_port_filter_list")), [])
        self.assertEqual(fw.parse_allowed_entries(read_fixture(QC, "get_allowed_port_list")), [])
        for cmd in ("get_port_fwd_list", "get_port_filter_list", "get_allowed_port_list", "get_ippt"):
            self.assertTrue(CliResult(QC, cmd, read_fixture(QC, cmd), 0).ok, cmd)

    def test_vpn_fixtures(self):
        self.assertFalse(vpn.parse_service_flag(read_fixture("vpn_cli", "get_auto_connect")))
        r = CliResult("vpn_cli", "get_ipsec_vpn", read_fixture("vpn_cli", "get_ipsec_vpn"), 0)
        self.assertTrue(r.ok)                      # "returned 0x0" is not an RC line; exit code decides
        self.assertFalse(vpn.parse_service_flag(r.text))
        self.assertFalse(vpn.parse_service_flag(read_fixture("vpn_cli", "get_wguard_vpn")))
        # get_settings / get_connection_statistics were captured with EOF on stdin: the fgets loops
        # spin, so nothing was flushed.  The parsers must treat that as "not configured".
        self.assertFalse(vpn.parse_openvpn_cfg(read_fixture("vpn_cli", "get_settings"))["configured"])
        self.assertIsNone(vpn.parse_stats(read_fixture("vpn_cli", "get_connection_statistics")))
        self.assertIsNone(vpn.parse_wg(""))
        self.assertIsNone(vpn.parse_ipsec(""))


class VendorFormatTests(unittest.TestCase):
    """Lines built with the printf formats decoded from mifi_qcmap_cli / vpn_cli."""

    def test_fwd_parse_group_expand(self):
        txt = ("cmd_router2_get_port_fwd_app_list returned 0 (MIFI_QCMAP: success.)\n\nPort forwarding List...\n"
               "fwd rule name=[Game], enabled=[1], ip=[192.168.1.50], type=[UDP], local port=[6000], remote port=[5000]\n"
               "fwd rule name=[Game], enabled=[1], ip=[192.168.1.50], type=[UDP], local port=[6001], remote port=[5001]\n"
               "fwd rule name=[Web [x]], enabled=[0], ip=[192.168.1.60], type=[TCP & UDP], local port=[80], remote port=[8080]\n")
        e = fw.parse_fwd_entries(txt)
        self.assertEqual([x["external"] for x in e], [5000, 5001, 8080])
        self.assertEqual(e[2]["name"], "Web [x]")
        self.assertEqual(e[2]["protocol"], "both")
        rules = fw.group_fwd(e)
        self.assertEqual(len(rules), 2)
        self.assertEqual((rules[0]["external"], rules[0]["external_end"], rules[0]["internal"], rules[0]["internal_end"]),
                         (5000, 5001, 6000, 6001))
        self.assertEqual(fw.expand_fwd(rules[0]), [dict(e[0], protocol="udp"), dict(e[1], protocol="udp")])

    def test_filter_split(self):
        lines = ["filter rule name=[%s], enabled=[1], type=[TCP], start port=[%d], end port=[%d]" % (n, a, b)
                 for n, a, b in fw.FILTER_STD[0]["members"]]
        lines.append("filter rule name=[Games], enabled=[0], type=[TCP & UDP], start port=[3074], end port=[3075]")
        std, custom = fw.split_filter(fw.parse_filter_entries("\n".join(lines)))
        self.assertTrue(std["email"])
        self.assertFalse(std["http"])
        self.assertEqual([c["name"] for c in custom], ["Games"])
        self.assertEqual(custom[0]["protocol"], "both")
        std, custom = fw.split_filter(fw.parse_filter_entries(lines[0]))      # partial group = not allowed
        self.assertFalse(std["email"])

    def test_allowed_parse(self):
        txt = ("Allowed Port rule : enabled=[1], interface name=[rmnet_data0,bridge0], port list=[22,8080], type=[TCP], "
               "range=[List], start/ext port=[0], end/int port=[0]\n"
               "Allowed Port rule : enabled=[0], interface name=[rmnet_data0], port list=[], type=[UDP], range=[Range], "
               "start/ext port=[5000], end/int port=[5010]\n")
        a = fw.parse_allowed_entries(txt)
        self.assertEqual(a[0]["interfaces"], ["rmnet_data0", "bridge0"])
        self.assertEqual((a[0]["mode"], a[0]["ports"]), ("list", [22, 8080]))
        self.assertEqual((a[1]["mode"], a[1]["start"], a[1]["end"], a[1]["enabled"]), ("range", 5000, 5010, False))

    def test_vpn_stats_and_settings(self):
        st = vpn.parse_stats("Vpn server type : [5]\nConnection state: [2]\nConnection IPv4 address: [10.8.0.6]\n"
                             "Connection IPv4 subnet address: [255.255.255.0]\nConnection IPv4 Tx Bytes: [100]\n"
                             "Connection IPv4 Rx Bytes: [200]\nConnection Up Time: [1 days 02:03:04]\n")
        self.assertEqual((st["state"], st["ip"], st["rx_bytes"], st["uptime_s"]), ("connected", "10.8.0.6", 200, 93784))
        st = vpn.parse_stats("Vpn server type : [6]\nConnection state: [4]\nConnection failed reason: [3]\n")
        self.assertEqual(st["state"], "failed")
        self.assertIn("Authentication", st["failed_text"])
        ov = vpn.parse_openvpn_cfg("vpn_get_settings returned 0x0 (VPN: success.)\n\n"
                                   "vpn_get_openvpn_config_file_settings returned 0 (VPN: success.)\n"
                                   "OpenVPN uploaded config file name: [home.ovpn]\nOpenVPN server URL: [vpn.example.com]\n"
                                   "OpenVPN server username: [bob]\nOpenVPN server password: [p]\n")
        self.assertEqual((ov["configured"], ov["config_file"], ov["password"]), (True, "home.ovpn", "p"))
        self.assertEqual(vpn.parse_validation("config file name:[a.ovpn]\nconfig URL:[x]\nMissed configuration file name is: [ca.crt]\n"),
                         {"config_file": "a.ovpn", "url": "x", "missing": "ca.crt"})


class OvpnValidationTests(unittest.TestCase):
    def files(self, **kw):
        return [{"name": f["name"], "data": base64.b64decode(f["data"])} for f in ovpn_files(**kw)]

    def test_good_profile(self):
        r = vpn.validate_ovpn_files(self.files())
        self.assertEqual(r["problems"], [])
        self.assertEqual((r["config_file"], r["server"]), ("home.ovpn", "vpn.example.com"))

    def test_rejections(self):
        self.assertTrue(vpn.validate_ovpn_files(self.files(with_certs=False))["problems"])
        self.assertIn("not allowed", vpn.validate_ovpn_files(self.files(extra=b"up /tmp/x.sh\n"))["problems"][0])
        self.assertIn("not allowed", vpn.validate_ovpn_files(self.files(extra=b"route-nopull\n"))["problems"][0])
        bad = [{"name": "a.ovpn", "data": b"client\ndev tap\nremote x 1\n<ca>\nup\n</ca>\n"}]
        p = vpn.validate_ovpn_files(bad)["problems"]
        self.assertEqual(len(p), 1)                     # "up" inside the inline CA is not a directive
        self.assertIn("dev tun", p[0])
        self.assertTrue(vpn.validate_ovpn_files([])["problems"])


class X25519Tests(unittest.TestCase):
    def test_rfc7748_vectors(self):
        a = binascii.unhexlify("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        self.assertEqual(binascii.hexlify(vpn.x25519(a, (9).to_bytes(32, "little"))).decode(),
                         "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
        k = vpn.wg_generate_private()
        self.assertRegex(k, vpn.WG_KEY_RE)
        self.assertRegex(vpn.wg_public_key(k), vpn.WG_KEY_RE)
        self.assertIsNone(vpn.wg_public_key("not base64"))


class SpecTests(unittest.TestCase):
    def setUp(self):
        self.specs = cli_specs.all_specs()

    def test_specs_loaded_without_conflict(self):
        for key in fwspecs.SPECS:
            self.assertIn(key, self.specs, key)
            self.assertNotIn("%s %s" % key, cli_specs.SPEC_ERRORS)
            self.assertEqual(cli_specs.classify(*key), "setter", key)
            # static analysis; fixtures/sandbox/verified.json may upgrade it after tools/device/verify_specs.py
            self.assertIn(self.specs[key].source, ("static", "sandbox", "roundtrip"))
            self.assertEqual(self.specs[key].tier, 1)
            self.assertTrue(self.specs[key].evidence)
            sp = self.specs[key]
            sp.check(sp.sample_answers())       # the sandbox sample is self-consistent

    def test_sandbox_records_match_prompts(self):
        """fixtures/sandbox/verified.json only upgrades a spec while its prompt list is unchanged."""
        import json
        import os
        from tests.helpers import FIXTURES
        path = os.path.join(FIXTURES, "sandbox", "verified.json")
        if not os.path.isfile(path):
            self.skipTest("no sandbox results")
        with open(path, encoding="utf-8") as f:
            recs = {(r["cli"], r["cmd"]): r for r in json.load(f)}
        for key, sp in fwspecs.SPECS.items():
            if key in recs:
                self.assertEqual(recs[key]["prompts"], sp.prompts, key)
                self.assertEqual(recs[key]["mode"], sp.mode, key)

    def test_no_vendor_names_in_ui(self):
        import glob
        import os
        import re
        from tests.helpers import ROOT
        files = glob.glob(os.path.join(ROOT, "web", "app", "pages", "firewall", "*.js")) + \
            glob.glob(os.path.join(ROOT, "web", "app", "pages", "vpn", "*.js"))
        self.assertGreaterEqual(len(files), 8)
        for fn in files:
            with open(fn, encoding="utf-8") as f:
                text = f.read()
            self.assertIsNone(re.search(r"(?i)verizon|inseego|mifi|jetpack|novatel", text), fn)

    def test_record_list_counts(self):
        sp = self.specs[(QC, "set_port_fwd_list")]
        sp.check(["0"])                                           # clear list (argv only)
        sp.check(["1", "1", "A", "192.168.1.5", "2", "80", "80", "1"])
        with self.assertRaises(ApiError):
            sp.check(["2", "1", "A", "192.168.1.5", "2", "80", "80", "1"])
        with self.assertRaises(ApiError):
            sp.check(["31"] + ["1"] * 217)
        with self.assertRaises(ApiError):
            sp.check(["1", "1", "", "192.168.1.5", "2", "80", "80", "1"])   # empty argv value
        self.assertEqual(self.specs[(QC, "set_port_filter_list")].expected_count(["3"]), 16)
        self.assertEqual(self.specs[(QC, "set_allowed_port_list")].expected_count(["2"]), 15)

    def test_branch_flows(self):
        sp = self.specs[("vpn_cli", "set_settings")]
        self.assertEqual(len(sp.prompts_for(["5", "1"])), 2)
        self.assertEqual(len(sp.prompts_for(["5", "0"])), 8)
        self.assertEqual(len(sp.prompts_for(["6", "0"])), 18)
        self.assertEqual(len(sp.prompts_for(["6", "1"])), 3)
        self.assertEqual(len(sp.prompts_for(["4", "1"])), 3)
        with self.assertRaises(ApiError):
            sp.check(["2", "0"])                                  # manual OpenVPN not offered
        with self.assertRaises(ApiError):
            sp.check(["5", "1", "extra"])
        with self.assertRaises(ApiError):
            sp.check(["5", "0", "3", "/tmp/vpnFiles", "a.ovpn", "x", "", "pw"])   # empty line would shift answers
        c = self.specs[("vpn_cli", "connect_vpn")]
        self.assertEqual(c.prompts_for(["5"]), [fwspecs.P_TYPE])
        self.assertEqual(c.prompts_for(["6", "1"]), [fwspecs.P_TYPE, fwspecs.P_TUNNEL])
        with self.assertRaises(ApiError):
            c.check(["3"])

    def test_ipsec_prompt_branches(self):
        base = ["4", "0", "0", "0", "1", "n", "1", "0", "%any", "%any"]
        tail = ["r", "10.0.0.0/24", "28800", "p1-x", "3600", "p2-x"]
        psk = base + ["1", "k" * 8, "1", "k" * 8, "2", "%defaultroute", "192.168.1.0/24"] + tail + ["0"]
        self.assertEqual(len(fwspecs.ipsec_set_prompts(psk)), len(psk) - 2)
        eap = base + ["2", "u", "p", "1", "k" * 8, "2"] + tail + ["1", "3", "30", "120"]
        prompts = fwspecs.ipsec_set_prompts(eap)
        self.assertEqual(len(prompts), len(eap) - 2)
        self.assertNotIn("Enter IPSec Local IP address: ", prompts)
        with self.assertRaises(ValueError):
            fwspecs.ipsec_set_prompts(base + ["3"])               # certificates not exposed


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.c = MockClient()

    def tearDown(self):
        self.c.cleanup()

    def last(self, cmd):
        calls = [x for x in self.c.app.cli.store.calls if x["cmd"] == cmd]
        self.assertTrue(calls, cmd)
        x = calls[-1]
        return x["args"] or x["answers"]

    def assert_matches_spec(self, cli, cmd):
        calls = [x for x in self.c.app.cli.store.calls if x["cmd"] == cmd]
        sp = cli_specs.spec(cli, cmd)
        vals = calls[-1]["args"] if sp.mode == "argv" else calls[-1]["answers"]
        sp.check(vals)
        return vals

    def test_every_get(self):
        keys = {"/api/firewall/general": ["vpn_passthrough", "wan_ping", "ipv6_firewall", "http_redirect", "severity"],
                "/api/firewall/forwarding": ["enabled", "rules", "max_entries", "dmz_enabled", "presets", "lan"],
                "/api/firewall/forwarding/rules": None, "/api/firewall/filtering": ["enabled", "standard", "custom"],
                "/api/firewall/filtering/apps": None, "/api/firewall/allowed": ["enabled", "rules", "max_rules"],
                "/api/firewall/allowed/rules": None, "/api/firewall/dmz": ["enabled", "ip", "forwarding_enabled"],
                "/api/vpn/status": ["running", "auto_connect", "services", "openvpn", "connected", "summary"],
                "/api/vpn/wireguard": ["service_enabled", "tunnels"], "/api/vpn/ipsec": ["service_enabled", "tunnels"]}
        for path, ks in keys.items():
            r = self.c.get(path)
            for k in ks or []:
                self.assertIn(k, r, (path, k))
        self.assertEqual(len(self.c.get("/api/vpn/wireguard")["tunnels"]), 3)
        self.assertEqual(len(self.c.get("/api/vpn/ipsec")["tunnels"]), 6)
        self.assertIn("lines", self.c.get("/api/vpn/log", query={"source": "wireguard"}))
        self.c.get("/api/vpn/log", query={"source": "nope"}, expect=400)
        self.assertTrue(self.c.app.features.all()["vpn"])

    def test_routes_are_tier1(self):
        for rt in self.c.app.router.describe():
            if rt["path"].startswith(("/api/firewall", "/api/vpn")):
                self.assertEqual(rt["tier"], 0, rt)
        # no write in this area asks for a tier-2/3 confirmation (design §8: firewall/VPN = tier 1)
        self.c.put("/api/firewall/general", {"wan_ping": True}, expect=200)

    def test_general(self):
        r = self.c.put("/api/firewall/general", {"wan_ping": True, "ipv6_firewall": False, "vpn_passthrough": False})
        self.assertEqual((r["wan_ping"], r["ipv6_firewall"], r["vpn_passthrough"]), (True, False, False))
        self.assertEqual(self.assert_matches_spec(QC, "set_icmp_response"), ["1"])
        self.assertEqual(self.assert_matches_spec(QC, "set_ipv6_firewall"), ["0"])
        e = self.c.put("/api/firewall/general", {"dns_passthrough": True}, expect=400)
        self.assertEqual(e["error"]["details"]["field"], "dns_passthrough")     # read-only: no setter
        self.c.put("/api/firewall/general", {"wan_ping": "maybe"}, expect=400)

    def test_static_specs_refused_on_a_real_unit(self):
        sp = cli_specs.spec(QC, "set_icmp_response")
        old = sp.source
        self.c.app.cli.allow_static_specs = False
        sp.source = "static"
        try:
            e = self.c.put("/api/firewall/general", {"wan_ping": True}, expect=403)
            self.assertEqual(e["error"]["code"], "unverified_setter")
        finally:
            sp.source = old

    def test_port_forward_crud(self):
        c = self.c
        r = c.post("/api/firewall/forwarding/rules", {"name": "Game", "ip": "192.168.1.50", "protocol": "udp",
                                                     "external": 5000, "external_end": 5002, "internal": 6000})
        self.assertEqual((r[0]["external_end"], r[0]["internal_end"], r[0]["entries"]), (5002, 6002, 3))
        vals = self.assert_matches_spec(QC, "set_port_fwd_list")
        self.assertEqual(vals[:8], ["3", "1", "Game", "192.168.1.50", "2", "5000", "6000", "2"])
        r = c.post("/api/firewall/forwarding/rules", {"preset": "web", "ip": "192.168.1.60"})
        self.assertEqual([x["name"] for x in r], ["Game", "HTTP", "HTTPS"])
        self.assertEqual(r[1]["app"], "web")
        r = c.put("/api/firewall/forwarding/rules/" + r[1]["id"], dict(r[1], external=8080, external_end="", internal=80))
        self.assertEqual((r[1]["external"], r[1]["external_end"], r[1]["internal"]), (8080, 8080, 80))
        self.assertEqual(self.assert_matches_spec(QC, "set_port_fwd_list")[0], "5")
        r = c.put("/api/firewall/forwarding/rules/" + r[2]["id"], {"external": 8443})    # single port stays single
        self.assertEqual((r[2]["external"], r[2]["external_end"], r[2]["internal"]), (8443, 8443, 443))
        r = c.delete("/api/firewall/forwarding/rules/" + r[0]["id"])
        self.assertEqual(len(r), 2)
        c.delete("/api/firewall/forwarding/rules/nope", expect=404)
        for rule in list(r):
            r = c.delete("/api/firewall/forwarding/rules/" + rule["id"])
        self.assertEqual(r, [])
        self.assertEqual(self.last("set_port_fwd_list"), ["0"])                   # empty list via argv

    def test_port_forward_validation(self):
        c = self.c
        c.post("/api/firewall/forwarding/rules", {"name": "A", "ip": "192.168.1.50", "protocol": "tcp", "external": 22})
        bad = [({"name": "B", "ip": "10.0.0.5", "protocol": "tcp", "external": 80}, "ip"),
               ({"name": "B", "ip": "192.168.1.1", "protocol": "tcp", "external": 80}, "ip"),
               ({"name": "B", "ip": "192.168.1.255", "protocol": "tcp", "external": 80}, "ip"),
               ({"name": "B", "ip": "192.168.1.9", "protocol": "both", "external": 20, "external_end": 25}, "external"),
               ({"name": "a", "ip": "192.168.1.9", "protocol": "udp", "external": 80}, "name"),
               ({"name": "B[1]", "ip": "192.168.1.9", "protocol": "udp", "external": 80}, "name"),
               ({"name": "B", "ip": "192.168.1.9", "protocol": "icmp", "external": 80}, "protocol"),
               ({"name": "B", "ip": "192.168.1.9", "protocol": "tcp", "external": 70000}, "external"),
               ({"name": "B", "ip": "192.168.1.9", "protocol": "tcp", "external": 90, "external_end": 80}, "external_end"),
               ({"name": "B", "ip": "192.168.1.9", "protocol": "tcp", "external": 1000, "external_end": 1040}, "external_end"),
               ({"name": "B", "ip": "192.168.1.9", "protocol": "tcp", "external": 80, "bogus": 1}, "bogus")]
        for body, field in bad:
            e = c.post("/api/firewall/forwarding/rules", body, expect=400)
            self.assertEqual(e["error"]["details"]["field"], field, body)

    def test_dmz_and_forwarding_exclusive(self):
        c = self.c
        self.assertTrue(c.put("/api/firewall/forwarding", {"enabled": True})["enabled"])
        self.assertEqual(c.app.rest.calls[-1]["path"], "PortForwardingSettings")
        c.put("/api/firewall/dmz", {"enabled": True, "ip": "192.168.1.80"}, expect=409)
        c.put("/api/firewall/forwarding", {"enabled": False})
        r = c.put("/api/firewall/dmz", {"enabled": True, "ip": "192.168.1.80"})
        self.assertEqual((r["enabled"], r["ip"]), (True, "192.168.1.80"))
        self.assertEqual(c.app.rest.data["DMZSettings"], {"DMZEnabled": True, "DMZIpAddress": "192.168.1.80"})
        c.put("/api/firewall/forwarding", {"enabled": True}, expect=409)
        c.put("/api/firewall/dmz", {"enabled": True, "ip": ""}, expect=400)
        c.put("/api/firewall/dmz", {"enabled": True, "ip": "8.8.8.8"}, expect=400)
        self.assertFalse(c.put("/api/firewall/dmz", {"enabled": False})["enabled"])

    def test_port_filtering(self):
        c = self.c
        r = c.put("/api/firewall/filtering", {"standard": {"email": True, "https": True}, "enabled": True})
        self.assertEqual((r["standard"]["email"], r["standard"]["https"], r["enabled"], r["used_entries"]), (True, True, True, 11))
        vals = self.assert_matches_spec(QC, "set_port_filter_list")
        self.assertEqual(vals[:6], ["11", "POP", "1", "110", "110", "1"])
        apps = c.post("/api/firewall/filtering/apps", {"name": "Games", "start": 3074, "end": 3075, "protocol": "both"})
        self.assertEqual(apps[0]["name"], "Games")
        self.assertEqual(self.assert_matches_spec(QC, "set_port_filter_list")[-5:], ["Games", "1", "3074", "3075", "3"])
        apps = c.put("/api/firewall/filtering/apps/" + apps[0]["id"], dict(apps[0], enabled=False))
        self.assertFalse(apps[0]["enabled"])
        c.post("/api/firewall/filtering/apps", {"name": "HTTP", "start": 80}, expect=400)          # reserved
        c.post("/api/firewall/filtering/apps", {"name": "X", "start": 90, "end": 80}, expect=400)
        c.put("/api/firewall/filtering", {"standard": {"nope": True}}, expect=400)
        self.assertEqual(c.delete("/api/firewall/filtering/apps/" + apps[0]["id"]), [])

    def test_allowed_ports(self):
        c = self.c
        r = c.post("/api/firewall/allowed/rules", {"interfaces": "rmnet_data0,bridge0", "mode": "list", "ports": "22, 8080",
                                                   "protocol": "tcp"})
        self.assertEqual(r[0]["ports"], [22, 8080])
        self.assertEqual(self.assert_matches_spec(QC, "set_allowed_port_list"),
                         ["1", "1", "rmnet_data0,bridge0", "1", "22,8080", "0", "1", "1"])
        r = c.post("/api/firewall/allowed/rules", {"interfaces": ["rmnet_data0"], "mode": "range", "start": 5000, "end": 5010,
                                                   "protocol": "udp", "enabled": False})
        self.assertEqual((r[1]["start"], r[1]["end"], r[1]["enabled"]), (5000, 5010, False))
        self.assertTrue(c.put("/api/firewall/allowed", {"enabled": True})["enabled"])
        self.assertEqual(self.last("set_allowed_ports"), ["1"])
        c.post("/api/firewall/allowed/rules", {"interfaces": "eth0;reboot", "start": 1}, expect=400)
        c.post("/api/firewall/allowed/rules", {"interfaces": "eth0", "mode": "list", "ports": ""}, expect=400)
        self.assertEqual(len(c.delete("/api/firewall/allowed/rules/" + r[0]["id"])), 1)

    def test_unavailable_is_503(self):
        self.c.app.cli.store.set_text(QC, "get_port_fwd_list",
                                      "cmd_router2_get_port_fwd_app_list returned 5 (MIFI_QCMAP: process not running.)\n")
        from m3100ui.adapters import mock
        saved = mock.CLI_HANDLERS.pop((QC, "get_port_fwd_list"))
        try:
            e = self.c.get("/api/firewall/forwarding/rules", expect=503)
            self.assertEqual(e["error"]["code"], "unavailable")
        finally:
            mock.CLI_HANDLERS[(QC, "get_port_fwd_list")] = saved

    def test_openvpn_flow(self):
        c = self.c
        e = c.post("/api/vpn/openvpn/validate", {"files": ovpn_files(with_certs=False)}, expect=400)
        self.assertIn("client.crt", e["error"]["message"])
        self.assertEqual(c.post("/api/vpn/openvpn/validate", {"files": ovpn_files()})["server"], "vpn.example.com")
        c.post("/api/vpn/openvpn", {"files": ovpn_files(), "username": "bob", "password": ""}, expect=400)
        c.post("/api/vpn/openvpn", {"files": [{"name": "../x.ovpn", "data": ""}], "username": "b", "password": "p"},
               expect=400)
        s = c.post("/api/vpn/openvpn", {"files": ovpn_files(), "username": "bob", "password": "s3cret pw", "retries": 2,
                                        "connect": True})
        self.assertTrue(s["openvpn"]["configured"])
        self.assertTrue(s["connected"])
        self.assertNotIn("password", s["openvpn"])
        vals = self.assert_matches_spec("vpn_cli", "set_settings")
        self.assertEqual(vals[:3] + vals[4:], ["5", "0", "2", "home.ovpn", "vpn.example.com", "bob", "s3cret pw"])
        self.assertEqual(self.assert_matches_spec("vpn_cli", "connect_vpn"), ["5"])
        self.assertEqual(c.get("/api/vpn/openvpn/secret")["password"], "s3cret pw")
        self.assertFalse(c.post("/api/vpn/openvpn/disconnect")["connected"])
        self.assertFalse(c.delete("/api/vpn/openvpn")["openvpn"]["configured"])
        self.assertEqual(self.last("set_settings"), ["5", "1"])
        for rec in c.app.state.audit_tail():
            self.assertNotIn("s3cret", str(rec))

    def test_services(self):
        r = self.c.put("/api/vpn/settings", {"auto_connect": True, "wireguard": True})
        self.assertTrue(r["reboot_required"])
        self.assertTrue(r["auto_connect"])
        self.assertEqual(self.last("set_wguard_vpn"), ["1"])
        self.assertEqual(self.assert_matches_spec("vpn_cli", "set_auto_connect"), ["1"])
        self.c.put("/api/vpn/settings", {"auto_connect": 2}, expect=400)

    def test_wireguard(self):
        c = self.c
        kp = c.post("/api/vpn/wireguard/keygen")
        self.assertEqual(c.post("/api/vpn/wireguard/pubkey", {"private_key": kp["private_key"]})["public_key"], kp["public_key"])
        body = {"private_key": kp["private_key"], "peer_public_key": kp["public_key"], "tunnel_ip": "10.9.0.2",
                "peer_ip": "10.9.0.1", "allowed_ips": "0.0.0.0/0, 10.1.0.0/16", "endpoint": "wg.example.com",
                "endpoint_port": 51820}
        r = c.put("/api/vpn/wireguard/1", body)
        t = r["tunnels"][1]
        self.assertTrue(t["configured"])
        self.assertNotIn("private_key", t)
        self.assertEqual(t["public_key"], kp["public_key"])
        self.assertEqual(t["allowed_ips"], ["0.0.0.0/0", "10.1.0.0/16"])
        vals = self.assert_matches_spec("vpn_cli", "set_settings")
        self.assertEqual(vals[:3] + vals[8:10], ["6", "0", "1", "wg1.key", kp["private_key"]])
        # keep the stored key when none is sent, and the explicit state flags
        r = c.put("/api/vpn/wireguard/1", dict(t, enabled=False))
        self.assertFalse(r["tunnels"][1]["enabled"])
        self.assertEqual(self.last("set_settings")[9], kp["private_key"])
        self.assertEqual(c.get("/api/vpn/wireguard/1/secret")["private_key"], kp["private_key"])
        self.assertEqual(c.post("/api/vpn/wireguard/1/connect")["tunnels"][1]["stats"]["state"], "connected")
        self.assertEqual(self.assert_matches_spec("vpn_cli", "connect_vpn"), ["6", "1"])
        for field, value in (("endpoint", "a;b"), ("allowed_ips", "nonsense"), ("name", "bad name"),
                             ("peer_public_key", "x"), ("tunnel_ip", "10.0.0.300"), ("listen_port", 0)):
            e = c.put("/api/vpn/wireguard/1", dict(body, **{field: value}), expect=400)
            self.assertEqual(e["error"]["details"]["field"], field)
        c.put("/api/vpn/wireguard/3", body, expect=400)
        c.put("/api/vpn/wireguard/2", dict(body, private_key=""), expect=400)    # new tunnel needs a key
        self.assertFalse(c.delete("/api/vpn/wireguard/1")["tunnels"][1]["configured"])

    def test_ipsec(self):
        c = self.c
        body = {"name": "office", "client_psk": "supersecret1", "remote_ip": "203.0.113.9", "remote_subnet": "10.1.0.0/16",
                "local_subnet": "192.168.1.0/24", "phase1": "aes256-sha2_256-modp2048", "phase2": "aes256-sha2_256",
                "dpd": {"enabled": True, "action": 3, "delay": 30, "timeout": 120}}
        r = c.put("/api/vpn/ipsec/2", body)
        t = r["tunnels"][2]
        self.assertTrue(t["configured"])
        self.assertEqual((t["remote_subnet"], t["client_auth"], t["dpd"]["timeout"]), ("10.1.0.0/16", "psk", 120))
        vals = self.assert_matches_spec("vpn_cli", "set_settings")
        self.assertEqual(vals[:3], ["4", "0", "2"])
        self.assertEqual(vals[-4:], ["1", "3", "30", "120"])
        eap = dict(body, client_auth="eap", client_username="u", client_password="p", dpd={"enabled": False})
        c.put("/api/vpn/ipsec/2", eap)
        vals = self.assert_matches_spec("vpn_cli", "set_settings")
        self.assertNotIn("192.168.1.0/24", vals)                  # EAP client: no local IP/subnet prompts
        self.assertEqual(vals[-1], "0")
        for field, value in (("client_psk", "short"), ("client_psk", 'has"quote1'), ("phase1", "aes 256"),
                             ("ike_lifetime", "1234567"), ("remote_ip", ""), ("name", "a b")):
            e = c.put("/api/vpn/ipsec/2", dict(body, **{field: value}), expect=400)
            self.assertEqual(e["error"]["details"]["field"], field, field)
        self.assertEqual(c.post("/api/vpn/ipsec/2/connect")["tunnels"][2]["stats"]["state"], "connected")
        self.assertEqual(self.assert_matches_spec("vpn_cli", "connect_vpn"), ["4", "2"])
        self.assertFalse(c.delete("/api/vpn/ipsec/2")["tunnels"][2]["configured"])
        self.assertEqual(self.last("set_settings"), ["4", "1", "2"])


if __name__ == "__main__":
    unittest.main()
