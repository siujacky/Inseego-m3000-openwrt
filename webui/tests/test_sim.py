"""SIM / APN / SMS / data-usage area: parsers on the real captures, routes on fixtures (no SIM) and on the
stateful mock (test SIM), setter answer lists against the Specs, validation errors and tier-2/3 confirmation."""
import unittest

from tests.helpers import MockClient, read_fixture

from m3100ui.adapters import cli_specs
from m3100ui.adapters.cli import CliResult
from m3100ui.adapters.mock import REST_HANDLERS
from m3100ui.adapters.specs import sim as sim_specs
from m3100ui.errors import ApiError
from m3100ui.mock_handlers import sim as mock
from m3100ui.services import apn as apn_svc
from m3100ui.services import sim as sim_svc
from m3100ui.services import sms as sms_svc
from m3100ui.services import usage as usage_svc


def res(cli, cmd, text=None):
    return CliResult(cli, cmd, read_fixture(cli, cmd) if text is None else text, 0)


class Base(unittest.TestCase):
    SIM = True
    MULTI = False
    LOCKED = False

    def setUp(self):
        mock.reset_state(sim_present=self.SIM, multi_sim=self.MULTI, carrier_locked=self.LOCKED)
        self.c = MockClient()

    def tearDown(self):
        self.c.cleanup()
        mock.reset_state()

    def last(self, cli=None, cmd=None):
        calls = self.c.app.cli.store.calls
        if cmd:
            calls = [x for x in calls if x["cli"] == cli and x["cmd"] == cmd]
        self.assertTrue(calls, "no setter call recorded for %s %s" % (cli, cmd))
        return calls[-1]

    def poke(self):
        self.c.app.cache.invalidate("")

    def spec_count(self, cli, cmd, values):
        return cli_specs.spec(cli, cmd).expected_count(values)


