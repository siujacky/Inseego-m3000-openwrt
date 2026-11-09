"""Local, synthetic WebUI preview. This file never executes a device command.

Run: python tools/preview.py [--stock] [--port 18088] [--modem-demo]
The preview and its synthetic credentials are not a router deployment.
"""
import argparse
import copy
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from m3100ui import config
from m3100ui.app import App
from m3100ui.adapters import Adapters
from m3100ui.adapters.mock import FakeSystem
from m3100ui.adapters.openwrt import NativePlatform
from m3100ui.errors import unavailable
from m3100ui.httpd import make_server

PREVIEW_PASSWORD = 'preview-admin'


class DemoSystem(FakeSystem):
    def power_supplies(self):
        return []

    def dhcp_leases(self):
        return [{'expires': int(time.time()) + 1800, 'mac': '02:00:00:00:00:02',
                 'ip': '192.168.8.101', 'hostname': 'demo-laptop', 'id': '*'}]


class DemoNative(NativePlatform):
    """Production configuration/validation methods with an in-memory boundary."""
    def __init__(self, cfg, modem_demo=False):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.system = DemoSystem()
        self.modem_demo = modem_demo
        self.calls = []
        self.uci = {
            'network': {
                'usbmgmt': {'.type': 'interface', 'proto': 'static', 'device': 'usb0', 'ipaddr': '192.168.77.1', 'netmask': '255.255.255.0'},
                'lan': {'.type': 'interface', 'proto': 'static', 'device': 'br-lan', 'ipaddr': '192.168.8.1', 'netmask': '255.255.255.0'},
                'wan': {'.type': 'interface', 'proto': 'qmi', 'device': '/dev/cdc-wdm0', 'apn': 'internet', 'password': 'synthetic-apn-secret'}},
            'wireless': {'radio0': {'.type': 'wifi-device', 'band': '2g', 'channel': 'auto', 'country': 'US', 'disabled': '0'},
                'default_radio0': {'.type': 'wifi-iface', 'device': 'radio0', 'network': 'lan', 'mode': 'ap', 'ssid': 'Preview network', 'encryption': 'psk2', 'key': 'synthetic-wifi-secret'}},
            'dhcp': {'lan': {'.type': 'dhcp', 'start': '100', 'limit': '100', 'leasetime': '12h', 'ra': 'disabled', 'dhcpv6': 'disabled'}},
            'system': {'cfg01': {'.type': 'system', 'hostname': 'demo-router', 'timezone': 'UTC0', 'zonename': 'UTC'}},
            'firewall': {'defaults': {'.type': 'defaults', 'input': 'REJECT', 'output': 'ACCEPT', 'forward': 'REJECT'},
                'usbmgmt': {'.type': 'zone', 'name': 'usbmgmt', 'network': ['usbmgmt'], 'input': 'ACCEPT', 'output': 'ACCEPT', 'forward': 'REJECT'},
                'lan': {'.type': 'zone', 'name': 'lan', 'network': ['lan'], 'input': 'ACCEPT', 'output': 'ACCEPT', 'forward': 'ACCEPT'}}}

    def password(self):
        return PREVIEW_PASSWORD

    def _execute(self, argv):
        raise unavailable('The host preview cannot execute commands')

    def command(self, argv):
        self.calls.append(copy.deepcopy(argv))
        if argv[:3] == ['ubus', 'call', 'uci']:
            action, payload = argv[3], json.loads(argv[4])
            package = self.uci.setdefault(payload['config'], {})
            if action == 'get':
                return {'values': copy.deepcopy(package)}
            if action == 'set':
                package.setdefault(payload['section'], {}).update(payload['values'])
            if action == 'add':
                package[payload['name']] = {'.type': payload['type'], **payload['values']}
                return {'section': payload['name']}
            if action == 'delete':
                package.pop(payload['section'], None)
            return {}
        if argv[:4] == ['ubus', 'call', 'system', 'board']:
            return {'model': 'Synthetic router preview', 'hostname': self.uci['system']['cfg01']['hostname'], 'release': {'description': 'OpenWrt · Demo data'}}
        if argv[:4] == ['ubus', 'call', 'system', 'info']:
            return {'uptime': 18000, 'localtime': int(time.time())}
        if argv[:4] == ['ubus', 'call', 'network.interface', 'dump']:
            return {'interface': [{'interface': name, 'up': name != 'wan', 'l3_device': item.get('device'), 'ipv4-address': [{'address': item['ipaddr'], 'mask': 24}] if item.get('ipaddr') else []}
                                  for name, item in self.uci['network'].items()]}
        if argv[:4] == ['ubus', 'call', 'network.wireless', 'status']:
            return {'radio0': {'up': False, 'pending': False}}
        if argv[:1] == ['logread']:
            return 'Demo data: services initialized\nDemo data: modem provider unavailable\nDemo data: no router was contacted'
        return {}

    def modem(self, action='status', *args):
        if action == 'status':
            return {'available': self.modem_demo, 'reason': 'Synthetic provider for host preview' if self.modem_demo else 'No modem provider in this host preview',
                    'capabilities': {k: self.modem_demo for k in ('connect', 'mode', 'sms')},
                    'sim': {'status': 'ready' if self.modem_demo else 'unknown'}, 'connection': {'state': 'disconnected'}}
        if not self.modem_demo:
            raise unavailable('No modem provider in this host preview')
        if action == 'sms-list':
            return {'messages': [{'number': '+15550100001', 'text': 'This is a synthetic message.', 'timestamp': 'Preview'}]}
        return {'requested': True, 'demo': True}

    def capabilities(self):
        return {k: {'available': value, 'reason': 'Synthetic host-preview capability'} for k, value in
                {'network': True, 'wifi': True, 'modem': self.modem_demo, 'firewall': False}.items()}

    def hardware_status(self):
        return {'fresh': True, 'age_s': 0, 'freshness': 'current', 'battery': {'voltage_uv': 3900000, 'sample_age_s': None},
                'display': {'registered': False}, 'touch': {'events_seen': 0, 'event_devices': []}, 'modem': {'remoteproc_state': 'offline'}, 'ipa': {'interfaces': []}}

    def hardware_capabilities(self):
        return {'hardware': {'available': True, 'reason': 'Synthetic observation; no physical validation'}}

    def gnss_status(self):
        return {'fresh': False, 'fix_valid': False, 'reason': 'No GNSS provider in this host preview'}


