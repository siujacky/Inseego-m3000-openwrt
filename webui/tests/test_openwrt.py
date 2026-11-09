"""Native OpenWrt contracts; command boundary faked because host is Windows."""
import importlib
import json
import os
import tempfile
import unittest
import sys
import threading
import time
import io
import shutil
import subprocess
import base64
from unittest.mock import patch

from tests.helpers import MockClient, make_app
from m3100ui.errors import ApiError
from tests import test_httpd


class NativeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.mod = importlib.import_module('m3100ui.adapters.openwrt')
        except ImportError:
            self.fail('Native OpenWrt adapter has not been implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.password = os.path.join(self.tmp.name, 'password')
        with open(self.password, 'w') as f:
            f.write('private-device-password\n')
        os.chmod(self.password, 0o600)
        self.config = {'platform': 'openwrt', 'mock': False, 'selfheal': False,
                       'state_dir': self.tmp.name, 'password_file': self.password}
        self.calls = []
        self.uci = {
            'network': {'lan': {'.type': 'interface', 'proto': 'static', 'ipaddr': '192.168.8.1',
                                'netmask': '255.255.255.0', 'device': 'br-lan'},
                        'wan': {'.type': 'interface', 'proto': 'qmi', 'device': '/dev/cdc-wdm0', 'apn': 'internet'}},
            'wireless': {'radio0': {'.type': 'wifi-device', 'band': '2g', 'disabled': '0'},
                         'default_radio0': {'.type': 'wifi-iface', 'device': 'radio0', 'mode': 'ap',
                                            'ssid': 'Router', 'encryption': 'psk2', 'key': 'secret123', 'network': 'lan'}},
            'dhcp': {'lan': {'.type': 'dhcp', 'start': '100', 'limit': '100', 'leasetime': '12h'}},
            'system': {'cfg01': {'.type': 'system', 'hostname': 'router', 'timezone': 'UTC'}}}

    def command(self, argv):
        self.calls.append(argv)
        if argv[:3] == ['ubus', 'call', 'uci']:
            payload = json.loads(argv[4])
            action = argv[3]
            if action == 'get':
                return {'values': self.uci.get(payload['config'], {})}
            if action == 'set':
                self.uci.setdefault(payload['config'], {}).setdefault(payload['section'], {}).update(payload['values'])
            if action == 'add':
                self.uci.setdefault(payload['config'], {})[payload['name']] = dict(payload['values'], **{'.type': payload['type']})
                return {'section': payload['name']}
            if action == 'delete':
                del self.uci[payload['config']][payload['section']]
            return {}
        if argv[:4] == ['ubus', 'call', 'system', 'board']:
            return {'model': 'M3100', 'release': {'description': 'OpenWrt native'}}
        if argv[:4] == ['ubus', 'call', 'network.interface', 'dump']:
            return {'interface': [{'interface': 'lan', 'up': True, 'l3_device': 'br-lan',
                                   'ipv4-address': [{'address': '192.168.8.1', 'mask': 24}]}]}
        if argv[:4] == ['ubus', 'call', 'network.wireless', 'status']:
            return {'radio0': {'up': False, 'pending': False}}
        return {}

    def app(self):
        with patch.object(self.mod.NativePlatform, 'command', side_effect=self.command):
            app = make_app(**self.config)
        app.adapters.native.command = self.command
        self.addCleanup(lambda: __import__('shutil').rmtree(app._test_state_dir, ignore_errors=True))
        return app

    def test_native_password_never_uses_vendor_or_fallback(self):
        app = self.app()
        self.assertEqual(app._password(), 'private-device-password')
        self.assertIsNone(app.auth.hash_store)
        app.auth.login('private-device-password', '192.0.2.1')
        os.remove(self.password)
        app.auth.forget_password_cache()
        with self.assertRaises(ApiError):
            app.auth.login('private-device-password', '192.0.2.1')
        self.assertFalse(any('webui_cli' in str(a) for a in self.calls))

    def test_native_rejects_mock_and_missing_password(self):
        for changes in ({'mock': True}, {'password_file': ''}):
            with self.assertRaises(ValueError):
                make_app(**dict(self.config, **changes))

    def test_existing_status_and_meta_shapes(self):
        c = MockClient(self.app())
        data = c.get('/api/status')
        for key in ('connection', 'signal', 'wan', 'battery', 'clients', 'wifi', 'memory', 'sim', 'nr'):
            self.assertIn(key, data)
        self.assertEqual(c.get('/api/meta/full')['platform'], 'openwrt')
        self.assertFalse(c.get('/api/meta/full')['mock'])
        self.assertIn('capabilities', c.get('/api/openwrt'))

    def test_status_prefers_enabled_ap_over_disabled_default_profile(self):
        self.uci['wireless']['default_radio0']['disabled'] = '1'
        self.uci['wireless']['active'] = {'.type': 'wifi-iface', 'device': 'radio0',
            'mode': 'ap', 'ssid': 'Active router', 'disabled': '0'}
        self.assertEqual(MockClient(self.app()).get('/api/status')['wifi']['primary_ssid'], 'Active router')

    def test_primary_wifi_routes_select_and_update_enabled_ap(self):
        self.uci['wireless']['default_radio0']['disabled'] = '1'
        self.uci['wireless']['active'] = {'.type': 'wifi-iface', 'device': 'radio0',
            'mode': 'ap', 'ssid': 'Active router', 'disabled': '0', 'encryption': 'psk2', 'key': 'active-secret'}
        client = MockClient(self.app())
        self.assertEqual(client.get('/api/wifi/primary')['name'], 'active')
        self.assertEqual(client.get('/api/wifi/primary/secret')['password'], 'active-secret')
        client.put('/api/wifi/primary', {'ssid': 'Updated active AP', 'confirm': True})
        self.assertEqual(self.uci['wireless']['active']['ssid'], 'Updated active AP')
        self.assertEqual(self.uci['wireless']['default_radio0']['ssid'], 'Router')

    def test_status_prefers_ap_on_enabled_radio_and_keeps_off_profile_fallback(self):
        self.uci['wireless']['radio0']['disabled'] = '1'
        self.uci['wireless']['radio1'] = {'.type': 'wifi-device', 'band': '5g', 'disabled': '0'}
        self.uci['wireless']['active'] = {'.type': 'wifi-iface', 'device': 'radio1',
            'mode': 'ap', 'ssid': 'Active router', 'disabled': '0'}
        self.assertEqual(MockClient(self.app()).get('/api/status')['wifi']['primary_ssid'], 'Active router')
        del self.uci['wireless']['active']
        self.assertEqual(MockClient(self.app()).get('/api/status')['wifi']['primary_ssid'], 'Router')

    def test_wifi_translation_and_secret_redaction(self):
        c = MockClient(self.app())
        d = c.get('/api/wifi/primary')
        self.assertEqual(d['ssid'], 'Router')
        self.assertTrue(d['password_set'])
        self.assertNotIn('secret123', json.dumps(d))
        c.put('/api/wifi/primary', {'ssid': 'New SSID'}, expect=428)
        self.assertFalse(any(a[3:4] == ['set'] for a in self.calls))
        c.put('/api/wifi/primary', {'ssid': 'New SSID', 'confirm': True})
        mutations = [json.loads(a[4]) for a in self.calls if a[:4] == ['ubus', 'call', 'uci', 'set']]
        self.assertEqual(mutations[-1]['values'], {'ssid': 'New SSID'})
        self.assertIn(['wifi', 'reload'], self.calls)

    def test_wifi_rejects_security_without_key_and_unknown_fields(self):
        self.uci['wireless']['default_radio0'].pop('key')
        c = MockClient(self.app())
        c.put('/api/wifi/primary', {'security': 'wpa2', 'confirm': True}, expect=400)
        c.put('/api/wifi/primary', {'shell': 'reboot', 'confirm': True}, expect=400)
        self.assertFalse(any(a[3:4] == ['set'] for a in self.calls))

    def test_client_mode_uses_native_wifi_interface(self):
        c = MockClient(self.app())
        d = c.put('/api/openwrt/wifi/default_radio0', {'mode': 'sta', 'network': 'wan',
                                                      'ssid': 'Upstream', 'confirm': True})
        self.assertEqual(d['mode'], 'sta')
        self.assertEqual(self.uci['wireless']['default_radio0']['mode'], 'sta')
        self.assertNotIn('secret123', json.dumps(d))

    def test_qmi_configuration_is_uci_and_requires_confirmation(self):
        c = MockClient(self.app())
        c.put('/api/openwrt/interfaces/wan', {'apn': 'new.apn'}, expect=428)
        c.put('/api/openwrt/interfaces/wan', {'apn': 'new.apn', 'confirm': True})
        self.assertEqual(self.uci['network']['wan']['apn'], 'new.apn')
        self.assertTrue(any(a[:4] == ['ubus', 'call', 'network', 'reload'] for a in self.calls))

    def test_unknown_interface_cannot_create_arbitrary_uci_sections(self):
        c = MockClient(self.app())
        c.put('/api/openwrt/interfaces/bad;reboot', {'apn': 'x', 'confirm': True}, expect=400)
        c.put('/api/openwrt/interfaces/nonexistent', {'apn': 'x', 'confirm': True}, expect=404)

    def test_native_failures_are_not_success(self):
        c = MockClient(self.app())
        original = c.app.adapters.native.command
        def fail(argv):
            if argv[:4] == ['ubus', 'call', 'uci', 'set']:
                raise ApiError(502, 'native_command_failed', 'native command failed')
            return original(argv)
        c.app.adapters.native.command = fail
        c.put('/api/wifi/primary', {'ssid': 'Failed', 'confirm': True}, expect=502)
        self.assertNotIn(['wifi', 'reload'], self.calls)

    def test_command_boundary_rejects_unapproved_executable(self):
        native = self.mod.NativePlatform(dict(self.config))
        with self.assertRaises(ApiError):
            native.command(['sh', '-c', 'echo unsafe'])

    def test_qmi_error_string_is_failure_even_on_exit_zero(self):
        native = self.mod.NativePlatform(dict(self.config))
        with patch.object(native, '_execute', return_value='"No SIM card present"'):
            with self.assertRaises(ApiError):
                native.command(['uqmi', '-d', '/dev/cdc-wdm0', '--get-pin-status'])

    def test_uci_staging_is_reverted_when_commit_fails(self):
        native = self.mod.NativePlatform(dict(self.config))
        calls = []
        def command(argv):
            calls.append(argv)
            if argv[3] == 'commit':
                raise ApiError(502, 'native_command_failed', 'commit failed')
            return {}
        native.command = command
        with self.assertRaises(ApiError):
            native.configure('wireless', [('default_radio0', {'ssid': 'test'})], ['wifi', 'reload'])
        self.assertEqual(calls[-1][:4], ['ubus', 'call', 'uci', 'revert'])

    def test_public_manifest_contains_native_page_and_no_vendor_pages(self):
        c = MockClient(self.app(), login=False)
        pages = c.get('/api/ui/pages')
        self.assertIn('/app/pages/system/openwrt.js', pages)
        self.assertNotIn('/app/pages/tools/nv.js', pages)
        c.get('/api/openwrt', expect=401)

    def test_system_uses_real_openwrt_init_reload(self):
        c = MockClient(self.app())
        c.put('/api/openwrt/system', {'hostname': 'new-router', 'confirm': True})
        self.assertIn(['/etc/init.d/system', 'reload'], self.calls)

    def test_native_does_not_cache_removed_password(self):
        app = self.app()
        app.auth.login('private-device-password', '192.0.2.1')
        os.remove(self.password)
        with self.assertRaises(ApiError):
            app.auth.login('private-device-password', '192.0.2.1')

    def test_native_snapshot_never_leaks_runtime_wifi_keys(self):
        app = self.app()
        original = app.adapters.native.ubus
        def ubus(obj, action, payload=None):
            if obj == 'network.wireless':
                return {'radio0': {'up': False, 'interfaces': [{'config': {'key': 'runtime-secret'}}]}}
            return original(obj, action, payload)
        app.adapters.native.ubus = ubus
        c = MockClient(app)
        self.assertNotIn('runtime-secret', json.dumps(c.get('/api/openwrt/wifi')))

    def test_audit_shape_and_no_secrets(self):
        c = MockClient(self.app())
        c.put('/api/wifi/primary', {'password': 'changed-secret', 'confirm': True})
        result = c.get('/api/tools/audit')
        self.assertIn('entries', result)
        self.assertNotIn('changed-secret', json.dumps(result))

    def test_apply_failure_reports_saved_but_not_applied(self):
        native = self.mod.NativePlatform(dict(self.config))
        def command(argv):
            if argv[0] == 'wifi':
                raise ApiError(502, 'native_command_failed', 'reload failed')
            return {}
        native.command = command
        with self.assertRaises(ApiError) as error:
            native.configure('wireless', [('default_radio0', {'ssid': 'test'})], ['wifi', 'reload'])
        self.assertEqual(error.exception.details, {'saved': True, 'applied': False})

    def test_modem_unavailable_cannot_claim_capability(self):
        native = self.mod.NativePlatform(dict(self.config))
        native.ubus = lambda *args: {}
        native.modem = lambda: {'available': False, 'reason': 'QRTR not ready'}
        self.assertFalse(native.capabilities()['cellular']['available'])

    def test_modem_action_requires_declared_capability(self):
        c = MockClient(self.app())
        c.app.adapters.native.modem = lambda action='status', *args: {'available': True, 'capabilities': {'connect': False}}
        c.post('/api/cellular/connect', {'confirm': True}, expect=503)

    def test_valid_qmi_data_state_string_is_not_an_error(self):
        native = self.mod.NativePlatform(dict(self.config))
        with patch.object(native, '_execute', return_value='"connected"'):
            self.assertEqual(native.command(['uqmi', '-d', '/dev/cdc-wdm0', '--get-data-status']), 'connected')

    def test_process_timeout_output_limit_and_nonzero_are_errors(self):
        native = self.mod.NativePlatform(dict(self.config, native_timeout_s=1, native_max_bytes=1024))
        for script, expected in [('import time; time.sleep(5)', 'native_timeout'),
                                 ('print("x" * 10000)', 'native_output_limit'),
                                 ('import sys; print("private-secret"); sys.exit(3)', 'native_command_failed')]:
            with self.subTest(expected=expected):
                with self.assertRaises(ApiError) as error:
                    native._execute([sys.executable, '-c', script])
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn('private-secret', str(error.exception.to_json()))

    def test_create_wifi_client_uses_uci_add_and_requires_password(self):
        c = MockClient(self.app())
        data = {'name': 'upstream', 'device': 'radio0', 'network': 'wan', 'mode': 'sta',
                'ssid': 'Upstream', 'security': 'wpa2', 'confirm': True}
        c.post('/api/openwrt/wifi', data, expect=400)
        result = c.post('/api/openwrt/wifi', dict(data, password='upstream-secret'))
        self.assertEqual(result['mode'], 'sta')
        self.assertNotIn('upstream-secret', json.dumps(result))
        self.assertEqual(self.uci['wireless']['upstream']['encryption'], 'psk2')
        c.post('/api/openwrt/wifi', dict(data, password='upstream-secret'), expect=409)

    def test_create_wifi_validation_has_no_partial_side_effects(self):
        c = MockClient(self.app())
        data = {'name': 'bad', 'device': 'radio0', 'network': 'missing', 'ssid': 'Bad',
                'mode': 'sta', 'security': 'open', 'confirm': True}
        c.post('/api/openwrt/wifi', data, expect=404)
        self.assertNotIn('bad', self.uci['wireless'])

    def test_remove_wifi_uses_uci_delete_and_protects_radio_sections(self):
        c = MockClient(self.app())
        c.call('DELETE', '/api/openwrt/wifi/default_radio0', {'confirm': True})
        self.assertNotIn('default_radio0', self.uci['wireless'])
        c.call('DELETE', '/api/openwrt/wifi/radio0', {'confirm': True}, expect=404)

    def test_network_pin_and_runtime_identifiers_are_redacted(self):
        self.uci['network']['wan']['pincode'] = '1234'
        c = MockClient(self.app())
        self.assertNotIn('1234', json.dumps(c.get('/api/openwrt/interfaces')))

    def hardware_report(self, timestamp=999):
        return {'timestamp': timestamp,
                'battery': {'voltage_uv': 4360000, 'source': 'pm7250b-last-adc', 'sample_age_s': None,
                            'capacity_percent': None, 'charging': None},
                'display': {'device': '/dev/m3100-lcd', 'registered': True, 'last_frame_written': 998,
                            'render_ok': True, 'physical_confirmed': False},
                'touch': {'event_devices': ['event0'], 'events_seen': 3, 'last_event_timestamp': 997},
                'modem': {'remoteproc_state': 'running'}, 'ipa': {'interfaces': ['wwan0']}}

    def hardware_file(self, value):
        real_open = open
        def read(path, *args, **kwargs):
            return io.StringIO(json.dumps(value)) if path == '/run/m3100-hardware.json' else real_open(path, *args, **kwargs)
        return patch('builtins.open', side_effect=read)

    def test_hardware_report_is_authenticated_read_only_and_uses_fixed_file(self):
        app = self.app()
        c = MockClient(app)
        self.calls.clear()
        with self.hardware_file(self.hardware_report()), patch('m3100ui.adapters.openwrt.time.time', return_value=1000):
            d = c.get('/api/openwrt/hardware', query={'path': '/dev/sda'})
        self.assertIs(d['fresh'], True)
        self.assertEqual(d['battery']['voltage_uv'], 4360000)
        self.assertEqual(d['display']['device'], '/dev/m3100-lcd')
        self.assertEqual(d['touch']['events_seen'], 3)
        self.assertIsNone(d['battery']['capacity_percent'])
        self.assertIsNone(d['battery']['charging'])
        self.assertIsNone(d['battery']['sample_age_s'])
        self.assertIs(d['display']['physical_confirmed'], False)
        self.assertEqual(self.calls, [], 'Hardware endpoint must never execute a command')
        MockClient(app, login=False).get('/api/openwrt/hardware', expect=401)
        c.put('/api/openwrt/hardware', {}, expect=405)

    def test_hardware_missing_malformed_oversized_files_fail_unavailable(self):
        c = MockClient(self.app())
        for content in ('[]', '{broken', '{' + ' ' * 65536 + '}'):
            with patch('builtins.open', return_value=io.StringIO(content)):
                c.get('/api/openwrt/hardware', expect=503)
        with patch('builtins.open', side_effect=FileNotFoundError()):
            c.get('/api/openwrt/hardware', expect=503)

    def test_hardware_stale_future_invalid_timestamp_cannot_claim_live_data(self):
        c = MockClient(self.app())
        for timestamp in (0, 969, 1001, None, True, float('nan'), float('inf'), 10 ** 1000):
            with self.hardware_file(self.hardware_report(timestamp)), patch('m3100ui.adapters.openwrt.time.time', return_value=1000):
                d = c.get('/api/openwrt/hardware')
            self.assertIs(d['fresh'], False)
            self.assertIsNone(d['battery']['voltage_uv'])
            self.assertIsNone(d['display']['registered'])
            self.assertIsNone(d['display']['render_ok'])
            self.assertEqual(d['touch']['event_devices'], [])
            self.assertIsNone(d['touch']['events_seen'])
            self.assertIsNone(d['modem']['remoteproc_state'])
            self.assertEqual(d['ipa']['interfaces'], [])

    def test_hardware_capability_supports_custom_lcd_and_voltage_only_not_framebuffer(self):
        native = self.app().adapters.native
        with self.hardware_file(self.hardware_report()), patch('m3100ui.adapters.openwrt.time.time', return_value=1000), patch.object(self.mod.glob, 'glob', return_value=[]):
            caps = native.capabilities()
        self.assertIs(caps['display']['available'], True)
        self.assertEqual(caps['display']['device'], '/dev/m3100-lcd')
        self.assertIs(caps['display']['physical_confirmed'], False)
        self.assertIs(caps['power']['available'], True)
        self.assertIs(caps['power']['voltage_only'], True)
        self.assertIsNone(caps['power']['data']['capacity_percent'])
        self.assertIsNone(caps['power']['data']['charging'])
        self.assertIs(caps['touch']['physical_confirmed'], False)

    def test_hardware_caps_expire_within_other_capability_cache(self):
        c = MockClient(self.app())
        now = [1000]
        with self.hardware_file(self.hardware_report(970)), patch('m3100ui.adapters.openwrt.time.time', side_effect=lambda: now[0]):
            first = c.get('/api/openwrt')['capabilities']
            self.assertIs(first['display']['available'], True)
            now[0] = 1001
            last = c.get('/api/openwrt')['capabilities']
            for name in ('display', 'power', 'touch'):
                self.assertIs(last[name]['available'], False)

    def test_hardware_unavailable_and_bad_fields_do_not_invent_support(self):
        native = self.app().adapters.native
        with patch('builtins.open', side_effect=FileNotFoundError()):
            caps = native.hardware_capabilities()
            for name in ('display', 'power', 'touch'):
                self.assertIs(caps[name]['available'], False)
        value = self.hardware_report()
        value['battery'].update({'voltage_uv': -1, 'capacity_percent': 85, 'charging': True})
        value['display'].update({'device': '/dev/fb0', 'registered': True, 'physical_confirmed': True})
        with self.hardware_file(value), patch('m3100ui.adapters.openwrt.time.time', return_value=1000):
            d = native.hardware_status()
            caps = native.hardware_capabilities()
        self.assertIsNone(d['battery']['voltage_uv'])
        self.assertIsNone(d['battery']['capacity_percent'])
        self.assertIsNone(d['battery']['charging'])
        self.assertIs(d['display']['physical_confirmed'], False)
        self.assertIs(caps['display']['available'], False)
        self.assertIs(caps['power']['available'], False)

    @unittest.skipUnless(shutil.which('node'), 'node unavailable')
    def test_hardware_panel_labels_voltage_only_and_unconfirmed_physical_proof(self):
        from tests.helpers import ROOT
        with open(os.path.join(ROOT, 'web', 'app', 'pages', 'system', '_native.js'), encoding='utf-8') as f:
            module = 'data:text/javascript;base64,' + base64.b64encode(f.read().encode()).decode()
        value = dict(self.hardware_report(), fresh=True, age_s=1)
        script = 'import {hardwareSummary} from %s; const d=%s; console.log(JSON.stringify([hardwareSummary(d),hardwareSummary({...d,fresh:false})]));' % (json.dumps(module), json.dumps(value))
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        current, stale = json.loads(result.stdout)
        self.assertIn('4.360 V', json.dumps(current))
        self.assertIn('sample age unknown', json.dumps(current))
        self.assertIn('capacity and charging unavailable', json.dumps(current))
        self.assertIn('unconfirmed', json.dumps(current))
        self.assertIn('registered', json.dumps(current))
        self.assertNotIn('4.360 V', json.dumps(stale))
        self.assertIn('unavailable', json.dumps(stale).lower())

    def test_deadline_includes_descendant_pipe_after_parent_exit(self):
        native = self.mod.NativePlatform(dict(self.config, native_timeout_s=1))
        child = 'import time; time.sleep(6)'
        parent = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c",%r]); print("{}",flush=True)' % child
        start = time.monotonic()
        with self.assertRaises(ApiError) as error:
            native._execute([sys.executable, '-c', parent])
        self.assertEqual(error.exception.code, 'native_timeout')
        self.assertLess(time.monotonic() - start, 2.5)

    def test_deadline_terminates_descendants_while_parent_alive(self):
        native = self.mod.NativePlatform(dict(self.config, native_timeout_s=1))
        marker = os.path.join(self.tmp.name, 'descendant-survived')
        child = 'import time; time.sleep(2.5); open(%r,"w").write("survived")' % marker
        parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",%r]); time.sleep(6)' % child
        start = time.monotonic()
        with self.assertRaises(ApiError) as error:
            native._execute([sys.executable, '-c', parent])
        self.assertEqual(error.exception.code, 'native_timeout')
        self.assertLess(time.monotonic() - start, 2.5)
        time.sleep(max(0, 3.2 - (time.monotonic() - start)))
        self.assertFalse(os.path.exists(marker), 'Timed-out descendant continued running')

    def test_passwordless_wifi_form_payload_can_create_open_and_owe(self):
        helper = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'web', 'app', 'pages', 'system', '_native.js')
        self.assertTrue(os.path.isfile(helper), 'Native form payload helper missing')
        if not shutil.which('node'):
            self.skipTest('node unavailable')
        with open(helper, encoding='utf-8') as f:
            module = 'data:text/javascript;base64,' + base64.b64encode(f.read().encode()).decode()
        c = MockClient(self.app())
        for security in ('open', 'owe'):
            values = {'name': security + '_ap', 'mode': 'ap', 'device': 'radio0', 'network': 'lan',
                      'ssid': security, 'security': security, 'password': ''}
            script = 'import {wifiCreateBody} from %s; console.log(JSON.stringify(wifiCreateBody(%s)))' % (json.dumps(module), json.dumps(values))
            result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, check=True, timeout=10)
            payload = json.loads(result.stdout)
            self.assertNotIn('password', payload)
            response = c.post('/api/openwrt/wifi', dict(payload, confirm=True))
            self.assertEqual(response['security'], security)

    def test_primary_does_not_advertise_unsupported_split_controls(self):
        c = MockClient(self.app())
        self.assertIs(c.get('/api/wifi/primary').get('split_ssid_available'), False)

    @unittest.skipUnless(shutil.which('node'), 'node unavailable')
    def test_primary_consumer_hides_split_controls_and_keeps_unknown_bands_explicit(self):
        from tests.helpers import ROOT
        import re
        with open(os.path.join(ROOT, 'web', 'app', 'pages', 'wifi', '_network.js'), encoding='utf-8') as f:
            source = re.sub(r'^import \{([^}]+)\} .*?;$', r'const {\1} = globalThis.stub;', f.read(), flags=re.M)
        module = 'data:text/javascript;base64,' + base64.b64encode(source.encode()).decode()
        data = MockClient(self.app()).get('/api/wifi/primary')
        script = '''
function node(tag,...children) { return {tag, children, append(...v){this.children.push(...v)}, appendChild(v){this.children.push(v);return v}}; }
let current=DATA; let fields=[];
globalThis.stub={h:node,clear:n=>{n.children=[];return n},form:(n,cfg)=>{fields=cfg.fields.filter(Boolean).map(x=>x.key);return{}},
alert:(...v)=>v,badge:(...v)=>v,button:()=>null,get:()=>{},put:()=>{},guard:(el,load,render)=>render(current),
strongPassword:()=>'',hexPassword:()=>'',showSecret:()=>{},BAND_LABEL:{'2g':'2.4 GHz'}};
const {renderNetwork}=await import(MODULE);
current.active_bands=null;
const unknown=node('root');renderNetwork(unknown,'primary');
const nativeFields=fields;
current.active_bands=[];
const inactive=node('root');renderNetwork(inactive,'primary');
delete current.split_ssid_available; current.platform='stock';
renderNetwork(node('root'),'primary');
console.log(JSON.stringify({nativeFields,stockFields:fields,unknown,inactive}));
'''.replace('DATA', json.dumps(data)).replace('MODULE', json.dumps(module))
        output = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, check=True, timeout=10)
        result = json.loads(output.stdout)
        self.assertNotIn('split_ssid', result['nativeFields'])
        self.assertNotIn('ssid_5g', result['nativeFields'])
        self.assertIn('split_ssid', result['stockFields'])
        self.assertIn('Active bands unavailable', json.dumps(result['unknown']))
        self.assertNotIn('not using any band', json.dumps(result['unknown']))
        self.assertIn('#/system/openwrt', json.dumps(result['inactive']))
        self.assertNotIn('#/wifi/advanced', json.dumps(result['inactive']))

    def test_primary_active_band_requires_matching_runtime_interface(self):
        app = self.app()
        original = app.adapters.native.ubus
        runtime = {'radio0': {'up': True, 'interfaces': [{'section': 'default_radio0'}]}}
        app.adapters.native.ubus = lambda obj, method, payload=None: runtime if obj == 'network.wireless' else original(obj, method, payload)
        c = MockClient(app)
        self.assertEqual(c.get('/api/wifi/primary')['active_bands'], ['2g'])
        runtime['radio0']['interfaces'] = [{'section': 'different_interface'}]
        self.assertEqual(c.get('/api/wifi/primary')['active_bands'], [])
        runtime['radio0'] = {'up': False, 'interfaces': [{'section': 'default_radio0'}]}
        self.assertEqual(c.get('/api/wifi/primary')['active_bands'], [])

    def test_sms_list_is_read_only_but_writes_require_acknowledgement(self):
        native = self.mod.NativePlatform(dict(self.config, modem_helper=os.path.abspath('helper')))
        with patch.object(self.mod.os.path, 'isfile', return_value=True):
            native.command = lambda argv: {'messages': []}
            self.assertEqual(native.modem('sms-list'), {'messages': []})
            with self.assertRaises(ApiError):
                native.modem('connect')
            for invalid in ({}, {'messages': 'bad'}, {'messages': [None]}):
                native.command = lambda argv, value=invalid: value
                with self.assertRaises(ApiError):
                    native.modem('sms-list')

    def test_status_preserves_unknown_and_read_failure(self):
        app = self.app()
        original = app.adapters.native.ubus
        app.adapters.native.modem = lambda: {'available': True, 'connection': {'state': 'unknown'}, 'data_enabled': None}
        def failed(obj, method, payload=None):
            if obj in ('network.interface', 'network.wireless'):
                raise ApiError(502, 'native_command_failed', 'Read failed')
            return original(obj, method, payload)
        app.adapters.native.ubus = failed
        d = MockClient(app).get('/api/status')
        self.assertEqual(d['connection']['state'], 'unknown')
        self.assertEqual(d['wan']['status'], 'Unavailable')
        self.assertIsNone(d['wifi']['enabled'])
        self.assertIsNone(d['connection']['data_enabled'])

    def test_gnss_stale_future_missing_timestamp_cannot_assert_current_fix(self):
        c = MockClient(self.app())
        real_open = open
        for timestamp, expected in ((0, False), (940, False), (1001, False), (None, False), (True, False), (999, True)):
            value = {'fix_valid': True, 'latitude': 1, 'longitude': 2}
            if timestamp is not None:
                value['timestamp'] = timestamp
            def read(path, *args, **kwargs):
                return io.StringIO(json.dumps(value)) if path == '/run/m3100-gnss.json' else real_open(path, *args, **kwargs)
            with patch('builtins.open', side_effect=read), patch('m3100ui.adapters.openwrt.time.time', return_value=1000):
                d = c.get('/api/openwrt/gnss')
                self.assertIs(d['fix_valid'], expected)
                self.assertIs(d['fresh'], expected)

    def test_cached_overview_cannot_keep_old_gnss_fix_current(self):
        c = MockClient(self.app())
        now = [1000]
        real_open = open
        def read(path, *args, **kwargs):
            return io.StringIO('{"timestamp":970,"fix_valid":true}') if path == '/run/m3100-gnss.json' else real_open(path, *args, **kwargs)
        with patch('builtins.open', side_effect=read), patch('m3100ui.adapters.openwrt.time.time', side_effect=lambda: now[0]):
            first = c.get('/api/openwrt')['capabilities']['gnss']
            self.assertIs(first['available'], True)
            now[0] = 1001
            latest = c.get('/api/openwrt')['capabilities']['gnss']
            self.assertIs(latest['available'], False)
            self.assertIs(latest['data']['fix_valid'], False)

    def test_gnss_rejects_nonfinite_and_extreme_timestamp_without_server_error(self):
        c = MockClient(self.app())
        real_open = open
        for timestamp in (float('nan'), float('inf'), 10 ** 1000, 'now'):
            def read(path, *args, **kwargs):
                return io.StringIO(json.dumps({'timestamp': timestamp, 'fix_valid': True})) if path == '/run/m3100-gnss.json' else real_open(path, *args, **kwargs)
            with patch('builtins.open', side_effect=read):
                self.assertIs(c.get('/api/openwrt/gnss')['fix_valid'], False)

    @unittest.skipUnless(shutil.which('node'), 'node unavailable')
    def test_dashboard_renders_unknown_without_off_labels(self):
        from tests.helpers import ROOT
        with open(os.path.join(ROOT, 'web', 'app', 'pages', 'dashboard.js'), encoding='utf-8') as f:
            source = f.read()
        import re
        source = re.sub(r'^import \* as fmt .*?;$', 'const fmt = globalThis.stubFmt;', source, flags=re.M)
        source = re.sub(r'^import \{([^}]+)\} .*?;$', r'const {\1} = globalThis.stub;', source, flags=re.M)
        module = 'data:text/javascript;base64,' + base64.b64encode(source.encode()).decode()
        script = '''
function node(tag,...children) { return {tag, children, append(...v){this.children.push(...v)}, appendChild(v){this.children.push(v);return v}}; }
globalThis.stub = {h:node, clear:n=>{n.children=[];return n}, card:(title,body)=>node('card',title,body),
 kv:x=>x, stat:(...x)=>x, badge:(...x)=>x, signalBars:()=>null, meter:()=>null, alert:(...x)=>x,
 button:(title)=>title, toast:()=>{}, lineChart:()=>null, post:()=>{}, put:()=>{}};
globalThis.stubFmt = new Proxy({}, {get:()=>()=>''});
const dashboard = (await import(MODULE)).default;
const root=node('root');
await dashboard.render(root,{store:{subscribe(fn){fn({status:{platform:'openwrt',connection:{state:'unknown',data_enabled:null},
wifi:{enabled:null},wan:{status:'Unavailable'},clients:{total:0},errors:{wifi:{message:'Read failed'}}},
history:{rsrp:[],nr_rsrp:[],sinr:[],rx_bps:[],tx_bps:[]}});return ()=>{}}}});
console.log(JSON.stringify(root));
'''.replace('MODULE', json.dumps(module))
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, check=True, timeout=10)
        rendered = result.stdout.decode()
        self.assertNotIn('Mobile data off', rendered)
        self.assertNotIn('"Off"', rendered)
        self.assertIn('Unknown', rendered)
        self.assertIn('Unavailable', rendered)



