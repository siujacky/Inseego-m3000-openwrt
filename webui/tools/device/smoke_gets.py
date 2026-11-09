#!/usr/bin/env python3
"""Call every parameter-less GET route of a deployed UI on a real unit and report status/latency.

Read-only.  Writes docs/device-smoke.md (status, time, error code/message, top-level keys —
never values, so no secrets end up in the repo).

    M3100_PW=... python tools/device/smoke_gets.py [--base http://192.168.1.1:8088]
"""
import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://192.168.1.1:8088")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "device-smoke.md"))
    a = ap.parse_args(argv)
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(method, path, body=None, timeout=90):
        rq = urllib.request.Request(a.base + path, data=json.dumps(body).encode() if body is not None else None,
                                    method=method, headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with op.open(rq, timeout=timeout) as r:
                raw = r.read()
                return r.status, raw, time.time() - t0
        except urllib.error.HTTPError as e:
            return e.code, e.read(), time.time() - t0
        except Exception as e:  # timeout, reset
            return 0, str(e).encode(), time.time() - t0

    s, raw, _ = call("POST", "/api/login", {"password": os.environ["M3100_PW"]})
    if s != 200:
        sys.exit("login failed: %s %s" % (s, raw[:200]))
    s, raw, _ = call("GET", "/api/meta/routes")
    routes = json.loads(raw)
    gets = [r["path"] for r in routes if r["method"] == "GET" and "{" not in r["path"]
            and not r["path"].endswith(("/secret", "/download")) and "/export" not in r["path"]]
    rows, slow, bad = [], [], []
    for p in gets:
        s, raw, dt = call("GET", p)
        keys, err = "", ""
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = None
        if s == 200:
            if isinstance(data, dict):
                keys = ", ".join(sorted(data.keys())[:12]) + (" …" if len(data) > 12 else "")
            elif isinstance(data, list):
                keys = "[%d items]" % len(data)
        else:
            e = (data or {}).get("error", {}) if isinstance(data, dict) else {}
            err = "%s: %s" % (e.get("code", ""), (e.get("message", "") or raw[:160].decode("utf-8", "replace")))
            bad.append(p)
        if dt > 3:
            slow.append(p)
        rows.append((p, s, dt, err or keys))
        print("%3s %6.2fs %s %s" % (s, dt, p, err[:100]))
    lines = ["# Real-device GET smoke test", "", "Base %s, %s, %d routes. Non-200: %d. Slower than 3 s: %d." % (
        a.base, time.strftime("%Y-%m-%d %H:%M"), len(rows), len(bad), len(slow)), "",
        "| route | status | time | error or keys |", "|---|---|---|---|"]
    for p, s, dt, info in rows:
        lines.append("| `%s` | %s | %.2f s | %s |" % (p, s, dt, str(info).replace("|", "/")[:220]))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n%d routes, %d non-200, %d slow -> %s" % (len(rows), len(bad), len(slow), a.out))


if __name__ == "__main__":
    main()
