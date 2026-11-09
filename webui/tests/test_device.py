"""Device & Services area: parsers on real fixtures, routes, validation, setter answers vs Spec, tiers,
read-after-write in --mock, secrets handling, privacy services and vendor-logs-in-RAM."""
import json
import os
import unittest

from tests.helpers import MockClient, read_fixture

from m3100ui.adapters import cli_specs
from m3100ui.adapters.cli import CliResult
from m3100ui.adapters.specs import device as dspecs
from m3100ui.services import device as dev
from m3100ui.services import gps as gpsmod
from m3100ui.services import privacy as priv
from m3100ui.services import svc_misc as misc


def fx(cli, cmd):
    return CliResult(cli, cmd, read_fixture(cli, cmd), 0)


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.c = MockClient()

    def tearDown(self):
        self.c.cleanup()

    def calls(self, cli=None, cmd=None):
        out = [x for x in self.c.app.cli.store.calls if (cli is None or x["cli"] == cli) and (cmd is None or x["cmd"] == cmd)]
        return out

    def last(self, cli, cmd):
        calls = self.calls(cli, cmd)
        self.assertTrue(calls, "no call to %s %s" % (cli, cmd))
        return calls[-1]

    def assert_spec_ok(self, call):
        sp = cli_specs.spec(call["cli"], call["cmd"])
        self.assertIsNotNone(sp, (call["cli"], call["cmd"]))
        sp.check(call["args"] if sp.mode == "argv" else call["answers"])


