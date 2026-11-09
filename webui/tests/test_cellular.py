"""Cellular area: parsers on the real captures, setter specs, routes, tiers, validation, mock round trips."""
import os
import unittest

from tests.helpers import FIXTURES, MockClient, make_app, read_fixture

from m3100ui.adapters import cli_specs
from m3100ui.adapters.cli import CliResult
from m3100ui.adapters.specs import cellular as cell_specs
from m3100ui.adapters.specs import core as core_specs
from m3100ui.services import cellular_network
from m3100ui.services import cellular_parse as P
from m3100ui.services.cellular_signal import mask_diag

EXCLUDED_NEVER = {"network_attach", "set_5g_icon_mode", "enable_data_call", "efs_write", "efs_delete", "set_sim_lock",
                  "validate_spc", "mdn_min_set", "factory_reset", "modem_init", "set_lte_band_option",
                  "band_class_set_enabled"}


def fx(cli, cmd):
    return CliResult(cli, cmd, read_fixture(cli, cmd), 0)


def values_of(call):
    """What the Spec saw: argv specs deliver args, stdin specs answers."""
    return call["args"] if call["args"] else call["answers"]


class ParserTests(unittest.TestCase):
    def test_neighbors_fixture(self):
        nb = P.parse_neighbors(read_fixture("modem2_cli", "get_neighbor_list"))
        self.assertEqual(nb["intra"], [{"pci": 48, "rsrp": -99.8, "rsrq": -12.2, "rssi": -68.2}])
        self.assertEqual([c["pci"] for c in nb["inter"]], [48, 196, 278])
        self.assertEqual(nb["inter"][1]["rsrq"], -15.3)
        self.assertEqual(nb["wcdma"], [])
        self.assertEqual(nb["lte_from_wcdma"], [])      # float zero rows dropped

    def test_ca_and_arfcn(self):
        self.assertEqual(P.parse_ca_bands(read_fixture("modem2_cli", "get_ca_band_list")), [])
        text = ("ca_bands[0] - pci:[262]\nca_bands[0] - band:[66]\nca_bands[0] - earfcn:[66786]\nca_bands[0] - status:[2]\n"
                "ca_bands[0] - bandwidth:[5] Bandwidth is 20MHZ \nca_bands[0] - rsrp:[-920]\nca_bands[0] - rsrq:[-11]\n"
                "ca_bands[0] - rssi:[-67]\nca_bands[0] - snr:[14]\n")
        c = P.parse_ca_bands(text)[0]
        self.assertEqual((c["band"], c["bandwidth_mhz"], c["status"], c["dl_mhz"]), (66, 20, "active", 2145.0))
        self.assertEqual((c["rsrp"], c["rsrq"], c["rssi"], c["snr"]), (-92.0, -11, -67, 14))
        a = P.parse_arfcn_list(read_fixture("modem2_cli", "get_arfcn_list"))
        self.assertEqual(a, [{"index": 0, "dl_earfcn": 66586, "ul_earfcn": None, "band": 66, "dl_mhz": 2125.0}])

    def test_rates_txrx_lifetime(self):
        self.assertEqual(P.parse_data_rates(read_fixture("modem2_cli", "get_data_rates")),
                         {"cur_tx_kbps": 0, "cur_rx_kbps": 0, "max_tx_kbps": 0, "max_rx_kbps": 0})
        r = P.parse_data_rates("cur tx rate: [1A2B] Kb/s, cur rx rate [1D4C0] Kb/s, max tx rate [75000] Kb/s, "
                               "max rx rate [1200000] Kb/s")
        self.assertEqual((r["cur_tx_kbps"], r["cur_rx_kbps"], r["max_rx_kbps"]), (0x1A2B, 120000, 1200000))
        self.assertEqual(P.parse_tx_rx(read_fixture("modem2_cli", "get_tx_rx_info")), {"tx_power": None, "avg_rx_power": None})
        self.assertEqual(P.parse_lifetime(read_fixture("modem2_cli", "lifetime_counters_get")), {"rx_kb": 0, "tx_kb": 0})

    def test_mns(self):
        self.assertEqual(P.parse_mns_list(read_fixture("modem2_cli", "mns_get_list")), [])
        lst = P.parse_mns_list('cmd_get_mns_list returned 0 (MDM_MAIN: success.)\n\nMns list output:[+COPS: '
                               '(2,"311480","Home",7,0)\n+COPS: (1,"310410","Other",7,1)\n+COPS: (1,"31026","Old",2,1)\n]\n')
        self.assertEqual([(n["mccmnc"], n["status"], n["tech"], n["roaming"]) for n in lst],
                         [("311480", "current", "LTE", False), ("310410", "available", "LTE", True),
                          ("31026", "available", "UMTS", True)])
        self.assertEqual(P.parse_mns_validate("MNS:[0](disallowed, reverting to automatic network selection.)")["reverted"], True)
        self.assertEqual(P.parse_mns_validate("\nMNS:[1](validated.)\n"), {"code": 1, "message": "validated", "reverted": False})

    def test_band_lists_fixtures(self):
        self.assertEqual(len(P.band_list(fx("modem2_cli", "get_supported_band_list"))), 21)
        self.assertEqual(P.band_list(fx("modem2_cli", "get_enabled_lte_bands")),
                         [1, 2, 3, 4, 5, 7, 12, 13, 17, 20, 28, 46, 48, 66])
        self.assertEqual(P.band_list(fx("modem2_cli", "get_supported_nr5g_band_list")), [2, 5, 12, 26, 48, 66, 77, 78, 257, 258])
        self.assertIn(260, P.band_list(fx("modem2_cli", "get_enabled_nr5g_nsa_bands")))
        self.assertEqual(P.band_list(fx("modem2_cli", "get_configured_band_priority_list")), [])
        self.assertIsNone(P.band_list(None))

    def test_tech_masks(self):
        r = fx("modem2_cli", "enabled_tech_get")
        mask = P.to_int(r.get("Enabled tech modes"))
        self.assertEqual(mask, 50)
        self.assertEqual(P.mask_names(mask), ["UMTS", "LTE", "NR5G"])
        self.assertEqual(P.preset_for(50), "auto")
        self.assertEqual(P.preset_for(0x11), "custom")
        self.assertEqual(P.names_mask(["LTE", "NR5G"]), 0x30)
        self.assertEqual(P.to_int(fx("modem2_cli", "get_sup_tech").get("Available tech modes")), 95)

    def test_signal_and_5g(self):
        s = P.parse_signal(fx("modem2_cli", "get_signal"))
        self.assertEqual((s["tech_code"], s["tech"], s["rsrp"], s["mccmnc"], s["band"]), (0, "None", None, "", None))
        nr = P.parse_5g(fx("modem2_cli", "get_5g_status"))
        self.assertEqual((nr["service_available"], nr["mode"], nr["bearer"]), (False, "none", "not allocated"))
        ok = CliResult("modem2_cli", "get_5g_status", "cmd_get_5g_status returned 0 (MDM_MAIN: success.)\n"
                       "service_available:[1]\nendc:[1]\nband:[77]\nchannel:[650016]\nmode:[2] (NON-STAND ALONE)\n", 0)
        nr = P.parse_5g(ok)
        self.assertEqual((nr["mode"], nr["band"], nr["dl_mhz"]), ("NSA", 77, 3750.24))
        sa = CliResult("x", "y", "cmd_get_5g_status returned 0 (MDM_MAIN: success.)\nservice_available:[1]\nendc:[0]\n"
                       "band:[77]\nmode:[1] (STAND ALONE)\n", 0)
        self.assertEqual(P.parse_5g(sa)["mode"], "SA")

    def test_raw_pairs(self):
        raw = P.raw_pairs(fx("modem2_cli", "get_signal"))
        self.assertEqual(raw[0], ["tech", "0 (NONE)"])
        self.assertIn(["radio temp", "0"], raw)
        raw = P.raw_pairs(fx("modem2_cli", "get_neighbor_list"))
        self.assertIn(["cells[0] - rsrp", "-998"], raw)
        raw = P.raw_pairs(fx("modem2_cli", "get_data_rates"))
        self.assertEqual(len(raw), 1)       # multi-value line kept as text
        self.assertEqual(P.raw_pairs(fx("modem2_cli", "radio_is_enabled")), [["Radio enabled: (disabled=0, enabled=1)", "1"]])

    def test_efs_and_frequency_tables(self):
        # the value the mod-ROM analysis read from this unit (docs/recon/rom-mods.md)
        self.assertEqual(P.decode_lte_bandpref(bytes.fromhex("5f18090800a00000")),
                         [1, 2, 3, 4, 5, 7, 12, 13, 17, 20, 28, 46, 48])
        self.assertEqual(P.decode_lte_bandpref(bytes.fromhex("02" + "00" * 23), 65), [66])
        self.assertEqual(P.lte_earfcn_info(1050), {"band": 2, "dl_mhz": 1975.0})
        self.assertEqual(P.lte_earfcn_info(66586), {"band": 66, "dl_mhz": 2125.0})
        self.assertEqual(P.lte_earfcn_info(0), {})
        self.assertEqual(P.nr_arfcn_mhz(650016), 3750.24)
        self.assertEqual(P.nr_arfcn_mhz(2079167), 28000.08)
        self.assertEqual(P.cell_parts(0x0A1B2C3D), {"eci": 0xA1B2C3D, "enb": 0xA1B2C, "sector": 0x3D})
        self.assertEqual(P.REJECT_CAUSES[15], "No suitable cells in tracking area")

    def test_ccm2_parsers(self):
        self.assertEqual(P.parse_air_disable(fx("ccm2_cli", "get_airplane_mode_disable_status")),
                         {"mask": 3, "wifi": True, "cellular": True})
        self.assertEqual(P.to_int(fx("ccm2_cli", "get_wwan_ac").get("Autoconnect Type")), 2)
        self.assertEqual(P.AUTOCONNECT[2], "always")

    def test_mask_diag(self):
        text = "IMSI_S: \nMobile Network Code (MNC): 480\nMobile Directory Number (MDN): 5551234567\n"
        out = mask_diag(text)
        self.assertIn("Mobile Network Code (MNC): 480", out)
        self.assertIn("******4567", out)
        self.assertNotIn("5551234567", out)


