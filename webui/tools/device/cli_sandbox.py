#!/usr/bin/env python3
"""Run a vendor CLI on the M3100 where it cannot change anything.

Purpose: discover the stdin prompt sequence of vendor *setter* commands without
executing them.  Runs on the device (Python 3.8, root).

Isolation, applied in this order (abort on any failure):
  1. unshare(NEWNS | NEWIPC | NEWNET)  (PID/UTS namespaces are not in this kernel)
  2. mount propagation of / made private (so nothing below leaks to the host)
  3. every mount bind-remounted read-only, including /proc (blocks /proc/sysrq-trigger)
  4. /dev replaced by a tmpfs holding only null, zero, random, urandom, tty
     (no smd/at/diag/mtd/ubi/qcqmi nodes)
  5. empty tmpfs over /tmp /run /var/run /dev/shm /dev/socket
     (hides msgbus.socket, systemd/dbus sockets, QCMAP/netmgr sockets)
  6. the CLI runs under `strace -f` with fault injection (EPERM) on reboot, kill,
     tkill, tgkill, init/finit/delete_module, kexec_load, mount, umount2, pivot_root,
     swapon, swapoff, settimeofday, clock_settime, adjtimex, clock_adjtime,
     sethostname, setdomainname, setns, unshare, process_vm_writev, acct and
     socket/socketpair (no QMI/QRTR, netlink, unix or inet sockets at all).
     This kernel has no seccomp and no PID/UTS namespaces, hence strace.

Usage:
    python3 cli_sandbox.py selftest
    python3 cli_sandbox.py run <cli> <command> [answer ...]
    python3 cli_sandbox.py batch <jsonfile>      # [{"cli":..,"cmd":..,"answers":[..]}, ...]

Each run: answers written one per line to stdin then stdin closed; 6 s timeout;
output capped at 64 KiB.
"""
import ctypes
import json
import os
import subprocess
import sys

CLONE_NEWNS, CLONE_NEWIPC, CLONE_NEWNET = 0x00020000, 0x08000000, 0x40000000
MS_RDONLY, MS_NOSUID, MS_REMOUNT, MS_BIND, MS_REC, MS_PRIVATE = 1, 2, 32, 4096, 0x4000, 1 << 18
# syscalls answered with EPERM (names as strace knows them)
BLOCKED = {
    "reboot": 88, "kill": 37, "tkill": 238, "tgkill": 268, "init_module": 128,
    "finit_module": 379, "delete_module": 129, "kexec_load": 347, "mount": 21,
    "umount2": 52, "pivot_root": 218, "swapon": 87, "swapoff": 115,
    "settimeofday": 79, "clock_settime": 262, "adjtimex": 124, "clock_adjtime": 372,
    "sethostname": 74, "setdomainname": 121, "setns": 375, "unshare": 337,
    "ptrace": 26, "process_vm_writev": 377, "acct": 51,
}

libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]


def die(msg):
    err = ctypes.get_errno()
    sys.stdout.write("SANDBOX_FAILED %s errno=%d %s\n" % (msg, err, os.strerror(err)))
    sys.stdout.flush()
    os._exit(3)


def _mount(src, dst, fstype, flags, data=None):
    return libc.mount(src, dst.encode(), fstype, flags, data)


def enter_sandbox():
    if libc.unshare(CLONE_NEWNS | CLONE_NEWIPC | CLONE_NEWNET) != 0:
        die("unshare")
    if _mount(None, "/", None, MS_REC | MS_PRIVATE) != 0:
        die("make-private")
    mps = []
    with open("/proc/self/mountinfo") as f:
        for line in f:
            mps.append(line.split()[4].replace("\\040", " "))
    failed = []
    for mp in mps:
        if mp.startswith("/dev"):
            continue
        if _mount(None, mp, None, MS_REMOUNT | MS_BIND | MS_RDONLY) != 0:
            failed.append(mp)
    for critical in ("/", "/data", "/systemrw", "/persist", "/proc", "/sys"):
        if critical in failed:
            die("ro-remount " + critical)
    if _mount(b"tmpfs", "/dev", b"tmpfs", MS_NOSUID, b"size=1m,mode=755") != 0:
        die("dev tmpfs")
    for name, major, minor in (("null", 1, 3), ("zero", 1, 5), ("random", 1, 8), ("urandom", 1, 9), ("tty", 5, 0)):
        path = "/dev/" + name
        try:
            os.mknod(path, 0o666 | 0o020000, os.makedev(major, minor))
            os.chmod(path, 0o666)
        except OSError as e:
            sys.stdout.write("SANDBOX_FAILED mknod %s %s\n" % (name, e))
            os._exit(3)
    os.symlink("/proc/self/fd", "/dev/fd")
    for d in ("/tmp", "/run", "/var/run", "/dev/shm", "/dev/socket"):
        if os.path.islink(d):
            continue
        if not os.path.isdir(d):
            if d.startswith("/dev/"):
                os.mkdir(d)
            else:
                continue
        if _mount(b"tmpfs", d, b"tmpfs", MS_NOSUID, b"size=4m") != 0:
            die("tmpfs " + d)
    return failed