# ------------------------------------------------------------------------------------------ parsers
class ParserTests(unittest.TestCase):
    def test_powersave(self):
        p = dev.parse_powersave(fx("powersave_cli", "get_settings"))
        self.assertEqual(p, {"enabled": True, "deep_sleep_enabled": True, "deep_sleep_idle_s": 300, "deep_sleep_wakeup_s": 7200,
                             "auto_shutdown_enabled": True, "auto_shutdown_idle_s": 7200})

    def test_dsm(self):
        s = dev.parse_dsm_state(fx("dsm_cli", "get_state"))
        self.assertEqual(s["battery_state"], "full")
        self.assertEqual(s["battery_temperature"], 354)
        self.assertEqual(s["battery_voltage"], 4260214)
        self.assertEqual(s["typec_pwr_role"], "sink")
        st = dev.parse_dsm_settings(fx("dsm_cli", "get_settings"))
        self.assertEqual(st, {"critical_battery_pct": 1, "battery_pack_active": 1, "battery_pack_low_pct": 25})
        cap = dev.parse_battery_health_capability(read_fixture("dsm_cli", "get_battery_health_capability"))
        self.assertEqual(cap, {"duration_s": 57600, "min_pct": 50, "max_pct": 60})
        self.assertEqual(fx("dsm_cli", "get_battery_health_enabled").get("Battery Health Mode"), "1")

    def test_thermal_sar_watchdog(self):
        self.assertEqual(dev.parse_thermal_report(read_fixture("thermal_monitor_cli", "report")),
                         [{"zone": "tz0", "celsius": 43.4}])
        self.assertEqual(dev.parse_sar(read_fixture("sar_cli", "get_sar_settings")), {"cs0": True, "cs1": True})
        apps = dev.parse_watchdog_apps(read_fixture("watchdog_cli", "get_app_list"))
        self.assertEqual(len(apps), 37)
        self.assertEqual(apps[0], "msgbus")
        self.assertIn("cloud_engine_main_thread", apps)

    def test_devui_and_flags(self):
        self.assertEqual(fx("devui_cli", "get_screen_timeout_settings").get("screen_timeout"), "30")
        self.assertEqual(dev.devui_value(fx("devui_cli", "get_data_usage_touchscreen_enabled"),
                                         "deviceui_get_data_usage_touchscreen_enabled"), "1")
        self.assertIsNone(dev.flag(fx("devui_cli", "get_admin_password_enabled").get("admin_password_setting_enabled")))
        self.assertTrue(dev.flag("Enable"))
        self.assertFalse(dev.flag("Disable"))
        self.assertTrue(dev.flag(fx("led_cli", "get_enabled").get("enabled")))

    def test_sysint_rc(self):
        self.assertEqual(dev.sysint_rc("Starting sysint client library\nEnable ? ( 1 or 0 ): sys_setDeepSleepEnabled returned 0\n"
                                       "Stopping sysint client library 0\n"), 0)
        self.assertEqual(dev.sysint_rc("sys_setPowerSaveSettings returned 7 \n"), 7)
        self.assertIsNone(dev.sysint_rc("Unknown cmd foo\n"))

    def test_file_sharing(self):
        s = misc.parse_fs_settings(read_fixture("file_sharing_cli", "settings_get"))
        self.assertEqual(s, {"enabled": True, "host_name": "JetpackM2100", "share_all": True, "read_write": True})
        self.assertEqual(misc.parse_fs_folders(read_fixture("file_sharing_cli", "settings_list_get")),
                         [{"name": "Public", "read_only": True}, {"name": "Shared", "read_only": True}])

    def test_ans(self):
        items = misc.parse_ans_list(read_fixture("ans_cli", "get_notification_settings"))
        self.assertEqual(len(items), 47)
        self.assertEqual(items[0], {"id": "startup", "enabled": "1", "audio_alert_enabled": "1", "wake_display_enabled": "0",
                                    "display_surfaces": "0"})
        self.assertEqual(misc.event_view(items[13])["surfaces"], "browser")
        cur = misc.parse_ans_list(read_fixture("ans_cli", "get_current_notification_list"))
        self.assertEqual([c["id"] for c in cur], ["simNone", "usbModePrompt"])
        self.assertEqual(cur[0]["severity"], "3")
        su = fx("ans_cli", "get_software_update_notification_settings")
        self.assertEqual(su.get("Show Initial software update notification"), "0")

    def test_tr069(self):
        r = fx("tr069_cli", "get_acs_server_config")
        self.assertEqual(r.get("TR069 Server URL"), "acs.inseego.com")
        self.assertEqual(r.get("TR069 Server Port Number"), "7547")
        c = fx("tr069_cli", "get_acs_client_config")
        self.assertEqual(c.get("TR069 Client Time Interval(secs)"), "100")
        x = fx("tr069_cli", "get_xmpp_config")
        self.assertEqual(x.get("TR069 XMPP Feature(0:Disable,1: Enabled)"), "0")
        self.assertEqual(fx("tr069_cli", "get_status").get("TR069 is"), "Disable")
        carriers = misc.parse_tr069_mccmnc(read_fixture("tr069_cli", "get_tr069_mccmnc_config"))
        self.assertEqual(len(carriers), 9)
        self.assertEqual(carriers[1]["mccmnc"][0], "311580")

    def test_gps(self):
        self.assertEqual(fx("gps_cli", "get_active").get("default_status"), "on")
        self.assertEqual(fx("gps_cli", "get_nmea_tcp").get_int("port"), 11010)
        self.assertEqual(fx("gps_cli", "gps_status").get("status"), "Disabled")
        r = fx("gps_cli", "get_last_fix")
        self.assertFalse(r.ok)
        self.assertEqual(r.rc, 1001)
        from m3100ui.mock_handlers import device as mockdev
        f = gpsmod.parse_fix(CliResult("gps_cli", "get_last_fix", mockdev._fix_text(52.5, 13.4), 0))
        self.assertTrue(f["valid"])
        self.assertAlmostEqual(f["latitude"], 52.5)
        self.assertEqual(f["satellites"], 9)
        self.assertTrue(f["geo_uri"].startswith("geo:52.5"))

    def test_mounts(self):
        m = priv.parse_mounts("tmpfs /tmp tmpfs rw 0 0\nm3ui-vendor-log /opt/nvtl/log tmpfs rw,size=16384k 0 0\n"
                              "/dev/x /media/a\\040b vfat rw 0 0\n")
        self.assertEqual(m[1]["source"], "m3ui-vendor-log")
        self.assertEqual(m[2]["target"], "/media/a b")


