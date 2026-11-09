import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import collect_stock_gnss_metadata as collector


class CollectorTests(unittest.TestCase):
    def test_dry_run_never_contacts_adb(self):
        with mock.patch.object(sys, 'argv', ['collector', '--dry-run']), mock.patch.object(collector, 'request') as request, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(collector.main(), 0)
            request.assert_not_called()

    def test_wrong_release_stops_before_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'capture.json'
            with mock.patch.object(sys, 'argv', ['collector', '--adb', 'fake-adb', '--output', str(output)]), mock.patch.object(collector, 'request', return_value={'returncode': 0, 'stdout': '6.12.112\n'}) as request, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(collector.main(), 1)
                request.assert_called_once_with('fake-adb', 'uname -r', 6)
            self.assertEqual(json.loads(output.read_text())['queries'], [])

    def test_budget_prevents_late_metadata_queries(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'capture.json'
            with mock.patch.object(sys, 'argv', ['collector', '--adb', 'fake-adb', '--output', str(output)]), mock.patch.object(collector, 'request', return_value={'returncode': 0, 'stdout': '5.4.161-perf\n'}) as request, mock.patch.object(collector.time, 'monotonic', side_effect=[0, 56]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(collector.main(), 1)
                self.assertEqual(request.call_count, 1)
            self.assertIn('budget', json.loads(output.read_text())['stopped_reason'])

    def test_request_uses_exact_serial_and_timeout(self):
        response = subprocess.CompletedProcess([], 0, b'5.4.161-perf\n', b'')
        with mock.patch.object(collector.subprocess, 'run', return_value=response) as run:
            self.assertEqual(collector.request('trusted-adb', 'uname -r', 6)['returncode'], 0)
            run.assert_called_once_with(['trusted-adb', '-s', '76fe1623', 'shell', 'uname -r'], capture_output=True, timeout=6, check=False)


if __name__ == '__main__':
    unittest.main()
