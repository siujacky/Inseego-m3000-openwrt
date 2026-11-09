import unittest
from m3100_touch.render import render, wrap_text, ROW_CAPACITY
from m3100_touch.keyboard import Keyboard


class RenderTests(unittest.TestCase):
    def test_every_kind_frame_and_primary_hitboxes_bounded(self):
        for kind in ['home', 'list', 'form', 'message', 'unlock', 'confirm', 'error']:
            view = dict(kind=kind, title='Ajustes / Settings', rows=[dict(id=str(i), label='Configuración larga ' * 3, value='MixedCase', kind='field') for i in range(6 if kind == 'home' else 3)], footer=[dict(id='back', label='Back'), dict(id='apply', label='Apply')])
            pixels, boxes = render(view)
            self.assertEqual(len(pixels), 230400)
            for b in boxes:
                self.assertGreaterEqual(min(b['w'], b['h']), 40)
                self.assertTrue(0 <= b['x'] < b['x'] + b['w'] <= 320)
                self.assertTrue(0 <= b['y'] < b['y'] + b['h'] <= 240)

    def test_case_preserving_and_spanish_glyphs(self):
        a = render(dict(title='Abc áéñ', rows=[]))[0]
        b = render(dict(title='ABC ÁÉÑ', rows=[]))[0]
        self.assertTrue(a != b, 'case must change rendered glyphs')

    def test_secret_rendering_identical_for_equal_lengths(self):
        def image(s):
            return render(dict(rows=[dict(id='pass', label='Password', value=s, secret=True, kind='field')]))[0]
        self.assertEqual(image('secret1'), image('secret2'))

    def test_keyboard_all_controls_large(self):
        _, boxes = render(Keyboard().view())
        self.assertEqual(len(boxes), 21)
        self.assertTrue(all(min(b['w'], b['h']) >= 40 for b in boxes))

    def test_wrapping_long_unbroken_values_and_bounded_pages(self):
        lines = wrap_text('AbCd' * 30, 10, max_lines=3)
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(len(s) <= 10 for s in lines))
        self.assertTrue(lines[-1].endswith('...'))
        _, boxes = render(dict(rows=[dict(id=str(i), label='item', kind='nav') for i in range(99)]))
        self.assertEqual(len(boxes), ROW_CAPACITY)

    def test_disabled_control_and_pressed_feedback(self):
        v = dict(rows=[dict(id='no', label='Unavailable', enabled=False, kind='action')])
        pixels, boxes = render(v)
        self.assertFalse(boxes[0]['enabled'])
        v['pressed'] = 'no'
        self.assertEqual(pixels, render(v)[0])

    def test_long_setting_identity_wraps_to_second_line(self):
        # With a value present a long field name still gets two lines; value
        # may be omitted on that row and remains available in its editor.
        long_label = 'Network interface configuration status'
        a = render(dict(rows=[dict(id='f', label=long_label, value='value', kind='field')]))[0]
        b = render(dict(rows=[dict(id='f', label=long_label[:34], value='value', kind='field')]))[0]
        self.assertTrue(a != b, 'long label continuation must be visible')

    def test_keyboard_cancel_and_space_labels_fit_single_line(self):
        k = Keyboard()
        k.press('mode:symbol')
        pixels, boxes = render(k.view())
        for id in ['cancel', 'char: ']:
            box = next(b for b in boxes if b['id'] == id)
            for y in range(box['y'] + 25, box['y'] + box['h']):
                line = pixels[(y * 320 + box['x']) * 3:(y * 320 + box['x'] + box['w']) * 3]
                self.assertNotIn(bytes((237, 244, 249)), line, id + ' should remain a single line')

    def test_light_theme_changes_pixels_preserves_frame_and_controls(self):
        view = dict(title='Settings', rows=[dict(id='field', label='Name', value='Router', kind='field')], footer=[dict(id='back', label='Back')])
        dark, dark_boxes = render(dict(view, theme='dark'))
        light, light_boxes = render(dict(view, theme='light'))
        self.assertEqual(len(light), 230400)
        self.assertTrue(dark != light, 'theme must affect the displayed pixels')
        self.assertEqual(dark_boxes, light_boxes)
        self.assertEqual(dark, render(dict(view, theme='system'))[0])
