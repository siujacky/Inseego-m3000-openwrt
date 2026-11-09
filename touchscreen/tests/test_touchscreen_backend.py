import pathlib
import os
import stat
import sys
import tempfile
import types
import unittest
import threading
from unittest.mock import Mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'backend'))
from m3100ui.touchscreen_api import register
from m3100ui.router import Router
from m3100ui.state import StateStore
from m3100ui.errors import ApiError

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = types.SimpleNamespace(router=Router(), state=StateStore(self.tmp.name), audit=lambda *a: None)
        register(self.app)
    def tearDown(self): self.tmp.cleanup()
    def call(self, method, path, data=None):
        route, params = self.app.router.match(method, path)
        self.assertTrue(route.auth)
        return route.handler(types.SimpleNamespace(json=data or {}, params=params))
    def test_preferences_validate_and_persist(self):
        self.assertEqual(self.call('GET', '/api/touchscreen/preferences')['language'], 'en')
        self.assertEqual(self.call('PUT', '/api/touchscreen/preferences', {'language': 'es', 'display_timeout_s': 120})['language'], 'es')
        for bad in ({'language': 'xx'}, {'display_timeout_s': 0}, {'screen_brightness': 99}, {'theme': 'evil'}):
            with self.assertRaises(ApiError): self.call('PUT', '/api/touchscreen/preferences', bad)
        self.assertEqual(self.call('GET', '/api/touchscreen/preferences')['display_timeout_s'], 120)
    def test_hardware_actions_honestly_unavailable(self):
        data = self.call('GET', '/api/touchscreen/capabilities')
        for key in ('charging', 'audio', 'reset', 'update', 'power', 'sim_unlock', 'sleep'):
            self.assertFalse(data['functions'][key]['available'])
        with self.assertRaises(ApiError) as caught:
            self.call('POST', '/api/touchscreen/actions/{name}', {})
        self.assertEqual(caught.exception.code, 'unavailable')

    def test_blocklist_preserves_entries_and_refuses_allow_policy(self):
        config = {'radio0': {'.type': 'wifi-device'}, 'ap': {'.type': 'wifi-iface', 'device': 'radio0', 'mode': 'ap', 'macfilter': 'deny', 'maclist': ['02:00:00:00:00:01']}, 'guest': {'.type': 'wifi-iface', 'device': 'radio0', 'mode': 'ap'}, 'sta': {'.type': 'wifi-iface', 'device': 'radio0', 'mode': 'sta'}}
        def configure(package, changes, command):
            for name, values in changes: config[name].update(values)
        native = types.SimpleNamespace(lock=threading.RLock(), config=lambda p: config, configure=Mock(side_effect=configure), ubus=lambda *args: {'changes': []})
        self.app.adapters = types.SimpleNamespace(native=native)
        self.app.require_tier = Mock()
        self.app.cache = types.SimpleNamespace(invalidate=Mock())
        self.assertEqual(self.call('GET', '/api/touchscreen/blocked')['ap'], 'ap')
        self.call('PUT', '/api/touchscreen/blocked', {'mac': '02:00:00:00:00:02', 'blocked': True})
        self.assertEqual(config['ap']['maclist'], ['02:00:00:00:00:01', '02:00:00:00:00:02'])
        self.call('PUT', '/api/touchscreen/blocked', {'ap': 'guest', 'mac': '02:00:00:00:00:03', 'blocked': True})
        self.assertEqual(config['guest']['macfilter'], 'deny')
        before = native.configure.call_count
        for selected in ('sta', 'missing'):
            with self.assertRaises(ApiError): self.call('PUT', '/api/touchscreen/blocked', {'ap': selected, 'mac': '02:00:00:00:00:04', 'blocked': True})
        config['ap']['macfilter'] = 'allow'
        with self.assertRaises(ApiError): self.call('PUT', '/api/touchscreen/blocked', {'mac': '02:00:00:00:00:02', 'blocked': False})
        self.assertEqual(native.configure.call_count, before)

    def test_guest_secret_only_accepts_existing_ap(self):
        config = {'guest': {'.type': 'wifi-iface', 'mode': 'ap', 'ssid': 'Guest', 'key': 'private'}, 'sta': {'.type': 'wifi-iface', 'mode': 'sta', 'key': 'other-private'}}
        self.app.adapters = types.SimpleNamespace(native=types.SimpleNamespace(lock=threading.RLock(), config=lambda p: config))
        route, _ = self.app.router.match('GET', '/api/touchscreen/wifi/guest/secret')
        self.assertTrue(route.auth)
        response = route.handler(types.SimpleNamespace(params={'name': 'guest'}))
        self.assertEqual(response, {'ssid': 'Guest', 'password': 'private'})
        for bad in ('sta', 'missing', '../system'):
            with self.assertRaises(ApiError): route.handler(types.SimpleNamespace(params={'name': bad}))

    def test_real_http_cookie_csrf_login_storage_and_tier(self):
        if os.name != 'nt' and os.geteuid() != 0:
            self.skipTest('Native HTTP auth requires a real root-owned 0600 password fixture; run this POSIX test as root')
        import http.client
        import json
        from m3100ui import config as cfg
        from m3100ui.app import App
        from m3100ui.httpd import make_server
        from m3100_touch.api import ApiClient, ApiError as ClientError
        password = pathlib.Path(self.tmp.name) / 'admin-password'
        password.write_text('test-private-password')
        password.chmod(0o600)
        if os.name != 'nt':
            self.assertEqual(password.stat().st_uid, 0)
            self.assertEqual(stat.S_IMODE(password.stat().st_mode), 0o600)
        app = App(cfg.load(None, {'platform': 'openwrt', 'mock': False, 'selfheal': False, 'password_file': str(password), 'state_dir': self.tmp.name}))
        srv = make_server(app, '127.0.0.1', 0)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            base = 'http://127.0.0.1:' + str(srv.server_port)
            client = ApiClient(base)
            with self.assertRaises(ClientError) as caught: client.request('GET', '/api/touchscreen/preferences')
            self.assertEqual(caught.exception.status, 401)
            client.login('test-private-password')
            self.assertTrue(list(client._cookies))
            client.request('PUT', '/api/touchscreen/preferences', {'language': 'es'})
            self.assertEqual(client.request('GET', '/api/touchscreen/preferences')['language'], 'es')
            saved = client.csrf
            client.csrf = None
            with self.assertRaises(ClientError) as caught: client.request('PUT', '/api/touchscreen/preferences', {'language': 'en'})
            self.assertEqual(caught.exception.status, 403)
            client.csrf = saved
            with self.assertRaises(ClientError) as caught: client.request('PUT', '/api/touchscreen/blocked', {'mac': '02:00:00:00:00:01', 'blocked': True})
            self.assertEqual(caught.exception.status, 428)
            client.logout()
            with self.assertRaises(ClientError): client.request('GET', '/api/touchscreen/preferences')
        finally:
            srv.shutdown(); srv.server_close(); thread.join(2)

    def test_prepared_wps_transitions_pass_real_snapshot_native_validator(self):
        from m3100_touch import catalog
        from m3100ui.native import register as register_native
        config = {'radio0': {'.type': 'wifi-device'}, 'primary': {'.type': 'wifi-iface', 'device': 'radio0', 'mode': 'ap', 'ssid': 'Main', 'encryption': 'psk2', 'key': 'existing-private', 'wps_pushbutton': '1', 'hidden': '0'}}
        def configure(package, changes, command, **kwargs):
            for name, values in changes: config[name].update(values)
        native = types.SimpleNamespace(lock=threading.RLock(), config=lambda p: config if p == 'wireless' else {}, ubus=lambda *args: {}, configure=Mock(side_effect=configure))
        self.app.adapters = types.SimpleNamespace(native=native)
        self.app.features = types.SimpleNamespace(add=lambda *args: None)
        self.app.route = self.app.router.route
        self.app.cfg = {}
        self.app.cache = types.SimpleNamespace(invalidate=Mock())
        self.app.require_tier = lambda req, tier: self.assertIs(req.json.get('confirm'), True)
        register_native(self.app)
        class Reader:
            def request(reader, method, path, **kwargs): return self.call(method, path)
        route, _ = self.app.router.match('PUT', '/api/wifi/primary')
        for transition in ({'security': 'wpa3'}, {'hidden': True}):
            with self.subTest(transition=transition):
                config['primary'].update(encryption='psk2', hidden='0', wps_pushbutton='1')
                loaded = catalog.load(Reader(), 'wifi_primary')
                tx = catalog.prepare('wifi_primary', loaded, dict(transition, wps=False))
                result = route.handler(types.SimpleNamespace(json=dict(tx['body'], confirm=True)))
                self.assertFalse(result['wps'])
                self.assertEqual(config['primary']['wps_pushbutton'], '0')
        config['primary'].update(encryption='psk2', hidden='0', wps_pushbutton='1')
        before = native.configure.call_count
        with self.assertRaises(ApiError): route.handler(types.SimpleNamespace(json={'security': 'wpa3', 'confirm': True}))
        self.assertEqual(native.configure.call_count, before)

    def test_refused_primary_policy_preserved_while_guest_is_mutable(self):
        config = {'radio0': {'.type': 'wifi-device'}, 'primary': {'.type': 'wifi-iface', 'mode': 'ap', 'device': 'radio0', 'macfilter': 'allow', 'maclist': ['02:00:00:00:00:01']}, 'guest': {'.type': 'wifi-iface', 'mode': 'ap', 'device': 'radio0', 'macfilter': 'deny', 'maclist': []}}
        def configure(package, changes, command):
            for name, values in changes: config[name].update(values)
        native = types.SimpleNamespace(lock=threading.RLock(), config=lambda p: config, configure=Mock(side_effect=configure), ubus=lambda *args: {'changes': []})
        self.app.adapters = types.SimpleNamespace(native=native)
        self.app.cache = types.SimpleNamespace(invalidate=Mock())
        self.app.require_tier = lambda req, tier: self.assertIs(req.json.get('confirm'), True)
        with self.assertRaises(ApiError): self.call('GET', '/api/touchscreen/blocked')
        self.call('PUT', '/api/touchscreen/blocked', {'ap': 'guest', 'mac': '02:00:00:00:00:02', 'blocked': True, 'confirm': True})
        self.assertEqual(config['primary']['macfilter'], 'allow')
        self.assertEqual(config['primary']['maclist'], ['02:00:00:00:00:01'])
        self.assertEqual(config['guest']['maclist'], ['02:00:00:00:00:02'])

