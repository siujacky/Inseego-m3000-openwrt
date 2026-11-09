"""System area: administration, formats, backup/restore, power, logs, software update, about."""
import gzip
import os
import shutil
import tempfile
import time
import unittest

from tests.helpers import MockClient, _FakeHandler, read_fixture

from m3100ui.adapters import cli_specs
from m3100ui.adapters.cli import CliResult
from m3100ui.adapters.specs import system as system_specs
from m3100ui.errors import ApiError
from m3100ui.httpd import Request, Response
from m3100ui.services import about as about_svc
from m3100ui.services import system as sys_svc
from m3100ui.services import update as upd_svc

MY_SPECS = sorted(system_specs.SPECS)


class Base(unittest.TestCase):
    def setUp(self):
        self.c = MockClient()
        self.store = self.c.app.cli.store

    def tearDown(self):
        self.c.cleanup()

    def last_call(self, cli=None, cmd=None):
        calls = [x for x in self.store.calls if (cli is None or x["cli"] == cli) and (cmd is None or x["cmd"] == cmd)]
        self.assertTrue(calls, "no call to %s %s" % (cli, cmd))
        return calls[-1]

    def assert_matches_spec(self, call):
        sp = cli_specs.spec(call["cli"], call["cmd"])
        self.assertIsNotNone(sp, (call["cli"], call["cmd"]))
        values = call["args"] if sp.mode == "argv" else call["answers"]
        self.assertEqual(len(values), sp.expected_count(values), call)
        sp.check(values)
        return values

    def upload(self, data, name="backup.bin", expect=200):
        route, params = self.c.app.router.match("POST", "/api/system/backup/upload")
        self.assertTrue(route.upload)
        req = Request(self.c.app, _FakeHandler({}, self.c.ip), "POST", "/api/system/backup/upload", {"name": name}, data,
                      params, self.c.session)
        try:
            res, status = route.handler(req), 200
        except ApiError as e:
            res, status = e.to_json(), e.status
        self.assertEqual(status, expect, res)
        return res


