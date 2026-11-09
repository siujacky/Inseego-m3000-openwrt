"""Core framework tests: CLI parser on every captured output, setter policy, auth, CSRF, routing."""
import glob
import os
import unittest

from tests.helpers import FIXTURES, MockClient, make_app, read_fixture

from m3100ui.adapters import at as at_mod
from m3100ui.adapters import cli_specs
from m3100ui.adapters.cli import CliResult, parse_pairs, parse_status
from m3100ui.errors import ApiError


class ParserTests(unittest.TestCase):
    def test_every_capture_parses(self):
        files = glob.glob(os.path.join(FIXTURES, "cli", "*", "*.txt"))
        self.assertGreater(len(files), 300)
        for f in files:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
            parse_status(text)
            parse_pairs(text)  # must not raise

    def test_shapes(self):
        r = CliResult("modem2_cli", "get_info", read_fixture("modem2_cli", "get_info"), 0)
        self.assertTrue(r.ok)
        self.assertEqual(r.get("Model"), "M3100")
        self.assertTrue(r.get("FW Version").startswith("THN-1.54"))
        r = CliResult("mifi_qcmap_cli", "get_lan_dhcp_settings", read_fixture("mifi_qcmap_cli", "get_lan_dhcp_settings"), 0)
        self.assertEqual(r.get("LAN IP address is"), "192.168.1.1")
        self.assertEqual(r.all("DHCP IP range"), ["192.168.1.2", "192.168.1.102"])
        r = CliResult("modem2_cli", "get_enabled_lte_bands", read_fixture("modem2_cli", "get_enabled_lte_bands"), 0)
        self.assertEqual(r.indexed("band")[:3], ["1", "2", "3"])
        r = CliResult("wan_mgr_cli", "get_all_wan_info", read_fixture("wan_mgr_cli", "get_all_wan_info"), 0)
        self.assertEqual(r.all("WAN Type"), ["WWAN", "Ethernet WAN"])
        self.assertEqual(r.note("Failback Timer"), "min")
        r = CliResult("led_cli", "get_enabled", read_fixture("led_cli", "get_enabled"), 0)
        self.assertEqual(r.get("enabled"), "1")

    def test_error_status(self):
        r = CliResult("x_cli", "y", "cmd_y returned 5 (MDM_MAIN: process not running.)\n", 1)
        self.assertFalse(r.ok)
        self.assertEqual(r.rc, 5)
        self.assertEqual(r.message, "process not running")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()

    def test_classification(self):
        self.assertEqual(cli_specs.classify("modem2_cli", "get_signal"), "get")
        self.assertEqual(cli_specs.classify("modem2_cli", "efs_write"), "excluded")
        self.assertEqual(cli_specs.classify("modem2_cli", "update_network_rsrp"), "excluded")
        self.assertEqual(cli_specs.classify("omadm_cli", "anything"), "excluded")
        self.assertEqual(cli_specs.classify("wifi_cli", "set_ap_settings"), "setter")
        self.assertEqual(cli_specs.classify("nwcli", "qmi_idl", ["read_file", "/dev/shm/m3ui-x", "/nvm/num/60002", "16"]), "get")
        self.assertEqual(cli_specs.classify("nwcli", "qmi_idl", ["write_file", "/dev/shm/m3ui-x", "/nvm/num/60002"]), "excluded")

    def test_setter_guards(self):
        cli = self.app.cli
        with self.assertRaises(ApiError):
            cli.run("modem2_cli", "set_enabled_lte_bands", ["0"])          # empty list
        with self.assertRaises(ApiError):
            cli.run("modem2_cli", "set_enabled_lte_bands", ["3", "1", "2"])  # count mismatch
        with self.assertRaises(ApiError):
            cli.run("modem2_cli", "efs_delete", ["/x"])
        with self.assertRaises(ApiError):
            cli.run("wifi_cli", "set_ap_settings", ["1\n0", "32"])            # injected newline
        self.assertTrue(cli.run("modem2_cli", "set_enabled_lte_bands", ["2", "1", "3"]).ok)

    def test_specs_unique_and_sane(self):
        for (c, cmd), sp in cli_specs.all_specs().items():
            if cli_specs.classify(c, cmd) == "get":
                continue  # redundant spec for a read command (harmless; reads never need one)
            self.assertEqual(cli_specs.classify(c, cmd), "setter", (c, cmd))
            self.assertIn(sp.tier, (1, 2, 3))
            self.assertTrue(sp.prompts or sp.mode == "argv", (c, cmd))

    def test_at_denylist(self):
        self.assertEqual(at_mod.classify("AT+CFUN?"), "ok")
        self.assertEqual(at_mod.classify("AT+CFUN=0"), "risky")
        self.assertEqual(at_mod.classify("at+qcfg=\"usbcfg\",1"), "deny")
        self.assertEqual(at_mod.classify("AT;+EGMR=1,7,\"1\""), "deny")
        self.assertEqual(at_mod.classify("ATI"), "ok")