class SpecTests(unittest.TestCase):
    def test_specs_loaded_and_unique(self):
        allsp = cli_specs.all_specs()
        for key, sp in cell_specs.SPECS.items():
            self.assertNotIn(key, core_specs.SPECS, key)
            self.assertIn(key, allsp, "%s dropped (duplicate in another area?): %s" % (key, cli_specs.SPEC_ERRORS))
            self.assertIs(allsp[key], sp)
            self.assertEqual(cli_specs.classify(*key), "setter", key)
            # declared "static"; fixtures/sandbox/verified.json (device sandbox run) may upgrade it
            self.assertIn(sp.source, ("static", "sandbox", "roundtrip"))
            self.assertTrue(sp.evidence)
            sp.check([str(x) for x in sp.sample_answers()])     # samples are well formed

    def test_argv_counts_match_handler_argc(self):
        # C argc compared by each handler minus argv[0..1]
        expect = {"radio_set_enabled": 1, "5g_radio_set_enabled": 1, "roam_set_enabled": 1, "roam_set_intl_enabled": 1,
                  "set_roam_guard_settings": 8, "mns_set_oper": 3, "ca_set_enabled": 1, "ca_bands_set_enabled": 3,
                  "ca_tri_bands_set_enabled": 4, "set_autonomous_gap_enabled": 1, "set_ext_ant_enabled": 2,
                  "set_lte_wifi_coex": 1, "set_5g_tuwb": 1, "mns_start_scan": 0, "mns_clear_list": 0, "mns_validate": 0}
        for cmd, n in expect.items():
            sp = cell_specs.SPECS[("modem2_cli", cmd)]
            self.assertEqual((sp.mode, sp.argc), ("argv", n), cmd)
        for cmd in ("set_air_mode_enabled", "set_cell_data_enabled", "set_wwan_ac"):
            self.assertEqual(cell_specs.SPECS[("ccm2_cli", cmd)].argc, 1)
        for cmd in ("wwan_connect", "wwan_disconnect"):
            sp = cell_specs.SPECS[("ccm2_cli", cmd)]
            self.assertEqual(sp.mode, "stdin")
            sp.check([""])                                       # empty line = default call is allowed on stdin
        self.assertEqual(cell_specs.SPECS[("webui_cli", "set_automatic_network_selection")].argc, 1)

    def test_priority_lists_may_be_cleared_but_band_lists_not(self):
        sp = cell_specs.SPECS[("modem2_cli", "set_configured_band_priority_list")]
        sp.check(["0"])
        sp.check(["2", "66", "2"])
        with self.assertRaises(Exception):
            sp.check(["2", "66"])
        with self.assertRaises(Exception):
            core_specs.SPECS[("modem2_cli", "set_enabled_lte_bands")].check(["0"])

    def test_tiers(self):
        tier2 = {"radio_set_enabled", "5g_radio_set_enabled", "mns_set_oper", "mns_start_scan", "mns_validate",
                 "ca_set_enabled", "ca_bands_set_enabled", "ca_tri_bands_set_enabled", "set_configured_band_priority_list",
                 "set_configured_nr5g_band_priority_list", "set_ext_ant_enabled", "set_lte_wifi_coex",
                 "set_autonomous_gap_enabled", "set_air_mode_enabled"}
        for (cli, cmd), sp in cell_specs.SPECS.items():
            self.assertEqual(sp.tier, 2 if cmd in tier2 else 1, cmd)