# ---------------------------------------------------------------------------------------------
class ParsingTests(unittest.TestCase):
    def test_login_fixture(self):
        d = sys_svc.parse_login(read_fixture("webui_cli", "get_login_settings"))
        self.assertEqual(d["retries"], 5)
        self.assertEqual(d["auto_logout_s"], 600)
        self.assertEqual(d["question_id"], 0)
        self.assertEqual(d["password"], "redacted")
        d2 = sys_svc.parse_login(read_fixture("webui_cli", "get_modified_login_settings"))
        self.assertEqual(d2["question_id"], 0)

    def test_bracket_value_keeps_inner_brackets(self):
        txt = "password:[a]b]\nsecurity.answer:[x [y] z]\n"
        self.assertEqual(sys_svc.bracket_value(txt, "password"), "a]b")
        self.assertEqual(sys_svc.bracket_value(txt, "security.answer"), "x [y] z")
        self.assertIsNone(sys_svc.bracket_value(txt, "default_password"))

    def test_password_rules(self):
        ok = sys_svc.check_new_password
        self.assertEqual(ok("Secret12!"), "Secret12!")
        for bad in ("short1A", "a" * 33, "with space1A", "brack[et1A", "alllowercase", "aaaaaaaa1", "Ab1!" * 9):
            with self.assertRaises(ApiError, msg=bad):
                ok(bad)
        with self.assertRaises(ApiError):
            ok("Secret12!", current="Secret12!")

    def test_answer_rules(self):
        self.assertEqual(sys_svc.check_answer("  01/02/1990 "), "01/02/1990")
        for bad in ("", "   ", "a\nb", "x" * 256, "[x]", "é" * 200):
            with self.assertRaises(ApiError, msg=repr(bad)):
                sys_svc.check_answer(bad)

    def test_history_fixture(self):
        recs = upd_svc.parse_records(read_fixture("fota_cli", "full_history"))
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["id"], 2)
        self.assertEqual(r["type"], "software")
        self.assertEqual(r["status_code"], 200)
        self.assertTrue(r["success"])
        self.assertEqual(r["status_text"], "FW Upgrade success")
        self.assertEqual(r["version"], "THN-1.54-5.4-2.609")
        self.assertEqual(r["date"], "2024-02-29 07:36:54")
        self.assertEqual(r["size_bytes"], 85253850)
        self.assertEqual(r["update_type"], "Mandatory")
        self.assertEqual(r["agent"], "omadm")
        self.assertEqual(r["estimated_min"], 22)
        self.assertEqual(upd_svc.parse_records(read_fixture("fota_cli", "revert_status")), [])
        self.assertEqual(len(upd_svc.parse_records(read_fixture("fota_cli", "last_success"))), 1)

    def test_dates(self):
        self.assertEqual(sys_svc.parse_history_date("02-28-2024-16:35:53"), "2024-02-28 16:35:53")
        self.assertIsNone(sys_svc.parse_history_date("00-00-0000-00:00:00"))
        self.assertIsNone(sys_svc.parse_history_date(""))
        r = CliResult("fota_cli", "last_update_check", read_fixture("fota_cli", "last_update_check"), 0)
        self.assertEqual(sys_svc.parse_history_date(r.get("last update check timestamp")), "2024-02-28 16:35:53")

    def test_vendor_event_lines(self):
        tag = "{mifios_wanevent}:"
        raw = ["Oct 31 10:00:00 mifi a: " + tag + "one", "Oct 31 10:00:01 mifi b: other line",
               "Oct 31 10:00:02 mifi a: " + tag + "cleared_wan_log", "Oct 31 10:00:03 mifi a: " + tag + "two",
               "Oct 31 10:00:04 mifi a: " + tag + "three"]
        self.assertEqual(sys_svc.vendor_event_lines(raw, tag, "cleared_wan_log", None), raw[3:])
        self.assertEqual(sys_svc.vendor_event_lines(raw, tag, "cleared_wan_log", raw[3]), raw[4:])
        self.assertEqual(sys_svc.vendor_event_lines(raw, tag, "cleared_wan_log", "gone"), raw[3:])
        t, msg = sys_svc.split_time(raw[0])
        self.assertEqual(t, "Oct 31 10:00:00")
        self.assertTrue(msg.startswith("mifi a:"))
        self.assertEqual(sys_svc.split_time("2026-10-31T22:49:01+0000 host x")[0], "2026-10-31T22:49:01+0000")
        self.assertEqual(sys_svc.split_time("[   12.500000] kernel")[0], "[   12.500000]")
        self.assertEqual(sys_svc.split_time("no stamp"), (None, "no stamp"))

    def test_rotated_files_and_gzip_tail(self):
        d = tempfile.mkdtemp()
        try:
            base = os.path.join(d, "debug_log")
            with open(base, "w") as f:
                f.write("new1\nnew2\n")
            with gzip.open(base + ".1.gz", "wb") as f:
                f.write(b"old1\nold2\n")
            with open(base + ".0", "w") as f:
                f.write("mid1\n")
            files = sys_svc.rotated_files(base)
            self.assertEqual([os.path.basename(p) for p in files], ["debug_log.1.gz", "debug_log.0", "debug_log"])
            self.assertEqual(sys_svc.read_tail_lines(files[0], 1000)[0], ["old1", "old2"])
            lines, used = sys_svc.read_tail_lines(base, 6)
            self.assertEqual(lines, ["new2"])      # cut first line dropped
            self.assertEqual(used, 6)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_schedule_due(self):
        now = time.mktime((2026, 11, 1, 3, 31, 0, 0, 0, -1))      # a Sunday, 03:31 local time
        sched = {"enabled": True, "days": ["sun"], "time": "03:30"}
        self.assertTrue(sys_svc.schedule_due(sched, now, 5000))
        self.assertFalse(sys_svc.schedule_due(dict(sched, enabled=False), now, 5000))
        self.assertFalse(sys_svc.schedule_due(sched, now, 300))                  # just booted
        self.assertFalse(sys_svc.schedule_due(dict(sched, last_run=now - 3600), now, 5000))
        self.assertFalse(sys_svc.schedule_due(dict(sched, days=["mon"]), now, 5000))
        self.assertFalse(sys_svc.schedule_due(dict(sched, time="05:00"), now, 5000))
        self.assertFalse(sys_svc.schedule_due(sched, time.mktime((2000, 1, 2, 3, 31, 0, 0, 0, -1)), 5000))  # clock unset

    def test_about_helpers(self):
        self.assertEqual(about_svc.mask("350000000000001"), "•" * 11 + "0001")
        self.assertEqual(about_svc.mask(""), "")
        sup = CliResult("modem2_cli", "get_sup_tech", read_fixture("modem2_cli", "get_sup_tech"), 0)
        ena = CliResult("modem2_cli", "enabled_tech_get", read_fixture("modem2_cli", "enabled_tech_get"), 0)
        names = about_svc.tech_list(sup.note("available tech modes"), ena.note("enabled tech modes"))
        self.assertEqual(names, ["2G GSM", "3G UMTS", "CDMA 1x", "EV-DO", "4G LTE", "5G NR"])
        self.assertEqual(about_svc.tech_list("0x30"), ["4G LTE", "5G NR"])

    def test_fixture_labels_used_by_services(self):
        r = CliResult("factory_reset_cli", "get_hw_reset_sw_enabled", read_fixture("factory_reset_cli", "get_hw_reset_sw_enabled"), 0)
        self.assertEqual(r.get_int("hw reset sw enabled"), 1)
        r = CliResult("webui_cli", "get_log_settings", read_fixture("webui_cli", "get_log_settings"), 0)
        self.assertEqual(r.get_int("log_life_time(in days)"), 3)
        r = CliResult("webui_cli", "get_i18n_settings", read_fixture("webui_cli", "get_i18n_settings"), 0)
        self.assertEqual(r.get_int("date_fmt"), 0)
        r = CliResult("nua_cli", "get_settings", read_fixture("nua_cli", "get_settings"), 0)
        self.assertEqual(r.get_int("low battery threshold with charger"), 10)
        r = CliResult("nua_cli", "get_poll_interval", read_fixture("nua_cli", "get_poll_interval"), 0)
        self.assertEqual(r.get_int("poll internval, seconds"), 0)
        r = CliResult("webui_cli", "get_software_update_status", read_fixture("webui_cli", "get_software_update_status"), 0)
        self.assertEqual(r.get_int("mdip value"), 0)
        r = CliResult("dsm_cli", "get_usb_mac_address", read_fixture("dsm_cli", "get_usb_mac_address"), 0)
        self.assertEqual(r.get("usb_device"), "02:00:00:00:00:01")
        r = CliResult("mifi_qcmap_cli", "get_ethernet_macaddr", read_fixture("mifi_qcmap_cli", "get_ethernet_macaddr"), 0)
        self.assertEqual(r.get("interface eth0 mac is"), "02:00:00:00:00:01")
        r = CliResult("webui_cli", "get_advanced_settings", read_fixture("webui_cli", "get_advanced_settings"), 0)
        self.assertEqual(r.get_int("network_settings_login_enabled"), 1)