# ------------------------------------------------------------------------------- parsers
class ParserTests(unittest.TestCase):
    def test_sim_status_fixtures(self):
        r = res("modem2_cli", "sim_get_status")
        self.assertEqual(r.get_int("status is"), 2)
        self.assertEqual(r.note("status is"), "(SIM_NOT_FOUND)")
        p = res("modem2_cli", "sim_pin_get_status")
        self.assertEqual(p.get_int("status is"), 0)
        self.assertEqual(p.get_int("unlocks is"), 0)

    def test_carrier_and_gid(self):
        self.assertEqual(sim_svc.carrier_name("SIM_CARRIER_INFO_NOT_AVAIL"), "")
        self.assertEqual(sim_svc.carrier_name("SIM_CARRIER_UNKNOWN(9)"), "")
        self.assertEqual(sim_svc.carrier_name("SIM_CARRIER_TMOBILE"), "Tmobile")
        self.assertEqual(sim_svc.parse_gid("gid_length : 2\ngid1 [1] : 0x 5\ngid1 [2] : 0xff\n"), [5, 255])
        self.assertEqual(sim_svc.parse_gid(read_fixture("modem2_cli", "sim_get_gid1")), [])

    def test_slot_blocks_fixture(self):
        blocks = sim_svc.parse_slot_blocks(read_fixture("modem2_cli", "sim_mgr_get_info"))
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[0]["active"])
        self.assertFalse(blocks[0]["present"])
        self.assertEqual(blocks[0]["sim_state"], "not_found")
        self.assertEqual(blocks[1]["sim_state"], "unknown")
        self.assertEqual(blocks[0]["wwan_state"], "none")
        one = sim_svc.parse_slot_blocks(read_fixture("modem2_cli", "sim_mgr_get_slot_info"))
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0]["operator"], "")
        phy = sim_svc.parse_phy_slots(read_fixture("modem2_cli", "uim_get_physical_slot_status"))
        self.assertEqual(phy, [{"index": 0, "present": False, "status": "absent", "active": True, "logical_slot": 1,
                                "iccid_set": False}])

    def test_carrier_lock_fixture(self):
        r = res("modem2_cli", "get_carrier_unlock")
        self.assertEqual((r.get_int("State"), r.get_int("Carrier block"), r.get_int("Verify retries")), (0, 0, 10))
        self.assertEqual(res("modem2_cli", "get_sim_lock").get_int("unlock"), 0)

    def test_sim_mgr_fixtures(self):
        r = res("sim_mgr_cli", "get_all_recovery_configs")
        self.assertIsNone(r.rc)                       # core regex misses '…: [recovery] returned 0 (…)'
        self.assertEqual(sim_svc.rc_of(r), 0)
        self.assertEqual(r.get_int("period"), 120)
        self.assertEqual(r.get_int("system_reset_cnt"), 3)
        cc = res("sim_mgr_cli", "get_conn_configs")
        self.assertEqual((cc.get_int("connection interval"), cc.get_int("connection retriies")), (30, 5))
        self.assertEqual(res("sim_mgr_cli", "get_conn_recovery_mode").get("conn recovery mode"), "Enabled")
        self.assertEqual(res("sim_mgr_cli", "get_is_multi_sim_supported").get_int("is_multi_sim_supported"), 0)
        self.assertEqual(res("sim_mgr_cli", "get_switch_mode").get_int("switch_mode"), 0)
        self.assertFalse(sim_svc.ok_of(res("sim_mgr_cli", "is_running")))

    def test_apn_fixtures(self):
        fb = apn_svc.parse_profile_list(read_fixture("modem2_cli", "get_failback_apn_list"))
        self.assertEqual(len(fb), 4)
        self.assertEqual(fb[1]["apn"], "b2b.static")
        self.assertEqual(apn_svc.parse_profile_list(read_fixture("modem2_cli", "get_multi_apn_list")), [])
        pri = apn_svc.parse_tech_profile(res("modem2_cli", "prof_get_pri_tech"))
        self.assertEqual(pri["apn"], "vzwinternet")
        self.assertTrue(pri["manual_dns"])
        self.assertTrue(apn_svc.dns_auto(pri))
        self.assertEqual(pri["pdp"], "ipv4v6")
        self.assertEqual(pri["ipv6_pref"], "::")
        cust = apn_svc.parse_tech_profile(res("modem2_cli", "prof_get_cust_tech"))
        self.assertEqual(cust["apn"], "ims")
        act = res("modem2_cli", "prof_get_act_tech")
        self.assertEqual(act.get_int("idx"), 3)
        self.assertFalse(res("modem2_cli", "prof_get_tech").ok)          # captured with EOF answers -> rc 2
        self.assertFalse(res("modem2_cli", "multi_apn_get_default_profile").ok)
        d = apn_svc.parse_default_profile("profile.id:[1]\nprofile.apn:[x.y]\n")
        self.assertEqual(d, {"id": "1", "apn": "x.y"})

    def test_sms_parsers(self):
        self.assertEqual(sms_svc.parse_list(read_fixture("sms_cli", "get_list")), [])
        rows = sms_svc.parse_list("count:[2]\nlist[0].id:[7]\nlist[0].state:[Unread]\nlist[0].storage:[1]\n"
                                  "list[1].id:[9]\nlist[1].state:[Read]\nlist[1].storage:[0]\n")
        self.assertEqual(rows, [{"id": 7, "unread": True, "storage": 1}, {"id": 9, "unread": False, "storage": 0}])
        self.assertEqual(sms_svc.parse_cli_time("Thu Jan  1 00:00:00 1970"), 0)
        self.assertEqual(sms_svc.parse_rest_time("1970-01-01 00:01:00"), 60)
        self.assertEqual(sms_svc.parse_rest_time(120), 120)
        self.assertEqual(res("sms_cli", "get_unread").get_int("count"), 0)
        self.assertEqual(res("sms_cli", "ab_get_list").get_int("count"), 0)

    def test_sms_encoding(self):
        e = sms_svc.sms_encoding("a" * 160)
        self.assertEqual((e["encoding"], e["parts"], e["left"]), ("gsm7", 1, 0))
        e = sms_svc.sms_encoding("a" * 161)
        self.assertEqual((e["parts"], e["left"]), (2, 145))
        self.assertEqual(sms_svc.sms_encoding("{}")["units"], 4)
        e = sms_svc.sms_encoding("你好")
        self.assertEqual((e["encoding"], e["units"], e["left"], e["ascii"]), ("ucs2", 2, 68, False))
        self.assertEqual(sms_svc.sms_encoding("x" * 71 + "ç你")["parts"], 2)

    def test_usage_fixtures(self):
        info = usage_svc.parse_usage_info(res("dua_vzw_cli", "get_interface_data_usage_info"))
        self.assertFalse(info["valid"])
        self.assertEqual(info["total_bytes"], 0)
        self.assertEqual(usage_svc.size_bytes("20.00GB"), 20 * 2 ** 30)
        self.assertEqual(usage_svc.limit_key("500 MB"), "500MB")
        self.assertIsNone(usage_svc.limit_key("50 GB"))
        lt = res("modem2_cli", "lifetime_counters_get")
        self.assertEqual(lt.rc, 1)
        st = res("dua_vzw_cli", "get_active_interface_level_info")
        self.assertIsNone(st.rc)                      # 'dua_get_level_info0 (DUA: success.)' has no 'returned'

    def test_presets_file(self):
        data = apn_svc.presets()
        self.assertGreater(data["count"], 90)
        self.assertEqual(data["count"], len(data["presets"]))
        for p in data["presets"]:
            self.assertRegex(p["mccmnc"], r"^[0-9]{5,6}$")
            self.assertEqual(p["mcc"] + p["mnc"], p["mccmnc"])
            self.assertTrue(p["apn"])
            self.assertIn(p["auth"], ("none", "pap", "chap", "pap_chap"))
        srcs = {s for p in data["presets"] for s in p["sources"]}
        self.assertEqual(srcs, {"stock", "m2000", "m8800l"})


class SpecTests(unittest.TestCase):
    def test_specs_loaded_and_classified(self):
        allspecs = cli_specs.all_specs()
        for key, sp in sim_specs.SPECS.items():
            self.assertIn(key, allspecs, "spec %s %s was dropped (duplicate?)" % key)
            self.assertEqual(cli_specs.classify(*key), "setter", key)
            self.assertTrue(sp.evidence, key)
            # written as "static"; fixtures/sandbox/verified.json upgrades the ones confirmed in the device sandbox
            self.assertIn(allspecs[key].source, ("static", "sandbox", "roundtrip"))
            sp.check(sp.sample_answers())               # sample answers satisfy the prompt count
        self.assertNotIn("sim_specs", cli_specs.SPEC_ERRORS)

    def test_secrets_use_stdin(self):
        for cmd in ("sim_unlock_pin", "sim_unlock_puk", "sim_enable_pin", "sim_change_pin", "unlock_carrier"):
            sp = sim_specs.SPECS[("modem2_cli", cmd)]
            self.assertEqual(sp.mode, "stdin", cmd)
            self.assertEqual(sp.tier, 3, cmd)

    def test_counts(self):
        self.assertEqual(len(sim_specs.SPECS[("modem2_cli", "prof_set_tech")].prompts), 18)
        self.assertEqual(len(sim_specs.SPECS[("modem2_cli", "prof_set_cust_tech")].prompts), 17)
        self.assertEqual(len(sim_specs.SPECS[("modem2_cli", "multi_apn_add_profile")].prompts), 10)
        self.assertEqual(len(sim_specs.SPECS[("modem2_cli", "multi_apn_update_profile")].prompts), 11)
        self.assertEqual(len(sim_specs.SPECS[("dua_vzw_cli", "set_data_usage_settings_info")].prompts), 6)

    def test_not_redefining_core(self):
        from m3100ui.adapters.specs import core
        self.assertFalse(set(core.SPECS) & set(sim_specs.SPECS))


