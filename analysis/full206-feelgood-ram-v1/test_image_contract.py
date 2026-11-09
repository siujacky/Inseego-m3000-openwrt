"""Image integration requirements, before a candidate has been composed."""
from pathlib import Path
import hashlib
import io
import json
import lzma
import sys
import tarfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis/wlan148-integration'))
import cpio148


class ImageContract(unittest.TestCase):
    def test_final_elf_chain_and_actual_staging_pool(self):
        path = HERE / 'image-verification.json'
        self.assertTrue(path.exists(), 'Final image verification is missing')
        report = json.loads(path.read_text())
        self.assertTrue(report.get('objcopy_Image_and_zImage_match_checked_ELFs'), 'Objcopy linkage proof is missing')
        self.assertEqual(report['staging_pool_size'], report['download_placement']['staging_pool_size'])

    def test_composed_archives_preserve_touch_and_real_webui(self):
        self.assertTrue((HERE / 'composition.json').exists(), 'New RAM composition is missing')
        report = json.loads((HERE / 'composition.json').read_text())
        self.assertEqual(report['webui_version'], '2026.11.08.1')
        self.assertEqual(report['touchscreen_version'], 'v6')
        self.assertEqual(report['original_firmware_files_byte_identical'], 545)
        self.assertEqual(report['kernel_source_changes'], [])
        self.assertFalse(report['hardware_boot_tested'])
        with tarfile.open(fileobj=io.BytesIO(lzma.decompress((HERE / 'userspace.tar.xz').read_bytes())), mode='r:') as archive:
            files = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
        prefix = 'opt/m3100-webui/'
        self.assertIn(prefix + 'web/app/preferences.js', files)
        self.assertIn(prefix + 'web/app/pages/openwrt/cellular.js', files)
        self.assertIn(b'touchscreen_api', files[prefix + 'server/m3100ui/services/__init__.py'])
        self.assertIn(b'app/pages/openwrt/', files[prefix + 'server/m3100ui/services/ui_pages.py'])
        self.assertNotIn(prefix + 'tools/preview.py', files)
        self.assertIn('opt/m3100-touchscreen/m3100_touch/controller.py', files)

    def test_bootstrap_checksums_and_touchscreen_start_order(self):
        path = HERE / 'bootstrap.cpio'
        self.assertTrue(path.exists(), 'New bootstrap is missing')
        entries = {e['name']: e for e in cpio148.parse(path.read_bytes())}
        worker = entries['usr/libexec/m3100-firmware-load']['payload']
        hardware = worker.index(b'/etc/init.d/m3100-hardware start')
        ready = worker.index(b'touch /tmp/full206-userspace.ready')
        gate = worker.index(b'/usr/libexec/full206-ui-ready')
        helper = entries['usr/libexec/full206-ui-ready']['payload']
        web = helper.index(b'/etc/init.d/m3100-webui start')
        touch = helper.index(b'/etc/init.d/m3100-touchscreen start')
        self.assertLess(hardware, ready)
        self.assertLess(ready, gate)
        self.assertLess(web, touch)
        self.assertIn(b'[ -s /tmp/full206-network/exit-status ]', helper)
        self.assertIn(b'if [ "$rc" -eq 0 ]; then', helper)
        self.assertIn(b"printf 'bars'", worker)
        self.assertNotIn(b"printf 'bars\\n'", worker)
        payload = hashlib.sha256((HERE / 'payload.cpio.xz').read_bytes()).hexdigest().encode()
        userspace = hashlib.sha256((HERE / 'userspace.tar.xz').read_bytes()).hexdigest().encode()
        self.assertEqual(entries['etc/m3100-payload.sha256']['payload'], payload + b'  /tmp/m3100-firmware.cpio.xz\n')
        self.assertEqual(entries['etc/full206-userspace.sha256']['payload'], userspace + b'  full206/userspace.tar.xz\n')


if __name__ == '__main__':
    unittest.main()