class SpecTests(unittest.TestCase):
    def test_specs_loaded_and_classified(self):
        specs = cli_specs.all_specs()
        self.assertFalse([k for k in cli_specs.SPEC_ERRORS if "system" in str(cli_specs.SPEC_ERRORS[k])],
                         cli_specs.SPEC_ERRORS)
        for key in MY_SPECS:
            self.assertIn(key, specs, key)       # not dropped as a duplicate
            self.assertEqual(cli_specs.classify(*key), "setter", key)
            sp = specs[key]
            self.assertIn(sp.source, ("static", "sandbox", "roundtrip"))
            self.assertTrue(sp.evidence, key)

    def test_login_spec_counts(self):
        sp = cli_specs.spec("webui_cli", "set_login_settings")
        self.assertEqual(sp.expected_count(["0", "a", "0"]), 3)
        self.assertEqual(sp.expected_count(["0", "a", "1", "pw"]), 4)
        with self.assertRaises(ApiError):
            sp.check(["0", "a", "1", "pass word"])      # scanf %255s would truncate
        with self.assertRaises(ApiError):
            sp.check(["0", "a", "1"])                    # password missing -> EOF

    def test_excluded_commands_stay_excluded(self):
        for cli, cmd in (("webui_cli", "su_check_for_update"), ("nua_cli", "factory_reset"), ("fota_cli", "install"),
                         ("fota_cli", "sideload_install"), ("nua_cli", "update_check")):
            self.assertEqual(cli_specs.classify(cli, cmd), "excluded", (cli, cmd))
        self.assertNotIn(("factory_reset_cli", "do_factory_reset_wipe_data"), cli_specs.all_specs())


