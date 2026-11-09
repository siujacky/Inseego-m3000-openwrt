import unittest
from test_check_ram_image import fixture
from test_firmware_ramdisk import firmware_fixture
from firmware_trailer import attach_trailer, check_trailer


class TrailerTests(unittest.TestCase):
    def test_one_file_preserves_small_android_envelope(self):
        image = attach_trailer(bytes(fixture()), firmware_fixture())
        result, _ = check_trailer(image)
        self.assertTrue(result['ram_payload'])
        self.assertEqual(result['firmware_files'], 2)
        self.assertEqual(image[16:20], bytes(4))
        consoles = [t for t in result['command_line'].split() if t.startswith('console=')]
        self.assertEqual(consoles[0], 'console=ttyGS0,115200n8')
        self.assertEqual(consoles[-1], 'console=ttyMSM0,115200n8')

    def test_corrupt_payload_and_truncation_rejected(self):
        image = bytearray(attach_trailer(bytes(fixture()), firmware_fixture()))
        image[-4096] ^= 1
        with self.assertRaises(ValueError):
            check_trailer(bytes(image))
        with self.assertRaises(ValueError):
            check_trailer(bytes(image[:-1]))


if __name__ == '__main__':
    unittest.main()