class _Base(unittest.TestCase):
    def setUp(self):
        self.c = MockClient()
        self.calls = self.c.app.cli.store.calls

    def tearDown(self):
        self.c.cleanup()

    def last(self, cmd):
        for call in reversed(self.calls):
            if call["cmd"] == cmd:
                return call
        self.fail("%s was not called" % cmd)

    def assert_specs_honoured(self):
        """Every setter call matched its Spec (count/mode) and nothing excluded was run."""
        for call in self.calls:
            self.assertNotIn(call["cmd"], EXCLUDED_NEVER)
            sp = cli_specs.spec(call["cli"], call["cmd"])
            if sp is None:
                continue
            if sp.mode == "argv":
                self.assertEqual(call["answers"], [], call)
                self.assertEqual(len(call["args"]), sp.argc, call)
            else:
                self.assertEqual(call["args"], [], call)
                sp.check(call["answers"])


class RouteTests(_Base):
    def test_get_routes_live_mock(self):
        sig = self.c.get("/api/cellular/signal")
        for k in ("summary", "lte", "nr", "ca", "neighbors", "rates", "counters", "raw", "reject_cause", "network_time"):
            self.assertIn(k, sig)
        self.assertEqual(sig["summary"]["tech"], "5G NSA")
        self.assertEqual(sig["summary"]["band_combo"], "B66 + B2 + n77")   # inactive SCC (B13) not counted
        self.assertEqual(sig["lte"]["enb"], 0xA1B2C)
        self.assertEqual(len(sig["ca"]["cells"]), 3)
        self.assertTrue(sig["ca"]["active"])
        self.assertGreater(sig["rates"]["max_rx_kbps"], 0)
        mode = self.c.get("/api/cellular/mode")
        self.assertEqual((mode["tech_preset"], mode["nr5g_mode"], mode["auto_connect_mode"]), ("auto", "nsa", "always"))
        self.assertEqual(mode["info"]["airplane_disables"], {"mask": 3, "wifi": True, "cellular": True})
        bands = self.c.get("/api/cellular/bands")
        self.assertEqual(bands["lte"]["enable_all_adds"], [8, 18, 19, 25, 26, 42, 43])   # rom-mods finding
        self.assertEqual(bands["nr_nsa"]["not_in_supported"], [260, 261])
        self.assertEqual(bands["lte"]["active"], [66, 2])          # B13 is configured but not active
        net = self.c.get("/api/cellular/network")
        self.assertEqual(net["mode"], "automatic")
        ant = self.c.get("/api/cellular/antenna")
        self.assertEqual((ant["external"], ant["external_source"], ant["mhb"], ant["uhb"]), (False, "rest", False, False))
        self.assertIn("status", ant)
        ov = self.c.get("/api/cellular/overview")
        self.assertTrue(ov["sim_present"])
        diag = self.c.get("/api/cellular/signal/diag")
        self.assertTrue(diag["ok"])
        self.assertIn("Diagnostic Data", diag["text"])

    def test_get_routes_on_real_no_sim_captures(self):
        self.c.app.cli.store.state["cellular.live"] = False
        self.c.app.rest.data["_mock_sim_status"] = "NotPresent"
        sig = self.c.get("/api/cellular/signal")
        self.assertEqual(sig["summary"]["state"], "no_sim")
        self.assertEqual(sig["summary"]["service_tech"], "None")
        self.assertEqual(sig["lte"]["earfcn"], 1050)                 # camped cell (limited service)
        self.assertEqual(sig["lte"]["band"], 2)                      # derived from the EARFCN
        self.assertEqual(sig["lte"]["serving_cell_id"], 48)
        self.assertEqual(sig["neighbors"]["intra"][0]["pci"], 48)
        life = sig["counters"]["lifetime"]                           # rc 1 on this unit (another mock may simulate it)
        self.assertTrue(life is None or set(life) == {"rx_kb", "tx_kb"})
        self.assertEqual(sig["ca"]["cells"], [])
        net = self.c.get("/api/cellular/network")
        self.assertIsNone(net["mns"])                                # rc 1000: SIM error
        self.assertTrue(any("SIM" in n for n in net["notes"]))
        bands = self.c.get("/api/cellular/bands")
        self.assertFalse(bands["ca"]["readable"])                    # ca_get_enabled: general error
        self.assertEqual(bands["priority"]["lte"], [])
        self.assertIsNone(bands["priority"]["lte_legacy"])
        ant = self.c.get("/api/cellular/antenna")
        self.assertIsNone(ant["autonomous_gap"])
        self.assertEqual(ant["status"]["autonomous_gap_message"], "general error")
        mode = self.c.get("/api/cellular/mode")
        self.assertIsNone(mode["tuwb"])                              # get_5g_tuwb: general error
        self.assertEqual(mode["info"]["icon_mode"]["code"], 4)

    def test_pages_and_features(self):
        pages = self.c.get("/api/ui/pages")
        for p in ("signal", "mode", "bands", "network", "antenna"):
            self.assertIn("/app/pages/cellular/%s.js" % p, pages)
        self.assertFalse(any(p.endswith("/_radio.js") for p in pages))
        feats = self.c.get("/api/meta/full")["features"]
        self.assertTrue(feats.get("cellular.radio"))
        self.assertTrue(feats.get("cellular.ext_antenna"))
        failed = self.c.app.failed_modules
        self.assertFalse([k for k in failed if "cellular" in k], failed)