# ------------------------------------------------------------------- routes on the fixtures
class FixtureRouteTests(Base):
    SIM = False

    def test_sim_no_sim(self):
        st = self.c.get("/api/sim/status")
        self.assertFalse(st["present"])
        self.assertEqual(st["state"], "not_found")
        self.assertEqual(st["actions"], [])
        self.assertEqual(st["identity"]["iccid"], "")
        self.assertTrue(st["identity_set"]["imei"])
        self.assertIn("•", st["identity"]["imei"])
        self.assertEqual(st["home_network"], {"mcc": "", "mnc": ""})
        sec = self.c.get("/api/sim/identity/secret")
        self.assertEqual(sec["imei"], "350000000000001")
        self.c.post("/api/sim/pin/verify", self.c.confirm({"pin": "1234"}, 3), expect=503)

    def test_lock_account_slots(self):
        lk = self.c.get("/api/sim/lock")
        self.assertFalse(lk["carrier_locked"])
        self.assertFalse(lk["can_unlock"])
        self.assertEqual(lk["verify_retries"], 10)
        self.assertTrue(lk["nv60002"]["available"])
        self.assertEqual(lk["nv60002"]["value"], 0)
        self.assertEqual(lk["sim_lock_policy"]["value"], 0)
        self.assertEqual(lk["sim_restriction"], {"value": 0, "label": "Carrier network only"})
        self.assertFalse(self.c.get("/api/sim/account")["restricted"])
        sl = self.c.get("/api/sim/slots")
        self.assertFalse(sl["supported"])
        self.assertEqual(sl["active_slot"], 1)
        self.assertEqual(len(sl["slots"]), 2)
        self.assertEqual(sl["conn_interval"], 30)
        self.assertFalse(sl["manager_running"])
        self.c.post("/api/sim/slots/switch", self.c.confirm({"slot": 2}), expect=503)
        rc = self.c.get("/api/sim/slots/recovery")
        self.assertEqual(rc["period_min"], 120)
        self.assertFalse(self.c.get("/api/meta/full")["features"]["sim.multi"])

    def test_apn_no_sim(self):
        self.assertEqual(self.c.get("/api/apn/active")["apn"], "internet.mock")
        p = self.c.get("/api/apn/profiles")
        self.assertEqual(p["profiles"], [])
        self.assertFalse(p["sim_present"])
        self.c.post("/api/apn/profiles", {"name": "x", "apn": "internet"}, expect=503)
        self.assertFalse(self.c.get("/api/apn/lookup")["available"])
        self.assertFalse(self.c.get("/api/apn/default")["available"])
        fb = self.c.get("/api/apn/failback")
        self.assertEqual(len(fb["apns"]), 4)
        self.assertFalse(fb["enabled"])
        self.assertEqual(self.c.get("/api/apn/presets", query={"sim": "1"})["count"], 0)

    def test_sms_and_usage_no_sim(self):
        self.assertEqual(self.c.get("/api/sms/messages")["messages"], [])
        self.assertEqual(self.c.get("/api/sms/messages", query={"box": "sent"})["messages"], [])
        self.assertEqual(self.c.get("/api/sms/status")["unread"], 0)
        u = self.c.get("/api/usage")
        self.assertFalse(u["sim_present"])
        self.assertFalse(u["cycle"]["valid"])
        self.assertFalse(u["lifetime"]["available"])
        self.assertIsNone(u["plan"]["alert_percent"])   # uninitialised buffer in the capture is not shown
        self.assertEqual(u["plan"]["limit_text"], "")
        self.assertTrue(any(i["kind"] == "cellular" for i in u["interfaces"]))
        self.assertFalse(u["reset_supported"])
        self.c.put("/api/usage/plan", {"cycle_day": 5}, expect=503)


