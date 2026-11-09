"""Typed touchscreen pages and explicit API transactions; no router IO outside client."""
import copy
import json
import pathlib
import re
from urllib.parse import quote
from .api import ApiError

LABELS = {'ipaddr': 'IPv4 address', 'netmask': 'Subnet mask', 'gateway': 'Gateway', 'dns': 'DNS servers', 'apn': 'APN', 'username': 'APN user name', 'auth': 'APN authentication', 'pdptype': 'Mobile IP type', 'ipv6': 'IPv6 enabled', 'peerdns': 'Use peer DNS', 'txpower': 'Transmit power (dBm)', 'family': 'IP family', 'masq': 'IPv4 masquerading', 'masq6': 'IPv6 masquerading', 'mtu_fix': 'Clamp TCP MSS', 'synflood_protect': 'SYN flood protection', 'drop_invalid': 'Drop invalid packets', 'reflection': 'NAT reflection', 'session_idle_s': 'Session idle timeout (seconds)', 'status_poll_s': 'Status refresh (seconds)', 'display_timeout_s': 'Screen inactivity (seconds)'}


def field(key, label=None, type='text', **kwargs):
    return dict(key=key, label=label or LABELS.get(key, key.replace('_', ' ').capitalize()), type=type, **kwargs)


def select(key, choices, **kw): return field(key, type='select', choices=list(choices), **kw)
def switch(key, **kw): return field(key, type='switch', **kw)
def number(key, low, high, **kw): return field(key, type='number', min=low, max=high, **kw)
def secret(key='password', **kw): return field(key, type='password', secret=True, keep_blank=True, **kw)


SECURITY = ['open', 'wpa2', 'wpa3', 'wpa2_wpa3', 'owe']
WIFI = [switch('enabled'), select('mode', ['ap', 'sta']), field('device', 'Radio', required=True), field('network', 'Network interface', required=True), field('ssid', 'Network name', required=True, max_length=32), select('security', SECURITY), secret(max_length=63, min_length=8, visible_if={'security': ['wpa2', 'wpa3', 'wpa2_wpa3']}), switch('hidden', label='Hidden SSID'), switch('isolation', label='Client isolation', visible_if={'mode': ['ap']}), switch('wps', label='WPS eligible', visible_if={'mode': ['ap'], 'security': ['wpa2'], 'hidden': [False]})]
INTERFACE = [switch('enabled'), select('proto', ['static', 'dhcp', 'dhcpv6', 'qmi', 'modemmanager', 'none'], label='Protocol'), field('device', max_length=128), *[field(k, visible_if={'proto': ['static']}, required=k != 'gateway') for k in ['ipaddr', 'netmask', 'gateway']], field('dns', 'DNS servers', type='list', max_items=4), number('metric', 0, 65535), *[field(k, max_length=100 if k == 'apn' else 128, visible_if={'proto': ['qmi', 'modemmanager']}) for k in ['apn', 'username']], secret(max_length=128, visible_if={'proto': ['qmi', 'modemmanager']}), select('auth', ['none', 'pap', 'chap', 'both'], visible_if={'proto': ['qmi']}), select('pdptype', ['ip', 'ipv6', 'ipv4v6'], visible_if={'proto': ['qmi']}), switch('ipv6'), switch('peerdns')]
DHCP = [switch('ignore', label='Disable DHCP server'), number('start', 1, 65535, label='First host offset'), number('limit', 1, 65535, label='Number of leases'), field('leasetime', 'Lease duration', max_length=12), select('ra', ['disabled', 'server', 'relay', 'hybrid'], label='IPv6 advertisements'), select('dhcpv6', ['disabled', 'server', 'relay', 'hybrid'])]
POLICIES = ['ACCEPT', 'DROP', 'REJECT']


def firewall_fields(kind, create=False):
    fields = [field('section', 'Configuration identifier', required=True, max_length=64, pattern=r'^[A-Za-z0-9_]+$')] if create else []
    if kind in ('defaults', 'zones'):
        if kind == 'zones': fields += [field('network', 'Networks', type='list', required=True)]
        fields += [select(k, POLICIES, label=k.capitalize() + ' policy') for k in ('input', 'output', 'forward')]
        return fields + ([switch('drop_invalid'), switch('synflood_protect')] if kind == 'defaults' else [switch('enabled'), select('family', ['any', 'ipv4', 'ipv6']), switch('masq'), switch('masq6'), switch('mtu_fix')])
    fields += [field('name', 'Label', max_length=64), switch('enabled'), select('src', [], label='Source zone', required=True), select('dest', [], label='Destination zone (empty = router)' if kind == 'rules' else 'Destination zone', required=kind != 'rules'), select('family', ['ipv4', 'ipv6'] if kind == 'redirects' else ['any', 'ipv4', 'ipv6'])]
    if kind == 'forwardings': return fields
    fields += [select('proto', ['tcp', 'udp', 'tcp udp'] + (['icmp', 'ipv6-icmp', 'all'] if kind == 'rules' else []), label='Protocol', type_hint='list'), field('src_ip', 'Source IP or CIDR'), field('src_mac', 'Source MAC')]
    ports = {'proto': ['tcp', 'udp', 'tcp udp']}
    if kind == 'rules':
        fields += [field('src_port', 'Source port/range', visible_if=ports), field('dest_ip', 'Destination IP or CIDR'), field('dest_port', 'Destination port/range', visible_if=ports), select('target', POLICIES, label='Action')]
    else:
        fields += [field('src_dip', 'External destination IP'), field('src_dport', 'External port/range', required=True, visible_if=ports), field('dest_ip', 'Internal destination IP', required=True), field('dest_port', 'Internal port/range', required=True, visible_if=ports), switch('reflection'), select('target', ['DNAT'], label='Translation action')]
    return fields


