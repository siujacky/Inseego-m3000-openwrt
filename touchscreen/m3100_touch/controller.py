"""Release-only touchscreen controller. All HTTP work runs in one bounded worker.

Only catalog.load/prepare and ApiClient implement the backend contract. UI state,
semantic generations and authentication epochs belong to the foreground thread.
No persistence, hardware commands, credentials discovery or mutation retry exists.
"""
import copy
import queue
import re
import threading
import textwrap
from . import catalog
from .api import ApiError
from .input import Gesture
from .keyboard import Keyboard
from .render import render, ROW_CAPACITY, HOME_CAPACITY
from .i18n import text


def _seconds(value, default, low, high):
    try: return max(low,min(high,int(value))) if not isinstance(value,bool) else default
    except (ValueError,TypeError,OverflowError): return default


def _error_text(error):
    lines=[str(error.message)]
    def append(value,prefix='',depth=0):
        if depth>3 or len(lines)>=100: return
        if isinstance(value,dict):
            for key,item in list(value.items())[:30]: append(item,(prefix+' / ' if prefix else '')+str(key),depth+1)
        elif isinstance(value,list):
            for item in value[:20]: append(item,prefix,depth+1)
        elif value is not None:
            lines.append(prefix+': '+('On' if value is True else 'Off' if value is False else str(value)[:1024]))
    append(error.details)
    return '\n'.join(lines)[:16384]


class ApiWorker:
    def __init__(self):
        self.jobs = queue.Queue(2)
        self.results = queue.Queue(4)
        self.cleanups = queue.Queue(2)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name='touch-api', daemon=True)
        self.thread.start()

    def submit(self, token, operation):
        try: self.jobs.put_nowait((token, operation)); return True
        except queue.Full: return False

    def _run(self):
        while not self.stop.is_set():
            try: token,operation=self.cleanups.get_nowait()
            except queue.Empty:
                try: token, operation = self.jobs.get(timeout=.1)
                except queue.Empty: continue
            try: result, error = operation(), None
            except Exception as exc: result, error = None, exc
            operation = None
            while not self.stop.is_set():
                try: self.results.put((token, result, error), timeout=.1); break
                except queue.Full: continue
            result = error = None

    def submit_cleanup(self,token,operation):
        try: self.cleanups.put_nowait((token,operation)); return True
        except queue.Full: return False

    def drain(self):
        out = []
        for _ in range(4):
            try: out.append(self.results.get_nowait())
            except queue.Empty: break
        return out

    def close(self):
        self.stop.set()
        while True:
            try: self.jobs.get_nowait()
            except queue.Empty: break
        while True:
            try:
                token,operation=self.cleanups.get_nowait()
                if hasattr(operation,'discard'): operation.discard()
            except queue.Empty: break


