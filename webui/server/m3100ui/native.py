"""OpenWrt services matching the SPA's status and settings contracts.

Native-specific controls expose netifd/UCI concepts without pretending vendor
features exist. Missing drivers and transports remain visible capability results.
"""
import ipaddress
import json
import math
import os
import re
import time

from . import validate as v
from .errors import ApiError, field_error, unavailable

SECURITY = {'open': 'none', 'wpa2': 'psk2', 'wpa3': 'sae', 'wpa2_wpa3': 'sae-mixed', 'owe': 'owe'}
SECRET_KEYS = {'key', 'password', 'passwd', 'pin', 'pincode', 'puk', 'username', 'imsi', 'iccid', 'imei', 'msisdn'}


def redact(value):
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key.lower() in SECRET_KEYS or any(x in key.lower() for x in ('password', 'secret', 'token', 'private_key', 'pincode', 'imsi', 'iccid', 'imei')):
                out[key + '_set'] = bool(item)
            elif key in ('command', 'env', 'environment'):
                continue
            else:
                out[key] = redact(item)
        return out
    return value


def identifier(value):
    return v.text('name', value, 1, 64, r'^[A-Za-z0-9_][A-Za-z0-9_-]*$')


def section(native, package, name, kind):
    identifier(name)
    value = native.config(package).get(name)
    if not value or value.get('.type') != kind:
        raise ApiError(404, 'not_found', 'No such native configuration section')
    return value


def safe(fn, default, errors=None, key=None):
    try:
        return fn()
    except ApiError as e:
        if errors is not None:
            errors[key] = {'code': e.code, 'message': e.message}
        return default


def metric_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def access_points(config):
    aps = [(name, value) for name, value in config.items()
           if value.get('.type') == 'wifi-iface' and value.get('mode', 'ap') == 'ap']
    return sorted(aps, key=lambda pair: pair[1].get('disabled') == '1'
                  or config.get(pair[1].get('device'), {}).get('.type') != 'wifi-device'
                  or config.get(pair[1].get('device'), {}).get('disabled') == '1')


