"""Cellular > Network mode: technologies, 5G SA/NSA, radios, airplane mode, mobile data, auto-connect,
roaming, roam guard, 5G icon timer.

GET  /api/cellular/mode           current values (flat keys = form fields) + info
PUT  /api/cellular/mode           partial update; radio-affecting keys need tier 2
POST /api/cellular/mode/restore   re-apply the previous technology / 5G mode snapshot (tier 2)

Write paths:
  tech_preset / tech_modes  modem2 enabled_tech_set (core spec, sandbox-verified)
  nr5g_mode                 modem2 set_nr5g_disable_mode (core spec)
  nr5g_radio / radio        modem2 5g_radio_set_enabled / radio_set_enabled (argv)
  airplane                  ccm2 set_air_mode_enabled (argv)
  data_enabled, auto_connect, roaming_domestic, roaming_international
                            REST CellularServiceSettings (merge PUT); CLI fallback when REST is unavailable
  auto_connect_mode         ccm2 set_wwan_ac (argv)
  roam_guard                modem2 set_roam_guard_settings (argv, all 8 values)
  tuwb                      modem2 set_5g_tuwb (only offered when the device reports a value)
"""
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import field_error, unavailable
from . import cellular_parse as P
from .cellular import CCM, M2, flag, get, int_of, invalidate, last_snapshot, run_set, snapshot

RADIO_LABEL = "Radio enabled: (disabled=0, enabled=1)"
TIER2_KEYS = ("tech_preset", "tech_modes", "nr5g_mode", "nr5g_radio", "radio", "airplane")
REST_KEYS = {"data_enabled": "CellularDataEnabled", "auto_connect": "AutoConnectEnabled",
             "roaming_domestic": "DomesticRoamingEnabled", "roaming_international": "InternationalRoamingEnabled"}
RG_KEYS = ["domestic_enabled", "domestic_forced", "domestic_guard_enabled", "domestic_guard_forced",
           "international_enabled", "international_forced", "international_guard_enabled", "international_guard_forced"]
RG_LABELS = ["Domestic_roam_enabled", "Domestic_roam_forced", "Domestic_roam_guard_enabled", "Domestic_roam_guard_forced",
             "International_roam_enabled", "International_roam_forced", "International_roam_guard_enabled",
             "International_roam_guard_forced"]