class ModeTests(_Base):
    def test_tier_and_validation(self):
        self.c.put("/api/cellular/mode", {"tech_preset": "lte"}, expect=428)
        self.c.put("/api/cellular/mode", self.c.confirm({"bogus": 1}), expect=400)
        self.c.put("/api/cellular/mode", self.c.confirm({}), expect=400)
        e = self.c.put("/api/cellular/mode", self.c.confirm({"tech_preset": "custom", "tech_modes": []}), expect=400)
        self.assertEqual(e["error"]["details"]["field"], "tech_modes")
        e = self.c.put("/api/cellular/mode", {"tech_preset": "5g", "nr5g_mode": "nsa"}, expect=400)   # validated before 428
        self.assertEqual(e["error"]["details"]["field"], "nr5g_mode")
        self.c.put("/api/cellular/mode", self.c.confirm({"tech_modes": ["WIMAX"]}), expect=400)
        self.c.put("/api/cellular/mode", {"tuwb": 31}, expect=400)
        self.c.put("/api/cellular/mode", {"auto_connect_mode": "sometimes"}, expect=400)
        self.c.put("/api/cellular/mode", {"roam_guard": {"nope": True}}, expect=400)
        self.c.put("/api/cellular/mode", {"data_enabled": "maybe"}, expect=400)
        self.assertEqual(self.calls, [])

    def test_tech_and_nr_mode(self):
        d = self.c.put("/api/cellular/mode", self.c.confirm({"tech_preset": "lte"}))
        self.assertEqual(self.last("enabled_tech_set")["answers"], ["LTE"])
        self.assertEqual((d["tech_preset"], d["tech_modes"]), ("lte", ["LTE"]))
        self.assertEqual(d["info"]["previous"]["value"], {"tech_mask": 50, "nr5g_mode": "nsa"})
        d = self.c.put("/api/cellular/mode", self.c.confirm({"tech_preset": "custom", "tech_modes": ["LTE", "NR5G"],
                                                             "nr5g_mode": "sa_nsa"}))
        self.assertEqual(self.last("enabled_tech_set")["answers"], ["LTE,NR5G"])
        self.assertEqual(self.last("set_nr5g_disable_mode")["answers"], ["0"])
        self.assertEqual((d["tech_preset"], d["nr5g_mode"]), ("5g_lte", "sa_nsa"))
        d = self.c.post("/api/cellular/mode/restore", self.c.confirm({}))
        self.assertEqual((d["tech_modes"], d["nr5g_mode"]), (["LTE"], "nsa"))
        self.c.post("/api/cellular/mode/restore", {}, expect=428)
        self.assert_specs_honoured()

    def test_restore_without_snapshot(self):
        self.c.post("/api/cellular/mode/restore", self.c.confirm({}), expect=503)

    def test_radio_switches(self):
        d = self.c.put("/api/cellular/mode", self.c.confirm({"nr5g_radio": False}))
        self.assertEqual(self.last("5g_radio_set_enabled")["args"], ["0"])
        self.assertEqual((d["nr5g_radio"], d["tech_modes"]), (False, ["UMTS", "LTE"]))
        d = self.c.put("/api/cellular/mode", self.c.confirm({"radio": False}))
        self.assertEqual(self.last("radio_set_enabled")["args"], ["0"])
        self.assertFalse(d["radio"])
        self.c.put("/api/cellular/mode", {"airplane": True}, expect=428)
        d = self.c.put("/api/cellular/mode", self.c.confirm({"airplane": True}))
        self.assertEqual(self.last("set_air_mode_enabled")["args"], ["1"])
        self.assertTrue(d["airplane"])
        self.assert_specs_honoured()

    def test_data_roaming_autoconnect_roamguard_tuwb(self):
        d = self.c.put("/api/cellular/mode", {"data_enabled": False, "roaming_international": False})   # tier 1
        self.assertEqual(self.c.app.rest.calls[-1], {"method": "PUT", "path": "CellularServiceSettings",
                                                     "body": {"CellularDataEnabled": False, "InternationalRoamingEnabled": False}})
        self.assertEqual((d["data_enabled"], d["roaming_international"], d["roaming_domestic"]), (False, False, True))
        d = self.c.put("/api/cellular/mode", {"auto_connect_mode": "not_roaming"})
        self.assertEqual(self.last("set_wwan_ac")["args"], ["3"])
        self.assertEqual(d["auto_connect_mode"], "not_roaming")
        d = self.c.put("/api/cellular/mode", {"roam_guard": {"domestic_guard_enabled": True, "international_enabled": True}})
        self.assertEqual(self.last("set_roam_guard_settings")["args"], ["0", "0", "1", "0", "1", "0", "0", "0"])
        self.assertTrue(d["roam_guard"]["domestic_guard_enabled"])
        d = self.c.put("/api/cellular/mode", {"tuwb": 12})
        self.assertEqual((self.last("set_5g_tuwb")["args"], d["tuwb"]), (["12"], 12))
        self.assert_specs_honoured()

    def test_cli_fallback_when_rest_unavailable(self):
        del self.c.app.rest.data["CellularServiceSettings"]
        d = self.c.get("/api/cellular/mode")
        self.assertEqual(d["info"]["settings_source"], "cli")
        self.c.put("/api/cellular/mode", {"data_enabled": False, "roaming_domestic": False, "auto_connect": False})
        self.assertEqual(self.last("set_cell_data_enabled")["args"], ["0"])
        self.assertEqual(self.last("roam_set_enabled")["args"], ["0"])
        self.assertEqual(self.last("set_wwan_ac")["args"], ["0"])
        d = self.c.get("/api/cellular/mode")
        self.assertEqual((d["data_enabled"], d["roaming_domestic"], d["auto_connect"]), (False, False, False))
        self.assert_specs_honoured()

    def test_connect_disconnect(self):
        r = self.c.post("/api/cellular/disconnect")
        self.assertEqual(self.last("wwan_disconnect")["answers"], [""])
        self.assertEqual(r["connection"]["ipv4_state"], "Waiting" if "network" in self.c.app.cli.store.state else "Idle")
        r = self.c.post("/api/cellular/connect")
        self.assertEqual(self.last("wwan_connect")["answers"], [""])
        self.assertEqual(r["connection"]["ipv4_state"], "Connected")
        self.c.post("/api/cellular/reconnect")
        self.assertEqual([c["cmd"] for c in self.calls[-2:]], ["wwan_disconnect", "wwan_connect"])
        self.assert_specs_honoured()

    def test_static_specs_refused_on_real_unit(self):
        self.c.app.cli.allow_static_specs = False          # what a real unit does for specs not yet verified
        sp = cli_specs.spec("modem2_cli", "radio_set_enabled")
        old = sp.source
        sp.source = "static"
        try:
            e = self.c.put("/api/cellular/mode", self.c.confirm({"radio": False}), expect=403)
            self.assertEqual(e["error"]["code"], "unverified_setter")
        finally:
            sp.source = old
        self.c.put("/api/cellular/mode", self.c.confirm({"tech_preset": "lte"}))   # core spec: sandbox verified
        self.c.put("/api/cellular/mode", {"data_enabled": False})                  # REST: no spec needed

    def test_sandbox_verification_records(self):
        """fixtures/sandbox/verified.json (device sandbox run, if present) must match these prompts exactly."""
        import json
        path = os.path.join(FIXTURES, "sandbox", "verified.json")
        if not os.path.isfile(path):
            self.skipTest("no sandbox verification recorded")
        with open(path, encoding="utf-8") as f:
            recs = {(r["cli"], r["cmd"]): r for r in json.load(f)}
        for key, sp in cell_specs.SPECS.items():
            rec = recs.get(key)
            if rec is not None:
                self.assertEqual(rec.get("prompts"), sp.prompts, key)
                self.assertEqual(rec.get("mode"), sp.mode, key)


