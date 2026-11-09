"""Wi-Fi area: parsers on real fixtures, setter answer lists vs Specs, routes, validation, tiers, fallbacks."""
import unittest

from tests.helpers import MockClient, read_fixture

from m3100ui.adapters import cli_specs
from m3100ui.mock_handlers import wifi as wifi_mock
from m3100ui.services import wifi as W


def calls(c, cmd=None):
    out = c.app.cli.store.calls
    return [x for x in out if cmd is None or x["cmd"] == cmd]


def spec_count(cli, cmd, answers):
    sp = cli_specs.spec(cli, cmd)
    assert sp is not None, (cli, cmd)
    return sp.expected_count(answers)


class ParserTests(unittest.TestCase):
    def test_caps_fixture(self):
        caps = W.parse_caps(read_fixture("wifi_cli", "get_caps"))
        self.assertTrue(caps["wps"])
        self.assertEqual(caps["countries"], ["US", "GB", "AU", "CA"])
        self.assertEqual(caps["max_clients"], 32)
        self.assertEqual(caps["system_max_clients"], 32)
        self.assertEqual(caps["interfaces"], 2)
        self.assertIn("ac", caps["modes"])            # "ACN5." (vendor typo) is a/n/ac
        self.assertIn("bgn_ax", caps["modes"])
        self.assertEqual(len(caps["modes"]), 13)       # 14 wifi_api modes; AC_2G is not offered
        self.assertEqual(caps["channels"]["US"]["2g"], list(range(1, 12)))
        self.assertEqual(caps["channels"]["GB"]["5g"], [36, 40, 44, 48])
        self.assertEqual(caps["channels"]["JP"]["5g"], [])

    def test_chlist_fixture(self):
        self.assertEqual(W.parse_chlist(read_fixture("wifi_cli", "get_ap_chlist2_4")), list(range(1, 12)))
        self.assertEqual(W.parse_chlist(read_fixture("wifi_cli", "get_ap_chlist5")), [36, 40, 44, 48, 149, 153, 157, 161])

    def test_unanswered_profile_fixture(self):
        # captured without an index answer: "Invalid profile index." -> not a profile
        self.assertIsNone(W.parse_profile(read_fixture("wifi_cli", "get_ap_profile")))

    def test_profile_shape(self):
        m = wifi_mock._seed({})
        p = m["profiles"][1]
        p.update({"ssid": " lead space", "ifmask": 1 | 4 | 16, "security": 5, "psk": "pa ss word]x", "width": 2})
        text = (wifi_mock.MBH_HEAD % "get_ap_profile" + "Enter the Wifi profile index (1-4):" +
                wifi_mock._status("wifi_get_ap_profile") + wifi_mock._profile_text(p) + wifi_mock.MBH_TAIL)
        r = W.parse_profile(text)
        self.assertEqual(r["ssid"], " lead space")     # exact, not stripped
        self.assertEqual(r["psk"], "pa ss word]x")
        self.assertEqual(r["ifmask"], 21)
        self.assertEqual(r["security"], "wpa3")
        self.assertEqual(r["width"], 40)
        self.assertEqual(r["mode"], "bgn_ax")

    def test_other_fixtures(self):
        self.assertEqual(W.parse_mac_list(read_fixture("wifi_cli", "get_sta_list")), [])
        self.assertEqual(W.parse_mac_list(read_fixture("wifi_cli", "get_wps_clients")), [])
        self.assertEqual(W.parse_mac_list(" MAC Addr[0]: [aa:bb:cc:dd:ee:0f]\n"), ["AA:BB:CC:DD:EE:0F"])
        t = read_fixture("wifi_cli", "get_settings")
        self.assertEqual(W._line_value(t, "Country Code").strip(), "US")
        t = read_fixture("wifi_cli", "get_ap_settings")
        self.assertEqual(W._line_value(t, "Max Number of Clients").strip(), "32")
        t = read_fixture("ccm2_cli", "get_wifi_while_usbteth")
        self.assertEqual(W._line_value(t, "enabled"), "1")

    def test_helpers(self):
        self.assertTrue(W.wps_pin_ok("12345670"))
        self.assertFalse(W.wps_pin_ok("12345678"))
        self.assertTrue(W.wps_pin_ok("1234"))
        self.assertFalse(W.wps_pin_ok("12a4"))
        self.assertEqual(W.ssid_suffix("02:00:00:00:10:0a"), "100A")
        self.assertEqual(W.ssid_suffix("bad"), "")
        for bad in ("", " x", "x ", "a" * 33, "café"):
            with self.assertRaises(Exception):
                W.v_ssid("ssid", bad)
        self.assertEqual(W.v_ssid("ssid", "My Net-1"), "My Net-1")
        for bad in ("short", "x" * 64, "passéword"):
            with self.assertRaises(Exception):
                W.v_password("password", bad)

    def test_profile_answers_match_spec(self):
        p = W.parse_profile(wifi_mock.MBH_HEAD % "x" + wifi_mock._status("wifi_get_ap_profile") +
                            wifi_mock._profile_text(wifi_mock._seed({})["profiles"][2]))
        ans = W.profile_answers(2, p)
        self.assertEqual(len(ans), spec_count("wifi_cli", "set_ap_profile", ans))
        self.assertEqual(ans[0], "2")
        self.assertEqual(ans[8], "14")   # wifi_api mode ACN_PLUS_AX
        p["security"] = "open"
        self.assertEqual(W.profile_answers(2, p)[13], "")