# ---------------------------------------------------------------------- SIM PIN / PUK flows
class PinTests(Base):
    def status(self):
        self.poke()
        return self.c.get("/api/sim/status")

    def test_enable_lock_tier3_and_answers(self):
        st = self.status()
        self.assertEqual(st["state"], "ready")
        self.assertEqual(st["actions"], ["enable_lock"])
        self.c.post("/api/sim/pin/lock", {"enabled": True, "pin": "1234"}, expect=428)
        bad = self.c.confirm({"enabled": True, "pin": "1234"}, 3)
        bad["password"] = "wrong"
        self.c.post("/api/sim/pin/lock", bad, expect=403)
        out = self.c.post("/api/sim/pin/lock", self.c.confirm({"enabled": True, "pin": "1234"}, 3))
        self.assertTrue(out["pin"]["lock_enabled"])
        call = self.last("modem2_cli", "sim_enable_pin")
        self.assertEqual(call["answers"], ["1", "1234"])
        self.assertEqual(len(call["answers"]), self.spec_count("modem2_cli", "sim_enable_pin", call["answers"]))
        self.assertEqual(out["actions"], ["disable_lock", "change_pin"])

    def test_validation(self):
        self.c.post("/api/sim/pin/lock", self.c.confirm({"enabled": True, "pin": "12a4"}, 3), expect=400)
        e = self.c.post("/api/sim/pin/lock", self.c.confirm({"enabled": True, "pin": "12"}, 3), expect=400)
        self.assertEqual(e["error"]["details"]["field"], "pin")
        self.c.post("/api/sim/pin/lock", self.c.confirm({"enabled": True, "pin": "1234", "x": 1}, 3), expect=400)
        self.c.post("/api/sim/pin/unblock", self.c.confirm({"puk": "1234567", "new_pin": "1234"}, 3), expect=400)
        self.c.post("/api/sim/pin/verify", self.c.confirm({"pin": "1234"}, 3), expect=409)   # not locked

    def test_wrong_pin_and_last_attempt(self):
        e = self.c.post("/api/sim/pin/lock", self.c.confirm({"enabled": True, "pin": "9999"}, 3), expect=400)
        self.assertEqual(e["error"]["details"]["field"], "pin")
        self.assertIn("2 attempt", e["error"]["message"])
        mock.state()["sim"]["pin_retries"] = 1
        self.poke()
        e = self.c.post("/api/sim/pin/lock", self.c.confirm({"enabled": True, "pin": "1234"}, 3), expect=409)
        self.assertEqual(e["error"]["code"], "last_attempts")
        ok = self.c.post("/api/sim/pin/lock", self.c.confirm({"enabled": True, "pin": "1234", "last_attempt": True}, 3))
        self.assertEqual(ok["pin"]["retries"], 3)

    def test_verify_change_and_puk(self):
        s = mock.state()["sim"]
        s.update(pin_enabled=True, verified=False)
        st = self.status()
        self.assertEqual(st["state"], "pin_locked")
        self.assertEqual(st["actions"], ["verify_pin"])
        out = self.c.post("/api/sim/pin/verify", self.c.confirm({"pin": "1234"}, 3))
        self.assertEqual(out["state"], "unlocked")
        self.assertEqual(self.last("modem2_cli", "sim_unlock_pin")["answers"], ["1234"])
        self.c.post("/api/sim/pin/change", self.c.confirm({"pin": "1234", "new_pin": "5678"}, 3))
        self.assertEqual(self.last("modem2_cli", "sim_change_pin")["answers"], ["1234", "5678"])
        self.assertEqual(s["pin"], "5678")
        # block the PIN -> PUK needed
        s.update(pin_retries=0, verified=False)
        st = self.status()
        self.assertEqual(st["state"], "puk_locked")
        self.assertEqual(st["actions"], ["unblock_puk"])
        self.c.post("/api/sim/pin/verify", self.c.confirm({"pin": "5678"}, 3), expect=503)
        e = self.c.post("/api/sim/pin/unblock", self.c.confirm({"puk": "00000000", "new_pin": "2468"}, 3), expect=400)
        self.assertIn("9 attempt", e["error"]["message"])
        s["puk_retries"] = 3
        self.poke()
        e = self.c.post("/api/sim/pin/unblock", self.c.confirm({"puk": "12345678", "new_pin": "2468"}, 3), expect=409)
        self.assertEqual(e["error"]["details"]["need"], "last_attempt")
        out = self.c.post("/api/sim/pin/unblock",
                          self.c.confirm({"puk": "12345678", "new_pin": "2468", "last_attempt": True}, 3))
        self.assertIn(out["state"], ("ready", "unlocked"))
        self.assertEqual(self.last("modem2_cli", "sim_unlock_puk")["answers"], ["12345678", "2468"])
        s["puk_retries"] = 0
        s["pin_retries"] = 0
        self.poke()
        self.assertEqual(self.c.get("/api/sim/status")["state"], "perm_blocked")

    def test_secret_identity(self):
        sec = self.c.get("/api/sim/identity/secret")
        self.assertEqual(sec["imsi"], "001010123456789")
        self.assertEqual(sec["iccid"], "8900101234567890123")
        st = self.c.get("/api/sim/status")
        self.assertEqual(st["identity"]["imsi"][-4:], "6789")
        self.assertNotIn("0010101", st["identity"]["imsi"])
        self.assertEqual(st["home_network"], {"mcc": "001", "mnc": "01"})
        self.assertEqual(st["gid1"], "FF")


class CarrierLockTests(Base):
    LOCKED = True

    def test_unlock_flow(self):
        lk = self.c.get("/api/sim/lock")
        self.assertTrue(lk["carrier_locked"])
        self.assertTrue(lk["can_unlock"])
        self.assertEqual(lk["nv60002"]["value"], 1)
        self.c.post("/api/sim/carrier-unlock", {"code": "11112222"}, expect=428)
        self.c.post("/api/sim/carrier-unlock", self.c.confirm({"code": "12"}, 3), expect=400)
        e = self.c.post("/api/sim/carrier-unlock", self.c.confirm({"code": "11112222"}, 3), expect=400)
        self.assertEqual(e["error"]["details"]["field"], "code")
        self.assertIn("9 attempt", e["error"]["message"])
        self.assertEqual(self.last("modem2_cli", "unlock_carrier")["answers"], ["11112222"])
        self.c.post("/api/sim/carrier-unlock", self.c.confirm({"code": "13572468"}, 3), expect=429)   # throttled
        self.c.app.cache.invalidate("")
        mock.state()["carrier"]["verify"] = 1
        self.c = MockClient()        # fresh app = fresh throttle
        e = self.c.post("/api/sim/carrier-unlock", self.c.confirm({"code": "13572468"}, 3), expect=409)
        self.assertEqual(e["error"]["code"], "last_attempts")
        out = self.c.post("/api/sim/carrier-unlock", self.c.confirm({"code": "13572468", "last_attempt": True}, 3))
        self.assertFalse(out["carrier_locked"])
        self.assertEqual(out["nv60002"]["value"], 0)

    def test_blocked(self):
        mock.state()["carrier"]["block"] = 1
        lk = self.c.get("/api/sim/lock")
        self.assertFalse(lk["can_unlock"])
        self.c.post("/api/sim/carrier-unlock", self.c.confirm({"code": "13572468"}, 3), expect=503)


