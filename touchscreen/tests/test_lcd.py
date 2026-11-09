import unittest
from unittest.mock import patch
from m3100_touch.lcd import LCD, FRAME_BYTES


class LCDTests(unittest.TestCase):
    def test_one_descriptor_exact_protocol(self):
        writes = []
        with patch('m3100_touch.lcd.os.open', return_value=8) as opened, patch('m3100_touch.lcd.os.close') as closed, patch('m3100_touch.lcd.os.write', side_effect=lambda fd, b: writes.append((fd, b)) or len(b)):
            with LCD('/dev/test') as lcd:
                lcd.write_frame(bytes(FRAME_BYTES))
            self.assertEqual(opened.call_count, 1)
            self.assertEqual([len(v) for _, v in writes], [6, FRAME_BYTES])
            self.assertEqual(writes[0], (8, b'frame\n'))
            closed.assert_called_once_with(8)

    def test_short_write_and_invalid_length_fail(self):
        with patch('m3100_touch.lcd.os.open', return_value=8), patch('m3100_touch.lcd.os.close'), patch('m3100_touch.lcd.os.write', return_value=5) as write:
            with LCD('/dev/test') as lcd:
                with self.assertRaises(ValueError):
                    lcd.write_frame(b'x')
                self.assertEqual(write.call_count, 0)
                with self.assertRaises(OSError):
                    lcd.write_frame(bytes(FRAME_BYTES))
                self.assertEqual(write.call_count, 1)

    def test_payload_short_write_is_failed_once_without_retry(self):
        with patch('m3100_touch.lcd.os.open', return_value=8), patch('m3100_touch.lcd.os.close'), patch('m3100_touch.lcd.os.write', side_effect=[6, FRAME_BYTES - 1]) as write:
            with LCD('/dev/test') as lcd:
                with self.assertRaises(OSError):
                    lcd.write_frame(bytes(FRAME_BYTES))
                self.assertEqual(write.call_count, 2)
