"""Mock adapters for host development, tests and browser checks (`--mock`).

FakeCli   — same policy checks as the real CliRunner (excluded / unverified setters are
            refused!), but answers come from fixtures/cli/<cli>/<cmd>.txt (redacted
            captures from a real M3100) or from per-command handlers registered in
            m3100ui.mock_handlers.* that keep simple state, so reads reflect writes.
FakeRest  — in-memory JSON store seeded from fixtures/rest/get.json; PUT merges;
            POST/DELETE go through registered handlers.
FakeSystem— plausible /proc,/sys values and canned diagnostics output.
"""
import copy
import json
import os
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..errors import ApiError
from . import cli_specs
from .cli import CliRunner
from .system import SystemInfo, valid_host

CliHandler = Callable[["FixtureStore", List[str], List[str]], str]
RestHandler = Callable[["FakeRest", Any], Any]

CLI_HANDLERS: Dict[Tuple[str, str], CliHandler] = {}
REST_HANDLERS: Dict[Tuple[str, str], RestHandler] = {}


def cli_handler(cli: str, command: str):
    """Register a mock implementation: fn(store, answers, args) -> CLI output text."""
    def deco(fn: CliHandler) -> CliHandler:
        CLI_HANDLERS[(cli, command)] = fn
        return fn
    return deco


def rest_handler(method: str, path: str):
    """Register a mock REST handler: fn(fake_rest, body) -> response JSON."""
    def deco(fn: RestHandler) -> RestHandler:
        REST_HANDLERS[(method.upper(), path.strip("/"))] = fn
        return fn
    return deco


def ok_text(fn: str, module: str = "MOCK", lines: Optional[List[str]] = None) -> str:
    """Build CLI output in the vendor shape: status line + body lines."""
    return "%s returned 0 (%s: success.)\n\n%s\n" % (fn, module, "\n".join(lines or []))


def kv_lines(pairs: List[Tuple[str, Any]], sep: str = ":") -> List[str]:
    return ["%s%s[%s]" % (k, sep, v) for k, v in pairs]


class FixtureStore:
    """Text fixtures keyed by (cli, cmd) with copy-on-write overrides and a call journal."""

    def __init__(self, root: str):
        self.root = root
        self.overrides: Dict[Tuple[str, str], str] = {}
        self.state: Dict[str, Any] = {}       # free-form state for handlers
        self.calls: List[Dict] = []           # every setter call (tests assert on this)
        self.lock = threading.RLock()

    def text(self, cli: str, cmd: str) -> Optional[str]:
        with self.lock:
            if (cli, cmd) in self.overrides:
                return self.overrides[(cli, cmd)]
        path = os.path.join(self.root, "cli", cli, cmd + ".txt")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        return None

    def set_text(self, cli: str, cmd: str, text: str) -> None:
        with self.lock:
            self.overrides[(cli, cmd)] = text


class FakeCli(CliRunner):
    def __init__(self, fixtures_dir: str, **kw):
        super().__init__(**kw)
        self.store = FixtureStore(fixtures_dir)
        from .. import mock_handlers  # noqa: F401  (registers handlers)

    def _execute(self, cli, command, args, answers, limit):
        key = (cli, command)
        h = CLI_HANDLERS.get(key)
        kind = cli_specs.classify(cli, command, args)
        if kind != "get":
            with self.store.lock:
                self.store.calls.append({"cli": cli, "cmd": command, "args": list(args), "answers": list(answers),
                                         "t": time.time()})
        if h:
            return h(self.store, list(answers), list(args)), 0, False, False
        txt = self.store.text(cli, command)
        if txt is not None and kind == "get":
            return txt, 0, False, False
        if kind == "get":
            return "%s returned 5 (MOCK: no fixture captured for this command.)\n" % command, 1, False, False
        return ok_text("cmd_" + command), 0, False, False


class FakeRest:
    def __init__(self, fixtures_dir: str):
        path = os.path.join(fixtures_dir, "rest", "get.json")
        with open(path, "r", encoding="utf-8") as f:
            self.data: Dict[str, Any] = json.load(f)
        self.calls: List[Dict] = []
        self.lock = threading.RLock()
        from .. import mock_handlers  # noqa: F401

    def request(self, method: str, path: str, body: Any = None) -> Any:
        method = method.upper()
        path = path.strip("/")
        with self.lock:
            if method != "GET":
                self.calls.append({"method": method, "path": path, "body": copy.deepcopy(body)})
            h = REST_HANDLERS.get((method, path))
            if h:
                return h(self, body)
            if method == "GET":
                if path not in self.data:
                    raise ApiError(503, "unsupported", "Vendor REST GET %s: unsupported (mock)" % path,
                                   {"vendor_status": 404, "resource": path})
                return copy.deepcopy(self.data[path])
            if method == "PUT":
                cur = self.data.get(path)
                if isinstance(cur, dict) and isinstance(body, dict):
                    cur.update(copy.deepcopy(body))
                else:
                    self.data[path] = copy.deepcopy(body)
                return None
            raise ApiError(503, "unsupported", "Vendor REST %s %s: unsupported (mock)" % (method, path),
                           {"vendor_status": 501, "resource": path})

    def get(self, path):
        return self.request("GET", path)

    def put(self, path, body):
        return self.request("PUT", path, body)

    def post(self, path, body):
        return self.request("POST", path, body)

    def delete(self, path, body=None):
        return self.request("DELETE", path, body)

    def merge_put(self, path, changes):
        cur = self.get(path)
        unknown = [k for k in changes if not isinstance(cur, dict) or k not in cur]
        if unknown:
            raise ApiError(400, "invalid_field", "Unknown field(s) for %s: %s" % (path, ", ".join(unknown)),
                           {"field": unknown[0]})
        # NOTE: the real VendorRest PUTs the full merged object; switching the mock to that is planned
        # together with the area tests that assert on the PUT body (review phase).
        self.put(path, changes)
        return self.get(path)

    def login(self):
        return None