# ------------------------------------------------------------------------------------------ specs
class SpecTests(unittest.TestCase):
    def test_all_device_specs_are_setters_and_sane(self):
        for (c, cmd), sp in dspecs.SPECS.items():
            self.assertEqual(cli_specs.classify(c, cmd), "setter", (c, cmd))
            self.assertIn(sp.tier, (1, 2, 3))
            self.assertTrue(sp.prompts or sp.mode == "argv", (c, cmd))
            self.assertIn(sp.source, ("static", "sandbox", "roundtrip"))
            self.assertTrue(sp.evidence, (c, cmd))
            sp.check(sp.sample_answers())
            self.assertIs(cli_specs.spec(c, cmd), sp, "spec %s %s dropped (duplicate?)" % (c, cmd))

    def test_group_repeat(self):
        sp = dspecs.SPECS[("ans_cli", "set_notification_settings")]
        self.assertEqual(sp.expected_count(["2"] + ["x"] * 10), 11)
        with self.assertRaises(Exception):
            sp.check(["2", "a", "1", "1", "1", "3"])          # too few
        with self.assertRaises(Exception):
            sp.check(["0"])                                    # empty list would wipe the table
        fs = dspecs.SPECS[("file_sharing_cli", "settings_list_set")]
        fs.check(["2", "A", "0", "B", "1"])
        with self.assertRaises(Exception):
            fs.check(["1", "My Folder", "0"])                  # scanf("%s"): no spaces


# ------------------------------------------------------------------------------------------ power
class PowerTests(Base):
    def test_battery(self):
        b = self.c.get("/api/device/battery")
        for k in ("present", "percent", "charging", "state", "voltage_v", "temperature_c", "supplies"):
            self.assertIn(k, b)
        self.assertEqual(b["percent"], 100)
        self.assertEqual(b["temperature_c"], 35.4)

    def test_powersave_rest_path_and_read_back(self):
        d = self.c.put("/api/device/powersave", {"deep_sleep_idle_s": 900})
        self.assertEqual(d["deep_sleep_idle_s"], 900)
        self.assertEqual(self.c.app.rest.calls[-1]["path"], "PowerSaveSettings")
        self.assertEqual(self.c.app.rest.calls[-1]["body"]["PowerSaveDeepSleepIdleDuration"], 900)
        self.assertEqual(self.c.get("/api/device/powersave")["deep_sleep_idle_s"], 900)

    def test_powersave_cli_paths(self):
        d = self.c.put("/api/device/powersave", {"auto_shutdown_idle_s": 14400, "deep_sleep_wakeup_s": 0})
        call = self.last("sysintcli", "setPowerSaveSettings")
        self.assertEqual(call["answers"], ["300", "0", "14400"])
        self.assert_spec_ok(call)
        self.assertEqual(d["auto_shutdown_idle_s"], 14400)
        self.assertEqual(d["deep_sleep_wakeup_s"], 0)
        d = self.c.post("/api/device/powersave/never-sleep")
        self.assertFalse(d["deep_sleep_enabled"])
        self.assertFalse(d["auto_shutdown_enabled"])
        self.assertEqual(self.last("sysintcli", "setDeepSleepEnabled")["answers"], ["0"])
        self.assertEqual(self.last("sysintcli", "setAutoShutdownEnabled")["answers"], ["0"])
        d = self.c.put("/api/device/powersave", {"deep_sleep_enabled": True, "deep_sleep_idle_s": 600})
        self.assertTrue(d["deep_sleep_enabled"])
        self.assertEqual(d["deep_sleep_idle_s"], 600)

    def test_powersave_validation(self):
        self.c.put("/api/device/powersave", {"deep_sleep_idle_s": 10}, expect=400)
        self.c.put("/api/device/powersave", {"deep_sleep_wakeup_s": 120}, expect=400)
        self.c.put("/api/device/powersave", {"bogus": 1}, expect=400)
        e = self.c.put("/api/device/powersave", {"auto_shutdown_idle_s": 3600, "deep_sleep_idle_s": 7200}, expect=400)
        self.assertEqual(e["error"]["details"]["field"], "auto_shutdown_idle_s")

    def test_preservation_and_universal(self):
        p = self.c.get("/api/device/battery-preservation")
        self.assertTrue(p["supported"])
        self.assertEqual((p["min_pct"], p["max_pct"]), (50, 60))
        p = self.c.put("/api/device/battery-preservation", {"enabled": False})
        self.assertFalse(p["enabled"])
        self.assertEqual(self.c.app.rest.calls[-1]["path"], "BatteryPreservationMode/Enabled")
        u = self.c.put("/api/device/universal-charging", {"threshold_pct": 50, "enabled": False})
        self.assertEqual(u["threshold_pct"], 50)
        self.assertFalse(u["enabled"])
        self.assertEqual(self.last("sysintcli", "setUniversalChargingSettings")["answers"], ["50"])
        self.c.put("/api/device/universal-charging", {"threshold_pct": 30}, expect=400)

    def test_preservation_cli_fallback(self):
        self.c.app.rest.data.pop("BatteryPreservationMode/Enabled")
        p = self.c.put("/api/device/battery-preservation", {"enabled": False})
        call = self.last("dsm_cli", "set_battery_health_enabled")
        self.assertEqual(call["answers"], ["0"])
        self.assert_spec_ok(call)
        self.assertFalse(p["enabled"])