STRACE = "/usr/bin/strace"
INJECT = sorted(list(BLOCKED) + ["socket", "socketpair"])


def _wrap(argv):
    if not os.path.exists(STRACE):
        sys.stdout.write("SANDBOX_FAILED no strace\n")
        os._exit(3)
    calls = ",".join(INJECT)
    return [STRACE, "-f", "-qq", "-o", "/dev/null", "-e", "trace=" + calls,
            "-e", "inject=" + calls + ":error=EPERM"] + argv


ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin:/opt/nvtl/bin",
       "LD_LIBRARY_PATH": "/opt/nvtl/lib:/opt/nvtl/data/branding/lib", "HOME": "/tmp"}


def run_argv(argv, answers=(), timeout=6.0):
    p = subprocess.Popen(_wrap(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env=ENV)
    data = ("\n".join(answers) + "\n").encode() if answers else b""
    try:
        out, _ = p.communicate(data, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        timed_out = True
    text = out[:65536].decode("utf-8", "replace")
    return {"rc": p.returncode, "timeout": timed_out, "out": text, "truncated": len(out) > 65536}


SELFTEST_CODE = r'''
import ctypes, os, socket
libc = ctypes.CDLL("libc.so.6", use_errno=True)
def t(name, fn):
    r = fn(); e = ctypes.get_errno()
    print(name, "ret=%s" % r, "errno=%s" % os.strerror(e) if r != 0 else "")
t("kill(1,0)", lambda: libc.kill(1, 0))
t("reboot(badmagic)", lambda: libc.syscall(88, 0, 0, 0, 0))
t("setns", lambda: libc.syscall(375, 0, 0))
try:
    s = socket.socket(42, socket.SOCK_DGRAM, 0); print("qrtr socket OPENED (NOT SAFE)"); s.close()
except OSError as e:
    print("qrtr socket blocked:", e.strerror)
for p in ("/data/.sb_probe", "/etc/.sb_probe", "/proc/sysrq-trigger"):
    try:
        open(p, "w").write("")
        print("WRITE OK (NOT SAFE)", p)
        if p != "/proc/sysrq-trigger":
            os.unlink(p)
    except OSError as e:
        print("write blocked", p, e.strerror)
print("msgbus socket visible:", os.path.exists("/tmp/msgbus.socket"))
print("dev nodes:", sorted(os.listdir("/dev")))
'''


def main(argv):
    if not argv or argv[0] not in ("selftest", "run", "runargv", "batch"):
        print(__doc__)
        return 2
    failed = enter_sandbox()
    if argv[0] == "selftest":
        r = run_argv(["/usr/bin/python3", "-c", SELFTEST_CODE])
        print(r["out"])
        print("ro-remount failures:", failed)
        return 0
    if argv[0] in ("run", "runargv"):
        cli, cmd, rest = argv[1], argv[2], argv[3:]
        if argv[0] == "run":     # rest = stdin answers
            r = run_argv(["/opt/nvtl/bin/" + cli] + ([cmd] if cmd else []), rest)
        else:                    # rest = command-line arguments, stdin empty
            r = run_argv(["/opt/nvtl/bin/" + cli] + ([cmd] if cmd else []) + rest, [])
        sys.stdout.write(r["out"])
        print("\n<<rc=%s timeout=%s>>" % (r["rc"], r["timeout"]))
        return 0
    jobs = json.load(open(argv[1]))
    results = []
    for j in jobs:
        if j.get("argv") is not None:      # values as command-line arguments, empty stdin
            r = run_argv(["/opt/nvtl/bin/" + j["cli"], j["cmd"]] + [str(a) for a in j["argv"]], [])
        else:                              # values as stdin answers
            r = run_argv(["/opt/nvtl/bin/" + j["cli"], j["cmd"]], j.get("answers", []))
        r.update({"cli": j["cli"], "cmd": j["cmd"], "answers": j.get("answers", []), "argv": j.get("argv")})
        results.append(r)
    json.dump(results, sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