# ---------------------------------------------------------------------------------------------
class AdminTests(Base):
    def test_get_admin(self):
        d = self.c.get("/api/system/admin")
        for k in ("password_set", "default_password_in_use", "security_question", "questions", "vendor_login", "password_rules"):
            self.assertIn(k, d)
        self.assertNotIn("password", d)
        self.assertEqual(d["vendor_login"]["max_retries"], 5)
        self.assertEqual(d["vendor_login"]["login_required"]["gps_page"], True)
        s = self.c.get("/api/system/admin/secret")
        self.assertEqual(set(s), {"security_answer", "default_password"})

    def test_password_change_tier3_and_answers(self):
        body = {"new_password": "Secret12!", "confirm_password": "Secret12!"}
        self.c.put("/api/system/admin/login", body, expect=428)
        bad = dict(body, confirm=True, password="wrong-one")
        self.c.put("/api/system/admin/login", bad, expect=403)
        other = MockClient(self.c.app, ip="192.168.1.50")
        d = self.c.put("/api/system/admin/login", self.c.confirm(body, 3))
        self.assertTrue(d["password_changed"])
        call = self.last_call("webui_cli", "set_login_settings")
        self.assertEqual(call["answers"], ["0", "redacted", "1", "Secret12!"])
        self.assert_matches_spec(call)
        self.assertEqual(self.c.app._password(), "Secret12!")
        other.get("/api/system/admin", expect=401)        # other sessions logged out
        self.c.get("/api/system/admin")                    # this one kept
        self.assertTrue(self.c.app.auth.check_password("Secret12!"))

    def test_question_only_change_keeps_password(self):
        d = self.c.put("/api/system/admin/login", self.c.confirm({"security_question": 2, "security_answer": "Elm Street"}, 3))
        self.assertFalse(d["password_changed"])
        call = self.last_call("webui_cli", "set_login_settings")
        self.assertEqual(call["answers"], ["2", "Elm Street", "0"])
        self.assert_matches_spec(call)
        self.assertEqual(self.c.get("/api/system/admin")["security_question"], 2)
        self.assertEqual(self.c.get("/api/system/admin/secret")["security_answer"], "Elm Street")

    def test_validation(self):
        put = lambda b, field: self.assertEqual(self.c.put("/api/system/admin/login", b, expect=400)["error"]["details"]["field"], field)
        put({"new_password": "short"}, "new_password")
        put({"new_password": "Secret12!", "confirm_password": "Secret13!"}, "confirm_password")
        put({"security_question": 7}, "security_question")
        put({"security_answer": "a\nb"}, "security_answer")
        put({"bogus": 1}, "bogus")
        r = self.c.put("/api/system/admin/login", self.c.confirm({"security_question": 1}, 3), expect=400)
        self.assertEqual(r["error"]["details"]["field"], "security_answer")
        r = self.c.put("/api/system/admin/login", self.c.confirm({}, 3), expect=400)
        self.assertEqual(r["error"]["details"]["field"], "new_password")

    def test_sessions(self):
        MockClient(self.c.app, ip="10.0.0.7")
        rows = self.c.get("/api/system/sessions")
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(1 for r in rows if r["current"]), 1)
        self.assertNotIn("sid", rows[0])
        d = self.c.post("/api/system/sessions/logout-others")
        self.assertEqual(len(d["sessions"]), 1)

    def test_https_readonly(self):
        d = self.c.get("/api/system/https")
        self.assertFalse(d["enabled"])
        self.assertEqual(d["port"], 443)
        self.assertFalse(d["editable"])
        self.c.call("PUT", "/api/system/https", {"enabled": True}, expect=405)

    def test_formats_rest_and_cli_fallback(self):
        d = self.c.get("/api/system/formats")
        self.assertEqual(d["date_format"], "mdy_slash")
        self.assertEqual(d["languages"][0][0], "en_US")
        d = self.c.put("/api/system/formats", {"date_format": "ymd_slash", "time_format": "24h", "temperature_unit": "celsius"})
        self.assertEqual((d["date_format"], d["time_format"], d["temperature_unit"]), ("ymd_slash", "24h", "celsius"))
        self.assertEqual(self.c.app.rest.calls[-1]["body"]["DisplayDateFormat"], "YYYYMMDDSlashSeparated")
        self.c.put("/api/system/formats", {"language": "xx_XX"}, expect=400)
        self.c.put("/api/system/formats", {"date_format": "bogus"}, expect=400)
        # REST resource missing -> webui_cli set_i18n_settings
        rest = self.c.app.rest
        rest.data.pop("DisplayFormatSettings")
        d = self.c.get("/api/system/formats")
        self.assertEqual(d["source"], "cli")
        self.c.put("/api/system/formats", {"decimal_separator": "comma", "distance_unit": "metric"})
        call = self.last_call("webui_cli", "set_i18n_settings")
        self.assertEqual(call["answers"], ["en_US", "0", "0", "0", "1", "1"])
        self.assert_matches_spec(call)
        self.assertEqual(self.c.get("/api/system/formats")["decimal_separator"], "comma")