# ------------------------------------------------------------------------------------------ display / LED / buzzer
class DisplayTests(Base):
    def test_display_read_and_write(self):
        d = self.c.get("/api/device/display")
        self.assertEqual(d["screen_timeout_s"], 30)
        self.assertTrue(d["show_primary_ssid"])
        d = self.c.put("/api/device/display", {"screen_timeout_s": 300, "quickstart_enabled": True, "show_guest_password": False})
        self.assertEqual(d["screen_timeout_s"], 300)
        self.assertTrue(d["quickstart_enabled"])
        self.assertFalse(d["show_guest_password"])
        call = self.last("devui_cli", "set_screen_timeout_settings")
        self.assertEqual(call["answers"], ["3"])
        self.assert_spec_ok(call)
        self.assertEqual(self.c.app.rest.calls[-1]["body"], {"HidePasswordTouchEnabledGuest": True})
        self.c.put("/api/device/display", {"screen_timeout_s": 45}, expect=400)
        self.c.put("/api/device/display", {"quickstart_enabled": "yes"}, expect=400)

    def test_display_devui_fallback(self):
        self.c.app.rest.data.pop("WiFi/DisplaySettings")
        d = self.c.put("/api/device/display", {"show_primary_password": False})
        call = self.last("devui_cli", "set_primary_wifi_info_display_settings")
        self.assertEqual(call["answers"], ["1", "0"])
        self.assert_spec_ok(call)
        self.assertFalse(d["show_primary_password"])
        self.assertTrue(d["show_primary_ssid"])

    def test_led_and_buzzer(self):
        self.assertFalse(self.c.put("/api/device/led", {"enabled": False})["enabled"])
        self.assertEqual(self.last("led_cli", "set_enabled")["answers"], ["0"])
        r = self.c.post("/api/device/buzzer", {"on_ms": 100, "off_ms": 50, "repeat": 2})
        self.assertTrue(r["ok"])
        call = self.last("buzzer_cli", "on")
        self.assertEqual(call["answers"], ["100", "50", "2"])
        self.assert_spec_ok(call)
        self.c.post("/api/device/buzzer", {"repeat": 50}, expect=400)
        self.c.post("/api/device/buzzer", {"on_ms": 99999}, expect=400)
        self.c.post("/api/device/buzzer/stop")
        self.assertEqual(self.last("buzzer_cli", "off")["args"], [])


# ------------------------------------------------------------------------------------------ health / watchdog
class HealthTests(Base):
    def test_health(self):
        h = self.c.get("/api/device/health")
        for k in ("thermal", "thermal_report", "sar", "memory", "cpu", "watchdog_apps", "failsafe"):
            self.assertIn(k, h)
        self.assertTrue(h["sar_running"])
        self.assertEqual(h["failsafe"]["crash_count"], 5)
        self.assertFalse(h["failsafe_running"])

    def test_watchdog_tier(self):
        w = self.c.put("/api/device/watchdog", {"interval_s": 120})
        self.assertEqual(w["interval_s"], 120)
        self.assertEqual(self.c.app.rest.calls[-1]["path"], "WatchdogSettings")
        e = self.c.put("/api/device/watchdog", {"enabled": False}, expect=428)
        self.assertEqual(e["error"]["details"]["tier"], 2)
        w = self.c.put("/api/device/watchdog", self.c.confirm({"enabled": False}))
        self.assertFalse(w["enabled"])
        call = self.last("watchdog_cli", "set_settings")
        self.assertEqual(call["answers"], ["0", "120"])
        self.assert_spec_ok(call)
        self.c.put("/api/device/watchdog", {"interval_s": 5}, expect=400)

    def test_lowmem_memdiag(self):
        d = self.c.put("/api/device/health/lowmem", {"threshold_mb": 20, "window_start_h": 3})
        self.assertEqual(d["threshold_mb"], 20)
        call = self.last("mifi_health_cli", "set_lowmem_reboot_settings")
        self.assertEqual(call["answers"], ["20", "60", "168", "3", "4"])
        self.assert_spec_ok(call)
        self.c.put("/api/device/health/lowmem", {"threshold_mb": 5}, expect=400)
        self.c.put("/api/device/health/lowmem", {"window_end_h": 24}, expect=400)
        m = self.c.put("/api/device/health/memdiag", {"debug_mode": 1})
        self.assertEqual(m["debug_mode"], 1)
        self.assertEqual(len(self.last("mifi_health_cli", "set_mem_diag_settings")["answers"]), 12)


