"""About: model, hardware and software versions, identifiers (masked unless revealed),
MAC addresses, uptime / kernel / CPU, open-source notices and the vendor-UI fallback link.

Sources: REST DeviceInfo; modem2_cli get_info / get_sup_tech; settings_cli get_branding_info /
get_features; usb_cli get_config (serial number); fota_cli cfg_get_current_record
(configuration version); dsm_cli get_usb_mac_address / get_wifi_mac_address ('2' Wi-Fi MACs,
handler @0x2620 has a fixed 2-entry buffer); mifi_qcmap_cli get_ethernet_macaddr;
REST EthernetSettings/Details; /opt/nvtl/etc/mifios-version; /sysconf/pri_cust_version.txt;
/proc (uptime, kernel, CPU, memory).
"""
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from .. import __version__
from ..errors import unavailable

SECRET_KEYS = ("imei", "imeisv", "meid", "esn", "serial", "iccid", "phone_number")
TECH_BITS = [(0x01, "GSM"), (0x02, "UMTS"), (0x04, "CDMA"), (0x08, "EVDO"), (0x10, "LTE"), (0x20, "NR5G")]  # modem2 tech mask
TECH_NAMES = {"GSM": "2G GSM", "UMTS": "3G UMTS", "CDMA": "CDMA 1x", "EVDO": "EV-DO", "LTE": "4G LTE", "NR5G": "5G NR"}

OPEN_SOURCE = [
    {"name": "This web interface", "license": "Original code",
     "note": "Server: Python standard library only. Browser: plain JavaScript and CSS written for this project; "
             "no third-party libraries, fonts or tracking."},
    {"name": "Python 3.8", "license": "PSF-2.0", "note": "Runtime used by this interface (part of the router firmware)."},
    {"name": "Linux kernel 5.4", "license": "GPL-2.0", "note": "Part of the router firmware."},
    {"name": "BusyBox, systemd, dnsmasq, lighttpd, OpenSSL, SQLite, zlib, libxml2", "license": "various open-source licences",
     "note": "Components of the router firmware supplied by its manufacturer, who provides the licence texts and source offer."},
]


def mask(v: Optional[str], keep: int = 4) -> str:
    s = (v or "").strip()
    if not s or s in ("0", "<redacted>", "redacted"):
        return s
    return "•" * max(0, len(s) - keep) + s[-keep:] if len(s) > keep else s


def clean(v: Any) -> str:
    s = "" if v is None else str(v).strip()
    return "" if s in ("<redacted>", "redacted") else s


def tech_list(*notes_or_masks: Optional[str]) -> List[str]:
    """Union of modem2 tech-mode notes '( GSM UMTS LTE NR5G)' or numeric masks, in mask order."""
    found = set()
    for v in notes_or_masks:
        if v is None:
            continue
        s = str(v).strip()
        if re.match(r"^(0x[0-9a-fA-F]+|\d+)$", s):
            m = int(s, 0)
            found.update(name for bit, name in TECH_BITS if m & bit)
        else:
            found.update(x for x in re.sub(r"[()]", " ", s).split() if x in TECH_NAMES)
    return [TECH_NAMES[name] for _bit, name in TECH_BITS if name in found]