class BandTests(_Base):
    def test_set_validate_restore(self):
        self.c.put("/api/cellular/bands", {"lte": [2, 4, 66]}, expect=428)
        self.c.put("/api/cellular/bands", self.c.confirm({"lte": []}), expect=400)
        e = self.c.put("/api/cellular/bands", self.c.confirm({"lte": [2, 999]}), expect=400)
        self.assertEqual(e["error"]["details"]["field"], "lte")
        self.c.put("/api/cellular/bands", self.c.confirm({"bogus": [1]}), expect=400)
        self.c.put("/api/cellular/bands", self.c.confirm({}), expect=400)
        d = self.c.put("/api/cellular/bands", self.c.confirm({"lte": [66, 2, 4]}))
        self.assertEqual(self.last("set_enabled_lte_bands")["answers"], ["3", "2", "4", "66"])
        self.assertEqual(d["lte"]["enabled"], [2, 4, 66])
        self.assertEqual(d["lte"]["previous"]["value"], [1, 2, 3, 4, 5, 7, 12, 13, 17, 20, 28, 46, 48, 66])
        self.assertEqual(d["lte"]["original"], [1, 2, 3, 4, 5, 7, 12, 13, 17, 20, 28, 46, 48, 66])
        d = self.c.post("/api/cellular/bands/restore", self.c.confirm({"list": "lte", "source": "previous"}))
        self.assertEqual(d["lte"]["enabled"], [1, 2, 3, 4, 5, 7, 12, 13, 17, 20, 28, 46, 48, 66])
        d = self.c.post("/api/cellular/bands/restore", self.c.confirm({"list": "lte"}))     # undo the undo
        self.assertEqual(d["lte"]["enabled"], [2, 4, 66])
        d = self.c.post("/api/cellular/bands/restore", self.c.confirm({"list": "all", "source": "original"}))
        self.assertEqual(d["lte"]["enabled"], [1, 2, 3, 4, 5, 7, 12, 13, 17, 20, 28, 46, 48, 66])
        self.c.post("/api/cellular/bands/restore", self.c.confirm({"list": "lte", "source": "bogus"}), expect=400)
        self.assert_specs_honoured()

    def test_nsa_keeps_enabled_but_unsupported_bands(self):
        d = self.c.put("/api/cellular/bands", self.c.confirm({"nr_nsa": [77, 78, 260]}))
        self.assertEqual(self.last("set_enabled_nr5g_nsa_bands")["answers"], ["3", "77", "78", "260"])
        self.assertEqual(d["nr_nsa"]["enabled"], [77, 78, 260])
        self.c.put("/api/cellular/bands", self.c.confirm({"nr_sa": [77, 260]}), expect=400)   # 260 not supported in SA

    def test_restore_nothing_yet(self):
        self.c.post("/api/cellular/bands/restore", self.c.confirm({"list": "lte"}), expect=503)

    def test_enable_all_supported(self):
        self.c.post("/api/cellular/bands/enable-all", {}, expect=428)
        d = self.c.post("/api/cellular/bands/enable-all", self.c.confirm({}))
        self.assertEqual(d["lte"]["enabled"], [1, 2, 3, 4, 5, 7, 8, 12, 13, 17, 18, 19, 20, 25, 26, 28, 42, 43, 46, 48, 66])
        self.assertEqual(d["changed"], ["lte"])               # NR lists already contain every supported band
        self.assertEqual(d["lte"]["enable_all_adds"], [])
        self.assertIn(260, d["nr_nsa"]["enabled"])           # union: never removes a band
        self.c.post("/api/cellular/bands/enable-all", self.c.confirm({"lists": ["wifi"]}), expect=400)
        self.assert_specs_honoured()

    def test_priority(self):
        self.c.put("/api/cellular/bands/priority", {"lte": [66, 2]}, expect=428)
        d = self.c.put("/api/cellular/bands/priority", self.c.confirm({"lte": [66, 2], "nr": [77]}))
        self.assertEqual(self.last("set_configured_band_priority_list")["answers"], ["2", "66", "2"])
        self.assertEqual(self.last("set_configured_nr5g_band_priority_list")["answers"], ["1", "77"])
        self.assertEqual((d["priority"]["lte"], d["priority"]["nr"]), ([66, 2], [77]))
        d = self.c.put("/api/cellular/bands/priority", self.c.confirm({"lte": []}))
        self.assertEqual(self.last("set_configured_band_priority_list")["answers"], ["0"])
        self.assertEqual(d["priority"]["lte"], [])
        self.assertEqual(d["priority"]["previous"]["lte"]["value"], [66, 2])
        self.c.put("/api/cellular/bands/priority", self.c.confirm({"lte": [999]}), expect=400)
        self.assert_specs_honoured()

    def test_carrier_aggregation(self):
        self.c.put("/api/cellular/ca", {"enabled": False}, expect=428)
        self.c.put("/api/cellular/ca", self.c.confirm({}), expect=400)
        d = self.c.put("/api/cellular/ca", self.c.confirm({"enabled": False}))
        self.assertEqual(self.last("ca_set_enabled")["args"], ["0"])
        self.assertEqual((d["enabled"], d["readable"]), (False, True))
        r = self.c.get("/api/cellular/ca/combo", query={"bands": "2,66"})
        self.assertEqual((r["bands"], r["enabled"]), ([2, 66], True))
        r = self.c.put("/api/cellular/ca/combo", self.c.confirm({"bands": [2, 66], "enabled": False}))
        self.assertEqual(self.last("ca_bands_set_enabled")["args"], ["2", "66", "0"])
        self.assertFalse(r["enabled"])
        r = self.c.put("/api/cellular/ca/combo", self.c.confirm({"bands": [2, 66, 13], "enabled": True}))
        self.assertEqual(self.last("ca_tri_bands_set_enabled")["args"], ["2", "66", "13", "1"])
        self.c.get("/api/cellular/ca/combo", query={"bands": "2"}, expect=400)
        self.c.get("/api/cellular/ca/combo", query={"bands": "2,999"}, expect=400)
        self.c.put("/api/cellular/ca/combo", {"bands": [2, 66], "enabled": True}, expect=428)
        self.assert_specs_honoured()

    def test_efs_decode_matches_enabled_list(self):
        d = self.c.get("/api/cellular/bands/efs")
        self.assertEqual(d["bands"], [1, 2, 3, 4, 5, 7, 12, 13, 17, 20, 28, 46, 48, 66])
        self.assertEqual(d["files"][0]["hex"], "5f18090800a00000")
        self.c.put("/api/cellular/bands", self.c.confirm({"lte": [2, 66]}))
        self.assertEqual(self.c.get("/api/cellular/bands/efs")["bands"], [2, 66])