class NativeHttpdTests(test_httpd.HttpdTests):
    """Re-run shared real-socket security suite against native composition."""
    @classmethod
    def setUpClass(cls):
        from m3100ui.httpd import make_server
        cls.fixture = NativeTests()
        cls.fixture.setUp()
        cls.app = cls.fixture.app()
        cls.srv = make_server(cls.app, '127.0.0.1', 0)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.fixture.doCleanups()

    def test_native_write_checks_auth_csrf_origin_and_tier_before_mutation(self):
        start = len(self.fixture.calls)
        response, _ = self.req('PUT', '/api/wifi/primary', {'ssid': 'Unsafe', 'confirm': True})
        self.assertEqual(response.status, 401)
        cookie, csrf = self.login()
        response, _ = self.req('PUT', '/api/wifi/primary', {'ssid': 'Unsafe', 'confirm': True}, headers={'Cookie': cookie})
        self.assertEqual(response.status, 403)
        response, _ = self.req('PUT', '/api/wifi/primary', {'ssid': 'Unsafe', 'confirm': True},
                               headers={'Cookie': cookie, 'X-CSRF-Token': csrf, 'Origin': 'http://evil.example.com'})
        self.assertEqual(response.status, 403)
        response, _ = self.req('PUT', '/api/wifi/primary', {'ssid': 'Unsafe'},
                               headers={'Cookie': cookie, 'X-CSRF-Token': csrf})
        self.assertEqual(response.status, 428)
        self.assertFalse(any(a[:4] == ['ubus', 'call', 'uci', 'set'] for a in self.fixture.calls[start:]))
        response, raw = self.req('PUT', '/api/wifi/primary', {'ssid': 'Authenticated', 'confirm': True},
                                 headers={'Cookie': cookie, 'X-CSRF-Token': csrf})
        self.assertEqual(response.status, 200, raw)
        self.assertEqual(json.loads(raw)['ssid'], 'Authenticated')


if __name__ == '__main__':
    unittest.main()
