import struct
import unittest
from unittest.mock import patch
from m3100_touch.input import EventDecoder, EventReader, Gesture, query_axes, query_contacts


def frame(*events):
    return b''.join(struct.pack('<IIHHi', 0, 0, *e) for e in (*events, (0, 0, 0)))


BOXES = [dict(id='apply', x=0, y=0, w=100, h=50, enabled=True),
         dict(id='cancel', x=100, y=0, w=100, h=50, enabled=True)]


class InputTests(unittest.TestCase):
    def ready(self):
        d = EventDecoder()
        self.assertEqual(d.feed(frame(), 0), [])
        return d

    def test_signed_mt_release_and_partial_16byte_records(self):
        d = self.ready()
        raw = frame((3, 47, 0), (3, 57, 8), (3, 53, 21), (3, 54, 22), (1, 330, 1))
        self.assertEqual(d.feed(raw[:13], 1), [])
        self.assertEqual(d.feed(raw[13:-16], 1), [])
        self.assertEqual(d.feed(raw[-16:], 1)[0]['kind'], 'down')
        events = d.feed(frame((3, 57, -1), (1, 330, 0)), 2)
        self.assertEqual([(e['kind'], e['x'], e['y']) for e in events], [('up', 21, 22)])
        self.assertEqual(d.feed(frame(), 3), [])

    def test_mt_wins_over_single_touch_and_clamps(self):
        d = self.ready()
        es = d.feed(frame((3, 57, 2), (3, 53, 999), (3, 54, -20), (3, 0, 100), (3, 1, 100), (1, 330, 1)), 1)
        self.assertEqual([(e['kind'], e['x'], e['y']) for e in es], [('down', 319, 0)])

    def test_startup_contact_requires_idle_before_new_press(self):
        d = EventDecoder()
        self.assertEqual(d.feed(frame((3, 57, 1), (3, 53, 10), (3, 54, 10)), 0), [])
        self.assertEqual(d.feed(frame((3, 57, -1)), 1), [])
        self.assertEqual(d.feed(frame((3, 57, 2), (3, 53, 10), (3, 54, 10)), 2)[0]['kind'], 'down')

    def test_multitouch_and_syn_dropped_cancel_until_idle(self):
        d = self.ready()
        d.feed(frame((3, 57, 1), (3, 53, 10), (3, 54, 10)), 1)
        self.assertEqual(d.feed(frame((3, 47, 1), (3, 57, 2)), 2)[0]['kind'], 'cancel')
        self.assertEqual(d.feed(frame((3, 47, 0), (3, 57, -1), (3, 47, 1), (3, 57, -1)), 3), [])
        d.feed(frame((3, 47, 0), (3, 57, 3)), 4)
        self.assertEqual(d.feed(frame((0, 3, 0)), 5)[0]['kind'], 'cancel')
        self.assertEqual(d.feed(frame((3, 53, 20)), 6), [])

    def test_single_touch_fallback_and_axis_mapping(self):
        d = EventDecoder(x_range=(100, 1100), y_range=(200, 2200))
        d.feed(frame(), 0)
        self.assertEqual(d.feed(frame((3, 0, 600), (3, 1, 1200), (1, 330, 1)), 1)[0]['x'], 160)
        self.assertEqual(d.feed(frame((1, 330, 0)), 2)[0]['kind'], 'up')

    def test_ioctl_axis_query_reads_actual_ranges(self):
        def ioctl(fd, request, buf, mutate=True):
            buf[:] = struct.pack('iiiiii', 0, 10, 329, 0, 0, 0)
            return 0
        with patch('m3100_touch.input._ioctl', side_effect=ioctl) as call:
            self.assertEqual(query_axes(7), ((10, 329), (10, 329)))
            self.assertEqual(call.call_count, 2)

    def test_release_only_exactly_once(self):
        g = Gesture()
        self.assertEqual(g.feed(dict(kind='down', x=20, y=20, time=0), BOXES), [])
        self.assertEqual(g.pressed, 'apply')
        self.assertEqual(g.feed(dict(kind='up', x=20, y=20, time=.1), BOXES), ['apply'])
        self.assertEqual(g.feed(dict(kind='up', x=20, y=20, time=.2), BOXES), [])

    def test_cross_target_movement_loss_timeout_cancel(self):
        for event in [dict(kind='move', x=120, y=20, time=.1), dict(kind='move', x=35, y=20, time=.1), dict(kind='cancel', time=.1)]:
            g = Gesture()
            g.feed(dict(kind='down', x=20, y=20, time=0), BOXES)
            g.feed(event, BOXES)
            self.assertEqual(g.feed(dict(kind='up', x=20, y=20, time=.2), BOXES), [])
        g = Gesture()
        g.feed(dict(kind='down', x=20, y=20, time=0), BOXES)
        g.tick(31)
        self.assertEqual(g.feed(dict(kind='up', x=20, y=20, time=31), BOXES), [])

    def test_swipe_never_activates_button(self):
        g = Gesture()
        g.feed(dict(kind='down', x=180, y=20, time=0), BOXES)
        g.feed(dict(kind='move', x=30, y=20, time=.1), BOXES)
        self.assertEqual(g.feed(dict(kind='up', x=30, y=20, time=.2), BOXES), ['swipe:left'])

    def test_coordinates_in_release_frame_cannot_activate_original_target(self):
        d, g = self.ready(), Gesture()
        for e in d.feed(frame((3, 57, 8), (3, 53, 20), (3, 54, 20)), 1):
            g.feed(e, BOXES)
        result = []
        for e in d.feed(frame((3, 53, 65), (3, 57, -1)), 2):
            result.extend(g.feed(e, BOXES))
        self.assertEqual(result, [])

    def test_same_frame_replaced_contact_cancels_old_gesture(self):
        d = self.ready()
        d.feed(frame((3, 57, 8), (3, 53, 20), (3, 54, 20)), 1)
        events = d.feed(frame((3, 57, -1), (3, 57, 8), (3, 53, 20), (3, 54, 20)), 2)
        self.assertEqual([e['kind'] for e in events], ['cancel'])

    def test_reader_partial_reads_nonblocking_loss_and_close(self):
        raw = frame((3, 0, 20), (3, 1, 20), (1, 330, 1))
        with patch('m3100_touch.input.os.open', return_value=5), patch('m3100_touch.input.os.close') as closed, patch('m3100_touch.input.query_axes', return_value=((0, 319), (0, 239))), patch('m3100_touch.input.query_contacts', return_value=False), patch('m3100_touch.input.os.read', side_effect=[raw[:17], raw[17:], BlockingIOError(), OSError('gone')]):
            r = EventReader('/dev/test')
            self.assertEqual(r.read(1), [])
            self.assertEqual(r.read(2)[0]['kind'], 'down')
            self.assertEqual(r.read(3), [])
            self.assertEqual(r.read(4)[0]['kind'], 'cancel')
            r.close()
            r.close()
            closed.assert_called_once_with(5)

    def test_unknown_ioctl_contact_state_does_not_accept_generic_tracking_id(self):
        with patch('m3100_touch.input._ioctl', side_effect=OSError('unsupported')):
            self.assertIsNone(query_contacts(3))
        d = EventDecoder()
        d.resynchronize(True)
        self.assertEqual(d.feed(frame((3, 57, 0), (3, 53, 20), (3, 54, 20)), 1), [])

    def test_gesture_target_disabled_between_down_up_cancels(self):
        g = Gesture()
        g.feed(dict(kind='down', x=20, y=20, time=0), BOXES)
        disabled = [dict(b, enabled=False) for b in BOXES]
        self.assertEqual(g.feed(dict(kind='up', x=20, y=20, time=1), disabled), [])

    def test_high_slot_is_detected_at_startup_and_after_drop(self):
        def ioctl(fd, request, buf, mutate=True):
            if request == 0x8018456f:
                buf[:] = struct.pack('6i', 0, 0, 31, 0, 0, 0)
            elif request & 0xffff == 0x450a:
                self.assertEqual(len(buf), 132)
                ids = [-1] * 32
                ids[31] = 25
                buf[:] = struct.pack('33i', 57, *ids)
            else:
                buf[:] = bytes(len(buf))
            return 0
        with patch('m3100_touch.input._ioctl', side_effect=ioctl):
            self.assertTrue(query_contacts(7))
            d = EventDecoder()
            d.resynchronize(query_contacts(7))
            self.assertEqual(d.feed(frame((3, 47, 31), (3, 57, 25), (3, 53, 20), (3, 54, 20)), 1), [])
        d = self.ready()
        events = d.feed(frame((3, 47, 31), (3, 57, 25), (3, 53, 20), (3, 54, 20)), 1)
        self.assertEqual([e['kind'] for e in events], ['down'])

    def test_corrupt_slot_count_cannot_claim_idle(self):
        def ioctl(fd, request, buf, mutate=True):
            if request == 0x8018456f:
                buf[:] = struct.pack('6i', 0, 0, 1000000, 0, 0, 0)
            else:
                buf[:] = bytes(len(buf))
            return 0
        with patch('m3100_touch.input._ioctl', side_effect=ioctl):
            self.assertIsNot(query_contacts(7), False)

    def test_reader_restores_current_slot_cursor_at_startup_and_drop(self):
        raw = frame((3, 57, 8), (3, 53, 20), (3, 54, 20))
        def ioctl(fd, request, buf, mutate=True):
            self.assertEqual(request, 0x8018456f)
            buf[:] = struct.pack('6i', 31, 0, 31, 0, 0, 0)
            return 0
        with patch('m3100_touch.input.os.open', return_value=5), patch('m3100_touch.input.os.close'), patch('m3100_touch.input.query_axes', return_value=((0, 319), (0, 239))), patch('m3100_touch.input.query_contacts', side_effect=[False, True, False]), patch('m3100_touch.input._ioctl', side_effect=ioctl), patch('m3100_touch.input.os.read', side_effect=[raw, frame((0, 3, 0)), BlockingIOError(), frame(), BlockingIOError(), BlockingIOError(), raw]):
            r = EventReader('/dev/test')
            self.assertEqual(r.decoder.slot, 31)
            self.assertEqual(r.read(1)[0]['kind'], 'down')
            self.assertEqual(r.read(2)[0]['kind'], 'cancel')
            self.assertEqual(r.read(3), [])
            self.assertEqual(r.decoder.slot, 31)
            self.assertEqual(r.read(4)[0]['kind'], 'down')
            r.close()

    def test_replacement_ending_idle_cancels_before_release(self):
        for replacement in [8, 9]:
            with self.subTest(replacement=replacement):
                d, g = self.ready(), Gesture()
                for event in d.feed(frame((3, 57, 8), (3, 53, 20), (3, 54, 20)), 1):
                    g.feed(event, BOXES)
                events = d.feed(frame((3, 57, -1), (3, 57, replacement), (3, 53, 20), (3, 54, 20), (3, 57, -1)), 2)
                actions = [action for event in events for action in g.feed(event, BOXES)]
                self.assertEqual(actions, [])
                self.assertEqual([event['kind'] for event in events], ['cancel'])

    def test_partial_discarded_record_keeps_recovery_disarmed(self):
        down = frame((3, 57, 8), (3, 53, 20), (3, 54, 20))
        up = frame((3, 57, -1))
        chunks = iter([frame((0, 3, 0)) + down[:7], BlockingIOError(), down[7:], up, BlockingIOError(), BlockingIOError()])
        def read(fd, size):
            result = next(chunks, BlockingIOError())
            if isinstance(result, Exception):
                raise result
            return result
        with patch('m3100_touch.input.os.open', return_value=5), patch('m3100_touch.input.os.close'), patch('m3100_touch.input.query_axes', return_value=((0,319),(0,239))), patch('m3100_touch.input.query_contacts', return_value=False), patch('m3100_touch.input.query_current_slot', return_value=0), patch('m3100_touch.input.os.read', side_effect=read):
            r, g = EventReader('/dev/test'), Gesture()
            actions = []
            for now in range(10):
                for event in r.read(now):
                    actions.extend(g.feed(event, BOXES))
            r.close()
            self.assertEqual(actions, [])

    def test_transient_second_slot_cancels_even_when_both_end_idle(self):
        for release_original in [False, True]:
            d, g = self.ready(), Gesture()
            for event in d.feed(frame((3, 57, 8), (3, 53, 20), (3, 54, 20)), 1):
                g.feed(event, BOXES)
            records = [(3, 47, 1), (3, 57, 9), (3, 53, 20), (3, 54, 20), (3, 57, -1), (3, 47, 0)]
            if release_original:
                records.append((3, 57, -1))
            events = d.feed(frame(*records), 2)
            self.assertEqual([event['kind'] for event in events], ['cancel'])
            self.assertEqual([action for event in events for action in g.feed(event, BOXES)], [])

    def test_new_slot_after_original_release_also_cancels_before_idle_up(self):
        d, g = self.ready(), Gesture()
        for event in d.feed(frame((3, 57, 8), (3, 53, 20), (3, 54, 20)), 1):
            g.feed(event, BOXES)
        events = d.feed(frame((3, 57, -1), (3, 47, 1), (3, 57, 9), (3, 53, 20), (3, 54, 20), (3, 57, -1)), 2)
        self.assertEqual([action for event in events for action in g.feed(event, BOXES)], [])
        self.assertEqual([event['kind'] for event in events], ['cancel'])

    def test_split_syn_dropped_quarantines_through_actual_boundary(self):
        raw = struct.pack('<IIHHi', 0, 0, 0, 3, 0) + frame((3, 57, 8), (3, 53, 20), (3, 54, 20)) + frame((3, 57, -1))
        for cut in range(16, len(raw), 16):
            with self.subTest(cut=cut):
                chunks = iter([raw[:cut], BlockingIOError(), raw[cut:], BlockingIOError(), BlockingIOError()])
                def read(fd, size):
                    result = next(chunks, BlockingIOError())
                    if isinstance(result, Exception):
                        raise result
                    return result
                with patch('m3100_touch.input.os.open', return_value=5), patch('m3100_touch.input.os.close'), patch('m3100_touch.input.query_axes', return_value=((0,319),(0,239))), patch('m3100_touch.input.query_contacts', return_value=False), patch('m3100_touch.input.query_current_slot', return_value=0), patch('m3100_touch.input.os.read', side_effect=read):
                    r, g = EventReader('/dev/test'), Gesture()
                    actions = []
                    for now in range(10):
                        for event in r.read(now):
                            actions.extend(g.feed(event, BOXES))
                    r.close()
                    self.assertEqual(actions, [])

    def test_recovery_discards_contacts_queued_before_or_during_idle_snapshot(self):
        down = frame((3, 57, 8), (3, 53, 20), (3, 54, 20))
        up = frame((3, 57, -1))
        for backlog in [[down, up], [BlockingIOError(), down, up]]:
            chunks = iter([frame((0, 3, 0))] + backlog + [BlockingIOError(), BlockingIOError()])
            def read(fd, size):
                result = next(chunks, BlockingIOError())
                if isinstance(result, Exception):
                    raise result
                return result
            with patch('m3100_touch.input.os.open', return_value=5), patch('m3100_touch.input.os.close'), patch('m3100_touch.input.query_axes', return_value=((0,319),(0,239))), patch('m3100_touch.input.query_contacts', return_value=False), patch('m3100_touch.input.query_current_slot', return_value=0), patch('m3100_touch.input.os.read', side_effect=read):
                r, g = EventReader('/dev/test'), Gesture()
                actions = []
                for now in range(10):
                    for event in r.read(now):
                        actions.extend(g.feed(event, BOXES))
                r.close()
                self.assertEqual(actions, [])

    def test_recovery_accepts_only_fresh_contact_after_empty_snapshot_boundary(self):
        down = frame((3, 57, 8), (3, 53, 20), (3, 54, 20))
        up = frame((3, 57, -1))
        chunks = iter([frame((0, 3, 0)), BlockingIOError(), BlockingIOError(), down, up])
        def read(fd, size):
            result = next(chunks, BlockingIOError())
            if isinstance(result, Exception):
                raise result
            return result
        with patch('m3100_touch.input.os.open', return_value=5), patch('m3100_touch.input.os.close'), patch('m3100_touch.input.query_axes', return_value=((0,319),(0,239))), patch('m3100_touch.input.query_contacts', return_value=False), patch('m3100_touch.input.query_current_slot', return_value=0), patch('m3100_touch.input.os.read', side_effect=read):
            r, g = EventReader('/dev/test'), Gesture()
            actions = []
            for now in range(8):
                for event in r.read(now):
                    actions.extend(g.feed(event, BOXES))
            r.close()
            self.assertEqual(actions, ['apply'])