PAGES = {}
def page(id, title, **kw): PAGES[id] = dict(title=title, **kw)
def menu(id, title, links): page(id, title, links=links)
menu('home', '5G Router', [('wifi_primary', 'Wi-Fi'), ('clients', 'Devices'), ('credentials', 'Wi-Fi credentials'), ('menu', 'Menu'), ('help', 'Help'), ('status', 'Status')])
menu('menu', 'Menu', [('status_menu', 'Status'), ('wifi_menu', 'Wi-Fi'), ('network', 'Network'), ('firewall', 'Firewall'), ('services_menu', 'Services'), ('system', 'System'), ('stock_settings', 'Stock settings'), ('help', 'Help')])
menu('status_menu', 'Status', [('status', 'Router status'), ('clients', 'Connected devices'), ('hardware', 'Hardware observations'), ('capabilities', 'Capabilities'), ('modem', 'Cellular / SIM'), ('gnss', 'GPS / GNSS'), ('data_usage', 'Data usage')])
menu('wifi_menu', 'Wi-Fi', [('wifi_primary', 'Primary network'), ('wifi', 'AP / client interfaces'), ('radios', 'Radio controls'), ('blocked_aps', 'Blocked devices'), ('wps', 'WPS')])
menu('network', 'Network', [('interfaces', 'Network interfaces'), ('dhcp', 'DHCP and IPv6'), ('clients', 'DHCP leases'), ('modem', 'Cellular controls'), ('sim', 'SIM / PIN'), ('roaming', 'Roaming'), ('tether', 'USB / Ethernet')])
menu('firewall', 'Firewall', [('firewall_defaults_edit', 'Default policies'), ('firewall_zones', 'Zones'), ('firewall_forwardings', 'Zone forwarding'), ('firewall_rules', 'Traffic rules'), ('firewall_redirects', 'Port redirects')])
menu('services_menu', 'Services', [('services', 'Installed services'), ('modem', 'Cellular connection'), ('sms', 'Messages'), ('sms_send', 'Send message'), ('gnss', 'GPS / GNSS'), ('charging', 'Universal charging'), ('activation', 'Carrier activation'), ('file_sharing', 'File sharing (stock disabled)'), ('speed_test', 'Speed test (stock disabled)')])
menu('system', 'System', [('system_edit', 'Host name and time zone'), ('system_info', 'System information'), ('settings', 'WebUI preferences'), ('presentation', 'Touchscreen preferences'), ('logs', 'System logs'), ('audit', 'Audit log'), ('explorer', 'API diagnostics'), ('about', 'About'), ('session', 'Session / lock'), ('reset', 'Factory reset'), ('update', 'Software update'), ('power', 'Power options')])
menu('stock_settings', 'Settings', [('wifi_primary', 'Wi-Fi'), ('presentation', 'Language / screen timeout'), ('audio', 'Audio alerts'), ('sleep', 'Sleep / stealth'), ('charging', 'Universal charging'), ('battery_preservation', 'Battery preservation'), ('roaming', 'Roaming'), ('advanced', 'Advanced settings'), ('about', 'About'), ('update', 'Software update')])
menu('help', 'Help', [('help_wifi', 'Connect with Wi-Fi'), ('help_admin', 'Admin settings'), ('help_usb', 'USB / Ethernet'), ('help_sim', 'SIM and PIN'), ('help_gps', 'GPS'), ('help_charging', 'Charging'), ('quickstart', 'Quick start'), ('support', 'Customer support')])
READS = {'status': ('Router status', '/api/status'), 'hardware': ('Hardware observations', '/api/openwrt/hardware'), 'capabilities': ('Capabilities', '/api/openwrt'), 'gnss': ('GPS / GNSS', '/api/openwrt/gnss'), 'system_info': ('System information', '/api/openwrt/system'), 'about': ('About', '/api/meta/full'), 'logs': ('System logs', '/api/openwrt/logs'), 'audit': ('Audit log', '/api/tools/audit?limit=100'), 'clients': ('Connected devices / DHCP leases', '/api/lan/leases'), 'data_usage': ('Data usage (runtime counters)', '/api/status'), 'sms': ('Messages', '/api/openwrt/sms'), 'session': ('Session', '/api/session')}
for id, (title, path) in READS.items(): page(id, title, endpoint=path)
for id, title, path, collection, editor in [('wifi', 'Wi-Fi interfaces', '/api/openwrt/wifi', 'interfaces', 'wifi_edit'), ('radios', 'Radios', '/api/openwrt/wifi', 'radios', 'radio_edit'), ('interfaces', 'Network interfaces', '/api/openwrt/interfaces', 'interfaces', 'interface_edit'), ('dhcp', 'DHCP and IPv6', '/api/openwrt/dhcp', None, 'dhcp_edit'), ('services', 'Installed services', '/api/openwrt/services', None, 'service')]:
    page(id, title, endpoint=path, collection=collection, editor=editor)
