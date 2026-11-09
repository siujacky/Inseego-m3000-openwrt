import json
import unittest
from summarize_gnss_capture import summarize


def nmea(body):
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f'NMEA ${body}*{checksum:02X}\n'


class CaptureSummaryTests(unittest.TestCase):
    def test_counts_without_location_or_time(self):
        text = nmea('GPGGA,123456,1234.5678,N,12345.6789,E,1,04,2.0,0,M,0,M,,')
        text += nmea('GPRMC,123456,V,1234.5678,N,12345.6789,E,0,0,010101,,,N')
        text += nmea('GPGSV,1,1,04,01,10,100,20')
        result = summarize(text)
        self.assertEqual(result['checksum_valid_gga_with_fix'], 1)
        self.assertEqual(result['checksum_valid_rmc_active'], 0)
        self.assertEqual(result['maximum_gsv_satellites_visible'], 4)
        for secret in ('123456', '1234.5678', '12345.6789', '010101'):
            self.assertNotIn(secret, json.dumps(result))

    def test_bad_checksum_cannot_count_as_fix(self):
        result = summarize('NMEA $GPGGA,123456,,,,,1*XX\n')
        self.assertEqual(result['checksum_valid_gga_with_fix'], 0)
        self.assertEqual(result['checksums']['missing'], 1)

    def test_cleanup_requires_stop_and_both_verified_values(self):
        text = 'QMI ACK msg=0x0022 result=0 error=0\n'
        self.assertFalse(summarize(text)['bounded_session_cleanup_verified'])
        text += ('QMI ACK msg=0x0023 result=0 error=0\n'
                 'RESTORE VERIFIED msg=0x004b value=4\n'
                 'RESTORE VERIFIED msg=0x003b value=3\n')
        self.assertTrue(summarize(text)['bounded_session_cleanup_verified'])


if __name__ == '__main__':
    unittest.main()
