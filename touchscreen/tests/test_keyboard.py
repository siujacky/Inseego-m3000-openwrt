import string
import unittest
from m3100_touch.keyboard import Keyboard


class KeyboardTests(unittest.TestCase):
    def test_all_printable_ascii_reachable_without_case_conversion(self):
        k = Keyboard()
        seen = set()
        for _ in range(k.page_count):
            for key in k.view()['keys']:
                if key['id'].startswith('char:'):
                    seen.add(key['id'][5:])
            k.press('next')
        self.assertEqual(seen, set(chr(i) for i in range(32, 127)))
        for char in 'aAzZ!@ /':
            k.press('char:' + char)
        self.assertEqual(k.text, 'aAzZ!@ /')

    def test_secret_never_in_view_or_repr_cancel_restores(self):
        k = Keyboard('Sëcret!', secret=True)
        k.press('char:x')
        self.assertNotIn('Sëcret', repr(k.view()))
        self.assertNotIn('Sëcret', repr(k))
        self.assertEqual(k.view()['value'], '*' * 8)
        self.assertEqual(k.press('cancel'), 'cancel')
        self.assertEqual(k.text, 'Sëcret!')

    def test_delete_limit_done_existing_unicode(self):
        k = Keyboard('é', max_length=2)
        k.press('char:A')
        k.press('char:B')
        self.assertEqual(k.text, 'éA')
        k.press('delete')
        self.assertEqual(k.text, 'é')
        self.assertEqual(k.press('done'), 'done')

    def test_reject_control_injection_and_invalid_limits(self):
        k = Keyboard()
        k.press('char:\n')
        k.press('char:ab')
        self.assertEqual(k.text, '')
        with self.assertRaises(ValueError):
            Keyboard(max_length=-1)

    def test_direct_character_group_selection_preserves_text(self):
        k = Keyboard('Typed')
        for mode, chars in [('lower', 'abcdefghijkl'), ('upper', 'ABCDEFGHIJKL'), ('numeric', '0123456789'), ('symbol', ' ')]:
            k.press('mode:' + mode)
            view = k.view()
            self.assertEqual(view['mode'], mode)
            keys = ''.join(key['id'][5:] for key in view['keys'])
            self.assertTrue(all(c in keys for c in chars))
            self.assertEqual(k.text, 'Typed')
            self.assertEqual(len(view['tabs']), 4)
            self.assertTrue(view['page_label'].startswith('1/'))
