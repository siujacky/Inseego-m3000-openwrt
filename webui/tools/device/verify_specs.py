#!/usr/bin/env python3
"""Verify every setter Spec on a real M3100 — inside the hardened sandbox, so nothing is applied.

For each spec the CLI is run in tools/device/cli_sandbox.py (no daemon reachable, read-only
filesystem, reboot/kill/socket blocked) with sample answers.  stdin specs pass when every
expected prompt is printed, in order, and no unexpected prompt follows; argv specs pass when
the handler prints no prompt at all (it took the argv path) and reached the vendor API.
Results go to fixtures/sandbox/verified.json (upgrades Spec.source to "sandbox") and a
human-readable report fixtures/sandbox/report.md; transcripts to fixtures/sandbox/<cli>/<cmd>.txt.

    python tools/device/verify_specs.py [--serial 76fe1623] [--only cli:cmd ...]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "server"))
from m3100ui.adapters import cli_specs  # noqa: E402

RC_RE = re.compile(r"[A-Za-z0-9_]+ returned -?\d+(?: \([^)]*\))?")
PROMPT_HINT = re.compile(r"(Enter [^:?]{0,80}[:?]|\(\d/\d\)\s*:|: $)")


def adb(serial, *args, input_bytes=None, timeout=300):
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    p = subprocess.run(["adb", "-s", serial] + list(args), input=input_bytes, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=timeout, env=env)
    return p.returncode, p.stdout.decode("utf-8", "replace")


FMT_RE = re.compile(r"%[-+ #0]*\d*(?:\.\d+)?(?:hh|h|ll|l|z)?[diouxXsc]")


def prompt_regex(prompt):
    """Prompt text -> regex: printf conversions become wildcards, whitespace is flexible."""
    out, pos = [], 0
    text = prompt.strip()
    for m in FMT_RE.finditer(text):
        out.append(re.escape(text[pos:m.start()]))
        out.append(r"-?\w+" if m.group(0)[-1] in "diouxX" else r".*?")
        pos = m.end()
    out.append(re.escape(text[pos:]))
    # re.escape turns spaces into "\ "; let any run of them match flexible whitespace
    return re.compile(re.sub(r"(?:\\ )+", lambda _m: r"\s*", "".join(out)))


def check_stdin(spec, out):
    """PASS: every expected prompt printed in order before the RC line.
    PARTIAL: the prompts that were printed all match, then the CLI called the vendor API (RC line) and stopped —
             trailing prompts (e.g. 'reset the device Y/N') only appear after a successful call.
    BLOCKED: the CLI needed a daemon before prompting (reads current values first) — needs a device round-trip."""
    # cut at the word "returned": prompts are printed without a newline, so the RC line's function name
    # can be glued to the last prompt ("...timeout valuecmd_set_waiting_timeout returned 1 (...)")
    m_rc = re.search(r"returned -?\d+", out)
    head = out[:m_rc.start()] if m_rc else out
    expected = list(spec.prompts)
    if spec.repeat:
        ci, ii = spec.repeat
        n = int(spec.sample_answers()[ci])
        expected = spec.prompts[:ii] + [spec.prompts[ii]] * n + spec.prompts[ii + 1:]
    pos, seen = 0, 0
    for pr in expected:
        m = prompt_regex(pr).search(head, pos)
        if not m:
            break
        pos, seen = m.end(), seen + 1
    if seen == len(expected):
        rest = head[pos:]
        if PROMPT_HINT.search(rest.strip()):
            return "FAIL", "extra prompt after the expected ones: %r" % rest.strip()[:120]
        return "PASS", "prompts match"
    if seen == 0 and not PROMPT_HINT.search(head):
        tail = " ".join(head.strip()[-80:].split())
        return "BLOCKED", "no prompt before the CLI stopped (%s) - verify with a device round-trip" % tail
    if m_rc and not PROMPT_HINT.search(head[pos:].strip()):
        return "PARTIAL", "first %d/%d prompts match; rest come after the vendor call" % (seen, len(expected))
    return "FAIL", "prompt %d not seen: %r" % (seen + 1, expected[seen].strip())


def check_argv(spec, out):
    head = RC_RE.split(out, 1)[0]
    if "Enter " in head or re.search(r"\(\d/\d\)\s*:", head):
        return "FAIL", "handler prompted although argv was given (argc mismatch?)"
    if "Usage:" in out or "usage:" in out:
        return "FAIL", "usage printed - argv format wrong (give the Spec a sample)"
    if not RC_RE.search(out):
        return "FAIL", "no RC line (crashed or rejected argv)"
    return "PASS", "argv path taken"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", default="76fe1623")
    ap.add_argument("--only", nargs="*", help="cli:cmd filters")
    args = ap.parse_args(argv)
    specs = cli_specs.all_specs()
    keys = sorted(specs)
    if args.only:
        want = set(tuple(x.split(":", 1)) for x in args.only)
        keys = [k for k in keys if k in want]
    jobs = []
    for cli, cmd in keys:
        sp = specs[(cli, cmd)]
        ans = sp.sample_answers()
        job = {"cli": cli, "cmd": cmd, "answers": [] if sp.mode == "argv" else ans}
        if sp.mode == "argv":
            job["argv"] = ans
        jobs.append(job)
    rc, out = adb(args.serial, "get-state", timeout=15)
    if "device" not in out:
        sys.exit("device %s not reachable" % args.serial)
    adb(args.serial, "push", os.path.join(HERE, "cli_sandbox.py"), "/data/local/tmp/cli_sandbox.py")
    rc, st = adb(args.serial, "shell", "python3 /data/local/tmp/cli_sandbox.py selftest")
    if "WRITE OK" in st or "qrtr socket OPENED" in st or "SANDBOX_FAILED" in st or "write blocked /data" not in st:
        sys.exit("sandbox self-test failed — refusing to run setters:\n" + st)
    # one batch on the device (stdin and argv jobs); the jobs file is pushed, not piped
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "m3ui-jobs.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(jobs, f)
    rc, out = adb(args.serial, "push", tmp, "/data/local/tmp/m3ui-jobs.json")
    if rc != 0:
        sys.exit("push failed: " + out)
    rc, out = adb(args.serial, "shell", "python3 /data/local/tmp/cli_sandbox.py batch /data/local/tmp/m3ui-jobs.json",
                  timeout=3600)
    start = out.find("[")
    try:
        results = json.loads(out[start:]) if start >= 0 else []
    except ValueError:
        sys.exit("could not parse sandbox output: " + out[:2000])
    if len(results) != len(jobs):
        print("warning: %d results for %d jobs" % (len(results), len(jobs)))
    verified, lines = [], ["# Sandbox verification %s (unit %s)" % (time.strftime("%Y-%m-%d %H:%M"), args.serial), "",
                           "| CLI | command | mode | result | detail |", "|---|---|---|---|---|"]
    for r in results:
        sp = specs[(r["cli"], r["cmd"])]
        verdict, why = (check_argv if sp.mode == "argv" else check_stdin)(sp, r["out"])
        ok = verdict == "PASS"
        d = os.path.join(ROOT, "fixtures", "sandbox", r["cli"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, r["cmd"] + ".txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(r["out"])
        lines.append("| %s | %s | %s | %s | %s |" % (r["cli"], r["cmd"], sp.mode, verdict, why.replace("|", "/")))
        if ok:
            verified.append({"cli": r["cli"], "cmd": r["cmd"], "source": "sandbox", "prompts": sp.prompts,
                             "mode": sp.mode, "date": time.strftime("%Y-%m-%d")})
    # merge with previous results (keep roundtrip records)
    vpath = os.path.join(ROOT, "fixtures", "sandbox", "verified.json")
    old = []
    if os.path.exists(vpath):
        with open(vpath, encoding="utf-8") as f:
            old = json.load(f)
    merged = {(v["cli"], v["cmd"]): v for v in old}
    for v in verified:
        if merged.get((v["cli"], v["cmd"]), {}).get("source") != "roundtrip":
            merged[(v["cli"], v["cmd"])] = v
    with open(vpath, "w", encoding="utf-8", newline="\n") as f:
        json.dump(sorted(merged.values(), key=lambda v: (v["cli"], v["cmd"])), f, indent=1)
    with open(os.path.join(ROOT, "fixtures", "sandbox", "report.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("\n%d/%d specs verified" % (len(verified), len(results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
