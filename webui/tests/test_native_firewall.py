"""Native firewall contracts; only the local process boundary is simulated."""
import copy
import json
import os
import base64
import re
import shutil
import subprocess
import http.client
import threading
import unittest
from unittest.mock import patch

from tests import test_openwrt
from tests.helpers import MockClient, ROOT
from m3100ui.errors import ApiError


class NativeFirewallTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_openwrt.NativeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.uci['network']['usbmgmt'] = {'.type': 'interface', 'proto': 'static',
            'device': 'usb0', 'ipaddr': '192.168.77.1', 'netmask': '255.255.255.0'}
        self.fixture.uci['firewall'] = {
            'cfg001': {'.type': 'defaults', 'input': 'REJECT', 'output': 'ACCEPT', 'forward': 'REJECT', 'syn_flood': '1'},
            'cfg002': {'.type': 'zone', 'name': 'lan', 'network': ['lan'], 'input': 'ACCEPT', 'output': 'ACCEPT', 'forward': 'ACCEPT'},
            'cfg003': {'.type': 'zone', 'name': 'wan', 'network': ['wan'], 'input': 'REJECT', 'output': 'ACCEPT', 'forward': 'DROP', 'masq': '1'},
            'usbmgmt': {'.type': 'zone', 'name': 'usbmgmt', 'network': ['usbmgmt'], 'input': 'ACCEPT', 'output': 'ACCEPT', 'forward': 'REJECT'}}
        self.committed = copy.deepcopy(self.fixture.uci['firewall'])
        self.pending = False
        self.fail_action = None
        self.calls = []
        self.app = self.fixture.app()
        self.native = self.app.adapters.native
        self.native.command = self.command
        self.client = MockClient(self.app)
        original_file = os.path.isfile
        self.addCleanup(patch.stopall)
        patch('m3100ui.adapters.openwrt.os.path.isfile', side_effect=lambda p: p in
              ('/sbin/fw4', '/usr/sbin/nft', '/etc/init.d/firewall', '/etc/init.d/network') or original_file(p)).start()
        original_access = os.access
        patch('m3100ui.adapters.openwrt.os.access', side_effect=lambda p, mode: p in
              ('/sbin/fw4', '/usr/sbin/nft', '/etc/init.d/firewall') or original_access(p, mode)).start()

    def command(self, argv):
        self.calls.append(argv)
        action = argv[3] if argv[:3] == ['ubus', 'call', 'uci'] else argv[1] if len(argv) > 1 else ''
        if action == self.fail_action:
            raise ApiError(502, 'native_command_failed', 'Simulated boundary failure')
        if argv[:3] == ['ubus', 'call', 'uci']:
            p = json.loads(argv[4])
            if p['config'] != 'firewall':
                return self.fixture.command(argv)
            cfg = self.fixture.uci['firewall']
            if action == 'get':
                return {'values': copy.deepcopy(cfg)}
            if action == 'changes':
                # Pinned rpcd's config-specific changes response is an array.
                return {'changes': [['set', 'example', 'enabled', '1']] if self.pending else []}
            if action == 'set':
                cfg[p['section']].update(p['values'])
                self.pending = True
            if action == 'add':
                cfg[p['name']] = dict(p['values'], **{'.type': p['type']})
                self.pending = True
                return {'section': p['name']}
            if action == 'delete':
                if 'option' in p:
                    cfg[p['section']].pop(p['option'], None)
                else:
                    del cfg[p['section']]
                self.pending = True
            if action == 'commit':
                self.committed = copy.deepcopy(cfg)
                self.pending = False
            if action == 'revert':
                self.fixture.uci['firewall'] = copy.deepcopy(self.committed)
                self.pending = False
            return {}
        if argv == ['/sbin/fw4', 'check']:
            return 'Ruleset passes nftables check.'
        if argv == ['/usr/sbin/nft', '-j', 'list', 'table', 'inet', 'fw4']:
            return {'nftables': [{'table': {'family': 'inet', 'name': 'fw4', 'handle': 1}}]}
        if argv == ['/etc/init.d/firewall', 'reload']:
            return {}
        return self.fixture.command(argv)

    def create(self, kind='redirects', **changes):
        data = {'section': 'webserver', 'name': 'Web server', 'src': 'wan', 'dest': 'lan',
                'proto': ['tcp', 'udp'], 'family': 'ipv4', 'src_dport': '8080-8081',
                'dest_ip': '192.168.8.10', 'dest_port': '80-81', 'reflection': False}
        data.update(changes)
        return self.client.post('/api/openwrt/firewall/' + kind, dict(data, confirm=True))

    def mutation_calls(self):
        return [a for a in self.calls if a[:3] == ['ubus', 'call', 'uci'] and a[3] in ('set', 'add', 'delete', 'commit')]

    def test_read_reports_actual_table_and_protected_management_zone(self):
        data = self.client.get('/api/openwrt/firewall')
        self.assertTrue(data['runtime']['loaded'])
        self.assertTrue(data['runtime']['available'])
        usb = next(x for x in data['zones'] if x['section'] == 'usbmgmt')
        self.assertFalse(usb['editable'])
        self.assertIn('USB', usb['reason'])
        self.assertEqual(data['protection']['networks'], ['usbmgmt'])

    def test_redirect_translation_check_commit_readback_and_reload_order(self):
        out = self.create()
        self.assertEqual((out['saved'], out['applied']), (True, True))
        self.assertEqual(self.committed['webserver']['target'], 'DNAT')
        self.assertEqual(self.committed['webserver']['proto'], ['tcp', 'udp'])
        self.assertEqual(self.committed['webserver']['reflection'], '0')
        self.assertEqual(self.committed['webserver']['src_dport'], '8080-8081')
        stages = [a[3] if a[:3] == ['ubus', 'call', 'uci'] else a[1] for a in self.calls]
        self.assertLess(stages.index('check'), stages.index('commit'))
        self.assertLess(stages.index('commit'), stages.index('reload'))
        self.assertEqual(next(x for x in out['readback']['redirects'] if x['section'] == 'webserver')['options']['dest_ip'], '192.168.8.10')

    def test_defaults_zone_forwarding_rule_update_clear_and_delete(self):
        self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'drop_invalid': True, 'confirm': True})
        self.assertEqual(self.committed['cfg001']['drop_invalid'], '1')
        self.client.put('/api/openwrt/firewall/zones/cfg003', {'masq': False, 'confirm': True})
        self.assertEqual(self.committed['cfg003']['masq'], '0')
        out = self.client.post('/api/openwrt/firewall/forwardings', {'section': 'lanwan', 'src': 'lan', 'dest': 'wan', 'confirm': True})
        self.assertTrue(out['saved'])
        self.client.post('/api/openwrt/firewall/rules', {'section': 'block', 'name': 'Block host', 'src': 'lan', 'dest': 'wan',
            'proto': ['tcp'], 'dest_port': '443', 'src_ip': '192.168.8.20/32', 'src_mac': '02:00:00:00:00:01', 'target': 'DROP', 'confirm': True})
        self.client.put('/api/openwrt/firewall/rules/block', {'src_ip': '', 'confirm': True})
        self.assertNotIn('src_ip', self.committed['block'])
        self.client.delete('/api/openwrt/firewall/forwardings/lanwan', {'confirm': True})
        self.assertNotIn('lanwan', self.committed)
        self.client.delete('/api/openwrt/firewall/rules/block', {'confirm': True})
        self.assertNotIn('block', self.committed)

    def test_auth_confirmation_and_public_manifest(self):
        MockClient(self.app, login=False).get('/api/openwrt/firewall', expect=401)
        MockClient(self.app, login=False).post('/api/openwrt/firewall/rules', {}, expect=401)
        self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP'}, expect=428)
        self.assertEqual(self.mutation_calls(), [])
        self.assertIn('/app/pages/firewall/openwrt.js', self.client.get('/api/ui/pages'))
        self.assertNotIn('/app/pages/firewall/openwrt.js', MockClient().get('/api/ui/pages'))

    def test_invalid_or_unsupported_inputs_have_no_side_effect(self):
        invalid = [{'section': 'bad;cmd'}, {'section': 'bad-name'}, {'name': 'bad\nname'}, {'src': '*'}, {'src': 'missing'},
            {'family': 'inet'}, {'family': 'ipv6'}, {'dest_ip': '192.168.77.5'}, {'dest_ip': '127.0.0.1'}, {'dest_ip': '224.0.0.1'},
            {'dest_ip': '192.168.8.10/24'}, {'dest_ip': '192.168.9.10'}, {'dest_ip': '192.168.8.0'}, {'dest_ip': '192.168.8.255'},
            {'dest_ip': '192.168.8.1'}, {'proto': ['tcp', 'tcp']}, {'proto': ['icmp']}, {'src_dport': '0'},
            {'src_dport': '90-80'}, {'src_dport': '80 81'}, {'src_dport': '1-65536'}, {'dest_port': '80'},
            {'src_mac': 'junk'}, {'src_ip': '192.168.77.0/24'}, {'src_ip': '0.0.0.0/0'},
            {'target': 'SNAT'}, {'shell': 'nft flush ruleset'}, {'reflection': []}]
        base = {'section': 'bad', 'name': 'Bad redirect', 'src': 'wan', 'dest': 'lan', 'proto': ['tcp'], 'family': 'ipv4',
                'src_dport': '8080-8081', 'dest_ip': '192.168.8.10', 'dest_port': '80-81'}
        for change in invalid:
            with self.subTest(change=change):
                self.client.post('/api/openwrt/firewall/redirects', dict(base, **change, confirm=True), expect=400)
        self.assertEqual(self.mutation_calls(), [])

    def test_conflicting_redirects_and_duplicate_forwardings_are_rejected(self):
        self.create()
        self.client.post('/api/openwrt/firewall/redirects', {'section': 'overlap', 'name': 'Overlap', 'src': 'wan', 'dest': 'lan',
            'proto': ['tcp'], 'family': 'ipv4', 'src_dport': '8081', 'dest_ip': '192.168.8.12', 'dest_port': '81', 'confirm': True}, expect=409)
        self.client.post('/api/openwrt/firewall/forwardings', {'section': 'lanwan', 'src': 'lan', 'dest': 'wan', 'confirm': True})
        self.client.post('/api/openwrt/firewall/forwardings', {'section': 'duplicate', 'src': 'lan', 'dest': 'wan', 'confirm': True}, expect=409)

    def test_management_zone_network_subnet_and_wildcard_cannot_be_changed(self):
        self.client.put('/api/openwrt/firewall/zones/usbmgmt', {'input': 'DROP', 'confirm': True}, expect=403)
        self.client.put('/api/openwrt/firewall/zones/cfg003', {'network': ['wan', 'usbmgmt'], 'confirm': True}, expect=403)
        self.client.put('/api/openwrt/firewall/zones/cfg003', {'network': ['lan'], 'confirm': True}, expect=409)
        self.client.post('/api/openwrt/firewall/forwardings', {'section': 'unsafe', 'src': 'usbmgmt', 'dest': 'wan', 'confirm': True}, expect=403)
        self.client.post('/api/openwrt/firewall/rules', {'section': 'unsafe', 'src': 'wan', 'dest': '*', 'target': 'ACCEPT', 'confirm': True}, expect=400)
        self.client.put('/api/openwrt/interfaces/usbmgmt', {'ipaddr': '192.168.77.2', 'confirm': True}, expect=403)
        self.client.put('/api/openwrt/interfaces/lan', {'device': 'usb0', 'confirm': True}, expect=403)
        self.client.post('/api/openwrt/interfaces/usbmgmt/down', {'confirm': True}, expect=403)
        self.assertEqual(self.mutation_calls(), [])

    def test_preexisting_staging_is_not_committed_or_reverted(self):
        self.pending = True
        self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, expect=409)
        self.assertEqual(self.mutation_calls(), [])
        self.assertFalse(any(a[3:4] == ['revert'] for a in self.calls))

    def test_check_failure_reverts_without_commit_or_reload(self):
        self.fail_action = 'check'
        out = self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, expect=502)
        self.assertEqual(out['error']['details']['saved'], False)
        self.assertTrue(out['error']['details']['staging_reverted'])
        self.assertEqual(self.fixture.uci['firewall'], self.committed)
        self.assertFalse(any(a[3:4] == ['commit'] or a == ['/etc/init.d/firewall', 'reload'] for a in self.calls))

    def test_commit_failure_reports_verified_unsaved_state(self):
        self.fail_action = 'commit'
        out = self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, expect=502)
        self.assertIs(out['error']['details']['saved'], False)
        self.assertFalse(any(a == ['/etc/init.d/firewall', 'reload'] for a in self.calls))

    def test_reload_failure_preserves_committed_readback_and_audit(self):
        self.fail_action = 'reload'
        out = self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, expect=502)
        self.assertEqual(self.committed['cfg001']['input'], 'DROP')
        details = out['error']['details']
        self.assertIs(details['saved'], True)
        self.assertIsNone(details['applied'])
        self.assertIs(details['reload_requested'], True)
        self.assertIs(details['reload_command_failed'], True)
        self.assertEqual(details['readback']['defaults']['options']['input'], 'DROP')
        self.assertFalse(any(a[3:4] == ['revert'] for a in self.calls))
        self.assertIn('openwrt.firewall', json.dumps(self.client.get('/api/tools/audit')))

    def test_missing_kernel_table_and_missing_programs_fail_closed(self):
        self.fail_action = '-j'
        view = self.client.get('/api/openwrt/firewall')
        self.assertFalse(view['runtime']['available'])
        self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, expect=503)
        self.assertEqual(self.mutation_calls(), [])
        with patch('m3100ui.adapters.openwrt.os.path.isfile', return_value=False):
            self.assertFalse(self.client.get('/api/openwrt/firewall')['runtime']['available'])

    def test_command_boundary_accepts_only_fixed_firewall_check_and_query(self):
        native = self.fixture.mod.NativePlatform(self.fixture.config)
        for argv in (['/sbin/fw4', 'flush'], ['/sbin/fw4', 'check', '-f', '/tmp/x'],
                     ['/usr/sbin/nft', 'flush', 'ruleset'], ['/usr/sbin/nft', '-j', 'list', 'ruleset']):
            with self.assertRaises(ApiError):
                native.command(argv)
        with patch.object(native, '_execute', return_value='Ruleset passes nftables check.'):
            self.assertEqual(native.command(['/sbin/fw4', 'check']), 'Ruleset passes nftables check.')
        with patch.object(native, '_execute', return_value='{"nftables":[]}'):
            self.assertEqual(native.command(['/usr/sbin/nft', '-j', 'list', 'table', 'inet', 'fw4']), {'nftables': []})

    def test_protected_recovery_path_cannot_be_stopped_through_service_controls(self):
        for name, action in [('firewall', 'stop'), ('network', 'stop'), ('network', 'restart')]:
            self.client.post('/api/openwrt/services/' + name + '/' + action, {'confirm': True}, expect=403)
        self.client.post('/api/openwrt/services/network/reload', {'confirm': True})
        self.assertNotIn(['/etc/init.d/firewall', 'stop'], self.calls)
        self.assertNotIn(['/etc/init.d/network', 'restart'], self.calls)
        self.assertIn(['/etc/init.d/network', 'reload'], self.calls)

    def test_zone_disable_or_family_change_cannot_silently_skip_active_references(self):
        self.client.post('/api/openwrt/firewall/forwardings', {'section': 'lanwan', 'src': 'lan', 'dest': 'wan', 'family': 'ipv6', 'confirm': True})
        self.calls.clear()
        self.client.put('/api/openwrt/firewall/zones/cfg003', {'enabled': False, 'confirm': True}, expect=409)
        self.client.put('/api/openwrt/firewall/zones/cfg003', {'family': 'ipv4', 'confirm': True}, expect=409)
        self.assertEqual(self.mutation_calls(), [])

    def test_unsupported_wildcard_redirect_blocks_new_overlapping_source(self):
        self.fixture.uci['firewall']['external'] = {'.type': 'redirect', 'src': '*', 'proto': 'tcp',
            'src_dport': '8080', 'dest': 'lan', 'dest_ip': '192.168.8.10', 'dest_port': '80'}
        self.committed = copy.deepcopy(self.fixture.uci['firewall'])
        self.client.post('/api/openwrt/firewall/redirects', {'section': 'new', 'src': 'wan', 'dest': 'lan', 'proto': ['tcp'],
            'family': 'ipv4', 'src_dport': '8080', 'dest_ip': '192.168.8.20', 'dest_port': '80', 'confirm': True}, expect=409)
        self.assertEqual(self.mutation_calls(), [])

    def test_conflicting_rule_action_is_rejected_and_redirect_update_delete_work(self):
        rule = {'src': 'lan', 'dest': 'wan', 'proto': ['tcp'], 'dest_port': '443', 'family': 'ipv4', 'target': 'ACCEPT'}
        self.client.post('/api/openwrt/firewall/rules', dict(rule, section='allow', confirm=True))
        self.client.post('/api/openwrt/firewall/rules', dict(rule, section='deny', target='DROP', confirm=True), expect=409)
        self.create()
        self.client.put('/api/openwrt/firewall/redirects/webserver', {'dest_ip': '192.168.8.11', 'confirm': True})
        self.assertEqual(self.committed['webserver']['dest_ip'], '192.168.8.11')
        self.client.delete('/api/openwrt/firewall/redirects/webserver', {'confirm': True})
        self.assertNotIn('webserver', self.committed)

    def test_readback_mismatch_prevents_reload_and_does_not_claim_requested_state(self):
        original = self.native.command
        committed = [False]
        def mismatch(argv):
            out = original(argv)
            if argv[:4] == ['ubus', 'call', 'uci', 'commit']:
                committed[0] = True
            if committed[0] and argv[:4] == ['ubus', 'call', 'uci', 'get'] and json.loads(argv[4])['config'] == 'firewall':
                out['values']['cfg001']['input'] = 'REJECT'
            return out
        self.native.command = mismatch
        out = self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, expect=502)
        self.assertEqual(out['error']['code'], 'native_readback_mismatch')
        self.assertIsNone(out['error']['details']['applied'])
        self.assertEqual(out['error']['details']['readback']['defaults']['options']['input'], 'REJECT')
        self.assertNotIn(['/etc/init.d/firewall', 'reload'], self.calls)

    def test_kernel_table_loss_after_successful_reload_is_an_unverified_apply(self):
        original = self.native.command
        def flapping(argv):
            out = original(argv)
            if argv == ['/etc/init.d/firewall', 'reload']:
                self.fail_action = '-j'
            return out
        self.native.command = flapping
        out = self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, expect=502)
        details = out['error']['details']
        self.assertIs(details['saved'], True)
        self.assertIsNone(details['applied'])
        self.assertIs(details['reload_succeeded'], True)
        self.assertIs(details['readback']['runtime']['available'], False)

    def test_missing_or_altered_management_policy_blocks_firewall_writes(self):
        self.fixture.uci['firewall']['usbmgmt']['forward'] = 'ACCEPT'
        self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, expect=503)
        self.assertEqual(self.mutation_calls(), [])

    def test_unsupported_root_rules_are_visible_and_preserved_by_other_edits(self):
        self.fixture.uci['firewall']['ping'] = {'.type': 'rule', 'name': 'Allow ping', 'src': 'wan', 'proto': 'icmp',
            'icmp_type': 'echo-request', 'family': 'ipv4', 'target': 'ACCEPT'}
        self.committed = copy.deepcopy(self.fixture.uci['firewall'])
        before = copy.deepcopy(self.committed['ping'])
        row = self.client.get('/api/openwrt/firewall')['rules'][0]
        self.assertFalse(row['editable'])
        self.client.put('/api/openwrt/firewall/rules/ping', {'target': 'DROP', 'confirm': True}, expect=400)
        self.client.put('/api/openwrt/firewall/defaults', {'forward': 'DROP', 'confirm': True})
        self.assertEqual(self.committed['ping'], before)

    def test_ipv6_redirect_requires_a_known_destination_subnet(self):
        data = {'section': 'v6web', 'name': 'IPv6 web', 'src': 'wan', 'dest': 'lan', 'family': 'ipv6', 'proto': ['tcp'],
                'src_dport': '8080', 'dest_ip': 'fd00:8::20', 'dest_port': '80', 'confirm': True}
        self.client.post('/api/openwrt/firewall/redirects', data, expect=400)
        self.fixture.uci['network']['lan']['ip6addr'] = ['fd00:8::1/64']
        out = self.client.post('/api/openwrt/firewall/redirects', data)
        self.assertTrue(out['applied'])
        self.assertEqual(self.committed['v6web']['dest_ip'], 'fd00:8::20')

    def test_native_firewall_http_keeps_auth_csrf_and_confirmation(self):
        from m3100ui.httpd import make_server
        srv = make_server(self.app, '127.0.0.1', 0)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection('127.0.0.1', srv.server_address[1], timeout=5)
            data = json.dumps(body).encode() if body is not None else None
            conn.request(method, path, data, dict({'Host': '127.0.0.1', 'Content-Type': 'application/json'}, **(headers or {})))
            response = conn.getresponse()
            raw = response.read()
            result = (response.status, response.getheader('Set-Cookie'), json.loads(raw))
            conn.close()
            return result
        self.assertEqual(request('GET', '/api/openwrt/firewall')[0], 401)
        status, cookie, login = request('POST', '/api/login', {'password': self.app._password()})
        self.assertEqual(status, 200)
        headers = {'Cookie': cookie.split(';')[0]}
        self.assertEqual(request('PUT', '/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, headers)[0], 403)
        headers['X-CSRF-Token'] = login['csrf']
        self.assertEqual(request('PUT', '/api/openwrt/firewall/defaults', {'input': 'DROP'}, headers)[0], 428)
        self.assertEqual(self.mutation_calls(), [])

    def test_preserved_unsupported_options_never_expose_secret_values(self):
        self.fixture.uci['firewall']['custom'] = {'.type': 'rule', 'src': 'wan', 'target': 'ACCEPT', 'password': 'must-not-leak'}
        out = self.client.get('/api/openwrt/firewall')
        self.assertNotIn('must-not-leak', json.dumps(out))
        self.assertIs(out['rules'][0]['options']['password_set'], True)

    def test_other_zone_cannot_capture_management_device_or_subnet(self):
        for option, value in [('device', ['usb0']), ('device', ['usb*']), ('subnet', ['192.168.77.0/24'])]:
            with self.subTest(option=option, value=value):
                self.fixture.uci['firewall']['cfg003'][option] = value
                self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, expect=503)
                self.fixture.uci['firewall']['cfg003'].pop(option)
        self.assertEqual(self.mutation_calls(), [])

    def test_candidate_zone_assignment_cannot_capture_unzoned_management_subnet(self):
        self.fixture.uci['network']['spare'] = {'.type': 'interface', 'proto': 'static',
            'device': 'eth1', 'ipaddr': '192.168.77.2', 'netmask': '255.255.255.0'}
        original = copy.deepcopy(self.committed)
        self.assertIsNone(self.client.get('/api/openwrt/firewall')['protection']['problem'])
        self.calls.clear()
        out = self.client.put('/api/openwrt/firewall/zones/cfg003',
            {'network': ['wan', 'spare'], 'confirm': True}, expect=403)
        self.assertEqual(out['error']['code'], 'management_protected')
        self.assertEqual(self.fixture.uci['firewall'], original)
        self.assertEqual(self.committed, original)
        self.assertEqual(self.mutation_calls(), [])
        self.assertNotIn(['/sbin/fw4', 'check'], self.calls)
        self.assertNotIn(['/etc/init.d/firewall', 'reload'], self.calls)
        self.assertFalse(any(a[:3] == ['ubus', 'call', 'uci'] and a[3] == 'revert' for a in self.calls))
        self.assertFalse(any(a[:4] == ['ubus', 'call', 'uci', 'changes'] for a in self.calls))

    def test_existing_zone_captures_cidr_and_list_ipv4_representations(self):
        for addresses in ['192.168.77.2/24', ['10.1.0.2/24', '192.168.77.2/24']]:
            with self.subTest(addresses=addresses):
                self.fixture.uci['network']['spare'] = {'.type': 'interface', 'proto': 'static', 'device': 'eth1', 'ipaddr': addresses}
                self.fixture.uci['firewall']['cfg003']['network'] = ['wan', 'spare']
                self.calls.clear()
                self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, expect=503)
                self.assertEqual(self.mutation_calls(), [])

    def test_candidate_zone_captures_cidr_and_list_ipv4_representations(self):
        for addresses in ['192.168.77.2/24', ['10.1.0.2/24', '192.168.77.2/24']]:
            with self.subTest(addresses=addresses):
                self.fixture.uci['network']['spare'] = {'.type': 'interface', 'proto': 'static', 'device': 'eth1', 'ipaddr': addresses}
                self.calls.clear()
                self.client.put('/api/openwrt/firewall/zones/cfg003', {'network': ['wan', 'spare'], 'confirm': True}, expect=403)
                self.assertEqual(self.mutation_calls(), [])
                self.assertNotIn(['/sbin/fw4', 'check'], self.calls)
                self.assertNotIn(['/etc/init.d/firewall', 'reload'], self.calls)

    def test_protected_device_aliases_and_alias_chains_cannot_join_zones(self):
        self.fixture.uci['network']['alias1'] = {'.type': 'interface', 'proto': 'none', 'device': '@usbmgmt'}
        for device in ['@usbmgmt', '@alias1']:
            with self.subTest(device=device):
                self.fixture.uci['network']['spare'] = {'.type': 'interface', 'proto': 'none', 'device': device}
                self.calls.clear()
                self.client.put('/api/openwrt/firewall/zones/cfg003', {'network': ['wan', 'spare'], 'confirm': True}, expect=403)
                self.assertEqual(self.mutation_calls(), [])

    def test_existing_zone_device_alias_and_interface_controls_are_protected(self):
        self.fixture.uci['network']['spare'] = {'.type': 'interface', 'proto': 'none', 'device': '@usbmgmt'}
        self.fixture.uci['firewall']['cfg003']['network'] = ['wan', 'spare']
        self.client.put('/api/openwrt/firewall/defaults', {'input': 'DROP', 'confirm': True}, expect=503)
        self.client.put('/api/openwrt/interfaces/spare', {'proto': 'dhcp', 'confirm': True}, expect=403)
        self.client.post('/api/openwrt/interfaces/spare/down', {'confirm': True}, expect=403)
        self.assertEqual(self.mutation_calls(), [])
        self.assertFalse(any(a[:4] == ['ubus', 'call', 'network.interface.spare', 'down'] for a in self.calls))

    def test_interface_guard_retains_cidr_and_list_subnet_protection(self):
        for addresses in ['192.168.77.2/24', ['10.1.0.2/24', '192.168.77.2/24']]:
            with self.subTest(addresses=addresses):
                self.fixture.uci['network']['spare'] = {'.type': 'interface', 'proto': 'static', 'device': 'eth1', 'ipaddr': addresses}
                self.calls.clear()
                self.client.put('/api/openwrt/interfaces/spare', {'proto': 'dhcp', 'confirm': True}, expect=403)
                self.client.post('/api/openwrt/interfaces/spare/down', {'confirm': True}, expect=403)
                self.assertEqual(self.mutation_calls(), [])

    def test_ordinary_wan6_alias_remains_usable_without_usb_capture(self):
        self.fixture.uci['network']['wan6'] = {'.type': 'interface', 'proto': 'dhcpv6', 'device': '@wan'}
        self.fixture.uci['firewall']['cfg003']['network'] = ['wan', 'wan6']
        self.committed = copy.deepcopy(self.fixture.uci['firewall'])
        self.assertIsNone(self.client.get('/api/openwrt/firewall')['protection']['problem'])
        out = self.client.put('/api/openwrt/firewall/zones/cfg003', {'masq': False, 'confirm': True})
        self.assertTrue(out['saved'])
        self.assertTrue(out['applied'])


