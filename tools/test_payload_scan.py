"""Exercise the real scanner against a sparse simulated physical-memory file."""
from pathlib import Path
import os
import struct
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'posix', 'run under WSL with gcc')
class PayloadScannerTests(unittest.TestCase):
    def test_copy_and_out_of_pool_rejection(self):
        with tempfile.TemporaryDirectory(prefix='m3100-scan-') as tmp:
            tmp = Path(tmp)
            src = Path(__file__).resolve().parents[1] / 'overlay/package/utils/m3100-payload/src/payload-scan.c'
            exe, mem, output = tmp / 'scan', tmp / 'memory', tmp / 'payload'
            subprocess.run(['gcc', '-Wall', '-Wextra', '-Werror', '-O2', str(src), '-o', str(exe)], check=True)
            header = bytearray(4096)
            header[:32] = b'M3100_FW_RAM_TRAILER_V1'.ljust(32, b'\0')
            payload = b'\0\xffverified binary payload\0'
            struct.pack_into('<IIQ', header, 32, 1, 4096, len(payload))
            location = 0xa1200000
            with mem.open('xb') as f:
                f.truncate(0xaa000000)  # sparse: only the written pages occupy disk
                f.seek(location)
                f.write(header + payload)
            r = subprocess.run([str(exe), str(mem), str(output)], capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(output.read_bytes(), payload)
            output.unlink()
            struct.pack_into('<Q', header, 40, 96 * 1024 * 1024)
            with mem.open('r+b') as f:
                f.seek(location)
                f.write(bytes(4096))
                f.seek(0xa9fff000)
                f.write(header)
            r = subprocess.run([str(exe), str(mem), str(output)], capture_output=True)
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
