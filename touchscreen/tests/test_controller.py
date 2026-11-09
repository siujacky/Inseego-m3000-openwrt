import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from m3100_touch.api import ApiError
from m3100_touch.controller import Controller, ApiWorker


class ManualWorker:
    def __init__(self): self.jobs = []; self.results = []
    def submit(self, token, operation):
        if len(self.jobs) >= 2: return False
        self.jobs.append((token, operation)); return True
    def drain(self):
        out, self.results = self.results, []; return out
    def run(self):
        token, operation = self.jobs.pop(0)
        try: self.results.append((token, operation(), None))
        except Exception as exc: self.results.append((token, None, exc))
    def close(self): pass


class Client:
    def __init__(self):
        self.authenticated = True; self.cleared = 0; self.writes = []
        self.values = {'/api/settings': {'router_name': 'Router', 'theme': 'dark', 'session_idle_s': 120}, '/api/session': {'idle_timeout_s': 120}, '/api/touchscreen/preferences': {'language': 'en', 'theme': 'dark', 'display_timeout_s': 30}}
        self.failure = None
    def clear_session(self): self.authenticated = False; self.cleared += 1
    def login(self, password): self.authenticated = True; return {'authenticated': True, 'idle_timeout_s': 120}
    def request(self, method, path, body=None, **kw):
        if method != 'GET':
            self.writes.append((method, path, copy.deepcopy(body), kw))
            if self.failure: raise self.failure
            self.values.setdefault(path, {}).update(body or {})
            return {'ok': True}
        return copy.deepcopy(self.values.get(path, {}))