# ------------------------------------------------------------------------------------------ GPS
class GpsTests(Base):
    def test_gps_flow(self):
        g = self.c.get("/api/gps")
        self.assertFalse(g["running"])
        self.assertFalse(g["privacy_accepted"])
        self.assertEqual(g["nmea_port"], 11010)
        e = self.c.post("/api/gps/start", {}, expect=400)
        self.assertEqual(e["error"]["details"]["field"], "accept_privacy")
        g = self.c.post("/api/gps/start", {"accept_privacy": True})
        self.assertTrue(g["running"])
        self.assertTrue(g["privacy_accepted"])
        body = self.c.app.rest.calls[-1]["body"]
        self.assertEqual(body["GPSEnabled"], True)
        self.assertEqual(body["NMEAOverWiFiEnabled"], True)      # raw setting preserved, not the masked REST value
        f = self.c.get("/api/gps/fix")
        self.assertTrue(f["available"])
        self.assertIn("latitude", f["fix"])
        g = self.c.post("/api/gps/stop")
        self.assertFalse(g["running"])

    def test_gps_settings(self):
        g = self.c.put("/api/gps", {"mode": "msb", "start_at_boot": False, "nmea_port": 20000})
        self.assertEqual(g["mode"], "msb")
        self.assertFalse(g["start_at_boot"])
        self.assertEqual(g["nmea_port"], 20000)
        self.assertEqual(self.last("gps_cli", "agps_mode_set")["answers"], ["2"])
        self.assertEqual(self.last("gps_cli", "set_active")["answers"], ["0"])
        self.c.put("/api/gps", {"mode": "galileo"}, expect=400)
        self.c.put("/api/gps", {"nmea_port": 80}, expect=400)
        self.c.post("/api/gps/xtra")
        self.assertEqual(self.last("gps_cli", "force_xtra")["args"], [])

    def test_gps_cli_fallback(self):
        self.c.app.rest.data.pop("GPSSettings")
        g = self.c.post("/api/gps/start", {"accept_privacy": True})
        self.assertEqual(self.last("gps_cli", "set_privacy")["answers"], ["1"])
        self.assertTrue(self.calls("gps_cli", "gps_start"))
        self.assertTrue(g["running"])
        g = self.c.put("/api/gps", {"nmea_enabled": False})
        call = self.last("gps_cli", "set_nmea_tcp")
        self.assertEqual(call["answers"], ["0", "11010"])
        self.assert_spec_ok(call)


# ------------------------------------------------------------------------------------------ file sharing
class FilesTests(Base):
    def test_files(self):
        f = self.c.get("/api/services/files")
        self.assertTrue(f["enabled"])
        f = self.c.put("/api/services/files", {"host_name": "ROUTER", "share_all": False})
        call = self.last("file_sharing_cli", "settings_set")
        self.assertEqual(call["answers"], ["1", "ROUTER", "0", "1"])
        self.assert_spec_ok(call)
        self.assertEqual(f["host_name"], "ROUTER")
        self.assertFalse(f["share_all"])
        f = self.c.put("/api/services/files", {"enabled": False})
        self.assertFalse(f["enabled"])
        self.assertEqual(f["host_name"], "ROUTER")              # preserved (sysintcli get-modify-set)
        self.assertEqual(self.last("sysintcli", "setSambaEnabled")["answers"], ["0"])
        self.c.put("/api/services/files", {"host_name": "OTHER"}, expect=400)      # off: settings locked
        self.c.put("/api/services/files", {"enabled": True, "host_name": "has space"}, expect=400)
        self.c.put("/api/services/files", {"enabled": True, "host_name": "x" * 16}, expect=400)

    def test_folders_and_actions(self):
        f = self.c.put("/api/services/files/folders", {"folders": [{"name": "Media", "read_only": False},
                                                                   {"name": "Docs", "read_only": True}]})
        call = self.last("file_sharing_cli", "settings_list_set")
        self.assertEqual(call["answers"], ["2", "Media", "0", "Docs", "1"])
        self.assert_spec_ok(call)
        self.assertEqual([x["name"] for x in f["folders"]], ["Media", "Docs"])
        self.c.put("/api/services/files/folders", {"folders": []}, expect=400)
        self.c.put("/api/services/files/folders", {"folders": [{"name": "a b"}]}, expect=400)
        self.c.post("/api/services/files/share", {"action": "start"})
        self.assertEqual(self.last("file_sharing_cli", "start_stop_share")["args"], ["start"])
        self.c.post("/api/services/files/drive", {"action": "unmount"})
        self.assertEqual(self.last("file_sharing_cli", "mount_umount_partition")["args"], ["unmount"])
        self.c.post("/api/services/files/drive", {"action": "format"}, expect=400)

    def test_feature_hidden_without_service(self):
        self.assertFalse(self.c.app.features.all()["services.files"])


