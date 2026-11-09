"""SMS: inbox / sent / outbox, read state, compose and send, delete (one, several, all), address book.

Sources:  REST SMSList/Inbox (GET; PUT read state), SMSList/Outbox (POST = send, sys_sendSMS), SMSStatus (unread)
          sms_cli get_list / read / get_unread / set_state / delete / send / ab_* (mailboxes PreInbox 0, Inbox 1,
          Outbox 2, Sentbox 3; the REST Sentbox/Outbox GETs fail on this firmware, so those come from sms_cli)
Message ids are the sms_api ids in both paths (libsysintclient sys_getSMSInbox = sms_api_get_list + sms_api_read).
"""
import calendar
import re
import time
from typing import Any, Dict, List, Optional

from .. import validate
from ..errors import ApiError, field_error, not_found, unavailable
from .sim import cli_get, cli_set, rest_get, to_int

BOXES = {"inbox": 1, "outbox": 2, "sent": 3}
PHONE_RE = r"^\+?[0-9]{2,20}$"
MAX_TEXT = 459            # 3 GSM-7 parts; also below sms_cli's 510-character line limit
GSM7_BASIC = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
                 "¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_EXT = set("^{}\\[~]|€\f")


def sms_encoding(text: str) -> Dict[str, Any]:
    """GSM 03.38 septet / UCS-2 accounting: encoding, units used, parts and characters left in the last part."""
    if all(c in GSM7_BASIC or c in GSM7_EXT for c in text):
        units = sum(2 if c in GSM7_EXT else 1 for c in text)
        single, multi, enc = 160, 153, "gsm7"
    else:
        units = sum(2 if ord(c) > 0xFFFF else 1 for c in text)
        single, multi, enc = 70, 67, "ucs2"
    parts = 1 if units <= single else -(-units // multi)
    per = single if parts == 1 else multi
    return {"encoding": enc, "units": units, "parts": parts, "left": per * parts - units,
            "ascii": all(32 <= ord(c) < 127 or c == "\n" for c in text)}


def parse_cli_time(s: str) -> Optional[int]:
    """sms_cli prints ctime() text ('Thu Jan  1 00:00:00 1970') for smsc/utc/local."""
    s = " ".join((s or "").split())
    if not s:
        return None
    try:
        return int(calendar.timegm(time.strptime(s, "%a %b %d %H:%M:%S %Y")))
    except ValueError:
        return None


def parse_rest_time(v: Any) -> Optional[int]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v) if v > 0 else None
    s = str(v or "").strip()
    if s.isdigit():
        return int(s) or None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%y %I:%M:%S %p",
                "%a %b %d %H:%M:%S %Y"):
        try:
            return int(calendar.timegm(time.strptime(" ".join(s.split()), fmt)))
        except ValueError:
            continue
    return None


def parse_list(text: str) -> List[Dict[str, Any]]:
    """sms_cli get_list: count:[n], list[i].id:[n], list[i].state:[Read|Unread], list[i].storage:[n]."""
    rows: Dict[int, Dict[str, Any]] = {}
    for m in re.finditer(r"list\[(\d+)\]\.(id|state|storage):\[([^\]]*)\]", text or ""):
        rows.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
    out = []
    for i in sorted(rows):
        r = rows[i]
        if "id" in r:
            out.append({"id": to_int(r["id"], 0), "unread": str(r.get("state", "")).lower() == "unread",
                        "storage": to_int(r.get("storage"))})
    return out


def parse_read(res) -> Dict[str, Any]:
    utc = res.get("utc") or ""
    return {"address": res.get("address") or "", "text": res.get("body") or "", "time": parse_cli_time(utc),
            "time_text": res.get("local") or utc, "smsc_time": parse_cli_time(res.get("smsc") or "")}