def register(app):
    from .native_firewall import register as register_firewall, guard_network, blocked_service_actions, protected_error
    register_firewall(app)
    native = app.adapters.native
    r = app.router
    app.features.add('openwrt', lambda: True)
    app.features.add('wifi.primary', lambda: any(x.get('.type') == 'wifi-iface' and x.get('mode', 'ap') == 'ap'
                                               for x in native.config('wireless').values()))

    def changed(req, action, keys):
        app.cache.invalidate()
        app.audit(req, action, {'keys': sorted(keys)})

    def wifi_public(name, config):
        radio = native.config('wireless').get(config.get('device'), {})
        encryption = config.get('encryption', 'none').split('+')[0]
        security = next((k for k, val in SECURITY.items() if val == encryption), None)
        enabled = config.get('disabled', '0') != '1' and radio.get('disabled', '0') != '1'
        runtime = safe(lambda: native.ubus('network.wireless', 'status'), None)
        bands = None
        if isinstance(runtime, dict):
            live = runtime.get(config.get('device'))
            if live is None or live.get('up') is False:
                bands = []
            elif live.get('up') is True and isinstance(live.get('interfaces'), list):
                matching = any(item.get('section') == name and item.get('up') is not False for item in live['interfaces'])
                band = radio.get('band') or {'11g': '2g', '11a': '5g'}.get(radio.get('hwmode'))
                bands = [band] if matching and band in ('2g', '5g', '6g') else None if matching else []
        return {'name': name, 'ssid': config.get('ssid', ''), 'mode': config.get('mode', 'ap'),
                'device': config.get('device', ''), 'network': config.get('network', ''),
                'enabled': enabled, 'security': security, 'security_label': encryption,
                'security_options': [{'value': k, 'label': label, 'needs_password': k not in ('open', 'owe')}
                                     for k, label in [('open', 'Open'), ('wpa2', 'WPA2'), ('wpa3', 'WPA3'),
                                                      ('wpa2_wpa3', 'WPA2/WPA3'), ('owe', 'Enhanced Open')]],
                'password_set': bool(config.get('key')), 'hidden': config.get('hidden', '0') == '1',
                'isolation': config.get('isolate', '0') == '1', 'wps': config.get('wps_pushbutton', '0') == '1',
                'active_bands': bands, 'advanced_available': True, 'split_ssid': False,
                'split_ssid_available': False, 'platform': 'openwrt',
                'show_ssid_on_screen': None, 'show_password_on_screen': None,
                'other_ssid': '', 'mac_filter_on': False, 'runtime_verification': 'See native radio state for driver/AP readiness'}

    def wifi_update(req, name, create=False):
        app.require_tier(req, 2)
        with native.lock:
            identifier(name)
            current = {} if create else section(native, 'wireless', name, 'wifi-iface')
            if create and name in native.config('wireless'):
                raise ApiError(409, 'conflict', 'A wireless configuration section already has this name')
            data = v.only_keys(req.json, ['ssid', 'password', 'security', 'hidden', 'isolation', 'wps',
                                         'mode', 'network', 'enabled', 'device'] + (['name'] if create else []))
            if create:
                data.pop('name', None)
                for required in ('ssid', 'mode', 'network', 'device', 'security'):
                    if required not in data:
                        raise field_error(required, 'Required for a new wireless interface')
            values = {}
            for key, option in [('ssid', 'ssid'), ('password', 'key')]:
                if key in data:
                    values[option] = v.text(key, data[key], 1 if key == 'ssid' else 8, 32 if key == 'ssid' else 63)
            if 'security' in data:
                values['encryption'] = SECURITY[v.choice('security', data['security'], SECURITY)]
            security = values.get('encryption', current.get('encryption', 'none')).split('+')[0]
            if security not in ('none', 'owe') and not values.get('key', current.get('key')):
                raise field_error('password', 'A password is required for this security mode')
            if 'mode' in data:
                values['mode'] = v.choice('mode', data['mode'], ['ap', 'sta'])
            if 'network' in data:
                values['network'] = identifier(data['network'])
                section(native, 'network', values['network'], 'interface')
            if 'device' in data:
                values['device'] = identifier(data['device'])
                section(native, 'wireless', values['device'], 'wifi-device')
            for key, option in [('hidden', 'hidden'), ('isolation', 'isolate'), ('wps', 'wps_pushbutton'),
                                ('enabled', 'disabled')]:
                if key in data:
                    flag = v.boolean(key, data[key])
                    values[option] = '1' if (not flag if key == 'enabled' else flag) else '0'
            if values.get('wps_pushbutton', current.get('wps_pushbutton', '0')) == '1':
                if security != 'psk2' or values.get('hidden', current.get('hidden', '0')) == '1':
                    raise field_error('wps', 'WPS requires WPA2 and a visible SSID')
            native.configure('wireless', [(name, values)], ['wifi', 'reload'], create_type='wifi-iface' if create else None)
            changed(req, 'openwrt.wifi.update', data)
            return wifi_public(name, section(native, 'wireless', name, 'wifi-iface'))

    def primary_name():
        config = native.config('wireless')
        aps = access_points(config)
        if aps:
            return aps[0][0]
        raise unavailable('No access point is configured; create or configure a native Wi-Fi interface')

    @r.route('GET', '/api/wifi/primary')
    def wifi_primary(req):
        name = primary_name()
        return wifi_public(name, section(native, 'wireless', name, 'wifi-iface'))

    @r.route('PUT', '/api/wifi/primary', tier=2)
    def put_wifi_primary(req):
        return wifi_update(req, primary_name())

    @r.route('GET', '/api/wifi/primary/secret')
    def wifi_secret(req):
        app.audit(req, 'openwrt.wifi.secret.read')
        return {'password': section(native, 'wireless', primary_name(), 'wifi-iface').get('key', '')}

    @r.route('GET', '/api/openwrt/wifi')
    def wifi_all(req):
        config = native.config('wireless')
        return {'interfaces': [wifi_public(name, val) for name, val in config.items() if val.get('.type') == 'wifi-iface'],
                'radios': [dict(redact(val), name=name) for name, val in config.items() if val.get('.type') == 'wifi-device'],
                'runtime': redact(native.ubus('network.wireless', 'status'))}

    @r.route('PUT', '/api/openwrt/wifi/{name}', tier=2)
    def put_wifi(req):
        return wifi_update(req, req.params['name'])

    @r.route('POST', '/api/openwrt/wifi', tier=2)
    def add_wifi(req):
        return wifi_update(req, req.field('name'), create=True)

    @r.route('DELETE', '/api/openwrt/wifi/{name}', tier=2)
    def remove_wifi(req):
        app.require_tier(req, 2)
        name = identifier(req.params['name'])
        with native.lock:
            section(native, 'wireless', name, 'wifi-iface')
            native.configure('wireless', [(name, {})], ['wifi', 'reload'], delete=True)
        changed(req, 'openwrt.wifi.delete', [name])
        return {'deleted': name}

    @r.route('PUT', '/api/openwrt/radios/{name}', tier=2)
    def put_radio(req):
        app.require_tier(req, 2)
        name = identifier(req.params['name'])
        with native.lock:
            section(native, 'wireless', name, 'wifi-device')
            data = v.only_keys(req.json, ['enabled', 'channel', 'country', 'txpower'])
            values = {}
            if 'enabled' in data:
                values['disabled'] = '0' if v.boolean('enabled', data['enabled']) else '1'
            if 'channel' in data:
                values['channel'] = 'auto' if data['channel'] == 'auto' else str(v.integer('channel', data['channel'], 1, 233))
            if 'country' in data:
                values['country'] = v.text('country', data['country'], 2, 2, r'^[A-Z]{2}$')
            if 'txpower' in data:
                values['txpower'] = str(v.integer('txpower', data['txpower'], 0, 30))
            native.configure('wireless', [(name, values)], ['wifi', 'reload'])
            changed(req, 'openwrt.radio.update', data)
            return redact(section(native, 'wireless', name, 'wifi-device'))

    @r.route('POST', '/api/wifi/radio', tier=2)
    def radio_toggle(req):
        app.require_tier(req, 2)
        data = v.only_keys(req.json, ['on'])
        on = v.boolean('on', data.get('on'))
        with native.lock:
            radios = [(name, {'disabled': '0' if on else '1'}) for name, val in native.config('wireless').items()
                      if val.get('.type') == 'wifi-device']
            if not radios:
                raise unavailable('No native wireless radio configured')
            native.configure('wireless', radios, ['wifi', 'reload'])
        changed(req, 'openwrt.wifi.radio', data)
        return {'enabled': on, 'requested': True}

    def network_public(name, config):
        out = redact(config)
        out.update({'name': name, 'enabled': config.get('disabled', '0') != '1',
                    'password_set': bool(config.get('password')), 'username': config.get('username', '')})
        return out

    @r.route('GET', '/api/openwrt/interfaces')
    def interfaces(req):
        return {'interfaces': [network_public(name, val) for name, val in native.config('network').items()
                               if val.get('.type') == 'interface'],
                'runtime': redact(native.ubus('network.interface', 'dump'))}

    @r.route('PUT', '/api/openwrt/interfaces/{name}', tier=2)
    def put_interface(req):
        app.require_tier(req, 2)
        name = identifier(req.params['name'])
        with native.lock:
            current = section(native, 'network', name, 'interface')
            data = v.only_keys(req.json, ['proto', 'ipaddr', 'netmask', 'gateway', 'dns', 'device', 'metric', 'enabled',
                                         'apn', 'username', 'password', 'auth', 'pdptype', 'ipv6', 'peerdns'])
            guard_network(native, name, data)
            values = {}
            if 'proto' in data:
                values['proto'] = v.choice('proto', data['proto'], ['static', 'dhcp', 'dhcpv6', 'qmi', 'modemmanager', 'none'])
            for key in ('ipaddr', 'gateway'):
                if key in data:
                    values[key] = v.ipv4(key, data[key], allow_empty=key == 'gateway')
            if 'netmask' in data:
                values['netmask'] = v.netmask('netmask', data['netmask'])
            if values.get('proto', current.get('proto')) == 'static':
                if not values.get('ipaddr', current.get('ipaddr')) or not values.get('netmask', current.get('netmask')):
                    raise field_error('ipaddr', 'Static interfaces require an address and subnet mask')
            if 'dns' in data:
                if not isinstance(data['dns'], list) or len(data['dns']) > 4:
                    raise field_error('dns', 'Use a list of up to four IP addresses')
                values['dns'] = [v.ip_any('dns', item) for item in data['dns']]
            if 'device' in data:
                values['device'] = v.text('device', data['device'], 1, 128, r'^(?:/dev/)?[A-Za-z0-9_.:-]+$')
            if 'apn' in data:
                values['apn'] = v.text('apn', data['apn'], 0, 100, r'^[A-Za-z0-9.-]*$')
            for key in ('username', 'password'):
                if key in data:
                    values[key] = v.text(key, data[key], 0, 128)
            if 'auth' in data:
                values['auth'] = v.choice('auth', data['auth'], ['none', 'pap', 'chap', 'both'])
            if 'pdptype' in data:
                values['pdptype'] = v.choice('pdptype', data['pdptype'], ['ip', 'ipv6', 'ipv4v6'])
            if 'metric' in data:
                values['metric'] = str(v.integer('metric', data['metric'], 0, 65535))
            for key in ('ipv6', 'peerdns', 'enabled'):
                if key in data:
                    flag = v.boolean(key, data[key])
                    values['disabled' if key == 'enabled' else key] = '1' if (not flag if key == 'enabled' else flag) else '0'
            native.configure('network', [(name, values)], ['ubus', 'call', 'network', 'reload', '{}'])
            changed(req, 'openwrt.network.update', data)
            return network_public(name, section(native, 'network', name, 'interface'))

    @r.route('POST', '/api/openwrt/interfaces/{name}/{action}', tier=2)
    def interface_action(req):
        app.require_tier(req, 2)
        name = identifier(req.params['name'])
        action = v.choice('action', req.params['action'], ['up', 'down'])
        if action == 'down':
            guard_network(native, name)
        section(native, 'network', name, 'interface')
        native.ubus('network.interface.' + name, action)
        changed(req, 'openwrt.interface.' + action, [name])
        return {'requested': True, 'interface': name, 'action': action}

    @r.route('GET', '/api/openwrt/dhcp')
    def get_dhcp(req):
        return [{'name': name, **redact(val)} for name, val in native.config('dhcp').items() if val.get('.type') == 'dhcp']

    @r.route('PUT', '/api/openwrt/dhcp/{name}', tier=2)
    def put_dhcp(req):
        app.require_tier(req, 2)
        name = identifier(req.params['name'])
        section(native, 'dhcp', name, 'dhcp')
        data = v.only_keys(req.json, ['start', 'limit', 'leasetime', 'ignore', 'ra', 'dhcpv6'])
        values = {}
        for key in ('start', 'limit'):
            if key in data:
                values[key] = str(v.integer(key, data[key], 1, 65535))
        if 'leasetime' in data:
            values['leasetime'] = v.text('leasetime', data['leasetime'], 1, 12, r'^[1-9][0-9]*[smhdw]?$')
        if 'ignore' in data:
            values['ignore'] = '1' if v.boolean('ignore', data['ignore']) else '0'
        for key in ('ra', 'dhcpv6'):
            if key in data:
                values[key] = v.choice(key, data[key], ['disabled', 'server', 'relay', 'hybrid'])
        native.configure('dhcp', [(name, values)], ['/etc/init.d/dnsmasq', 'restart'])
        if 'ra' in data or 'dhcpv6' in data:
            native.command(['/etc/init.d/odhcpd', 'restart'])
        changed(req, 'openwrt.dhcp.update', data)
        return redact(section(native, 'dhcp', name, 'dhcp'))

    @r.route('GET', '/api/lan/leases')
    def leases(req):
        rows = native.system.dhcp_leases()
        if rows is None:
            raise unavailable('DHCP lease source is unavailable')
        return rows

    @r.route('GET', '/api/openwrt/system')
    def get_system(req):
        config = native.config('system')
        names = [(key, val) for key, val in config.items() if val.get('.type') == 'system']
        return {'board': native.ubus('system', 'board'), 'info': native.ubus('system', 'info'),
                'config': {'name': names[0][0], **redact(names[0][1])} if names else {},
                'power': native.system.power_supplies(), 'thermal': native.system.thermal(),
                'cpu': native.system.cpu()}

    @r.route('PUT', '/api/openwrt/system', tier=2)
    def put_system(req):
        app.require_tier(req, 2)
        data = v.only_keys(req.json, ['hostname', 'timezone', 'zonename'])
        names = [name for name, val in native.config('system').items() if val.get('.type') == 'system']
        if not names:
            raise unavailable('No system UCI section')
        values = {}
        for key in data:
            values[key] = v.text(key, data[key], 1, 100, r'^[A-Za-z0-9][A-Za-z0-9-]{0,62}$' if key == 'hostname' else None)
        native.configure('system', [(names[0], values)], ['/etc/init.d/system', 'reload'])
        changed(req, 'openwrt.system.update', data)
        return get_system(req)

    @r.route('GET', '/api/openwrt/services')
    def services(req):
        live = native.ubus('service', 'list')
        return [{'name': name, 'installed': os.path.isfile('/etc/init.d/' + name), 'runtime': redact(live.get(name, {})),
                 'blocked_actions': blocked_service_actions(native, name)}
                for name in app.cfg.get('native_services', []) if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name)]

    @r.route('POST', '/api/openwrt/services/{name}/{action}', tier=2)
    def service_action(req):
        app.require_tier(req, 2)
        name = identifier(req.params['name'])
        action = v.choice('action', req.params['action'], ['start', 'stop', 'restart', 'reload'])
        if name not in app.cfg.get('native_services', []) or not os.path.isfile('/etc/init.d/' + name):
            raise unavailable('This native service is not installed or not allowed')
        with native.lock:
            if action in blocked_service_actions(native, name):
                raise protected_error()
            native.command(['/etc/init.d/' + name, action])
        changed(req, 'openwrt.service.' + action, [name])
        return {'requested': True, 'name': name, 'action': action}

    @r.route('GET', '/api/openwrt/logs')
    def logs(req):
        return {'output': native.command(['logread', '-l', str(v.integer('lines', req.query.get('lines', 150), 1, 500))])}

    @r.route('GET', '/api/openwrt/modem')
    def modem_status(req):
        return redact(native.modem())

    def require_modem_capability(name):
        status = native.modem()
        if status.get('available') is not True or status.get('capabilities', {}).get(name) is not True:
            raise unavailable('The native modem service does not currently support ' + name)

    for action in ('connect', 'disconnect', 'reconnect'):
        def register_cell_action(action=action):
            @r.route('POST', '/api/cellular/' + action, tier=2)
            def run(req):
                app.require_tier(req, 2)
                require_modem_capability('connect')
                if action == 'reconnect':
                    native.modem('disconnect')
                    result = native.modem('connect')
                else:
                    result = native.modem(action)
                changed(req, 'openwrt.cellular.' + action, [])
                return {'requested': True, 'result': redact(result)}
        register_cell_action()

    @r.route('PUT', '/api/cellular/mode', tier=2)
    def airplane(req):
        app.require_tier(req, 2)
        data = v.only_keys(req.json, ['airplane'])
        enabled = v.boolean('airplane', data.get('airplane'))
        require_modem_capability('mode')
        result = native.modem('mode', 'offline' if enabled else 'online')
        changed(req, 'openwrt.cellular.mode', data)
        return {'airplane': enabled, 'requested': True, 'result': redact(result)}

    @r.route('GET', '/api/openwrt/sms')
    def get_sms(req):
        require_modem_capability('sms')
        return redact(native.modem('sms-list'))

    @r.route('POST', '/api/openwrt/sms', tier=2)
    def send_sms(req):
        app.require_tier(req, 2)
        data = v.only_keys(req.json, ['number', 'text'])
        number = v.text('number', data.get('number'), 3, 24, r'^\+?[0-9]+$')
        message = v.text('text', data.get('text'), 1, 160)
        require_modem_capability('sms')
        result = native.modem('sms-send', number, message)
        changed(req, 'openwrt.sms.send', [])
        return {'requested': True, 'result': redact(result)}

    @r.route('GET', '/api/openwrt/gnss')
    def gnss(req):
        return native.gnss_status()

    @r.route('GET', '/api/openwrt/hardware')
    def hardware(req):
        return native.hardware_status()

    @r.route('GET', '/api/openwrt')
    def overview(req):
        capabilities = dict(app.cache.get('native:capabilities', 10, native.capabilities))
        capabilities['gnss'] = native.gnss_capability()  # expiry is evaluated now, independent of capability cache
        capabilities.update(native.hardware_capabilities())  # custom hardware reports expire independently too
        return {'platform': 'openwrt', 'ram_only': True,
                'capabilities': redact(capabilities),
                'notes': ['Configuration is in the RAM filesystem and is lost at the next stock boot.',
                          'Kernel nodes and accepted configuration do not establish physical or radio functionality.']}

    @r.route('GET', '/api/status')
    def status(req):
        def gather():
            errors = {}
            modem = safe(lambda: app.cache.get('native:modem', 5, native.modem), {}, errors, 'cellular')
            runtime = safe(lambda: native.ubus('network.interface', 'dump'), None, errors, 'network')
            wireless = safe(lambda: native.ubus('network.wireless', 'status'), None, errors, 'wifi')
            wifi = safe(lambda: native.config('wireless'), {}, errors, 'wifi_config')
            signal = modem.get('signal', {}) if isinstance(modem, dict) else {}
            service = modem.get('service', {}) if isinstance(modem, dict) else {}
            sim = modem.get('sim', {}) if isinstance(modem, dict) else {}
            connection = modem.get('connection', {}) if isinstance(modem, dict) else {}
            if not isinstance(service, dict):
                service = {'status': service}
            if not isinstance(sim, dict):
                sim = {'status': sim}
            if not isinstance(signal, dict):
                signal = {}
            if not isinstance(connection, dict):
                connection = {'state': connection}
            if modem.get('airplane'):
                state = 'airplane'
            elif sim.get('status') in ('absent', 'not_present', 'no_sim'):
                state = 'no_sim'
            elif connection.get('state') == 'connected':
                state = 'connected'
            elif connection.get('state') == 'disconnected':
                state = 'disconnected'
            else:
                state = 'unknown' if modem.get('available') is True else 'unavailable'
            wan = next((x for x in (runtime or {}).get('interface', []) if x.get('interface') == app.cfg.get('cellular_interface', 'wan')), {})
            wan_state = 'Unavailable' if runtime is None else 'Connected' if wan.get('up') is True else 'Disconnected' if wan.get('up') is False else 'Unknown'
            radio_states = [x.get('up') for x in (wireless or {}).values()]
            wifi_enabled = True if True in radio_states else False if radio_states and all(x is False for x in radio_states) else None
            ip4 = wan.get('ipv4-address') or [{}]
            ip6 = wan.get('ipv6-address') or [{}]
            counters = native.system.net_dev().get(wan.get('l3_device'), {})
            power = native.system.power_supplies()
            battery = next((x for x in power if str(x.get('type', '')).lower() == 'battery'), {})
            leases_data = native.system.dhcp_leases()
            aps = [value for _, value in access_points(wifi)]
            mem = native.system.meminfo()
            uptime = metric_number(native.system.uptime())
            load = native.system.loadavg()
            for name, missing in (('uptime', uptime is None), ('memory', mem['total'] is None or mem['available'] is None),
                                  ('clients', leases_data is None), ('load', load is None)):
                if missing:
                    errors[name] = {'code': 'telemetry_unavailable', 'message': name.capitalize() + ' telemetry source is unavailable'}
            return {'time': int(time.time()), 'uptime_s': int(uptime) if uptime is not None else None,
                    'connection': {'state': state, 'tech': service.get('tech', signal.get('type', 'none')),
                                   'tech_raw': service.get('tech', ''), 'nr_mode': service.get('nr_mode', 'none'),
                                   'operator': service.get('operator', ''), 'roaming': service.get('roaming', False),
                                   'service': service.get('status', 'unknown'), 'data_enabled': modem.get('data_enabled')},
                    'signal': dict({'bars': None, 'rssi': None, 'rsrp': None, 'rsrq': None, 'sinr': None,
                                    'band': '', 'bandwidth_mhz': None, 'pci': None, 'cell_id': ''}, **signal),
                    'nr': modem.get('nr', {}),
                    'wan': {'status': wan_state,
                            'ipv4': ip4[0].get('address', ''), 'ipv6': ip6[0].get('address', ''),
                            'gateway': next((x.get('nexthop', '') for x in wan.get('route', []) if x.get('target') == '0.0.0.0'), ''),
                            'dns': wan.get('dns-server', []), 'duration_s': metric_number(wan.get('uptime')),
                            'rx_bytes': metric_number(counters.get('rx_bytes')), 'tx_bytes': metric_number(counters.get('tx_bytes'))},
                    'sim': {'status': sim.get('status', 'Unknown')},
                    'battery': {'present': battery.get('present') == 1 if battery else None,
                                'percent': battery.get('capacity'), 'charging': battery.get('status') == 'Charging',
                                'source': next((x['name'] for x in power if x.get('online') == 1), '')},
                    'clients': {'total': len(leases_data) if leases_data is not None else None, 'wifi': None, 'usb': None, 'ethernet': None},
                    'wifi': {'enabled': wifi_enabled,
                             'primary_ssid': aps[0].get('ssid', '') if aps else '', 'guest_ssid': '',
                             'bands': {'2g': '', '5g': ''}},
                    'sms_unread': modem.get('sms_unread'), 'temps': native.system.thermal(),
                    'memory': {'total': mem['total'], 'available': mem['available']},
                    'load': load, 'errors': errors, 'platform': 'openwrt'}
        return app.cache.get('status', 2.5, gather)

    @r.route('GET', '/api/tools/audit')
    def audit(req):
        limit = v.integer('limit', req.query.get('limit', 300), 1, 2000)
        query = str(req.query.get('q', '')).strip().lower()
        entries = list(reversed(app.state.audit_tail(2000 if query else limit)))
        if query:
            entries = [x for x in entries if query in json.dumps(x).lower()][:limit]
        return {'entries': entries, 'count': len(entries), 'limit': limit}

    @r.route('GET', '/api/tools/audit/export')
    def export_audit(req):
        from .httpd import Response
        import csv
        import io
        entries = app.state.audit_tail(5000)
        if req.query.get('format', 'csv') == 'json':
            return Response(json.dumps(entries).encode(), 'application/json', headers={'Content-Disposition': 'attachment; filename="audit-log.json"'})
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['time', 'actor', 'action', 'details'])
        for entry in entries:
            row = [str(entry.get(key, '')) for key in ('t', 'actor', 'action', 'details')]
            writer.writerow(["'" + value if value.startswith(('=', '+', '-', '@')) else value for value in row])
        return Response(output.getvalue().encode(), 'text/csv', headers={'Content-Disposition': 'attachment; filename="audit-log.csv"'})
