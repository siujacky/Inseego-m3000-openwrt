#!/usr/bin/env python3
"""Install / update / remove the web UI on an M3100 over ADB.

    python tools/deploy.py install              # side-by-side on :8088, stock UI untouched
    python tools/deploy.py install --takeover   # new UI on :80, stock UI moved to :8080
    python tools/deploy.py status | logs [-n 100] | restart | rollback
    python tools/deploy.py uninstall [--keep-state]   # back to stock (vendor UI on :80)

Layout on the device:
    /data/m3100-webui/releases/<stamp>/{server,web,device}   one directory per install
    /data/m3100-webui/current -> releases/<stamp>             atomic switch, previous kept for rollback
    /data/m3100-webui/config.json                             listener + vendor settings (managed here)
    /data/m3100-webui/state/                                  UI settings, snapshots, audit log (kept)
    /etc/systemd/system/m3100-webui.service
    /etc/systemd/system/nvtl_webserver.service.d/50-m3100-webui.conf   (takeover only)
Always targets one serial (default 76fe1623) — never a bare `adb shell`.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "/data/m3100-webui"
UNIT = "/etc/systemd/system/m3100-webui.service"
DROPIN_DIR = "/etc/systemd/system/nvtl_webserver.service.d"
DROPIN = DROPIN_DIR + "/50-m3100-webui.conf"
ALLOWED_HOSTS = ["router.lan", "router", "5g.router", "mifi", "mifi.local", "webapi.nvtl"]


class Adb:
    def __init__(self, serial: str):
        self.serial = serial
        env = dict(os.environ)
        env["MSYS_NO_PATHCONV"] = "1"
        self.env = env

    def _cmd(self, *args):
        return ["adb", "-s", self.serial] + list(args)

    def sh(self, script: str, check: bool = True, timeout: int = 120) -> str:
        p = subprocess.run(self._cmd("shell", script), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, env=self.env)
        out = p.stdout.decode("utf-8", "replace")
        if check and p.returncode != 0:
            raise RuntimeError("device command failed (%s):\n%s\n%s" % (p.returncode, script, out))
        return out

    def push(self, local: str, remote: str) -> None:
        p = subprocess.run(self._cmd("push", local, remote), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=300, env=self.env)
        if p.returncode != 0:
            raise RuntimeError("adb push failed: %s" % p.stdout.decode("utf-8", "replace"))

    def reachable(self) -> bool:
        try:
            p = subprocess.run(self._cmd("get-state"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15, env=self.env)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return p.stdout.strip() == b"device"


def build_bundle() -> bytes:
    """tar.gz of server/, web/, device/ (no caches, no tests)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for top in ("server", "web", "device"):
            base = os.path.join(ROOT, top)
            for dirpath, dirnames, files in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".pytest_cache")]
                for f in files:
                    if f.endswith((".pyc", ".pyo")):
                        continue
                    full = os.path.join(dirpath, f)
                    arc = os.path.relpath(full, ROOT).replace(os.sep, "/")
                    info = tar.gettarinfo(full, arc)
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    info.mode = 0o755 if f.endswith(".sh") else 0o644
                    if f.endswith((".sh", ".service", ".conf", ".py", ".js", ".css", ".html", ".json", ".svg", ".md")):
                        with open(full, "rb") as fh:
                            data = fh.read().replace(b"\r\n", b"\n")
                        info.size = len(data)
                        tar.addfile(info, io.BytesIO(data))
                    else:
                        with open(full, "rb") as fh:
                            tar.addfile(info, fh)
    return buf.getvalue()


def write_config(adb: Adb, port: int, takeover: bool) -> None:
    cfg = {
        "host": "::", "port": port, "state_dir": BASE + "/state",
        "web_root": BASE + "/current/web",
        "vendor_base": "http://127.0.0.1:8080" if takeover else "http://127.0.0.1:80",
        "allowed_hosts": ALLOWED_HOSTS, "transport": "local", "log_level": "INFO",
        "allow_static_specs": False,
    }
    text = json.dumps(cfg, indent=1)
    adb.sh("cat > %s/config.json.tmp <<'EOF'\n%s\nEOF\nmv %s/config.json.tmp %s/config.json" % (BASE, text, BASE, BASE))