class NetworkTests(_Base):
    def tearDown(self):
        t = getattr(self.c.app.cellular_scan, "thread", None)
        if t is not None:
            t.join(5)
        super().tearDown()

    def start_scan(self):
        r = self.c.post("/api/cellular/network/scan", self.c.confirm({}))
        self.c.app.cellular_scan.thread.join(5)
        return r

    def test_scan_select_validate_automatic(self):
        self.c.post("/api/cellular/network/scan", {}, expect=428)
        self.c.app.cli.store.state["cellular.scan_s"] = 0
        r = self.start_scan()
        self.assertIn(r["state"], ("starting", "scanning"))
        r = self.c.get("/api/cellular/network/scan")
        self.assertEqual(r["state"], "complete")
        self.assertEqual([n["mccmnc"] for n in r["networks"]], ["311480", "310410", "312530", "310260"])
        self.assertEqual([c["cmd"] for c in self.calls if c["cmd"].startswith("mns_")][:2], ["mns_clear_list", "mns_start_scan"])
        self.c.post("/api/cellular/network/select", self.c.confirm({"mccmnc": "31a410", "act": 7}), expect=400)
        self.c.post("/api/cellular/network/select", self.c.confirm({"mccmnc": "310410", "act": 11}), expect=400)
        self.c.post("/api/cellular/network/select", {"mccmnc": "310410", "act": 7}, expect=428)
        d = self.c.post("/api/cellular/network/select", self.c.confirm({"mccmnc": "310410", "act": 7, "name": "Other"}))
        self.assertEqual(self.last("mns_set_oper")["args"], ["1", "310410", "7"])
        self.assertEqual(self.last("set_automatic_network_selection")["args"], ["0"])
        self.assertEqual((d["mode"], d["mns"]["selected"], d["last_selection"]["mccmnc"]), ("manual", True, "310410"))
        d = self.c.post("/api/cellular/network/validate", self.c.confirm({}))
        self.assertEqual(d["validation"], {"code": 1, "message": "validated", "reverted": False})
        self.c.put("/api/cellular/network", {"mode": "automatic"}, expect=428)
        d = self.c.put("/api/cellular/network", self.c.confirm({"mode": "automatic"}))
        self.assertEqual([c["cmd"] for c in self.calls[-3:]], ["set_automatic_network_selection", "mns_set_oper", "mns_clear_list"])
        self.assertEqual(self.calls[-2]["args"], ["0", "310410", "7"])
        self.assertEqual((d["mode"], d["mns"]["selected"]), ("automatic", False))
        d = self.c.put("/api/cellular/network", {"mode": "manual"})                   # tier 1: setting only
        self.assertEqual((d["mode"], self.last("set_automatic_network_selection")["args"]), ("manual", ["0"]))
        self.c.put("/api/cellular/network", {"mode": "sideways"}, expect=400)
        self.c.post("/api/cellular/network/clear", {})
        self.assertEqual(self.c.get("/api/cellular/network/scan")["networks"], [])
        self.assert_specs_honoured()

    def test_scan_conflict_and_timeout(self):
        self.c.app.cli.store.state["cellular.scan_s"] = 3600
        self.start_scan()
        self.assertEqual(self.c.get("/api/cellular/network/scan")["state"], "scanning")
        self.c.post("/api/cellular/network/scan", self.c.confirm({}), expect=409)
        old = cellular_network.SCAN_BUDGET_S
        cellular_network.SCAN_BUDGET_S = 0.0
        try:
            r = self.c.get("/api/cellular/network/scan")
        finally:
            cellular_network.SCAN_BUDGET_S = old
        self.assertEqual(r["state"], "timeout")

    def test_scan_failure_without_sim(self):
        from m3100ui.adapters import mock as M
        self.c.app.cli.store.state["cellular.live"] = False          # no-SIM captures: mns_get_info rc 1000
        key = ("modem2_cli", "mns_start_scan")
        old = M.CLI_HANDLERS[key]
        M.CLI_HANDLERS[key] = lambda store, a, g: ("cmd_start_mns_scan returned 1000 "
                                                   "(MDM_MAIN: an error reading from the sim was encountered.)\n")
        try:
            self.start_scan()
        finally:
            M.CLI_HANDLERS[key] = old
        r = self.c.get("/api/cellular/network/scan")
        self.assertEqual(r["state"], "failed")                       # reported, not raised
        self.assertIn("sim", r["error"].lower())
        self.assertIsNone(self.c.get("/api/cellular/network")["mns"])