# ------------------------------------------------------------------------------------------ notifications
class NotificationTests(Base):
    def test_settings(self):
        n = self.c.put("/api/services/notifications", {"stealth": True, "vital_update": True, "wake_on_notification": False})
        self.assertTrue(n["stealth"])
        self.assertTrue(n["vital_update"])
        self.assertFalse(n["wake_on_notification"])
        self.assertEqual(self.last("ans_cli", "set_software_update_notification_settings")["answers"], ["0", "1"])

    def test_events_full_list_write(self):
        ev = self.c.get("/api/services/notifications/events")
        self.assertEqual(len(ev["events"]), 47)
        self.assertTrue(any(g["id"] == "sim" for g in ev["groups"]))
        ev = self.c.put("/api/services/notifications/events",
                        {"events": [{"id": "startup", "audio": False}, {"id": "shutdown", "audio": False, "surfaces": "screen"}]})
        call = self.last("ans_cli", "set_notification_settings")
        self.assertEqual(call["answers"][0], "47")
        self.assertEqual(len(call["answers"]), 1 + 5 * 47)
        self.assertEqual(call["answers"][1:6], ["startup", "1", "0", "0", "0"])
        self.assertEqual(call["answers"][6:11], ["shutdown", "1", "0", "0", "2"])
        self.assert_spec_ok(call)
        by = {e["id"]: e for e in ev["events"]}
        self.assertFalse(by["startup"]["audio"])
        self.assertEqual(by["shutdown"]["surfaces"], "screen")
        self.assertEqual(len(ev["events"]), 47)                   # nothing lost
        self.c.put("/api/services/notifications/events", {"events": [{"id": "nope", "audio": False}]}, expect=400)
        self.c.put("/api/services/notifications/events", {"events": [{"id": "startup", "surfaces": "tv"}]}, expect=400)

    def test_current_and_ack(self):
        cur = self.c.get("/api/services/notifications/current")
        self.assertEqual(len(cur), 2)
        cur = self.c.post("/api/services/notifications/current/usbModePrompt/ack")
        self.assertEqual([x["id"] for x in cur], ["simNone"])
        self.assertEqual(self.last("ans_cli", "acknowledge_notification")["answers"], ["usbModePrompt"])
        self.c.post("/api/services/notifications/current/usbModePrompt/ack", expect=404)


