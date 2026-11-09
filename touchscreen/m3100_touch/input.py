"""Bounded ARM32 evdev decoder and release-only gestures. No device grabs/logs."""
import os
import struct

try:
    from fcntl import ioctl as _ioctl
except ImportError:  # Host tests run on Windows; runtime is Linux.
    def _ioctl(*args):
        raise OSError('evdev ioctls require Linux')

EVENT = struct.Struct('<IIHHi')
MAX_SLOTS = 64


def query_axes(fd):
    """Read actual MT axes, falling back to single-touch ABS axes."""
    for codes in ((53, 54), (0, 1)):
        try:
            ranges = []
            for code in codes:
                buf = bytearray(24)
                _ioctl(fd, 0x80184540 + code, buf, True)
                _, low, high, _, _, _ = struct.unpack('iiiiii', buf)
                if high <= low:
                    raise OSError('invalid evdev axis range')
                ranges.append((low, high))
            return tuple(ranges)
        except OSError:
            pass
    raise OSError('no usable touch axes')


def query_contacts(fd):
    """Return contact state; never infer contact from generic ABS_TRACKING_ID.

    EVIOCGMTSLOTS requests actual per-slot IDs. BTN_TOUCH is a second signal.
    None means unknown, and startup remains disarmed until an idle frame.
    """
    states = []
    try:
        info = bytearray(24)
        _ioctl(fd, 0x8018456F, info, True)  # EVIOCGABS(ABS_MT_SLOT)
        _, minimum, maximum, _, _, _ = struct.unpack('6i', info)
        count = maximum + 1
        if minimum != 0 or not 1 <= count <= MAX_SLOTS:
            states.append(True)  # Corrupt/unbounded slot range cannot prove idle.
        else:
            buf = bytearray(struct.pack('%di' % (count + 1), 57, *([-1] * count)))
            _ioctl(fd, 0x8000450A | (len(buf) << 16), buf, True)
            states.append(any(i >= 0 for i in struct.unpack('%di' % (count + 1), buf)[1:]))
    except OSError:
        pass
    try:
        buf = bytearray(96)
        _ioctl(fd, 0x80604518, buf, True)
        states.append(bool(buf[330 // 8] & (1 << (330 % 8))))
    except OSError:
        pass
    return any(states) if states else None


def query_current_slot(fd):
    """Restore the kernel's slot cursor; slot selection events can be omitted."""
    try:
        info = bytearray(24)
        _ioctl(fd, 0x8018456F, info, True)
        current, minimum, maximum, _, _, _ = struct.unpack('6i', info)
        if minimum == 0 and 0 <= current <= maximum < MAX_SLOTS:
            return current
    except OSError:
        pass
    return 0


class EventDecoder:
    def __init__(self, x_range=(0, 319), y_range=(0, 239)):
        if x_range[1] <= x_range[0] or y_range[1] <= y_range[0]:
            raise ValueError('invalid axis range')
        self.x_range, self.y_range = x_range, y_range
        self.buffer = bytearray()
        self.slot = 0
        self.slots = {}
        self.mt_seen = False
        self.single = {'x': None, 'y': None, 'down': False}
        self.armed = False
        self.contact = None
        self.blocked = False
        self.dropped = False
        self.drop_boundary_seen = False
        self.released = set()
        self.frame_invalid = False

    def resynchronize(self, active):
        """A reader ioctl snapshot may establish idle; active/unknown stays blocked."""
        self.contact = None
        self.slots.clear()
        self.released.clear()
        self.frame_invalid = False
        self.single['down'] = False
        self.armed = active is False
        self.blocked = active is not False
        self.dropped = active is not False
        # Snapshot quarantine has no unconsumed SYN_DROPPED boundary. A real
        # SYN_DROPPED below always resets this until its following SYN_REPORT.
        self.drop_boundary_seen = active is not False

    def cancel(self, now, reason='device-lost'):
        self.contact = None
        self.armed = False
        self.blocked = True
        self.buffer.clear()
        return [{'kind': 'cancel', 'time': now, 'reason': reason}]

    def _xy(self, x, y):
        def scale(v, limits, maximum):
            return min(maximum, max(0, round((v - limits[0]) * maximum / (limits[1] - limits[0]))))
        return scale(x, self.x_range, 319), scale(y, self.y_range, 239)

    def _frame(self, now):
        released = self.released
        self.released = set()
        invalid = self.frame_invalid
        self.frame_invalid = False
        if self.dropped:
            return []
        active = [(i, s) for i, s in self.slots.items() if s['id'] >= 0] if self.mt_seen else ([('single', self.single)] if self.single['down'] else [])
        if invalid:
            had_contact = self.contact is not None
            self.contact = None
            self.blocked = bool(active)
            if not active:
                self.armed = True
            return [dict(kind='cancel', time=now, reason='contact-replaced')] if had_contact else []
        if len(active) > 1:
            had_contact = self.contact is not None
            self.contact = None
            self.blocked = True
            return [{'kind': 'cancel', 'time': now, 'reason': 'multitouch'}] if had_contact else []
        if not active:
            old = self.contact
            self.contact = None
            self.armed = True
            self.blocked = False
            if old:
                state = self.slots.get(old[0][0]) if self.mt_seen else self.single
                x, y = self._xy(state['x'], state['y']) if state and state['x'] is not None and state['y'] is not None else old[1:]
                return [dict(kind='up', x=x, y=y, time=now)]
            return []
        if not self.armed or self.blocked:
            return []
        identity, state = active[0]
        if state['x'] is None or state['y'] is None:
            return []
        identity = (identity, state.get('id'))
        x, y = self._xy(state['x'], state['y'])
        old = self.contact
        if old and (old[0] != identity or identity[0] in released):
            self.contact = None
            self.blocked = True
            return [dict(kind='cancel', time=now, reason='contact-replaced')]
        self.contact = (identity, x, y)
        if old is None:
            return [dict(kind='down', x=x, y=y, time=now)]
        return [dict(kind='move', x=x, y=y, time=now)] if (x, y) != old[1:] else []

    def feed(self, data, now):
        if len(data) + len(self.buffer) > 65536:
            self.dropped = True
            self.drop_boundary_seen = False
            return self.cancel(now, 'overflow')
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= EVENT.size:
            _, _, kind, code, value = EVENT.unpack_from(self.buffer)
            del self.buffer[:EVENT.size]
            if kind == 0 and code == 3:
                self.contact = None
                self.armed = False
                self.blocked = True
                self.dropped = True
                self.drop_boundary_seen = False
                result.append(dict(kind='cancel', time=now, reason='syn-dropped'))
            elif kind == 0 and code == 0:
                if self.dropped:
                    self.drop_boundary_seen = True
                else:
                    result.extend(self._frame(now))
            elif self.dropped:
                continue
            elif kind == 3 and code == 47:
                if not 0 <= value < MAX_SLOTS:
                    self.blocked = True
                    self.contact = None
                    result.append(dict(kind='cancel', time=now, reason='slot-range'))
                else:
                    self.slot = value
            elif kind == 3 and code in (57, 53, 54):
                self.mt_seen = True
                state = self.slots.setdefault(self.slot, dict(id=-1, x=None, y=None))
                if code == 57:
                    if value >= 0 and (self.slot in self.released or state['id'] >= 0 and value != state['id'] or self.contact is not None and (self.slot, value) != self.contact[0]):
                        self.frame_invalid = True
                    if value < 0 and state['id'] >= 0:
                        self.released.add(self.slot)
                    if value >= 0 and value != state['id']:
                        state['x'] = state['y'] = None
                    state['id'] = value
                    if sum(s['id'] >= 0 for s in self.slots.values()) > 1:
                        self.frame_invalid = True
                else:
                    state['x' if code == 53 else 'y'] = value
            elif kind == 3 and code in (0, 1):
                self.single['x' if code == 0 else 'y'] = value
            elif kind == 1 and code == 330:
                if value and 'single' in self.released:
                    self.frame_invalid = True
                if not value and self.single['down']:
                    self.released.add('single')
                self.single['down'] = bool(value)
        return result


class Gesture:
    def __init__(self, movement=12, timeout=30):
        self.movement, self.timeout = movement, timeout
        self.pressed = None
        self.start = None
        self.cancelled = False
        self.signature = None

    @staticmethod
    def _target(event, boxes):
        x, y = event.get('x', -1), event.get('y', -1)
        return next((b for b in boxes if b.get('enabled', True) and b['x'] <= x < b['x'] + b['w'] and b['y'] <= y < b['y'] + b['h']), None)

    def tick(self, now):
        if self.start and now - self.start[2] > self.timeout:
            self.start = None
            self.pressed = None

    def feed(self, event, hitboxes):
        now = event.get('time', 0)
        self.tick(now)
        kind = event['kind']
        if kind == 'cancel':
            self.start = None
            self.pressed = None
            return []
        target = self._target(event, hitboxes)
        if kind == 'down':
            self.start = (event['x'], event['y'], now)
            self.pressed = target['id'] if target else None
            self.signature = dict(target) if target else None
            self.cancelled = False
            return []
        if self.start is None:
            return []
        dx, dy = event['x'] - self.start[0], event['y'] - self.start[1]
        if abs(dx) > self.movement or abs(dy) > self.movement or target != self.signature:
            self.cancelled = True
            self.pressed = None
        if kind != 'up':
            return []
        action = self.pressed if not self.cancelled else None
        self.start = None
        self.pressed = None
        if abs(dx) >= 70 and abs(dx) > 2 * abs(dy):
            return ['swipe:left' if dx < 0 else 'swipe:right']
        if abs(dy) >= 70 and abs(dy) > 2 * abs(dx):
            return ['swipe:up' if dy < 0 else 'swipe:down']
        return [action] if action else []


class EventReader:
    def __init__(self, path='/dev/input/event0'):
        self.fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0x800))
        try:
            axes = query_axes(self.fd)
            self.decoder = EventDecoder(*axes)
            self._resynchronize(query_contacts(self.fd))
        except BaseException:
            os.close(self.fd)
            raise

    def _resynchronize(self, active):
        self.decoder.resynchronize(active)
        self.decoder.slot = query_current_slot(self.fd)

    def read(self, now):
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return self._recover(now)
        except OSError:
            return self.decoder.cancel(now)
        if not data:
            return self.decoder.cancel(now)
        events = self.decoder.feed(data, now)
        events.extend(self._recover(now))
        return events

    def _recover(self, now):
        """Quarantine loss through SYN_REPORT, drain, then fence an idle snapshot.

        Draining is bounded to keep the render loop responsive. A second empty
        read after the snapshot proves no older queued records can be replayed
        as a new contact. Any records arriving during the snapshot stay discarded
        and require another drain/snapshot pass. Partial records also keep the
        decoder disarmed until they are completely consumed.
        """
        if not self.decoder.dropped or not self.decoder.drop_boundary_seen:
            return []
        events = []
        for _ in range(16):
            try:
                data = os.read(self.fd, 4096)
            except BlockingIOError:
                break
            except OSError:
                return events + self.decoder.cancel(now)
            if not data:
                return events + self.decoder.cancel(now)
            events.extend(self.decoder.feed(data, now))
        else:
            return events
        if not self.decoder.drop_boundary_seen or self.decoder.buffer:
            return events
        if query_contacts(self.fd) is not False:
            return events
        slot = query_current_slot(self.fd)
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            self.decoder.resynchronize(False)
            self.decoder.slot = slot
            return events
        except OSError:
            return events + self.decoder.cancel(now)
        if not data:
            return events + self.decoder.cancel(now)
        events.extend(self.decoder.feed(data, now))
        return events

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
