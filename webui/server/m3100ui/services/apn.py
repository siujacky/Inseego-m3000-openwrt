"""APN: the internet APN in use, saved APN profiles (multi-APN), presets, carrier suggestion, failback,
and (expert) modem tech profiles, PRI/custom per-technology profiles and PDN parameters.

Sources:  REST APNProfiles/3GPP/active (GET/PUT, vendor validated)
          modem2_cli get_multi_apn_list / multi_apn_* (stdin only), get/set_apn_failback_enabled,
          get_failback_apn_list / get_failback_sim_list / failback_switch_apn, prof_get/set_tech,
          prof_get/set_act_tech, prof_get_pri_tech, prof_get/set_cust_tech, pdn_get/set_override_param,
          pdn_get/set_ext_params, get_apn_from_database, get_custom_apn_from_db_using_iccid
          server/m3100ui/data/apn_presets.json (stock + mod-ROM apn_database.xml, merged)
"""
import json
import os
import re
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import ApiError, field_error, not_found, unavailable, vendor_error
from .sim import cli_get, cli_set, identity_raw, mask, rest_get, sim_status, to_int

AUTH_NUM = {"none": 0, "pap": 1, "chap": 2, "pap_chap": 3}
AUTH_NAME = {v: k for k, v in AUTH_NUM.items()}
PDP_NUM = {"ipv4": 0, "ipv6": 2, "ipv4v6": 3}
PDP_NAME = {0: "ipv4", 1: "ppp", 2: "ipv6", 3: "ipv4v6"}
PDP3GPP2_NAME = {0: "ipv4", 1: "ipv6", 2: "ipv4v6", 3: "no_pref"}
REST_AUTH = {"none": "None", "pap": "PAP", "chap": "CHAP"}
TECHS = {"lte": 0, "umts": 1, "ehrpd": 2}
OVERRIDE_NAME = {0: "ipv4", 1: "invalid", 2: "ipv6", 3: "ipv4v6"}
APN_RE = r"^[A-Za-z0-9]([A-Za-z0-9._-]{0,61}[A-Za-z0-9])?$"
MAX_PROFILES = 10
PRESETS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "apn_presets.json")
PROFILE_LINE = re.compile(r"^\s*profile\[(\d+)\]\.(\w+):\[(.*)\]\s*$")
DEFAULT_LINE = re.compile(r"^\s*profile\.(\w+):\[(.*)\]\s*$")


# ---------------------------------------------------------------------------------- parsing
def parse_profile_list(text: str) -> List[Dict[str, str]]:
    """`profile[i].field:[value]` lines (get_multi_apn_list, get_failback_*_list) -> list of dicts."""
    rows: Dict[int, Dict[str, str]] = {}
    for line in (text or "").splitlines():
        m = PROFILE_LINE.match(line)
        if m:
            rows.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
    return [rows[i] for i in sorted(rows)]


def parse_default_profile(text: str) -> Dict[str, str]:
    out = {}
    for line in (text or "").splitlines():
        m = DEFAULT_LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def norm_multi(p: Dict[str, str], cur_imsi: str = "", cur_iccid: str = "") -> Dict[str, Any]:
    auth = to_int(p.get("auth"), 0)
    pdp = to_int(p.get("pdp"), 3)
    imsi, iccid = p.get("imsi", ""), p.get("iccid", "")
    return {
        "id": to_int(p.get("id"), 0), "name": p.get("name", ""), "apn": p.get("apn", ""),
        "auth": AUTH_NAME.get(auth, "none"), "username": p.get("username", ""), "password_set": bool(p.get("password")),
        "pdp": PDP_NAME.get(pdp, "ipv4v6"), "active": bool(to_int(p.get("active"), 0)),
        "default": bool(to_int(p.get("default"), 0)), "imsi_masked": mask(imsi), "iccid_masked": mask(iccid),
        "this_sim": bool((imsi and imsi == cur_imsi) or (iccid and iccid == cur_iccid)),
    }


def parse_tech_profile(res) -> Dict[str, Any]:
    g, gi = res.get, res.get_int
    return {
        "apn": g("APN") or "", "username": g("User Name") or "", "password_set": bool(g("Password")),
        "name": g("Name") or "", "locked": gi("Locked", 0), "auth": AUTH_NAME.get(gi("Authenticate", 0), "none"),
        "manual_dns": bool(gi("Manual DNS", 0)), "dns1": g("DNS1") or "", "dns2": g("DNS2") or "",
        "pdp": PDP_NAME.get(gi("PDP Type", 3), "ipv4v6"), "apn_disabled": bool(gi("APN Disabled", 0)),
        "inactivity_timeout": gi("PDN Inactivity Timeout", 0), "ipv4_pref": g("IPV4 address") or "",
        "ipv6_pref": g("IPV6 address") or "", "data_mode": gi("Data Mode", 0),
        "pdp_3gpp2": PDP3GPP2_NAME.get(gi("PDP Type 3GPP2", 0), "ipv4"),
    }


