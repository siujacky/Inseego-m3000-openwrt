"""Vendor CLI adapter: run `/opt/nvtl/bin/<cli> <command>` and parse its output.

Facts this module is built on (see docs/recon and fixtures/cli):
- Parameters are read from stdin prompts, one answer per line.  On EOF the CLIs
  pass *uninitialised* values to the vendor API, and menu-style CLIs loop forever
  (usb_serial_cli wrote 247 MB to /tmp in 6 s).  Therefore:
    * every call has a timeout and an output cap, and is killed otherwise;
    * answers may not contain CR/LF/NUL (that would shift later answers);
    * setters need a Spec in adapters/specs/ (fail closed); the answer count is
      checked against it, and "static"-only specs are refused on a real unit unless
      allow_static_specs is set.
- Output: one or more status lines `<fn> returned <rc> (<MODULE>: <msg>)`
  plus `label:[value]` style lines whose shape differs per CLI.
"""
import re
import subprocess
import threading
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..errors import ApiError, vendor_error
from . import cli_specs

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
STATUS_RE = re.compile(
    r"(?P<fn>[A-Za-z0-9_]+) returned (?P<rc>-?0x[0-9A-Fa-f]+|-?\d+) \((?P<module>[^:()]+):\s*(?P<msg>[^)]*)\)"
)
# sysintcli prints "sys_getX returned 0" (no module/message) and always exits 0
STATUS_BARE_RE = re.compile(r"^\s*(?P<fn>sys_[A-Za-z0-9_]+) returned (?P<rc>-?0x[0-9A-Fa-f]+|-?\d+)\s*$", re.M)
STATUS_PLAIN_RE = re.compile(r"\breturned success\b")
MAX_ANSWER_LEN = 1024


def norm_label(label: str) -> str:
    """Normalise a label for lookup: lowercase, single spaces, no trailing ':'/'='."""
    label = re.sub(r"\s+", " ", label.strip())
    label = label.strip(" :=\t-")
    return label.lower()


class CliResult:
    """Parsed output of one CLI invocation."""

    def __init__(self, cli: str, command: str, text: str, exit_code: Optional[int],
                 timed_out: bool = False, truncated: bool = False):
        self.cli = cli
        self.command = command
        self.text = text
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.truncated = truncated
        self.fn, self.rc, self.module, self.message = parse_status(text)
        self.pairs: List[Tuple[str, str, str]] = parse_pairs(text)  # (norm_label, value, note)

    @property
    def ok(self) -> bool:
        if self.timed_out:
            return False
        if self.rc is not None:
            return self.rc == 0
        if STATUS_PLAIN_RE.search(self.text):
            return True
        if re.search(r"(?im)^\s*(failed|error)\b|\bfailed to\b", self.text) and not self.pairs:
            return False
        return self.exit_code == 0

    def get(self, label: str, default: Optional[str] = None) -> Optional[str]:
        """First value for a label (normalised match)."""
        key = norm_label(label)
        for k, v, _n in self.pairs:
            if k == key:
                return v
        return default

    def note(self, label: str) -> Optional[str]:
        key = norm_label(label)
        for k, _v, n in self.pairs:
            if k == key:
                return n or None
        return None

    def all(self, label: str) -> List[str]:
        """Every value recorded under a label, in order (repeated labels, continuations)."""
        key = norm_label(label)
        return [v for k, v, _n in self.pairs if k == key]

    def get_int(self, label: str, default: Optional[int] = None) -> Optional[int]:
        v = self.get(label)
        try:
            return int(str(v).strip(), 0) if v not in (None, "") else default
        except ValueError:
            return default

    def indexed(self, base: str) -> List[str]:
        """Values of `base[0]`, `base[1]`, … in index order."""
        b = norm_label(base)
        out: Dict[int, str] = {}
        rx = re.compile(re.escape(b) + r"\[(\d+)\]$")
        for k, v, _n in self.pairs:
            m = rx.match(k)
            if m:
                out.setdefault(int(m.group(1)), v)
        return [out[i] for i in sorted(out)]

    def mapping(self) -> Dict[str, str]:
        d: Dict[str, str] = {}
        for k, v, _n in self.pairs:
            d.setdefault(k, v)
        return d

    def to_json(self) -> dict:
        return {"cli": self.cli, "command": self.command, "ok": self.ok, "rc": self.rc,
                "module": self.module, "message": self.message, "timed_out": self.timed_out,
                "truncated": self.truncated, "text": self.text}

    def raise_for_error(self, what: str = "") -> "CliResult":
        if not self.ok:
            raise vendor_error(
                (what or "%s %s" % (self.cli, self.command)) + " failed: " +
                (self.message or ("timed out" if self.timed_out else "no status")),
                {"cli": self.cli, "command": self.command, "rc": self.rc, "module": self.module},
            )
        return self


