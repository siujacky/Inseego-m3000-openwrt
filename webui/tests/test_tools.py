"""Tools area: network tests, modem info, field/lab test, PCO, sessions, audit, AT console, CLI explorer,
message bus, NV & EFS viewer.  Parsers are checked against the real (redacted) fixtures."""
import io
import json
import os
import unittest
import zipfile

from tests.helpers import FIXTURES, ROOT, MockClient, read_fixture

from m3100ui.adapters import at as at_mod
from m3100ui.adapters import cli_specs
from m3100ui.adapters.cli import CliResult
from m3100ui.errors import ApiError
from m3100ui.services import expert as expert_svc
from m3100ui.services import tools as tools_svc

AREA_KEYS = [("modem2_cli", "run_raw_command"), ("settings_cli", "set_modem_logging_settings"),
             ("settings_cli", "clear_modem_logs"), ("ccm2_cli", "accept_pco_event")]


def res(cli, cmd):
    return CliResult(cli, cmd, read_fixture(cli, cmd), 0)


class FixtureParserTests(unittest.TestCase):
    def test_diag_info_fixture(self):
        d = tools_svc.parse_diag_info(read_fixture("modem2_cli", "get_diag_info"))
        self.assertEqual(d["title"], "Diagnostic Data")
        items = {i["label"]: i for i in d["items"]}
        self.assertEqual(items["LTE Band Class"]["values"][:2], ["E_UTRA_BAND_1", "E_UTRA_BAND_2"])
        self.assertEqual(len(items["CDMA Band Class"]["values"]), 5)
        self.assertEqual(items["RSSI"]["value"], "[0]:-61")
        self.assertEqual(items["MIP Mode"]["value"], "MIP off")
        self.assertNotIn("returned", d["text"])
        self.assertGreater(len(d["items"]), 35)

    def test_diag_info_masks_identifiers_unless_revealed(self):
        text = ("cmd_get_diag_info returned 0 (MDM_MAIN: success.)\n\n[NAM: 1\n"
                "Mobile Directory Number (MDN): 5551234567\nIMSI_S: 1234567890\nRSSI: [0]:-61\n]")
        masked = tools_svc.parse_diag_info(text)
        mdn = next(i for i in masked["items"] if "MDN" in i["label"])
        self.assertTrue(mdn["value"].endswith("4567"))
        self.assertTrue(mdn.get("sensitive"))
        self.assertNotIn("5551234567", masked["text"])
        self.assertNotIn("1234567890", json.dumps(masked))
        shown = tools_svc.parse_diag_info(text, reveal=True)
        self.assertIn("5551234567", shown["text"])

    def test_pco_logging_ippt_autoconnect_tech_fixtures(self):
        r = res("modem2_cli", "get_pco_event")
        self.assertEqual((r.get_int("pco_value"), r.get_int("pdn")), (0, 0))
        self.assertEqual(tools_svc.pco_info(0)["status"], "none")
        self.assertEqual(tools_svc.pco_info(3)["status"], "out_of_credit")
        self.assertEqual(tools_svc.pco_info(9)["status"], "other")
        lg = tools_svc.modem_logging_from(res("settings_cli", "get_modem_logging_settings"))
        self.assertEqual((lg["enabled"], lg["file_size_mb"], lg["file_count"], lg["total_mb"]), (False, 25, 3, 75))
        ip = res("mifi_qcmap_cli", "get_ippt")
        self.assertEqual((ip.get_int("is active"), ip.get_int("interface")), (0, 2))
        self.assertEqual(res("mifi_qcmap_cli", "get_ippt_feature_mode").get_int("ippt feature mode is"), 0)
        ac = res("ccm2_cli", "get_wwan_ac")
        self.assertEqual(ac.get_int("autoconnect type"), 2)
        self.assertEqual(ac.note("autoconnect type"), "Autoconnect when service is available")
        tech = res("modem2_cli", "enabled_tech_get")
        self.assertEqual(tools_svc.tech_names(int(tech.get("enabled tech modes"))), ["UMTS", "LTE", "NR5G"])

    def test_msgbus_channels_fixture(self):
        names = res("msgbus_cli", "print_channels").indexed("list")
        self.assertEqual(len(names), 41)
        self.assertIn("modem2.conn_state", names)

    def test_parse_msgbus_shapes(self):
        head = "command [MsgBusGet]\n\nWhat is channel: MsgBusGet returned 0 (MSGBUS: success.)\nlast updated time:[Sat Oct 31 22:00:00 2026]\ndata type:["
        v = expert_svc.parse_msgbus(head + "int32]\ndata:[5]\n")
        self.assertEqual((v["ok"], v["type"], v["value"], v["updated"]), (True, "int32", 5, "Sat Oct 31 22:00:00 2026"))
        v = expert_svc.parse_msgbus(head + "string]\ndata:[Mock [x] Mobile]\n")
        self.assertEqual(v["value"], "Mock [x] Mobile")
        v = expert_svc.parse_msgbus(head + "binary]\nlen:[12] (bytes)\ndata:\n01 02 03 04 05 06 07 08 09 0a \n0b 0c \n")
        self.assertEqual((v["type"], v["length"]), ("binary", 12))
        self.assertEqual(v["hex"].split(), ["%02x" % i for i in range(1, 13)])
        v = expert_svc.parse_msgbus(head + "double]\ndata:[1.500000]\n")
        self.assertEqual(v["value"], 1.5)
        v = expert_svc.parse_msgbus("What is channel: MsgBusGet returned 1 (MSGBUS: unknown error.)\n")
        self.assertEqual((v["ok"], v["rc"]), (False, 1))

    def test_parse_efs_read_and_nv(self):
        ok = "command [read_file]\n\nAttempting to read file:[/x]\nFile read success, (Total bytes read:8)\nSuccess\n"
        self.assertEqual(expert_svc.parse_efs_read(ok), {"ok": True, "size": 8})
        bad = "command [read_file]\n\nAttempting to read file:[/x]\nFile read QMI failure:3\nread_efs_file() failed, rc=[3]\n"
        self.assertEqual(expert_svc.parse_efs_read(bad)["error"], "QMI error 3")
        self.assertFalse(expert_svc.parse_efs_read("")["ok"])
        self.assertEqual(expert_svc.parse_nv("02:00:00:00:00:01\n")["value"], "02:00:00:00:00:01")
        self.assertFalse(expert_svc.parse_nv("NV Item not supported\n")["ok"])
        self.assertEqual(expert_svc.interpret_nv("usbmode", "DEBUG"), "Debug USB mode (diagnostic / ADB ports)")
        self.assertEqual(expert_svc.interpret_nv("runlevel", "1 5"), "Flag 1, run level 5")

    def test_band_masks_from_unlock_rom_payloads(self):
        # M3100 value (docs/recon/rom-mods.md) and the 8800L mod payload
        self.assertEqual(expert_svc.band_bits(bytes.fromhex("5f18090800a00000")),
                         [1, 2, 3, 4, 5, 7, 12, 13, 17, 20, 28, 46, 48])
        mod = os.path.join(ROOT, "reference", "roms", "m8800l", "system", "2002442369", "rootfs", "opt",
                           "custom_files_by_operator555")
        if os.path.isdir(mod):
            with open(os.path.join(mod, "lte_bandpref_extn_65_256"), "rb") as f:
                self.assertEqual(expert_svc.decode_efs(f.read(), "auto", "/x/lte_bandpref_extn_65_256")["bands"], [66])
            with open(os.path.join(mod, "00029002"), "rb") as f:
                self.assertEqual(expert_svc.decode_efs(f.read(), "auto", "/nv/item_files/rfnv/00029002")["kind"], "zlib")
        self.assertEqual(expert_svc.decode_efs(b"\x00", "auto")["kind"], "flag")
        self.assertEqual(expert_svc.decode_efs(b"hello\nworld\x00", "auto")["kind"], "text")
        self.assertEqual(expert_svc.decode_efs(b"\x01\xff\x10", "auto")["kind"], "hex")

    def test_network_tool_parsers(self):
        p = tools_svc.parse_ping("PING 1.1.1.1 (1.1.1.1): 56 data bytes\n64 bytes from 1.1.1.1: seq=0 ttl=57 time=20.1 ms\n\n"
                                 "--- 1.1.1.1 ping statistics ---\n1 packets transmitted, 1 packets received, 0% packet loss\n"
                                 "round-trip min/avg/max = 20.1/20.1/20.1 ms\n")
        self.assertEqual((p["sent"], p["received"], p["loss_pct"], p["rtt_avg_ms"]), (1, 1, 0.0, 20.1))
        hops = tools_svc.parse_traceroute("traceroute to x (1.2.3.4), 20 hops max\n 1  10.0.0.1  3.2 ms\n 2  *\n")
        self.assertEqual([h["hop"] for h in hops], [1, 2])
        self.assertTrue(hops[1]["timeout"])
        self.assertEqual(tools_svc.parse_nslookup("Server: 127.0.0.1\nAddress 1: 127.0.0.1\n\nName: x\nAddress 1: 93.184.216.34\n", "x"),
                         ["93.184.216.34"])

    def test_sessions_parser(self):
        text = ("sys_getSessionLogStats returned 3, count:[2]\nsys_getSessionLogStats returned 0, count:[2]\n"
                "list[0].ipv4:[1.2.3.4]\nlist[0].ipv6:[]\nlist[0].roam_status:[2]\nlist[0].time:[100]\nlist[0].duration:[60]\n"
                "list[0].rx:[10]\nlist[0].tx:[5]\nlist[0].rxv6:[1]\nlist[0].txv6:[1]\n"
                "list[1].ipv4:[5.6.7.8]\nlist[1].roam_status:[1]\nlist[1].time:[200]\nlist[1].duration:[30]\n")
        out = tools_svc.parse_sessions(text)
        self.assertEqual((out["rc"], out["count"]), (0, 2))
        self.assertEqual(out["sessions"][0]["ipv4"], "5.6.7.8")          # newest first
        s = out["sessions"][1]
        self.assertEqual((s["rx_bytes"], s["tx_bytes"], s["total_bytes"], s["roaming"]), (11, 6, 17, "roaming"))

    def test_mask(self):
        self.assertEqual(tools_svc.mask("350000000000001"), "•" * 11 + "0001")
        self.assertEqual(tools_svc.mask(""), "")
        self.assertEqual(tools_svc.mask("N/A"), "N/A")