def register(app):
    r = app.router

    def nr_supported() -> bool:
        lst = P.band_list(get(app, M2, "get_supported_nr5g_band_list"))
        return bool(lst)

    def tech_state() -> Dict[str, Any]:
        en = get(app, M2, "enabled_tech_get")
        sup = get(app, M2, "get_sup_tech")
        act = get(app, M2, "active_tech_get")
        mask = int_of(en, "Enabled tech modes")
        smask = int_of(sup, "Available tech modes")
        amask = int_of(act, "Active tech modes")
        nr_ok = nr_supported()
        allowed = ((smask or 0) & P.ALL_TECH_MASK) | (mask or 0)
        if nr_ok:
            allowed |= 0x20
        if smask is None:
            allowed |= 0x32
        return {"mask": mask, "supported_mask": smask, "active_mask": amask, "allowed_mask": allowed,
                "nr_supported": nr_ok}

    def roam_guard() -> Optional[Dict[str, bool]]:
        res = get(app, M2, "get_roam_guard_settings")
        if res is None:
            return None
        out = {}
        for k, lab in zip(RG_KEYS, RG_LABELS):
            v = P.to_int(res.get(lab))
            if v is None:
                return None
            out[k] = bool(v)
        return out

    def read() -> Dict[str, Any]:
        t = tech_state()
        mask = t["mask"]
        nrm = int_of(get(app, M2, "get_nr5g_disable_mode"), "disable mode")
        cset = app.rest_try("CellularServiceSettings")
        air_dis = P.parse_air_disable(get(app, CCM, "get_airplane_mode_disable_status"))
        wac = int_of(get(app, CCM, "get_wwan_ac"), "Autoconnect Type")
        icon = int_of(get(app, M2, "get_5g_icon_mode"), "mode")
        tuwb = int_of(get(app, M2, "get_5g_tuwb"), "tuwb")
        notes = []
        if cset is None:
            cdata = flag(get(app, CCM, "get_cell_data_enabled"), "enabled")
            dom = flag(get(app, M2, "roam_get_enabled"), "Roaming enabled")
            intl = flag(get(app, M2, "roam_get_intl_enabled"), "Roaming International")
            auto = None if wac is None else wac != 0
            notes.append("Vendor settings service unavailable: values read from the modem tools instead.")
        else:
            cdata = bool(cset.get("CellularDataEnabled"))
            auto = bool(cset.get("AutoConnectEnabled"))
            dom = bool(cset.get("DomesticRoamingEnabled"))
            intl = bool(cset.get("InternationalRoamingEnabled"))
        if mask is None:
            notes.append("The modem did not report its enabled technologies.")
        prev = last_snapshot(app, "mode")
        return {
            "tech_preset": P.preset_for(mask) if mask is not None else None,
            "tech_modes": P.mask_names(mask),
            "nr5g_mode": P.NR5G_DISABLE.get(nrm) if nrm is not None else None,
            "nr5g_radio": flag(get(app, M2, "5g_radio_is_enabled"), RADIO_LABEL),
            "radio": flag(get(app, M2, "radio_is_enabled"), RADIO_LABEL),
            "airplane": flag(get(app, CCM, "get_air_mode_enabled"), "enabled"),
            "data_enabled": cdata, "auto_connect": auto,
            "auto_connect_mode": P.AUTOCONNECT.get(wac) if wac is not None else None,
            "roaming_domestic": dom, "roaming_international": intl,
            "roam_guard": roam_guard(),
            "tuwb": tuwb,
            "info": {
                "tech_mask": mask, "active_modes": P.mask_names(t["active_mask"]), "active_mask": t["active_mask"],
                "supported_modes": P.mask_names(t["allowed_mask"]), "supported_mask": t["supported_mask"],
                "allowed_mask": t["allowed_mask"], "nr_supported": t["nr_supported"],
                "nr5g_disable_code": nrm, "airplane_disables": air_dis,
                "icon_mode": None if icon is None else {"code": icon, "label": P.ICON_MODES.get(icon, str(icon))},
                "auto_connect_code": wac, "settings_source": "rest" if cset is not None else "cli",
                "previous": prev,
            },
            "presets": P.TECH_PRESETS,
            "labels": {"tech": P.TECH_LABEL, "nr5g_mode": P.NR5G_MODE_LABEL, "auto_connect_mode": P.AUTOCONNECT_LABEL},
            "notes": notes,
        }

    def bool_field(data, key):
        return validate.boolean(key, data[key])

    def resolve_mask(data, t) -> Optional[int]:
        if "tech_preset" in data and data["tech_preset"] != "custom":
            pid = validate.choice("tech_preset", data["tech_preset"], [p["id"] for p in P.TECH_PRESETS] + ["custom"])
            return [p["mask"] for p in P.TECH_PRESETS if p["id"] == pid][0]
        if "tech_modes" in data:
            v = data["tech_modes"]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise field_error("tech_modes", "Choose at least one technology")
            bad = [x for x in v if x not in [n for n, _b in P.TECH_BITS]]
            if bad:
                raise field_error("tech_modes", "Unknown technology: %s" % bad[0])
            return P.names_mask(v)
        if "tech_preset" in data:  # "custom" without a list: keep the current mask
            return t["mask"]
        return None

    def plan(data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate everything first (so a bad request never asks for confirmation) and read current state."""
        t = tech_state()
        cur_mask = t["mask"]
        new_mask = resolve_mask(data, t)
        if new_mask is not None:
            if new_mask == 0:
                raise field_error("tech_modes", "Select at least one technology")
            extra = new_mask & ~t["allowed_mask"]
            if extra:
                raise field_error("tech_modes", "Not supported by this device: %s" % ", ".join(P.mask_names(extra)))
        nr_code = None
        if "nr5g_mode" in data:
            m = validate.choice("nr5g_mode", data["nr5g_mode"], list(P.NR5G_DISABLE_REV))
            nr_code = P.NR5G_DISABLE_REV[m]
            eff = new_mask if new_mask is not None else cur_mask
            if eff == 0x20 and m == "nsa":
                raise field_error("nr5g_mode", "5G NSA needs 4G LTE. Choose SA or enable LTE.")
        bools = {k: bool_field(data, k) for k in ("nr5g_radio", "radio", "airplane") + tuple(REST_KEYS) if k in data}
        wac = None
        if "auto_connect_mode" in data:
            wac = P.AUTOCONNECT_REV[validate.choice("auto_connect_mode", data["auto_connect_mode"], list(P.AUTOCONNECT_REV))]
        rg = None
        if "roam_guard" in data:
            cur = roam_guard()
            if cur is None:
                raise unavailable("Roam guard settings are not available on this device")
            v = data["roam_guard"]
            if not isinstance(v, dict):
                raise field_error("roam_guard", "Expected an object")
            for k in v:
                if k not in RG_KEYS:
                    raise field_error("roam_guard", "Unknown roam guard setting: %s" % k)
            rg = dict(cur)
            rg.update({k: validate.boolean("roam_guard", v[k]) for k in v})
        tuwb = None
        if "tuwb" in data:
            tuwb = validate.integer("tuwb", data["tuwb"], 0, 30)
        nr_now = None
        if nr_code is not None or new_mask is not None:
            nr_now = int_of(get(app, M2, "get_nr5g_disable_mode"), "disable mode")
        return {"cur_mask": cur_mask, "new_mask": new_mask, "nr_code": nr_code, "nr_now": nr_now, "bools": bools,
                "wac": wac, "rg": rg, "tuwb": tuwb}

    def execute(req, p: Dict[str, Any]) -> Dict[str, Any]:
        changes: List[str] = []
        cur_mask, new_mask, nr_code, nr_now = p["cur_mask"], p["new_mask"], p["nr_code"], p["nr_now"]
        bools, wac, rg, tuwb = p["bools"], p["wac"], p["rg"], p["tuwb"]
        # snapshot radio settings before changing them (design section 8: restore previous)
        if (new_mask is not None and new_mask != cur_mask) or (nr_code is not None and nr_code != nr_now):
            snapshot(app, "mode", {"tech_mask": cur_mask, "nr5g_mode": P.NR5G_DISABLE.get(nr_now)})
        if new_mask is not None and new_mask != cur_mask:
            run_set(app, M2, "enabled_tech_set", [",".join(P.mask_names(new_mask))], "Setting network technologies",
                    timeout=20)
            changes.append("tech")
        if nr_code is not None and nr_code != nr_now:
            run_set(app, M2, "set_nr5g_disable_mode", [nr_code], "Setting the 5G mode", timeout=20)
            changes.append("nr5g_mode")
        if "nr5g_radio" in bools:
            run_set(app, M2, "5g_radio_set_enabled", [int(bools["nr5g_radio"])], "Switching the 5G radio", timeout=20)
            changes.append("nr5g_radio")
        if "radio" in bools:
            run_set(app, M2, "radio_set_enabled", [int(bools["radio"])], "Switching the cellular radio", timeout=20)
            changes.append("radio")
        if "airplane" in bools:
            run_set(app, CCM, "set_air_mode_enabled", [int(bools["airplane"])], "Switching airplane mode", timeout=20)
            changes.append("airplane")
        rest_changes = {REST_KEYS[k]: bools[k] for k in REST_KEYS if k in bools}
        if rest_changes:
            cset = app.rest_try("CellularServiceSettings")
            if cset is not None:
                app.rest.merge_put("CellularServiceSettings", rest_changes)
            else:  # vendor REST unavailable: same settings through the CLIs
                if "data_enabled" in bools:
                    run_set(app, CCM, "set_cell_data_enabled", [int(bools["data_enabled"])], "Switching mobile data")
                if "roaming_domestic" in bools:
                    run_set(app, M2, "roam_set_enabled", [int(bools["roaming_domestic"])], "Setting domestic roaming")
                if "roaming_international" in bools:
                    run_set(app, M2, "roam_set_intl_enabled", [int(bools["roaming_international"])],
                            "Setting international roaming")
                if "auto_connect" in bools and wac is None:
                    wac = 2 if bools["auto_connect"] else 0
            changes += [k for k in REST_KEYS if k in bools]
        if wac is not None:
            run_set(app, CCM, "set_wwan_ac", [wac], "Setting auto-connect")
            changes.append("auto_connect_mode")
        if rg is not None:
            run_set(app, M2, "set_roam_guard_settings", [int(rg[k]) for k in RG_KEYS], "Saving roam guard settings")
            changes.append("roam_guard")
        if tuwb is not None:
            run_set(app, M2, "set_5g_tuwb", [tuwb], "Setting the 5G icon timer")
            changes.append("tuwb")
        invalidate(app)
        app.audit(req, "cellular.mode.update", {"keys": changes})
        return read()

    @r.route("GET", "/api/cellular/mode")
    def get_mode(req):
        """Enabled technologies, 5G mode, radios, airplane mode, mobile data, auto-connect, roaming."""
        return read()

    @r.route("PUT", "/api/cellular/mode")
    def put_mode(req):
        """Change network mode settings (technology, 5G and radio switches need confirmation)."""
        data = validate.only_keys(req.json, ["tech_preset", "tech_modes", "nr5g_mode", "nr5g_radio", "radio",
                                             "airplane", "auto_connect_mode", "roam_guard", "tuwb"] + list(REST_KEYS))
        if not data:
            raise field_error("tech_preset", "Nothing to change")
        p = plan(data)
        if any(k in data for k in TIER2_KEYS):
            app.require_tier(req, 2)
        return execute(req, p)

    @r.route("POST", "/api/cellular/mode/restore")
    def restore(req):
        """Re-apply the technologies / 5G mode that were active before the last change."""
        app.require_tier(req, 2)
        prev = last_snapshot(app, "mode")
        if not prev or not isinstance(prev.get("value"), dict):
            raise unavailable("There is no previous network mode to restore")
        v = prev["value"]
        data: Dict[str, Any] = {}
        if v.get("tech_mask"):
            data["tech_preset"] = "custom"
            data["tech_modes"] = P.mask_names(v["tech_mask"])
        if v.get("nr5g_mode"):
            data["nr5g_mode"] = v["nr5g_mode"]
        if not data:
            raise unavailable("The saved network mode is empty")
        return execute(req, plan(data))
