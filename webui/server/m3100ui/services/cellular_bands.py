"""Cellular > Bands: LTE / 5G NR SA / 5G NR NSA band selection, band priority, carrier aggregation.

GET  /api/cellular/bands                 supported / enabled / active bands, priorities, restore info
PUT  /api/cellular/bands                 {"lte": [..], "nr_sa": [..], "nr_nsa": [..]}            tier 2
POST /api/cellular/bands/enable-all      {"lists": [...]} enable every supported band (mod-ROM feature) tier 2
POST /api/cellular/bands/restore         {"list": "lte|nr_sa|nr_nsa|all", "source": "previous|original"} tier 2
PUT  /api/cellular/bands/priority        {"lte": [..], "nr": [..]} ordered, may be empty (= cleared)   tier 2
GET  /api/cellular/bands/efs             raw EFS lte_bandpref masks decoded (read-only, nwcli qmi_idl read_file)
GET  /api/cellular/ca                    carrier aggregation state
PUT  /api/cellular/ca                    {"enabled": bool}                                          tier 2
GET  /api/cellular/ca/combo?bands=2,66   is a 2- or 3-band CA combination enabled
PUT  /api/cellular/ca/combo              {"bands": [b1, b2(, b3)], "enabled": bool}                 tier 2

Band setters are the core specs (set_enabled_lte_bands / _nr5g_sa_bands / _nr5g_nsa_bands, answers
[count, band...], never empty).  Every change snapshots the previous list (app.state) and the first change
ever also records the "original" set the device had before this UI touched it.
"""
import os
import threading
import time
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import field_error, unavailable
from . import cellular_parse as P
from .cellular import M2, get, get_any, invalidate, last_snapshot, run_set, snapshot

LISTS = {
    "lte": {"get": "get_enabled_lte_bands", "set": "set_enabled_lte_bands", "sup": "get_supported_band_list",
            "label": "4G LTE"},
    "nr_sa": {"get": "get_enabled_nr5g_sa_bands", "set": "set_enabled_nr5g_sa_bands",
              "sup": "get_supported_nr5g_band_list", "label": "5G SA"},
    "nr_nsa": {"get": "get_enabled_nr5g_nsa_bands", "set": "set_enabled_nr5g_nsa_bands",
               "sup": "get_supported_nr5g_band_list", "label": "5G NSA"},
}
PRIORITY = {"lte": ("get_configured_band_priority_list", "set_configured_band_priority_list", "get_supported_band_list"),
            "nr": ("get_configured_nr5g_band_priority_list", "set_configured_nr5g_band_priority_list",
                   "get_supported_nr5g_band_list")}
ORIGINAL = "cellular/bands-original.json"
EFS_FILES = [("lte_bandpref", "/nv/item_files/modem/mmode/lte_bandpref", 8, 1),
             ("lte_bandpref_extn_65_256", "/nv/item_files/modem/mmode/lte_bandpref_extn_65_256", 24, 65)]
_efs_lock = threading.Lock()


