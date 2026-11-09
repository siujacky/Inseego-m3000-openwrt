#!/usr/bin/env python3
"""Redact private values from captured vendor CLI output / REST JSON.

Used to turn raw captures (kept in ../.private) into shareable test fixtures.
Rules are conservative: anything that looks like an identifier or secret is
replaced with a same-shaped placeholder so parsers still see realistic text.

    python tools/redact.py SRC_DIR DST_DIR --secret "<literal>" ...
"""
from __future__ import annotations

import argparse
import os
import re
import sys

MAC_RE = re.compile(r"\b([0-9A-Fa-f]{2})([:-])(?:[0-9A-Fa-f]{2}\2){4}[0-9A-Fa-f]{2}\b")
LONG_DIGITS_RE = re.compile(r"(?<![\d.])\d{14,20}(?![\d.])")
# label:[value] / label : [value] / label=value where the label names a secret
SECRET_LABEL_RE = re.compile(
    r"(?P<label>[A-Za-z0-9_ .\-\[\]]*(?:pass(?:word|phrase)?|psk|secret|answer|private[ _]?key|"
    r"pre[ _-]?shared|wpa[ _]?key|wep[ _]?key|auth[ _]?key|lwm2m|token|pin(?:1|2)?\b|puk)"
    r"[A-Za-z0-9_ .\-\[\]()]*?)(?P<sep>\s*[:=]\s*)\[(?P<value>[^\]\n]*)\]",
    re.IGNORECASE,
)
SSID_LABEL_RE = re.compile(r"(?P<label>[A-Za-z0-9_ .\-\[\]]*ssid[A-Za-z0-9_ .\-\[\]]*?)(?P<sep>\s*[:=]\s*)\[(?P<value>[^\]\n]*)\]", re.IGNORECASE)


def _mac(m: re.Match) -> str:
    sep = m.group(2)
    return sep.join(["02", "00", "00", "00", "00", "01"])


def redact_text(text: str, secrets: list[str]) -> str:
    for s in sorted(set(x for x in secrets if x), key=len, reverse=True):
        text = text.replace(s, "redacted")
    text = SECRET_LABEL_RE.sub(lambda m: f"{m.group('label')}{m.group('sep')}[{'redacted' if m.group('value') else ''}]", text)
    text = SSID_LABEL_RE.sub(lambda m: m.group(0) if re.search(r"ignore|broadcast|hide|hidden|enable", m.group("label"), re.I)
                             else f"{m.group('label')}{m.group('sep')}[{'Router-5G' if m.group('value') else ''}]", text)
    text = MAC_RE.sub(_mac, text)
    text = LONG_DIGITS_RE.sub(lambda m: ("35" + "0" * (len(m.group(0)) - 3) + "1"), text)
    return text


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--secret", action="append", default=[], help="literal string to scrub (repeatable)")
    args = ap.parse_args(argv)
    n = 0
    for root, _dirs, files in os.walk(args.src):
        for name in files:
            if name.startswith("_"):
                continue
            sp = os.path.join(root, name)
            rel = os.path.relpath(sp, args.src)
            dp = os.path.join(args.dst, rel)
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            with open(sp, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            with open(dp, "w", encoding="utf-8", newline="\n") as f:
                f.write(redact_text(text, args.secret))
            n += 1
    print(f"redacted {n} files -> {args.dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