class QueuedTapReader:
    """Completed contacts received together while a full LCD frame was drawn."""
    def __init__(self, controller, identities):
        controller.render()
        self.events, self.closed = [], False
        for index, identity in enumerate(identities):
            box = next(item for item in controller.hitboxes if item['id'] == identity)
            x, y = box['x'] + box['w']//2, box['y'] + box['h']//2
            self.events += [dict(kind='down', x=x, y=y, time=10+index*.1),
                            dict(kind='up', x=x, y=y, time=10.04+index*.1)]
    def read(self, now):
        events, self.events = self.events, []
        return events
    def close(self): self.closed = True


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.client = Client(); self.worker = ManualWorker(); self.now = 10
        self.c = Controller(self.client, self.worker, now=self.now)
        self.c.locked = False
    def settle(self):
        while self.worker.jobs: self.worker.run(); self.c.poll(self.now)
    def settings(self): self.c.open('settings'); self.settle()
    def review(self): self.c.activate('action:save'); self.settle()
    def test_home_and_all_navigation(self):
        self.assertEqual(self.c.view()['kind'], 'home')
        self.assertIn('menu', [x['id'] for x in self.c.view()['rows']])
        self.c.activate('menu'); self.settle(); self.assertEqual(self.c.page, 'menu')
    def test_keyboard_cancel_and_preserve_case(self):
        self.settings(); self.c.activate('field:router_name')
        self.c.activate('char:X'); self.c.activate('cancel')
        self.assertEqual(self.c.draft, {})
        self.c.activate('field:router_name'); self.c.activate('char:X'); self.c.activate('done')
        self.assertEqual(self.c.draft['router_name'], 'RouterX')
    def test_batched_keyboard_digits_preserve_all_same_layout_taps(self):
        from m3100_touch.__main__ import process_input
        self.c.lock(); self.c.activate('unlock'); self.c.activate('mode:numeric')
        reader = QueuedTapReader(self.c, ['char:'+digit for digit in '13579'])
        generation = self.c.generation
        self.assertIs(process_input(self.c, reader, 11), reader)
        self.assertEqual(self.c.keyboard.text, '13579')
        self.assertFalse(reader.closed)
        self.assertEqual(self.c.generation, generation)
    def test_keyboard_text_and_delete_keep_layout_and_semantic_generation(self):
        self.c.activate('unlock'); self.c.render()
        boxes = copy.deepcopy(self.c.hitboxes); generation = self.c.generation
        for identity in ('char:a', 'char:b', 'delete', 'space'):
            self.c.activate(identity); self.c.render()
            self.assertEqual(self.c.hitboxes, boxes)
            self.assertEqual(self.c.generation, generation)
        self.assertEqual(self.c.keyboard.text, 'a ')
    def test_keyboard_layout_changes_still_fence_queued_contacts(self):
        from m3100_touch.__main__ import process_input
        for identity in ('mode:numeric', 'next'):
            self.c.lock(); self.c.activate('unlock')
            reader = QueuedTapReader(self.c, [identity, 'char:a'])
            self.assertIsNone(process_input(self.c, reader, 11))
            self.assertTrue(reader.closed)
            self.assertEqual(self.c.keyboard.text, '')
    def test_bad_login_password_is_not_reported_as_expired_session(self):
        self.c.lock(); self.c.activate('unlock'); self.c.activate('char:x')
        def reject(password): raise ApiError(401, 'bad_password', 'Wrong password')
        self.client.login = reject
        self.c.activate('done'); self.settle()
        self.assertTrue(self.c.locked)
        self.assertEqual(self.c.message, 'Incorrect password. Try again.')
        self.assertIsNone(self.c.keyboard)
    def test_authenticated_request_401_still_reports_session_expiry(self):
        def expired(*args, **kwargs): raise ApiError(401, 'unauthorized', 'Login required')
        self.client.request = expired
        self.c.open('settings'); self.settle()
        self.assertTrue(self.c.locked)
        self.assertEqual(self.c.message, 'Session ended. Unlock again.')
    def test_review_and_exactly_one_confirmed_write(self):
        self.settings(); self.c.draft['router_name'] = 'New'; self.review()
        self.assertEqual(self.c.mode, 'confirm'); self.assertEqual(self.client.writes, [])
        self.c.activate('apply'); self.c.activate('apply'); self.settle()
        self.assertEqual(len(self.client.writes), 1)
        self.assertTrue(self.client.writes[0][3]['confirm'])
    def test_conflict_blocks_review(self):
        self.settings(); self.c.draft['router_name'] = 'New'; self.client.values['/api/settings']['router_name'] = 'Browser'
        self.review(); self.assertEqual(self.c.mode, 'message'); self.assertIn('changed', self.c.message.lower())
        self.assertEqual(self.client.writes, [])
    def test_idle_immediately_clears_auth_and_secrets_and_late_results(self):
        self.settings(); self.c.draft['password'] = 'synthetic-secret'; self.c.open('credentials_reveal')
        self.c.tick(200); self.assertTrue(self.c.locked); self.assertEqual(self.c.draft, {})
        self.assertGreater(self.client.cleared, 0); self.settle(); self.assertEqual(self.c.page, 'home')
        self.assertNotIn('synthetic-secret', repr(self.c.view()))
    def test_refresh_during_contact_invalidates_reused_row(self):
        self.c.open('menu'); self.settle(); frame, boxes = self.c.render()
        target = next(x for x in boxes if x['id'] == 'status_menu'); x,y=target['x']+4,target['y']+4
        self.c.feed({'kind':'down','x':x,'y':y,'time':10}); self.c.accept_loaded(copy.deepcopy(self.c.loaded))
        self.c.feed({'kind':'up','x':x,'y':y,'time':11}); self.assertEqual(self.c.page, 'menu')
    def test_pressed_redraw_does_not_cancel_release(self):
        self.c.render(); box = next(x for x in self.c.hitboxes if x['id']=='menu'); x,y=box['x']+4,box['y']+4
        self.c.feed({'kind':'down','x':x,'y':y,'time':10}); self.c.render()
        self.c.feed({'kind':'up','x':x,'y':y,'time':11}); self.assertEqual(self.c.page,'menu')
    def test_blank_wake_consumes_whole_gesture(self):
        self.c.display_timeout=30
        self.c.tick(41); self.assertTrue(self.c.blanked)
        self.c.render(); self.c.feed({'kind':'down','x':120,'y':110,'time':42}); self.c.feed({'kind':'up','x':120,'y':110,'time':43})
        self.assertFalse(self.c.blanked); self.assertEqual(self.c.page, 'home')
    def test_timeout_unknown_no_replay_until_explicit_readback(self):
        self.settings(); self.c.draft['router_name']='New'; self.review(); self.client.failure=ApiError(0,'transport','Outcome unknown')
        self.c.activate('apply'); self.settle(); self.c.activate('apply'); self.settle()
        self.assertTrue(self.c.unknown); self.assertEqual(len(self.client.writes),1)
        self.c.activate('readback'); self.settle(); self.assertFalse(self.c.unknown)
    def test_401_locks_and_428_does_not_replay(self):
        self.settings(); self.c.draft['router_name']='New'; self.review(); self.client.failure=ApiError(401,'expired','Expired')
        self.c.activate('apply'); self.settle(); self.assertTrue(self.c.locked); self.assertEqual(len(self.client.writes),1)
    def test_unavailable_reason_and_back_are_usable(self):
        self.c.open('charging'); self.settle(); self.assertTrue(self.c.loaded['reason'])
        self.assertIn('back',[x['id'] for x in self.c.view()['footer']])
    def test_dirty_navigation_requires_discard(self):
        self.settings(); self.c.draft['router_name']='New'; self.c.activate('home')
        self.assertEqual(self.c.mode,'discard'); self.c.activate('cancel'); self.assertTrue(self.c.draft)
        self.c.activate('home'); self.c.activate('discard'); self.assertEqual(self.c.page,'home'); self.assertEqual(self.c.draft,{})
    def test_language_theme_and_password_modes(self):
        self.c.language='es'; self.c.theme='light'; self.assertEqual(self.c.view()['theme'],'light')
        self.c.activate('unlock'); self.assertEqual(self.c.view()['kind'],'keyboard')
        self.assertEqual(len(self.c.view()['tabs']),4); self.assertNotIn('password',repr(self.c.view()).lower())
    def test_worker_is_bounded_and_input_nonblocking(self):
        import threading
        gate=threading.Event(); w=ApiWorker(); self.addCleanup(w.close)
        w.submit('slow',lambda:gate.wait(.3)); start=time.monotonic()
        c=Controller(self.client,w,now=10); c.activate('menu'); c.render()
        self.assertLess(time.monotonic()-start,.15); gate.set()

    def test_short_lcd_write_closes_and_never_reuses_descriptor(self):
        from m3100_touch.__main__ import FrameRuntime
        class BrokenLCD:
            def __init__(self): self.calls=0; self.closed=False
            def write_frame(self, frame): self.calls+=1; raise OSError('short frame')
            def close(self): self.closed=True
        lcd=BrokenLCD(); runtime=FrameRuntime(self.c,lcd,'boot')
        with self.assertRaises(OSError): runtime.draw(10,100)
        self.assertTrue(lcd.closed); self.assertFalse(runtime.status(10,100)['render_ok'])
        with self.assertRaises(OSError): runtime.draw(11,101)
        self.assertEqual(lcd.calls,1)

    def test_status_exact_fields_and_static_periodic_refresh(self):
        from m3100_touch.__main__ import FrameRuntime, parser
        class GoodLCD:
            def write_frame(self, frame): pass
            def close(self): pass
        runtime=FrameRuntime(self.c,GoodLCD(),'boot')
        runtime.draw(10,100)
        self.assertFalse(runtime.needs_frame(14)); self.assertTrue(runtime.needs_frame(15))
        self.assertEqual(set(runtime.status(11,101)),{'boot_id','pid','timestamp','monotonic','render_ok','last_frame_written','last_frame_monotonic','screen_id','locked'})
        args=parser().parse_args(['--base-url','http://127.0.0.1:8088','--device','event','--lcd','lcd','--status','status','--lock','lock'])
        self.assertEqual(args.device,'event')

    def test_queued_login_and_write_cannot_start_after_local_lock(self):
        self.c.lock(); self.c.activate('unlock'); self.c.activate('char:x'); self.c.activate('done')
        self.c.lock(); self.settle(); self.assertFalse(self.client.authenticated)
        self.c.locked=False; self.settings(); self.c.draft['router_name']='New'; self.review()
        self.c.activate('apply'); self.c.lock(); self.settle(); self.assertEqual(self.client.writes,[])

    def test_reload_cannot_overwrite_newer_editor_or_sensitive_back(self):
        self.settings(); self.c.activate('reload'); self.c.activate('field:router_name'); self.c.activate('char:X')
        self.settle(); self.assertEqual(self.c.mode,'keyboard'); self.c.activate('done')
        self.assertEqual(self.c.draft['router_name'],'RouterX')
        self.c.draft={}; self.c.open('credentials_reveal'); self.c.activate('back'); self.settle()
        self.assertNotEqual(self.c.page,'credentials_reveal')

    def test_confirmed_tier3_requires_reauthentication_then_one_final_apply(self):
        self.settings(); self.c.draft['router_name']='New'; self.review(); self.c.pending['tier']=3
        self.c.activate('apply'); self.assertEqual(self.c.mode,'keyboard'); self.c.activate('char:x'); self.c.activate('done')
        self.assertEqual(self.client.writes,[])
        self.assertEqual(self.c.mode,'confirm'); self.c.activate('apply'); self.settle()
        self.assertEqual(len(self.client.writes),1); self.assertEqual(self.client.writes[0][3]['password'],'x')

    def test_428_and_409_do_not_replay_and_hide_password_error_echo(self):
        for status in (428,409):
            self.setUp(); self.settings(); self.c.draft['router_name']='New'; self.review()
            self.client.failure=ApiError(status,'blocked','Reload required')
            self.c.activate('apply'); self.settle(); self.c.activate('apply'); self.settle()
            self.assertEqual(len(self.client.writes),1); self.assertEqual(self.c.mode,'message')

    def test_locked_home_observations_are_bounded_truthful_and_never_capacity(self):
        self.c.set_observations({'battery':{'voltage_uv':3900000,'capacity_percent':99,'charging':True},'modem':{'remoteproc_state':'offline'}})
        view=self.c.view(); self.assertIn('3.900 V',view['status']); self.assertIn('offline',view['status'])
        self.assertNotIn('99',repr(view)); self.assertNotIn('charging',repr(view))

    def test_background_unknown_write_still_requires_readback_after_navigation(self):
        self.settings(); self.c.draft['router_name']='New'; self.review(); self.c.activate('apply')
        self.c.open('home',force=True); self.client.failure=ApiError(0,'transport','Unknown')
        # This simulates a dispatched response: queued requests are fenced instead.
        token,_=self.worker.jobs.pop(0); self.worker.results.append((token,None,self.client.failure))
        self.c.poll(10); self.assertTrue(self.c.unknown)

    def test_unsaved_back_cancel_preserves_navigation_history(self):
        self.settings(); history=list(self.c.history); self.c.draft['router_name']='New'
        self.c.activate('back'); self.c.activate('cancel'); self.assertEqual(self.c.history,history)

    def test_malformed_preferences_do_not_crash_or_disable_idle_policy(self):
        self.c._preferences({'display_timeout_s':'bad'}); self.assertEqual(self.c.display_timeout,300)

    def test_queued_old_input_is_closed_on_async_or_gesture_semantic_transition(self):
        from m3100_touch.__main__ import fence_reader, process_input
        class Reader:
            def __init__(self,events): self.events=events; self.closed=False; self.reads=0
            def read(self,now): self.reads+=1; return self.events
            def close(self): self.closed=True
        self.c.render(); box=next(b for b in self.c.hitboxes if b['id']=='menu'); x,y=box['x']+3,box['y']+3
        events=[dict(kind=kind,x=x,y=y,time=10+i) for i,kind in enumerate(('down','up','down','up'))]
        reader=Reader(events)
        self.assertIsNone(process_input(self.c,reader,10)); self.assertTrue(reader.closed); self.assertEqual(self.c.page,'menu')
        reader=Reader(events); previous=self.c.generation; self.c.accept_loaded(copy.deepcopy(self.c.loaded))
        self.assertIsNone(fence_reader(reader,previous,self.c.generation)); self.assertEqual(reader.reads,0); self.assertTrue(reader.closed)

    def test_error_saved_and_applied_details_are_visible(self):
        self.settings(); self.c.draft['router_name']='New'; self.review()
        self.client.failure=ApiError(409,'reload_failed','Runtime failed',{'saved':True,'applied':False})
        self.c.activate('apply'); self.settle(); self.assertIn('saved: On',self.c.message); self.assertIn('applied: Off',self.c.message)

    @unittest.skipUnless(os.name=='posix','Linux foreground flock integration')
    def test_actual_main_holds_lease_through_frame_and_releases_on_exit(self):
        import fcntl
        import signal
        from m3100_touch import __main__ as runtime
        with tempfile.TemporaryDirectory() as directory:
            lock=str(Path(directory)/'lock'); status=str(Path(directory)/'status'); held=[]; handlers={}
            class GoodLCD:
                def __init__(self,path): pass
                def write_frame(self,frame):
                    with open(lock,'a+b') as probe:
                        try: fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        except BlockingIOError: held.append(True)
                        else: held.append(False)
                def close(self): pass
            class Reader:
                fd=0
                def __init__(self,path): pass
                def read(self,now): return []
                def close(self): pass
            def select_and_stop(*args): handlers[signal.SIGTERM](); return [],[],[]
            with patch.object(runtime,'LCD',GoodLCD),patch.object(runtime,'EventReader',Reader),patch.object(runtime,'read_observations',return_value={}),patch.object(runtime.signal,'signal',side_effect=lambda sig,handler:handlers.__setitem__(sig,handler)),patch.object(runtime.select,'select',side_effect=select_and_stop):
                self.assertEqual(runtime.main(['--lock',lock,'--status',status]),0)
            self.assertTrue(held); self.assertTrue(all(held))
            with open(lock,'a+b') as probe: fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            value=json.loads(Path(status).read_text()); self.assertFalse(value['render_ok']); self.assertTrue(value['locked'])

    def test_observation_read_rejects_old_large_or_nonfinite_data(self):
        from m3100_touch.__main__ import read_observations
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'observations'
            for value in ({'timestamp':0,'battery':{}},{'timestamp':float('nan'),'battery':{}},{'timestamp':101,'battery':{}},[]):
                path.write_text(json.dumps(value)); self.assertEqual(read_observations(path,100),{})
            path.write_text('x'*16385); self.assertEqual(read_observations(path,100),{})

    def test_idle_during_slow_unlock_rejects_late_login(self):
        self.c.lock(); self.c.activate('unlock'); self.c.activate('char:x'); self.c.activate('done')
        epoch=self.c.auth_epoch; self.c.tick(200)
        self.assertGreater(self.c.auth_epoch,epoch); self.settle()
        self.assertTrue(self.c.locked); self.assertFalse(self.client.authenticated)

    def test_dispatched_write_origin_survives_navigation_lock_and_late_success(self):
        self.settings(); self.c.draft['router_name']='New'; self.review(); self.c.activate('apply')
        token,_=self.worker.jobs.pop(0)  # Already dispatched: completion will arrive later.
        self.c.activate('home'); self.c.activate('discard'); self.c.lock()
        self.assertEqual(self.c.unknown_target,('settings',{}))
        self.worker.results.append((token,{'ok':True},None)); self.c.poll(10)
        self.assertEqual(self.c.unknown_target,('settings',{})); self.assertTrue(self.c.unknown)
        self.c.locked=False; self.c.activate('readback'); self.settle()
        self.assertEqual(self.c.page,'settings'); self.assertTrue(self.c.unknown)
        self.c.activate('readback'); self.settle(); self.assertFalse(self.c.unknown)

    def test_unavailable_readback_keeps_guard_on_other_writable_page(self):
        self.settings(); self.c.draft['router_name']='New'; self.review()
        self.client.failure=ApiError(0,'transport','Unknown'); self.c.activate('apply'); self.settle()
        original=self.client.request
        def request(method,path,*args,**kwargs):
            if method=='GET' and path=='/api/settings': raise ApiError(503,'unavailable','Backend unavailable')
            return original(method,path,*args,**kwargs)
        self.client.request=request; self.c.activate('readback'); self.settle()
        self.assertTrue(self.c.unknown); self.assertEqual(self.c.unknown_target,('settings',{}))
        self.assertIn('unavailable',self.c.message.lower())
        self.c.open('presentation',force=True); self.settle(); self.c.draft={'theme':'light'}
        self.c.activate('action:save'); self.assertNotEqual(self.c.mode,'confirm'); self.assertEqual(len(self.client.writes),1)

    def test_empty_origin_readback_does_not_claim_observed_configuration(self):
        self.settings(); self.c.unknown=True; self.c.unknown_target=('settings',{})
        self.client.values['/api/settings']={}; self.c.activate('readback'); self.settle()
        self.assertTrue(self.c.unknown)

    def test_home_telemetry_during_pending_login_preserves_success(self):
        for completed in (False,True):
            self.setUp(); self.c.lock(); self.c.activate('unlock'); self.c.activate('char:x'); self.c.activate('done')
            generation=self.c.generation
            if completed: self.worker.run()
            self.c.set_observations({'battery':{'voltage_uv':3900000}})
            self.assertEqual(self.c.generation,generation)
            self.c.poll(self.now); self.settle(); self.assertFalse(self.c.locked); self.assertEqual(self.c.mode,'page'); self.assertEqual(self.c.page,'menu')

    def test_explicit_reload_exits_error_detail_and_postwrite_messages_preserving_draft(self):
        for mode in ('error','detail','postwrite'):
            self.setUp(); self.settings()
            if mode=='postwrite':
                self.c.draft={'router_name':'New'}; self.review(); self.c.activate('apply'); self.settle()
            elif mode=='error': self.c.draft={'router_name':'Draft'}; self.c.show_message('Transient failure')
            else: self.c.detail='Old diagnostic detail'; self.c.mode='detail'
            old_draft=dict(self.c.draft)
            self.assertIn('reload',[r['id'] for r in self.c.view()['footer']])
            self.c.activate('reload'); self.settle()
            self.assertEqual(self.c.mode,'page'); self.assertEqual(self.c.message,''); self.assertEqual(self.c.detail,'')
            self.assertEqual(self.c.draft,old_draft)
            if mode=='postwrite': self.assertEqual(self.c.loaded['data']['router_name'],'New')

    def test_runtime_wake_same_batch_and_held_snapshot_allow_first_new_tap(self):
        from m3100_touch.__main__ import process_input
        from m3100_touch.input import EventDecoder
        import struct
        def record(type,code,value): return struct.pack('<IIHHi',0,0,type,code,value)
        def frame(*records): return b''.join(record(*r) for r in records)+record(0,0,0)
        class Reader:
            def __init__(self,events): self.events=events; self.closed=False
            def read(self,now): events,self.events=self.events,[]; return events
            def close(self): self.closed=True
        for held in (False,True):
            self.setUp(); self.c.blanked=True; self.c.render()
            events=[dict(kind='down',x=120,y=110,time=10)]
            if not held: events.append(dict(kind='up',x=120,y=110,time=11))
            old=Reader(events); self.assertIsNone(process_input(self.c,old,10)); self.assertTrue(old.closed)
            self.assertFalse(self.c.blanked); self.assertFalse(self.c.wake_contact); self.c.render()
            box=next(b for b in self.c.hitboxes if b['id']=='menu'); x,y=box['x']+3,box['y']+3
            decoder=EventDecoder(); decoder.resynchronize(held)
            if held:
                # Snapshot of held wake finger quarantines its release; it emits no UP.
                self.assertEqual(decoder.feed(frame((1,330,0)),12),[])
                self.assertEqual(self.c.page,'home')
                decoder.resynchronize(False)  # Fresh idle snapshot in Task1 reader recovery.
            events=decoder.feed(frame((3,0,x),(3,1,y),(1,330,1)),13)+decoder.feed(frame((1,330,0)),14)
            fresh=Reader(events); process_input(self.c,fresh,14)
            self.assertEqual(self.c.page,'menu')

    def test_explicit_reload_preserves_conflict_baseline_for_dirty_keys(self):
        self.settings(); self.c.draft={'router_name':'Draft'}; self.c.show_message('Old error')
        self.client.values['/api/settings']['router_name']='Browser'
        self.c.activate('reload'); self.settle(); self.assertEqual(self.c.mode,'page')
        self.assertEqual(self.c.draft['router_name'],'Draft')
        self.review(); self.assertEqual(self.c.mode,'message'); self.assertIn('changed',self.c.message.lower())

    def test_home_observations_preserve_keyboard_and_working_generation_but_fence_home(self):
        self.c.open('home',force=True); before=self.c.generation
        self.c.set_observations({'battery':{'voltage_uv':3900000}}); self.assertGreater(self.c.generation,before)
        self.c.activate('unlock'); before=self.c.generation
        self.c.set_observations({'battery':{'voltage_uv':3901000}}); self.assertEqual(self.c.generation,before)

    def test_wrong_origin_readback_result_does_not_release_guard(self):
        self.settings(); self.c.unknown=True; self.c.unknown_target=('settings',{})
        self.c.activate('readback'); token,_=self.worker.jobs.pop()
        from m3100_touch import catalog
        self.worker.results.append((token,catalog.load(self.client,'presentation'),None)); self.c.poll(10)
        self.assertTrue(self.c.unknown); self.assertEqual(self.c.unknown_target,('settings',{}))

    @unittest.skipUnless(os.name=='posix','Linux foreground flock startup contention')
    def test_startup_lease_retries_transient_contention_and_honors_stop(self):
        import fcntl
        import threading
        from m3100_touch.__main__ import acquire_display_lock
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'lock'
            with open(path,'a+b') as producer,open(path,'a+b') as frontend:
                fcntl.flock(producer,fcntl.LOCK_EX)
                released=threading.Event()
                def release():
                    released.wait(.05); fcntl.flock(producer,fcntl.LOCK_UN)
                thread=threading.Thread(target=release); thread.start()
                try: self.assertTrue(acquire_display_lock(frontend,lambda:False,timeout=.5))
                finally: released.set(); thread.join(1)
                fcntl.flock(frontend,fcntl.LOCK_UN)
                self.assertFalse(acquire_display_lock(frontend,lambda:True,timeout=.5))

    @unittest.skipUnless(os.name=='posix','Linux foreground flock startup contention')
    def test_permanently_held_startup_lease_times_out_without_opening_lcd(self):
        import fcntl
        from m3100_touch import __main__ as runtime
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'lock'
            with open(path,'a+b') as producer:
                fcntl.flock(producer,fcntl.LOCK_EX)
                started=time.monotonic()
                with patch.object(runtime,'LCD') as lcd,patch.object(runtime,'EventReader') as reader,patch.object(runtime.signal,'signal'):
                    self.assertEqual(runtime.main(['--lock',str(path),'--status',str(Path(directory)/'status')]),1)
                self.assertGreaterEqual(time.monotonic()-started,1.8)
                self.assertLess(time.monotonic()-started,3)
                lcd.assert_not_called(); reader.assert_not_called()

    def test_unknown_create_requires_actual_new_resource_not_creation_template(self):
        from touchscreen.tests.test_ui_sweep import controller_for
        for exists in (False,True):
            c=controller_for('wifi_edit'); c.open('wifi_edit',{'create':True},force=True)
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.draft={'name':'new_ap','ssid':'Synthetic New','password':'SYNTHETIC-ONLY-123'}
            c.activate('action:save')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.failure=ApiError(0,'transport','Unknown'); c.activate('apply')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            if exists: c.client.values['/api/openwrt/wifi']['interfaces'].append(dict(name='new_ap',mode='ap',device='radio0',network='lan',ssid='Synthetic New',security='wpa2',password_set=True))
            c.activate('readback')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            self.assertEqual(c.unknown,not exists)
            if exists: self.assertEqual(c.context,{'name':'new_ap'}); self.assertEqual(c.loaded['data']['name'],'new_ap')

    def test_unknown_delete_is_resolved_only_by_authoritative_collection_absence(self):
        from touchscreen.tests.test_ui_sweep import controller_for
        for exists in (True,False):
            c=controller_for('wifi_edit'); c.activate('action:delete')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.failure=ApiError(0,'transport','Unknown'); c.activate('apply')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            if not exists: c.client.values['/api/openwrt/wifi']['interfaces']=[]
            c.activate('readback')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            self.assertEqual(c.unknown,exists)
            if not exists: self.assertEqual(c.page,'wifi'); self.assertEqual(c.mode,'page')

    def test_sms_timeout_readback_cannot_prove_delivery_and_never_retries(self):
        from touchscreen.tests.test_ui_sweep import controller_for
        c=controller_for('sms_send'); c.draft={'number':'+15555550100','text':'SYNTHETIC message'}
        c.activate('action:save')
        while c.worker.jobs: c.worker.run(); c.poll(10)
        c.client.failure=ApiError(0,'transport','Unknown'); c.activate('apply')
        while c.worker.jobs: c.worker.run(); c.poll(10)
        c.activate('readback')
        while c.worker.jobs: c.worker.run(); c.poll(10)
        self.assertTrue(c.unknown); c.activate('apply'); self.assertEqual(len(c.client.writes),1)

    def test_pending_dispatched_write_cannot_be_resolved_by_early_readback(self):
        self.settings(); self.c.draft={'router_name':'New'}; self.review(); self.c.activate('apply')
        self.worker.jobs.pop(0); self.c.lock(); self.c.locked=False
        self.c.open('settings',force=True); self.settle(); count=len(self.worker.jobs)
        self.c.show_message('Waiting for the old operation'); self.c.activate('readback')
        self.assertEqual(len(self.worker.jobs),count); self.assertTrue(self.c.unknown)

    def test_unknown_firewall_create_reads_section_and_unavailable_create_keeps_guard(self):
        from touchscreen.tests.test_ui_sweep import controller_for
        for available in (False,True):
            c=controller_for('firewall_rules_edit'); c.open('firewall_rules_edit',{'create':True},force=True)
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.draft=dict(section='new_rule',name='Synthetic rule',enabled=True,src='lan',dest='wan',family='ipv4',proto='tcp',target='ACCEPT')
            c.activate('action:save')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.failure=ApiError(0,'transport','Unknown'); c.activate('apply')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            if available:
                c.client.values['/api/openwrt/firewall']['rules'].append(dict(section='new_rule',options=dict(name='Synthetic rule',enabled=True,src='lan',dest='wan',family='ipv4',proto=['tcp'],target='ACCEPT'),editable=True))
            else:
                original=c.client.request
                def unavailable(method,path,*args,**kw):
                    if method=='GET' and path=='/api/openwrt/firewall': raise ApiError(503,'unavailable','Synthetic firewall unavailable')
                    return original(method,path,*args,**kw)
                c.client.request=unavailable
            c.activate('readback')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            self.assertEqual(c.unknown,not available)
            if available: self.assertEqual(c.context,{'section':'new_rule'})

    def test_delete_unavailable_or_malformed_collection_cannot_prove_absence(self):
        from touchscreen.tests.test_ui_sweep import controller_for
        for response in (ApiError(503,'unavailable','Synthetic Wi-Fi unavailable'),{}):
            c=controller_for('wifi_edit'); c.activate('action:delete')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.failure=ApiError(0,'transport','Unknown'); c.activate('apply')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            original=c.client.request
            def unavailable(method,path,*args,**kw):
                if method=='GET' and path=='/api/openwrt/wifi':
                    if isinstance(response,ApiError): raise response
                    return response
                return original(method,path,*args,**kw)
            c.client.request=unavailable; c.activate('readback')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            self.assertTrue(c.unknown); self.assertEqual(len(c.client.writes),1)

    def test_wifi_put_negative_or_password_only_raw_state_cannot_resolve_unknown(self):
        from touchscreen.tests.test_ui_sweep import controller_for
        for response in ({'available':False,'reason':'unavailable'},{'password':''},{'enabled':True,'error':'Malformed backend result'},{'ssid':None,'security':'wpa2'},{'ssid':'SYNTHETIC','available':0}):
            c=controller_for('wifi_primary'); c.draft={'ssid':'SYNTHETIC changed'}; c.activate('action:save')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.failure=ApiError(0,'transport','Unknown'); c.activate('apply')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.values['/api/wifi/primary']=response; c.activate('readback')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            self.assertTrue(c.unknown,response); self.assertEqual(c.unknown_target,('wifi_primary',{}))

    def test_delete_all_source_entries_need_valid_identities_even_if_target_absent(self):
        from touchscreen.tests.test_ui_sweep import controller_for
        for entries in ([{}],[{'name':None}],[{'name':''}],[{'name':17}],[{'name':'other','available':False}]):
            c=controller_for('wifi_edit'); c.activate('action:delete')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.failure=ApiError(0,'transport','Unknown'); c.activate('apply')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.values['/api/openwrt/wifi']={'interfaces':entries}; c.activate('readback')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            self.assertTrue(c.unknown,entries)

    def test_valid_empty_collection_is_authoritative_but_negative_envelope_is_not(self):
        from touchscreen.tests.test_ui_sweep import controller_for
        for response,expected in (({'interfaces':[]},False),({'interfaces':[],'available':False},True),({'interfaces':[],'reason':'unavailable'},True)):
            c=controller_for('wifi_edit'); c.activate('action:delete')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.failure=ApiError(0,'transport','Unknown'); c.activate('apply')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.values['/api/openwrt/wifi']=response; c.activate('readback')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            self.assertEqual(c.unknown,expected,response)

    def test_actual_form_sources_remain_positive_without_hidden_required_fields(self):
        from touchscreen.tests.test_ui_sweep import controller_for
        pages=('wifi_primary','wifi_edit','radio_edit','interface_edit','dhcp_edit','system_edit','settings','presentation','firewall_defaults_edit','firewall_zones_edit','firewall_forwardings_edit','firewall_rules_edit','firewall_redirects_edit')
        for page in pages:
            c=controller_for(page)
            if page=='interface_edit':
                for key in ('ipaddr','netmask','gateway'): c.client.values['/api/openwrt/interfaces']['interfaces'][0].pop(key,None)
                c.open(page,{'name':'wan'},force=True)
                while c.worker.jobs: c.worker.run(); c.poll(10)
            self.assertTrue(c._usable_readback(c.loaded),page)

    def test_radio_and_dhcp_synthesized_switches_are_not_raw_evidence(self):
        from touchscreen.tests.test_ui_sweep import controller_for
        for page,path,response,context in (('radio_edit','/api/openwrt/wifi',{'radios':[{'name':'radio0'}]},{'name':'radio0'}),('dhcp_edit','/api/openwrt/dhcp',[{'name':'lan'}],{'name':'lan'})):
            c=controller_for(page); c.client.values[path]=response; c.open(page,context,force=True)
            while c.worker.jobs: c.worker.run(); c.poll(10)
            self.assertFalse(c._usable_readback(c.loaded),page)

    def test_block_command_readback_binds_submitted_ap_not_navigation_context(self):
        self.client.values['/api/touchscreen/blocked?ap=ap0']={'available':True,'ap':'ap0','aps':['ap0','guest'],'policy':'deny','macs':[]}
        self.client.values['/api/touchscreen/blocked?ap=guest']={'available':True,'ap':'guest','aps':['ap0','guest'],'policy':'deny','macs':[]}
        self.c.open('blocked',{'ap':'ap0'}); self.settle()
        self.c.draft={'ap':'guest','mac':'02:00:00:00:00:01','blocked':True}; self.review()
        self.assertEqual(self.c.pending['body']['ap'],'guest')
        self.client.failure=ApiError(0,'transport','Unknown'); self.c.activate('apply'); self.settle()
        self.assertEqual(self.c.unknown_target,('blocked',{'ap':'guest'}))
        self.c.activate('readback'); self.settle(); self.c.activate('readback'); self.settle()
        self.assertFalse(self.c.unknown); self.assertEqual(self.c.loaded['source']['ap'],'guest')

    def test_block_wrong_ap_response_cannot_release_unknown_guard(self):
        self.client.values['/api/touchscreen/blocked?ap=ap0']={'available':True,'ap':'ap0','aps':['ap0','guest'],'policy':'deny','macs':[]}
        self.c.open('blocked',{'ap':'ap0'}); self.settle(); self.c.draft={'ap':'guest','mac':'02:00:00:00:00:01','blocked':True}; self.review()
        self.client.failure=ApiError(0,'transport','Unknown'); self.c.activate('apply'); self.settle()
        # Server incorrectly returns ap0 even when the actual command target is guest.
        self.client.values['/api/touchscreen/blocked?ap=guest']=self.client.values['/api/touchscreen/blocked?ap=ap0']
        self.c.activate('readback'); self.settle(); self.c.activate('readback'); self.settle()
        self.assertTrue(self.c.unknown)

    def test_primary_alias_switch_requires_actual_original_ap_readback(self):
        from touchscreen.tests.test_ui_sweep import controller_for
        for actual_present in (True,False):
            c=controller_for('wifi_primary'); c.client.values['/api/wifi/primary']['name']='ap0'; c.open('wifi_primary',force=True)
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.draft={'enabled':False}; c.activate('action:save')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.failure=ApiError(0,'transport','Unknown'); c.activate('apply')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            c.client.values['/api/wifi/primary'].update(name='guest',ssid='SYNTHETIC GUEST',enabled=True)
            if not actual_present: c.client.values['/api/openwrt/wifi']['interfaces']=[]
            c.activate('readback')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            self.assertEqual(c.unknown,not actual_present)
            if actual_present: self.assertEqual(c.loaded['data']['name'],'ap0'); self.assertEqual(c.context,{'name':'ap0'})


