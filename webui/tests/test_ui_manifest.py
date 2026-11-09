"""Browser manifest contracts: native-only controls never leak onto stock."""
import os
import types
import unittest
from tests.helpers import ROOT
from m3100ui.router import Router
from m3100ui.services import ui_pages


class ManifestTests(unittest.TestCase):
    def manifest(self, platform):
        app = types.SimpleNamespace(cfg={'platform': platform, 'web_root': os.path.join(ROOT, 'web')}, router=Router())
        ui_pages.register(app)
        route, _ = app.router.match('GET', '/api/ui/pages')
        return route.handler(None)

    def test_native_sections_and_existing_bookmarks(self):
        pages = self.manifest('openwrt')
        for name in ('cellular', 'sms', 'wireless', 'network', 'dhcp', 'system', 'services', 'logs', 'hardware'):
            self.assertIn('/app/pages/openwrt/' + name + '.js', pages)
        self.assertIn('/app/pages/system/openwrt.js', pages)
        self.assertIn('/app/pages/firewall/openwrt.js', pages)
        self.assertIn('/app/pages/wifi/primary.js', pages)
        self.assertFalse(any('/_' in p for p in pages))

    def test_native_pages_do_not_leak_to_stock(self):
        pages = self.manifest('stock')
        self.assertFalse(any('/openwrt/' in p or p.endswith('/openwrt.js') for p in pages))
        self.assertIn('/app/pages/dashboard.js', pages)
        self.assertIn('/app/pages/cellular/apn.js', pages)