def create_preview(state_dir, stock=False, modem_demo=False):
    cfg = config.load(None, {'mock': bool(stock), 'platform': 'stock' if stock else 'openwrt', 'selfheal': False,
        'state_dir': state_dir, 'web_root': str(ROOT / 'web'), 'fixtures_dir': str(ROOT / 'fixtures'), 'allowed_hosts': ['127.0.0.1', 'localhost']})
    if stock:
        app = App(cfg)
    else:
        native = DemoNative(cfg, modem_demo)
        adapters = Adapters(native, native, native.system, mock=True)
        adapters.native = native
        app = App(cfg, adapters=adapters)
    app.state.update_settings({'router_name': '5G Router Preview', 'expert_mode': True})
    route, _ = app.router.match('GET', '/api/meta')
    original = route.handler
    route.handler = lambda req: dict(original(req), mock=True)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', action='store_true')
    parser.add_argument('--modem-demo', action='store_true')
    parser.add_argument('--port', type=int, default=18088)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    with tempfile.TemporaryDirectory(prefix='m3100ui-preview-') as state:
        app = create_preview(state, args.stock, args.modem_demo)
        server = make_server(app, '127.0.0.1', args.port)
        url = 'http://127.0.0.1:%d' % args.port
        print('Demo data only. No router is connected.\n%s\nPassword: %s' % (url, 'admin' if args.stock else PREVIEW_PASSWORD), flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