class SpecTests(unittest.TestCase):
    def test_specs_loaded_without_conflicts(self):
        specs = cli_specs.all_specs()
        for key in [("wifi_cli", "set_ap_profile"), ("wifi_cli", "set_all_profiles"), ("wifi_cli", "start_wps"),
                    ("wifi_cli", "stop_wps"), ("wifi_cli", "set_settings"), ("wifi_cli", "set_wifi_ps"),
                    ("wifi_cli", "set_profile_status"), ("wifi_cli", "add_mac_blist"),
                    ("ccm2_cli", "set_wifi_ap_enabled"), ("sysintcli", "setMacFilterWhiteListEnabled"),
                    ("sysintcli", "setDeviceName")]:
            self.assertIn(key, specs, key)
            self.assertEqual(cli_specs.classify(*key), "setter")
        self.assertFalse([k for k in cli_specs.SPEC_ERRORS if "wifi_cli" in k or "sysintcli setDeviceName" in k or
                          "setMacFilterWhiteListEnabled" in k], cli_specs.SPEC_ERRORS)
        self.assertEqual(specs[("wifi_cli", "set_settings")].tier, 3)
        self.assertEqual(len(specs[("wifi_cli", "set_ap_profile")].prompts), 14)

    def test_every_recorded_setter_call_matches_its_spec(self):
        c = MockClient()
        c.put("/api/wifi/primary", {"security": "wpa3"})
        c.put("/api/wifi/advanced", {"mode_2g": "n", "bandwidth_2g": 40})
        c.post("/api/wifi/wps/start", {"network": "guest", "band": "2g", "method": "pbc"}, expect=400)
        c.put("/api/wifi/guest", {"enabled": True, "band": "2g", "wps": True})
        c.post("/api/wifi/wps/start", {"network": "guest", "band": "2g", "method": "pbc"})
        c.post("/api/wifi/wps/stop", {"network": "guest", "band": "2g"})
        c.put("/api/wifi/country", c.confirm({"country": "AU"}, 3))
        c.post("/api/wifi/radio", c.confirm({"on": False}))
        c.put("/api/clients/02:00:00:00:00:02", {"name": "Phone"})
        c.put("/api/wifi/advanced", {"wifi_while_tethered": False})
        self.assertTrue(calls(c))
        for x in calls(c):
            sp = cli_specs.spec(x["cli"], x["cmd"])
            self.assertIsNotNone(sp, x["cmd"])
            vals = x["args"] if sp.mode == "argv" else x["answers"]
            self.assertEqual(len(vals), sp.expected_count(vals), (x["cli"], x["cmd"], vals))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.c = MockClient()

    def tearDown(self):
        self.c.cleanup()

    def test_gets(self):
        c = self.c
        p = c.get("/api/wifi/primary")
        for k in ("ssid", "security", "password_set", "hidden", "isolation", "wps", "security_options", "active_bands",
                  "ssid_suggestion", "mac_filter_on"):
            self.assertIn(k, p)
        self.assertNotIn("password", p)
        self.assertNotIn("changeme123", str(p))
        g = c.get("/api/wifi/guest")
        self.assertIn("isolate_lan", g)
        self.assertNotEqual(g["ssid"], p["ssid"])
        a = c.get("/api/wifi/advanced")
        for k in ("enabled", "band_2g", "band_5g", "mode_2g", "mode_5g", "channels_2g", "channels_5g", "max_clients",
                  "max_clients_limit", "bandwidth_5g", "allow_screen_changes", "wifi_while_tethered"):
            self.assertIn(k, a)
        self.assertEqual(a["channels_5g"], [36, 40, 44, 48, 149, 153, 157, 161])
        self.assertIn("caps", c.get("/api/wifi/info"))
        self.assertEqual(c.get("/api/wifi/country")["country"], "US")
        self.assertEqual(len(c.get("/api/wifi/wps")["entries"]), 4)
        self.assertEqual(c.get("/api/wifi/macfilter")["mode"], "block")
        cl = c.get("/api/clients")
        self.assertIn("clients", cl)
        self.assertTrue(any(x["is_self"] for x in cl["clients"]))
        self.assertEqual(c.get("/api/clients/limit")["max_clients"], 32)
        self.assertEqual(c.get("/api/wifi/primary/secret")["password"], "changeme123")
        meta = c.get("/api/meta/full")["features"]
        for f in ("wifi.primary", "wifi.guest", "wifi.advanced", "wifi.wps", "wifi.macfilter", "wifi.clients"):
            self.assertTrue(meta.get(f), f)

    def test_primary_rest_path(self):
        c = self.c
        r = c.put("/api/wifi/primary", {"ssid": "Home Net", "password": "a-new-password"})
        self.assertEqual(r["ssid"], "Home Net")
        self.assertEqual(c.app.rest.calls[-1]["path"], "WiFi/PrimarySettings")
        self.assertEqual(c.app.rest.calls[-1]["body"]["WiFiSecurityMode"], "WPA3TRANSITION")
        self.assertFalse(calls(c, "set_ap_profile"))
        self.assertEqual(c.get("/api/wifi/primary/secret")["password"], "a-new-password")

    def test_primary_cli_path_wpa3(self):
        c = self.c
        r = c.put("/api/wifi/primary", {"security": "wpa3"})
        self.assertEqual(r["security"], "wpa3")
        sets = calls(c, "set_ap_profile")
        self.assertEqual([x["answers"][0] for x in sets], ["1", "2"])
        self.assertEqual(sets[0]["answers"][11], "5")       # wifi_api WPA3
        self.assertEqual(sets[0]["answers"][13], "changeme123")
        self.assertEqual(sets[0]["answers"][5], "0")        # WPS off with WPA3-only
        self.assertEqual(calls(c)[-1]["cmd"], "set_all_profiles")
        # REST reports WPA3 as "None" - the view must not be fooled
        self.assertEqual(c.app.rest.get("WiFi/PrimarySettings")["WiFiSecurityMode"], "None")
        self.assertEqual(c.get("/api/wifi/primary")["security"], "wpa3")
        # an SSID change now must not go through REST (merge_put would send security None)
        n = len(c.app.rest.calls)
        c.put("/api/wifi/primary", {"ssid": "Other"})
        self.assertEqual(len(c.app.rest.calls), n)
        self.assertEqual(c.get("/api/wifi/primary")["security"], "wpa3")

    def test_primary_open(self):
        c = self.c
        r = c.put("/api/wifi/primary", {"security": "open"})       # REST path ("None")
        self.assertEqual(r["security"], "open")
        self.assertFalse(r["wps"])
        self.assertEqual(c.app.rest.calls[-1]["body"]["WiFiSecurityMode"], "None")

    def test_owe_then_wpa2_needs_a_new_password(self):
        c = self.c
        r = c.put("/api/wifi/primary", {"security": "owe"})
        self.assertEqual(r["security"], "owe")
        self.assertFalse(r["wps"])
        self.assertEqual(len(calls(c, "set_ap_profile")[-1]["answers"]), 14)
        # the OWE prompt flow never reads a passphrase, so the vendor stores an empty key
        self.assertFalse(c.get("/api/wifi/primary")["password_set"])
        r = c.put("/api/wifi/primary", {"security": "wpa2"}, expect=400)
        self.assertEqual(r["error"]["details"]["field"], "password")
        r = c.put("/api/wifi/primary", {"security": "wpa2", "password": "12345678"})
        self.assertEqual(r["security"], "wpa2")
        self.assertTrue(r["password_set"])

    def test_split_ssid(self):
        c = self.c
        r = c.put("/api/wifi/primary", {"split_ssid": True, "ssid_5g": "Home 5G"})
        self.assertTrue(r["split_ssid"])
        self.assertEqual(r["ssid_5g"], "Home 5G")
        # only 5 GHz active (2.4 GHz given to the guest): names must stay separate across an unrelated save
        c.put("/api/wifi/guest", {"enabled": True, "band": "2g"})
        r = c.put("/api/wifi/primary", {"isolation": True})
        self.assertEqual((r["ssid"], r["ssid_5g"], r["split_ssid"]), ("Router-5G", "Home 5G", True))
        self.assertEqual(r["active_bands"], ["5g"])
        r = c.put("/api/wifi/primary", {"split_ssid": False})
        self.assertFalse(r["split_ssid"])
        self.assertEqual(r["ssid_5g"], r["ssid"])

    def test_primary_validation(self):
        c = self.c
        for body, field in (({"ssid": "x" * 33}, "ssid"), ({"ssid": ""}, "ssid"), ({"ssid": " lead"}, "ssid"),
                            ({"password": "short"}, "password"), ({"security": "wep"}, "security"),
                            ({"security": "bogus"}, "security"), ({"hidden": "yes"}, "hidden"), ({"bogus": 1}, "bogus"),
                            ({"ssid": c.get("/api/wifi/guest")["ssid"]}, "ssid"), ({"band": "2g"}, "band")):
            r = c.put("/api/wifi/primary", body, expect=400)
            self.assertEqual(r["error"]["details"]["field"], field, body)
        r = c.put("/api/wifi/primary", {"hidden": True, "wps": True}, expect=400)
        self.assertEqual(r["error"]["details"]["field"], "wps")
        r = c.put("/api/wifi/primary", {"hidden": True})       # WPS is switched off automatically
        self.assertTrue(r["hidden"])
        self.assertFalse(r["wps"])

    def test_guest(self):
        c = self.c
        r = c.put("/api/wifi/guest", {"enabled": True, "band": "5g"})
        self.assertEqual(r["active_bands"], ["5g"])
        a = c.get("/api/wifi/advanced")
        self.assertEqual((a["band_2g"], a["band_5g"]), ("primary", "guest"))
        r = c.put("/api/wifi/guest", {"block_admin": True, "block_file_sharing": True, "isolate_lan": False})
        self.assertTrue(r["block_admin"])
        self.assertTrue(r["block_file_sharing"])
        self.assertFalse(r["isolate_lan"])
        self.assertEqual(calls(c, "set_ap_profile")[-1]["answers"][7], str(1 | 8 | 16))
        r = c.put("/api/wifi/guest", {"enabled": False})
        self.assertEqual(r["active_bands"], [])
        self.assertEqual(c.get("/api/wifi/advanced")["band_5g"], "primary")
        r = c.put("/api/wifi/guest", {"show_ssid_on_screen": False})
        self.assertFalse(r["show_ssid_on_screen"])
        self.assertTrue(c.app.rest.get("WiFi/DisplaySettings")["HideSSIDTouchEnabledGuest"])

    def test_advanced_tiers_and_restore(self):
        c = self.c
        r = c.put("/api/wifi/advanced", {"enabled": False}, expect=428)
        self.assertEqual(r["error"]["details"]["tier"], 2)
        self.assertTrue(c.get("/api/wifi/advanced")["enabled"])
        self.assertFalse(c.put("/api/wifi/advanced", c.confirm({"enabled": False}))["enabled"])
        self.assertTrue(c.post("/api/wifi/advanced/restore")["enabled"])
        self.assertTrue(c.put("/api/wifi/advanced", {"enabled": True})["enabled"])   # turning on: no confirm

    def test_advanced_rest_and_cli_paths(self):
        c = self.c
        r = c.put("/api/wifi/advanced", {"channel_2g": 6, "mode_5g": "ac", "bandwidth_5g": 40, "max_clients": 20})
        self.assertEqual((r["channel_2g"], r["mode_5g"], r["bandwidth_5g"], r["max_clients"]), (6, "ac", 40, 20))
        self.assertEqual(c.app.rest.calls[-1]["path"], "WiFi/AdvancedSettings")
        self.assertFalse(calls(c, "set_ap_profile"))
        r = c.put("/api/wifi/advanced", {"mode_2g": "g"})                 # hidden legacy mode -> CLI, width 20
        self.assertEqual(r["mode_2g"], "g")
        self.assertEqual([x["answers"][0] for x in calls(c, "set_ap_profile")], ["1", "3"])
        self.assertEqual(calls(c, "set_ap_profile")[0]["answers"][8], "4")
        # REST now cannot round-trip (802.11g) -> band state and limit go through the CLI too
        n = len(c.app.rest.calls)
        r = c.put("/api/wifi/advanced", {"band_2g": "guest", "max_clients": 12})
        self.assertEqual((r["band_2g"], r["max_clients"]), ("guest", 12))
        self.assertEqual(len(c.app.rest.calls), n)
        self.assertTrue(calls(c, "set_profile_status"))
        self.assertEqual(calls(c, "set_ap_settings")[-1]["answers"], ["1", "12"])

    def test_advanced_validation(self):
        c = self.c
        for body, field in (({"channel_5g": 165}, "channel_5g"), ({"channel_2g": 13}, "channel_2g"),
                            ({"mode_2g": "ac"}, "mode_2g"), ({"bandwidth_2g": 80}, "bandwidth_2g"),
                            ({"max_clients": 0}, "max_clients"), ({"max_clients": 33}, "max_clients"),
                            ({"band_2g": "off", "band_5g": "off"}, "band_2g"),
                            ({"mode_5g": "a", "bandwidth_5g": 40}, "bandwidth_5g"), ({"band_2g": "x"}, "band_2g")):
            r = c.put("/api/wifi/advanced", body, expect=400)
            self.assertEqual(r["error"]["details"]["field"], field, body)
        r = c.put("/api/wifi/advanced", {"mode_5g": "an"})           # 80 MHz narrows to 40 automatically
        self.assertEqual(r["bandwidth_5g"], 40)

    def test_screen_and_tether(self):
        c = self.c
        r = c.put("/api/wifi/advanced", {"allow_screen_changes": False, "wifi_while_tethered": False})
        self.assertFalse(r["allow_screen_changes"])
        self.assertFalse(r["wifi_while_tethered"])
        self.assertEqual(calls(c, "set_wifi_while_usbteth")[-1]["answers"], ["0"])

    def test_country_tier3(self):
        c = self.c
        c.put("/api/wifi/country", {"country": "GB"}, expect=428)
        bad = dict(c.confirm({"country": "GB"}, 3), password="wrong")
        r = c.put("/api/wifi/country", bad, expect=403)
        self.assertEqual(r["error"]["code"], "reauth_failed")
        self.assertFalse(calls(c, "set_settings"))
        r = c.put("/api/wifi/country", c.confirm({"country": "GB"}, 3))
        self.assertEqual(r["country"], "GB")
        self.assertEqual(calls(c, "set_settings")[-1]["answers"], ["1", "GB"])
        self.assertEqual(c.get("/api/wifi/advanced")["channels_2g"], list(range(1, 14)))
        c.put("/api/wifi/country", c.confirm({"country": "JP"}, 3), expect=400)
        self.assertEqual(c.put("/api/wifi/country", {"country": "GB"})["country"], "GB")   # no change: no confirm

    def test_radio_pause(self):
        c = self.c
        c.post("/api/wifi/radio", {"on": False}, expect=428)
        self.assertTrue(c.post("/api/wifi/radio", c.confirm({"on": False}))["radio_paused"])
        self.assertEqual(calls(c, "set_wifi_ps")[-1]["answers"], ["0"])
        self.assertFalse(c.post("/api/wifi/radio", {"on": True})["radio_paused"])
        c.post("/api/wifi/radio", {}, expect=400)

    def test_wps(self):
        c = self.c
        r = c.post("/api/wifi/wps/start", {"network": "guest", "band": "2g"}, expect=400)
        self.assertEqual(r["error"]["details"]["field"], "band")
        c.put("/api/wifi/primary", {"wps": False})
        c.post("/api/wifi/wps/start", {"network": "primary", "band": "2g"}, expect=400)
        c.put("/api/wifi/primary", {"wps": True})
        c.post("/api/wifi/wps/start", {"network": "primary", "band": "2g", "method": "pbc"})
        self.assertEqual(calls(c, "start_wps")[-1]["answers"], ["1", "2", ""])
        c.post("/api/wifi/wps/start", {"network": "primary", "band": "5g", "method": "pin", "pin": "1234"})
        self.assertEqual(calls(c, "start_wps")[-1]["answers"], ["2", "1", "1234"])
        r = c.post("/api/wifi/wps/start", {"network": "primary", "band": "5g", "method": "pin", "pin": "12345678"}, expect=400)
        self.assertEqual(r["error"]["details"]["field"], "pin")
        self.assertTrue(c.get("/api/wifi/wps")["clients"])
        c.post("/api/wifi/wps/stop", {"network": "primary", "band": "2g"})
        self.assertEqual(calls(c, "stop_wps")[-1]["answers"], ["1"])

    def test_clients(self):
        c = self.c
        r = c.put("/api/clients/02%3A00%3A00%3A00%3A00%3A02".replace("%3A", ":"), {"name": "Anna"})
        self.assertEqual(r["name"], "Anna")
        self.assertTrue(r["vendor_synced"])
        self.assertEqual(calls(c, "setDeviceName")[-1]["answers"], ["02:00:00:00:00:02", "Anna"])
        r = c.post("/api/clients/02:00:00:00:00:02/block")
        self.assertTrue(r["blocked"])
        self.assertEqual(c.app.rest.calls[-1], {"method": "POST", "path": "BlockedDevices",
                                                "body": {"DeviceMacAddress": "02:00:00:00:00:02"}})
        lst = c.get("/api/clients")
        self.assertEqual([b["mac"] for b in lst["blocked"]], ["02:00:00:00:00:02"])
        self.assertEqual(lst["blocked"][0]["name"], "Anna")
        c.post("/api/clients/02:00:00:00:00:01/block", expect=400)        # self
        c.post("/api/clients/02:00:00:00:00:02/unblock")
        self.assertEqual(c.get("/api/clients")["blocked"], [])
        c.post("/api/clients/02:00:00:00:00:02/unblock", expect=404)
        c.put("/api/clients/zz", {"name": "x"}, expect=400)
        c.put("/api/clients/02:00:00:00:00:99", {"name": "x"}, expect=404)
        c.put("/api/clients/02:00:00:00:00:02", {"name": "a" * 33}, expect=400)
        self.assertEqual(c.put("/api/clients/limit", {"max_clients": 8})["max_clients"], 8)
        c.put("/api/clients/limit", {"max_clients": 99}, expect=400)

    def test_rename_survives_refused_vendor_write(self):
        c = self.c
        c.app.cli.allow_static_specs = False            # what a real unit does with unverified specs
        sp = cli_specs.spec("sysintcli", "setDeviceName")
        old = sp.source
        sp.source = "static"                             # (it passed the device sandbox; pretend it did not)
        try:
            r = c.put("/api/clients/02:00:00:00:00:03", {"name": "Tablet"})
        finally:
            sp.source = old
        self.assertEqual(r["name"], "Tablet")
        self.assertFalse(r["vendor_synced"])
        # start_wps is still only statically analysed -> refused with a clear error, nothing run
        if cli_specs.spec("wifi_cli", "start_wps").source == "static":
            r = c.post("/api/wifi/wps/start", {"network": "primary", "band": "2g"}, expect=403)
            self.assertEqual(r["error"]["code"], "unverified_setter")
            self.assertFalse(calls(c, "start_wps"))

    def test_sandbox_transcripts_match_specs(self):
        """The device sandbox transcripts (fixtures/sandbox) show every spec prompt in order."""
        import os
        from tests.helpers import FIXTURES
        from m3100ui.adapters.specs import wifi as wifi_specs
        for (cli, cmd), sp in wifi_specs.SPECS.items():
            if not sp.prompts or sp.mode != "stdin":
                continue
            path = os.path.join(FIXTURES, "sandbox", cli, cmd + ".txt")
            if not os.path.isfile(path) or (cli, cmd) == ("wifi_cli", "start_wps"):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            pos = 0
            for p in sp.prompts:
                i = text.find(p, pos)
                self.assertGreaterEqual(i, 0, "%s %s: prompt %r" % (cli, cmd, p))
                pos = i + len(p)
        with open(os.path.join(FIXTURES, "sandbox", "wifi_cli", "start_wps.txt"), encoding="utf-8") as f:
            t = f.read()
        sp = cli_specs.spec("wifi_cli", "start_wps")
        self.assertIn(sp.prompts[0] + sp.prompts[1], t)
        # the transcript was re-captured with the spec's PIN sample (method 1): the PIN prompt follows;
        # with the push-button method (2) it would be absent - either way it can only come third
        if sp.prompts[2] in t:
            self.assertGreater(t.index(sp.prompts[2]), t.index(sp.prompts[1]))

    def test_macfilter(self):
        c = self.c
        c.put("/api/wifi/macfilter", {"mode": "allow"}, expect=400)          # empty allow-list
        c.post("/api/wifi/macfilter/allowed", {"mac": "02:00:00:00:00:02", "name": "Phone"})
        c.post("/api/wifi/macfilter/allowed", {"mac": "02:00:00:00:00:02"}, expect=400)   # duplicate
        c.post("/api/wifi/macfilter/allowed", {"mac": "01:00:5e:00:00:01"}, expect=400)   # multicast
        r = c.put("/api/wifi/macfilter", {"mode": "allow"})
        self.assertEqual(r["mode"], "allow")
        self.assertTrue(r["wps_switched_off"])
        self.assertFalse(c.get("/api/wifi/primary")["wps"])
        self.assertEqual(calls(c, "setMacFilterWhiteListEnabled")[-1]["answers"], ["1"])
        c.put("/api/wifi/primary", {"wps": True}, expect=400)
        c.delete("/api/wifi/macfilter/allowed/02:00:00:00:00:02", expect=400)     # last entry while in use
        r = c.post("/api/wifi/macfilter/allow-self")
        self.assertTrue(r["self_allowed"])
        c.delete("/api/wifi/macfilter/allowed/" + r["self_mac"], expect=400)        # lock-out guard
        c.post("/api/wifi/wps/start", {"network": "primary", "band": "2g"}, expect=400)
        self.assertEqual(c.put("/api/wifi/macfilter", {"mode": "block"})["mode"], "block")
        c.delete("/api/wifi/macfilter/allowed/02:00:00:00:00:02")
        c.post("/api/wifi/macfilter/blocked", {"mac": "02:00:00:00:00:01"}, expect=400)  # self
        r = c.post("/api/wifi/macfilter/blocked", {"mac": "aa-bb-cc-dd-ee-ff"})
        self.assertEqual([b["mac"] for b in r["blocked"]], ["AA:BB:CC:DD:EE:FF"])
        r = c.delete("/api/wifi/macfilter/blocked/aa:bb:cc:dd:ee:ff")
        self.assertEqual(r["blocked"], [])
        c.delete("/api/wifi/macfilter/blocked/aa:bb:cc:dd:ee:ff", expect=404)
        c.put("/api/wifi/macfilter", {"mode": "both"}, expect=400)

    def test_allow_mode_requires_own_wifi_device(self):
        c = MockClient(ip="192.168.1.101")              # the phone, a Wi-Fi client
        c.post("/api/wifi/macfilter/allowed", {"mac": "02:00:00:00:00:03"})
        r = c.put("/api/wifi/macfilter", {"mode": "allow"}, expect=400)
        self.assertIn("using", r["error"]["message"])
        c.post("/api/wifi/macfilter/allow-self")
        self.assertEqual(c.put("/api/wifi/macfilter", {"mode": "allow"})["mode"], "allow")