for id, title, endpoint, fields in [('wifi_primary', 'Primary Wi-Fi', '/api/wifi/primary', WIFI[:1] + WIFI[4:]), ('wifi_edit', 'Wi-Fi interface', '/api/openwrt/wifi', WIFI), ('radio_edit', 'Radio', '/api/openwrt/wifi', [switch('enabled'), field('channel', 'Channel (auto or number)'), field('country', 'Country code', max_length=2, pattern=r'^[A-Z]{2}$'), number('txpower', 0, 30)]), ('interface_edit', 'Network interface', '/api/openwrt/interfaces', INTERFACE), ('dhcp_edit', 'DHCP / IPv6', '/api/openwrt/dhcp', DHCP), ('system_edit', 'Host name and time zone', '/api/openwrt/system', [field('hostname', 'Host name', required=True, max_length=63, pattern=r'^[A-Za-z0-9][A-Za-z0-9-]*$'), field('timezone', 'POSIX time zone', max_length=100), field('zonename', 'Time zone name', max_length=100)]), ('settings', 'WebUI preferences', '/api/settings', [field('router_name', 'Router name', required=True, max_length=32), switch('expert_mode'), number('session_idle_s', 120, 86400), select('theme', ['system', 'light', 'dark']), number('status_poll_s', 2, 60)]), ('presentation', 'Touchscreen preferences', '/api/touchscreen/preferences', [select('language', ['en', 'es']), select('theme', ['system', 'light', 'dark']), number('display_timeout_s', 30, 3600)]), ('sms_send', 'Send message', '/api/openwrt/modem', [field('number', 'Recipient number', required=True, min_length=3, max_length=24, pattern=r'^\+?[0-9]+$'), field('text', 'Message', required=True, max_length=160)])]: page(id, title, endpoint=endpoint, fields=fields, tier=0 if id in ('settings', 'presentation') else 2)
page('service', 'Service controls', endpoint='/api/openwrt/services')
page('modem', 'Cellular / SIM', endpoint='/api/openwrt/modem')
page('credentials', 'Wi-Fi credentials', endpoint='/api/wifi/primary')
page('credentials_reveal', 'Wi-Fi password', endpoint='/api/wifi/primary/secret')
page('wifi_credentials', 'AP credentials', endpoint='/api/openwrt/wifi', collection='interfaces', editor='credentials')
page('blocked', 'Blocked devices', endpoint='/api/touchscreen/blocked', fields=[select('ap', [], label='Access point', required=True), field('mac', 'Device MAC', required=True, max_length=17), switch('blocked', label='Block this device')], tier=2)
page('blocked_aps', 'Choose AP for blocked devices', endpoint='/api/openwrt/wifi', collection='interfaces', editor='blocked')
page('explorer', 'API diagnostics', endpoint='/api/meta/routes')
page('explorer_read', 'API read result')
for kind in ('defaults', 'zones', 'forwardings', 'rules', 'redirects'):
    if kind != 'defaults': page('firewall_' + kind, kind.capitalize(), endpoint='/api/openwrt/firewall', collection=kind, editor='firewall_' + kind + '_edit')
    page('firewall_' + kind + '_edit', 'Firewall ' + kind, endpoint='/api/openwrt/firewall', firewall=kind, tier=2)
UNAVAILABLE = {
 'charging': ('Universal charging', 'No verified native USB source/charging policy provider exists.'), 'audio': ('Audio alerts', 'No verified native audio output or alert provider exists.'), 'reset': ('Factory reset', 'Persistent factory reset is outside this RAM-only backend.'), 'update': ('Software update', 'No validated image, rollback and native firmware update provider exists.'), 'power': ('Power options', 'No validated native shutdown/restart provider is exposed.'), 'sim': ('SIM / PIN / unlock', 'No typed native SIM PIN/PUK/restriction provider exists; modem is offline after secure IPA rejection.'), 'sleep': ('Sleep / stealth', 'Software screen inactivity is available in Touchscreen preferences; no validated suspend/stealth-power provider exists.'), 'battery_preservation': ('Battery preservation', 'No validated battery charge threshold provider exists.'), 'wps': ('WPS', 'WPS eligibility can be configured in Wi-Fi settings; no verified WPS session/status provider exists.'), 'file_sharing': ('File sharing', 'Disabled in the inspected stock menu configuration.'), 'speed_test': ('Speed test', 'Disabled in the inspected stock menu configuration.'), 'activation': ('Carrier activation / profile update', 'No native carrier activation/profile-update provider exists.'), 'tether': ('USB / Ethernet options', 'USB management is fixed/protected; no typed safe tether-mode provider exists.'), 'roaming': ('Roaming', 'No native roaming policy provider exists; modem is offline after secure IPA rejection.')}