class AtPolicyTests(unittest.TestCase):
    def test_core_expectations(self):
        self.assertEqual(at_mod.classify("AT+CFUN?"), "ok")
        self.assertEqual(at_mod.classify("AT+CFUN=0"), "risky")
        self.assertEqual(at_mod.classify('at+qcfg="usbcfg",1'), "deny")
        self.assertEqual(at_mod.classify('AT;+EGMR=1,7,"1"'), "deny")
        self.assertEqual(at_mod.classify("ATI"), "ok")

    def test_red_line_and_excluded_operations_are_denied(self):
        for cmd in ("AT+USBSWITCH=9013", "AT+MODESWITCH=1", "AT+DIAG_ENABLE=1", "AT+EDL", "AT+EFS_ERASE", "AT+RESET",
                    "AT+FASTBOOT", "AT+FFOTADL=1", "AT+FFOTAUPGRADE", "AT$QCPWRDN", "AT$NV=1,2", "AT+QNVW=1,2,3",
                    'AT+CLCK="PN",1,"1234"', 'AT+CLCK="SC",0,"1234"', 'AT+CRSM=214,28539,0,0,1,"00"', "AT+CSIM=10,x",
                    "AT+ODIS=1", "AT$QCDMG", "at+cmux=0", "AT+CPOF", "AT$SPC=000000"):
            self.assertEqual(at_mod.classify(cmd), "deny", cmd)

    def test_read_atcmd_odis_emulation_is_blocked(self):
        # read_atcmd handles any command containing "AT+ODIS" itself; only the exact query is read-only
        self.assertEqual(at_mod.classify("AT+ODIS?"), "ok")
        for cmd in ("AT+ODIS", "AT+ODIS=1,2", 'AT+CMGS="AT+ODIS"', "AT+CSQ;+ODIS", "at+odis"):
            self.assertEqual(at_mod.classify(cmd), "deny", cmd)
        with self.assertRaises(ApiError):                      # read_atcmd's 255-byte argv buffer
            at_mod.normalise("AT+" + "X" * 248)
        at_mod.normalise("AT+" + "X" * 240)

    def test_queries_and_reads(self):
        for cmd in ("AT", "ATI", "AT+CSQ", "AT+CGMR", "AT+CGCONTRDP", "AT+CGCONTRDP=1", "AT+CGPADDR=1", "AT+CEER",
                    'AT+CLCK="SC",2', "AT+CLCK=?", "AT+MODEM?" , "AT$NWPREFMODE2?", "AT+CRSM=176,28539,0,0,12",
                    "AT+CMEE=2", "ATE0", "AT+CSQ;+COPS?"):
            self.assertEqual(at_mod.classify(cmd), "ok", cmd)

    def test_tiers(self):
        for cmd in ("AT+CFUN=1,1", "AT+COPS=0", 'AT+CGDCONT=1,"IP","apn"', "AT$NWPREFMODE2=50", "AT+CGATT=0",
                    "AT+CMGD=1", "ATH", "AT+CSQ;+CFUN=0"):
            self.assertEqual(at_mod.describe(cmd)["tier"], 2, cmd)
        for cmd in ('AT+CPIN="1234"', "ATD5551234;", "ATZ", "AT&F", "AT+FOO=1", "AT$QCFOO"):
            self.assertEqual(at_mod.describe(cmd)["tier"], 3, cmd)
        self.assertIn("SIM PIN", at_mod.describe('AT+CPIN="1234"')["reason"])
        self.assertEqual(at_mod.classify('AT+CSQ="x;+EGMR=1"'), "deny")   # splitting inside quotes stays strict

    def test_quick_commands_are_read_only(self):
        for cmd, _label in at_mod.QUICK:
            self.assertEqual(at_mod.classify(cmd), "ok", cmd)

    def test_normalise(self):
        for bad in ("", "CSQ", "AT+CSQ\r\nATZ", "AT+é", None, 5):
            with self.assertRaises(ApiError):
                at_mod.normalise(bad)
        self.assertEqual(at_mod.normalise("  AT+CSQ "), "AT+CSQ")

    def test_final_code_and_echo(self):
        self.assertEqual(at_mod.final_code("+CSQ: 1,99\r\n\r\nOK\r\n"), "OK")
        self.assertEqual(at_mod.final_code("+CME ERROR: 10"), "+CME ERROR: 10")
        self.assertIsNone(at_mod.final_code("+CSQ: 1,99"))
        self.assertEqual(at_mod._clean("AT+CSQ\r\n+CSQ: 1,99\r\n\r\nOK\r\n", "at+csq"), "+CSQ: 1,99\n\nOK")


