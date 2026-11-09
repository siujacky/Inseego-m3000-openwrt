"""Verify compiled stock pair tables against the owner's DTB, without hardware."""
import pathlib
import re
import struct
import unittest

from check_ram_image import parse_dtb

ROOT = pathlib.Path(__file__).resolve().parents[1]


class StockSwitchSequence(unittest.TestCase):
    def test_exact_ordered_pairs_including_duplicates(self):
        node = parse_dtb((ROOT / 'stock/live.dtb').read_bytes())['/soc/i2c@835000/pcie_i2c_ctrl']
        header = (ROOT / 'tools/tc956x-switch-sequence.h').read_text()
        for key, count in [('reg_update', 56), ('switch_reg_update', 6)]:
            with self.subTest(key=key):
                block = re.search(r'stock_' + key + r'\[\] = \{(.*?)\};', header, re.S).group(1)
                actual = [(int(a, 16), int(b, 16)) for a, b in
                          re.findall(r'\{(0x[0-9a-f]+), (0x[0-9a-f]+)\}', block)]
                expected = list(struct.iter_unpack('>II', node[key]))
                self.assertEqual(len(actual), count)
                self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
