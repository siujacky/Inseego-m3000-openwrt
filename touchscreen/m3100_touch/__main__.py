"""Single-owner Linux frontend; no network or hardware work is done at import."""
import argparse
import json
import math
import os
from pathlib import Path
import select
import signal
import tempfile
import time
from .api import ApiClient
from .controller import Controller
from .input import EventReader
from .lcd import LCD


def parser():
    value = argparse.ArgumentParser(description='M3100 standalone touchscreen')
    value.add_argument('--base-url', default='http://127.0.0.1:8088')
    value.add_argument('--device', default='/dev/input/event0')
    value.add_argument('--lcd', default='/dev/m3100-lcd')
    value.add_argument('--status', default='/run/m3100-touchscreen.json')
    value.add_argument('--lock', default='/run/m3100-display.lock')
    return value


def atomic_status(path, status):
    """Closed schema, bounded secret-free state; replace on the same filesystem."""
    raw = json.dumps(status, separators=(',', ':'), allow_nan=False).encode('ascii')
    if len(raw) > 2048: raise ValueError('status exceeds limit')
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix='.touch-status-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream: stream.write(raw)
        os.replace(temp, path)
    finally:
        try: os.unlink(temp)
        except FileNotFoundError: pass


def read_observations(path='/run/m3100-hardware.json', wall=None):
    """Bounded current producer observations; never inspect credential files."""
    wall=time.time() if wall is None else wall
    try:
        with open(path,'rb') as stream: raw=stream.read(16385)
        if len(raw)>16384: return {}
        data=json.loads(raw)
        stamp=data.get('timestamp')
        if not isinstance(stamp,(int,float)) or isinstance(stamp,bool) or not math.isfinite(stamp) or not 0<=wall-stamp<=15: return {}
        return {key:data[key] for key in ('battery','modem') if isinstance(data.get(key),dict)}
    except (OSError,ValueError,TypeError,AttributeError): return {}


class FrameRuntime:
    def __init__(self, controller, lcd, boot_id):
        self.controller, self.lcd, self.boot_id = controller, lcd, boot_id
        self.last_frame_written, self.last_frame_monotonic = None, None
        self.render_ok = False
        self.failed = False

    def needs_frame(self, now):
        return self.controller.dirty or self.last_frame_monotonic is None or now-self.last_frame_monotonic >= 5

    def draw(self, now, wall):
        if self.failed: raise OSError('LCD descriptor failed')
        try:
            frame, boxes = self.controller.render()
            self.lcd.write_frame(frame)
        except Exception:
            self.render_ok, self.failed = False, True
            self.lcd.close()  # Never send a second header to a partially written FD.
            raise
        self.last_frame_written, self.last_frame_monotonic = wall, now
        self.render_ok = True

    def status(self, now, wall):
        return dict(boot_id=self.boot_id, pid=os.getpid(), timestamp=wall, monotonic=now,
                    render_ok=self.render_ok, last_frame_written=self.last_frame_written,
                    last_frame_monotonic=self.last_frame_monotonic,
                    screen_id='blank' if self.controller.blanked else self.controller.page,
                    locked=self.controller.locked)


def fence_reader(reader, before, after, controller=None):
    """A fresh evdev FD discards old queued history and snapshots held fingers."""
    if reader is not None and before!=after:
        reader.close()
        if controller is not None: controller.input_fenced()
        return None
    return reader


def process_input(controller, reader, now):
    generation=controller.generation
    for event in reader.read(now):
        controller.feed(event)
        if event.get('reason')=='device-lost':
            reader.close(); controller.input_fenced(); return None
        if controller.generation!=generation:
            reader.close()
            controller.input_fenced()
            return None  # Discard remaining batch; it predates the next paint.
    return reader


def acquire_display_lock(stream, stopped, timeout=2):
    """Wait briefly for the producer's static frame, without stealing a UI lease."""
    import fcntl
    deadline=time.monotonic()+timeout
    while not stopped():
        try:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            remaining=deadline-time.monotonic()
            if remaining<=0: return False
            time.sleep(min(.05,remaining))
    return False


def main(argv=None):
    args = parser().parse_args(argv)
    import fcntl  # Device deployment is Linux; tests can import pure helpers elsewhere.
    stop = [False]
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.__setitem__(0, True))
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()[:80]
    lock = open(args.lock, 'a+b')
    try:
        if not acquire_display_lock(lock,lambda:stop[0]): return 1
        controller = Controller(ApiClient(args.base_url), now=time.monotonic())
        reader, lcd, runtime = None, None, None
        try:
            lcd = LCD(args.lcd)
            runtime = FrameRuntime(controller, lcd, boot_id)
            next_status, next_input, next_observation = 0, 0, 0
            while not stop[0]:
                now, wall = time.monotonic(), time.time()
                generation=controller.generation
                controller.tick(now)
                if now>=next_observation:
                    controller.set_observations(read_observations(wall=wall))
                    next_observation=now+5
                reader=fence_reader(reader,generation,controller.generation,controller)
                semantic_fence=generation!=controller.generation
                if reader is None and now >= next_input and not semantic_fence:
                    try: reader = EventReader(args.device)
                    except OSError:
                        next_input = now + 5
                        if controller.mode != 'message': controller.show_message('Touch input unavailable. Waiting for the input device.')
                if reader is not None:
                    try:
                        # Recovery reads are nonblocking and bounded by EventReader.
                        reader=process_input(controller,reader,now)
                    except OSError:
                        controller.feed({'kind':'cancel','time':now})
                        reader.close(); reader=None; next_input=now+5
                if runtime.needs_frame(now):
                    try: runtime.draw(now, wall)
                    except Exception:
                        atomic_status(args.status, runtime.status(now, wall))
                        return 1  # Release flock; producer/procd can recover.
                if now >= next_status:
                    atomic_status(args.status, runtime.status(now, wall))
                    next_status = now+1
                if reader is not None:
                    select.select([reader.fd], [], [], .05)
                else: time.sleep(.05)
            return 0
        finally:
            controller.lock()
            controller.worker.close()
            if runtime is not None:
                runtime.render_ok=False
                atomic_status(args.status, runtime.status(time.monotonic(), time.time()))
            if reader is not None: reader.close()
            if lcd is not None: lcd.close()
    finally:
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