class SpecTests(unittest.TestCase):
    def test_area_setters_have_exactly_one_spec(self):
        self.assertEqual({k: v for k, v in cli_specs.SPEC_ERRORS.items() if "tools" in v or k == "tools"}, {})
        for key in AREA_KEYS:
            self.assertEqual(cli_specs.classify(*key), "setter", key)
            self.assertIsNotNone(cli_specs.spec(*key), key)
        # reads used by this area need no spec
        for key in (("sysintcli", "getSessionLogStats"), ("msgbus_cli", "MsgBusGet"), ("modem2_cli", "get_diag_info"),
                    ("settings_cli", "get_modem_logging_settings"), ("modem2_cli", "get_pco_event")):
            self.assertEqual(cli_specs.classify(*key), "get", key)
        self.assertEqual(cli_specs.classify("nwcli", "qmi_idl", ["read_file", "/dev/shm/m3ui-efs-1", "/nvm/num/60002", "64"]), "get")
        self.assertEqual(cli_specs.classify("nwnvitem", "-r", ["-e", "NW_NV_MAC_ID_I"]), "get")

    def test_run_raw_command_spec(self):
        sp = cli_specs.spec("modem2_cli", "run_raw_command")
        self.assertEqual(sp.mode, "stdin")
        self.assertEqual(len(sp.prompts), 4)
        self.assertTrue(sp.prompts[0].startswith("Enter the command string"))
        sp.check(["AT+CSQ", "", "", "10"])
        with self.assertRaises(ApiError):
            sp.check(["AT+CSQ"])

    def test_answers_without_input_specs(self):
        for key in (("settings_cli", "clear_modem_logs"), ("ccm2_cli", "accept_pco_event")):
            sp = cli_specs.spec(*key)
            self.assertEqual(sp.mode, "argv", key)
            self.assertEqual(sp.argc, 0, key)
            sp.check([])
        cli_specs.spec("settings_cli", "set_modem_logging_settings").check(["1", "25", "3"])


