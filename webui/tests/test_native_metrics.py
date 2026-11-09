"""Native missing numeric telemetry must survive the actual frontend consumers."""
import base64
import json
import os
import re
import shutil
import subprocess
import unittest

from tests import test_openwrt
from tests.helpers import MockClient, ROOT
from m3100ui.errors import ApiError


def module_url(source):
    return 'data:text/javascript;base64,' + base64.b64encode(source.encode()).decode()


def read_source(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


class NativeMetricBackendTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_openwrt.NativeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.app()
        self.app.system.root = os.path.join(self.fixture.tmp.name, 'sources')

    def source(self, path, text):
        target = self.app.system.p(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w', encoding='utf-8') as f:
            f.write(text)

    def test_missing_sources_are_null_and_errors_stay_explicit(self):
        def unavailable(argv):
            raise ApiError(503, 'unavailable', 'Native executable unavailable')
        self.app.adapters.native.command = unavailable
        c = MockClient(self.app)
        data = c.get('/api/status')
        for field in ('duration_s', 'rx_bytes', 'tx_bytes'):
            self.assertIsNone(data['wan'][field], field)
        self.assertIsNone(data['uptime_s'])
        self.assertIsNone(data['memory']['total'])
        self.assertIsNone(data['memory']['available'])
        self.assertIsNone(data['clients']['total'])
        self.assertIsNone(data['load'])
        self.assertIn('network', data['errors'])
        c.get('/api/lan/leases', expect=503)

    def test_readable_zero_sources_remain_measured_zero(self):
        self.source('/proc/uptime', '0.00 0.00\n')
        self.source('/proc/meminfo', 'MemTotal: 2048 kB\nMemAvailable: 0 kB\nMemFree: 0 kB\n')
        self.source('/proc/loadavg', '0.00 0.00 0.00 1/32 1\n')
        self.source('/tmp/dhcp.leases', '')
        self.source('/proc/net/dev', 'header\nheader\nwwan0: ' + ' '.join(['0'] * 16) + '\n')
        original = self.app.adapters.native.ubus
        def ubus(obj, method, payload=None):
            if obj == 'network.interface':
                return {'interface': [{'interface': 'wan', 'up': False, 'l3_device': 'wwan0', 'uptime': 0}]}
            return original(obj, method, payload)
        self.app.adapters.native.ubus = ubus
        c = MockClient(self.app)
        data = c.get('/api/status')
        self.assertEqual(data['uptime_s'], 0)
        self.assertEqual(data['wan']['duration_s'], 0)
        self.assertEqual(data['wan']['rx_bytes'], 0)
        self.assertEqual(data['wan']['tx_bytes'], 0)
        self.assertEqual(data['memory'], {'total': 2097152, 'available': 0})
        self.assertEqual(data['clients']['total'], 0)
        self.assertEqual(data['load'], [0, 0, 0])
        self.assertEqual(c.get('/api/lan/leases'), [])

    def test_malformed_sources_are_unknown_not_zero(self):
        self.source('/proc/uptime', 'nan invalid\n')
        self.source('/proc/meminfo', 'MemTotal: invalid\nMemAvailable: invalid\n')
        self.source('/proc/loadavg', 'bad values\n')
        self.source('/tmp/dhcp.leases', 'not a valid lease\n')
        data = MockClient(self.app).get('/api/status')
        self.assertIsNone(data['uptime_s'])
        self.assertIsNone(data['memory']['total'])
        self.assertIsNone(data['memory']['available'])
        self.assertIsNone(data['load'])
        self.assertIsNone(data['clients']['total'])

    def test_missing_memory_fields_do_not_erase_measured_free_zero(self):
        self.source('/proc/meminfo', 'MemFree: 0 kB\n')
        data = MockClient(self.app).get('/api/status')
        self.assertIsNone(data['memory']['total'])
        self.assertEqual(data['memory']['available'], 0)


@unittest.skipUnless(shutil.which('node'), 'node unavailable')
class NativeMetricConsumerTests(unittest.TestCase):
    def run_node(self, script):
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace')[:2000])
        return json.loads(result.stdout)

    def fmt_url(self):
        return module_url(read_source('web/app/ui/format.js'))

    def test_actual_formatters_distinguish_unknown_and_valid_zero(self):
        data = self.run_node('''
const f=await import(FMT);
console.log(JSON.stringify({unknown:[f.bytes(null),f.rate(undefined),f.duration(null),f.bytes(''),f.rate(NaN)],
zero:[f.bytes(0),f.rate(0),f.duration(0)],stock:[f.bytes(1024),f.rate(1000),f.duration(60),f.bytes('0')]}));
'''.replace('FMT', json.dumps(self.fmt_url())))
        self.assertEqual(data['unknown'], ['Unavailable'] * 5)
        self.assertEqual(data['zero'], ['0 B', '0 bit/s', '0s'])
        self.assertEqual(data['stock'], ['1.0 KB', '1 kbit/s', '1m 0s', '0 B'])

    def test_actual_dashboard_missing_and_measured_zero_numeric_rows(self):
        source = read_source('web/app/pages/dashboard.js')
        source = re.sub(r'^import \* as fmt .*?;$', 'const fmt=globalThis.realFmt;', source, flags=re.M)
        source = re.sub(r'^import \{([^}]+)\} .*?;$', r'const {\1}=globalThis.stub;', source, flags=re.M)
        script = '''
function node(tag,...children){return {tag,children,append(...x){this.children.push(...x)},appendChild(x){this.children.push(x);return x}};}
globalThis.realFmt=await import(FMT);
globalThis.stub={h:node,clear:n=>{n.children=[];return n},card:(title,body)=>node('card',title,body),kv:x=>x,
stat:(...x)=>x,badge:(...x)=>x,signalBars:()=>null,meter:()=>null,alert:(...x)=>x,button:()=>null,toast:()=>{},lineChart:()=>null,post:()=>{},put:()=>{}};
const dashboard=(await import(DASHBOARD)).default;
function text(v){if(v==null||typeof v==='boolean')return '';if(typeof v!=='object')return String(v);return (Array.isArray(v)?v:v.children||[]).map(text).join('');}
function rows(v,out={}){if(!v||typeof v!=='object')return out;if(Array.isArray(v)&&v.length===2&&['Session','Data used','Uptime','Memory free','Clients','Load'].includes(v[0]))out[v[0]]=text(v[1]);for(const x of Array.isArray(v)?v:v.children||[])rows(x,out);return out;}
const results=[];
for(const [platform,value] of [['openwrt',null],['openwrt',0],['stock',0]]){
const root=node('root');const st={platform,connection:{state:'unavailable',data_enabled:null},wan:{status:'Unavailable',duration_s:value,rx_bytes:value,tx_bytes:value},
uptime_s:value,memory:{total:value===0?2048:null,available:value},clients:{total:value,wifi:value,usb:value,ethernet:value},wifi:{enabled:null},load:value===0?[0,0,0]:null};
await dashboard.render(root,{store:{subscribe(fn){fn({status:st,history:{rsrp:[],nr_rsrp:[],sinr:[],rx_bps:[],tx_bps:[]}});return ()=>{}}}});results.push(rows(root));}
console.log(JSON.stringify(results));
'''.replace('FMT', json.dumps(self.fmt_url())).replace('DASHBOARD', json.dumps(module_url(source)))
        unknown, zero, stock = self.run_node(script)
        for key in ('Session', 'Uptime', 'Memory free', 'Clients', 'Load'):
            self.assertEqual(unknown[key], 'Unavailable', key)
        self.assertEqual(unknown['Data used'], '↓ Unavailable  ↑ Unavailable')
        self.assertEqual(zero['Session'], '0s')
        self.assertEqual(zero['Uptime'], '0s')
        self.assertEqual(zero['Data used'], '↓ 0 B  ↑ 0 B')
        self.assertEqual(zero['Memory free'], '0 B of 2.0 KB')
        self.assertTrue(zero['Clients'].startswith('0 DHCP leases'))
        self.assertEqual(zero['Load'], '0 0 0')
        self.assertEqual(stock['Data used'], zero['Data used'])
        self.assertEqual(stock['Clients'], '0 (Wi-Fi 0, USB 0, Ethernet 0)')

    def test_actual_header_unknown_numeric_values_are_unavailable(self):
        source = read_source('web/app/main.js')
        start, end = source.index('function statusBar()'), source.index('\nasync function start()')
        module = module_url('const {h,clear,append,icon,signalBars,store,fmt}=globalThis.stub;\n' + source[start:end] + '\nexport {statusBar};')
        data = self.run_node('''
function node(tag,...children){return {tag,children,append(...x){this.children.push(...x)},appendChild(x){this.children.push(x);return x}};}
const fmt=await import(FMT);let current;
globalThis.stub={h:node,clear:n=>{n.children=[];return n},append:(n,x)=>n.append(...x),icon:()=>null,signalBars:()=>null,fmt,store:{subscribe(fn){fn(current);return()=>{}}}};
const {statusBar}=await import(HEADER);const out=[];
for(const value of [null,0]){current={status:{platform:'openwrt',connection:{state:'unavailable'},clients:{total:value}},lastRx:value,lastTx:value};out.push(statusBar().node);}
console.log(JSON.stringify(out));
'''.replace('FMT', json.dumps(self.fmt_url())).replace('HEADER', json.dumps(module)))
        unknown, zero = [json.dumps(x) for x in data]
        self.assertNotIn('0 bit/s', unknown)
        self.assertNotIn('"0"', unknown)
        self.assertIn('Unavailable', unknown)
        self.assertIn('0 bit/s', zero)
        self.assertIn('"0"', zero)

    def test_actual_store_requires_valid_counter_pair_and_recovery_baseline(self):
        source = re.sub(r'^import \{([^}]+)\} .*?;$', r'const {\1}=globalThis.deps;', read_source('web/app/store.js'), flags=re.M)
        data = self.run_node('''
const fmt=await import(FMT);
const frames=[[0,0],[0,0],[null,null],[0,0],[0,0],[5,6],[2,1],[2,1],'error',[2,1],[2,1]];
let i=0,clock=0;Date.now=()=>{clock+=1000;return clock};
globalThis.document={hidden:false,addEventListener(){}};globalThis.setTimeout=()=>1;globalThis.clearTimeout=()=>{};
globalThis.deps={...fmt,get:async()=>{const row=frames[i++];if(row==='error')throw Error('read failed');return {platform:'openwrt',wan:{rx_bytes:row[0],tx_bytes:row[1]}}}};
const store=await import(STORE);const rates=[];
store.startPolling(4);await new Promise(setImmediate);rates.push([store.state.lastRx??null,store.state.lastTx??null]);
for(let n=1;n<frames.length;n++){await store.refreshNow();rates.push([store.state.lastRx??null,store.state.lastTx??null]);}
console.log(JSON.stringify({rates,history:store.state.history.rx_bps}));
'''.replace('FMT', json.dumps(self.fmt_url())).replace('STORE', json.dumps(module_url(source))))
        self.assertEqual(data['rates'], [[None, None], [0, 0], [None, None], [None, None], [0, 0], [40, 48],
                                          [None, None], [0, 0], [None, None], [None, None], [0, 0]])
        self.assertEqual(data['history'], [None, 0, None, None, 0, 40, None, 0, None, 0])