# ------------------------------------------------------------------------------------------ TR-069 / CBSD
class Tr069Tests(Base):
    def test_get_hides_passwords(self):
        t = self.c.get("/api/services/tr069")
        self.assertTrue(t["server_password_set"])
        self.assertNotIn("server_password", t)
        self.assertEqual(t["server_interface"], "cellular")
        self.assertEqual(t["client_auth_mode"], "basic")
        s = self.c.get("/api/services/tr069/secret")
        self.assertEqual(s["server_password"], "redacted")

    def test_put_server_keeps_password_and_never_echoes(self):
        t = self.c.put("/api/services/tr069", {"server_url": "acs.example.net", "server_port": 8080})
        call = self.last("tr069_cli", "set_acs_server_config")
        self.assertEqual(call["answers"], ["easycwmp", "redacted", "acs.example.net", "8080", "1", "1"])
        self.assert_spec_ok(call)
        self.assertEqual(t["server_url"], "acs.example.net")
        t = self.c.put("/api/services/tr069", {"client_password": "S3cret-Pass", "client_inform_s": 300})
        call = self.last("tr069_cli", "set_acs_client_config")
        self.assertEqual(call["answers"][1], "S3cret-Pass")
        self.assertEqual(call["answers"][5], "300")
        self.assertNotIn("S3cret-Pass", json.dumps(t))
        audit = read_text(os.path.join(self.c.app._test_state_dir, "audit.log"))
        self.assertNotIn("S3cret-Pass", audit)
        self.c.put("/api/services/tr069", {"client_inform_s": 601}, expect=400)
        self.c.put("/api/services/tr069", {"server_url": "has space"}, expect=400)
        self.c.put("/api/services/tr069", {"server_username": "x" * 97}, expect=400)

    def test_tiers(self):
        self.c.put("/api/services/tr069/status", {"enabled": True}, expect=428)
        t = self.c.put("/api/services/tr069/status", self.c.confirm({"enabled": True}))
        self.assertTrue(t["enabled"])
        self.assertFalse(self.c.put("/api/services/tr069/status", {"enabled": False})["enabled"])
        self.c.post("/api/services/tr069/restart", {}, expect=428)
        self.c.post("/api/services/tr069/restart", self.c.confirm())
        self.assertEqual(self.last("tr069_cli", "tr069_restart")["args"], [])

    def test_cbsd_read_only(self):
        d = self.c.get("/api/services/cbsd")
        self.assertEqual(d["antenna"], {"azimuth": 0, "downtilt": 0})
        self.assertTrue(d["info_note"])
        self.assertIsNone(d["registration"])
        self.assertFalse(d["certificates"]["passphrase_set"])
        paths = [x["path"] for x in self.c.app.router.describe() if x["path"].startswith("/api/services/cbsd")]
        methods = [x["method"] for x in self.c.app.router.describe() if x["path"].startswith("/api/services/cbsd")]
        self.assertEqual(set(methods), {"GET"}, paths)


