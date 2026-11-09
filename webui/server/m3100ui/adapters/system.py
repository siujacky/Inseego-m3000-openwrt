"""Linux-level telemetry and diagnostics (no vendor code involved)."""
import glob
import ipaddress
import os
import re
import subprocess
import time
from typing import Dict, List, Optional

from ..errors import bad_request

HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$")


def valid_host(host: str) -> str:
    h = (host or "").strip()
    try:
        ipaddress.ip_address(h)
        return h
    except ValueError:
        pass
    if not HOST_RE.match(h) or h.startswith("-"):
        raise bad_request("Enter a valid host name or IP address", {"field": "host"})
    return h


def _read(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


class SystemInfo:
    def __init__(self, root: str = ""):
        self.root = root  # for tests: prefix for /proc and /sys

    def p(self, path: str) -> str:
        return self.root + path

    def uptime(self) -> float:
        try:
            return float(_read(self.p("/proc/uptime"), "0").split()[0])
        except (ValueError, IndexError):
            return 0.0

    def loadavg(self) -> List[float]:
        try:
            return [float(x) for x in _read(self.p("/proc/loadavg")).split()[:3]]
        except ValueError:
            return [0.0, 0.0, 0.0]

    def meminfo(self) -> Dict[str, int]:
        out = {}
        for line in _read(self.p("/proc/meminfo")).splitlines():
            m = re.match(r"(\w+):\s+(\d+)", line)
            if m:
                out[m.group(1)] = int(m.group(2)) * 1024
        return {"total": out.get("MemTotal", 0), "available": out.get("MemAvailable", out.get("MemFree", 0)),
                "free": out.get("MemFree", 0), "swap_total": out.get("SwapTotal", 0), "swap_free": out.get("SwapFree", 0)}

    def thermal(self) -> List[Dict]:
        zones = []
        for z in sorted(glob.glob(self.p("/sys/class/thermal/thermal_zone*"))):
            t = _read(os.path.join(z, "temp"))
            try:
                c = int(t) / 1000.0
            except ValueError:
                continue
            if c <= -40 or c > 200:
                continue
            zones.append({"zone": os.path.basename(z), "type": _read(os.path.join(z, "type")), "celsius": round(c, 1)})
        return zones

    def power_supplies(self) -> List[Dict]:
        out = []
        for d in sorted(glob.glob(self.p("/sys/class/power_supply/*"))):
            info = {"name": os.path.basename(d)}
            for k in ("type", "status", "capacity", "health", "present", "online", "voltage_now", "current_now",
                      "temp", "charge_full_design", "charge_full", "charge_now", "cycle_count", "charge_type"):
                v = _read(os.path.join(d, k))
                if v != "":
                    info[k] = int(v) if re.match(r"^-?\d+$", v) else v
            out.append(info)
        return out

    def net_dev(self) -> Dict[str, Dict[str, int]]:
        out = {}
        for line in _read(self.p("/proc/net/dev")).splitlines()[2:]:
            if ":" not in line:
                continue
            name, rest = line.split(":", 1)
            f = rest.split()
            if len(f) >= 16:
                out[name.strip()] = {"rx_bytes": int(f[0]), "rx_packets": int(f[1]), "rx_errors": int(f[2]),
                                     "tx_bytes": int(f[8]), "tx_packets": int(f[9]), "tx_errors": int(f[10])}
        return out

    def hostname(self) -> str:
        return _read(self.p("/proc/sys/kernel/hostname"), "router")

    def kernel(self) -> str:
        return _read(self.p("/proc/version"))

    def cpu(self) -> Dict:
        info = _read(self.p("/proc/cpuinfo"))
        model = ""
        for line in info.splitlines():
            if line.lower().startswith(("model name", "processor")) and ":" in line:
                v = line.split(":", 1)[1].strip()
                if not v.isdigit():
                    model = v
                    break
        freq = _read(self.p("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"))
        return {"model": model, "cores": max(1, info.count("processor\t:")),
                "freq_mhz": int(freq) // 1000 if freq.isdigit() else None}

    # -- commands ---------------------------------------------------------------------------
    @staticmethod
    def run(argv: List[str], timeout: float = 15.0, max_bytes: int = 128 * 1024) -> Dict:
        t0 = time.time()
        try:
            p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=timeout)
            out, rc, to = p.stdout[:max_bytes], p.returncode, False
        except subprocess.TimeoutExpired as e:
            out, rc, to = (e.stdout or b"")[:max_bytes], None, True
        except FileNotFoundError:
            return {"ok": False, "rc": None, "output": "%s not available on this device" % argv[0], "timed_out": False}
        return {"ok": rc == 0, "rc": rc, "timed_out": to, "elapsed_s": round(time.time() - t0, 2),
                "output": out.decode("utf-8", "replace")}

    def ping(self, host: str, count: int = 4, ipv6: bool = False) -> Dict:
        host = valid_host(host)
        count = max(1, min(int(count), 20))
        argv = ["ping6" if ipv6 else "ping", "-c", str(count), "-W", "2", host]
        return self.run(argv, timeout=count * 3 + 5)

    def traceroute(self, host: str, max_hops: int = 20) -> Dict:
        host = valid_host(host)
        return self.run(["traceroute", "-n", "-w", "2", "-q", "1", "-m", str(max(1, min(int(max_hops), 30))), host],
                        timeout=75)

    def nslookup(self, host: str, server: Optional[str] = None) -> Dict:
        argv = ["nslookup", valid_host(host)]
        if server:
            argv.append(valid_host(server))
        return self.run(argv, timeout=15)

    def journal(self, lines: int = 200, unit: Optional[str] = None) -> Dict:
        lines = max(10, min(int(lines), 2000))
        argv = ["journalctl", "--no-pager", "-o", "short-iso", "-n", str(lines)]
        if unit:
            if not re.match(r"^[A-Za-z0-9_.@-]{1,80}$", unit):
                raise bad_request("bad unit name")
            argv += ["-u", unit]
        return self.run(argv, timeout=20, max_bytes=512 * 1024)

    def ip(self, *args: str) -> Dict:
        return self.run(["ip"] + list(args), timeout=5)

    def dhcp_leases(self) -> List[Dict]:
        leases = []
        for path in ("/var/run/data/dnsmasq.leases", "/data/dnsmasq.leases", "/var/lib/misc/dnsmasq.leases",
                     "/tmp/dnsmasq.leases"):
            txt = _read(self.p(path))
            if not txt:
                continue
            for line in txt.splitlines():
                f = line.split()
                if len(f) >= 4:
                    leases.append({"expires": int(f[0]) if f[0].isdigit() else 0, "mac": f[1], "ip": f[2],
                                   "hostname": "" if f[3] == "*" else f[3]})
            break
        return leases

    def systemctl(self, action: str, unit: str) -> Dict:
        if action not in ("start", "stop", "restart", "enable", "disable", "mask", "unmask", "is-active",
                          "is-enabled", "status"):
            raise bad_request("bad action")
        if not re.match(r"^[A-Za-z0-9_.@-]{1,80}\.service$", unit):
            raise bad_request("bad unit name")
        return self.run(["systemctl", action, unit] + (["--no-pager"] if action == "status" else []), timeout=30)