class AuthTests(unittest.TestCase):
    def test_login_logout_and_csrf(self):
        app = make_app()
        c = MockClient(app, login=False)
        c.get("/api/status", expect=401)
        c.call("POST", "/api/login", {"password": "nope"}, expect=401)
        s = app.auth.login(app._password(), "192.168.1.100")
        self.assertTrue(app.auth.session(s.sid))
        with self.assertRaises(ApiError):
            app.auth.check_csrf(s, "bad")
        app.auth.check_csrf(s, s.csrf)

    def test_throttle(self):
        app = make_app()
        for _ in range(3):
            with self.assertRaises(ApiError):
                app.auth.login("x", "10.0.0.9")
        with self.assertRaises(ApiError) as ctx:
            app.auth.login(app._password(), "10.0.0.9")  # still delayed
        self.assertEqual(ctx.exception.status, 429)

    def test_tier3_requires_password(self):
        app = make_app()
        c = MockClient(app)

        class R:
            json = {"confirm": True, "password": "wrong"}
            client_ip = "192.168.1.100"
        with self.assertRaises(ApiError):
            app.require_tier(R, 3)
        R.json = {"confirm": True, "password": app._password()}
        app.require_tier(R, 3)


class ResilienceTests(unittest.TestCase):
    def test_login_fallback_when_vendor_store_down(self):
        app = make_app()
        pw = app._password()
        app.auth.login(pw, "10.1.1.1")                       # confirmed once -> hash remembered
        from m3100ui.errors import vendor_error

        def down():
            raise vendor_error("Reading the admin password failed: process not running")
        app.auth.password_provider = down
        app.auth.forget_password_cache()
        s = app.auth.login(pw, "10.1.1.2")                   # still works from the hash
        self.assertTrue(app.auth.session(s.sid))
        self.assertTrue(app.auth.vendor_unavailable)
        with self.assertRaises(ApiError):
            app.auth.login("wrong-password", "10.1.1.3")
        fb = app.state.read_json("auth_fallback.json")
        self.assertNotIn(pw, str(fb))                         # only a salted hash is stored

    def test_selfheal_repairs_only_on_process_not_running(self):
        from m3100ui import selfheal
        app = make_app()
        calls = []

        class Sys:
            def systemctl(self, action, unit):
                calls.append((action, unit))
                return {"output": "inactive" if action == "is-active" else ""}

            def run(self, argv, timeout=5):
                return {"output": ""}               # webuid not running
        app.system = Sys()
        import time as _t
        orig_sleep = selfheal.time.sleep
        selfheal.time.sleep = lambda s: None
        try:
            # healthy store -> nothing done
            self.assertEqual(selfheal.check_and_repair(app), {})
            # store down with "process not running" -> start tz_securefsd + restart web server
            from m3100ui.adapters.cli import CliResult
            app.cli.run = lambda *a, **k: CliResult("webui_cli", "get_login_settings",
                                                     "webui_get_login_settings returned 5 (WEBUI: process not running.)", 0)
            done = selfheal.check_and_repair(app)
        finally:
            selfheal.time.sleep = orig_sleep
        self.assertEqual(done.get("tz_securefsd"), "started")
        self.assertEqual(done.get("nvtl_webserver"), "restarted")
        self.assertIn(("start", "tz_securefsd.service"), calls)
        self.assertIn(("restart", "nvtl_webserver.service"), calls)


class RouteTests(unittest.TestCase):
    def test_status_and_meta(self):
        c = MockClient()
        st = c.get("/api/status")
        self.assertIn("connection", st)
        self.assertIn(st["connection"]["state"], ("connected", "disconnected", "no_sim", "no_service"))
        meta = c.get("/api/meta/full")
        self.assertIn("features", meta)
        pages = c.get("/api/ui/pages")
        self.assertIn("/app/pages/dashboard.js", pages)

    def test_settings_validation(self):
        c = MockClient()
        c.put("/api/settings", {"router_name": ""}, expect=400)
        c.put("/api/settings", {"bogus": 1}, expect=400)
        self.assertEqual(c.put("/api/settings", {"router_name": "Home 5G"})["router_name"], "Home 5G")


if __name__ == "__main__":
    unittest.main()