def register(app):
    r = app.router

    def refresh():
        app.cache.invalidate("sms:")
        app.cache.invalidate("status")

    def contacts_raw() -> List[Dict[str, Any]]:
        res = cli_get(app, "sms_cli", "ab_get_list")
        if res is None or not res.ok:
            return []
        ids = [to_int(m.group(1), 0) for m in re.finditer(r"list\[\d+\]\.id:\[(-?\d+)\]", res.text)]
        out = []
        for cid in ids[:100]:
            e = cli_get(app, "sms_cli", "ab_get_entry", [str(cid)])
            if e is not None and e.ok:
                out.append({"id": cid, "name": e.get("name") or "", "address": e.get("address") or ""})
        return out

    def contacts() -> List[Dict[str, Any]]:
        return app.cache.get("sms:contacts", 30, contacts_raw)

    def name_for(address: str, book: List[Dict[str, Any]]) -> str:
        digits = re.sub(r"\D", "", address or "")
        for c in book:
            cd = re.sub(r"\D", "", c["address"])
            if cd and digits and (cd == digits or (len(cd) >= 7 and digits.endswith(cd[-10:]))):
                return c["name"]
        return ""

    def from_rest(box: str) -> Optional[List[Dict[str, Any]]]:
        if box != "inbox":
            return None
        try:
            data = app.rest.get("SMSList/Inbox")
        except ApiError:
            return None
        if isinstance(data, dict):
            data = data.get("SMSList") or data.get("SMS") or data.get("Messages") or []
        if not isinstance(data, list):
            return None
        out = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("SMS"), dict):
                item = item["SMS"]
            if not isinstance(item, dict) or "SMSMessageIdentifier" not in item:
                continue
            ts = item.get("SMSMessageTimestamp")
            out.append({"id": to_int(item.get("SMSMessageIdentifier"), 0), "box": "inbox",
                        "unread": bool(item.get("SMSMessageUnreadState")), "address": str(item.get("SMSMessageAddress") or ""),
                        "contact": str(item.get("SMSMessageContactName") or ""), "text": str(item.get("SMSMessageBody") or ""),
                        "time": parse_rest_time(ts), "time_text": "" if ts is None else str(ts), "storage": None})
        return out

    def from_cli(box: str) -> List[Dict[str, Any]]:
        res = cli_get(app, "sms_cli", "get_list", [str(BOXES[box])], timeout=10)
        if res is None:
            raise unavailable("The SMS service did not answer")
        if not res.ok:
            raise unavailable("Messages are not available: %s" % (res.message or "SMS service error"))
        out = []
        for row in parse_list(res.text)[:200]:
            rd = cli_get(app, "sms_cli", "read", [str(BOXES[box]), str(row["id"])])
            msg = {"id": row["id"], "box": box, "unread": row["unread"], "storage": row["storage"], "contact": "",
                   "address": "", "text": "", "time": None, "time_text": ""}
            if rd is not None and rd.ok:
                msg.update(parse_read(rd))
            out.append(msg)
        return out

    def messages(box: str) -> List[Dict[str, Any]]:
        def load():
            rows = from_rest(box)
            src = "rest"
            if rows is None:
                rows, src = from_cli(box), "cli"
            book = contacts()
            for m in rows:
                if not m.get("contact"):
                    m["contact"] = name_for(m["address"], book)
                m["source"] = src
            rows.sort(key=lambda m: (m.get("time") or 0, m["id"]), reverse=True)
            return rows
        return app.cache.get("sms:box:" + box, 10, load)

    def unread() -> Optional[int]:
        st = rest_get(app, "SMSStatus")
        if isinstance(st, dict) and "SMSInboxUnreadCount" in st:
            return to_int(st.get("SMSInboxUnreadCount"), 0)
        res = cli_get(app, "sms_cli", "get_unread")
        return res.get_int("count") if res is not None and res.ok else None

    def available() -> bool:
        if isinstance(rest_get(app, "SMSStatus"), dict):
            return True
        res = cli_get(app, "sms_cli", "is_running")
        return bool(res is not None and res.ok)

    app.features.add("sms", available)

    @r.route("GET", "/api/sms/status")
    def get_status(req):
        """Unread count and whether the SMS service answers."""
        return {"available": available(), "unread": unread(), "max_text": MAX_TEXT}

    @r.route("GET", "/api/sms/messages")
    def get_messages(req):
        """Messages of one box (inbox, sent or outbox), newest first."""
        box = validate.choice("box", req.query.get("box", "inbox"), list(BOXES))
        rows = messages(box)
        return {"box": box, "count": len(rows), "unread": sum(1 for m in rows if m["unread"]), "messages": rows}

    def find(box: str, mid: int) -> Dict[str, Any]:
        for m in messages(box):
            if m["id"] == mid:
                return m
        raise not_found("No message %d in the %s" % (mid, box))

    def set_read(box: str, mid: int, read: bool) -> None:
        if box == "inbox":
            try:
                app.rest.put("SMSList/Inbox", {"SMSMessageIdentifier": mid, "SMSMessageUnreadState": not read})
                return
            except ApiError as e:
                if e.code not in ("unsupported", "disabled", "vendor_error"):
                    raise
        cli_set(app, "sms_cli", "set_state", [str(BOXES[box]), str(mid), "1" if read else "0"], "Marking the message")

    @r.route("POST", "/api/sms/messages/{box}/{mid}/read")
    def mark(req):
        """Mark a message read (or unread with {"read": false})."""
        box = validate.choice("box", req.params["box"], list(BOXES))
        mid = validate.integer("id", req.params["mid"], 0, 65535)
        data = validate.only_keys(req.json, ["read"])
        read = validate.boolean("read", data.get("read", True))
        msg = find(box, mid)
        if msg["unread"] == (not read):
            return msg
        set_read(box, mid, read)
        refresh()
        return find(box, mid)

    def delete_ids(box: str, ids: List[int]) -> int:
        n = 0
        for mid in ids:
            cli_set(app, "sms_cli", "delete", [str(BOXES[box]), str(mid)], "Deleting message %d" % mid)
            n += 1
        return n

    @r.route("DELETE", "/api/sms/messages/{box}/{mid}")
    def delete_one(req):
        """Delete one message."""
        box = validate.choice("box", req.params["box"], list(BOXES))
        mid = validate.integer("id", req.params["mid"], 0, 65535)
        find(box, mid)
        delete_ids(box, [mid])
        app.audit(req, "sms.delete", {"box": box, "count": 1})
        refresh()
        return {"deleted": 1}

    @r.route("POST", "/api/sms/messages/delete")
    def delete_many(req):
        """Delete several messages ({"box", "ids": [...]}) or all of a box ({"box", "all": true})."""
        data = validate.only_keys(req.json, ["box", "ids", "all"])
        box = validate.choice("box", data.get("box", "inbox"), list(BOXES))
        have = [m["id"] for m in messages(box)]
        if validate.boolean("all", data.get("all", False)):
            ids = have
        else:
            ids = validate.int_list("ids", data.get("ids", []), min_items=1)
            missing = [i for i in ids if i not in have]
            if missing:
                raise field_error("ids", "No such message: %s" % ", ".join(str(i) for i in missing))
        n = 0
        try:
            n = delete_ids(box, ids)
        finally:
            app.audit(req, "sms.delete", {"box": box, "count": n})
            refresh()
        return {"deleted": n}

    @r.route("POST", "/api/sms/send")
    def send(req):
        """Send a text message (plain text; long texts are sent in several parts)."""
        data = validate.only_keys(req.json, ["to", "text"])
        to = validate.text("to", str(data.get("to", "")).replace(" ", "").replace("-", ""), 2, 21, pattern=PHONE_RE)
        txt = data.get("text", "")
        if not isinstance(txt, str) or not txt.strip():
            raise field_error("text", "Enter a message")
        txt = txt.replace("\r\n", "\n").replace("\r", "\n")
        if len(txt) > MAX_TEXT:
            raise field_error("text", "At most %d characters" % MAX_TEXT)
        enc = sms_encoding(txt)
        if not enc["ascii"]:
            raise field_error("text", "Only plain text (letters, digits and common punctuation) can be sent by this router")
        via = "rest"
        try:
            app.rest.post("SMSList/Outbox", {"SMSMessageAddress": to, "SMSMessageBody": txt})
        except ApiError as e:
            if e.code not in ("unsupported", "disabled"):
                raise
            if "\n" in txt or "\t" in txt:
                raise field_error("text", "Line breaks cannot be sent on this firmware; use spaces instead")
            via = "cli"
            cli_set(app, "sms_cli", "send", [to, txt], "Sending the message", timeout=30)
        app.audit(req, "sms.send", {"parts": enc["parts"], "via": via})
        refresh()
        return {"sent": True, "via": via, "parts": enc["parts"], "encoding": enc["encoding"]}

    @r.route("POST", "/api/sms/encode")
    def encode(req):
        """Character accounting for a draft (encoding, parts, characters left)."""
        data = validate.only_keys(req.json, ["text"])
        txt = data.get("text", "")
        if not isinstance(txt, str):
            raise field_error("text", "Must be text")
        return dict(sms_encoding(txt[:2000]), max_text=MAX_TEXT)

    # --------------------------------------------------------------------- address book
    def contact_values(data: Dict[str, Any], cur: Optional[Dict[str, Any]] = None) -> List[str]:
        cur = cur or {}
        name = validate.text("name", data.get("name", cur.get("name", "")), 1, 40).strip()
        if not name:
            raise field_error("name", "Enter a name")
        if any(ord(c) > 126 for c in name):
            raise field_error("name", "Use plain (ASCII) characters only")
        addr = validate.text("address", str(data.get("address", cur.get("address", ""))).replace(" ", "").replace("-", ""),
                             2, 21, pattern=PHONE_RE)
        return [name, addr]

    @r.route("GET", "/api/sms/contacts")
    def get_contacts(req):
        """Address book entries."""
        return {"contacts": contacts(), "max": 100}

    @r.route("POST", "/api/sms/contacts")
    def add_contact(req):
        """Add an address book entry."""
        data = validate.only_keys(req.json, ["name", "address"])
        vals = contact_values(data)
        if len(contacts()) >= 100:
            raise ApiError(409, "conflict", "The address book is full")
        cli_set(app, "sms_cli", "ab_add_entry", vals, "Adding the contact")
        app.audit(req, "sms.contact.add")
        refresh()
        return {"contacts": contacts(), "max": 100}

    def find_contact(cid: int) -> Dict[str, Any]:
        for c in contacts():
            if c["id"] == cid:
                return c
        raise not_found("No contact %d" % cid)

    @r.route("PUT", "/api/sms/contacts/{cid}")
    def edit_contact(req):
        """Change an address book entry."""
        cid = validate.integer("id", req.params["cid"], 0, 100)
        data = validate.only_keys(req.json, ["name", "address", "id"])
        cur = find_contact(cid)
        cli_set(app, "sms_cli", "ab_edit_entry", [str(cid)] + contact_values(data, cur), "Saving the contact")
        app.audit(req, "sms.contact.edit")
        refresh()
        return {"contacts": contacts(), "max": 100}

    @r.route("DELETE", "/api/sms/contacts/{cid}")
    def del_contact(req):
        """Delete an address book entry."""
        cid = validate.integer("id", req.params["cid"], 0, 100)
        find_contact(cid)
        cli_set(app, "sms_cli", "ab_del_entry", [str(cid)], "Deleting the contact")
        app.audit(req, "sms.contact.delete")
        refresh()
        return {"contacts": contacts(), "max": 100}
