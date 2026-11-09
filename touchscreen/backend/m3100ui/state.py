"""Persistent UI state under <state_dir>: settings, radio-setting snapshots, audit log.

All writes are atomic (temp file + rename).  Nothing secret is written here.
"""
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

SETTINGS_DEFAULTS: Dict[str, Any] = {
    "router_name": "5G Router",
    "expert_mode": False,
    "session_idle_s": 1800,
    "session_max_s": 43200,
    "theme": "system",
    "status_poll_s": 4,
}


class StateStore:
    def __init__(self, root: str):
        self.root = root
        self._lock = threading.RLock()
        os.makedirs(os.path.join(root, "snapshots"), exist_ok=True)

    # -- files ------------------------------------------------------------------------------
    def _path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def read_json(self, name: str, default: Any = None) -> Any:
        try:
            with open(self._path(name), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def write_json(self, name: str, data: Any) -> None:
        with self._lock:
            path = self._path(name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)

    # -- settings ---------------------------------------------------------------------------
    def settings(self) -> Dict[str, Any]:
        s = dict(SETTINGS_DEFAULTS)
        s.update(self.read_json("settings.json", {}) or {})
        return s

    def update_settings(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            s = self.settings()
            s.update(changes)
            self.write_json("settings.json", {k: s[k] for k in s})
            return s

    # -- snapshots (previous values of radio settings, for "restore previous") ---------------
    def snapshot(self, name: str, value: Any, keep: int = 10) -> None:
        with self._lock:
            hist = self.read_json(os.path.join("snapshots", name + ".json"), []) or []
            hist.append({"t": int(time.time()), "value": value})
            self.write_json(os.path.join("snapshots", name + ".json"), hist[-keep:])

    def snapshots(self, name: str) -> List[Dict[str, Any]]:
        return self.read_json(os.path.join("snapshots", name + ".json"), []) or []

    def last_snapshot(self, name: str) -> Optional[Any]:
        h = self.snapshots(name)
        return h[-1]["value"] if h else None

    # -- audit log --------------------------------------------------------------------------
    def audit(self, actor: str, action: str, details: Optional[Dict[str, Any]] = None) -> None:
        rec = {"t": int(time.time()), "actor": actor, "action": action}
        if details:
            rec["details"] = details
        line = json.dumps(rec, sort_keys=True)
        with self._lock:
            path = self._path("audit.log")
            try:
                if os.path.getsize(path) > 512 * 1024:
                    os.replace(path, path + ".1")
            except OSError:
                pass
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def audit_tail(self, n: int = 200) -> List[Dict[str, Any]]:
        try:
            with open(self._path("audit.log"), "r", encoding="utf-8") as f:
                lines = f.readlines()[-n:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out
