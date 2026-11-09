"""Adapters to the outside world: vendor CLIs, vendor REST, modem AT, Linux system.

`build(cfg)` returns an Adapters bundle for the configured mode:
  mock  -> fixtures + in-memory state (PC development, tests)
  local -> on the device
  adb   -> CLIs over `adb shell`, REST over HTTP to the unit (PC development on real data)
"""
from .at import ModemAT
from .cli import AdbTransport, CliRunner, LocalTransport
from .rest import VendorRest
from .system import SystemInfo


class Adapters:
    def __init__(self, cli, rest, system, mock: bool = False):
        self.cli = cli
        self.rest = rest
        self.system = system
        self.at = ModemAT(cli)
        self.mock = mock


def vendor_password(cli) -> str:
    """The device admin password (same as the stock web UI), read from the vendor settings."""
    r = cli.run("webui_cli", "get_login_settings", check=True, what="Reading the admin password")
    pw = None
    for line in r.text.splitlines():           # "password:[<value>]" - the value may itself contain ']'
        st = line.strip()
        if st.startswith("password:[") and st.endswith("]"):
            pw = st[len("password:["):-1]
            break
    if pw is None:
        pw = r.get("password")
    if pw is None:
        from ..errors import vendor_error
        raise vendor_error("Admin password not found in vendor login settings")
    return pw


def build(cfg) -> Adapters:
    if cfg.get("platform", "stock") not in ("stock", "openwrt"):
        raise ValueError("platform must be stock or openwrt")
    if cfg.get("platform") == "openwrt":
        from .openwrt import build as build_native
        return build_native(cfg)
    if cfg.get("mock"):
        from .mock import FakeCli, FakeRest, FakeSystem
        cli = FakeCli(cfg["fixtures_dir"], cli_dir=cfg["cli_dir"], timeout=cfg["cli_timeout_s"], allow_static_specs=True)
        rest = FakeRest(cfg["fixtures_dir"])
        cli.store.state["rest"] = rest      # one mock model can serve both the CLI and the REST view
        rest.store = cli.store
        return Adapters(cli, rest, FakeSystem(), mock=True)
    if cfg.get("transport") == "adb":
        transport = AdbTransport(cfg.get("adb_serial", ""))
    else:
        transport = LocalTransport()
    cli = CliRunner(cfg["cli_dir"], cfg["cli_timeout_s"], cfg["cli_max_bytes"], transport,
                    allow_static_specs=bool(cfg.get("allow_static_specs", False)))
    rest = VendorRest(cfg["vendor_base"], lambda: vendor_password(cli))
    return Adapters(cli, rest, SystemInfo())
