import json
import pathlib
import unittest
from m3100_touch import catalog as c

class Client:
    def __init__(self, data): self.data = data
    def request(self, method, path, **kwargs): return self.data[path]

class CatalogTests(unittest.TestCase):
    def test_coverage(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        if not (root / 'analysis/full206-integrated/m3000-screen-reference/qml-recovery.json').exists():
            self.skipTest('Private stock capture omitted from source-only publication')
        source = json.loads((root / 'analysis/full206-integrated/m3000-screen-reference/qml-recovery.json').read_text())['resources']
        self.assertEqual(len(c.STOCK_COVERAGE), 124)
        self.assertEqual({x['file'] for x in source}, {x['file'] for x in c.STOCK_COVERAGE})
        for item in c.STOCK_COVERAGE:
            self.assertIn(item['target'], c.PAGES)
            self.assertTrue(item['reason'])
        routes = json.loads((root / 'analysis/full206-integrated/r15/touch-current-routing.json').read_text())['routes']
        self.assertEqual({(r['method'], r['path']) for r in routes}, set(c.NATIVE_ROUTES))

    def test_dirty_blank_secret_and_visibility(self):
        cl = Client({'/api/openwrt/interfaces': {'interfaces': [{'name': 'wan', 'proto': 'qmi', 'password_set': True, 'apn': 'old'}]}})
        loaded = c.load(cl, 'interface_edit', {'name': 'wan'})
        tx = c.prepare('interface_edit', loaded, {'password': '', 'apn': 'new'})
        self.assertEqual(tx['body'], {'apn': 'new'})
        self.assertEqual(tx['tier'], 2)
        self.assertFalse(c.visible(next(x for x in loaded['fields'] if x['key'] == 'ipaddr'), loaded['data']))
        with self.assertRaises(ValueError): c.prepare('interface_edit', loaded, {'proto': 'garbage'})

    def test_firewall_fields_and_protected_rows(self):
        data = {'zones': [{'section': 'usb', 'options': {'name': 'usbmgmt'}, 'editable': False, 'reason': 'protected'}], 'rules': [], 'redirects': [], 'forwardings': [], 'defaults': {'options': {}, 'editable': True}, 'protection': {'zone': 'usbmgmt'}, 'networks': ['lan']}
        cl = Client({'/api/openwrt/firewall': data})
        loaded = c.load(cl, 'firewall_rules_edit', {'create': True})
        self.assertTrue({'src_port', 'dest_port', 'src_mac', 'target'} <= {x['key'] for x in loaded['fields']})
        self.assertFalse(c.visible(next(x for x in loaded['fields'] if x['key'] == 'src_port'), {'proto': ['icmp']}))
        blocked = c.load(cl, 'firewall_zones_edit', {'section': 'usb'})
        with self.assertRaises(ValueError): c.prepare('firewall_zones_edit', blocked, {'enabled': False})

    def test_unavailable_and_no_changes(self):
        loaded = c.load(Client({}), 'charging')
        self.assertTrue(loaded['reason'])
        self.assertEqual(loaded['actions'], [])
        with self.assertRaises(ValueError): c.prepare('charging', loaded, {})
        loaded = c.load(Client({'/api/settings': {'theme': 'dark'}}), 'settings')
        with self.assertRaises(ValueError): c.prepare('settings', loaded, {'theme': 'dark'})

    def test_wifi_create_requires_complete_fields_and_secret_summary(self):
        loaded = c.load(Client({'/api/openwrt/wifi': {'interfaces': [], 'radios': [{'name': 'radio0'}]}}), 'wifi_edit', {'create': True})
        with self.assertRaises(ValueError): c.prepare('wifi_edit', loaded, {'ssid': 'Example', 'password': 'privatepw'})
        tx = c.prepare('wifi_edit', loaded, {'name': 'new_ap', 'ssid': 'Example', 'password': 'privatepw'})
        self.assertEqual(tx['method'], 'POST')
        self.assertNotIn('privatepw', repr(tx['summary']))

    def test_radio_channel_and_empty_numeric_fields(self):
        loaded = c.load(Client({'/api/openwrt/wifi': {'radios': [{'name': 'radio0', 'channel': 'auto'}]}}), 'radio_edit', {'name': 'radio0'})
        self.assertEqual(c.prepare('radio_edit', loaded, {'channel': '6'})['body'], {'channel': 6})
        with self.assertRaises(ValueError): c.prepare('radio_edit', loaded, {'channel': '999'})

    def test_required_fields_and_hidden_fields(self):
        loaded = c.load(Client({'/api/openwrt/interfaces': {'interfaces': [{'name': 'wan', 'proto': 'dhcp'}]}}), 'interface_edit', {'name': 'wan'})
        with self.assertRaises(ValueError): c.prepare('interface_edit', loaded, {'proto': 'static'})
        tx = c.prepare('interface_edit', loaded, {'proto': 'qmi', 'apn': 'example', 'ipaddr': '1.2.3.4'})
        self.assertNotIn('ipaddr', tx['body'])

    def test_sensitive_credentials_are_only_loaded_explicitly(self):
        cl = Client({'/api/wifi/primary': {'ssid': 'Main', 'password_set': True}, '/api/wifi/primary/secret': {'password': 'privatepw'}, '/api/touchscreen/wifi/guest/secret': {'ssid': 'Guest', 'password': 'guest-private'}})
        ordinary = c.load(cl, 'credentials')
        self.assertNotIn('privatepw', repr(ordinary))
        explicit = c.load(cl, 'credentials_reveal')
        self.assertTrue(explicit['sensitive'])
        self.assertIn('privatepw', repr(explicit['rows']))
        guest = c.load(cl, 'credentials_reveal', {'name': 'guest'})
        self.assertIn('guest-private', repr(guest['rows']))

    def test_wifi_quick_radio_action_and_firewall_protocol_transition(self):
        loaded = c.load(Client({'/api/wifi/primary': {'ssid': 'Main', 'enabled': True}}), 'wifi_primary')
        tx = c.prepare('wifi_primary', loaded, {}, 'wifi_off')
        self.assertEqual((tx['path'], tx['body']), ('/api/wifi/radio', {'on': False}))
        firewall = {'rules': [{'section': 'rule1', 'options': {'name': 'Old', 'src': 'lan', 'dest': '', 'family': 'ipv4', 'proto': ['tcp'], 'src_port': '80', 'dest_port': '90', 'target': 'ACCEPT'}, 'editable': True}], 'zones': [{'options': {'name': 'lan'}}]}
        loaded = c.load(Client({'/api/openwrt/firewall': firewall}), 'firewall_rules_edit', {'section': 'rule1'})
        with self.assertRaises(ValueError): c.prepare('firewall_rules_edit', loaded, {'proto': 'icmp'})
        tx = c.prepare('firewall_rules_edit', loaded, {'src_port': '', 'dest_port': ''})
        self.assertEqual(tx['body'], {'src_port': '', 'dest_port': ''})

    def test_api_unavailable_is_navigable_but_401_propagates(self):
        from m3100_touch.api import ApiError
        class Offline:
            def __init__(self, status): self.status = status
            def request(self, *args, **kwargs): raise ApiError(self.status, 'unavailable', 'No native SMS provider')
        page = c.load(Offline(503), 'sms_send')
        self.assertEqual(page['reason'], 'No native SMS provider')
        self.assertEqual(page['actions'], [])
        with self.assertRaises(ApiError): c.load(Offline(401), 'sms_send')

    def test_live_modem_provider_failure_disables_cellular_sms_and_preflight(self):
        from m3100_touch.api import ApiError
        from m3100_touch.controller import Controller
        for page_id, path in [('modem', '/api/openwrt/modem'), ('sms', '/api/openwrt/sms'), ('sms_send', '/api/openwrt/modem')]:
            with self.subTest(page=page_id):
                class FailedProvider:
                    def request(self, method, requested, **kwargs):
                        self_outer.assertEqual((method, requested, kwargs), ('GET', path, {}))
                        raise ApiError(502, 'native_command_failed', 'Native command failed', {'program': 'm3100-modem', 'rc': 1})
                self_outer = self
                loaded = c.load(FailedProvider(), page_id)
                self.assertIn('provider', loaded['reason'].lower())
                self.assertIn('failed', loaded['reason'].lower())
                self.assertIn('unavailable', loaded['reason'].lower())
                self.assertEqual((loaded['data'], loaded['fields'], loaded['actions']), ({}, [], []))
                self.assertNotIn('source', loaded)
                self.assertFalse(Controller._usable_readback(loaded))
                diagnostics = ' '.join(str(row['value']) for row in loaded['rows'])
                for cause in ('502', 'native_command_failed', 'Native command failed', 'm3100-modem', '1'):
                    self.assertIn(cause, diagnostics)
                self.assertTrue(all(row['kind'] == 'text' and not row['enabled'] for row in loaded['rows']))
                with self.assertRaises(ValueError): c.prepare(page_id, loaded, {'number': '+15555550123', 'text': 'Synthetic'})

    def test_explorer_reports_live_gnss_and_modem_sms_unavailability(self):
        from m3100_touch.api import ApiError
        cases = [('/api/openwrt/gnss', ApiError(503, 'unavailable', 'No verified native GNSS provider')),
                 ('/api/openwrt/modem', ApiError(502, 'native_command_failed', 'Native command failed', {'program': 'm3100-modem', 'rc': 1})),
                 ('/api/openwrt/sms', ApiError(502, 'native_command_failed', 'Native command failed', {'program': 'm3100-modem', 'rc': 1}))]
        for path, error in cases:
            with self.subTest(path=path):
                class UnavailableRead:
                    def request(self, method, requested, **kwargs):
                        self_outer.assertEqual((method, requested, kwargs), ('GET', path, {}))
                        raise error
                self_outer = self
                loaded = c.load(UnavailableRead(), 'explorer_read', {'path': path})
                self.assertTrue(loaded['reason'])
                self.assertIn(error.message, ' '.join(str(row['value']) for row in loaded['rows']))
                self.assertEqual((loaded['data'], loaded['fields'], loaded['actions']), ({}, [], []))
                self.assertNotIn('source', loaded)
                self.assertEqual(loaded['context'], {'path': path})

    def test_availability_classification_preserves_unrelated_and_malformed_errors(self):
        from m3100_touch.api import ApiError
        cases = [(401, 'unauthorized', {'program': 'm3100-modem', 'rc': 1}),
                 (403, 'forbidden', {'program': 'm3100-modem', 'rc': 1}),
                 (404, 'not_found', {'program': 'm3100-modem', 'rc': 1}),
                 (502, 'request_failed', {'program': 'm3100-modem', 'rc': 1}),
                 (502, 'invalid_response', {'program': 'm3100-modem', 'rc': 1}),
                 (502, 'native_command_failed', {'program': 'unexpected-provider', 'rc': 1}),
                 (503, 'invalid_response', None)]
        for page_id, ctx in [('modem', {}), ('sms_send', {}), ('explorer_read', {'path': '/api/openwrt/modem'})]:
            for status, code, details in cases:
                with self.subTest(page=page_id, status=status, code=code, details=details):
                    error = ApiError(status, code, 'Synthetic failure', details)
                    class FailedRead:
                        def request(self, *args, **kwargs): raise error
                    with self.assertRaises(ApiError) as raised: c.load(FailedRead(), page_id, ctx)
                    self.assertIs(raised.exception, error)
        error = ApiError(502, 'native_command_failed', 'Native command failed', {'program': 'm3100-modem', 'rc': 1})
        class UnrelatedRead:
            def request(self, *args, **kwargs): raise error
        for page_id, ctx in [('status', {}), ('explorer_read', {'path': '/api/status'}), ('gnss', {})]:
            with self.subTest(unrelated_page=page_id):
                with self.assertRaises(ApiError) as raised: c.load(UnrelatedRead(), page_id, ctx)
                self.assertIs(raised.exception, error)

    def test_explorer_availability_handling_keeps_nonsecret_read_whitelist(self):
        class NoRequests:
            def request(self, *args, **kwargs): raise AssertionError('Forbidden diagnostics request')
        for path in ('/api/wifi/primary/secret', '/api/tools/audit/export', '/api/cellular/connect', '/api/openwrt/modem?command=anything', '/api/unknown'):
            with self.subTest(path=path):
                with self.assertRaises(ValueError): c.load(NoRequests(), 'explorer_read', {'path': path})

    def test_unavailable_diagnostics_are_bounded_and_do_not_expose_secret_fields(self):
        from m3100_touch.api import ApiError
        details = {'program': 'm3100-modem', 'rc': 1, 'password': 'synthetic-private', 'nested': {'token': 'synthetic-token'}}
        details.update({'item_' + str(i): 'x' * 4096 for i in range(100)})
        class FailedProvider:
            def request(self, *args, **kwargs):
                raise ApiError(502, 'native_command_failed', 'Native command failed', details)
        loaded = c.load(FailedProvider(), 'sms')
        self.assertLessEqual(len(loaded['rows']), 10)
        self.assertTrue(all(len(str(row['value'])) <= 1024 for row in loaded['rows']))
        self.assertNotIn('synthetic-private', repr(loaded))
        self.assertNotIn('synthetic-token', repr(loaded))

    def test_all_native_mutable_fields_are_forms(self):
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'backend'))
        from m3100ui.native_firewall import FIELDS
        for plural, singular in [('defaults', 'defaults'), ('zones', 'zone'), ('forwardings', 'forwarding'), ('rules', 'rule'), ('redirects', 'redirect')]:
            self.assertEqual(set(FIELDS[singular]), {f['key'] for f in c.firewall_fields(plural)})
        self.assertEqual({f['key'] for f in c.INTERFACE}, {'enabled','proto','device','ipaddr','netmask','gateway','dns','metric','apn','username','password','auth','pdptype','ipv6','peerdns'})
        self.assertEqual({f['key'] for f in c.WIFI}, {'enabled','mode','device','network','ssid','security','password','hidden','isolation','wps'})

    def test_firewall_unavailable_runtime_blocks_create(self):
        loaded = c.load(Client({'/api/openwrt/firewall': {'zones': [], 'rules': [], 'runtime': {'available': False}}}), 'firewall_rules_edit', {'create': True})
        self.assertTrue(loaded['reason'])
        self.assertNotIn('save', [a['id'] for a in loaded['actions']])

    def test_firewall_legacy_syn_flood_alias_is_displayed_truthfully(self):
        loaded = c.load(Client({'/api/openwrt/firewall': {'defaults': {'options': {'syn_flood': '1'}, 'editable': True}}}), 'firewall_defaults_edit')
        self.assertTrue(loaded['data']['synflood_protect'])
        tx = c.prepare('firewall_defaults_edit', loaded, {'drop_invalid': True})
        self.assertEqual(tx['body'], {'drop_invalid': True})

    def test_explicit_wps_off_survives_hidden_destination_state(self):
        client = Client({'/api/wifi/primary': {'ssid': 'Main', 'mode': 'ap', 'security': 'wpa2', 'password_set': True, 'hidden': False, 'wps': True}})
        loaded = c.load(client, 'wifi_primary')
        for transition in ({'security': 'wpa3'}, {'hidden': True}):
            with self.subTest(transition=transition):
                tx = c.prepare('wifi_primary', loaded, dict(transition, wps=False))
                self.assertIs(tx['body']['wps'], False)
                self.assertTrue(set(transition) <= set(tx['body']))
        loaded['data']['wps'] = False
        tx = c.prepare('wifi_primary', loaded, {'security': 'wpa3', 'wps': True})
        self.assertNotIn('wps', tx['body'])

    def test_block_policy_refusal_does_not_hide_eligible_guest(self):
        from m3100_touch.api import ApiError
        calls = []
        class PolicyClient:
            def request(self, method, path, **kwargs):
                calls.append(path)
                if path == '/api/touchscreen/blocked': raise ApiError(503, 'unavailable', 'Primary uses allow-list')
                if path == '/api/openwrt/wifi': return {'interfaces': [{'name': 'primary', 'mode': 'ap', 'enabled': True}, {'name': 'guest', 'mode': 'ap', 'enabled': True}, {'name': 'disabled', 'mode': 'ap', 'enabled': False}, {'name': 'station', 'mode': 'sta', 'enabled': True}]}
                if path == '/api/touchscreen/blocked?ap=guest': return {'ap': 'guest', 'aps': ['primary', 'guest'], 'policy': 'deny', 'macs': [], 'available': True}
                raise AssertionError(path)
        client = PolicyClient()
        refused = c.load(client, 'blocked')
        choose = next(row for row in refused['rows'] if row.get('target') == 'blocked_aps')
        aps = c.load(client, choose['target'], choose['context'])
        self.assertEqual({row['context']['ap'] for row in aps['rows'] if row.get('target') == 'blocked'}, {'primary', 'guest'})
        guest = next(row for row in aps['rows'] if row.get('context', {}).get('ap') == 'guest')
        page = c.load(client, guest['target'], guest['context'])
        self.assertEqual(page['data']['ap'], 'guest')
        self.assertIn('save', [action['id'] for action in page['actions']])
        menu = c.load(client, 'wifi_menu')
        self.assertTrue(any(row.get('target') == 'blocked_aps' for row in menu['rows']))

    def test_band_gap_explained_without_removing_native_radio_controls(self):
        client = Client({'/api/openwrt/wifi': {'radios': [{'name': 'radio0', 'channel': 'auto', 'country': 'US'}]}})
        for page_id, ctx in [('radios', {}), ('radio_edit', {'name': 'radio0'})]:
            page = c.load(client, page_id, ctx)
            explanatory = ' '.join(str(row.get('value', '')) for row in page['rows']).lower()
            self.assertIn('band selection unavailable', explanatory)
        editor = c.load(client, 'radio_edit', {'name': 'radio0'})
        self.assertEqual({f['key'] for f in editor['fields']}, {'enabled', 'channel', 'country', 'txpower'})
        self.assertIn('save', [a['id'] for a in editor['actions']])
        help_text = ' '.join(row.get('value', '') for row in c.load(client, 'help_wifi')['rows']).lower()
        self.assertNotIn('controls band/channel', help_text)
        self.assertIn('band selection unavailable', help_text)
        record = next(item for item in c.STOCK_COVERAGE if item['inferred_name'] == 'WifiBandSelection')
        self.assertIn('no typed', record['reason'].lower())