def dns_auto(p: Dict[str, Any]) -> bool:
    return not p.get("manual_dns") or (p.get("dns1") in ("", "0.0.0.0") and p.get("dns2") in ("", "0.0.0.0"))


_PRESETS: Optional[Dict[str, Any]] = None


def presets() -> Dict[str, Any]:
    global _PRESETS
    if _PRESETS is None:
        try:
            with open(PRESETS_PATH, "r", encoding="utf-8") as f:
                _PRESETS = json.load(f)
        except (OSError, ValueError):
            _PRESETS = {"presets": [], "count": 0}
    return _PRESETS


def register(app):
    r = app.router

    def refresh():
        app.cache.invalidate("apn:")
        app.cache.invalidate("status")

    def text_field(name, v, lo, hi, pattern=None, msg=None):
        s = validate.text(name, v if v is not None else "", lo, hi)
        if any(ord(c) > 126 for c in s):
            raise field_error(name, "Use plain (ASCII) characters only")
        if pattern and s and not re.match(pattern, s):
            raise field_error(name, msg or "Invalid format")
        return s

    # ------------------------------------------------------------------------- active APN
    def active() -> Dict[str, Any]:
        d = rest_get(app, "APNProfiles/3GPP/active")
        if not isinstance(d, dict):
            raise unavailable("The modem does not report an active APN profile")
        auth = str(d.get("PDPAuthenticationType", "None") or "None").lower()
        auth = {"none": "none", "pap": "pap", "chap": "chap", "papchap": "pap_chap", "pap_chap": "pap_chap",
                "both": "pap_chap"}.get(auth.replace(" ", ""), "none")
        snap = app.state.last_snapshot("apn_active")
        return {"apn": d.get("APN", ""), "name": d.get("Name", ""), "auth": auth, "username": d.get("UserName", ""),
                "password_set": bool(d.get("Password")), "previous": snap}

    @r.route("GET", "/api/apn/active")
    def get_active(req):
        """Internet APN the modem uses now (active 3GPP profile)."""
        return active()

    @r.route("GET", "/api/apn/active/secret")
    def get_active_secret(req):
        """Password of the active APN (audited)."""
        d = rest_get(app, "APNProfiles/3GPP/active") or {}
        app.audit(req, "apn.active.secret")
        return {"password": d.get("Password", "")}

    def put_active_values(req, data, audit="apn.active.update"):
        cur = rest_get(app, "APNProfiles/3GPP/active")
        if not isinstance(cur, dict):
            raise unavailable("The modem does not report an active APN profile")
        ch: Dict[str, Any] = {}
        if "apn" in data:
            ch["APN"] = text_field("apn", data["apn"], 1, 62, APN_RE, "Letters, digits, dots and hyphens only")
        if "auth" in data:
            ch["PDPAuthenticationType"] = REST_AUTH[validate.choice("auth", data["auth"], list(REST_AUTH))]
        if "username" in data:
            ch["UserName"] = text_field("username", data["username"], 0, 32)
        if "password" in data:
            ch["Password"] = text_field("password", data["password"], 0, 32)
        auth = ch.get("PDPAuthenticationType", cur.get("PDPAuthenticationType", "None"))
        if str(auth).lower() != "none" and not ch.get("UserName", cur.get("UserName")):
            raise field_error("username", "A user name is needed for PAP/CHAP")
        if not ch:
            return active()
        app.state.snapshot("apn_active", {"apn": cur.get("APN", ""), "auth": active().get("auth"),
                                          "username": cur.get("UserName", "")})
        app.rest.merge_put("APNProfiles/3GPP/active", ch)
        app.audit(req, audit, {"keys": sorted(k for k in data if k != "password") + (["password"] if "password" in data else [])})
        refresh()
        return active()

    @r.route("PUT", "/api/apn/active")
    def put_active(req):
        """Change the internet APN (tier 2: the data connection restarts)."""
        app.require_tier(req, 2)
        data = validate.only_keys(req.json, ["apn", "auth", "username", "password"])
        return put_active_values(req, data)

    @r.route("POST", "/api/apn/active/restore")
    def restore_active(req):
        """Go back to the APN that was set before the last change (tier 2; the password is kept)."""
        app.require_tier(req, 2)
        validate.only_keys(req.json, [])
        prev = app.state.last_snapshot("apn_active")
        if not prev:
            raise ApiError(409, "conflict", "No previous APN recorded")
        data = {"apn": prev.get("apn"), "username": prev.get("username", "")}
        if prev.get("auth") in REST_AUTH:
            data["auth"] = prev["auth"]
        return put_active_values(req, data, "apn.active.restore")

    # ----------------------------------------------------------------------- multi-APN list
    def raw_list() -> List[Dict[str, str]]:
        res = cli_get(app, "modem2_cli", "get_multi_apn_list", ["0"], timeout=15)
        if res is None or res.rc is None:
            raise unavailable("APN profiles are not available (the modem service did not answer)")
        if res.rc == 5:
            raise unavailable("APN profiles are not available: the modem service is not running")
        if res.rc != 0:
            raise vendor_error("Reading APN profiles failed: %s" % (res.message or res.rc))
        return parse_profile_list(res.text)

    def profiles() -> Dict[str, Any]:
        ident = identity_raw(app)
        rows = raw_list()
        return {"profiles": [norm_multi(p, ident.get("imsi", ""), ident.get("iccid", "")) for p in rows],
                "max": MAX_PROFILES, "sim_present": bool(sim_status(app).get("present"))}

    @r.route("GET", "/api/apn/profiles")
    def get_profiles(req):
        """Saved APN profiles (multi-APN list; passwords hidden)."""
        return profiles()

    def find(pid: int) -> Dict[str, str]:
        for p in raw_list():
            if to_int(p.get("id")) == pid:
                return p
        raise not_found("No APN profile with id %d" % pid)

    @r.route("GET", "/api/apn/profiles/{pid}/secret")
    def get_profile_secret(req):
        """Password of one saved APN profile (audited)."""
        p = find(validate.integer("pid", req.params["pid"], 0, 100000))
        app.audit(req, "apn.profile.secret", {"id": p.get("id")})
        return {"password": p.get("password", "")}

    def profile_values(data: Dict[str, Any], base: Dict[str, str]) -> Dict[str, str]:
        out = dict(base)
        if "name" in data:
            out["name"] = text_field("name", data["name"], 1, 31).strip()
            if not out["name"]:
                raise field_error("name", "Enter a name")
        if "apn" in data:
            out["apn"] = text_field("apn", data["apn"], 1, 62, APN_RE, "Letters, digits, dots and hyphens only")
        if "auth" in data:
            out["auth"] = str(AUTH_NUM[validate.choice("auth", data["auth"], list(AUTH_NUM))])
        if "username" in data:
            out["username"] = text_field("username", data["username"], 0, 32)
        if "password" in data:
            out["password"] = text_field("password", data["password"], 0, 32)
        if "pdp" in data:
            out["pdp"] = str(PDP_NUM[validate.choice("pdp", data["pdp"], list(PDP_NUM))])
        if not out.get("name"):
            raise field_error("name", "Enter a name")
        if not out.get("apn"):
            raise field_error("apn", "Enter the APN")
        if out.get("auth", "0") != "0" and not out.get("username"):
            raise field_error("username", "A user name is needed for PAP/CHAP")
        return out

    def answers_for(p: Dict[str, str]) -> List[str]:
        return [p.get("name", ""), p.get("imsi", ""), p.get("iccid", ""), str(to_int(p.get("active"), 0)),
                str(to_int(p.get("default"), 0)), p.get("apn", ""), p.get("username", ""), p.get("password", ""),
                str(to_int(p.get("auth"), 0)), str(to_int(p.get("pdp"), 3))]

    def dup_name(name: str, skip_id: Optional[int] = None):
        for p in raw_list():
            if p.get("name", "").strip().lower() == name.strip().lower() and to_int(p.get("id")) != skip_id:
                raise field_error("name", "A profile with this name already exists")

    @r.route("POST", "/api/apn/profiles")
    def add_profile(req):
        """Add an APN profile for the inserted SIM (tier 2 when it is made active)."""
        data = validate.only_keys(req.json, ["name", "apn", "auth", "username", "password", "pdp", "active"])
        make_active = validate.boolean("active", data.get("active", False))
        if make_active:
            app.require_tier(req, 2)
        vals = profile_values(data, {"auth": "0", "pdp": "3", "username": "", "password": ""})
        ident = identity_raw(app)
        if not ident.get("imsi"):
            raise unavailable("Insert a SIM first: APN profiles are stored per SIM")
        rows = raw_list()
        if len(rows) >= MAX_PROFILES:
            raise ApiError(409, "conflict", "At most %d APN profiles can be saved" % MAX_PROFILES)
        dup_name(vals["name"])
        vals.update({"imsi": ident["imsi"], "iccid": ident.get("iccid", ""), "active": "1" if make_active else "0",
                     "default": "0"})
        cli_set(app, "modem2_cli", "multi_apn_add_profile", answers_for(vals), "Adding the APN profile", timeout=20,
                need_rc=True)
        app.audit(req, "apn.profile.add", {"active": make_active})
        refresh()
        return profiles()

    @r.route("PUT", "/api/apn/profiles/{pid}")
    def update_profile(req):
        """Edit an APN profile (tier 2 when it is the active one)."""
        pid = validate.integer("pid", req.params["pid"], 0, 100000)
        data = validate.only_keys(req.json, ["name", "apn", "auth", "username", "password", "pdp"])
        cur = find(pid)
        if to_int(cur.get("active"), 0):
            app.require_tier(req, 2)
        vals = profile_values(data, cur)
        if "name" in data:
            dup_name(vals["name"], pid)
        cli_set(app, "modem2_cli", "multi_apn_update_profile", [str(pid)] + answers_for(vals),
                "Saving the APN profile", timeout=20, need_rc=True)
        app.audit(req, "apn.profile.update", {"id": pid, "keys": sorted(data)})
        refresh()
        return profiles()

    @r.route("POST", "/api/apn/profiles/{pid}/activate")
    def activate_profile(req):
        """Use this APN profile for the internet connection (tier 2: the data connection restarts)."""
        app.require_tier(req, 2)
        validate.only_keys(req.json, [])
        pid = validate.integer("pid", req.params["pid"], 0, 100000)
        cur = find(pid)
        if to_int(cur.get("active"), 0):
            raise ApiError(409, "conflict", "This profile is already active")
        try:
            app.state.snapshot("apn_active", {k: v for k, v in active().items() if k in ("apn", "auth", "username")})
        except ApiError:
            pass
        vals = dict(cur, active="1")
        cli_set(app, "modem2_cli", "multi_apn_update_profile", [str(pid)] + answers_for(vals),
                "Activating the APN profile", timeout=20, need_rc=True)
        app.audit(req, "apn.profile.activate", {"id": pid})
        refresh()
        return profiles()

    @r.route("DELETE", "/api/apn/profiles/{pid}")
    def delete_profile(req):
        """Delete a saved APN profile (the carrier default cannot be deleted; tier 2 for the active one)."""
        pid = validate.integer("pid", req.params["pid"], 0, 100000)
        cur = find(pid)
        if to_int(cur.get("default"), 0):
            raise ApiError(409, "conflict", "The carrier default profile cannot be deleted; restore it instead")
        if to_int(cur.get("active"), 0):
            app.require_tier(req, 2)
        cli_set(app, "modem2_cli", "multi_apn_delete_profile", [str(pid)], "Deleting the APN profile", timeout=20,
                need_rc=True)
        app.audit(req, "apn.profile.delete", {"id": pid})
        refresh()
        return profiles()

    @r.route("POST", "/api/apn/profiles/{pid}/restore")
    def restore_profile(req):
        """Restore the carrier default values of the default profile."""
        validate.only_keys(req.json, [])
        pid = validate.integer("pid", req.params["pid"], 0, 100000)
        cur = find(pid)
        if not to_int(cur.get("default"), 0):
            raise ApiError(409, "conflict", "Only the carrier default profile can be restored")
        if to_int(cur.get("active"), 0):
            app.require_tier(req, 2)
        cli_set(app, "modem2_cli", "multi_apn_restore_default_profile", [str(pid)], "Restoring the default profile",
                timeout=20, need_rc=True)
        app.audit(req, "apn.profile.restore", {"id": pid})
        refresh()
        return profiles()

    @r.route("GET", "/api/apn/default")
    def get_default(req):
        """Carrier default APN profile for the inserted SIM (from the vendor auto-APN database)."""
        imsi = identity_raw(app).get("imsi", "")
        if not imsi:
            return {"available": False, "reason": "No SIM card"}
        res = cli_get(app, "modem2_cli", "multi_apn_get_default_profile", [imsi], timeout=15)
        if res is None or res.rc != 0:
            return {"available": False, "reason": (res.message if res is not None else "") or "Not found"}
        p = parse_default_profile(res.text)
        return dict(norm_multi(p, imsi), available=True)

    # --------------------------------------------------------------------------- presets
    @r.route("GET", "/api/apn/presets", auth=True)
    def get_presets(req):
        """APN presets (stock + mod-ROM APN databases); filter with q, mcc, mnc, mccmnc or sim=1."""
        q = (req.query.get("q") or "").strip().lower()[:64]
        mcc = (req.query.get("mcc") or "").strip()
        mnc = (req.query.get("mnc") or "").strip()
        mccmnc = (req.query.get("mccmnc") or "").strip()
        for name, v, n in (("mcc", mcc, 3), ("mnc", mnc, 3), ("mccmnc", mccmnc, 6)):
            if v and (not v.isdigit() or len(v) > n):
                raise field_error(name, "Digits only")
        limit = validate.integer("limit", req.query.get("limit", 200), 1, 500)
        gid1 = ""
        if (req.query.get("sim") or "") in ("1", "true"):
            st = sim_status(app)
            mcc, mnc = st["home_network"]["mcc"], st["home_network"]["mnc"]
            gid1 = str(st["gid1_bytes"][0]) if st.get("gid1_bytes") else ""
            if not mcc:
                return {"total": presets().get("count", 0), "count": 0, "presets": [], "home": st["home_network"]}
        out = []
        for p in presets().get("presets", []):
            if mccmnc and p["mccmnc"] != mccmnc:
                continue
            if mcc and p["mcc"] != mcc:
                continue
            if mnc and p["mnc"].lstrip("0") != mnc.lstrip("0"):
                continue
            if q and q not in ("%s %s %s %s %s" % (p["operator"], p["apn"], p["mccmnc"], p["gid1_name"], p["name"])).lower():
                continue
            item = dict(p)
            item["gid1_match"] = bool(gid1) and p.get("gid1") == gid1
            out.append(item)
        if gid1:
            out.sort(key=lambda p: (not p["gid1_match"], p.get("gid1") != ""))
        return {"total": presets().get("count", 0), "count": len(out), "presets": out[:limit],
                "sources": presets().get("generated_from", {})}

    # ------------------------------------------------------------------- vendor suggestion
    @r.route("GET", "/api/apn/lookup")
    def lookup(req):
        """APN the vendor database suggests for the inserted SIM (by MCC/MNC and GID1/GID2)."""
        st = sim_status(app)
        home = st.get("home_network") or {}
        out: Dict[str, Any] = {"available": False, "home": home}
        if not home.get("mcc"):
            out["reason"] = "No SIM card"
            return out
        g1 = ",".join(str(b) for b in st.get("gid1_bytes") or [])
        g2 = ",".join(str(b) for b in st.get("gid2_bytes") or [])
        res = cli_get(app, "modem2_cli", "get_apn_from_database", [home["mcc"] + home["mnc"], g1, g2], timeout=15)
        if res is not None and res.ok:
            out.update({"available": True, "apn": res.get("apn") or "", "username": res.get("username") or "",
                        "password_set": bool(res.get("password")), "auth": AUTH_NAME.get(res.get_int("auth", 0), "none")})
        else:
            out["reason"] = (res.message if res is not None else "") or "Not in the vendor database"
        iccid = identity_raw(app).get("iccid", "")
        if iccid:
            c = cli_get(app, "modem2_cli", "get_custom_apn_from_db_using_iccid", [iccid], timeout=15)
            if c is not None and c.ok:
                out["custom"] = {"apn": c.get("apn") or "", "username": c.get("username") or "",
                                 "password_set": bool(c.get("password")), "auth": AUTH_NAME.get(c.get_int("auth", 0), "none"),
                                 "pdp": PDP_NAME.get(c.get_int("pdp_type", 3), "ipv4v6"), "profile": c.get_int("profile Id")}
        return out

    # ---------------------------------------------------------------------------- failback
    def failback() -> Dict[str, Any]:
        en = cli_get(app, "modem2_cli", "get_apn_failback_enabled")
        al = cli_get(app, "modem2_cli", "get_failback_apn_list", ["0"], timeout=15)
        sl = cli_get(app, "modem2_cli", "get_failback_sim_list", ["0"], timeout=15)
        apns = []
        for p in (parse_profile_list(al.text) if al is not None and al.rc == 0 else []):
            apns.append({"plmn": p.get("plmn", ""), "carrier_id": to_int(p.get("carrier_id")), "apn": p.get("apn", ""),
                         "pdp": PDP_NAME.get(to_int(p.get("pdp"), 3), "ipv4v6"), "max_iterations": to_int(p.get("max_iterations")),
                         "backoff_s": to_int(p.get("backoff_time")), "network_mode": to_int(p.get("network_mode"))})
        sims = []
        for p in (parse_profile_list(sl.text) if sl is not None and sl.rc == 0 else []):
            sims.append({"imsi_masked": mask(p.get("imsi", "")), "iteration": to_int(p.get("iteration")),
                         "index": to_int(p.get("index")), "timestamp": to_int(p.get("timestamp")),
                         "enabled": bool(to_int(p.get("enabled"), 0))})
        return {"available": en is not None and en.ok, "enabled": bool(en.get_int("enabled", 0)) if en is not None and en.ok else False,
                "apns": apns, "sims": sims}

    @r.route("GET", "/api/apn/failback")
    def get_failback(req):
        """APN failback: on/off, the vendor's failback APN list and per-SIM progress."""
        return failback()

    @r.route("PUT", "/api/apn/failback")
    def put_failback(req):
        """Turn APN failback on or off."""
        data = validate.only_keys(req.json, ["enabled"])
        en = validate.boolean("enabled", data.get("enabled"))
        cli_set(app, "modem2_cli", "set_apn_failback_enabled", ["1" if en else "0"], "Setting APN failback")
        app.audit(req, "apn.failback", {"enabled": en})
        return failback()

    @r.route("POST", "/api/apn/failback/switch")
    def switch_failback(req):
        """Switch to the next failback APN now (tier 2: the data connection restarts)."""
        app.require_tier(req, 2)
        validate.only_keys(req.json, [])
        cli_set(app, "modem2_cli", "failback_switch_apn", [], "Switching APN", timeout=30)
        app.audit(req, "apn.failback.switch")
        refresh()
        return failback()

    # ------------------------------------------------------------------ modem tech profiles
    def read_profile(ptype: int, idx: int) -> Optional[Dict[str, Any]]:
        res = cli_get(app, "modem2_cli", "prof_get_tech", [str(ptype), str(idx)])
        if res is None or not res.ok:
            return None
        p = parse_tech_profile(res)
        p.update({"type": ptype, "index": idx, "dns_auto": dns_auto(p)})
        return p

    def raw_password(ptype: int, idx: int) -> str:
        res = cli_get(app, "modem2_cli", "prof_get_tech", [str(ptype), str(idx)])
        return (res.get("Password") or "") if res is not None and res.ok else ""

    def active_index(ptype: int) -> Optional[int]:
        res = cli_get(app, "modem2_cli", "prof_get_act_tech", [str(ptype)])
        return res.get_int("idx") if res is not None and res.ok else None

    @r.route("GET", "/api/apn/modem-profiles")
    def get_modem_profiles(req):
        """Modem data profiles (3GPP type 0 or 3GPP2 type 1), indexes 1..max (default 6), and the active index."""
        ptype = validate.integer("type", req.query.get("type", 0), 0, 1)
        mx = validate.integer("max", req.query.get("max", 6), 1, 16)
        rows = [p for p in (read_profile(ptype, i) for i in range(1, mx + 1)) if p is not None]
        act = active_index(ptype)
        for p in rows:
            p["active"] = p["index"] == act
        return {"type": ptype, "active_index": act, "profiles": rows, "previous_active": app.state.last_snapshot("apn_act_tech")}

    @r.route("GET", "/api/apn/modem-profiles/{ptype}/{idx}/secret")
    def get_modem_profile_secret(req):
        """Password stored in one modem profile (audited)."""
        ptype = validate.integer("type", req.params["ptype"], 0, 1)
        idx = validate.integer("index", req.params["idx"], 1, 254)
        app.audit(req, "apn.modem_profile.secret", {"type": ptype, "index": idx})
        return {"password": raw_password(ptype, idx)}

    def tech_answers(data: Dict[str, Any], cur: Dict[str, Any], password: str) -> List[str]:
        """Answers shared by prof_set_tech / prof_set_cust_tech (after type/index or tech)."""
        apn = text_field("apn", data.get("apn", cur["apn"]), 0, 62, APN_RE, "Letters, digits, dots and hyphens only")
        user = text_field("username", data.get("username", cur["username"]), 0, 63)
        pw = text_field("password", data["password"], 0, 63) if "password" in data else password
        name = text_field("name", data.get("name", cur["name"]), 0, 63)
        auth = validate.choice("auth", data.get("auth", cur["auth"]), ["none", "pap", "chap"])
        pdp = validate.choice("pdp", data.get("pdp", cur["pdp"]), ["ipv4", "ipv6", "ipv4v6"])
        disabled = validate.boolean("apn_disabled", data.get("apn_disabled", cur["apn_disabled"]))
        timeout = validate.integer("inactivity_timeout", data.get("inactivity_timeout", cur["inactivity_timeout"] or 0), 0, 86400)
        auto = validate.boolean("dns_auto", data.get("dns_auto", cur["dns_auto"]))
        if auto:
            d1 = d2 = "0.0.0.0"
        else:
            d1 = validate.ipv4("dns1", data.get("dns1", cur["dns1"]))
            d2 = validate.ipv4("dns2", data.get("dns2", cur["dns2"]) or "0.0.0.0")
            if d1 == "0.0.0.0":
                raise field_error("dns1", "Enter a DNS server address")
        v4 = cur.get("ipv4_pref") or ""
        v6 = cur.get("ipv6_pref") or ""
        if len(v4) > 15 or len(v6) > 45 or not re.match(r"^[0-9a-fA-F:.]*$", v4 + v6):
            raise vendor_error("Unexpected address preference in the current profile")
        locked = str(cur.get("locked") or 0)
        data_mode = str(cur.get("data_mode") or 0)
        p3 = str({v: k for k, v in PDP3GPP2_NAME.items()}.get(cur.get("pdp_3gpp2"), 0))
        return [apn, user, pw, name, locked, str(AUTH_NUM[auth]), "1", d1, d2, str(PDP_NUM[pdp]),
                "1" if disabled else "0", str(timeout), v4, v6, data_mode, p3]

    PROFILE_KEYS = ["apn", "username", "password", "name", "auth", "pdp", "apn_disabled", "inactivity_timeout",
                    "dns_auto", "dns1", "dns2"]

    @r.route("PUT", "/api/apn/modem-profiles/{ptype}/{idx}")
    def put_modem_profile(req):
        """Edit a modem data profile (tier 2; wrong values on the attach/IMS profile can stop registration)."""
        app.require_tier(req, 2)
        ptype = validate.integer("type", req.params["ptype"], 0, 1)
        idx = validate.integer("index", req.params["idx"], 1, 254)
        data = validate.only_keys(req.json, PROFILE_KEYS)
        cur = read_profile(ptype, idx)
        if cur is None:
            raise not_found("The modem has no profile %d" % idx)
        answers = [str(ptype), str(idx)] + tech_answers(data, cur, raw_password(ptype, idx))
        app.state.snapshot("apn_modem_profile_%d_%d" % (ptype, idx),
                           {k: cur[k] for k in ("apn", "username", "name", "auth", "pdp", "apn_disabled", "dns_auto")})
        cli_set(app, "modem2_cli", "prof_set_tech", answers, "Saving the modem profile", timeout=20)
        app.audit(req, "apn.modem_profile.update", {"type": ptype, "index": idx, "keys": sorted(data)})
        refresh()
        return read_profile(ptype, idx)

    @r.route("PUT", "/api/apn/modem-profiles/active")
    def put_active_index(req):
        """Choose which modem profile the internet connection uses (tier 2)."""
        app.require_tier(req, 2)
        data = validate.only_keys(req.json, ["type", "index"])
        ptype = validate.integer("type", data.get("type", 0), 0, 1)
        idx = validate.integer("index", data.get("index"), 1, 254)
        if read_profile(ptype, idx) is None:
            raise field_error("index", "The modem has no profile %d" % idx)
        app.state.snapshot("apn_act_tech", {"type": ptype, "index": active_index(ptype)})
        cli_set(app, "modem2_cli", "prof_set_act_tech", [str(ptype), str(idx)], "Changing the active profile")
        app.audit(req, "apn.modem_profile.active", {"type": ptype, "index": idx})
        refresh()
        return {"type": ptype, "active_index": active_index(ptype)}

    # ------------------------------------------------------------ PRI / custom tech profiles
    def tech_profile(kind: str, tech: str) -> Optional[Dict[str, Any]]:
        res = cli_get(app, "modem2_cli", "prof_get_%s_tech" % kind, [str(TECHS[tech])])
        if res is None or not res.ok:
            return None
        p = parse_tech_profile(res)
        p.update({"tech": tech, "dns_auto": dns_auto(p)})
        return p

    @r.route("GET", "/api/apn/tech-profiles")
    def get_tech_profiles(req):
        """Carrier-provisioned (PRI, read only) and custom per-technology profiles (LTE / UMTS / eHRPD)."""
        return {"pri": {t: tech_profile("pri", t) for t in TECHS}, "custom": {t: tech_profile("cust", t) for t in TECHS}}

    @r.route("PUT", "/api/apn/tech-profiles/custom/{tech}")
    def put_custom_tech(req):
        """Edit the custom profile of one technology (tier 2; used for the attach/IMS PDN)."""
        app.require_tier(req, 2)
        tech = validate.choice("tech", req.params["tech"], list(TECHS))
        data = validate.only_keys(req.json, PROFILE_KEYS)
        cur = tech_profile("cust", tech)
        if cur is None:
            raise unavailable("The modem does not report a custom %s profile" % tech.upper())
        res = cli_get(app, "modem2_cli", "prof_get_cust_tech", [str(TECHS[tech])])
        pw = (res.get("Password") or "") if res is not None and res.ok else ""
        answers = [str(TECHS[tech])] + tech_answers(data, cur, pw)
        cli_set(app, "modem2_cli", "prof_set_cust_tech", answers, "Saving the custom profile", timeout=20)
        app.audit(req, "apn.custom_profile.update", {"tech": tech, "keys": sorted(data)})
        refresh()
        return tech_profile("cust", tech)

    # ----------------------------------------------------------------------- PDN parameters
    def pdn(idx: int) -> Dict[str, Any]:
        o = cli_get(app, "modem2_cli", "pdn_get_override_param", [str(idx), "0"])
        e = cli_get(app, "modem2_cli", "pdn_get_ext_params", [str(idx), "0"])
        out: Dict[str, Any] = {"profile": idx, "override_available": o is not None and o.ok,
                               "ext_available": e is not None and e.ok}
        if o is not None and o.ok:
            out.update({"pdp_override": OVERRIDE_NAME.get(o.get_int("PDP override", 0), "ipv4"),
                        "clat": bool(o.get_int("CLAT enabled", 0))})
        if e is not None and e.ok:
            out.update({"max_connections": e.get_int("Extra PDN max connections"),
                        "wait_timer": e.get_int("Extra PDN wait timer"),
                        "connection_timer": e.get_int("Extra PDN connection timer")})
        return out

    @r.route("GET", "/api/apn/pdn")
    def get_pdn(req):
        """PDN override (PDP type, CLAT) and extra context parameters of 3GPP profiles 1..max (default 4)."""
        mx = validate.integer("max", req.query.get("max", 4), 1, 16)
        return {"profiles": [pdn(i) for i in range(1, mx + 1)]}

    @r.route("PUT", "/api/apn/pdn/{idx}")
    def put_pdn(req):
        """Change PDN override / extra parameters of one profile (tier 2)."""
        app.require_tier(req, 2)
        idx = validate.integer("profile", req.params["idx"], 1, 254)
        data = validate.only_keys(req.json, ["pdp_override", "clat", "max_connections", "wait_timer", "connection_timer"])
        cur = pdn(idx)
        if "pdp_override" in data or "clat" in data:
            if not cur["override_available"]:
                raise unavailable("The modem has no PDN override record for profile %d" % idx)
            pdp = validate.choice("pdp_override", data.get("pdp_override", cur["pdp_override"]), ["ipv4", "ipv6", "ipv4v6"])
            clat = validate.boolean("clat", data.get("clat", cur["clat"]))
            cli_set(app, "modem2_cli", "pdn_set_override_param",
                    [str(idx), "0", str({"ipv4": 0, "ipv6": 2, "ipv4v6": 3}[pdp]), "1" if clat else "0"],
                    "Setting the PDN override")
        if any(k in data for k in ("max_connections", "wait_timer", "connection_timer")):
            if not cur["ext_available"]:
                raise unavailable("The modem has no extra PDN parameters for profile %d" % idx)
            mc = validate.integer("max_connections", data.get("max_connections", cur.get("max_connections") or 0), 0, 1023)
            wt = validate.integer("wait_timer", data.get("wait_timer", cur.get("wait_timer") or 0), 0, 65535)
            ct = validate.integer("connection_timer", data.get("connection_timer", cur.get("connection_timer") or 0), 0, 65535)
            cli_set(app, "modem2_cli", "pdn_set_ext_params", [str(idx), "0", str(mc), str(wt), str(ct)],
                    "Setting extra PDN parameters")
        app.audit(req, "apn.pdn.update", {"profile": idx, "keys": sorted(data)})
        return pdn(idx)