for id, (title, reason) in UNAVAILABLE.items(): page(id, title, reason=reason)
TEXTS = {'advanced': ('Advanced settings', 'Full OpenWrt settings are available through Menu. The admin website uses the same password.'), 'quickstart': ('Quick start', 'Open Wi-Fi to configure your network. Use Devices to view DHCP leases. Settings last for this RAM boot.'), 'support': ('Customer support', 'This is a RAM OpenWrt session. Preserve boot identity and diagnostics when requesting support.'), 'help_wifi': ('Wi-Fi help', 'Use Primary Wi-Fi for credentials and security. Radios controls enabled state, channel, country and requested transmit power. Band selection unavailable: no typed native band-selection provider exists. Live API capabilities determine radio availability; traffic remains unqualified.'), 'help_admin': ('Admin help', 'Unlock with the WebUI admin password. Review each change before Apply. Lock ends the touchscreen session.'), 'help_usb': ('USB / Ethernet help', 'USB management is protected. Network settings affect connectivity; keep a recovery transport available.'), 'help_sim': ('SIM help', 'SIM PIN/PUK operations require a validated native provider. The current secure IPA rejection keeps the modem offline.'), 'help_gps': ('GPS help', 'GPS/GNSS shows backend availability and fixes only when reported. No fix is invented.'), 'help_charging': ('Charging help', 'Battery voltage is an observation. No charging policy, capacity or USB source mode is inferred from voltage.')}
for id, (title, text) in TEXTS.items(): page(id, title, text=text)


def visible(field, data):
    for key, values in field.get('visible_if', {}).items():
        value = data.get(key)
        if isinstance(value, list): value = ' '.join(value)
        if value not in values: return False
    return True


def _nav(id, label, target, context=None): return dict(id=id, label=label, kind='nav', value='', enabled=True, target=target, context=context or {})
def _text(label, value, id=None): return dict(id=id or label, label=label, value=value, kind='text', enabled=False)
def _human(value, prefix='', rows=None, depth=0):
    """Readable labelled diagnostics; bounded recursion, no raw JSON fallback."""
    rows = [] if rows is None else rows
    if depth > 6 or len(rows) >= 300: return rows
    if isinstance(value, dict):
        for key, item in value.items():
            if re.search(r'password|secret|csrf|cookie|token|imsi|iccid|imei', str(key), re.I): continue
            if str(key).startswith('.'): continue
            _human(item, (prefix + ' / ' if prefix else '') + str(key).replace('_', ' '), rows, depth+1)
    elif isinstance(value, list):
        for i, item in enumerate(value[:100]): _human(item, prefix + ' ' + str(i+1), rows, depth+1)
        if not value: rows.append(_text(prefix, 'None reported'))
    else: rows.append(_text(prefix, 'Unknown' if value is None else 'On' if value is True else 'Off' if value is False else str(value)[:2048]))
    return rows


def _availability_reason(endpoint, error):
    """Only declared availability and the identified modem provider read failure."""
    if error.status == 503 and error.code == 'unavailable':
        return error.message or 'Function unavailable'
    if (error.status == 502 and error.code == 'native_command_failed'
            and endpoint in ('/api/openwrt/modem', '/api/openwrt/sms')
            and isinstance(error.details, dict) and error.details.get('program') == 'm3100-modem'):
        return 'Native cellular / SMS provider failed; this function is unavailable.'
    return None