class BackupTests(Base):
    def test_backup_requires_password(self):
        self.c.post("/api/system/backup", {}, expect=403)
        r = self.c.post("/api/system/backup", {"password": self.c.app._password()})
        self.assertIsInstance(r, Response)
        self.assertEqual(r.content_type, "application/octet-stream")
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertGreater(len(r.body), 100)
        self.assert_matches_spec(self.last_call("settings_cli", "backup"))
        hist = self.c.get("/api/system/backup")["history"]
        self.assertEqual(len(hist), 1)

    def test_upload_validation(self):
        self.assertEqual(self.upload(b"", expect=400)["error"]["details"]["field"], "file")
        self.upload(b"tiny", expect=400)
        self.upload(b"<!DOCTYPE html><html><body>" + b"x" * 100, expect=400)
        self.upload(b"\x7fELF" + b"\x00" * 100, expect=400)
        self.upload(b"\x00" * (sys_svc.BACKUP_MAX + 1), expect=400)

    def test_restore_flow(self):
        blob = self.c.post("/api/system/backup", {"password": self.c.app._password()}).body
        up = self.upload(blob)
        self.assertTrue(up["created_here"])
        self.c.post("/api/system/backup/restore", {"token": up["token"]}, expect=428)
        self.c.post("/api/system/backup/restore", self.c.confirm({"token": "nonexistent-token"}, 3), expect=400)
        r = self.c.post("/api/system/backup/restore", self.c.confirm({"token": up["token"]}, 3))
        self.assertTrue(r["restarting"])
        call = self.last_call("settings_cli", "restore")
        self.assert_matches_spec(call)
        self.assertTrue(call["answers"][0].endswith(".bin"))
        self.assertFalse(os.path.exists(call["answers"][0]))     # temp file removed
        self.assertEqual(self.store.state["system.restored"]["size"], len(blob))
        self.assertEqual(self.c.app.rest.calls[-1]["body"], {"Operation": "Reboot"})
        self.c.post("/api/system/backup/restore", self.c.confirm({"token": up["token"]}, 3), expect=400)  # one-shot

    def test_restore_foreign_file_and_no_restart(self):
        up = self.upload(b"Salted__" + os.urandom(200))
        self.assertFalse(up["created_here"])
        r = self.c.post("/api/system/backup/restore", self.c.confirm({"token": up["token"], "restart": False}, 3))
        self.assertFalse(r["restarting"])