class AccountAndSlotTests(Base):
    MULTI = True

    def test_pco(self):
        self.c.app.cli.store.state["pco"] = (3, 3)        # shared PCO mock (mock_handlers/tools.py)
        a = self.c.get("/api/sim/account")
        self.assertTrue(a["restricted"])
        self.assertIn("credit", a["message"])
        self.c.post("/api/sim/account/accept", {})
        call = self.last("ccm2_cli", "accept_pco_event")
        self.assertEqual((call["args"], call["answers"]), ([], []))
        self.c.post("/api/sim/account/accept", {"x": 1}, expect=400)

    def test_slots(self):
        sl = self.c.get("/api/sim/slots")
        self.assertTrue(sl["supported"])
        self.assertEqual(sl["num_sims"], 2)
        self.assertTrue(sl["slots"][0]["active"])
        self.assertTrue(sl["slots"][1]["present"])
        self.assertTrue(self.c.get("/api/meta/full")["features"]["sim.multi"])
        self.c.post("/api/sim/slots/switch", {"slot": 2}, expect=428)
        self.c.post("/api/sim/slots/switch", self.c.confirm({"slot": 3}), expect=400)
        self.c.post("/api/sim/slots/switch", self.c.confirm({"slot": 1}), expect=409)
        out = self.c.post("/api/sim/slots/switch", self.c.confirm({"slot": 2}))
        self.assertEqual(out["active_slot"], 2)
        self.assertEqual(out["previous_slot"], 1)
        self.assertEqual(self.last("modem2_cli", "sim_mgr_switch_slot")["args"], ["2"])
        self.c.put("/api/sim/slots/settings", {"auto_switch": True}, expect=428)
        out = self.c.put("/api/sim/slots/settings", self.c.confirm({"auto_switch": True, "conn_monitor": False,
                                                                    "conn_interval": 60}))
        self.assertTrue(out["auto_switch"])
        self.assertFalse(out["conn_monitor"])
        self.assertEqual(out["conn_interval"], 60)
        self.assertEqual(self.last("sim_mgr_cli", "set_switch_mode")["answers"], ["1"])
        self.assertEqual(self.last("sim_mgr_cli", "set_conn_configs")["answers"], ["60", "5"])
        self.c.put("/api/sim/slots/settings", {"sim_interval": 5}, expect=400)
        out = self.c.put("/api/sim/slots/priority", {"slot": 2, "priority": 1})
        self.assertEqual(self.last("modem2_cli", "sim_mgr_set_imsi_priority")["args"], ["001010987654321", "1"])
        rc = self.c.put("/api/sim/slots/recovery", {"period_min": 240, "max_modem_resets": 2})
        self.assertEqual((rc["period_min"], rc["max_modem_resets"]), (240, 2))
        self.assertEqual(self.last("sim_mgr_cli", "set_recovery_period")["answers"], ["240"])
        self.c.put("/api/sim/slots/recovery", {"period_min": 10}, expect=400)
        self.c.put("/api/sim/slots/recovery", {"bogus": 1}, expect=400)