def register(app):
    r = app.router

    def supported(cmd: str) -> List[int]:
        return app.cache.get("cellular:sup:" + cmd, 300, lambda: P.band_list(get(app, M2, cmd)) or [])

    def enabled(name: str) -> Optional[List[int]]:
        return P.band_list(get(app, M2, LISTS[name]["get"]))

    def current_all() -> Dict[str, Optional[List[int]]]:
        return {n: enabled(n) for n in LISTS}

    def active_bands() -> Dict[str, List[int]]:
        sig = P.parse_signal(get(app, M2, "get_signal"))
        nr = P.parse_5g(get(app, M2, "get_5g_status"))
        ca = get(app, M2, "get_ca_band_list")
        lte = []
        for c in (P.parse_ca_bands(ca.text) if ca else []):
            if c["band"] and (c["index"] == 0 or c.get("status_code") in (2, None)) and c["band"] not in lte:
                lte.append(c["band"])          # primary + active secondary carriers
        if sig.get("band") and sig["band"] not in lte:
            lte.insert(0, sig["band"])
        nrb = [nr["band"]] if nr.get("service_available") and nr.get("band") else []
        return {"lte": lte, "nr": nrb}

    def ensure_original(cur: Dict[str, Optional[List[int]]]) -> None:
        if app.state.read_json(ORIGINAL) is None and all(cur.get(n) for n in LISTS):
            app.state.write_json(ORIGINAL, {"t": int(time.time()), "lists": cur})

    def read() -> Dict[str, Any]:
        cur = current_all()
        act = active_bands()
        orig = app.state.read_json(ORIGINAL) or {}
        out: Dict[str, Any] = {}
        for n, meta in LISTS.items():
            sup = supported(meta["sup"])
            en = cur[n]
            prev = last_snapshot(app, "bands." + n)
            extra = [b for b in sup if en is not None and b not in en]
            out[n] = {"label": meta["label"], "supported": sup, "enabled": en,
                      "active": act["lte"] if n == "lte" else act["nr"],
                      "not_in_supported": [b for b in (en or []) if b not in sup],
                      "enable_all_adds": extra,
                      "previous": prev, "original": (orig.get("lists") or {}).get(n)}
        pr = {}
        for k, (g, _s, _sup) in PRIORITY.items():
            pr[k] = P.band_list(get(app, M2, g))
        legacy = get(app, M2, "lte_band_get_prior")
        pr["lte_legacy"] = [P.to_int(v) for v in legacy.all("LTE band")] if legacy else None
        pr["previous"] = {k: last_snapshot(app, "priority." + k) for k in PRIORITY}
        out["priority"] = pr
        out["ca"] = read_ca()
        out["original_time"] = orig.get("t")
        out["band_names"] = {"lte": P.LTE_BAND_MHZ, "nr": P.NR_BAND_MHZ}
        notes = []
        if all(cur[n] is None for n in LISTS):
            notes.append("The modem did not report its band configuration.")
        out["notes"] = notes
        return out

    def check_list(name: str, value: Any) -> List[int]:
        if name not in LISTS:
            raise field_error(name, "Unknown band list")
        cur = enabled(name) or []
        allowed = sorted(set(supported(LISTS[name]["sup"])) | set(cur))
        bands = validate.int_list(name, value, allowed=allowed, min_items=1)
        if len(bands) > 512:
            raise field_error(name, "Too many bands")
        return sorted(bands)

    def write_lists(req, new: Dict[str, List[int]], action: str) -> Dict[str, Any]:
        cur = current_all()
        ensure_original(cur)
        done = []
        for n in LISTS:
            if n not in new:
                continue
            bands = new[n]
            if not bands:
                raise field_error(n, "At least one band must stay enabled")
            if cur.get(n) is not None and sorted(cur[n]) == sorted(bands):
                continue
            if cur.get(n):
                snapshot(app, "bands." + n, cur[n])
            run_set(app, M2, LISTS[n]["set"], [len(bands)] + bands, "Setting %s bands" % LISTS[n]["label"], timeout=30)
            back = enabled(n)
            if back is not None and sorted(back) != sorted(bands):
                done.append("%s (read-back differs)" % n)
            else:
                done.append(n)
        invalidate(app)
        app.audit(req, "cellular.bands." + action, {"lists": done})
        res = read()
        res["changed"] = done
        return res

    @r.route("GET", "/api/cellular/bands")
    def get_bands(req):
        """Supported, enabled and active LTE / 5G SA / 5G NSA bands, priority lists, restore points."""
        return read()

    @r.route("PUT", "/api/cellular/bands")
    def put_bands(req):
        """Set the enabled band lists (tier 2; never empty)."""
        data = validate.only_keys(req.json, list(LISTS))
        if not data:
            raise field_error("lte", "Nothing to change")
        new = {n: check_list(n, v) for n, v in data.items()}
        app.require_tier(req, 2)
        return write_lists(req, new, "set")

    @r.route("POST", "/api/cellular/bands/enable-all")
    def enable_all(req):
        """Enable every band the hardware supports (keeps bands that are already enabled)."""
        data = validate.only_keys(req.json, ["lists"])
        lists = data.get("lists", list(LISTS))
        if not isinstance(lists, list) or not lists or any(x not in LISTS for x in lists):
            raise field_error("lists", "Choose lte, nr_sa and/or nr_nsa")
        app.require_tier(req, 2)
        new = {}
        for n in lists:
            cur = enabled(n) or []
            sup = supported(LISTS[n]["sup"])
            if not sup:
                continue
            new[n] = sorted(set(cur) | set(sup))
        if not new:
            raise unavailable("The modem did not report its supported bands")
        return write_lists(req, new, "enable_all")

    @r.route("POST", "/api/cellular/bands/restore")
    def restore(req):
        """Restore the previous (undo) or the original band lists."""
        data = validate.only_keys(req.json, ["list", "source"])
        which = validate.choice("list", data.get("list", "all"), list(LISTS) + ["all"])
        source = validate.choice("source", data.get("source", "previous"), ["previous", "original"])
        names = list(LISTS) if which == "all" else [which]
        new = {}
        if source == "original":
            orig = (app.state.read_json(ORIGINAL) or {}).get("lists") or {}
            for n in names:
                if orig.get(n):
                    new[n] = sorted(orig[n])
        else:
            for n in names:
                prev = last_snapshot(app, "bands." + n)
                if prev and prev.get("value"):
                    new[n] = sorted(prev["value"])
        if not new:
            raise unavailable("Nothing to restore yet: no earlier band setting was saved by this interface")
        app.require_tier(req, 2)
        return write_lists(req, new, "restore_" + source)

    @r.route("PUT", "/api/cellular/bands/priority")
    def put_priority(req):
        """Set the LTE / NR band priority order (first = most preferred; empty list = no priority)."""
        data = validate.only_keys(req.json, list(PRIORITY))
        if not data:
            raise field_error("lte", "Nothing to change")
        new = {}
        for k, v in data.items():
            g, _s, sup_cmd = PRIORITY[k]
            sup = supported(sup_cmd)
            cur = P.band_list(get(app, M2, g)) or []
            allowed = sorted(set(sup) | set(cur)) if sup else None
            new[k] = validate.int_list(k, v, allowed=allowed, min_items=0)
        app.require_tier(req, 2)
        done = []
        for k, bands in new.items():
            g, s, _sup = PRIORITY[k]
            cur = P.band_list(get(app, M2, g))
            if cur is not None and cur == bands:
                continue
            snapshot(app, "priority." + k, cur or [])
            run_set(app, M2, s, [len(bands)] + bands, "Setting the %s band priority" % ("LTE" if k == "lte" else "5G"),
                    timeout=30)
            done.append(k)
        invalidate(app)
        app.audit(req, "cellular.bands.priority", {"lists": done})
        res = read()
        res["changed"] = done
        return res

    # ---- carrier aggregation ---------------------------------------------------------------------

    def read_ca() -> Dict[str, Any]:
        res = get_any(app, M2, "ca_get_enabled")
        en = None
        if res is not None and res.ok:
            v = P.to_int(res.get("CA enabled"))
            en = None if v is None else bool(v)
        last = last_snapshot(app, "ca.set")
        return {"enabled": en, "readable": en is not None,
                "message": "" if en is not None else (res.message if res is not None else "not available"),
                "last_set": last}

    @r.route("GET", "/api/cellular/ca")
    def get_ca(req):
        """Carrier aggregation on/off as reported by the modem."""
        return read_ca()

    @r.route("PUT", "/api/cellular/ca")
    def put_ca(req):
        """Switch LTE carrier aggregation on or off (tier 2)."""
        data = validate.only_keys(req.json, ["enabled"])
        if "enabled" not in data:
            raise field_error("enabled", "Required")
        v = validate.boolean("enabled", data["enabled"])
        app.require_tier(req, 2)
        cur = read_ca()
        snapshot(app, "ca", cur["enabled"])
        run_set(app, M2, "ca_set_enabled", [int(v)], "Switching carrier aggregation", timeout=20)
        snapshot(app, "ca.set", v)
        invalidate(app)
        app.audit(req, "cellular.ca.set", {"enabled": v})
        return read_ca()

    def combo_bands(value: Any) -> List[int]:
        sup = supported("get_supported_band_list")
        if isinstance(value, str):
            value = [x for x in value.replace(" ", "").split(",") if x]
        bands = validate.int_list("bands", value, allowed=sup or None)
        if len(bands) not in (2, 3):
            raise field_error("bands", "Choose two or three different LTE bands")
        return bands

    def combo_state(bands: List[int]) -> Dict[str, Any]:
        cmd = "ca_bands_get_enabled" if len(bands) == 2 else "ca_tri_bands_get_enabled"
        res = get_any(app, M2, cmd, bands)
        en = None
        if res is not None and res.ok:
            v = P.to_int(res.get("LTE band enabled"))
            en = None if v is None else bool(v)
        return {"bands": bands, "enabled": en,
                "message": "" if en is not None else (res.message if res is not None else "not available")}

    @r.route("GET", "/api/cellular/ca/combo")
    def get_combo(req):
        """Whether a 2- or 3-band LTE carrier aggregation combination is enabled."""
        return combo_state(combo_bands(req.query.get("bands", "")))

    @r.route("PUT", "/api/cellular/ca/combo")
    def put_combo(req):
        """Enable or disable a 2- or 3-band LTE carrier aggregation combination (tier 2)."""
        data = validate.only_keys(req.json, ["bands", "enabled"])
        bands = combo_bands(data.get("bands"))
        if "enabled" not in data:
            raise field_error("enabled", "Required")
        v = validate.boolean("enabled", data["enabled"])
        app.require_tier(req, 2)
        cmd = "ca_bands_set_enabled" if len(bands) == 2 else "ca_tri_bands_set_enabled"
        run_set(app, M2, cmd, bands + [int(v)], "Setting the carrier aggregation combination", timeout=20)
        snapshot(app, "ca.combo", {"bands": bands, "enabled": v})
        invalidate(app)
        app.audit(req, "cellular.ca.combo", {"bands": bands, "enabled": v})
        return combo_state(bands)

    # ---- raw EFS band preference (read-only) ----------------------------------------------------

    def read_local(path: str) -> Optional[bytes]:
        store = getattr(app.cli, "store", None)        # FakeCli in --mock: no real /dev/shm
        if store is not None:
            data = (store.state.get("shm") or {}).pop(path, None)
            if data is not None:
                return data
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return None
        try:
            os.remove(path)
        except OSError:
            pass
        return data

    @r.route("GET", "/api/cellular/bands/efs")
    def efs(req):
        """Decode the modem's raw LTE band preference (EFS lte_bandpref), read-only."""
        out = {"files": [], "bands": []}
        with _efs_lock:
            for name, efs_path, size, base in EFS_FILES:
                local = "/dev/shm/m3ui-%s" % name
                try:
                    res = app.cli.run("nwcli", "qmi_idl", args=["read_file", local, efs_path, str(size * 8)], timeout=10)
                except Exception:  # noqa: BLE001  (ApiError or transport problem: show as unavailable)
                    res = None
                data = read_local(local)
                item = {"name": name, "path": efs_path, "ok": data is not None, "hex": None, "bands": []}
                if data is not None:
                    data = data[:size]
                    item["hex"] = data.hex()
                    item["bands"] = P.decode_lte_bandpref(data, base)
                    out["bands"] += item["bands"]
                elif res is not None:
                    item["message"] = res.message or (res.text or "").strip()[-160:]
                out["files"].append(item)
        if not any(f["ok"] for f in out["files"]):
            raise unavailable("The modem's band preference file could not be read", out)
        return out