class PowerTests(Base):
    def test_status(self):
        d = self.c.get("/api/system/power")
        for k in ("uptime_s", "instance", "power_on_cause", "reset_button_enabled", "schedule", "router_time"):
            self.assertIn(k, d)
        self.assertEqual(d["power_on_cause"], "reboot")
        self.assertTrue(d["reset_button_enabled"])

    def test_ping_public_and_restart_simulation(self):
        anon = MockClient(self.c.app, login=False)
        inst = anon.get("/api/system/power/ping")["instance"]
        self.c.post("/api/system/power/reboot", {}, expect=428)
        r = self.c.post("/api/system/power/reboot", self.c.confirm({}, 2))
        self.assertEqual(r["instance"], inst)
        self.assertEqual(r["via"], "rest")
        self.assertEqual(self.c.app.rest.calls[-1], {"method": "PUT", "path": "DeviceState", "body": {"Operation": "Reboot"}})
        anon.get("/api/system/power/ping", expect=503)

    def test_tiers(self):
        self.c.post("/api/system/power/shutdown", self.c.confirm({}, 2), expect=403)   # tier 3 needs the password
        self.c.post("/api/system/power/factory-reset", {}, expect=428)
        r = self.c.post("/api/system/power/factory-reset", self.c.confirm({}, 3))
        self.assertEqual(r["action"], "factory_reset")
        self.assertEqual(self.c.app.rest.calls[-1]["body"], {"Operation": "ResetToFactoryDefaults"})

    def test_cli_fallback_when_rest_fails(self):
        def broken(path, body):
            raise ApiError(503, "unsupported", "no such resource")
        self.c.app.rest.put = broken
        r = self.c.post("/api/system/power/reboot", self.c.confirm({}, 2))
        self.assertEqual(r["via"], "cli")
        call = self.last_call("dsm_cli", "reset_device")
        self.assertEqual(call["answers"], ["0"])
        self.assert_matches_spec(call)
        self.c.post("/api/system/power/shutdown", self.c.confirm({}, 3))
        self.assert_matches_spec(self.last_call("dsm_cli", "shutdown_device"))
        self.c.post("/api/system/power/factory-reset", self.c.confirm({}, 3))
        self.assert_matches_spec(self.last_call("factory_reset_cli", "do_factory_reset"))

    def test_unverified_static_spec_becomes_503(self):
        self.assertEqual(cli_specs.spec("webui_cli", "set_login_settings").source, "static")
        self.c.app.cli.allow_static_specs = False          # as on a real unit
        r = self.c.put("/api/system/admin/login", self.c.confirm({"new_password": "Secret12!"}, 3), expect=503)
        self.assertEqual(r["error"]["details"]["reason"], "unverified_setter")
        self.assertIn("verified on the device", r["error"]["message"])

    def test_reset_button(self):
        self.c.put("/api/system/power/reset-button", {"enabled": False}, expect=428)
        d = self.c.put("/api/system/power/reset-button", self.c.confirm({"enabled": False}, 2))
        self.assertFalse(d["reset_button_enabled"])
        call = self.last_call("factory_reset_cli", "set_hw_reset_sw_enabled")
        self.assertEqual(call["answers"], ["0"])
        self.assert_matches_spec(call)
        self.assertTrue(self.c.put("/api/system/power/reset-button", {"enabled": True})["reset_button_enabled"])
        self.c.put("/api/system/power/reset-button", {"enabled": "maybe"}, expect=400)

    def test_schedule(self):
        d = self.c.put("/api/system/power/schedule", {"enabled": True, "days": ["sun", "mon"], "time": "4:05"})
        self.assertEqual(d["schedule"]["days"], ["mon", "sun"])
        self.assertEqual(d["schedule"]["time"], "04:05")
        for body, field in (({"time": "25:00"}, "time"), ({"days": ["xyz"]}, "days"), ({"days": "mon"}, "days"),
                            ({"enabled": True, "days": []}, "days")):
            self.assertEqual(self.c.put("/api/system/power/schedule", body, expect=400)["error"]["details"]["field"], field)