class Controller:
    def __init__(self, client, worker=None, now=0):
        self.client, self.worker = client, worker or ApiWorker()
        self.page, self.context, self.history = 'home', {}, []
        self.loaded = catalog.load(client, 'home')
        self.locked, self.auth_epoch, self.page_epoch = True, 0, 0
        self.generation, self.contact_generation = 0, None
        self.gesture, self.hitboxes = Gesture(), []
        self.mode, self.draft, self.pending = 'page', {}, None
        self.keyboard, self.edit_field, self.keyboard_purpose = None, None, None
        self.row_page, self.message, self.message_page = 0, '', 0
        self.detail, self.choices, self.choice_page = '', [], 0
        self.return_mode, self.discard_target = 'page', None
        self.language, self.theme = 'en', 'dark'
        self.idle_timeout, self.display_timeout = 120, 300
        self.now, self.last_touch = now, now
        self.blanked, self.wake_contact = False, False
        self.home_observation = ''
        self.mutation_inflight, self.unknown = False, False
        self.unknown_target, self.reauth_password = None, None
        self.mutation_target = None
        self.mutation_record, self.pending_action = None, None
        self.busy, self.job_sequence = False, 0
        self.revocation_failed=False
        self.dirty = True

    def _semantic(self):
        self.generation += 1
        self.gesture.feed({'kind':'cancel', 'time':self.now}, [])
        self.contact_generation = None
        self.hitboxes = []
        self.dirty = True

    def _submit(self, kind, operation):
        self.job_sequence += 1
        token = (kind, self.auth_epoch, self.page_epoch, self.job_sequence, self.generation)
        def guarded():
            if token[1:3] != (self.auth_epoch,self.page_epoch) or (kind!='write' and token[4]!=self.generation):
                raise ApiError(401,'cancelled','Request cancelled before dispatch')
            return operation()
        if not self.worker.submit(token, guarded):
            self.show_message('The API is busy. Wait, then reload.'); return False
        self.busy = True
        return True

    def lock(self):
        if self.mutation_inflight:
            self.unknown, self.unknown_target = True, self.mutation_target
        self.auth_epoch += 1
        self.page_epoch += 1
        detach=getattr(self.client,'detach_session',None)
        cleanup=detach() if detach is not None else None
        if detach is None: self.client.clear_session()
        self.locked, self.page, self.context = True, 'home', {}
        self.loaded = catalog.load(self.client, 'home')
        self.draft, self.pending, self.history = {}, None, []
        self.keyboard, self.edit_field, self.keyboard_purpose = None, None, None
        self.reauth_password = None
        self.mode, self.message, self.detail = 'page', '', ''
        self.busy = False
        self._semantic()
        if cleanup is not None: self._queue_revocation(cleanup)

    def _queue_revocation(self,cleanup):
        token=('revoke',self.auth_epoch,self.page_epoch,0,self.generation)
        submit=getattr(self.worker,'submit_cleanup',self.worker.submit)
        if not submit(token,cleanup):
            self.revocation_failed=True
            if hasattr(cleanup,'discard'): cleanup.discard()

    def open(self, page, context=None, remember=True, force=False):
        if page not in catalog.PAGES: self.show_message('This page is unavailable.'); return
        if self.draft and not force:
            self.discard_target = (page, context or {}, remember)
            self.mode = 'discard'; self._semantic(); return
        spec = catalog.PAGES[page]
        if self.locked and 'endpoint' in spec or self.locked and page == 'explorer_read':
            self.discard_target = (page, context or {}, remember)
            self.start_keyboard('login'); return
        if remember and page != self.page:
            self.history.append((self.page, dict(self.context)))
            self.history = self.history[-32:]
        elif not remember and self.history and self.history[-1] == (page,dict(context or {})):
            self.history.pop()
        self.page, self.context = page, dict(context or {})
        self.page_epoch += 1
        self.mode, self.draft, self.pending = 'page', {}, None
        self.reauth_password = None
        self.keyboard, self.edit_field = None, None
        self.loaded = dict(id=page, title=spec['title'], kind='list', rows=[], fields=[], actions=[], data={}, context=self.context)
        self.row_page, self.message, self.detail = 0, '', ''
        self.busy = False
        self._semantic()
        if 'endpoint' not in spec and page != 'explorer_read':
            self.accept_loaded(catalog.load(self.client, page, self.context)); return
        ctx = dict(self.context)
        self._submit('load', lambda: catalog.load(self.client, page, ctx))

    def accept_loaded(self, loaded):
        self.loaded = loaded
        self.busy = False
        self.row_page = min(self.row_page, max(0, (len(self._rows())-1)//ROW_CAPACITY))
        if loaded['id'] == 'presentation': self._preferences(loaded.get('data', {}))
        if loaded['id'] in ('session', 'settings'):
            data = loaded.get('data', {})
            data=data if isinstance(data,dict) else {}
            self.idle_timeout = _seconds(data.get('idle_timeout_s', data.get('session_idle_s', self.idle_timeout)),self.idle_timeout,120,86400)
        self._semantic()

    def _preferences(self, values):
        if not isinstance(values,dict): return
        self.language = values.get('language', self.language) if values.get('language', self.language) in ('en','es') else 'en'
        self.theme = values.get('theme', self.theme) if values.get('theme', self.theme) in ('light','dark','system') else 'dark'
        self.display_timeout = _seconds(values.get('display_timeout_s'),self.display_timeout,30,3600)

    def set_observations(self, observations):
        """Allowlisted telemetry only: voltage is an observation, never capacity."""
        battery=observations.get('battery',{})
        voltage=battery.get('voltage_uv') if isinstance(battery,dict) else None
        voltage_label='Battery unknown'
        if isinstance(voltage,(int,float)) and not isinstance(voltage,bool) and 2000000<=voltage<=6000000:
            voltage_label='%.3f V obs'%(voltage/1000000)
        modem=observations.get('modem',{})
        state=modem.get('remoteproc_state') if isinstance(modem,dict) else None
        modem_label='offline' if state=='offline' else 'proc running' if state=='running' else 'unknown'
        value='RAM | '+voltage_label+' | '+modem_label
        if value!=self.home_observation:
            self.home_observation=value
            if self.page=='home' and self.mode=='page' and not self.blanked: self._semantic()

    def show_message(self, message, title='Error'):
        self.message = str(message)[:16384]
        self.message_title, self.message_page = title, 0
        self.mode = 'message'; self._semantic()

    def start_keyboard(self, purpose, field=None):
        self.keyboard_purpose, self.edit_field = purpose, field
        current = '' if purpose in ('login','reauth') else self.draft.get(field['key'], self.loaded.get('data', {}).get(field['key'], ''))
        if isinstance(current, list): current = ', '.join(current)
        if current is None: current = ''
        self.keyboard = Keyboard(str(current), secret=purpose in ('login','reauth') or field.get('secret', False), max_length=256 if field is None else field.get('max_length', 4096 if field['type']=='list' else 256))
        self.mode = 'keyboard'; self._semantic()

    def _rows(self):
        rows = []
        data = self.loaded.get('data', {})
        merged = dict(data) if isinstance(data, dict) else {}
        merged.update(self.draft)
        for field in self.loaded.get('fields', []):
            if not catalog.visible(field, merged): continue
            value = merged.get(field['key'], '')
            if field.get('secret'): value = '********' if value else 'Saved; blank keeps existing'
            elif isinstance(value, bool): value = 'On' if value else 'Off'
            elif isinstance(value, list): value = ', '.join(str(x) for x in value)
            rows.append(dict(id='field:'+field['key'], label=field['label'], value=value, kind='field', enabled=not bool(self.loaded.get('reason')), secret=field.get('secret',False)))
        for i,row in enumerate(self.loaded.get('rows', [])):
            row = dict(row)
            if row.get('kind') == 'text':
                row.update(id='detail:'+str(i),kind='nav',enabled=True)
            rows.append(row)
        for action in self.loaded.get('actions', []):
            if action['id'] != 'save': rows.append(dict(action, id='action:'+action['id'], kind='action'))
        if not rows and not self.busy: rows.append(dict(id='detail:empty',label='Information', value='No entries reported',kind='text'))
        return rows

    def _review(self, action):
        if self.busy or self.mutation_inflight or self.unknown: return
        baseline, draft, page, ctx = copy.deepcopy(self.loaded), dict(self.draft), self.page, dict(self.context)
        self.pending_action=action
        self.mode = 'working'; self._semantic()
        def operation():
            fresh = catalog.load(self.client, page, ctx)
            if not ctx.get('create'):
                for key in draft:
                    if baseline.get('data', {}).get(key) != fresh.get('data', {}).get(key):
                        raise ValueError('A setting changed in another session. Reload before applying.')
            return fresh, catalog.prepare(page, fresh, draft, action)
        self._submit('review', operation)

    def _apply(self):
        if self.mode != 'confirm' or self.pending is None or self.mutation_inflight or self.unknown or self.busy: return
        if self.pending['tier'] >= 3 and self.reauth_password is None:
            self.start_keyboard('reauth'); return
        password, self.reauth_password = self.reauth_password, None
        transaction, self.pending = self.pending, None
        self.mutation_inflight = True
        self.mutation_target = (self.page,dict(self.context))
        actual_ap=None
        if self.page=='blocked': actual_ap=transaction['body'].get('ap')
        elif self.page=='wifi_primary': actual_ap=self.loaded.get('source',{}).get('name')
        valid_ap=isinstance(actual_ap,str) and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,63}',actual_ap)
        if self.page=='blocked' and valid_ap: self.mutation_target=('blocked',{'ap':actual_ap})
        self.mutation_record=dict(origin=copy.deepcopy(self.mutation_target),method=transaction['method'],action=self.pending_action)
        if self.page in ('blocked','wifi_primary'): self.mutation_record['ap']=actual_ap if valid_ap else None
        if self.context.get('create'):
            identity_key='section' if catalog.PAGES[self.page].get('firewall') else 'name'
            self.mutation_record['identity']=(identity_key,transaction['body'].get(identity_key))
        self.mode = 'working'; self._semantic()
        # One task, one request. No retry, including 428 and transport errors.
        def operation():
            try: return self.client.request(transaction['method'], transaction['path'], transaction['body'], confirm=True, password=password)
            finally: transaction.clear()
        if not self._submit('write', operation): self.mutation_inflight=False

    @staticmethod
    def _negative_state(value):
        if not isinstance(value,dict): return False
        return 'available' in value and type(value['available']) is not bool or value.get('available') is False or value.get('ok') is False or bool(value.get('reason')) or bool(value.get('error'))

    @classmethod
    def _source_entries(cls, source, key, identity):
        """Only complete, identifiable raw collections can establish absence."""
        if cls._negative_state(source): return None
        if isinstance(source,dict) and cls._negative_state(source.get('runtime')): return None
        entries=source.get(key) if isinstance(source,dict) and key else source if key is None else None
        if not isinstance(entries,list): return None
        pattern=r'[A-Za-z0-9_]{1,64}' if identity=='section' else r'[A-Za-z0-9_][A-Za-z0-9_-]{0,63}'
        seen=set()
        for row in entries:
            value=row.get(identity) if isinstance(row,dict) else None
            if cls._negative_state(row) or not isinstance(value,str) or not re.fullmatch(pattern,value) or value in seen: return None
            seen.add(value)
        return entries

    @classmethod
    def _usable_readback(cls, model):
        """Validate observed raw fields, never defaults synthesized by catalog."""
        source=model.get('source'); page=model.get('id'); ctx=model.get('context',{})
        if model.get('reason') or cls._negative_state(source) or page not in catalog.PAGES: return False
        if isinstance(source,dict) and cls._negative_state(source.get('runtime')): return False
        spec=catalog.PAGES[page]
        raw=source
        if spec.get('firewall'):
            kind=spec['firewall']
            if kind=='defaults': row=source.get('defaults') if isinstance(source,dict) else None
            else:
                rows=cls._source_entries(source,kind,'section')
                row=next((r for r in rows if r['section']==ctx.get('section')),None) if rows is not None else None
            if not isinstance(row,dict) or cls._negative_state(row): return False
            raw=row.get('options')
        elif page in ('wifi_edit','radio_edit','interface_edit','dhcp_edit','service'):
            key={'wifi_edit':'interfaces','radio_edit':'radios','interface_edit':'interfaces'}.get(page)
            rows=cls._source_entries(source,key,'name')
            raw=next((r for r in rows if r['name']==ctx.get('name')),None) if rows is not None else None
        elif page=='system_edit': raw=source.get('config') if isinstance(source,dict) else None
        elif page=='blocked':
            return isinstance(source,dict) and source.get('available') is True and isinstance(source.get('ap'),str) and bool(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,63}',source['ap'])) and source.get('policy') in ('disabled','deny') and isinstance(source.get('macs'),list) and all(isinstance(mac,str) and re.fullmatch(r'[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}',mac) for mac in source['macs'])
        if not isinstance(raw,dict) or cls._negative_state(raw): return False
        observed=0
        for field in model.get('fields',[]):
            if field.get('secret') or not catalog.visible(field,model.get('data',{})): continue
            key=field['key']
            # These aliases are actual UCI observations, unlike inserted defaults.
            alias='disabled' if page=='radio_edit' and key=='enabled' else 'syn_flood' if key=='synflood_protect' else key
            present=key in raw or alias in raw
            value=raw.get(key,raw.get(alias))
            if not present:
                if field.get('required'): return False
                continue
            kind=field['type']
            if kind=='switch': valid=type(value) is bool or value in ('0','1')
            elif kind=='number':
                try: valid=not isinstance(value,bool) and str(int(str(value)))==str(value) and field['min']<=int(str(value))<=field['max']
                except (ValueError,TypeError): valid=False
            elif kind=='select': valid=(' '.join(value) if isinstance(value,list) and all(isinstance(v,str) for v in value) else value) in field.get('choices',[])
            elif kind=='list': valid=isinstance(value,str) or isinstance(value,list) and all(isinstance(v,str) for v in value)
            else: valid=isinstance(value,str)
            if not valid or field.get('required') and value in ('',[]): return False
            observed+=1
        return observed>0

    def _load_readback(self, origin, record):
        """Observe typed resource state; templates/list reads cannot prove actions."""
        page,ctx=origin
        if page=='wifi_primary' and record:
            ap=record.get('ap')
            model=catalog.load(self.client,'wifi_edit',{'name':ap}) if ap else None
            resolved=bool(ap) and record['method']=='PUT' and record['action']=='save' and self._usable_readback(model)
            return dict(origin=origin,model=model,resolved=resolved,reason='The original primary access point must be observed by its saved identity.')
        model=catalog.load(self.client,page,ctx) if not ctx.get('create') and (not record or record['method']!='DELETE') else None
        reason='No usable configuration was returned.'
        resolved=False
        if record and record['method']=='DELETE':
            parent=next((pid for pid,spec in catalog.PAGES.items() if spec.get('editor')==page and spec.get('endpoint')==catalog.PAGES[page].get('endpoint')),None)
            if parent:
                model=catalog.load(self.client,parent)
                key=catalog.PAGES[parent].get('collection')
                identity='section' if catalog.PAGES[page].get('firewall') else 'name'
                rows=self._source_entries(model.get('source'),key,identity)
                present=rows is not None and any(row[identity]==ctx.get(identity) for row in rows)
                resolved=rows is not None and not model.get('reason') and not present
                reason='The original resource is still present or its collection is unavailable.'
        elif ctx.get('create'):
            identity=(record or {}).get('identity')
            if identity and identity[1]:
                actual_ctx={identity[0]:identity[1]}
                model=catalog.load(self.client,page,actual_ctx)
                resolved=self._usable_readback(model) and model.get('context')==actual_ctx
            reason='Creation remains unconfirmed; an actual new resource must be observed.'
        elif record and (record['method']!='PUT' or record['action']!='save'):
            reason='This action has no conclusive outcome readback. Its result remains unknown and it will not be retried.'
        else:
            resolved=self._usable_readback(model) and (model.get('id'),model.get('context',{}))==origin
            if page=='blocked' and record:
                resolved=resolved and bool(record.get('ap')) and model.get('source',{}).get('ap')==record['ap']
        if model and model.get('reason'): reason=model['reason']
        return dict(origin=origin,model=model,resolved=resolved,reason=reason)

    def poll(self, now):
        self.now = now
        for token,result,error in self.worker.drain():
            kind, auth, page, sequence, generation = token
            if error and getattr(error,'revocation',None) is not None:
                self._queue_revocation(error.revocation)
                error.revocation=None
            if kind=='revoke':
                if error is not None:
                    self.revocation_failed=True
                    if self.locked: self.dirty=True
                continue  # Cleanup never changes active auth/page/draft state.
            if kind == 'write':
                self.mutation_inflight = False
                if error and (not isinstance(error,ApiError) or error.status in (0,408) or error.status>=500):
                    self.unknown,self.unknown_target=True,self.mutation_target
            if error and isinstance(error,ApiError) and error.status==401 and error.code!='cancelled' and auth==self.auth_epoch:
                self.lock()
                message = 'Incorrect password. Try again.' if kind=='login' and error.code=='bad_password' else 'Session ended. Unlock again.'
                self.show_message(message,'Admin access'); continue
            if auth != self.auth_epoch or page != self.page_epoch: continue
            if kind != 'write' and generation != self.generation:
                if sequence == self.job_sequence: self.busy = False
                continue
            self.busy = False
            if error:
                if isinstance(error, ApiError) and error.status == 401:
                    self.lock(); self.show_message('Session ended. Unlock again.', 'Admin access'); continue
                if kind == 'write':
                    self.unknown = not isinstance(error, ApiError) or error.status in (0, 408) or error.status>=500
                    if self.unknown: self.unknown_target=self.mutation_target
                    self.pending = None
                    if self.unknown: self.show_message('Change outcome unknown. Do not repeat it. Select Read back to inspect current state.\n'+(_error_text(error) if isinstance(error,ApiError) else 'Backend did not return a complete result.')); continue
                self.show_message(_error_text(error) if isinstance(error,ApiError) else str(error) if isinstance(error,ValueError) else 'Backend unavailable. Reload or return Home.')
                continue
            if kind == 'login':
                if not result[0].get('authenticated'):
                    self.lock(); self.show_message('Authentication was not accepted.'); continue
                self.locked = False
                self.last_touch = now
                self.idle_timeout = _seconds(result[0].get('idle_timeout_s'),120,120,86400)
                self._preferences(result[1])
                target, self.discard_target = self.discard_target, None
                self.open(*(target[:2] if target else ('menu',{})), remember=target[2] if target else True, force=True)
            elif kind in ('load','reload','readback'):
                if kind == 'readback':
                    origin=self.unknown_target or (self.page,self.context)
                    if not result.get('resolved') or result.get('origin')!=origin or not result.get('model'):
                        self.show_message('Read back could not observe the original state. The previous change remains unresolved.\n'+str(result.get('reason') or 'No usable configuration was returned.'))
                        continue
                    result=result['model']
                    self.unknown = False; self.unknown_target=None; self.draft = {}
                    self.page,self.context=result['id'],dict(result.get('context',{}))
                elif kind == 'reload' and self.draft:
                    # Refresh other state without silently rebasing dirty edits over
                    # another client's changes; review must still detect conflicts.
                    old=self.loaded.get('data',{})
                    fresh=result.get('data',{})
                    if isinstance(old,dict) and isinstance(fresh,dict):
                        for key in self.draft:
                            if key in old: fresh[key]=copy.deepcopy(old[key])
                            else: fresh.pop(key,None)
                if kind in ('reload','readback'):
                    self.mode='page'; self.message=''; self.detail=''; self.message_page=0
                self.accept_loaded(result)
            elif kind == 'review':
                self.loaded, self.pending = result
                self.mode, self.row_page = 'confirm', 0; self._semantic()
            elif kind == 'write':
                self.draft = {}
                self.show_message('Request accepted. Reload to verify current saved/runtime state.', 'Information')

    def tick(self, now):
        self.now = now
        self.gesture.tick(now)
        if now-self.last_touch >= self.idle_timeout and (not self.locked or self.keyboard is not None or self.busy or self.draft or self.pending): self.lock()
        if now-self.last_touch >= self.display_timeout and not self.blanked:
            self.blanked=True; self._semantic()
        self.poll(now)

    def feed(self, event):
        self.now = event.get('time', self.now)
        if event['kind'] in ('down','move','up'): self.last_touch = self.now
        if self.blanked and event['kind'] == 'down':
            self.blanked=False; self.wake_contact=True; self._semantic(); return
        if self.wake_contact:
            if event['kind'] in ('up','cancel'): self.wake_contact=False
            return
        if event['kind'] == 'down': self.contact_generation = self.generation
        if event['kind'] == 'up' and self.contact_generation != self.generation:
            self.gesture.feed({'kind':'cancel','time':self.now}, []); return
        actions = self.gesture.feed(event, self.hitboxes)
        self.dirty = True
        for action in actions: self.activate(action)

    def input_fenced(self):
        """Runtime closed the old FD; its replacement quarantines held contacts.

        That reader, rather than a future synthetic UP, now consumes the wake
        contact. Pure-controller users retain wake_contact until their actual UP.
        """
        self.wake_contact=False
        self.contact_generation=None
        self.gesture.feed({'kind':'cancel','time':self.now},[])

    def activate(self, identity):
        if self.blanked: return
        if identity == 'lock': self.lock(); return
        if self.mode == 'keyboard':
            previous_page = self.keyboard.page
            answer = self.keyboard.press(identity)
            self.dirty = True
            if answer is None:
                # Editing text does not change the keys/hitboxes. Keep queued
                # completed taps; only a new key layout invalidates that input.
                if self.keyboard.page != previous_page: self._semantic()
                return
            keyboard, purpose, field = self.keyboard, self.keyboard_purpose, self.edit_field
            self.keyboard, self.edit_field, self.keyboard_purpose = None,None,None
            self.mode = 'confirm' if purpose=='reauth' else 'page'
            self._semantic()
            if answer == 'cancel': return
            value, keyboard.text = keyboard.text, ''
            if purpose == 'login':
                self.mode='working'
                def operation():
                    info=self.client.login(value)
                    preferences=catalog.load(self.client,'presentation')['data']
                    return info,preferences
                self._submit('login',operation)
            elif purpose == 'reauth': self.reauth_password=value; self._semantic()
            else:
                self.draft[field['key']] = value
                self._semantic()
            return
        if self.mode == 'discard':
            if identity == 'cancel': self.mode='page'; self._semantic()
            elif identity == 'discard':
                target,self.discard_target=self.discard_target,None
                self.open(target[0],target[1],remember=target[2],force=True)
            return
        if identity == 'unlock': self.start_keyboard('login'); return
        if identity in ('home','back'):
            self.reauth_password=None
            if self.mode in ('detail','choice','message','confirm'):
                if identity=='back': self.mode='page'; self.pending=None; self.message=''; self.detail=''; self._semantic(); return
            if identity=='home': self.open('home'); return
            if self.history:
                target=self.history[-1]; self.open(*target,remember=False)
            else: self.open('home',remember=False)
            return
        if identity in ('reload','readback'):
            if self.busy: return
            if identity=='readback' and self.mutation_inflight: return
            if identity=='readback' and self.unknown_target and (self.page,self.context)!=self.unknown_target:
                target=self.unknown_target
                self.open(target[0],target[1],force=True)
                return
            self.pending=None; self.page_epoch+=1; self._semantic()
            page,ctx=self.page,dict(self.context)
            if identity=='readback':
                origin=copy.deepcopy(self.unknown_target or (page,ctx))
                record=copy.deepcopy(self.mutation_record)
                self._submit('readback',lambda:self._load_readback(origin,record))
            else: self._submit('reload',lambda:catalog.load(self.client,page,ctx))
            return
        if identity in ('next','prev','swipe:left','swipe:right','swipe:up','swipe:down'):
            delta=1 if identity in ('next','swipe:left','swipe:up') else -1
            if self.mode in ('message','detail'): self.message_page=max(0,min(len(self._message_chunks())-1,self.message_page+delta))
            elif self.mode=='choice': self.choice_page=max(0,min((len(self.choices)-1)//3,self.choice_page+delta))
            else: self.row_page=max(0,min((len(self._confirm_rows() if self.mode=='confirm' else self._rows())-1)//(6 if self.page=='home' else 3),self.row_page+delta))
            self._semantic(); return
        if self.mode=='message' or self.mode=='working': return
        if self.mode=='confirm':
            if identity=='apply': self._apply()
            elif identity=='cancel': self.mode='page'; self.pending=None; self.reauth_password=None; self._semantic()
            return
        if self.mode=='choice':
            if identity.startswith('choice:'):
                self.draft[self.edit_field['key']]=self.choices[int(identity.split(':')[1])]
                self.edit_field=None; self.mode='page'; self._semantic()
            return
        if identity.startswith('field:'):
            field=next((f for f in self.loaded.get('fields',[]) if f['key']==identity[6:]),None)
            if field is None or self.loaded.get('reason'): return
            merged=dict(self.loaded.get('data',{})); merged.update(self.draft)
            if not catalog.visible(field,merged): return
            if field['type']=='switch': self.draft[field['key']]=not bool(merged.get(field['key'])); self._semantic()
            elif field['type']=='select': self.edit_field=field; self.choices=field['choices']; self.choice_page=0; self.mode='choice'; self._semantic()
            else: self.start_keyboard('field',field)
            return
        if identity.startswith('detail:'):
            try:
                row=self.loaded['rows'][int(identity[7:])]
                self.detail=str(row.get('label',''))+'\n'+str(row.get('value',''))
            except (ValueError,IndexError): self.detail='No entries reported'
            self.mode='detail'; self.message_page=0; self._semantic(); return
        if identity.startswith('action:'):
            if identity=='action:logout': self.lock()
            else: self._review(identity[7:])
            return
        row=next((r for r in self.loaded.get('rows',[]) if r.get('id')==identity and r.get('enabled',True)),None)
        if row and row.get('target'): self.open(row['target'],row.get('context'))

    def _confirm_rows(self):
        rows=[]
        for line in (self.pending or {}).get('summary',[]):
            lines=textwrap.wrap(text(line,self.language),33) or ['']
            for start in range(0,len(lines),2):
                rows.append(dict(id='summary:'+str(len(rows)),label='\n'.join(lines[start:start+2]),kind='text'))
        return rows

    def _message_chunks(self):
        value = self.detail if self.mode=='detail' else self.message
        lines=[]
        for paragraph in text(value,self.language).split('\n'):
            lines.extend(textwrap.wrap(paragraph,34,replace_whitespace=False) or [''])
        return ['\n'.join(lines[i:i+8]) for i in range(0,len(lines),8)] or ['']

    def view(self):
        def control(id,label,enabled=True): return dict(id=id,label=text(label,self.language),enabled=enabled)
        common=dict(theme=self.theme,pressed=self.gesture.pressed)
        if self.blanked: return dict(common,kind='message',title='',message='',footer=[])
        if self.mode=='keyboard':
            out=self.keyboard.view()
            out['title']=text(self.edit_field['label'] if self.edit_field else 'Admin access',self.language)
            for group in ('keys','tabs','footer'):
                out[group]=[dict(row,label=text(row['label'],self.language)) for row in out.get(group,[])]
            return dict(out,**common)
        footer=[control('back','Back'),control('home','Home')]
        if self.mode in ('message','detail'):
            chunks=self._message_chunks()
            if len(chunks)>1: footer += [control('prev','Prev',self.message_page>0),control('next','Next',self.message_page<len(chunks)-1)]
            footer.append(control('readback' if self.unknown else 'reload','Read back' if self.unknown else 'Reload',not self.busy and not (self.unknown and self.mutation_inflight)))
            return dict(common,kind='message',title=text('Information' if self.mode=='detail' else getattr(self,'message_title','Error'),self.language),message=chunks[self.message_page],footer=footer,status='%s/%s'%(self.message_page+1,len(chunks)))
        if self.mode=='discard': return dict(common,kind='confirm',title=text('Discard changes?',self.language),message=text('Keep editing',self.language),footer=[control('cancel','Cancel'),control('discard','Discard')])
        if self.mode=='working': return dict(common,kind='message',title=text('Working',self.language),message=text('Loading',self.language),footer=[control('home','Home'),control('lock','Lock')])
        if self.mode=='choice':
            rows=[dict(id='choice:'+str(i),label=str(value) if value!='' else '(empty)',kind='action') for i,value in enumerate(self.choices)]
            index=self.choice_page; title=self.edit_field['label']; capacity=3
        elif self.mode=='confirm':
            rows=self._confirm_rows(); index=self.row_page; title='Confirm change'; capacity=3
        else:
            rows=self._rows(); index=self.row_page; title=self.loaded.get('title',self.page); capacity=HOME_CAPACITY if self.page=='home' else ROW_CAPACITY
        pages=max(1,(len(rows)+capacity-1)//capacity)
        if pages>1: footer += [control('prev','Prev',index>0),control('next','Next',index<pages-1)]
        if self.mode=='confirm': footer=[control('cancel','Cancel')]+footer[2:]+[control('apply','Apply',not self.busy and not self.mutation_inflight and not self.unknown)]
        elif self.mode=='page':
            if self.page=='home': footer=[control('unlock' if self.locked else 'lock','Unlock' if self.locked else 'Lock')]
            elif any(a['id']=='save' for a in self.loaded.get('actions',[])):
                footer.append(control('action:save','Review',not self.busy and not self.mutation_inflight and not self.unknown))
            elif len(footer)<5: footer.append(control('reload','Reload',not self.busy))
            if self.unknown and not self.locked:
                footer=footer[:4]+[control('readback','Read back',not self.busy and not self.mutation_inflight)]
        visible_rows=[]
        for row in rows[index*capacity:(index+1)*capacity]:
            row=dict(row); row['label']=text(row.get('label',''),self.language)
            if self.page=='home' and row.get('target')=='credentials': row['label']=text('Wi-Fi key',self.language)
            row['value']=text(row.get('value',''),self.language)
            visible_rows.append(row)
        status='Loading' if self.busy else 'RAM session; unlock settings' if self.locked else 'RAM session; changes reset at reboot' if self.page=='home' else '%s/%s'%(index+1,pages)
        if self.page=='home' and self.home_observation: status=self.home_observation
        if self.locked and self.revocation_failed: status='Locked; server logout unconfirmed'
        return dict(common,kind='home' if self.page=='home' and self.mode=='page' else 'confirm' if self.mode=='confirm' else self.loaded.get('kind','list'),title=text(title,self.language),rows=visible_rows,footer=footer,status=text(status,self.language))

    def render(self):
        frame,self.hitboxes=render(self.view())
        self.dirty=False
        return frame,self.hitboxes