class ToolsRouteTests(unittest.TestCase):
    def setUp(self):
        self.c = MockClient()
        self.calls = self.c.app.cli.store.calls

    def tearDown(self):
        self.c.cleanup()

    def test_modules_loaded(self):
        failed = getattr(self.c.app, "failed_modules", {})
        self.assertNotIn("m3100ui.services.tools", failed)
        self.assertNotIn("m3100ui.services.expert", failed)
        from m3100ui import mock_handlers
        self.assertNotIn("tools", mock_handlers.FAILED)

    def test_network_tests(self):
        r = self.c.post("/api/tools/ping", {"host": "1.1.1.1", "count": 2})
        self.assertTrue(r["ok"])
        self.assertEqual(r["summary"]["sent"], 2)
        r = self.c.post("/api/tools/traceroute", {"host": "example.com", "max_hops": 5})
        self.assertGreater(len(r["hops"]), 1)
        r = self.c.post("/api/tools/nslookup", {"host": "example.com", "server": "9.9.9.9"})
        self.assertEqual(r["addresses"], ["93.184.216.34"])
        cases = [("/api/tools/ping", {"host": "-x"}, "host"), ("/api/tools/ping", {"host": "1.1.1.1", "count": 21}, "count"),
                 ("/api/tools/ping", {}, "host"), ("/api/tools/traceroute", {"host": "a", "max_hops": 0}, "max_hops"),
                 ("/api/tools/nslookup", {"host": "ok.example", "server": "bad host"}, "server"),
                 ("/api/tools/ping", {"host": "1.1.1.1", "bogus": 1}, "bogus")]
        for path, body, fld in cases:
            e = self.c.post(path, body, expect=400)
            self.assertEqual(e["error"]["details"]["field"], fld, (path, body))

    def test_modem_info_masked_with_reveal(self):
        d = self.c.get("/api/tools/modem-info")
        for key in ("modem", "network", "legacy"):
            self.assertIn(key, d)
        self.assertNotIn("350000000000001", json.dumps(d))
        self.assertTrue(d["modem"]["imei"].endswith("0001"))
        self.assertIn(d["network"]["status"], ("connected", "available", "not_available", "radio_off", "no_sim"))
        self.assertEqual(d["network"]["enabled_tech"], ["UMTS", "LTE", "NR5G"])
        ids = self.c.get("/api/tools/modem-info/identifiers")
        self.assertEqual(ids["imei"], "350000000000001")

    def test_field_and_lab_test(self):
        d = self.c.get("/api/tools/field-test")
        self.assertEqual(d["title"], "Diagnostic Data")
        self.assertTrue(d["masked"])
        self.assertFalse(self.c.get("/api/tools/field-test", query={"reveal": "1"})["masked"])
        lab = self.c.get("/api/tools/lab-test")
        self.assertEqual(lab["technology"]["label"], "Auto (LTE/5G)")
        self.assertEqual([o["value"] for o in lab["technology"]["options"]], [50, 18, 32])
        self.assertEqual(lab["auto_connect"]["type"], 2)
        self.assertEqual(lab["ippt"], {"active": False, "interface": 2, "feature_mode": 0})
        self.assertEqual(lab["modem_logging"]["total_mb"], 75)

    def test_modem_logging_setter_answers(self):
        r = self.c.put("/api/tools/modem-logging", {"enabled": True, "file_size_mb": 20, "file_count": 2})
        call = self.calls[-1]
        self.assertEqual((call["cli"], call["cmd"]), ("settings_cli", "set_modem_logging_settings"))
        self.assertEqual(call["answers"], ["1", "20", "2"])
        cli_specs.spec("settings_cli", "set_modem_logging_settings").check(call["answers"])
        self.assertEqual((r["enabled"], r["file_size_mb"], r["file_count"], r["total_mb"]), (True, 20, 2, 40))
        r = self.c.put("/api/tools/modem-logging", {"enabled": False})              # partial: keeps size/count
        self.assertEqual(self.calls[-1]["answers"], ["0", "20", "2"])
        self.assertFalse(r["enabled"])
        self.assertEqual(self.c.app.state.audit_tail(1)[0]["action"], "tools.modem_logging.update")

    def test_modem_logging_validation(self):
        for body, fld in (({"file_count": 4}, "file_count"), ({"file_size_mb": 30, "file_count": 3}, "file_size_mb"),
                          ({"file_size_mb": 4, "file_count": 2}, "file_size_mb"), ({"enabled": "maybe"}, "enabled"),
                          ({"size": 1}, "size")):
            e = self.c.put("/api/tools/modem-logging", body, expect=400)
            self.assertEqual(e["error"]["details"]["field"], fld, body)

    def test_clear_logs_needs_tier2(self):
        e = self.c.post("/api/tools/modem-logging/clear", {}, expect=428)
        self.assertEqual(e["error"]["details"]["tier"], 2)
        self.c.post("/api/tools/modem-logging/clear", self.c.confirm({}))
        call = self.calls[-1]
        self.assertEqual((call["cmd"], call["answers"], call["args"]), ("clear_modem_logs", [], []))
        self.assertEqual(self.c.app.state.audit_tail(1)[0]["action"], "tools.modem_logs.clear")

    def test_pco(self):
        d = self.c.get("/api/tools/pco")
        self.assertEqual((d["pco_value"], d["status"], d["can_allow_access"]), (0, "none", False))
        self.c.app.cli.store.state["pco"] = (3, 1)
        d = self.c.get("/api/tools/pco")
        self.assertEqual((d["pco_value"], d["pdn"], d["status"]), (3, 1, "out_of_credit"))
        self.c.post("/api/tools/pco/accept", {})
        self.assertEqual((self.calls[-1]["cmd"], self.calls[-1]["answers"]), ("accept_pco_event", []))

    def test_sessions_and_export(self):
        d = self.c.get("/api/tools/sessions")
        self.assertEqual(d["count"], 3)
        self.assertGreater(d["totals"]["rx_bytes"], 0)
        r = self.c.get("/api/tools/sessions/export")
        self.assertTrue(r.body.startswith(b"start (UTC),duration"))
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.c.app.cli.store.state["sessions"] = []
        self.c.app.cache.invalidate("tools:sessions")
        self.assertEqual(self.c.get("/api/tools/sessions")["count"], 0)

    def test_audit(self):
        self.c.put("/api/tools/modem-logging", {"enabled": True})
        d = self.c.get("/api/tools/audit", query={"limit": "10"})
        self.assertEqual(d["entries"][0]["action"], "tools.modem_logging.update")
        self.assertEqual(self.c.get("/api/tools/audit", query={"q": "nothing-matches"})["count"], 0)
        self.c.get("/api/tools/audit", query={"limit": "x"}, expect=400)
        csv_ = self.c.get("/api/tools/audit/export", query={"format": "csv"})
        self.assertIn(b"tools.modem_logging.update", csv_.body)
        js = self.c.get("/api/tools/audit/export", query={"format": "json"})
        self.assertIsInstance(json.loads(js.body.decode("utf-8")), list)
        self.c.get("/api/tools/audit/export", query={"format": "xml"}, expect=400)