# ------------------------------------------------------------------------------------------ privacy
class PrivacyTests(Base):
    def test_services_disable_enable(self):
        lst = self.c.get("/api/privacy/services")["services"]
        names = [s["name"] for s in lst]
        for n in ("cloud_engined", "deviceagentd", "omadmd", "nuad", "duad", "wdcp", "tr069d", "ccd", "longshipd", "agentmd"):
            self.assertIn(n, names)
        cloud = [s for s in lst if s["name"] == "cloud_engined"][0]
        self.assertTrue(cloud["watched"])
        self.c.post("/api/privacy/services/cloud_engined/disable", {}, expect=428)
        s = self.c.post("/api/privacy/services/cloud_engined/disable", self.c.confirm())
        self.assertTrue(s["blocked"])
        self.assertFalse(s["running"])
        self.assertEqual(s["enabled"], "disabled")
        st = self.c.app.state.read_json("privacy.json")
        self.assertEqual(st["services"]["cloud_engined"]["was_enabled"], {"cloud_engined.service": "enabled"})
        s = self.c.post("/api/privacy/services/nuad/disable", self.c.confirm())
        self.assertEqual(len(s["units"]), 2)
        self.assertTrue(all(u["blocked"] for u in s["units"]))
        s = self.c.post("/api/privacy/services/cloud_engined/enable", self.c.confirm())
        self.assertFalse(s["blocked"])
        self.assertTrue(s["running"])
        self.assertEqual(s["enabled"], "enabled")
        self.c.post("/api/privacy/services/sshd/disable", self.c.confirm(), expect=404)

    def test_dropin_files_real_mode(self):
        # exercise the real (non-mock) drop-in writer against a temp systemd dir
        import tempfile
        d = tempfile.mkdtemp()
        app = self.c.app
        app.cfg["systemd_dir"] = d
        c = MockClient(app)                       # log in while the password still comes from the mock
        app.mock = False
        try:
            from m3100ui.router import Router
            app.router = Router()
            calls = []
            app.system.systemctl = lambda action, unit: (calls.append((action, unit)) or
                                                         {"ok": True, "output": {"is-active": "active",
                                                                                 "is-enabled": "enabled"}.get(action, "")})
            app.system.run = lambda argv, timeout=15.0, max_bytes=0: (calls.append(tuple(argv)) or
                                                                       {"ok": True, "rc": 0, "output": ""})
            priv.register(app)
            c.post("/api/privacy/services/wdcp/disable", c.confirm())
            path = os.path.join(d, "wdcp.service.d", priv.DROPIN_NAME)
            self.assertTrue(os.path.isfile(path))
            self.assertIn("ConditionPathExists", read_text(path))
            self.assertIn(("systemctl", "daemon-reload"), calls)
            self.assertIn(("stop", "wdcp.service"), calls)
            self.assertIn(("disable", "wdcp.service"), calls)
            c.post("/api/privacy/services/wdcp/enable", c.confirm())
            self.assertFalse(os.path.exists(path))
            self.assertIn(("enable", "wdcp.service"), calls)
            self.assertIn(("start", "wdcp.service"), calls)
        finally:
            app.mock = True

    def test_vendor_logs(self):
        self.c.put("/api/privacy/vendor-logs", {"enabled": True}, expect=428)
        s = self.c.put("/api/privacy/vendor-logs", self.c.confirm({"enabled": True}))
        self.assertTrue(s["active"])
        self.assertTrue(s["enabled"])
        s = self.c.put("/api/privacy/vendor-logs", self.c.confirm({"enabled": True}))      # idempotent
        self.assertTrue(s["active"])
        s = self.c.put("/api/privacy/vendor-logs", self.c.confirm({"enabled": False}))
        self.assertFalse(s["active"])
        self.assertFalse(s["enabled"])

    def test_vendor_logs_real_mode_refuses_foreign_mount(self):
        import tempfile
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "proc"))
        os.makedirs(os.path.join(root, "opt", "nvtl", "log", "modem2"))
        with open(os.path.join(root, "proc", "mounts"), "w") as f:
            f.write("/dev/ubi0 /opt/nvtl/log ubifs rw 0 0\n")
        app = self.c.app
        c = MockClient(app)
        app.mock = False
        app.system.root = root
        try:
            from m3100ui.router import Router
            app.router = Router()
            ran = []
            app.system.run = lambda argv, timeout=15.0, max_bytes=0: ran.append(tuple(argv)) or {"ok": True, "output": ""}
            priv.register(app)
            s = c.get("/api/privacy/vendor-logs")
            self.assertTrue(s["foreign_mount"])
            c.put("/api/privacy/vendor-logs", c.confirm({"enabled": True}), expect=503)
            c.put("/api/privacy/vendor-logs", c.confirm({"enabled": False}), expect=503)
            self.assertFalse([a for a in ran if a[0] in ("mount", "umount")])
            with open(os.path.join(root, "proc", "mounts"), "w") as f:
                f.write("tmpfs /tmp tmpfs rw 0 0\n")
            c.put("/api/privacy/vendor-logs", c.confirm({"enabled": True}))
            self.assertEqual(ran[-1][:3], ("mount", "-t", "tmpfs"))
            self.assertEqual(ran[-1][-2:], (priv.LOG_MOUNT_SOURCE, priv.LOG_DIR))
        finally:
            app.mock = True
            app.system.root = ""

    def test_health_upload(self):
        u = self.c.put("/api/privacy/health-upload", {"enabled": False})
        self.assertFalse(u["enabled"])
        call = self.last("mifi_health_cli", "set_log_settings")
        self.assertEqual(call["answers"], ["0", "86400"])
        self.assert_spec_ok(call)
        self.c.put("/api/privacy/health-upload", {"interval_s": 10}, expect=400)


class PagesTests(Base):
    def test_pages_listed(self):
        pages = self.c.get("/api/ui/pages")
        for p in ("/app/pages/device/power.js", "/app/pages/device/display.js", "/app/pages/device/health.js",
                  "/app/pages/services/gps.js", "/app/pages/services/files.js", "/app/pages/services/notifications.js",
                  "/app/pages/services/tr069.js", "/app/pages/services/cbsd.js", "/app/pages/services/privacy.js"):
            self.assertIn(p, pages)
        self.assertFalse([p for p in pages if "/_" in p])

    def test_no_vendor_names_in_pages(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app", "pages")
        for sub in ("device", "services"):
            for fn in os.listdir(os.path.join(root, sub)):
                txt = read_text(os.path.join(root, sub, fn)).lower()
                for bad in ("verizon", "inseego", "mifi", "jetpack", "novatel"):
                    self.assertNotIn(bad, txt, "%s mentions %s" % (fn, bad))


if __name__ == "__main__":
    unittest.main()
