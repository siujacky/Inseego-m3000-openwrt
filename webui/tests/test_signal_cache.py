"""R6 signal timing/zero regressions against actual JS store/dashboard consumers."""
import base64
import json
import os
import re
import shutil
import subprocess
import unittest

from tests.helpers import ROOT


def source(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


def module_url(text):
    return 'data:text/javascript;base64,' + base64.b64encode(text.encode()).decode()


@unittest.skipUnless(shutil.which('node'), 'node unavailable')
class SignalCacheConsumerTests(unittest.TestCase):
    def run_js(self, script):
        result = subprocess.run(['node', '--input-type=module'], input=script.encode(), capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace')[:2000])
        return json.loads(result.stdout)

    def store(self, frames):
        actual = re.sub(r'^import \{([^}]+)\} .*?;$', r'const {\1}=globalThis.deps;', source('web/app/store.js'), flags=re.M)
        script = '''
const fmt=await import(FMT);const frames=FRAMES;let i=0,clock=0;
Date.now=()=>{clock+=1000;return clock};globalThis.document={hidden:false,addEventListener(){}};
globalThis.setTimeout=()=>1;globalThis.clearTimeout=()=>{};
globalThis.deps={...fmt,get:async()=>frames[i++]};
const store=await import(STORE);
// Start the public polling lifecycle; later frames use manual refresh.
store.startPolling(4);await new Promise(setImmediate);
for(let n=1;n<frames.length;n++)await store.refreshNow();
console.log(JSON.stringify({history:store.state.history,status:store.state.status,lastRx:store.state.lastRx}));
'''.replace('FMT', json.dumps(module_url(source('web/app/ui/format.js')))).replace('FRAMES', json.dumps(frames)).replace('STORE', json.dumps(module_url(actual)))
        return self.run_js(script)

    def frame(self, lte_timing=None, nr_timing=None, **overrides):
        frame = {'platform': 'openwrt', 'connection': {'state': 'disconnected', 'data_enabled': None},
                 'signal': {'rsrp': -83, 'rsrq': -11, 'sinr': 0},
                 'nr': {'service_available': True, 'rsrp': -98, 'snr': 0},
                 'wan': {'rx_bytes': 0, 'tx_bytes': 0}, 'wifi': {'enabled': None}, 'clients': {'total': None}}
        for key, timing in (('signal', lte_timing), ('nr', nr_timing)):
            if timing is not None:
                frame[key].update({'source': 'modemmanager-cache', 'sample_time_known': timing, 'sample_age_s': None})
        frame.update(overrides)
        return frame

    def test_repeated_cached_reads_are_history_gaps_but_keep_values_visible(self):
        frame = self.frame(False, False)
        result = self.store([frame] * 185)
        for key in ('rsrp', 'rsrq', 'sinr', 'nr_rsrp', 'nr_snr'):
            self.assertEqual(result['history'][key], [None] * 180, key)
        self.assertEqual(len(result['history']['t']), 180)
        self.assertEqual(result['status']['signal']['rsrp'], -83)
        self.assertEqual(result['status']['nr']['snr'], 0)
        self.assertEqual(result['lastRx'], 0, 'Signal cache metadata must not suppress valid data counters')

    def test_lte_nr_timing_is_independent_and_known_zero_ratios_survive(self):
        result = self.store([self.frame(False, None), self.frame(None, False),
                             self.frame(True, True), self.frame(None, None, platform='stock')])
        self.assertEqual(result['history']['rsrp'], [None, -83, -83, -83])
        self.assertEqual(result['history']['rsrq'], [None, -11, -11, -11])
        self.assertEqual(result['history']['sinr'], [None, 0, 0, 0])
        self.assertEqual(result['history']['nr_rsrp'], [-98, None, -98, -98])
        self.assertEqual(result['history']['nr_snr'], [0, None, 0, 0])

    def test_stock_without_metadata_retains_signal_samples_and_zero_sentinels(self):
        first = self.frame(platform='stock', signal={'rsrp': 0, 'rsrq': 0, 'sinr': '0'},
                           nr={'service_available': True, 'rsrp': 0, 'snr': 0})
        second = self.frame(platform='stock', signal={'rsrp': -91, 'rsrq': -14, 'sinr': -3},
                            nr={'service_available': True, 'rsrp': -103, 'snr': -2})
        result = self.store([first, second])
        self.assertEqual(result['history']['rsrp'], [None, -91])
        self.assertEqual(result['history']['rsrq'], [None, -14])
        self.assertEqual(result['history']['sinr'], [0, -3])
        self.assertEqual(result['history']['nr_rsrp'], [None, -103])
        self.assertEqual(result['history']['nr_snr'], [0, -2])

    def test_invalid_ratio_values_do_not_become_measured_zero(self):
        result = self.store([self.frame(signal={'sinr': value}, nr={'snr': value})
                             for value in (None, '', False, 'not-a-number')])
        self.assertEqual(result['history']['sinr'], [None] * 4)
        self.assertEqual(result['history']['nr_snr'], [None] * 4)

    def dashboard(self, frames):
        actual = re.sub(r'^import \* as fmt .*?;$', 'const fmt=globalThis.realFmt;', source('web/app/pages/dashboard.js'), flags=re.M)
        actual = re.sub(r'^import \{([^}]+)\} .*?;$', r'const {\1}=globalThis.stub;', actual, flags=re.M)
        script = '''
function node(tag,...children){return{tag,children,append(...x){this.children.push(...x)},appendChild(x){this.children.push(x);return x}};}
globalThis.realFmt=await import(FMT);let charts=[];
globalThis.stub={h:node,clear:n=>{n.children=[];return n},card:(title,body)=>node('card',title,body),kv:x=>x,
stat:(...x)=>node('stat',...x),badge:(...x)=>node('badge',...x),signalBars:()=>null,meter:()=>null,alert:(...x)=>node('alert',...x),
button:()=>null,toast:()=>{},lineChart:(series)=>{charts.push(series);return null},post:()=>{},put:()=>{}};
const dashboard=(await import(DASHBOARD)).default;const output=[];
for(const status of FRAMES){charts=[];const root=node('root');
await dashboard.render(root,{store:{subscribe(fn){fn({status,history:{rsrp:[null,null],nr_rsrp:[null,null],sinr:[null,null],rx_bps:[],tx_bps:[]}});return()=>{}}}});
output.push({root,charts});}
console.log(JSON.stringify(output));
'''.replace('FMT', json.dumps(module_url(source('web/app/ui/format.js')))).replace('DASHBOARD', json.dumps(module_url(actual))).replace('FRAMES', json.dumps(frames))
        return self.run_js(script)

    def test_dashboard_labels_cached_unknown_age_without_hiding_lte_nr_values(self):
        result = self.dashboard([self.frame(False, False)])[0]
        rendered = json.dumps(result['root'])
        self.assertIn('LTE / NR', rendered)
        self.assertIn('cached', rendered.lower())
        self.assertIn('measurement age is unknown', rendered.lower())
        self.assertIn('-83 dBm', rendered)
        self.assertIn('-98 dBm', rendered)
        self.assertEqual(rendered.count('0 dB'), 2, 'Measured LTE SINR and NR SNR zero must remain visible')
        self.assertTrue(all(series['values'] == [None, None] for series in result['charts'][0]))

    def test_dashboard_label_is_per_radio_and_does_not_change_stock_without_metadata(self):
        frames = [self.frame(False, True), self.frame(True, False), self.frame(platform='stock'),
                  self.frame(nr={'sample_time_known': False, 'service_available': False})]
        rendered = [json.dumps(item['root']) for item in self.dashboard(frames)]
        self.assertIn('LTE signal values are cached', rendered[0])
        self.assertNotIn('NR signal values are cached', rendered[0])
        self.assertIn('NR signal values are cached', rendered[1])
        self.assertNotIn('LTE signal values are cached', rendered[1])
        self.assertNotIn('cached', rendered[2].lower())
        self.assertNotIn('cached', rendered[3].lower())

    def test_dashboard_cached_nr_is_visible_without_claiming_current_nr_service(self):
        frames = []
        for available in (None, False):
            value = self.frame(nr_timing=False)
            value['nr']['service_available'] = available
            frames.append(value)
        for item in self.dashboard(frames):
            rendered = json.dumps(item['root'])
            self.assertIn('NR signal values are cached', rendered)
            self.assertIn('measurement age is unknown', rendered)
            self.assertIn('-98 dBm', rendered)
            self.assertIn('0 dB', rendered)
            self.assertIn('Disconnected', rendered)
            self.assertNotIn('Connected', rendered)
        old = self.frame(platform='stock')
        old['nr']['service_available'] = None
        self.assertNotIn('NR SNR', json.dumps(self.dashboard([old])[0]['root']))