class AntennaTests(_Base):
    def test_antenna_and_coex(self):
        self.c.put("/api/cellular/antenna", {"external": True}, expect=428)
        self.c.put("/api/cellular/antenna", self.c.confirm({}), expect=400)
        self.c.put("/api/cellular/antenna", self.c.confirm({"external": "yes"}), expect=400)
        d = self.c.put("/api/cellular/antenna", self.c.confirm({"external": True}))
        self.assertEqual(self.c.app.rest.calls[-1], {"method": "PUT", "path": "Antenna/External/Enabled", "body": {"Enabled": 1}})
        self.assertTrue(d["external"])
        d = self.c.put("/api/cellular/antenna", self.c.confirm({"uhb": True}))
        self.assertEqual(self.last("set_ext_ant_enabled")["args"], ["0", "1"])     # mhb kept, uhb changed
        self.assertEqual((d["mhb"], d["uhb"]), (False, True))
        d = self.c.put("/api/cellular/antenna", self.c.confirm({"lte_u": False, "autonomous_gap": True}))
        self.assertEqual(self.last("set_lte_wifi_coex")["args"], ["0"])
        self.assertEqual(self.last("set_autonomous_gap_enabled")["args"], ["1"])
        self.assertEqual((d["lte_u"], d["autonomous_gap"]), (False, True))
        self.assertIsNotNone(d["lte_u_set_at"])
        self.assert_specs_honoured()

    def test_external_fallback_to_cli(self):
        del self.c.app.rest.data["Antenna/External/Enabled"]
        d = self.c.get("/api/cellular/antenna")
        self.assertEqual(d["external_source"], "cli")
        self.c.put("/api/cellular/antenna", self.c.confirm({"external": True}))
        self.assertEqual(self.last("set_ext_ant_enabled")["args"], ["1", "1"])
        self.assertTrue(self.c.get("/api/cellular/antenna")["external"])

    def test_throttle_check(self):
        r = self.c.get("/api/cellular/antenna/throttle", query={"code": "33"})
        self.assertEqual(r, {"code": 33, "throttled": False})
        self.c.get("/api/cellular/antenna/throttle", query={"code": "70000"}, expect=400)
        self.c.get("/api/cellular/antenna/throttle", query={"code": "x"}, expect=400)


