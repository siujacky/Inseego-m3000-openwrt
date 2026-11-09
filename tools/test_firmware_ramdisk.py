import unittest
import struct
from firmware_ramdisk import (newc_archive, inspect_archive, check_ramdisk_layout,
                              combine, check_combined_image, check_modem_segments, PROBE_FILES,
                              check_abl_relocation)
from test_check_ram_image import fixture


def firmware_fixture():
    mdt = bytearray(84)
    mdt[:6] = b'\x7fELF\x01\x01'
    struct.pack_into('<H', mdt, 18, 164)
    struct.pack_into('<I', mdt, 28, 52)
    struct.pack_into('<HH', mdt, 42, 32, 1)
    struct.pack_into('<8I', mdt, 52, 1, 0, 0, 0x90800000, 4, 4096, 5, 4096)
    return {'modem.mdt': bytes(mdt), 'modem.b00': b'Q6fw'}


class FirmwareRamdiskTests(unittest.TestCase):
    def test_binary_firmware_roundtrip(self):
        files = {'modem.mdt': b'\x7fELF\0\xff', 'modem.b00': bytes(range(256))}
        archive = newc_archive(files)
        self.assertEqual(inspect_archive(archive), files)

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            newc_archive({'../init': b'bad'})

    def test_truncation_rejected(self):
        with self.assertRaises(ValueError):
            inspect_archive(newc_archive({'modem.mdt': b'fw'})[:-20])

    def test_load_overlaps_modem_or_exceeds_ram(self):
        for addr, size in [(0x90800000, 4096), (0xa0800000, 4096),
                           (0xbff00000, 0x200000), (0xa1000000, 0)]:
            with self.assertRaises(ValueError):
                check_ramdisk_layout(addr, size)
        check_ramdisk_layout(0xa1000000, 100 * 1024 * 1024)

    def test_combined_image_and_payload_integrity(self):
        image = combine(bytes(fixture()), firmware_fixture())
        result, _ = check_combined_image(image)
        self.assertEqual(result['firmware_files'], 2)
        self.assertEqual(result['modem_load_segments'], 1)
        self.assertIn('vmalloc=384M', result['command_line'].split())
        damaged = bytearray(image)
        damaged[8300] ^= 1
        with self.assertRaisesRegex(ValueError, 'hash'):
            check_combined_image(bytes(damaged))

    def test_wrong_ramdisk_address_rejected(self):
        image = bytearray(combine(bytes(fixture()), firmware_fixture()))
        struct.pack_into('<I', image, 20, 0x90800000)
        with self.assertRaisesRegex(ValueError, 'address'):
            check_combined_image(bytes(image))

    def test_exact_diagnostic_marker_has_no_modem(self):
        result, _ = check_combined_image(combine(bytes(fixture()), PROBE_FILES))
        self.assertTrue(result['ramdisk_probe'])
        self.assertEqual(result['modem_load_segments'], 0)

    def test_real_bootloader_window_rejects_large_external_ramdisk(self):
        with self.assertRaisesRegex(ValueError, 'relocation window'):
            check_abl_relocation(8 * 1024 * 1024, 55418988)
        self.assertEqual(check_abl_relocation(8 * 1024 * 1024, 360), 0x822fe000)

    def test_missing_segment_and_wrong_carveout_rejected(self):
        files = firmware_fixture()
        del files['modem.b00']
        with self.assertRaisesRegex(ValueError, 'segment'):
            check_modem_segments(files)
        files = firmware_fixture()
        mdt = bytearray(files['modem.mdt'])
        struct.pack_into('<I', mdt, 52 + 12, 0x80000000)
        files['modem.mdt'] = bytes(mdt)
        with self.assertRaisesRegex(ValueError, 'carveout'):
            check_modem_segments(files)


if __name__ == '__main__':
    unittest.main()