class ExpertRouteTests(unittest.TestCase):
    def setUp(self):
        self.c = MockClient()
        self.c.app.state.update_settings({"expert_mode": True})
        self.store = self.c.app.cli.store

    def tearDown(self):
        self.c.cleanup()

    def test_expert_mode_required(self):
        c = MockClient()
        try:
            for path in ("/api/expert/at", "/api/expert/cli", "/api/expert/msgbus/channels", "/api/expert/efs/presets",
                         "/api/expert/nv"):
                self.assertEqual(c.get(path, expect=403)["error"]["code"], "expert_required", path)
            c.post("/api/expert/at", {"command": "ATI"}, expect=403)
        finally:
            c.cleanup()

    def test_at_info_and_classify(self):
        d = self.c.get("/api/expert/at")
        self.assertIn(d["transport"], ("smd", "modem2"))
        self.assertTrue(all(at_mod.classify(q["command"]) == "ok" for q in d["quick"]))
        self.assertEqual(self.c.post("/api/expert/at/classify", {"command": "AT+CFUN=0"})["classification"], "risky")
        self.assertEqual(self.c.post("/api/expert/at/classify", {"command": "x"}, expect=400)["error"]["details"]["field"], "command")

    def test_at_query(self):
        r = self.c.post("/api/expert/at", {"command": "AT+CSQ"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["transport"], "smd" if at_mod.smd_available() else "modem2")
        self.assertTrue(r["response"].startswith("+CSQ:"))               # the modem's echo line is dropped
        self.assertEqual(r["final"], "OK")
        self.assertEqual(r["tier"], 0)
        self.c.post("/api/expert/at", {"command": "AT+CSQ", "timeout": 99}, expect=400)

    def test_at_tiers_deny_and_audit(self):
        e = self.c.post("/api/expert/at", {"command": "AT+CFUN=0"}, expect=428)
        self.assertEqual(e["error"]["details"]["tier"], 2)
        self.assertIn("radio power", e["error"]["message"])
        self.assertTrue(self.c.post("/api/expert/at", self.c.confirm({"command": "AT+CFUN=0"}))["ok"])
        e = self.c.post("/api/expert/at", {"command": 'AT+CPIN="1234"'}, expect=428)
        self.assertEqual(e["error"]["details"]["tier"], 3)
        self.c.post("/api/expert/at", self.c.confirm({"command": 'AT+CPIN="1234"'}, 2), expect=None)   # no password
        r = self.c.post("/api/expert/at", self.c.confirm({"command": 'AT+CPIN="1234"'}, 3))
        self.assertEqual(r["final"], "+CME ERROR: incorrect password")
        self.assertEqual(self.c.post("/api/expert/at", {"command": 'AT+EGMR=1,7,"1"'}, expect=403)["error"]["code"], "at_denied")
        log = json.dumps(self.c.app.state.audit_tail(20))
        self.assertIn("AT+CFUN=...", log)
        self.assertNotIn("1234", log)

    def test_at_password_is_checked(self):
        body = self.c.confirm({"command": "ATZ"})
        body["password"] = "wrong"
        r = self.c.call("POST", "/api/expert/at", body, expect=None)
        self.assertIn("error", r)

    def test_modem2_transport_answers_match_spec(self):
        out = self.c.app.at._via_modem2("AT+CSQ", 10)
        call = self.store.calls[-1]
        self.assertEqual((call["cli"], call["cmd"], call["answers"]), ("modem2_cli", "run_raw_command", ["AT+CSQ", "", "", "10"]))
        cli_specs.spec("modem2_cli", "run_raw_command").check(call["answers"])
        self.assertTrue(out["ok"])
        self.assertTrue(out["response"].startswith("+CSQ:"))
        self.store.state["at_stub"] = True                    # the real firmware's QMI back end is a stub
        out = self.c.app.at._via_modem2("ATI", 10)
        self.assertFalse(out["ok"])
        self.assertEqual(out["rc"], 1)
        self.assertIn("stub", out["hint"])

    def test_cli_catalogue_and_listing(self):
        d = self.c.get("/api/expert/cli")
        self.assertEqual(len(d["clis"]), len([f for f in os.listdir(os.path.join(ROOT, "reference", "cli-help", "clihelp"))
                                              if f.endswith(".txt")]) if os.path.isdir(os.path.join(ROOT, "reference")) else 50)
        m2 = {x["name"]: x for x in self.c.get("/api/expert/cli/modem2_cli")["commands"]}
        self.assertEqual(m2["get_signal"]["kind"], "get")
        self.assertEqual(m2["set_enabled_lte_bands"]["kind"], "setter")
        self.assertTrue(m2["set_enabled_lte_bands"]["spec"])
        self.assertEqual(m2["efs_write"]["kind"], "excluded")
        self.assertEqual(len(m2["run_raw_command"]["prompts"]), 4)
        self.c.get("/api/expert/cli/nosuch_cli", expect=404)

    def test_cli_run(self):
        r = self.c.post("/api/expert/cli/run", {"cli": "modem2_cli", "command": "get_bytes_counters", "answers": ["0"]})
        self.assertTrue(r["ok"])
        self.assertIn("tx_bytes", [p[0] for p in r["pairs"]])
        for cli, cmd in (("wifi_cli", "set_ap_settings"), ("modem2_cli", "efs_write"), ("omadm_cli", "get_x"),
                         ("modem2_cli", "factory_reset")):
            e = self.c.post("/api/expert/cli/run", {"cli": cli, "command": cmd}, expect=403)
            self.assertEqual(e["error"]["code"], "not_read_only", (cli, cmd))
        for body, fld in (({"cli": "rm -rf", "command": "x"}, "cli"), ({"cli": "bash", "command": "x"}, "cli"),
                          ({"cli": "modem2_cli", "command": "get;x"}, "command"),
                          ({"cli": "modem2_cli", "command": "get_info", "answers": ["1"] * 9}, "answers"),
                          ({"cli": "modem2_cli", "command": "get_info", "answers": ["a\nb"]}, "answers")):
            self.assertEqual(self.c.post("/api/expert/cli/run", body, expect=400)["error"]["details"]["field"], fld, body)

    def test_msgbus(self):
        d = self.c.get("/api/expert/msgbus/channels")
        self.assertEqual(d["count"], 41)
        v = self.c.get("/api/expert/msgbus/value", query={"channel": "modem2.conn_state"})
        self.assertEqual((v["ok"], v["type"], v["value"]), (True, "int32", 5))
        v = self.c.get("/api/expert/msgbus/value", query={"channel": "no.such.channel"})
        self.assertFalse(v["ok"])
        self.c.get("/api/expert/msgbus/value", query={"channel": "bad channel"}, expect=400)
        many = self.c.get("/api/expert/msgbus/values", query={"channels": "modem2.conn_state,ccm2.wan_stats"})
        self.assertEqual(many["values"]["ccm2.wan_stats"]["type"], "binary")
        self.assertEqual(len(self.c.get("/api/expert/msgbus/values", query={"channels": "all"})["values"]), 41)
        self.c.get("/api/expert/msgbus/values", query={"channels": ""}, expect=400)

    def test_efs_reads(self):
        d = self.c.get("/api/expert/efs", query={"path": "/nv/item_files/modem/mmode/lte_bandpref"})
        self.assertEqual(d["size"], 8)
        self.assertEqual(d["decoded"]["bands"], [1, 2, 3, 4, 5, 7, 12, 13, 17, 20, 28, 46, 48])
        d = self.c.get("/api/expert/efs", query={"path": "/nv/item_files/modem/mmode/lte_bandpref_extn_65_256"})
        self.assertEqual(d["decoded"]["bands"], [66])
        d = self.c.get("/api/expert/efs", query={"path": "/nv/item_files/rfnv/00029002"})
        self.assertEqual(d["decoded"]["kind"], "zlib")
        self.assertEqual(d["decoded"]["inflated_size"], 512)
        d = self.c.get("/api/expert/efs", query={"path": "/factory_backup/item_list.save", "max": "16"})
        self.assertEqual(d["size"], 16)
        self.assertTrue(d["possibly_truncated"])
        self.assertEqual(self.store.state.get("shm"), {})                 # local copies collected and removed
        self.c.get("/api/expert/efs", query={"path": "/nvm/num/60076"}, expect=503)
        for p in ("/nvm/num/60253", "/nvm/num/060004", "/nvm/num/85"):
            self.assertEqual(self.c.get("/api/expert/efs", query={"path": p}, expect=403)["error"]["code"], "secret_item")
        for p in ("nv/x", "/a/../b", "/a//b", "/a b", "/x;rm", ""):
            self.c.get("/api/expert/efs", query={"path": p}, expect=400)
        self.c.get("/api/expert/efs", query={"path": "/nvm/num/60002", "max": "999999"}, expect=400)
        call_args = [c for c in self.store.calls if c["cli"] == "nwcli"]
        self.assertEqual(call_args, [])                                     # reads are not setter calls

    def test_efs_download_and_backup(self):
        r = self.c.get("/api/expert/efs/download", query={"path": "/nvm/num/60002"})
        self.assertEqual(r.body, b"\x00")
        self.assertIn('filename="nvm_num_60002.bin"', r.headers["Content-Disposition"])
        z = zipfile.ZipFile(io.BytesIO(self.c.get("/api/expert/efs/backup").body))
        names = z.namelist()
        self.assertIn("nv/item_files/modem/mmode/lte_bandpref", names)
        manifest = json.loads(z.read("manifest.json"))
        self.assertEqual(len(manifest["files"]), len(expert_svc.EFS_PRESETS))
        self.assertTrue(any(not f["ok"] for f in manifest["files"]))        # 60076 is absent, like on the real unit

    def test_named_nv_items(self):
        d = self.c.get("/api/expert/nv")
        names = [i["name"] for i in d["items"]]
        self.assertNotIn("NW_NV_LINUX_ROOT_PASSWORD_I", names)
        r = self.c.get("/api/expert/nv/NW_NV_USB_DEFAULT_MODE_I")
        self.assertEqual((r["value"], r["id"]), ("EUM", 60256))
        self.assertEqual(r["meaning"], "Normal (end-user) USB mode")
        self.c.get("/api/expert/nv/NW_NV_MAC_ID_I", query={"index": "1"})
        self.c.get("/api/expert/nv/NW_NV_MAC_ID_I", query={"index": "999"}, expect=400)
        self.c.get("/api/expert/nv/NW_NV_LINUX_ROOT_PASSWORD_I", expect=400)
        self.c.get("/api/expert/nv/NW_NV_L", expect=400)                   # nwnvitem matches by prefix: never pass partial names


class CatalogueTests(unittest.TestCase):
    def test_cli_commands_json(self):
        path = os.path.join(ROOT, "server", "m3100ui", "data", "cli_commands.json")
        with open(path, encoding="utf-8") as f:
            clis = json.load(f)["clis"]
        self.assertGreaterEqual(len(clis), 50)
        self.assertGreater(sum(len(v) for v in clis.values()), 1400)
        m2 = {r["name"]: r for r in clis["modem2_cli"]}
        self.assertEqual(len(m2), 304)
        self.assertEqual(m2["get_packet_statistics"]["prompts"][1], "Enter ip_family (V4=0, V6=2):")
        self.assertTrue(all(set(r) >= {"name", "help"} for v in clis.values() for r in v))


if __name__ == "__main__":
    unittest.main()