def load(client, page_id, context=None):
    spec = PAGES[page_id]
    ctx = dict(context or {})
    out = dict(id=page_id, title=spec['title'], kind='list', rows=[], fields=[], actions=[], data={}, context=ctx)
    if 'links' in spec:
        out['rows'] = [_nav(target, label, target) for target, label in spec['links']]
        return out
    if 'reason' in spec or 'text' in spec:
        out['reason'] = spec.get('reason')
        out['rows'] = [_text('Availability' if 'reason' in spec else 'Information', spec.get('reason', spec.get('text')))]
        if page_id == 'sleep': out['rows'].append(_nav('preferences', 'Screen inactivity', 'presentation'))
        return out
    if page_id == 'explorer_read':
        path = ctx.get('path')
        allowed = [p for m, p in NATIVE_ROUTES if m == 'GET' and '{' not in p and p not in ('/api/wifi/primary/secret', '/api/tools/audit/export')]
        if path not in allowed: raise ValueError('Only catalogued non-secret JSON reads are allowed')
        endpoint = path
    else: endpoint = spec['endpoint']
    if page_id == 'blocked' and ctx.get('ap'): endpoint += '?ap=' + quote(str(ctx['ap']), safe='')
    if page_id == 'credentials_reveal' and ctx.get('name'): endpoint = '/api/touchscreen/wifi/' + quote(str(ctx['name']), safe='') + '/secret'
    if page_id == 'credentials' and ctx.get('name'): endpoint = '/api/openwrt/wifi'
    try: data = client.request('GET', endpoint)
    except ApiError as error:
        reason = _availability_reason(endpoint, error)
        if reason is None: raise
        out['reason'] = reason
        # ApiError has already bounded/sanitized the cause. Keep diagnostics as
        # disabled text, never as observed source/configuration or form values.
        out['rows'] = [_text('Unavailable', reason), _text('API failure', ('HTTP ' + str(error.status) + ' / ' + error.code + ': ' + str(error.message))[:1024])]
        out['rows'] += [dict(row, value=str(row['value'])[:1024]) for row in _human(error.details, 'Diagnostic')[:8]] if error.details is not None else []
        if page_id == 'blocked': out['rows'].append(_nav('choose-ap', 'Choose another access point', 'blocked_aps'))
        return out
    if page_id == 'explorer_read':
        out['data'], out['rows'] = data, _human(data)
        return out
    out['source'] = copy.deepcopy(data)
    out['data'] = copy.deepcopy(data)
    if page_id == 'explorer':
        out['rows'] = [_nav(str(i), r['method'] + ' ' + r['path'], 'explorer_read', {'path': r['path']}) if r['method'] == 'GET' and '{' not in r['path'] and r['path'] not in ('/api/wifi/primary/secret', '/api/tools/audit/export') and (r['method'], r['path']) in NATIVE_ROUTES else _text(r['method'] + ' ' + r['path'], 'Use its typed settings form; tier ' + str(r.get('tier', 0))) for i, r in enumerate(data)]
        return out
    if 'editor' in spec:
        items = data.get(spec['collection'], []) if spec.get('collection') else data
        if page_id == 'wifi_credentials': items = [x for x in items if x.get('mode', 'ap') == 'ap']
        if page_id == 'blocked_aps': items = [x for x in items if x.get('mode', 'ap') == 'ap' and x.get('enabled') is True]
        if not isinstance(items, list): items = []
        for i, item in enumerate(items):
            name = item.get('section') or item.get('name')
            label = item.get('options', {}).get('name') or name or str(i+1)
            context_key = 'ap' if page_id == 'blocked_aps' else 'section' if item.get('section') else 'name'
            out['rows'].append(_nav('item:' + str(i), label, spec['editor'], {context_key: name}))
        if page_id == 'wifi' or page_id in ('firewall_forwardings', 'firewall_rules', 'firewall_redirects'):
            out['rows'].append(_nav('create', 'Add new', spec['editor'], {'create': True}))
        if not items: out['rows'].insert(0, _text('Status', 'No entries reported'))
        if page_id == 'radios': out['rows'].append(_text('Band selection', 'Band selection unavailable: no typed native band-selection provider exists. Supported radio controls remain enabled state, channel, country and transmit power.'))
        return out
    if spec.get('firewall'):
        kind = spec['firewall']
        row = data.get('defaults') if kind == 'defaults' else next((x for x in data.get(kind, []) if x['section'] == ctx.get('section')), None)
        if not ctx.get('create') and not row: raise ValueError('Configuration no longer exists; reload')
        out['data'] = copy.deepcopy(row['options']) if row else {'section': '', 'name': '', 'enabled': True, 'family': 'ipv4' if kind == 'redirects' else 'any', 'proto': ['tcp'], 'target': 'ACCEPT' if kind == 'rules' else 'DNAT'}
        if kind == 'defaults' and 'synflood_protect' not in out['data'] and 'syn_flood' in out['data']:
            out['data']['synflood_protect'] = out['data']['syn_flood'] == '1'
        out['fields'] = firewall_fields(kind, ctx.get('create', False))
        zones = [x['options']['name'] for x in data.get('zones', []) if x['options'].get('name') != data.get('protection', {}).get('zone') and x['options'].get('enabled', True)]
        for f in out['fields']:
            if f['key'] in ('src', 'dest'): f['choices'] = ([''] if f['key'] == 'dest' and kind == 'rules' else []) + zones
        if row and not row.get('editable'): out['reason'] = row.get('reason') or 'Backend marks this section read-only'
        if row and row.get('removable'): out['actions'].append(dict(id='delete', label='Remove section', tier=2))
        if data.get('runtime', {}).get('available') is False:
            out['reason'] = 'A working firewall4 runtime table is required before editing; inspect Firewall diagnostics.'
        if data.get('protection', {}).get('problem'):
            out['reason'] = 'USB management policy requires repair: ' + str(data['protection']['problem'])
        if out.get('reason'): out['actions'] = []
    elif page_id in ('wifi_edit', 'radio_edit', 'interface_edit', 'dhcp_edit', 'service'):
        key = {'wifi_edit': 'interfaces', 'radio_edit': 'radios', 'interface_edit': 'interfaces'}.get(page_id)
        items = data.get(key, []) if key else data
        row = next((x for x in items if x.get('name') == ctx.get('name')), None)
        if not row and not ctx.get('create'): raise ValueError('Configuration no longer exists; reload')
        out['data'] = copy.deepcopy(row or {'name': '', 'mode': 'ap', 'security': 'wpa2', 'enabled': True, 'network': 'lan', 'device': data.get('radios', [{}])[0].get('name', '') if data.get('radios') else '', 'ssid': ''})
        out['fields'] = copy.deepcopy(spec.get('fields', []))
        if page_id == 'wifi_edit':
            if ctx.get('create'): out['fields'].insert(0, field('name', 'Configuration name', required=True, max_length=64, pattern=r'^[A-Za-z0-9_][A-Za-z0-9_-]*$'))
            else: out['actions'].append(dict(id='delete', label='Remove interface', tier=2))
            next(f for f in out['fields'] if f['key'] == 'device').update(type='select', choices=[r['name'] for r in data.get('radios', [])])
        if page_id == 'radio_edit':
            out['data']['enabled'] = out['data'].get('disabled', '0') != '1'
            out['rows'].append(_text('Band selection', 'Band selection unavailable: no typed native band-selection provider exists. Enabled state, channel, country and transmit power can still be edited.'))
        if page_id == 'dhcp_edit': out['data']['ignore'] = out['data'].get('ignore') == '1'
        if page_id == 'interface_edit': out['actions'] += [dict(id=a, label=a.capitalize() + ' interface', tier=2) for a in ('up', 'down')]
        if page_id == 'service':
            out['actions'] = [dict(id=a, label=a.capitalize(), tier=2, enabled=bool(row.get('installed')) and a not in row.get('blocked_actions', [])) for a in ('start', 'stop', 'restart', 'reload')]
            out['rows'] = _human(row)
    elif page_id == 'system_edit': out['data'], out['fields'] = copy.deepcopy(data.get('config', {})), copy.deepcopy(spec['fields'])
    elif 'fields' in spec:
        out['fields'] = copy.deepcopy(spec['fields'])
        if page_id == 'wifi_primary': out['actions'] += [dict(id=a, label=l, tier=2) for a, l in [('wifi_on', 'Enable all Wi-Fi radios'), ('wifi_off', 'Disable all Wi-Fi radios')]]
        if page_id == 'sms_send':
            out['data'] = {'number': '', 'text': ''}
            if data.get('available') is not True or data.get('capabilities', {}).get('sms') is not True: out['reason'] = 'Native modem SMS capability is unavailable; current modem is offline after secure IPA rejection.'
        if page_id == 'blocked':
            out['data'] = {'ap': data.get('ap', ''), 'mac': ctx.get('mac', ''), 'blocked': True}
            out['fields'][0]['choices'] = data.get('aps', [])
            out['rows'] = _human(data)
    elif page_id == 'modem':
        out['rows'] = _human(data)
        if data.get('available') is True:
            if data.get('capabilities', {}).get('connect') is True: out['actions'] += [dict(id=a, label=a.capitalize(), tier=2) for a in ('connect', 'disconnect', 'reconnect')]
            if data.get('capabilities', {}).get('mode') is True: out['actions'] += [dict(id=a, label=l, tier=2) for a, l in [('airplane_on', 'Airplane mode on'), ('airplane_off', 'Airplane mode off')]]
        else: out['reason'] = data.get('reason') or 'Native modem unavailable; current boot rejects IPA securely.'
    elif page_id == 'credentials':
        if ctx.get('name'):
            data = next((x for x in data.get('interfaces', []) if x.get('name') == ctx['name'] and x.get('mode', 'ap') == 'ap'), None)
            if data is None: raise ValueError('Access point no longer exists; reload')
        out['rows'] = [_text('Network name', data.get('ssid', 'Unknown')), _text('Security', data.get('security') or data.get('security_label', 'Unknown')), _text('Password', 'Saved; select Show Wi-Fi password' if data.get('password_set') else 'Not set')]
        out['rows'].append(_nav('reveal', 'Show Wi-Fi password', 'credentials_reveal', ctx))
        out['rows'].append(_nav('choose', 'Other access points', 'wifi_credentials'))
        out['rows'].append(_nav('edit', 'Edit credentials', 'wifi_edit' if ctx.get('name') else 'wifi_primary', ctx))
    elif page_id == 'credentials_reveal':
        out['sensitive'] = True
        out['rows'] = [dict(_text('Network name', data.get('ssid', 'Primary Wi-Fi')), sensitive=True), dict(_text('Password', data.get('password') or 'Not set'), sensitive=True)]
    else:
        if page_id == 'data_usage':
            data = {'receive bytes': data.get('wan', {}).get('rx_bytes'), 'transmit bytes': data.get('wan', {}).get('tx_bytes'), 'note': 'Runtime interface counters; no carrier billing quota provider exists.'}
        out['rows'] = _human(data)
        if page_id == 'session': out['actions'] = [dict(id='logout', label='Lock / logout', tier=0)]
    if out['fields']:
        out['kind'] = 'form'
        for f in out['fields']:
            key = f['key']
            if f.get('secret'): out['data'][key] = ''
            if f['type'] == 'number' and key in out['data']:
                try: out['data'][key] = int(out['data'][key])
                except (ValueError, TypeError): pass
            if f['type'] == 'switch' and key in out['data'] and isinstance(out['data'][key], str): out['data'][key] = out['data'][key] == '1'
        if not out.get('reason'): out['actions'].insert(0, dict(id='save', label='Review changes', tier=spec.get('tier', 2)))
    return out