# ------------------------------------------------------------------------------------- APN
class ApnTests(Base):
    def test_active_apn_tier2_snapshot_restore(self):
        self.c.put("/api/apn/active", {"apn": "fast.example"}, expect=428)
        self.c.put("/api/apn/active", self.c.confirm({"apn": "bad apn!"}), expect=400)
        self.c.put("/api/apn/active", self.c.confirm({"auth": "pap"}), expect=400)
        self.c.put("/api/apn/active", self.c.confirm({"auth": "both"}), expect=400)
        out = self.c.put("/api/apn/active", self.c.confirm({"apn": "fast.example", "auth": "chap", "username": "u",
                                                            "password": "p"}))
        self.assertEqual((out["apn"], out["auth"], out["username"], out["password_set"]), ("fast.example", "chap", "u", True))
        self.assertEqual(out["previous"]["apn"], "internet.mock")
        put = [x for x in self.c.app.rest.calls if x["method"] == "PUT" and x["path"] == "APNProfiles/3GPP/active"][-1]
        self.assertEqual(put["body"]["PDPAuthenticationType"], "CHAP")
        self.assertEqual(self.c.get("/api/apn/active/secret")["password"], "p")
        self.c.post("/api/apn/active/restore", {}, expect=428)
        back = self.c.post("/api/apn/active/restore", self.c.confirm({}))
        self.assertEqual(back["apn"], "internet.mock")
        self.assertEqual(back["auth"], "none")

    def test_profiles_crud(self):
        spec = cli_specs.spec("modem2_cli", "multi_apn_add_profile")
        out = self.c.post("/api/apn/profiles", {"name": "Work", "apn": "work.example", "auth": "pap", "username": "me",
                                                "password": "secret", "pdp": "ipv4"})
        add = self.last("modem2_cli", "multi_apn_add_profile")
        self.assertEqual(len(add["answers"]), len(spec.prompts))
        self.assertEqual(add["answers"], ["Work", "001010123456789", "8900101234567890123", "0", "0", "work.example", "me",
                                          "secret", "1", "0"])
        work = [p for p in out["profiles"] if p["name"] == "Work"][0]
        self.assertTrue(work["password_set"])
        self.assertNotIn("password", work)
        self.assertTrue(work["this_sim"])
        self.assertEqual(self.c.get("/api/apn/profiles/%d/secret" % work["id"])["password"], "secret")
        self.c.post("/api/apn/profiles", {"name": "work", "apn": "x"}, expect=400)          # duplicate name
        self.c.post("/api/apn/profiles", {"name": "A", "apn": "x y"}, expect=400)
        self.c.post("/api/apn/profiles", {"name": "B", "apn": "x", "auth": "chap"}, expect=400)
        self.c.post("/api/apn/profiles", {"name": "C", "apn": "x", "pdp": "ppp"}, expect=400)
        self.c.post("/api/apn/profiles", {"name": "C", "apn": "x", "active": True}, expect=428)
        # edit (not active -> tier 1), keeps the password
        self.c.put("/api/apn/profiles/%d" % work["id"], {"apn": "work2.example"})
        upd = self.last("modem2_cli", "multi_apn_update_profile")
        self.assertEqual(upd["answers"][0], str(work["id"]))
        self.assertEqual(upd["answers"][6], "work2.example")
        self.assertEqual(upd["answers"][8], "secret")
        self.assertEqual(len(upd["answers"]), 11)
        # activate (tier 2) -> becomes the active APN
        self.c.post("/api/apn/profiles/%d/activate" % work["id"], {}, expect=428)
        out = self.c.post("/api/apn/profiles/%d/activate" % work["id"], self.c.confirm({}))
        self.assertTrue([p for p in out["profiles"] if p["id"] == work["id"]][0]["active"])
        self.assertEqual(self.last("modem2_cli", "multi_apn_update_profile")["answers"][4], "1")
        self.assertEqual(self.c.get("/api/apn/active")["apn"], "work2.example")
        # the active profile needs tier 2 to edit / delete; the default cannot be deleted
        self.c.put("/api/apn/profiles/%d" % work["id"], {"name": "Work 2"}, expect=428)
        self.c.delete("/api/apn/profiles/1", expect=409)
        self.c.delete("/api/apn/profiles/%d" % work["id"], expect=428)
        self.c.delete("/api/apn/profiles/%d" % work["id"], self.c.confirm({}))
        self.assertEqual(self.last("modem2_cli", "multi_apn_delete_profile")["answers"], [str(work["id"])])
        self.c.delete("/api/apn/profiles/999", expect=404)
        self.c.post("/api/apn/profiles/1/restore", self.c.confirm({}))
        self.assertEqual(self.last("modem2_cli", "multi_apn_restore_default_profile")["answers"], ["1"])
        self.assertTrue(self.c.get("/api/apn/default")["available"])

    def test_presets_and_lookup(self):
        allp = self.c.get("/api/apn/presets")
        self.assertGreater(allp["total"], 90)
        one = self.c.get("/api/apn/presets", query={"mccmnc": "310260"})
        self.assertTrue(one["presets"])
        self.assertTrue(all(p["mccmnc"] == "310260" for p in one["presets"]))
        q = self.c.get("/api/apn/presets", query={"q": "jionet"})
        self.assertEqual(q["presets"][0]["apn"], "jionet")
        m = self.c.get("/api/apn/presets", query={"mcc": "302", "mnc": "610"})
        self.assertTrue(any(p["gid1"] for p in m["presets"]))
        self.c.get("/api/apn/presets", query={"mcc": "3a2"}, expect=400)
        lk = self.c.get("/api/apn/lookup")
        self.assertTrue(lk["available"])
        self.assertEqual(lk["apn"], "internet.mock")
        self.assertEqual(lk["home"], {"mcc": "001", "mnc": "01"})

    def test_failback(self):
        out = self.c.put("/api/apn/failback", {"enabled": True})
        self.assertTrue(out["enabled"])
        self.assertEqual(self.last("modem2_cli", "set_apn_failback_enabled")["args"], ["1"])
        self.c.put("/api/apn/failback", {"enabled": "maybe"}, expect=400)
        self.c.post("/api/apn/failback/switch", {}, expect=428)
        self.c.post("/api/apn/failback/switch", self.c.confirm({}))
        self.assertEqual(self.last("modem2_cli", "failback_switch_apn")["args"], [])

    def test_modem_profiles(self):
        mp = self.c.get("/api/apn/modem-profiles")
        self.assertEqual(mp["active_index"], 3)
        self.assertEqual([p["index"] for p in mp["profiles"]], [1, 2, 3, 4, 5])
        self.assertTrue([p for p in mp["profiles"] if p["index"] == 3][0]["active"])
        self.c.put("/api/apn/modem-profiles/0/3", {"apn": "x.example"}, expect=428)
        out = self.c.put("/api/apn/modem-profiles/0/3", self.c.confirm({"apn": "x.example", "pdp": "ipv4"}))
        self.assertEqual(out["apn"], "x.example")
        call = self.last("modem2_cli", "prof_set_tech")
        self.assertEqual(len(call["answers"]), 18)
        self.assertEqual(call["answers"][:3], ["0", "3", "x.example"])
        self.assertEqual(call["answers"][8:12], ["1", "0.0.0.0", "0.0.0.0", "0"])
        self.assertEqual(self.c.get("/api/apn/active")["apn"], "x.example")
        out = self.c.put("/api/apn/modem-profiles/0/3", self.c.confirm({"dns_auto": False, "dns1": "9.9.9.9"}))
        self.assertFalse(out["dns_auto"])
        self.assertEqual(self.last("modem2_cli", "prof_set_tech")["answers"][9:11], ["9.9.9.9", "0.0.0.0"])
        self.c.put("/api/apn/modem-profiles/0/3", self.c.confirm({"dns_auto": False, "dns1": "0.0.0.0"}), expect=400)
        self.c.put("/api/apn/modem-profiles/0/9", self.c.confirm({"apn": "x"}), expect=404)
        self.c.put("/api/apn/modem-profiles/0/3", self.c.confirm({"apn": "a" * 70}), expect=400)
        self.assertEqual(self.c.get("/api/apn/modem-profiles/0/3/secret")["password"], "")
        self.c.put("/api/apn/modem-profiles/active", {"type": 0, "index": 2}, expect=428)
        out = self.c.put("/api/apn/modem-profiles/active", self.c.confirm({"type": 0, "index": 2}))
        self.assertEqual(out["active_index"], 2)
        self.assertEqual(self.last("modem2_cli", "prof_set_act_tech")["args"], ["0", "2"])
        self.c.put("/api/apn/modem-profiles/active", self.c.confirm({"type": 0, "index": 77}), expect=400)

    def test_tech_profiles_and_pdn(self):
        tp = self.c.get("/api/apn/tech-profiles")
        self.assertEqual(tp["custom"]["lte"]["apn"], "ims")
        out = self.c.put("/api/apn/tech-profiles/custom/lte", self.c.confirm({"apn": "ims2"}))
        self.assertEqual(out["apn"], "ims2")
        call = self.last("modem2_cli", "prof_set_cust_tech")
        self.assertEqual(len(call["answers"]), 17)
        self.assertEqual(call["answers"][:2], ["0", "ims2"])
        self.c.put("/api/apn/tech-profiles/custom/gsm", self.c.confirm({"apn": "x"}), expect=400)
        pd = self.c.get("/api/apn/pdn")
        self.assertEqual(len(pd["profiles"]), 4)
        self.c.put("/api/apn/pdn/3", {"clat": True}, expect=428)
        out = self.c.put("/api/apn/pdn/3", self.c.confirm({"pdp_override": "ipv6", "clat": True, "wait_timer": 5}))
        self.assertEqual((out["pdp_override"], out["clat"], out["wait_timer"]), ("ipv6", True, 5))
        self.assertEqual(self.last("modem2_cli", "pdn_set_override_param")["args"], ["3", "0", "2", "1"])
        self.assertEqual(self.last("modem2_cli", "pdn_set_ext_params")["args"], ["3", "0", "1", "5", "0"])
        self.c.put("/api/apn/pdn/3", self.c.confirm({"pdp_override": "invalid"}), expect=400)


