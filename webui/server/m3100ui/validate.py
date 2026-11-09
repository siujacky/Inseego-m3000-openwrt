"""Input validation helpers shared by services.  All raise errors.field_error(field, msg)."""
import ipaddress
import re
from typing import Any, Iterable, List, Optional, Sequence

from .errors import field_error

MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")


def boolean(field: str, v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v in (0, 1, "0", "1", "true", "false"):
        return v in (1, "1", "true")
    raise field_error(field, "Must be true or false")


def integer(field: str, v: Any, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    try:
        if isinstance(v, bool):
            raise ValueError
        n = int(v)
        if isinstance(v, float) and n != v:
            raise ValueError
    except (TypeError, ValueError):
        raise field_error(field, "Must be a whole number")
    if lo is not None and n < lo or hi is not None and n > hi:
        raise field_error(field, "Must be between %s and %s" % (lo, hi))
    return n


def choice(field: str, v: Any, options: Iterable[Any]) -> Any:
    opts = list(options)
    if v not in opts:
        raise field_error(field, "Must be one of: %s" % ", ".join(str(o) for o in opts))
    return v


def text(field: str, v: Any, min_len: int = 0, max_len: int = 255, pattern: Optional[str] = None,
         no_spaces: bool = False, printable: bool = True) -> str:
    if not isinstance(v, str):
        raise field_error(field, "Must be text")
    if len(v) < min_len or len(v) > max_len:
        raise field_error(field, "Must be %d–%d characters" % (min_len, max_len))
    if any(c in v for c in "\r\n\x00"):
        raise field_error(field, "Line breaks are not allowed")
    if printable and any(ord(c) < 32 for c in v):
        raise field_error(field, "Control characters are not allowed")
    if no_spaces and " " in v:
        raise field_error(field, "Spaces are not allowed here")
    if pattern and not re.match(pattern, v):
        raise field_error(field, "Invalid format")
    return v


def ipv4(field: str, v: Any, allow_empty: bool = False) -> str:
    if allow_empty and v in ("", None):
        return ""
    try:
        return str(ipaddress.IPv4Address(str(v).strip()))
    except ValueError:
        raise field_error(field, "Enter a valid IPv4 address")


def ipv6(field: str, v: Any, allow_empty: bool = False) -> str:
    if allow_empty and v in ("", None):
        return ""
    try:
        return str(ipaddress.IPv6Address(str(v).strip()))
    except ValueError:
        raise field_error(field, "Enter a valid IPv6 address")


def ip_any(field: str, v: Any, allow_empty: bool = False) -> str:
    if allow_empty and v in ("", None):
        return ""
    try:
        return str(ipaddress.ip_address(str(v).strip()))
    except ValueError:
        raise field_error(field, "Enter a valid IP address")


def netmask(field: str, v: Any) -> str:
    try:
        n = ipaddress.IPv4Network("0.0.0.0/" + str(v).strip())
        return str(n.netmask)
    except ValueError:
        raise field_error(field, "Enter a valid subnet mask")


def mac(field: str, v: Any) -> str:
    if not isinstance(v, str) or not MAC_RE.match(v.strip()):
        raise field_error(field, "Enter a MAC address like 00:11:22:33:44:55")
    return v.strip().replace("-", ":").upper()


def port(field: str, v: Any) -> int:
    return integer(field, v, 1, 65535)


def port_range(field: str, start: Any, end: Any) -> List[int]:
    a, b = port(field, start), port(field, end)
    if b < a:
        raise field_error(field, "End port must not be lower than start port")
    return [a, b]


def int_list(field: str, v: Any, allowed: Optional[Sequence[int]] = None, min_items: int = 0) -> List[int]:
    if not isinstance(v, list):
        raise field_error(field, "Must be a list")
    out = []
    for x in v:
        n = integer(field, x)
        if allowed is not None and n not in allowed:
            raise field_error(field, "%s is not supported by this device" % n)
        if n not in out:
            out.append(n)
    if len(out) < min_items:
        raise field_error(field, "Select at least %d" % min_items)
    return out


def only_keys(data: Any, allowed: Iterable[str]) -> dict:
    if not isinstance(data, dict):
        raise field_error("_", "Expected an object")
    allowed = set(allowed)
    extra = [k for k in data if k not in allowed and k not in ("confirm", "password")]
    if extra:
        raise field_error(extra[0], "Unknown field")
    return {k: v for k, v in data.items() if k in allowed}