@unittest.skipUnless(shutil.which('node'), 'node unavailable')
class NativeFirewallConsumerTests(unittest.TestCase):
    def run_page(self, scenario):
        path = os.path.join(ROOT, 'web/app/pages/firewall/openwrt.js')
        self.assertTrue(os.path.isfile(path), 'Native firewall editor is missing')
        with open(path, encoding='utf-8') as f:
            source = re.sub(r'^import \{([^}]+)\} .*?;$', r'const {\1}=globalThis.deps;', f.read(), flags=re.M)
        url = 'data:text/javascript;base64,' + base64.b64encode(source.encode()).decode()
        script = '''
function node(tag,...children){return {tag,children,append(...x){this.children.push(...x)},appendChild(x){this.children.push(x);return x}};}
function text(v){if(v==null||typeof v==='boolean')return '';if(typeof v!=='object')return String(v);return (Array.isArray(v)?v:v.children||[]).map(text).join('');}
let forms=[],calls=[],toasts=[],fail=false;
const state={runtime:{available:true,loaded:true},protection:{networks:['usbmgmt'],zone:'usbmgmt',reason:'USB management is fixed',problem:null},networks:['lan','wan'],
defaults:{section:'cfg001',editable:true,options:{input:'REJECT',output:'ACCEPT',forward:'REJECT'}},
zones:[{section:'cfg002',editable:true,options:{name:'lan',network:['lan'],input:'ACCEPT',output:'ACCEPT',forward:'ACCEPT'}},
{section:'usbmgmt',editable:false,reason:'USB fixed policy',options:{name:'usbmgmt',network:['usbmgmt'],input:'ACCEPT',output:'ACCEPT',forward:'REJECT'}}],forwardings:[],rules:[],redirects:[],other_sections:[]};
const root=node('root');
globalThis.deps={h:node,clear:n=>{n.children=[];return n},card:(title,body)=>node('card',title,body),alert:(...x)=>node('alert',...x),
badge:(...x)=>node('badge',...x),button:(label,action,opts)=>({tag:'button',children:[label],action,opts}),errorBox:e=>node('error',e.message),toast:m=>toasts.push(m),
form:(host,cfg)=>{forms.push(cfg);return {}},get:async path=>{calls.push(['GET',path]);return state},
post:async(path,body,opts)=>{calls.push(['POST',path,body,opts]);if(fail){const e=Error('Native request failed');e.details={saved:true,applied:null,reload_requested:true,reload_command_failed:true,readback:state};throw e;}
return {saved:true,applied:true,readback:state}},
put:async(path,body,opts)=>{calls.push(['PUT',path,body,opts]);return {saved:true,applied:true,readback:state}},
del:async(path,body,opts)=>{calls.push(['DELETE',path,body,opts]);return {saved:true,applied:true,readback:state}}};
const page=(await import(PAGE)).default;await page.render(root);
SCENARIO
'''.replace('PAGE', json.dumps(url)).replace('SCENARIO', scenario)
        proc = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', 'replace')[-3000:])
        return json.loads(proc.stdout)

    def test_actual_editor_creates_typed_redirect_with_confirmation(self):
        data = self.run_page('''
const create=forms.find(x=>x.title==='Add port redirect');
const values=await create.load();Object.assign(values,{section:'webserver',name:'Web',src:'wan',dest:'lan',proto:'tcp udp',family:'ipv4',src_dport:'8080',dest_ip:'192.168.8.2',dest_port:'80'});
await create.save({},values);
console.log(JSON.stringify({call:calls.find(x=>x[0]==='POST'),usbEditable:forms.some(x=>x.title==='Zone: usbmgmt'),text:text(root)}));
''')
        self.assertEqual(data['call'][1], '/api/openwrt/firewall/redirects')
        self.assertEqual(data['call'][2]['proto'], ['tcp', 'udp'])
        self.assertEqual(data['call'][2]['section'], 'webserver')
        self.assertIn('confirmMessage', data['call'][3])
        self.assertFalse(data['usbEditable'])
        self.assertIn('USB', data['text'])

    def test_actual_editor_exposes_committed_but_failed_reload_and_throws(self):
        data = self.run_page('''
fail=true;const create=forms.find(x=>x.title==='Add port redirect');const values=await create.load();Object.assign(values,{section:'webserver',src:'wan',dest:'lan',src_dport:'8080',dest_ip:'192.168.8.2',dest_port:'80'});
let rejected=false;try{await create.save({},values)}catch(e){rejected=true}
console.log(JSON.stringify({rejected,text:text(root),toasts}));
''')
        self.assertTrue(data['rejected'])
        self.assertIn('saved', data['text'].lower())
        self.assertIn('reload could not be confirmed', data['text'].lower())
        self.assertEqual(data['toasts'], [])

    def test_actual_editor_disables_writes_when_kernel_table_is_absent(self):
        data = self.run_page('''
state.runtime={available:false,loaded:false,reason:'Kernel table unavailable'};forms=[];await page.render(root);
console.log(JSON.stringify({writeForms:forms.filter(x=>!x.readonly).length,text:text(root)}));
''')
        self.assertEqual(data['writeForms'], 0)
        self.assertIn('Kernel table unavailable', data['text'])