def cmd_install(adb: Adb, args) -> None:
    pyv = adb.sh("/usr/bin/python3 --version", check=False).strip()
    if "Python 3." not in pyv:
        sys.exit("python3 not found on the device: %s" % pyv)
    print("device:", adb.sh("cat /etc/os-release | grep PRETTY_NAME", check=False).strip(), "|", pyv)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bundle = build_bundle()
    tmp = os.path.join(os.environ.get("TEMP", "/tmp"), "m3100-webui-%s.tgz" % stamp)
    with open(tmp, "wb") as f:
        f.write(bundle)
    print("bundle %.1f KB" % (len(bundle) / 1024))
    adb.push(tmp, "/data/local/tmp/m3100-webui.tgz")
    os.unlink(tmp)
    rel = "%s/releases/%s" % (BASE, stamp)
    adb.sh("set -e; mkdir -p %s %s/state; tar -xzf /data/local/tmp/m3100-webui.tgz -C %s; rm -f /data/local/tmp/m3100-webui.tgz; "
           "chmod 755 %s/device/*.sh; ln -sfn %s %s/current"
           % (rel, BASE, rel, rel, rel, BASE))
    takeover = args.takeover
    port = 80 if takeover else args.port
    write_config(adb, port, takeover)
    adb.sh("cp %s/current/device/m3100-webui.service %s && chmod 644 %s" % (BASE, UNIT, UNIT))
    if takeover:
        adb.sh("set -e; mkdir -p %s; cp %s/current/device/50-m3100-webui.conf %s; chmod 644 %s; systemctl daemon-reload; "
               "systemctl stop m3100-webui.service 2>/dev/null || true; systemctl restart nvtl_webserver.service"
               % (DROPIN_DIR, BASE, DROPIN, DROPIN), timeout=180)
        print("vendor web server:", adb.sh("sh %s/current/device/vendor-port.sh status" % BASE, check=False).strip())
    else:
        adb.sh("systemctl daemon-reload")
    adb.sh("systemctl enable m3100-webui.service >/dev/null 2>&1; systemctl restart m3100-webui.service")
    # keep the 3 newest releases
    adb.sh("cd %s/releases && ls -1t | tail -n +4 | xargs -r rm -rf" % BASE, check=False)
    health(adb, port)


def health(adb: Adb, port: int) -> None:
    for _ in range(20):
        out = adb.sh("curl -s -m 3 -H 'Host: 192.168.1.1' http://127.0.0.1:%d/api/meta || true" % port, check=False)
        if '"ui_version"' in out:
            print("UI is up:", out.strip())
            print("Open http://192.168.1.1%s/" % ("" if port == 80 else ":%d" % port))
            return
        time.sleep(1.5)
    print("UI did not answer yet; recent log:")
    print(adb.sh("journalctl -u m3100-webui.service -n 40 --no-pager", check=False))


def cmd_uninstall(adb: Adb, args) -> None:
    adb.sh("systemctl disable --now m3100-webui.service 2>/dev/null; rm -f %s" % UNIT, check=False)
    had = adb.sh("test -f %s && echo yes || echo no" % DROPIN, check=False).strip() == "yes"
    if had:
        adb.sh("rm -f %s; rmdir %s 2>/dev/null; systemctl daemon-reload; "
               "grep -q ' /etc/lighttpd/lighttpd.conf ' /proc/self/mountinfo && umount /etc/lighttpd/lighttpd.conf; "
               "systemctl restart nvtl_webserver.service" % (DROPIN, DROPIN_DIR), check=False, timeout=180)
    else:
        adb.sh("systemctl daemon-reload", check=False)
    if args.keep_state:
        adb.sh("rm -rf %s/releases %s/current %s/config.json" % (BASE, BASE, BASE), check=False)
    else:
        adb.sh("rm -rf %s" % BASE, check=False)
    print("removed%s; stock web UI on :80" % (" (state kept)" if args.keep_state else ""))


def cmd_status(adb: Adb, args) -> None:
    print(adb.sh("systemctl is-active m3100-webui.service; systemctl is-enabled m3100-webui.service; "
                 "readlink %s/current; cat %s/config.json 2>/dev/null | grep -E 'port|vendor_base'; "
                 "sh %s/current/device/vendor-port.sh status 2>/dev/null; netstat -tlnp 2>/dev/null | grep -E ':(80|8080|8088) '"
                 % (BASE, BASE, BASE), check=False))


def cmd_logs(adb: Adb, args) -> None:
    print(adb.sh("journalctl -u m3100-webui.service -n %d --no-pager" % args.n, check=False))


def cmd_restart(adb: Adb, args) -> None:
    adb.sh("systemctl restart m3100-webui.service")
    port = int(adb.sh("grep -o '\"port\": *[0-9]*' %s/config.json | grep -o '[0-9]*$'" % BASE, check=False).strip() or 8088)
    health(adb, port)


def cmd_rollback(adb: Adb, args) -> None:
    rels = adb.sh("ls -1t %s/releases" % BASE).split()
    cur = adb.sh("readlink %s/current" % BASE, check=False).strip().rsplit("/", 1)[-1]
    older = [r for r in rels if r < cur]
    if not older:
        sys.exit("no older release to roll back to")
    target = older[0]
    adb.sh("ln -sfn %s/releases/%s %s/current && systemctl restart m3100-webui.service" % (BASE, target, BASE))
    print("rolled back to", target)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", default="76fe1623", help="adb serial of the M3100 (default 76fe1623)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("--takeover", action="store_true", help="serve on :80 and move the stock UI to :8080")
    i.add_argument("--port", type=int, default=8088, help="side-by-side port (default 8088)")
    u = sub.add_parser("uninstall")
    u.add_argument("--keep-state", action="store_true")
    sub.add_parser("status")
    lg = sub.add_parser("logs")
    lg.add_argument("-n", type=int, default=80)
    sub.add_parser("restart")
    sub.add_parser("rollback")
    args = ap.parse_args(argv)
    adb = Adb(args.serial)
    if not adb.reachable():
        sys.exit("M3100 %s not reachable over adb (USB mode switch needed? see ../m3000/STATUS.txt)" % args.serial)
    {"install": cmd_install, "uninstall": cmd_uninstall, "status": cmd_status, "logs": cmd_logs,
     "restart": cmd_restart, "rollback": cmd_rollback}[args.cmd](adb, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