def register(app):
    r = app.router

    def read_text(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(256).strip()
        except OSError:
            return ""

    def vendor_port() -> int:
        try:
            u = urllib.parse.urlsplit(app.cfg.get("vendor_base", "http://127.0.0.1:80"))
            return int(u.port or 80)
        except ValueError:
            return 80

    def gather() -> Dict[str, Any]:
        dev = app.rest_try("DeviceInfo", {}) or {}
        info = app.cli_try("modem2_cli", "get_info")
        brand = app.cli_try("settings_cli", "get_branding_info")
        sup = app.cli_try("modem2_cli", "get_sup_tech")
        ena = app.cli_try("modem2_cli", "enabled_tech_get")
        usb = app.cli_try("usb_cli", "get_config")
        cfg = app.cli_try("fota_cli", "cfg_get_current_record")
        if not dev and not info:
            raise unavailable("Device information is not available")
        g = (lambda k: info.get(k) if info else None)
        macs: List[Dict[str, str]] = []

        def add_mac(name: str, mac: Optional[str]):
            mac = clean(mac).upper()
            if re.match(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", mac) and mac != "00:00:00:00:00:00" and \
                    not any(m["name"] == name for m in macs):
                macs.append({"name": name, "mac": mac})
        add_mac("Router (LAN / Wi-Fi)", dev.get("DeviceInfoMacAddress"))
        w = app.cli_try("dsm_cli", "get_wifi_mac_address", ["2"])
        if w:  # the prompt is echoed on the same line: "What is the no of WiFi Macs:WiFi1=[..]"
            for n, mac in re.findall(r"WiFi(\d)=\[([^\]]*)\]", w.text):
                add_mac("Wi-Fi %s" % n, mac)
        u = app.cli_try("dsm_cli", "get_usb_mac_address")
        if u:
            add_mac("USB (router side)", u.get("usb_device"))
            add_mac("USB (computer side)", u.get("usb_host"))
        e = app.cli_try("mifi_qcmap_cli", "get_ethernet_macaddr")
        if e:
            add_mac("Ethernet", e.get("interface eth0 mac is"))
        for i, d in enumerate(app.rest_try("EthernetSettings/Details", []) or []):
            if isinstance(d, dict):
                add_mac("Ethernet" if i == 0 else "Ethernet %d" % (i + 1), d.get("Mac"))
        mem = app.system.meminfo()
        up = app.system.uptime()
        secrets = {
            "imei": clean(dev.get("DeviceInfoIMEI")) or clean(g("imei")),
            "imeisv": clean(dev.get("DeviceInfoIMEISV")) or clean(g("imei sv")),
            "meid": clean(dev.get("DeviceInfoMEID")) or clean(g("meid")),
            "esn": "" if clean(dev.get("DeviceInfoESN") or g("esn")) in ("", "0") else clean(dev.get("DeviceInfoESN") or g("esn")),
            "serial": clean(usb.get("ser")) if usb else "",
            "iccid": clean(g("iccid")),
            "phone_number": clean(g("mdn")),
        }
        return {
            "device": {
                "model": dev.get("DeviceInfoModelNumber") or g("model") or "",
                "manufacturer": dev.get("DeviceInfoManufacturerName") or g("manufacture") or "",
                "hardware_version": dev.get("DeviceInfoHwVersion") or g("hw version") or "",
                "product_sku": clean(dev.get("DeviceInfoSKU")) or (clean(brand.get("product_sku")) if brand else ""),
                "factory_id": clean(g("fid")),
                "radio_technologies": tech_list(sup.note("available tech modes") if sup else None,
                                                ena.note("enabled tech modes") if ena else None),
            },
            "software": {
                "firmware": dev.get("DeviceInfoSwVersion") or g("fw version") or "",
                "modem_firmware": dev.get("DeviceInfoModemFwVersion") or g("fw version") or "",
                "wifi_firmware": dev.get("DeviceInfoWiFiFwVesion", ""),
                "vendor_web_ui": dev.get("DeviceInfoWebUISwVersion", ""),
                "device_screen_ui": dev.get("DeviceInfoDeviceUISwVersion", ""),
                "os_version": read_text("/opt/nvtl/etc/mifios-version"),
                "software_version": dev.get("DeviceInfoCuteVersion") or (brand.get("cute_version") if brand else "") or "",
                "pri_version": str(dev.get("DeviceInfoPRIVersionDetailed") or dev.get("DeviceInfoPRIVersion") or g("pri version full") or ""),
                "pri_customization": read_text("/sysconf/pri_cust_version.txt"),
                "configuration_version": cfg.get("configuration version") if cfg else "",
                "prl_version": g("prl version") or "",
                "eri_version": str(dev.get("DeviceInfoERIVersion", "") if dev else (g("eri version") or "")),
                "rest_api": dev.get("DeviceInfoRESTAPIVersion", ""),
                "kernel": app.system.kernel(),
                "this_ui": __version__,
            },
            "identifiers": {k: mask(v) for k, v in secrets.items()},
            "identifiers_present": {k: bool(v) for k, v in secrets.items()},
            "macs": macs,
            "system": {
                "uptime_s": int(up), "boot_time": int(time.time() - up), "hostname": app.system.hostname(),
                "cpu": app.system.cpu(), "memory_total": mem.get("total"), "memory_available": mem.get("available"),
                "load": app.system.loadavg(), "temperatures": app.system.thermal(),
            },
            "vendor_ui": {"port": vendor_port(),
                          "note": "The original vendor interface stays available as a fallback."},
            "open_source": OPEN_SOURCE,
            "_secrets": secrets,
        }

    def cached() -> Dict[str, Any]:
        return app.cache.get("about:info", 60, gather)

    @r.route("GET", "/api/about")
    def about(req):
        """Model, versions, masked identifiers, MAC addresses, system info, notices."""
        d = dict(cached())
        d.pop("_secrets", None)
        d["system"] = dict(d["system"], uptime_s=int(app.system.uptime()))
        return d

    @r.route("GET", "/api/about/identifiers")
    def identifiers(req):
        """Unmasked device identifiers (IMEI, IMEISV, MEID, ESN, serial, ICCID, phone number)."""
        app.audit(req, "about.reveal_identifiers")
        return dict(cached()["_secrets"])