class FallbackTests(unittest.TestCase):
    def test_rest_only_when_profiles_unreadable(self):
        c = MockClient()
        c.app.wifi.profiles = lambda: None
        p = c.get("/api/wifi/primary")
        self.assertEqual(p["source"], "rest")
        self.assertEqual(p["security"], "wpa2_wpa3")
        self.assertNotIn("wpa3", [o["value"] for o in p["security_options"]])
        self.assertNotIn("owe", [o["value"] for o in p["security_options"]])
        self.assertIsNone(c.get("/api/wifi/guest")["isolate_lan"])
        r = c.put("/api/wifi/primary", {"security": "wpa3"}, expect=503)
        self.assertEqual(r["error"]["code"], "unavailable")
        c.put("/api/wifi/guest", {"block_admin": True}, expect=503)
        self.assertEqual(c.put("/api/wifi/primary", {"ssid": "Fallback"})["ssid"], "Fallback")
        c.put("/api/wifi/advanced", {"mode_2g": "g"}, expect=503)
        self.assertEqual(c.put("/api/wifi/advanced", {"channel_2g": 11})["channel_2g"], 11)
        self.assertIsNone(c.get("/api/wifi/macfilter")["mode"])

    def test_rest_none_is_not_assumed_open(self):
        """REST reports WPA3/OWE as "None": without the CLI view a save must not silently open the network."""
        c = MockClient()
        c.put("/api/wifi/primary", {"security": "wpa3"})          # real state: WPA3 (REST shows "None")
        c.app.wifi.profiles = lambda: None
        c.app.cache.invalidate("wifi:")
        p = c.get("/api/wifi/primary")
        self.assertIsNone(p["security"])
        self.assertIn("Unknown", p["security_label"])
        n = len(c.app.rest.calls)
        r = c.put("/api/wifi/primary", {"ssid": "Renamed"}, expect=400)
        self.assertEqual(r["error"]["details"]["field"], "security")
        self.assertEqual(len(c.app.rest.calls), n)                 # nothing written
        c.put("/api/wifi/primary", {"show_ssid_on_screen": False})   # display-only change still fine
        r = c.put("/api/wifi/primary", {"ssid": "Renamed", "security": "wpa2_wpa3"})
        self.assertEqual((r["ssid"], r["security"]), ("Renamed", "wpa2_wpa3"))

    def test_unknown_cli_security_label_blocks_profile_writes(self):
        c = MockClient()
        m = wifi_mock._model_rest(c.app.rest)
        for i in (1, 2):
            m["profiles"][i]["security"] = 99                       # prints "INVALID"
        r = c.put("/api/wifi/primary", {"hidden": True}, expect=400)
        self.assertEqual(r["error"]["details"]["field"], "security")
        self.assertFalse(calls(c, "set_ap_profile"))

    def test_cli_only_when_rest_resource_missing(self):
        c = MockClient()
        real = c.app.wifi.rest_get
        c.app.wifi.rest_get = lambda path, ttl=5.0: None if path.startswith("WiFi/") else real(path, ttl)
        r = c.put("/api/wifi/primary", {"ssid": "Via CLI"})
        self.assertEqual(r["ssid"], "Via CLI")
        self.assertEqual([x["answers"][0] for x in calls(c, "set_ap_profile")], ["1", "2"])
        r = c.put("/api/wifi/advanced", {"channel_2g": 1, "max_clients": 9})
        self.assertEqual((r["channel_2g"], r["max_clients"]), (1, 9))
        self.assertEqual(calls(c, "set_ap_settings")[-1]["answers"], ["1", "9"])

    def test_unsupported_rest(self):
        c = MockClient()
        c.app.wifi.profiles = lambda: None
        c.app.wifi.rest_get = lambda path, ttl=5.0: None
        r = c.get("/api/wifi/primary", expect=503)
        self.assertEqual(r["error"]["code"], "unavailable")
        c.get("/api/wifi/advanced", expect=503)
        c.get("/api/wifi/macfilter", expect=503)


if __name__ == "__main__":
    unittest.main()
