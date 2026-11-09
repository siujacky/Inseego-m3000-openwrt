"""Configuration: built-in defaults < config.json < command line.

Runtime-editable UI settings (router name, expert mode, session timeout …) live in
<state_dir>/settings.json via app.state, not here.
"""
import json
import os
from typing import Any, Dict, Optional

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(os.path.dirname(_PKG_DIR))

DEFAULTS: Dict[str, Any] = {
    "platform": "stock",             # explicit native selection, never inferred from missing vendor tools
    "password_file": "",             # required in native mode; private, device-specific provisioning
    "qmi_device": "",                # USB QMI only, never inferred for the integrated QRTR modem
    "modem_helper": "",              # optional root-provisioned native QRTR helper; see docs/openwrt.md
    "native_timeout_s": 10,
    "native_max_bytes": 262144,
    "native_services": ["system", "dnsmasq", "odhcpd", "network", "firewall", "sysntpd", "bluetooth", "gpsd", "m3100-gnss", "m3100-display"],
    # HTTP listener. "::" with dual-stack serves IPv4 too; falls back to 0.0.0.0.
    "host": "::",
    "port": 8088,
    "web_root": os.path.join(_REPO_DIR, "web"),
    "state_dir": "/data/m3100-webui/state",
    # Vendor web stack (lighttpd + restapi.fcgi). :80 in side-by-side mode, :8080 after takeover.
    "vendor_base": "http://127.0.0.1:80",
    # Vendor CLIs
    "cli_dir": "/opt/nvtl/bin",
    "cli_timeout_s": 8.0,
    "cli_max_bytes": 256 * 1024,
    # Extra Host header names accepted besides IP literals and localhost (DNS-rebinding guard).
    "allowed_hosts": ["router.lan", "router", "5g.router"],
    # Upload limit for the few endpoints that accept files (backup restore, VPN profiles).
    "max_upload_bytes": 4 * 1024 * 1024,
    "max_body_bytes": 256 * 1024,
    # Transport for CLI calls: "local" (on device), "adb" or "ssh" (PC development against a real unit).
    "transport": "local",
    "adb_serial": "",
    "ssh_host": "192.168.1.1",
    # Mock mode
    "mock": False,
    "fixtures_dir": os.path.join(_REPO_DIR, "fixtures"),
    "mock_password": "admin",
    "log_level": "INFO",
}


class Config(dict):
    """dict with attribute access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


def load(path: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None) -> Config:
    cfg = Config(DEFAULTS)
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config file must hold a JSON object")
        cfg.update(data)
    for k, v in (overrides or {}).items():
        if v is not None:
            cfg[k] = v
    return cfg