class ControllerSessionHTTPTests(unittest.TestCase):
    """Actual snapshot HTTP/session stack with a synthetic in-memory mock password."""
    PASSWORD='SYNTHETIC-HTTP-PASSWORD'
    def setUp(self):
        import sys
        import threading
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
        from m3100ui.app import App
        from m3100ui.config import load
        from m3100ui.httpd import make_server
        from m3100_touch.api import ApiClient
        self.tmp=tempfile.TemporaryDirectory()
        password_path=Path(self.tmp.name)/'synthetic-password'
        password_path.write_text(self.PASSWORD); password_path.chmod(0o600)
        if os.name!='nt' and os.geteuid()!=0:
            self.tmp.cleanup(); self.skipTest('Native HTTP password fixture must be root-owned0600; run this test as root')
        self.app=App(load(None,{'platform':'openwrt','mock':False,'selfheal':False,'state_dir':self.tmp.name,'password_file':str(password_path)}))
        self.server=make_server(self.app,'127.0.0.1',0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.client=ApiClient('http://127.0.0.1:'+str(self.server.server_port),timeout=1)
        self.worker=ManualWorker(); self.c=Controller(self.client,self.worker,now=10)
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2); self.tmp.cleanup()
    def login(self):
        self.client.login(self.PASSWORD); self.c.locked=False
        return '; '.join(cookie.name+'='+cookie.value for cookie in self.client._cookies)
    def retained_authenticated(self,cookie):
        import http.client
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=1)
        try:
            connection.request('GET','/api/session',headers={'Cookie':cookie})
            response=connection.getresponse(); return json.loads(response.read())['authenticated']
        finally: connection.close()
    def settle(self):
        while self.worker.jobs: self.worker.run(); self.c.poll(10)
    def test_queued_numeric_keypad_done_logs_in_through_real_http(self):
        from m3100_touch.__main__ import process_input
        password = '13579'  # Synthetic fixture, independent of device credentials.
        Path(self.app.cfg['password_file']).write_text(password)
        self.c.activate('unlock'); self.c.activate('mode:numeric')
        reader = QueuedTapReader(self.c, ['char:'+digit for digit in password]+['done', 'char:0'])
        self.assertIsNone(process_input(self.c, reader, 11))
        self.assertTrue(reader.closed)  # Done changes to the authenticated scene.
        self.assertEqual(len(self.worker.jobs), 1)
        self.settle()
        self.assertFalse(self.c.locked)
        self.assertEqual(self.c.page, 'menu')
        self.assertTrue(self.client.request('GET', '/api/session')['authenticated'])
    def test_explicit_logout_and_idle_lock_revoke_retained_cookie(self):
        for idle in (False,True):
            old=self.login(); self.c.draft={'password':'SYNTHETIC-draft'}
            if idle: self.c.tick(200)
            else: self.c.activate('action:logout')
            self.assertTrue(self.c.locked); self.assertFalse(self.client.authenticated); self.assertEqual(self.c.draft,{})
            self.assertEqual(list(self.client._cookies),[])
            self.settle(); self.assertFalse(self.retained_authenticated(old))
    def test_delayed_old_logout_cannot_revoke_or_clear_new_login(self):
        old=self.login(); self.c.lock()
        new=self.login(); csrf=self.client.csrf; epoch=self.c.auth_epoch
        self.settle()
        self.assertFalse(self.retained_authenticated(old)); self.assertTrue(self.retained_authenticated(new))
        self.assertTrue(self.client.authenticated); self.assertEqual(self.client.csrf,csrf); self.assertFalse(self.c.locked); self.assertEqual(self.c.auth_epoch,epoch)
    def test_failed_remote_revoke_does_not_delay_or_reverse_local_lock(self):
        self.login(); self.c.lock()
        self.assertTrue(self.c.locked); self.assertFalse(self.client.authenticated)
        # Failure is simulated only on the isolated cleanup, after local lock.
        self.assertTrue(self.worker.jobs)
        token,_=self.worker.jobs.pop(0); self.worker.results.append((token,None,ApiError(0,'transport','Synthetic unavailable')))
        self.c.poll(10); self.assertTrue(self.c.locked); self.assertFalse(self.client.authenticated)

    def test_late_login_cookie_is_revoked_without_clearing_a_new_session(self):
        import threading
        ready,release=threading.Event(),threading.Event()
        route,_=self.app.router.match('POST','/api/login'); original=route.handler; old_cookie=[]
        def delayed(req):
            result=original(req)
            if not ready.is_set():
                old_cookie.append(req.set_cookie.split(';')[0]); ready.set(); release.wait(.8)
            return result
        route.handler=delayed
        worker=ApiWorker(); self.addCleanup(worker.close)
        c=Controller(self.client,worker,now=10); c.mode='working'
        c._submit('login',lambda:(self.client.login(self.PASSWORD),{}))
        try:
            self.assertTrue(ready.wait(.5)); c.lock()
            self.client.login(self.PASSWORD); c.locked=False
            new_cookie='; '.join(cookie.name+'='+cookie.value for cookie in self.client._cookies)
            csrf=self.client.csrf; release.set()
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                c.poll(10)
                if not self.retained_authenticated(old_cookie[0]): break
                time.sleep(.01)
            self.assertFalse(self.retained_authenticated(old_cookie[0])); self.assertTrue(self.retained_authenticated(new_cookie))
            self.assertTrue(self.client.authenticated); self.assertEqual(self.client.csrf,csrf); self.assertFalse(c.locked)
        finally: release.set(); route.handler=original

    def test_stale_401_completion_uses_old_cookie_jar_only(self):
        import threading
        from m3100ui.errors import ApiError as ServerError
        old=self.login(); ready,release=threading.Event(),threading.Event(); errors=[]
        route,_=self.app.router.match('GET','/api/session'); original=route.handler
        def delayed(req):
            if not ready.is_set():
                ready.set(); release.wait(.8); raise ServerError(401,'unauthorized','Synthetic expired old request')
            return original(req)
        route.handler=delayed
        def request():
            try: self.client.request('GET','/api/session')
            except ApiError as error: errors.append(error)
        thread=threading.Thread(target=request); thread.start()
        try:
            self.assertTrue(ready.wait(.5)); self.c.lock(); new=self.login(); csrf=self.client.csrf
            release.set(); thread.join(1); self.settle()
            self.assertEqual(len(errors),1); self.assertTrue(self.client.authenticated); self.assertEqual(self.client.csrf,csrf)
            self.assertFalse(self.retained_authenticated(old)); self.assertTrue(self.retained_authenticated(new))
        finally: release.set(); thread.join(1); route.handler=original

    def test_login_cookie_arriving_before_csrf_body_is_revoked_after_lock(self):
        import threading
        ready,release=threading.Event(),threading.Event(); old_cookie=[]
        opener=self.client._opener; original=opener.open
        def headers_received(*args,**kwargs):
            response=original(*args,**kwargs)  # Real HTTP cookie processed; body not parsed.
            old_cookie.append('; '.join(cookie.name+'='+cookie.value for cookie in self.client._cookies))
            ready.set(); release.wait(.8)
            return response
        worker=ApiWorker(); self.addCleanup(worker.close); c=Controller(self.client,worker,now=10); c.mode='working'
        try:
            with patch.object(opener,'open',side_effect=headers_received):
                c._submit('login',lambda:(self.client.login(self.PASSWORD),{}))
                self.assertTrue(ready.wait(.5)); self.assertTrue(old_cookie[0]); self.assertIsNone(self.client.csrf)
                c.lock(); self.client.login(self.PASSWORD); c.locked=False
                new_cookie='; '.join(cookie.name+'='+cookie.value for cookie in self.client._cookies); csrf=self.client.csrf
                release.set(); deadline=time.monotonic()+1.5
                while time.monotonic()<deadline:
                    c.poll(10)
                    if not self.retained_authenticated(old_cookie[0]): break
                    time.sleep(.01)
                self.assertFalse(self.retained_authenticated(old_cookie[0])); self.assertTrue(self.retained_authenticated(new_cookie))
                self.assertTrue(self.client.authenticated); self.assertEqual(self.client.csrf,csrf); self.assertFalse(c.locked)
        finally: release.set()