def parse_status(text: str):
    last = None
    for m in STATUS_RE.finditer(text):
        last = m
    if last:
        return (last.group("fn"), int(last.group("rc"), 0), last.group("module").strip(),
                last.group("msg").strip().rstrip("."))
    bare = None
    for m in STATUS_BARE_RE.finditer(text):
        bare = m
    if bare:
        rc = int(bare.group("rc"), 0)
        return bare.group("fn"), rc, "SYSINT", "success" if rc == 0 else "error %d" % rc
    return None, None, None, None


def _scan_brackets(line: str):
    """Yield (start, end, inner) for each top-level [...] group (nested brackets kept)."""
    depth, start = 0, -1
    for i, ch in enumerate(line):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
            if depth == 0:
                yield start, i + 1, line[start + 1:i]


_PUNCT_ONLY = re.compile(r"^[\s\-,~/to]*$")
_PLAIN_KV = re.compile(r"^\s*([^:\[\]]{1,80}?)\s*:\s*(\S.*?)\s*$")


def parse_pairs(text: str) -> List[Tuple[str, str, str]]:
    """Extract (label, value, note) triples from vendor CLI output.

    Handles `label:[v]`, `label : [v]`, `label [v]`, `label[v]`, list items `band[3]: [26]`,
    `features[0].feature_name:[X] features[0].feature_enabled:[0]` (several per line),
    nested values `[THN-1.54 1 [2024-…]]`, continuations `range: [a] - [b]`
    (both values under the same label), trailing notes `[50] ( UMTS LTE NR5G)` / `[120] min`,
    and bracket-less `count : 0`.  Status lines and prompt lines are skipped.
    """
    out: List[Tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or STATUS_RE.search(line) or STATUS_BARE_RE.match(line):
            continue
        groups = list(_scan_brackets(line))
        if not groups:
            m = _PLAIN_KV.match(line)
            if m and "enter " not in m.group(1).lower():
                out.append((norm_label(m.group(1)), m.group(2), ""))
            continue
        pos = 0
        pending_label = ""
        prev_label: Optional[str] = None
        idx = 0
        while idx < len(groups):
            s, e, inner = groups[idx]
            seg = line[pos:s]
            after = line[e:e + 1]
            preceded_by_word = s > 0 and (line[s - 1].isalnum() or line[s - 1] == "_")
            if preceded_by_word and after not in ("", " ", "\t") and after not in ("(",):
                # an index such as band[3] or features[0].name — part of the label
                pending_label += seg + "[" + inner + "]"
                pos = e
                idx += 1
                continue
            label_text = pending_label + seg
            pending_label = ""
            if _PUNCT_ONLY.match(label_text) and prev_label is not None:
                label = prev_label
            else:
                label = norm_label(label_text)
            if label.startswith("enter ") or "?" in label:
                # an input prompt echoed into the output
                pos = e
                idx += 1
                continue
            # note = trailing text up to the next group (only for the last group, or parenthesised)
            nxt = groups[idx + 1][0] if idx + 1 < len(groups) else len(line)
            tail = line[e:nxt]
            note = ""
            if idx + 1 == len(groups):
                note = tail.strip()
            elif tail.strip().startswith("("):
                note = tail.strip()
            out.append((label, inner.strip(), note))
            prev_label = label
            pos = e if not note or idx + 1 == len(groups) else nxt
            idx += 1
    return out


class Transport:
    """How a CLI process is started.  `argv` excludes the cli path prefix."""

    def popen(self, path: str, args: Sequence[str]) -> subprocess.Popen:
        raise NotImplementedError


class LocalTransport(Transport):
    def __init__(self, env: Optional[Dict[str, str]] = None):
        import os
        self.env = dict(os.environ)
        self.env["LD_LIBRARY_PATH"] = "/opt/nvtl/lib:/opt/nvtl/data/branding/lib"
        self.env["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin:/opt/nvtl/bin"
        if env:
            self.env.update(env)

    def popen(self, path, args):
        return subprocess.Popen([path] + list(args), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=self.env, close_fds=True)


class AdbTransport(Transport):
    """PC development against a real unit over `adb shell` (no PTY, stdin forwarded)."""

    def __init__(self, serial: str = "", adb: str = "adb"):
        self.serial = serial
        self.adb = adb

    def popen(self, path, args):
        import shlex  # adb shell joins argv into one device-side shell command: quote every value
        cmd = [self.adb] + (["-s", self.serial] if self.serial else []) + [
            "shell", "-T", "LD_LIBRARY_PATH=/opt/nvtl/lib:/opt/nvtl/data/branding/lib", shlex.quote(path)] + \
            [shlex.quote(str(a)) for a in args]
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


class CliRunner:
    """Runs vendor CLIs safely.  One lock per CLI: the vendor daemons are not re-entrant-friendly."""

    def __init__(self, cli_dir: str = "/opt/nvtl/bin", timeout: float = 8.0, max_bytes: int = 256 * 1024,
                 transport: Optional[Transport] = None, allow_unverified_setters: bool = False,
                 allow_static_specs: bool = False):
        self.cli_dir = cli_dir.rstrip("/")
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.transport = transport or LocalTransport()
        self.allow_unverified_setters = allow_unverified_setters
        self.allow_static_specs = allow_static_specs
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock(self, cli: str) -> threading.Lock:
        with self._locks_guard:
            if cli not in self._locks:
                self._locks[cli] = threading.Lock()
            return self._locks[cli]

    @staticmethod
    def check_answers(answers: Iterable) -> List[str]:
        out = []
        for a in answers:
            s = str(a)
            if any(c in s for c in ("\n", "\r", "\x00")):
                raise ApiError(400, "invalid_value", "Value may not contain line breaks")
            if len(s) > MAX_ANSWER_LEN:
                raise ApiError(400, "invalid_value", "Value too long")
            out.append(s)
        return out

    def run(self, cli: str, command: str, answers: Iterable = (), timeout: Optional[float] = None,
            check: bool = False, what: str = "", args: Sequence[str] = ()) -> CliResult:
        """Run `<cli> <command> [args…]`, feeding `answers` to its stdin prompts.

        `args` (extra argv) is only accepted for the argv-style tools whitelisted in
        cli_specs.ARGV_RULES (e.g. `nwcli qmi_idl read_file …`, `nwnvitem -r -e …`).
        """
        if not NAME_RE.match(cli) or (not cli.endswith("cli") and cli not in cli_specs.EXTRA_TOOLS):
            raise ValueError("bad cli name %r" % cli)
        if command and not NAME_RE.match(command.lstrip("-")):
            raise ValueError("bad command name %r" % command)
        if args:
            cli_specs.check_argv(cli, command, list(args))
        kind = cli_specs.classify(cli, command, list(args))
        if kind == "excluded":
            raise ApiError(403, "excluded", "%s %s is deliberately not exposed" % (cli, command))
        ans = self.check_answers(answers)
        if kind == "setter":
            sp = cli_specs.spec(cli, command)
            if sp is None:
                if not self.allow_unverified_setters:
                    raise ApiError(403, "unverified_setter",
                                   "Setter %s %s has no prompt specification" % (cli, command))
            else:
                if sp.source == "static" and not self.allow_static_specs:
                    raise ApiError(403, "unverified_setter",
                                   "Setter %s %s is only statically analysed; verify it on the device "
                                   "(tools/device/verify_specs.py) before use" % (cli, command))
                sp.check(ans)
                if sp.mode == "argv":
                    args, ans = list(ans), []
        limit = timeout or self.timeout
        with self._lock(cli):
            text, exit_code, timed_out, truncated = self._execute(cli, command, list(args), ans, limit)
        res = CliResult(cli, command, text, exit_code, timed_out=timed_out, truncated=truncated)
        if check:
            res.raise_for_error(what)
        return res

    def _execute(self, cli: str, command: str, args: List[str], answers: List[str], limit: float):
        """Start the process, feed answers, collect capped output.  Overridden by the mock."""
        data = ("\n".join(answers) + "\n").encode("utf-8") if answers else b""
        p = self.transport.popen(self.cli_dir + "/" + cli, ([command] if command else []) + list(args))
        fired = threading.Event()

        def on_timeout():
            fired.set()
            self._kill(p)

        timer = threading.Timer(limit, on_timeout)
        timer.daemon = True
        timer.start()
        truncated = False
        chunks: List[bytes] = []
        size = 0
        try:
            try:
                if data:
                    p.stdin.write(data)
                p.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            while True:
                chunk = p.stdout.read(8192)
                if not chunk:
                    break
                size += len(chunk)
                if size > self.max_bytes:
                    truncated = True
                    chunks.append(chunk[: max(0, self.max_bytes - (size - len(chunk)))])
                    self._kill(p)
                    break
                chunks.append(chunk)
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._kill(p)
                p.wait(timeout=2)
        finally:
            timer.cancel()
            try:
                p.stdout.close()
            except OSError:
                pass
        return b"".join(chunks).decode("utf-8", "replace"), p.returncode, fired.is_set(), truncated

    @staticmethod
    def _kill(p: subprocess.Popen) -> None:
        try:
            if p.poll() is None:
                p.kill()
        except OSError:
            pass
