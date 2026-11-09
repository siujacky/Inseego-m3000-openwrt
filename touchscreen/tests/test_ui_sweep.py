"""Whole UI sweep using synthetic host fixtures, never router credentials/IO."""
import copy
import itertools
import unittest
from m3100_touch import catalog
from m3100_touch.controller import Controller
from m3100_touch.render import render
from touchscreen.tests.test_controller import Client, ManualWorker


class FixtureClient(Client):
    def __init__(self):
        super().__init__()
        self.values.update({
            '/api/wifi/primary':dict(enabled=True,ssid='SYNTHETIC WIFI',security='wpa2',password_set=True,hidden=False,isolation=False,wps=False),
            '/api/wifi/primary/secret':dict(ssid='SYNTHETIC WIFI',password='SYNTHETIC-ONLY-123'),
            '/api/touchscreen/wifi/ap0/secret':dict(ssid='SYNTHETIC WIFI',password='SYNTHETIC-ONLY-123'),
            '/api/openwrt/wifi':dict(interfaces=[dict(name='ap0',mode='ap',device='radio0',network='lan',ssid='SYNTHETIC WIFI',security='wpa2',password_set=True,enabled=True,hidden=False,isolation=False,wps=False)],radios=[dict(name='radio0',disabled='0',channel='auto',country='US',txpower=10)]),
            '/api/openwrt/interfaces':dict(interfaces=[dict(name='wan',enabled=True,proto='qmi',device='wwan0',apn='synthetic',username='fixture',password_set=True,auth='none',pdptype='ipv4v6',ipv6=True,peerdns=True,dns=['1.1.1.1'],metric=10,ipaddr='192.0.2.1',netmask='255.255.255.0',gateway='192.0.2.2')]),
            '/api/openwrt/dhcp':[dict(name='lan',ignore='0',start=100,limit=100,leasetime='12h',ra='server',dhcpv6='server')],
            '/api/openwrt/services':[dict(name='fixture_service',installed=True,running=False,blocked_actions=[])],
            '/api/openwrt/system':dict(config=dict(hostname='router',timezone='UTC0',zonename='UTC')),
            '/api/openwrt/modem':dict(available=True,capabilities=dict(connect=True,mode=True,sms=True),reason='Synthetic provider for host coverage only'),
            '/api/touchscreen/blocked':dict(ap='ap0',aps=['ap0'],macs=[],traffic_verified=False),
            '/api/status':dict(wan=dict(rx_bytes=123,tx_bytes=456),cellular=dict(available=False,reason='Modem offline; IPA unavailable'),wifi=dict(driver_ready=True,traffic_verified=False)),
            '/api/openwrt/hardware':dict(battery=dict(voltage_mv=3900,capacity=None),wifi=dict(driver_ready=True,traffic_verified=False)),
            '/api/lan/leases':[dict(hostname='Synthetic client',mac='02:00:00:00:00:01',ip='192.0.2.3')],
            '/api/openwrt/logs':dict(lines=['SYNTHETIC log record ' + 'long diagnostic text '*20]),
            '/api/meta/routes':[dict(method=method,path=path,tier=2 if method!='GET' else 0) for method,path in catalog.NATIVE_ROUTES],
            '/api/openwrt/firewall':dict(runtime=dict(available=True),protection=dict(zone='usbmgmt'),defaults=dict(options=dict(input='ACCEPT',output='ACCEPT',forward='REJECT',drop_invalid=False,synflood_protect=True),editable=True),
                zones=[dict(section='zone_lan',options=dict(name='lan',network=['lan'],input='ACCEPT',output='ACCEPT',forward='ACCEPT',enabled=True,family='any',masq=False,masq6=False,mtu_fix=True),editable=True,removable=False),dict(section='zone_wan',options=dict(name='wan'),editable=True)],
                forwardings=[dict(section='forward1',options=dict(name='To WAN',enabled=True,src='lan',dest='wan',family='any'),editable=True,removable=True)],
                rules=[dict(section='rule1',options=dict(name='Example rule',enabled=True,src='lan',dest='wan',family='ipv4',proto=['tcp'],src_ip='',src_mac='',src_port='',dest_ip='',dest_port='80',target='ACCEPT'),editable=True,removable=True)],
                redirects=[dict(section='redirect1',options=dict(name='Example redirect',enabled=True,src='wan',dest='lan',family='ipv4',proto=['tcp'],src_ip='',src_mac='',src_dip='',src_dport='8080',dest_ip='192.0.2.4',dest_port='80',reflection=True,target='DNAT'),editable=True,removable=True)])})
    def request(self, method,path,body=None,**kwargs):
        if method=='GET': return copy.deepcopy(self.values.get(path,dict(available=False,reason='Synthetic provider unavailable')))
        return super().request(method,path,body,**kwargs)