class FakeSystem(SystemInfo):
    START = time.time()

    def uptime(self):
        return 3600 * 5 + (time.time() - self.START)

    def loadavg(self):
        return [round(0.4 + random.random() * 0.6, 2), 0.7, 0.8]

    def meminfo(self):
        return {"total": 693428 * 1024, "available": 400 * 1024 * 1024 + random.randint(0, 9) * 1048576,
                "free": 380 * 1024 * 1024, "swap_total": 338 * 1048576, "swap_free": 338 * 1048576}

    def thermal(self):
        base = 41 + random.random() * 2
        return [{"zone": "thermal_zone0", "type": "cpu0-usr", "celsius": round(base, 1)},
                {"zone": "thermal_zone6", "type": "modem0-usr", "celsius": round(base + 2.5, 1)},
                {"zone": "thermal_zone12", "type": "pa-therm0", "celsius": round(base - 4, 1)},
                {"zone": "thermal_zone14", "type": "battery", "celsius": round(base - 9, 1)}]

    def power_supplies(self):
        return [{"name": "battery", "type": "Battery", "status": "Full", "capacity": 100, "health": "Good",
                 "present": 1, "voltage_now": 4350000, "current_now": 0, "temp": 290, "charge_full_design": 4484000},
                {"name": "usb", "type": "USB", "online": 1}]

    def net_dev(self):
        t = int(time.time() - self.START)
        return {"lo": {"rx_bytes": 1000 + t, "rx_packets": 10, "rx_errors": 0, "tx_bytes": 1000 + t, "tx_packets": 10, "tx_errors": 0},
                "bridge0": {"rx_bytes": 52_000_000 + t * 9000, "rx_packets": 41000, "rx_errors": 0,
                            "tx_bytes": 380_000_000 + t * 85000, "tx_packets": 290000, "tx_errors": 0},
                "rmnet_data0": {"rx_bytes": 395_000_000 + t * 88000, "rx_packets": 300000, "rx_errors": 0,
                                "tx_bytes": 55_000_000 + t * 9500, "tx_packets": 43000, "tx_errors": 0}}

    def hostname(self):
        return "mifi"

    def kernel(self):
        return "Linux version 5.4.161-perf (mock) armv7l"

    def cpu(self):
        return {"model": "ARMv7 Processor rev 5 (v7l)", "cores": 1, "freq_mhz": 1804}

    def dhcp_leases(self):
        return [{"expires": int(time.time()) + 80000, "mac": "02:00:00:00:00:01", "ip": "192.168.1.100", "hostname": "laptop"},
                {"expires": int(time.time()) + 70000, "mac": "02:00:00:00:00:02", "ip": "192.168.1.101", "hostname": "phone"}]

    @staticmethod
    def run(argv, timeout=15.0, max_bytes=128 * 1024):
        cmd = argv[0]
        if cmd in ("ping", "ping6"):
            host = argv[-1]
            n = int(argv[argv.index("-c") + 1]) if "-c" in argv else 4
            lines = ["PING %s (%s): 56 data bytes" % (host, host)]
            for i in range(n):
                lines.append("64 bytes from %s: seq=%d ttl=115 time=%.1f ms" % (host, i, 28 + random.random() * 12))
            lines.append("\n--- %s ping statistics ---\n%d packets transmitted, %d packets received, 0%% packet loss" % (host, n, n))
            return {"ok": True, "rc": 0, "timed_out": False, "elapsed_s": n * 1.0, "output": "\n".join(lines)}
        if cmd == "traceroute":
            host = argv[-1]
            hops = ["%2d  10.%d.%d.1  %.1f ms" % (i, i, i * 3, 10 + i * 4.2) for i in range(1, 8)]
            return {"ok": True, "rc": 0, "timed_out": False, "elapsed_s": 3.2,
                    "output": "traceroute to %s, 20 hops max\n%s\n 8  %s  41.9 ms" % (host, "\n".join(hops), host)}
        if cmd == "nslookup":
            return {"ok": True, "rc": 0, "timed_out": False, "elapsed_s": 0.1,
                    "output": "Server:    192.168.1.1\nAddress 1: 192.168.1.1\n\nName:      %s\nAddress 1: 93.184.216.34" % argv[1]}
        if cmd == "journalctl":
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            return {"ok": True, "rc": 0, "timed_out": False, "elapsed_s": 0.1,
                    "output": "\n".join("%s+0000 mifi m3100ui[812]: mock log line %d" % (now, i) for i in range(40))}
        if cmd == "systemctl":
            action = argv[1]
            out = {"is-active": "active", "is-enabled": "enabled"}.get(action, "")
            return {"ok": True, "rc": 0, "timed_out": False, "elapsed_s": 0.0, "output": out}
        return {"ok": True, "rc": 0, "timed_out": False, "elapsed_s": 0.0, "output": "(mock) " + " ".join(argv)}