class LogTests(Base):
    def test_sources_and_entries(self):
        srcs = [s["id"] for s in self.c.get("/api/system/logs")["sources"]]
        self.assertEqual(srcs, ["system", "wwan", "device", "sessions", "kernel", "audit"])
        for s in srcs:
            d = self.c.get("/api/system/logs/" + s, query={"lines": "50"})
            self.assertIn("entries" if s != "sessions" else "rows", d, s)
        d = self.c.get("/api/system/logs/wwan")
        self.assertTrue(d["entries"])
        self.assertNotIn("{mifios_wanevent}", d["entries"][0]["text"])
        self.assertTrue(all("housekeeping" not in e["text"] for e in d["entries"]))   # untagged lines dropped
        d = self.c.get("/api/system/logs/device", query={"q": "battery"})
        self.assertTrue(d["entries"] and all("battery" in e["text"] for e in d["entries"]))
        self.c.get("/api/system/logs/bogus", expect=404)
        self.c.get("/api/system/logs/system", query={"lines": "1"}, expect=400)

    def test_clear_and_unclear(self):
        self.assertEqual(self.c.post("/api/system/logs/wwan/clear")["count"], 0)
        self.assertEqual(self.c.get("/api/system/logs/wwan")["count"], 0)
        self.assertGreater(self.c.post("/api/system/logs/wwan/unclear")["count"], 0)
        self.c.post("/api/system/logs/audit/clear", expect=400)

    def test_downloads(self):
        r = self.c.get("/api/system/logs/device/download")
        self.assertTrue(r.content_type.startswith("text/plain"))
        self.assertIn(b"battery", r.body)
        r = self.c.get("/api/system/logs/sessions/download")
        self.assertTrue(r.content_type.startswith("text/csv"))
        self.assertTrue(r.body.startswith(b"start,end"))

    def test_audit_has_no_secrets(self):
        self.c.put("/api/system/admin/login", self.c.confirm({"new_password": "Secret12!"}, 3))
        txt = " ".join(e["text"] for e in self.c.get("/api/system/logs/audit")["entries"])
        self.assertIn("system.admin.login", txt)
        self.assertNotIn("Secret12!", txt)

    def test_log_settings(self):
        d = self.c.get("/api/system/log-settings")
        self.assertEqual(d["event_log"], {"enabled": False, "life_days": 3})
        d = self.c.put("/api/system/log-settings", {"event_log_enabled": True, "event_log_days": 2})
        self.assertEqual(d["event_log"], {"enabled": True, "life_days": 2})
        call = self.last_call("webui_cli", "set_log_settings")
        self.assertEqual(call["answers"], ["1", "2"])
        self.assert_matches_spec(call)
        self.c.put("/api/system/log-settings", {"event_log_days": 9}, expect=400)

    def test_support_bundle(self):
        self.c.post("/api/system/support-bundle", {"password": "wrong"}, expect=403)
        r = self.c.post("/api/system/support-bundle", {"password": self.c.app._password()})
        self.assertEqual(r.content_type, "application/gzip")
        self.assertEqual(r.body[:2], b"\x1f\x8b")