def prepare(page_id, loaded, draft, action=None):
    if loaded.get('id') != page_id or loaded.get('reason'): raise ValueError(loaded.get('reason') or 'Page mismatch')
    spec, ctx = PAGES[page_id], loaded['context']
    action = action or 'save'
    offered = next((x for x in loaded['actions'] if x['id'] == action and x.get('enabled', True)), None)
    if not offered: raise ValueError('Action is unavailable')
    base = loaded.get('data', {})
    values = dict(base) if isinstance(base, dict) else {}
    values.update(draft)
    body, summary = {}, []
    create = ctx.get('create', False)
    if action == 'save':
        if spec.get('firewall') == 'rules' and 'proto' in draft:
            proto = draft['proto'] if isinstance(draft['proto'], list) else str(draft['proto']).split()
            if not set(proto) <= {'tcp', 'udp'} and any(base.get(key) for key in ('src_port', 'dest_port')):
                raise ValueError('Clear source and destination ports before selecting a protocol without ports')
        for f in loaded['fields']:
            if visible(f, values) and f.get('required') and values.get(f['key']) in (None, '', []):
                raise ValueError(f['label'] + ': required')
        for f in loaded['fields']:
            key = f['key']
            explicit_wps_disable = key == 'wps' and draft.get('wps') is False
            if (not visible(f, values) and not explicit_wps_disable) or key not in values: continue
            value = values[key]
            if f.get('keep_blank') and value == '': continue
            if not create and key not in draft: continue
            if f['type'] == 'number':
                if isinstance(value, bool): raise ValueError(f['label'] + ': enter a number')
                try: value = int(str(value))
                except (ValueError, TypeError): raise ValueError(f['label'] + ': enter a number') from None
                if not f['min'] <= value <= f['max']: raise ValueError(f['label'] + ': out of range')
            elif f['type'] == 'switch':
                if type(value) is not bool: raise ValueError(f['label'] + ': use on or off')
            elif f['type'] == 'list':
                if isinstance(value, str): value = [x for x in re.split(r'[\s,]+', value) if x]
                if not isinstance(value, list) or not all(isinstance(x, str) for x in value) or len(value) > f.get('max_items', 32): raise ValueError(f['label'] + ': invalid list')
            elif f['key'] == 'proto' and f.get('type_hint') == 'list':
                if isinstance(value, list): value = ' '.join(value)
                if value not in f['choices']: raise ValueError('Choose a supported protocol')
                value = value.split()
            elif f['type'] == 'select':
                if value not in f['choices']: raise ValueError(f['label'] + ': choose a supported value')
            else:
                if not isinstance(value, str): raise ValueError(f['label'] + ': enter text')
                if len(value) > f.get('max_length', 256) or len(value) < f.get('min_length', 0): raise ValueError(f['label'] + ': invalid length')
                if f.get('pattern') and not re.fullmatch(f['pattern'], value): raise ValueError(f['label'] + ': invalid format')
            if f.get('required') and value in ('', []): raise ValueError(f['label'] + ': required')
            if not create and value == base.get(key): continue
            if page_id == 'radio_edit' and key == 'channel' and value != 'auto':
                try: value = int(value)
                except (ValueError, TypeError): raise ValueError('Channel: use auto or a legal channel number') from None
                if not 1 <= value <= 233: raise ValueError('Channel: out of range')
            body[key] = value
            summary.append(f['label'] + ': ' + ('Changed' if f.get('secret') else ', '.join(value) if isinstance(value, list) else str(value)))
        if not body: raise ValueError('No changes to apply')
        if page_id in ('wifi_edit', 'wifi_primary'):
            if values.get('security') in ('wpa2', 'wpa3', 'wpa2_wpa3') and not values.get('password') and not base.get('password_set'): raise ValueError('Wi-Fi password is required for this security mode')
        if page_id == 'blocked':
            # This typed operation is a complete command, not a partial config patch.
            body = {key: values[key] for key in ('ap', 'mac', 'blocked')}
    else: summary = [offered['label']]
    method, path = 'PUT', spec.get('endpoint')
    name = quote(str(ctx.get('name', '')), safe='')
    section = quote(str(ctx.get('section', '')), safe='')
    if page_id in ('wifi_edit', 'radio_edit', 'interface_edit', 'dhcp_edit', 'service'):
        prefix = {'wifi_edit': 'wifi', 'radio_edit': 'radios', 'interface_edit': 'interfaces', 'dhcp_edit': 'dhcp', 'service': 'services'}[page_id]
        path = '/api/openwrt/' + prefix + '/' + name
        if create: method, path = 'POST', '/api/openwrt/' + prefix
        if action == 'delete': method = 'DELETE'
        elif action not in ('save', 'delete'): method, path = 'POST', path + '/' + action
    elif spec.get('firewall'):
        kind = spec['firewall']
        path = '/api/openwrt/firewall/' + kind
        if kind != 'defaults' and not create: path += '/' + section
        if create: method = 'POST'
        if action == 'delete': method = 'DELETE'
    elif page_id == 'sms_send': method, path = 'POST', '/api/openwrt/sms'
    elif page_id == 'wifi_primary' and action.startswith('wifi_'): method, path, body = 'POST', '/api/wifi/radio', {'on': action == 'wifi_on'}
    elif page_id == 'modem':
        if action.startswith('airplane_'): path, body = '/api/cellular/mode', {'airplane': action == 'airplane_on'}
        else: method, path = 'POST', '/api/cellular/' + action
    elif page_id == 'session': method, path = 'POST', '/api/logout'
    return dict(method=method, path=path, body=body, tier=offered['tier'], summary=summary)


