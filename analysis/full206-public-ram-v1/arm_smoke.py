"""Run image Python/backend/touchscreen together, with all commands simulated."""
import importlib
import json
from pathlib import Path
import platform
import sys
import tempfile
import threading
from urllib.request import urlopen
from unittest.mock import patch

from m3100ui import config
from m3100ui.app import App
from m3100ui.adapters.openwrt import NativePlatform
from m3100ui.httpd import make_server
from m3100_touch.api import ApiClient, ApiError


def main():
    calls = []
    def command(argv):
        calls.append(argv[:4])
        if argv[:3] == ['ubus', 'call', 'uci']:
            return {'values': {}}
        if argv[:4] == ['ubus', 'call', 'system', 'board']:
            return {'model': 'Image ARM smoke, synthetic boundary'}
        return {}
    with tempfile.TemporaryDirectory(prefix='arm-ui-smoke-') as state:
        cfg = config.load('/etc/full206-webui.json', {'state_dir': state, 'password_file': '/etc/full206/admin-password'})
        with patch.object(NativePlatform, 'command', side_effect=command):
            app = App(cfg)
        app.adapters.native.command = command
        server = make_server(app, '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:%d' % server.server_address[1]
        client = ApiClient(base_url=base)
        try:
            try:
                client.login('wrong-synthetic-input')
                raise AssertionError('Wrong password accepted')
            except ApiError as error:
                assert error.code == 'bad_password'
            password = Path('/etc/full206/admin-password').read_text().rstrip('\r\n')
            client.login(password)
            del password
            pages = client.request('GET', '/api/ui/pages')
            assert len(pages) == 15 and '/app/pages/openwrt/cellular.js' in pages
            assert client.request('GET', '/api/meta/full')['mock'] is False
            assert client.request('GET', '/api/touchscreen/preferences')['language'] == 'en'
            capabilities = client.request('GET', '/api/touchscreen/capabilities')
            assert capabilities['functions']['update']['available'] is False
            routes = client.request('GET', '/api/meta/routes')
            assert len(routes) == 61
            for name in ('catalog', 'controller', 'input', 'keyboard', 'lcd', 'render'):
                importlib.import_module('m3100_touch.' + name)
            for path in ('/', '/app/main.js', '/app/preferences.js', '/app/pages/openwrt/cellular.js', '/app/styles.css', '/THIRD-PARTY-NOTICES.txt'):
                with urlopen(base + path, timeout=10) as response:
                    assert response.status == 200 and response.read()
            client.logout()
            print(json.dumps({'python': sys.version.split()[0], 'architecture': platform.machine(),
                'native_pages': len(pages), 'native_and_touchscreen_routes': len(routes),
                'public_seed_login': True, 'wrong_password_rejected': True, 'real_mode_not_mock': True,
                'touchscreen_preferences_and_capabilities': True, 'touchscreen_modules_import': True,
                'offline_web_assets_http_200': True, 'logout': True,
                'scope': 'Actual image ARM Python and merged source under qemu, command boundary simulated; no hardware boot.'}, indent=2))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