# ------------------------------------------------------------------------------------- SMS
class SmsTests(Base):
    def test_boxes_and_read_state(self):
        inbox = self.c.get("/api/sms/messages")
        self.assertEqual(inbox["count"], 2)
        self.assertEqual(inbox["unread"], 1)
        self.assertEqual(inbox["messages"][0]["contact"], "Support")
        self.assertEqual(inbox["messages"][0]["source"], "rest")
        sent = self.c.get("/api/sms/messages", query={"box": "sent"})
        self.assertEqual(sent["messages"][0]["text"], "On my way")
        self.assertEqual(sent["messages"][0]["source"], "cli")
        self.c.get("/api/sms/messages", query={"box": "trash"}, expect=400)
        self.c.post("/api/sms/messages/inbox/2/read", {"read": True})
        put = [x for x in self.c.app.rest.calls if x["method"] == "PUT" and x["path"] == "SMSList/Inbox"][-1]
        self.assertEqual(put["body"], {"SMSMessageIdentifier": 2, "SMSMessageUnreadState": False})
        self.assertEqual(self.c.get("/api/sms/status")["unread"], 0)
        self.c.post("/api/sms/messages/sent/3/read", {"read": False})
        self.assertEqual(self.last("sms_cli", "set_state")["answers"], ["3", "3", "0"])
        self.c.post("/api/sms/messages/inbox/77/read", {}, expect=404)

    def test_send(self):
        out = self.c.post("/api/sms/send", {"to": "+1 555-0100", "text": "Hello there"})
        self.assertEqual((out["via"], out["parts"], out["encoding"]), ("rest", 1, "gsm7"))
        post = [x for x in self.c.app.rest.calls if x["method"] == "POST" and x["path"] == "SMSList/Outbox"][-1]
        self.assertEqual(post["body"], {"SMSMessageAddress": "+15550100", "SMSMessageBody": "Hello there"})
        sent = self.c.get("/api/sms/messages", query={"box": "sent"})
        self.assertTrue(any(m["text"] == "Hello there" for m in sent["messages"]))
        self.c.post("/api/sms/send", {"to": "abc", "text": "x"}, expect=400)
        self.c.post("/api/sms/send", {"to": "5550100", "text": "  "}, expect=400)
        self.c.post("/api/sms/send", {"to": "5550100", "text": "x" * 460}, expect=400)
        e = self.c.post("/api/sms/send", {"to": "5550100", "text": "café"}, expect=400)
        self.assertEqual(e["error"]["details"]["field"], "text")
        long = self.c.post("/api/sms/send", {"to": "5550100", "text": "y" * 200})
        self.assertEqual(long["parts"], 2)

    def test_send_cli_fallback(self):
        key = ("POST", "SMSList/Outbox")
        orig = REST_HANDLERS[key]

        def unsupported(rest, body):
            raise ApiError(503, "unsupported", "Vendor REST POST SMSList/Outbox: unsupported", {"vendor_status": 501})
        REST_HANDLERS[key] = unsupported
        try:
            out = self.c.post("/api/sms/send", {"to": "5550100", "text": "via cli"})
            self.assertEqual(out["via"], "cli")
            call = self.last("sms_cli", "send")
            self.assertEqual(call["answers"], ["5550100", "via cli"])
            self.assertEqual(len(call["answers"]), self.spec_count("sms_cli", "send", call["answers"]))
            self.c.post("/api/sms/send", {"to": "5550100", "text": "two\nlines"}, expect=400)
        finally:
            REST_HANDLERS[key] = orig

    def test_delete(self):
        self.c.delete("/api/sms/messages/inbox/1")
        self.assertEqual(self.last("sms_cli", "delete")["answers"], ["1", "1"])
        self.assertEqual(self.c.get("/api/sms/messages")["count"], 1)
        self.c.delete("/api/sms/messages/inbox/1", expect=404)
        self.c.post("/api/sms/messages/delete", {"box": "inbox", "ids": [42]}, expect=400)
        self.c.post("/api/sms/messages/delete", {"box": "inbox", "ids": []}, expect=400)
        out = self.c.post("/api/sms/messages/delete", {"box": "sent", "all": True})
        self.assertEqual(out["deleted"], 1)
        self.assertEqual(self.c.get("/api/sms/messages", query={"box": "sent"})["count"], 0)

    def test_contacts(self):
        out = self.c.post("/api/sms/contacts", {"name": "Alice", "address": "+15550100999"})
        self.assertEqual(self.last("sms_cli", "ab_add_entry")["answers"], ["Alice", "+15550100999"])
        alice = [x for x in out["contacts"] if x["name"] == "Alice"][0]
        self.c.put("/api/sms/contacts/%d" % alice["id"], {"name": "Alice B"})
        self.assertEqual(self.last("sms_cli", "ab_edit_entry")["answers"], [str(alice["id"]), "Alice B", "+15550100999"])
        self.c.post("/api/sms/contacts", {"name": "", "address": "1234"}, expect=400)
        self.c.post("/api/sms/contacts", {"name": "Bob", "address": "12-ab"}, expect=400)
        self.c.delete("/api/sms/contacts/%d" % alice["id"])
        self.assertEqual(self.last("sms_cli", "ab_del_entry")["answers"], [str(alice["id"])])
        self.c.delete("/api/sms/contacts/77", expect=404)

    def test_encode_route(self):
        e = self.c.post("/api/sms/encode", {"text": "a" * 153 * 2 + "b"})
        self.assertEqual((e["parts"], e["encoding"]), (3, "gsm7"))