STOCK_COVERAGE = json.loads(pathlib.Path(__file__).with_name('stock_coverage.json').read_text(encoding='utf-8'))['resources']
# NATIVE_ROUTES is the frozen 54-route R15 inventory, including lifecycle/diagnostics.
NATIVE_ROUTES = [('POST', '/api/cellular/connect'), ('POST', '/api/cellular/disconnect'), ('PUT', '/api/cellular/mode'), ('POST', '/api/cellular/reconnect'), ('GET', '/api/lan/leases'), ('POST', '/api/login'), ('POST', '/api/logout'), ('GET', '/api/meta'), ('GET', '/api/meta/full'), ('GET', '/api/meta/routes'), ('GET', '/api/openwrt'), ('GET', '/api/openwrt/dhcp'), ('PUT', '/api/openwrt/dhcp/{name}'), ('GET', '/api/openwrt/firewall'), ('PUT', '/api/openwrt/firewall/defaults'), ('POST', '/api/openwrt/firewall/forwardings'), ('DELETE', '/api/openwrt/firewall/forwardings/{section}'), ('PUT', '/api/openwrt/firewall/forwardings/{section}'), ('POST', '/api/openwrt/firewall/redirects'), ('DELETE', '/api/openwrt/firewall/redirects/{section}'), ('PUT', '/api/openwrt/firewall/redirects/{section}'), ('POST', '/api/openwrt/firewall/rules'), ('DELETE', '/api/openwrt/firewall/rules/{section}'), ('PUT', '/api/openwrt/firewall/rules/{section}'), ('PUT', '/api/openwrt/firewall/zones/{section}'), ('GET', '/api/openwrt/gnss'), ('GET', '/api/openwrt/hardware'), ('GET', '/api/openwrt/interfaces'), ('PUT', '/api/openwrt/interfaces/{name}'), ('POST', '/api/openwrt/interfaces/{name}/{action}'), ('GET', '/api/openwrt/logs'), ('GET', '/api/openwrt/modem'), ('PUT', '/api/openwrt/radios/{name}'), ('GET', '/api/openwrt/services'), ('POST', '/api/openwrt/services/{name}/{action}'), ('GET', '/api/openwrt/sms'), ('POST', '/api/openwrt/sms'), ('GET', '/api/openwrt/system'), ('PUT', '/api/openwrt/system'), ('GET', '/api/openwrt/wifi'), ('POST', '/api/openwrt/wifi'), ('DELETE', '/api/openwrt/wifi/{name}'), ('PUT', '/api/openwrt/wifi/{name}'), ('GET', '/api/session'), ('GET', '/api/settings'), ('PUT', '/api/settings'), ('GET', '/api/status'), ('GET', '/api/tools/audit'), ('GET', '/api/tools/audit/export'), ('GET', '/api/ui/pages'), ('GET', '/api/wifi/primary'), ('PUT', '/api/wifi/primary'), ('GET', '/api/wifi/primary/secret'), ('POST', '/api/wifi/radio')]