class UpdateTests(Base):
    def test_status_history_settings(self):
        d = self.c.get("/api/update/status")
        self.assertEqual(d["state"]["name"], "idle")
        self.assertEqual(d["last_check"], "2024-02-28 16:35:53")
        self.assertEqual(d["install_duration_min"], 22)
        self.assertFalse(d["install_in_progress"])
        self.assertTrue(d["actions"]["check"])
        self.assertFalse(d["actions"]["install"])
        self.assertFalse(d["sideload"]["supported"])
        h = self.c.get("/api/update/history")
        self.assertEqual(h["records"][0]["version"], "THN-1.54-5.4-2.609")
        s = self.c.get("/api/update/settings")
        self.assertEqual((s["auto_update"], s["auto_check"], s["low_battery_threshold"]), (True, True, 20))
        self.assertFalse(s["nua"]["running"])

    def test_cli_status_fallback(self):
        self.c.app.rest.data.pop("SoftwareUpdate/Status", None)
        from m3100ui.adapters import mock as mock_mod
        handler = mock_mod.REST_HANDLERS.pop(("GET", "SoftwareUpdate/Status"))
        try:
            d = self.c.get("/api/update/status")
            self.assertEqual(d["source"], "cli")
            self.assertEqual(d["state"]["code"], 0)
        finally:
            mock_mod.REST_HANDLERS[("GET", "SoftwareUpdate/Status")] = handler

    def test_settings_put(self):
        d = self.c.put("/api/update/settings", {"auto_update": False, "low_battery_threshold": 30})
        self.assertFalse(d["auto_update"])
        call = self.last_call("webui_cli", "set_software_update_settings")
        self.assertEqual(call["answers"], ["0", "1", "30"])
        self.assert_matches_spec(call)
        self.c.put("/api/update/settings", {"low_battery_threshold": 0}, expect=400)
        self.c.put("/api/update/settings", {"bogus": 1}, expect=400)

    def test_nua(self):
        d = self.c.put("/api/update/nua", {"poll": False, "low_battery": 30})
        call = self.last_call("nua_cli", "set_settings")
        self.assertEqual(call["args"], ["-f", "1", "-p", "0", "-s", "1", "-l", "30", "-w", "10"])
        self.assertEqual(call["answers"], [])
        self.assert_matches_spec(call)
        self.assertFalse(d["nua"]["poll"])
        d = self.c.put("/api/update/nua/poll-interval", {"seconds": 7200})
        self.assertEqual(d["nua"]["poll_interval_s"], 7200)
        self.assert_matches_spec(self.last_call("nua_cli", "set_poll_interval"))
        self.c.put("/api/update/nua/poll-interval", {"seconds": 5}, expect=400)

    def test_actions_and_tiers(self):
        self.c.post("/api/update/check", {}, expect=428)
        d = self.c.post("/api/update/check", self.c.confirm({}, 2))
        self.assertEqual(d["state"]["name"], "checking")
        self.assertEqual(self.c.app.rest.calls[-1]["path"], "SoftwareUpdate/CheckUpdate")
        self.c.post("/api/update/install", self.c.confirm({}, 3), expect=400)        # nothing to install
        self.c.app.rest.data["_mock_su"] = {"phase": "available", "t": time.time()}
        d = self.c.get("/api/update/status")
        self.assertTrue(d["actions"]["install"] and d["actions"]["download"])
        self.c.post("/api/update/install", self.c.confirm({}, 2), expect=403)        # tier 3: password needed
        self.c.post("/api/update/download", {}, expect=428)
        d = self.c.post("/api/update/download", self.c.confirm({}, 2))
        self.assertEqual(d["state"]["name"], "downloading")
        self.c.app.rest.data["_mock_su"] = {"phase": "ready", "t": time.time()}
        d = self.c.post("/api/update/install/postpone")
        self.assertEqual(d["state"]["name"], "deferred")
        d = self.c.post("/api/update/install", self.c.confirm({}, 3))
        self.assertEqual(d["state"]["name"], "installing")


class AboutTests(Base):
    def test_about_masked_and_reveal(self):
        d = self.c.get("/api/about")
        for k in ("device", "software", "identifiers", "macs", "system", "vendor_ui", "open_source"):
            self.assertIn(k, d)
        self.assertNotIn("_secrets", d)
        self.assertEqual(d["device"]["model"], "M3100")
        self.assertEqual(d["software"]["wifi_firmware"], "1.1.0.0.2917.1")
        self.assertTrue(d["identifiers"]["imei"].startswith("•"))
        self.assertNotIn("350000000000001", str(d))
        self.assertIn("Wi-Fi 1", [m["name"] for m in d["macs"]])
        self.assertEqual(d["vendor_ui"]["port"], 80)
        ids = self.c.get("/api/about/identifiers")
        self.assertEqual(ids["imei"], "350000000000001")
        anon = MockClient(self.c.app, login=False)
        anon.get("/api/about/identifiers", expect=401)


if __name__ == "__main__":
    unittest.main()