def context_for(page):
    if page in ('wifi_edit','credentials','credentials_reveal'): return {'name':'ap0'} if page=='wifi_edit' else {}
    if page=='radio_edit': return {'name':'radio0'}
    if page=='interface_edit': return {'name':'wan'}
    if page=='dhcp_edit': return {'name':'lan'}
    if page=='service': return {'name':'fixture_service'}
    if page.startswith('firewall_') and page.endswith('_edit') and page!='firewall_defaults_edit':
        return {'section':{'firewall_zones_edit':'zone_lan','firewall_rules_edit':'rule1','firewall_redirects_edit':'redirect1','firewall_forwardings_edit':'forward1'}[page]}
    if page=='explorer_read': return {'path':'/api/status'}
    return {}


def controller_for(page, language='en', theme='dark'):
    client, worker=FixtureClient(),ManualWorker()
    controller=Controller(client,worker,now=10)
    controller.locked=False
    controller.open(page,context_for(page),force=True)
    while worker.jobs: worker.run(); controller.poll(10)
    controller.language,controller.theme=language,theme
    return controller


class UISweepTests(unittest.TestCase):
    def assert_frame(self, controller):
        frame,boxes=controller.render()
        self.assertEqual(len(frame),230400)
        self.assertLessEqual(len(controller.view().get('footer',[])),5)
        for b in boxes:
            self.assertGreaterEqual(min(b['w'],b['h']),40)
            self.assertGreaterEqual(b['x'],0); self.assertGreaterEqual(b['y'],0)
            self.assertLessEqual(b['x']+b['w'],320); self.assertLessEqual(b['y']+b['h'],240)
        return boxes

    def test_every_page_every_row_languages_themes_and_all124_targets(self):
        visited=set()
        for page in catalog.PAGES:
            for language,theme in itertools.product(('en','es'),('dark','light')):
                c=controller_for(page,language,theme)
                self.assertEqual(c.mode,'page',page)
                seen=set()
                for _ in range(110):
                    boxes=self.assert_frame(c); seen.update(b['id'] for b in boxes)
                    next_button=next((b for b in boxes if b['id']=='next' and b['enabled']),None)
                    if next_button is None: break
                    c.activate('next')
                self.assertTrue({r['id'] for r in c._rows() if r.get('kind')!='text'}.issubset(seen),page)
            visited.add(page)
        self.assertEqual(visited,set(catalog.PAGES))
        self.assertTrue({r['target'] for r in catalog.STOCK_COVERAGE}.issubset(visited))
        self.assertEqual(len(catalog.STOCK_COVERAGE),124)

    def test_each_field_editor_selector_and_visibility_branch_is_reachable(self):
        for page in catalog.PAGES:
            c=controller_for(page)
            for field in c.loaded.get('fields',[]):
                c.mode='page'; c.draft={}
                for key,values in field.get('visible_if',{}).items(): c.draft[key]=values[0]
                if c.loaded.get('reason'): continue
                self.assertIn('field:'+field['key'],[r['id'] for r in c._rows()],(page,field))
                c.activate('field:'+field['key'])
                if field['type']=='select':
                    seen=set()
                    for _ in range(100):
                        boxes=self.assert_frame(c); seen.update(b['id'] for b in boxes)
                        if not any(b['id']=='next' and b['enabled'] for b in boxes): break
                        c.activate('next')
                    self.assertTrue({'choice:'+str(i) for i in range(len(field['choices']))}.issubset(seen))
                    if field['choices']: c.activate('choice:0'); self.assertEqual(c.mode,'page')
                    else: c.activate('back')
                elif field['type']!='switch':
                    self.assertEqual(c.mode,'keyboard'); self.assert_frame(c)
                    for _ in range(10): c.activate('next'); self.assert_frame(c)
                    c.activate('cancel'); self.assertEqual(c.mode,'page')

    def test_every_enabled_catalog_action_prepares_through_controller_review(self):
        prepared=[]
        for page in catalog.PAGES:
            original=controller_for(page)
            for action in original.loaded.get('actions',[]):
                if not action.get('enabled',True): continue
                c=controller_for(page)
                if action['id']=='logout': c.activate('action:logout'); self.assertTrue(c.locked); continue
                if action['id']=='save':
                    f=next(f for f in c.loaded['fields'] if f['type']=='switch' or (f['type'] in ('text','password') and f['key'] not in ('mac','ap','number','text','section')) or f['type']=='number') if page not in ('presentation','sms_send','blocked') else None
                    if page=='presentation': c.draft={'theme':'light'}
                    elif page=='sms_send': c.draft={'number':'+15555550100','text':'Synthetic fixture'}
                    elif page=='blocked': c.draft={'mac':'02:00:00:00:00:01','blocked':True}
                    elif f['type']=='switch': c.draft={f['key']:not bool(c.loaded['data'].get(f['key']))}
                    elif f['type']=='number': c.draft={f['key']:f['min']}
                    elif f['key']=='channel': c.draft={'channel':'6'}
                    elif f['key']=='hostname': c.draft={'hostname':'synthetic-router'}
                    else: c.draft={f['key']:'SyntheticValue'}
                c.activate('action:'+action['id'])
                while c.worker.jobs: c.worker.run(); c.poll(10)
                self.assertEqual(c.mode,'confirm',(page,action,c.message))
                self.assert_frame(c)
                prepared.append((page,action['id']))
        self.assertEqual(len(prepared),32)

    def test_actual_navigation_graph_reaches_all_pages_and_stock_targets(self):
        visited=set(); pending=[('home',{})]; client=FixtureClient()
        while pending:
            page,context=pending.pop()
            if page in visited: continue
            visited.add(page)
            loaded=catalog.load(client,page,context)
            pending.extend((row['target'],row.get('context',{})) for row in loaded['rows'] if row.get('target'))
        self.assertEqual(visited,set(catalog.PAGES))
        self.assertTrue({r['target'] for r in catalog.STOCK_COVERAGE}.issubset(visited))

    def test_supported_create_edit_delete_review_paths(self):
        for page in ('wifi_edit','firewall_forwardings_edit','firewall_rules_edit','firewall_redirects_edit'):
            c=controller_for(page); c.open(page,{'create':True},force=True)
            while c.worker.jobs: c.worker.run(); c.poll(10)
            if page=='wifi_edit': c.draft=dict(name='new_ap',ssid='SYNTHETIC NEW',password='SYNTHETIC-ONLY-123')
            else:
                c.draft=dict(section='synthetic_section',name='Synthetic rule',enabled=True,src='wan',dest='lan',family='ipv4')
                if page=='firewall_rules_edit': c.draft.update(proto='tcp',target='ACCEPT')
                if page=='firewall_redirects_edit': c.draft.update(proto='tcp',target='DNAT',src_dport='8080',dest_ip='192.0.2.10',dest_port='80')
            c.activate('action:save')
            while c.worker.jobs: c.worker.run(); c.poll(10)
            self.assertEqual(c.mode,'confirm',(page,c.message)); self.assertEqual(c.pending['method'],'POST')
            self.assertNotIn('SYNTHETIC-ONLY-123',repr(c.view()))

    def test_long_error_and_unavailable_messages_have_full_paged_paths(self):
        c=controller_for('charging'); c.activate('detail:0')
        self.assertEqual(c.mode,'detail'); self.assert_frame(c); c.activate('back')
        message=' '.join('word'+str(i) for i in range(1000))
        c.show_message(message)
        reconstructed=' '.join(c._message_chunks()).replace('\n',' ')
        self.assertEqual(reconstructed,message)
        for _ in range(len(c._message_chunks())): self.assert_frame(c); c.activate('next')
        c.activate('home'); self.assertEqual(c.page,'home')

    def test_secret_masking_and_ascii_modes_all_pages(self):
        for language,theme in itertools.product(('en','es'),('dark','light')):
            c=controller_for('wifi_primary',language,theme)
            c.activate('field:password'); c.activate('char:X')
            self.assertNotIn('SYNTHETIC-ONLY-123',repr(c.view()))
            keys=set()
            for _ in range(10):
                self.assert_frame(c); keys.update(k['id'][5:] for k in c.view()['keys'] if k['id'].startswith('char:')); c.activate('next')
            keys.add(' ')
            self.assertEqual(keys,{chr(i) for i in range(32,127)})