class FixtureCoverageTests(unittest.TestCase):
    def test_every_getter_used_has_a_capture(self):
        # the read commands the cellular services call must exist in the real captures (shape source of truth)
        used = ["get_signal", "get_5g_status", "get_ca_band_list", "get_data_rates", "get_tx_rx_info", "get_service_info",
                "get_neighbor_list", "get_arfcn_list", "get_oper_info", "get_ecgi", "get_serving_cell_id", "get_earfcn",
                "get_5g_band_pci", "active_band_get", "active_channel_get", "get_bandwidth", "get_aggregated_bandwidth",
                "get_rach_fail", "get_reject_cause_code", "get_network_time", "lifetime_counters_get", "validate_home",
                "check_lte_ca_status", "get_bytes_counters", "get_packet_statistics", "enabled_tech_get", "get_sup_tech",
                "active_tech_get", "get_nr5g_disable_mode", "5g_radio_is_enabled", "radio_is_enabled",
                "get_roam_guard_settings", "get_5g_icon_mode", "get_5g_tuwb", "get_supported_band_list",
                "get_supported_nr5g_band_list", "get_enabled_lte_bands", "get_enabled_nr5g_sa_bands",
                "get_enabled_nr5g_nsa_bands", "get_configured_band_priority_list",
                "get_configured_nr5g_band_priority_list", "lte_band_get_prior", "ca_get_enabled", "ca_bands_get_enabled",
                "ca_tri_bands_get_enabled", "mns_get_info", "mns_get_list", "get_ext_ant_enabled",
                "get_autonomous_gap_enabled", "get_call_throttle_status", "get_world_mode_enabled", "get_test_sim_status",
                "emergency_get_mode", "get_dss", "bsr_get_timers", "get_pco_event", "get_lte_embms_info", "get_diag_info",
                "roam_get_enabled", "roam_get_intl_enabled"]
        for cmd in used:
            self.assertTrue(os.path.isfile(os.path.join(FIXTURES, "cli", "modem2_cli", cmd + ".txt")), cmd)
            self.assertEqual(cli_specs.classify("modem2_cli", cmd), "get", cmd)
        for cmd in ("get_air_mode_enabled", "get_airplane_mode_disable_status", "get_wwan_ac", "get_cell_data_enabled",
                    "get_call_throttle_state", "get_conn_state"):
            self.assertTrue(os.path.isfile(os.path.join(FIXTURES, "cli", "ccm2_cli", cmd + ".txt")), cmd)
        self.assertTrue(os.path.isfile(os.path.join(FIXTURES, "cli", "webui_cli", "get_automatic_network_selection.txt")))

    def test_app_starts_without_cellular_problems(self):
        app = make_app()
        self.assertFalse([k for k in app.failed_modules if "cellular" in k])
        from m3100ui import mock_handlers
        self.assertNotIn("cellular", mock_handlers.FAILED)


if __name__ == "__main__":
    unittest.main()