# ------------------------------------------------------------------------------- data usage
class UsageTests(Base):
    def test_usage_and_plan(self):
        u = self.c.get("/api/usage")
        for k in ("session", "plan", "cycle", "lifetime", "call", "interfaces", "limits"):
            self.assertIn(k, u)
        self.assertTrue(u["cycle"]["valid"])
        self.assertEqual(u["plan"]["limit"], "10GB")
        self.assertGreater(u["cycle"]["percent"], 0)
        self.assertTrue(u["lifetime"]["available"])
        self.assertIn("ipv4", u["call"])
        out = self.c.put("/api/usage/plan", {"cycle_day": 15, "limit": "5GB", "alert_percent": 90})
        self.assertEqual((out["cycle_day"], out["limit"], out["alert_percent"]), (15, "5GB", 90))
        call = self.last("dua_vzw_cli", "set_data_usage_settings_info")
        self.assertEqual(call["answers"], ["0", "001010123456789", "8900101234567890123", "15", "9", "90"])
        self.c.put("/api/usage/plan", {"cycle_day": 32}, expect=400)
        self.c.put("/api/usage/plan", {"limit": "7GB"}, expect=400)
        self.c.put("/api/usage/plan", {"alert_percent": 0}, expect=400)
        self.c.post("/api/usage/reset", {}, expect=503)
        self.assertFalse(self.c.get("/api/meta/full")["features"]["usage.reset"])


class TierTests(Base):
    MULTI = True
    LOCKED = True

    def test_all_tiered_routes_need_confirmation(self):
        cases = [
            ("POST", "/api/sim/pin/lock", {"enabled": True, "pin": "1234"}, 3),
            ("POST", "/api/sim/pin/verify", {"pin": "1234"}, 3),
            ("POST", "/api/sim/pin/unblock", {"puk": "12345678", "new_pin": "1234"}, 3),
            ("POST", "/api/sim/pin/change", {"pin": "1234", "new_pin": "4321"}, 3),
            ("POST", "/api/sim/carrier-unlock", {"code": "13572468"}, 3),
            ("POST", "/api/sim/slots/switch", {"slot": 2}, 2),
            ("PUT", "/api/apn/active", {"apn": "x.example"}, 2),
            ("POST", "/api/apn/active/restore", {}, 2),
            ("POST", "/api/apn/profiles/1/activate", {}, 2),
            ("POST", "/api/apn/failback/switch", {}, 2),
            ("PUT", "/api/apn/modem-profiles/0/3", {"apn": "x"}, 2),
            ("PUT", "/api/apn/modem-profiles/active", {"type": 0, "index": 3}, 2),
            ("PUT", "/api/apn/tech-profiles/custom/lte", {"apn": "x"}, 2),
            ("PUT", "/api/apn/pdn/1", {"clat": True}, 2),
        ]
        for method, path, body, tier in cases:
            e = self.c.call(method, path, body, expect=428)
            self.assertEqual(e["error"]["details"]["tier"], tier, path)
        before = len(self.c.app.cli.store.calls)
        self.assertEqual(before, len(self.c.app.cli.store.calls))


if __name__ == "__main__":
    unittest.main()
