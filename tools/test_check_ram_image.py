import hashlib
import struct
import unittest

from check_ram_image import check_image, check_reboot_configuration


def fixture():
    kernel = bytearray(64)
    struct.pack_into('<I', kernel, 36, 0x016f2818)
    struct.pack_into('<I', kernel, 44, 64)
    # Minimal DT header is enough for the envelope tests, not for DT validation.
    kernel += struct.pack('>10I', 0xd00dfeed, 40, 40, 40, 40, 17, 16, 0, 0, 0)
    image = bytearray(8192)
    image[:8] = b'ANDROID!'
    struct.pack_into('<10I', image, 8, len(kernel), 0x80008000, 0,
                     0x81100000, 0, 0x80f00000, 0x82000000, 4096, 0, 0)
    image[48:64] = b'M3100-RAM'.ljust(16, b'\0')
    command = b'noinitrd ro root=/dev/ram0 console=ttyMSM0,115200n8'
    image[64:64 + len(command)] = command
    image[4096:4096 + len(kernel)] = kernel
    image[576:596] = hashlib.sha1(kernel + struct.pack('<I', len(kernel)) + bytes(8)).digest()
    return image


class ImageChecks(unittest.TestCase):
    def test_valid_envelope_does_not_claim_boot_success(self):
        result, dtb = check_image(bytes(fixture()))
        self.assertEqual(result['board_name'], 'M3100-RAM')
        self.assertFalse(result['hardware_boot_tested'])
        self.assertEqual(len(dtb), 40)

    def test_corrupt_payload_is_rejected(self):
        image = fixture()
        image[4100] ^= 1
        with self.assertRaisesRegex(ValueError, 'hash'):
            check_image(bytes(image))

    def test_nand_root_is_rejected(self):
        image = fixture()
        image[64:576] = b'root=ubi0:rootfs ubi.mtd=29'.ljust(512, b'\0')
        with self.assertRaisesRegex(ValueError, 'RAM root'):
            check_image(bytes(image))

    def test_wrong_board_is_rejected(self):
        image = fixture()
        image[48:64] = b'H5-200Q1'.ljust(16, b'\0')
        with self.assertRaisesRegex(ValueError, 'board'):
            check_image(bytes(image))

    def test_truncated_payload_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'size'):
            check_image(bytes(fixture()[:4140]))


class RebootChecks(unittest.TestCase):
    def test_missing_download_mode_mapping_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'download-mode'):
            check_reboot_configuration({}, {})

    def test_stock_mapping_and_normal_reason_are_required(self):
        cell = lambda *v: struct.pack('>' + 'I' * len(v), *v)
        draft = {
            '/firmware/scm': {'qcom,dload-mode': cell(7, 0x13000)},
            '/soc/tcsr': {'phandle': cell(7), 'reg': cell(0x1fc0000, 0x30000)},
            '/soc/sram@1468f000/reboot-mode': {
                'compatible': b'syscon-reboot-mode\0', 'offset': cell(0x65c),
                'mode-normal': cell(0x77665501)}}
        stock = {
            '/soc/scm': {'qcom,dload-mode': cell(9, 0x13000)},
            '/soc/syscon': {'phandle': cell(9), 'reg': cell(0x1fc0000, 0x30000)}}
        check_reboot_configuration(draft, stock)
        draft['/soc/sram@1468f000/reboot-mode']['mode-normal'] = cell(0)
        with self.assertRaisesRegex(ValueError, 'normal restart'):
            check_reboot_configuration(draft, stock)


if __name__ == '__main__':
    unittest.main()
