"""Real flock/process tests run under Linux; metadata tests also run on Windows."""
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'runtime' / 'hardware.py'


def load_hardware():
    spec = importlib.util.spec_from_file_location('touch_hardware', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hold_lock(path, ready):
    import fcntl
    with open(path, 'a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        ready.set()
        time.sleep(30)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.hw = load_hardware()

    def status(self, **changes):
        value = dict(boot_id='boot', pid=123, timestamp=1000.0, monotonic=100.0,
                     render_ok=True, last_frame_written=999.0,
                     last_frame_monotonic=99.0, screen_id='home', locked=True)
        value.update(changes)
        return value

    def test_fresh_same_boot_frame_ignores_wall_clock_jump(self):
        value = self.hw.valid_ui_status(self.status(timestamp=-500, last_frame_written=-501), 'boot', 101)
        self.assertTrue(value['render_ok'])
        self.assertEqual(value['last_frame_written'], -501)

    def test_stale_wrong_boot_future_or_failed_frame_never_reuses_success(self):
        for change in ({'boot_id': 'old'}, {'monotonic': 60}, {'monotonic': 110},
                       {'last_frame_monotonic': 60}, {'last_frame_monotonic': 110},
                       {'render_ok': False}, {'pid': 0}, {'monotonic': float('nan')},
                       {'last_frame_monotonic': True}):
            with self.subTest(change=change):
                value = self.hw.valid_ui_status(self.status(**change), 'boot', 101)
                self.assertFalse(value['render_ok'])
                self.assertIsNone(value['last_frame_written'])

    def test_missing_oversized_or_malformed_status_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status'
            for data in (None, b'{' , b'x' * 16385):
                if data is not None:
                    path.write_bytes(data)
                self.assertFalse(self.hw.read_ui_status(path, 'boot', 101)['render_ok'])

    @unittest.skipUnless(os.name == 'posix', 'Linux process liveness')
    def test_recent_dead_process_status_is_not_reported_as_current_ui_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status'
            ready = multiprocessing.Event()
            process = multiprocessing.Process(target=hold_lock, args=(str(Path(directory) / 'lock'), ready))
            process.start()
            self.assertTrue(ready.wait(3))
            pid = process.pid
            process.terminate()
            process.join(3)
            path.write_text(json.dumps(self.status(pid=pid)))
            self.assertFalse(self.hw.read_ui_status(path, 'boot', 101)['render_ok'])

    @unittest.skipUnless(__import__('os').name == 'posix', 'Linux flock integration')
    def test_ui_ownership_skips_static_write_and_process_death_restores_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = str(Path(directory) / 'lock')
            ready = multiprocessing.Event()
            process = multiprocessing.Process(target=hold_lock, args=(lock, ready))
            process.start()
            try:
                self.assertTrue(ready.wait(3))
                calls = []
                result = self.hw.display_cycle(lock, Path(directory) / 'status', 'boot',
                                               lambda: calls.append('frame'), now=101)
                self.assertEqual(calls, [])
                self.assertEqual(result['owner'], 'touchscreen')
                self.assertFalse(result['render_ok'])
            finally:
                process.terminate()
                process.join(3)
            result = self.hw.display_cycle(lock, Path(directory) / 'status', 'boot',
                                           lambda: calls.append('frame'), now=102)
            self.assertEqual(calls, ['frame'])
            self.assertEqual(result['owner'], 'hardware')
            self.assertTrue(result['render_ok'])

    @unittest.skipUnless(__import__('os').name == 'posix', 'Linux flock integration')
    def test_failed_static_frame_does_not_publish_old_success(self):
        with tempfile.TemporaryDirectory() as directory:
            def fail():
                raise OSError('short frame')
            result = self.hw.display_cycle(str(Path(directory) / 'lock'), 'missing', 'boot', fail, now=10)
            self.assertFalse(result['render_ok'])
            self.assertIsNone(result['last_frame_written'])


if __name__ == '__main__':
    unittest.main()
