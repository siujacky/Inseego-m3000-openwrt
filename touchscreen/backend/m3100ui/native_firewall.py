"""Bounded firewall4 configuration, with no caller-controlled command surface.

Local rpcd UCI calls omit ubus_rpc_session, so staged deltas and fw4's default
UCI cursor share /tmp/.uci. Refuse pre-existing deltas before owning a change.
The process lock excludes this server's writers, not external administrators.
"""
import copy
import fnmatch
import ipaddress
import os
import re

from . import validate as v
from .errors import ApiError, field_error, unavailable

POLICIES = ('ACCEPT', 'DROP', 'REJECT')
FAMILIES = ('any', 'ipv4', 'ipv6')
KINDS = {'zones': 'zone', 'forwardings': 'forwarding', 'rules': 'rule', 'redirects': 'redirect'}
BOOLS = {'enabled', 'masq', 'masq6', 'mtu_fix', 'reflection', 'synflood_protect', 'drop_invalid'}
FIELDS = {
    'defaults': {'input', 'output', 'forward', 'synflood_protect', 'drop_invalid'},
    'zone': {'network', 'input', 'output', 'forward', 'enabled', 'masq', 'masq6', 'mtu_fix', 'family'},
    'forwarding': {'name', 'src', 'dest', 'enabled', 'family'},
    'rule': {'name', 'src', 'dest', 'enabled', 'family', 'proto', 'src_ip', 'dest_ip', 'src_mac', 'src_port', 'dest_port', 'target'},
    'redirect': {'name', 'src', 'dest', 'enabled', 'family', 'proto', 'src_ip', 'src_dip', 'src_mac', 'src_dport', 'dest_ip', 'dest_port', 'reflection', 'target'}}
OPTIONAL = {'src_ip', 'dest_ip', 'src_dip', 'src_mac', 'src_port', 'dest_port', 'dest'}


def ident(field, value):
    return v.text(field, value, 1, 64, r'^[A-Za-z0-9_]+$')


def protection(native):
    """ROOT-owned policy; HTTP cannot alter or disable these restrictions."""
    p = native.cfg.get('native_firewall_management', {
        'network': 'usbmgmt', 'zone': 'usbmgmt', 'device': 'usb0', 'ipv4_cidr': '192.168.77.0/24'})
    if not isinstance(p, dict) or set(p) != {'network', 'zone', 'device', 'ipv4_cidr'}:
        raise unavailable('ROOT must provision a valid native firewall management policy')
    try:
        if not all(isinstance(p[k], str) and re.fullmatch(r'[A-Za-z0-9_]{1,32}', p[k]) for k in ('network', 'zone')):
            raise ValueError()
        if not isinstance(p['device'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,32}', p['device']):
            raise ValueError()
        if ipaddress.ip_network(p['ipv4_cidr'], strict=True).version != 4:
            raise ValueError()
    except (ValueError, TypeError):
        raise unavailable('ROOT must provision a valid native firewall management policy')
    return dict(p)


def protected_error():
    return ApiError(403, 'management_protected', 'The USB management network, zone and fixed ACCEPT/ACCEPT/REJECT policy are protected')


def as_list(value):
    return value if isinstance(value, list) else str(value).split() if value else []


def ipv4_interfaces(interface):
    """netifd accepts scalar/list ipaddr, with inline prefixes or netmask."""
    addresses = []
    mask = str(interface.get('netmask', '32'))
    for value in as_list(interface.get('ipaddr')):
        if not isinstance(value, str):
            continue
        try:
            addresses.append(ipaddress.IPv4Interface(value if '/' in value else value + '/' + mask))
        except ValueError:
            continue
    return addresses


def uses_management_device(native, networks, device):
    """Resolve netifd @interface alias chains; unresolved/cyclic aliases fail closed."""
    p = protection(native)
    pending = list(as_list(device))
    visited = set()
    while pending:
        value = pending.pop()
        if value == p['device']:
            return True
        if not isinstance(value, str) or not value.startswith('@'):
            continue
        name = value[1:]
        if name == p['network'] or name in visited or name not in networks:
            return True
        visited.add(name)
        pending.extend(as_list(networks[name].get('device')))
    return False


def overlaps_management(native, value):
    try:
        net = ipaddress.ip_network(value, strict=False)
        protected = ipaddress.ip_network(protection(native)['ipv4_cidr'])
        return net.version == protected.version and net.overlaps(protected)
    except (ValueError, TypeError):
        return False


def guard_network(native, name, changes=None):
    """Also guard the existing netifd editor from moving the USB rescue path."""
    p = protection(native)
    if name == p['network']:
        raise protected_error()
    changes = changes or {}
    networks = native.config('network')
    current = networks.get(name, {})
    candidate = dict(current, **changes)
    if (uses_management_device(native, networks, current.get('device'))
            or uses_management_device(native, networks, candidate.get('device'))):
        raise protected_error()
    if any(overlaps_management(native, str(address.network)) for address in ipv4_interfaces(candidate)):
        raise protected_error()
    if changes.get('gateway') and overlaps_management(native, changes['gateway']):
        raise protected_error()


def blocked_service_actions(native, name):
    actions = ['stop'] if name == 'firewall' else ['stop', 'restart'] if name == 'network' else []
    if not actions:
        return []
    p = protection(native)
    if (p['network'] in native.config('network') or any(
            item.get('.type') == 'zone' and zone_is_protected(native, item) for item in native.config('firewall').values())):
        return actions
    return []


def runtime(native):
    required = ['/sbin/fw4', '/usr/sbin/nft', '/etc/init.d/firewall']
    missing = [p for p in required if not os.path.isfile(p) or not os.access(p, os.X_OK)]
    if missing:
        return {'available': False, 'loaded': False, 'reason': 'Required firewall4 programs or init service are unavailable', 'missing': missing}
    if 'firewall' not in native.cfg.get('native_services', []):
        return {'available': False, 'loaded': False, 'reason': 'ROOT has not enabled the native firewall service'}
    try:
        data = native.command(['/usr/sbin/nft', '-j', 'list', 'table', 'inet', 'fw4'])
        loaded = isinstance(data, dict) and isinstance(data.get('nftables'), list) and any(
            isinstance(x, dict) and isinstance(x.get('table'), dict) and x['table'].get('family') == 'inet'
            and x['table'].get('name') == 'fw4' for x in data['nftables'])
        return {'available': loaded, 'loaded': loaded,
                'reason': 'inet fw4 table detected; individual traffic behavior is unverified' if loaded else 'No inet fw4 table was returned'}
    except ApiError as e:
        return {'available': False, 'loaded': False, 'reason': 'Firewall kernel table could not be read', 'code': e.code}


def snapshot(native):
    return {name: {k: val for k, val in item.items() if not k.startswith('.') or k == '.type'}
            for name, item in native.config('firewall').items()}


def zone_is_protected(native, item):
    p = protection(native)
    return item.get('name') == p['zone'] or p['network'] in as_list(item.get('network'))


def management_problem(native, config):
    p = protection(native)
    zones = [x for x in config.values() if x.get('.type') == 'zone' and zone_is_protected(native, x)]
    if len(zones) != 1:
        return 'USB management must belong to exactly one protected zone'
    zone = zones[0]
    if (zone.get('name') != p['zone'] or as_list(zone.get('network')) != [p['network']]
            or any(zone.get(k) != val for k, val in [('input', 'ACCEPT'), ('output', 'ACCEPT'), ('forward', 'REJECT')])
            or zone.get('enabled', '1') != '1' or zone.get('family', 'any') not in ('any', '')
            or any(k in zone for k in ('device', 'subnet', 'masq_src', 'masq_dest'))
            or zone.get('masq', '0') != '0' or zone.get('masq6', '0') != '0'):
        return 'USB management zone differs from its fixed policy; ROOT must repair it'
    netifd = native.config('network')
    net = netifd.get(p['network'], {})
    addresses = ipv4_interfaces(net)
    if (net.get('.type') != 'interface' or net.get('proto') != 'static' or net.get('device') != p['device']
            or net.get('disabled', '0') != '0' or len(addresses) != 1 or str(addresses[0].network) != p['ipv4_cidr']):
        return 'USB management netifd configuration differs from the protected network'
    for item in config.values():
        if item.get('.type') != 'zone' or zone_is_protected(native, item) or item.get('enabled', '1') == '0':
            continue
        if any(fnmatch.fnmatchcase(p['device'], str(device).replace('+', '*')) for device in as_list(item.get('device'))):
            return 'Another zone captures the protected USB management device; ROOT must repair it'
        if any(overlaps_management(native, subnet) for subnet in as_list(item.get('subnet'))):
            return 'Another zone captures the protected USB subnet; ROOT must repair it'
        for name in as_list(item.get('network')):
            interface = netifd.get(name, {})
            if uses_management_device(native, netifd, interface.get('device')):
                return 'Another network aliases the protected USB management device; ROOT must repair it'
            if any(overlaps_management(native, str(address.network)) for address in ipv4_interfaces(interface)):
                return 'Another network overlaps the protected USB subnet; ROOT must repair it'
    return None


def port_value(field, value):
    value = v.text(field, value, 1, 11, r'^\d{1,5}(?:-\d{1,5})?$')
    parts = [int(x) for x in value.split('-')]
    if not all(1 <= x <= 65535 for x in parts) or parts[-1] < parts[0]:
        raise field_error(field, 'Use a port from 1 to 65535 or an ascending range')
    return str(parts[0]) if len(parts) == 1 or parts[0] == parts[1] else '%d-%d' % tuple(parts)


def address_value(native, field, value, family, host=False):
    value = v.text(field, value, 1, 64)
    try:
        parsed = ipaddress.ip_address(value) if host else ipaddress.ip_network(value, strict=False)
        addr = parsed if host else parsed.network_address
        version = parsed.version
        if family != 'any' and family != 'ipv%d' % version:
            raise ValueError()
        if addr.is_multicast or addr.is_loopback or addr.is_unspecified or addr.is_link_local:
            raise ValueError()
        if host and (addr.is_reserved or str(addr) == '255.255.255.255'):
            raise ValueError()
        if overlaps_management(native, value):
            raise ValueError()
        return str(parsed)
    except ValueError:
        raise field_error(field, 'Use a unicast IP%s matching the family and outside the protected USB subnet' % ('' if host else ' or CIDR'))


def redirect_destination(native, values, zone):
    """Keep DNAT destinations inside an observed/configured destination subnet."""
    target = ipaddress.ip_address(values['dest_ip'])
    networks = native.config('network')
    names = as_list(zone.get('network'))
    local = []
    for name in names:
        interface = networks.get(name, {})
        local.extend(ipv4_interfaces(interface))
        for value in as_list(interface.get('ip6addr')):
            try:
                local.append(ipaddress.ip_interface(value))
            except (ValueError, TypeError):
                continue
    if not any(item.version == target.version and target in item.network for item in local):
        dump = native.ubus('network.interface', 'dump')
        if isinstance(dump, dict) and isinstance(dump.get('interface'), list):
            for interface in dump['interface']:
                if not isinstance(interface, dict) or interface.get('interface') not in names:
                    continue
                for key in ('ipv4-address', 'ipv6-address'):
                    for item in interface.get(key, []) if isinstance(interface.get(key, []), list) else []:
                        try:
                            local.append(ipaddress.ip_interface(item['address'] + '/' + str(item['mask'])))
                        except (ValueError, TypeError, KeyError):
                            continue
    matching = [item for item in local if item.version == target.version and target in item.network]
    if (not matching or any(target == item.ip for item in local if item.version == target.version)
            or all(target == item.network.network_address or (target.version == 4 and target == item.network.broadcast_address) for item in matching)):
        raise field_error('dest_ip', 'Use a client address in a configured or observed destination-zone subnet, excluding router, network and broadcast addresses')


def normalize(native, kind, data, current, config):
    allowed = FIELDS[kind]
    # Auth confirmation fields are consumed by the shared tier handler.
    clean = v.only_keys(data, allowed)
    values = {k: val for k, val in current.items() if not k.startswith('.')}
    values.update(clean)
    if kind == 'defaults':
        if 'synflood_protect' in clean:
            values.pop('syn_flood', None)
        else:
            values.pop('syn_flood', None)  # legacy alias is preserved on disk, outside this typed projection
    for key in BOOLS & allowed:
        if key in values:
            values[key] = '1' if v.boolean(key, values[key]) else '0'
    if 'family' in allowed:
        values['family'] = v.choice('family', values.get('family', 'any'), FAMILIES)
    for key in ('input', 'output', 'forward'):
        if key in allowed and key in values:
            values[key] = v.choice(key, values[key], POLICIES)
    if kind == 'zone':
        values.pop('name', None)  # zone name is immutable and not an HTTP field
        networks = values.get('network', [])
        if not isinstance(networks, list) or not networks or len(networks) > 16:
            raise field_error('network', 'Use a list of one to sixteen existing netifd networks')
        if len(networks) != len(set(str(x) for x in networks)):
            raise field_error('network', 'Duplicate networks are not allowed')
        p = protection(native)
        if p['network'] in networks:
            raise protected_error()
        netifd = native.config('network')
        for net in networks:
            ident('network', net)
            if netifd.get(net, {}).get('.type') != 'interface':
                raise field_error('network', 'Unknown netifd interface: ' + net)
            if uses_management_device(native, netifd, netifd[net].get('device')):
                raise protected_error()
        return values
    if kind == 'defaults':
        return values
    values['name'] = v.text('name', values.get('name', ''), 0, 64)
    values['enabled'] = values.get('enabled', '1')
    zones = {x.get('name'): x for x in config.values() if x.get('.type') == 'zone'}
    for key in ('src', 'dest'):
        val = values.get(key, '')
        if not val and kind == 'rule' and key == 'dest':
            values.pop(key, None)
            continue
        ident(key, val)
        if val == protection(native)['zone']:
            raise protected_error()
        if val not in zones:
            raise field_error(key, 'Choose an existing zone')
        if zones[val].get('enabled', '1') != '1':
            raise field_error(key, 'Choose an enabled zone')
        if zone_is_protected(native, zones[val]):
            raise protected_error()
        zone_family = zones[val].get('family', 'any') or 'any'
        if zone_family != 'any' and values['family'] != zone_family:
            raise field_error('family', 'The selected family must match both zones')
    if values.get('src') == values.get('dest'):
        raise field_error('dest', 'Choose a different destination zone')
    if kind == 'forwarding':
        return values
    protocols = values.get('proto', ['tcp', 'udp'])
    if isinstance(protocols, str):
        protocols = protocols.split()
    options = ('tcp', 'udp') if kind == 'redirect' else ('tcp', 'udp', 'icmp', 'ipv6-icmp', 'all')
    if (not isinstance(protocols, list) or not protocols or len(protocols) > 2
            or not all(isinstance(x, str) and x in options for x in protocols) or len(set(protocols)) != len(protocols)
            or (len(protocols) > 1 and set(protocols) != {'tcp', 'udp'})):
        raise field_error('proto', 'Use TCP, UDP or TCP and UDP; traffic rules may also use ICMP, IPv6 ICMP or all')
    values['proto'] = [x for x in options if x in protocols]
    family = values['family']
    if ('ipv6-icmp' in protocols and family != 'ipv6') or ('icmp' in protocols and family == 'any'):
        raise field_error('family', 'ICMP rules require an explicit matching IP family')
    for key in ('src_port', 'dest_port', 'src_dport'):
        if key not in allowed:
            continue
        if values.get(key) in ('', None):
            values.pop(key, None)
        elif not set(protocols) <= {'tcp', 'udp'}:
            raise field_error(key, 'Ports require only TCP and/or UDP')
        else:
            values[key] = port_value(key, values[key])
    for key in ('src_ip', 'dest_ip', 'src_dip'):
        if key in allowed:
            if values.get(key) in ('', None):
                values.pop(key, None)
            else:
                values[key] = address_value(native, key, values[key], family, host=kind == 'redirect' and key != 'src_ip')
    if values.get('src_mac'):
        values['src_mac'] = v.mac('src_mac', values['src_mac'])
        first = int(values['src_mac'][:2], 16)
        if first & 1 or values['src_mac'] == '00:00:00:00:00:00':
            raise field_error('src_mac', 'Use a unicast source MAC address')
    else:
        values.pop('src_mac', None)
    if kind == 'rule':
        values['target'] = v.choice('target', values.get('target'), POLICIES)
        ips = [ipaddress.ip_network(values[k]).version for k in ('src_ip', 'dest_ip') if k in values]
        if len(set(ips)) > 1:
            raise field_error('family', 'Source and destination addresses must use the same family')
    else:
        if family == 'any':
            raise field_error('family', 'Port redirects require an explicit IPv4 or IPv6 family')
        values['target'] = v.choice('target', values.get('target', 'DNAT'), ['DNAT'])
        for key in ('src_dport', 'dest_ip', 'dest_port'):
            if not values.get(key):
                raise field_error(key, 'Required for a port redirect')
        redirect_destination(native, values, zones[values['dest']])
        def width(port):
            ends = [int(x) for x in port.split('-')]
            return ends[-1] - ends[0]
        if width(values['src_dport']) != width(values['dest_port']):
            raise field_error('dest_port', 'External and internal port ranges must have the same size')
    return values


def supported(native, item, config):
    kind = item.get('.type')
    options = {k: val for k, val in item.items() if not k.startswith('.')}
    if kind == 'zone' and zone_is_protected(native, item):
        return False, 'USB management uses a fixed policy and cannot be edited'
    extra = set(options) - FIELDS.get(kind, set()) - ({'name'} if kind == 'zone' else {'syn_flood'} if kind == 'defaults' else set())
    if extra:
        return False, 'Contains options outside this editor: ' + ', '.join(sorted(extra))
    try:
        normalize(native, kind, {}, item, config)
    except ApiError as e:
        return False, e.message
    return True, None


def view(native, config=None, state=None):
    from .native import redact
    config = snapshot(native) if config is None else config
    result = {key: [] for key in KINDS}
    result['defaults'] = None
    result['other_sections'] = []
    for sid, item in config.items():
        kind = item.get('.type')
        if kind not in FIELDS:
            result['other_sections'].append({'section': sid, 'type': kind})
            continue
        editable, reason = supported(native, item, config)
        options = redact({k: val for k, val in item.items() if not k.startswith('.')})
        for key in BOOLS & set(options):
            options[key] = options[key] == '1'
        if 'network' in options:
            options['network'] = as_list(options['network'])
        if 'proto' in options:
            options['proto'] = as_list(options['proto'])
        if kind != 'defaults':
            options.setdefault('family', 'any')
            options.setdefault('enabled', True)
        row = {'section': sid, 'options': options, 'editable': editable, 'reason': reason}
        row['removable'] = kind in ('rule', 'redirect', 'forwarding') and not (
            any(item.get(k) in ('*', protection(native)['zone']) for k in ('src', 'dest')) or any(
                overlaps_management(native, x) for k in ('src_ip', 'dest_ip', 'src_dip') for x in as_list(item.get(k))))
        if kind == 'defaults':
            if result['defaults'] is None:
                result['defaults'] = row
            else:
                result['defaults']['editable'] = False
                result['defaults']['reason'] = 'Multiple defaults sections require ROOT repair'
        else:
            result[next(k for k, typ in KINDS.items() if typ == kind)].append(row)
    p = protection(native)
    result['protection'] = {'networks': [p['network']], 'zone': p['zone'], 'device': p['device'], 'ipv4_cidr': p['ipv4_cidr'],
        'reason': 'USB management is fixed: input ACCEPT, output ACCEPT, forward REJECT. Wildcard zones and USB subnet matches are excluded.',
        'problem': management_problem(native, config)}
    result['runtime'] = runtime(native) if state is None else state
    result['networks'] = [name for name, entry in native.config('network').items()
                          if entry.get('.type') == 'interface' and name != p['network']]
    return result


def check_conflicts(native, config, sid, item):
    kind = item['.type']
    opts = {k: val for k, val in item.items() if not k.startswith('.')}
    if kind == 'zone':
        for ref in config.values():
            if (ref.get('.type') in ('forwarding', 'rule', 'redirect') and ref.get('enabled', '1') != '0'
                    and item['name'] in (ref.get('src'), ref.get('dest'))):
                if opts.get('enabled', '1') == '0':
                    raise ApiError(409, 'conflict', 'Disable or remove active references before disabling their zone')
                if opts.get('family', 'any') != 'any' and ref.get('family', 'any') != opts['family']:
                    raise ApiError(409, 'conflict', 'Update active references to a matching family before restricting their zone')
    for other_sid, other in config.items():
        if sid == other_sid or other.get('.type') != kind:
            continue
        if kind == 'zone':
            if set(as_list(other.get('network'))) & set(opts['network']):
                raise ApiError(409, 'conflict', 'A network already belongs to another firewall zone')
            continue
        if kind not in ('forwarding', 'rule', 'redirect'):
            continue
        try:
            old = normalize(native, kind, {}, other, config)
        except ApiError:
            if kind == 'redirect' and other.get('enabled', '1') != '0' and other.get('src') in (None, '', '*', opts.get('src')):
                raise ApiError(409, 'conflict', 'An existing redirect outside this editor uses the source zone; ROOT must review overlap')
            continue
        if opts.get('name') and opts.get('name') == old.get('name'):
            raise ApiError(409, 'conflict', 'A firewall section of this type already uses this label')
        family_overlap = opts.get('family', 'any') == 'any' or old.get('family', 'any') == 'any' or opts.get('family') == old.get('family')
        if kind == 'redirect' and opts.get('enabled', '1') != '0' and old.get('enabled', '1') != '0':
            def interval(value):
                ports = [int(x) for x in value.split('-')]
                return ports[0], ports[-1]
            a, b = interval(opts['src_dport']), interval(old['src_dport'])
            if (opts['src'] == old['src'] and family_overlap and set(opts['proto']) & set(old['proto'])
                    and max(a[0], b[0]) <= min(a[1], b[1])):
                raise ApiError(409, 'conflict', 'An enabled redirect already overlaps this source zone, protocol, family and external port range')
        elif kind == 'forwarding' and family_overlap and opts['src'] == old['src'] and opts['dest'] == old['dest']:
            raise ApiError(409, 'conflict', 'This zone forwarding already exists')
        elif kind == 'rule':
            ignore = {'name', 'enabled', 'target'}
            if {k: val for k, val in opts.items() if k not in ignore} == {k: val for k, val in old.items() if k not in ignore}:
                raise ApiError(409, 'conflict', 'A traffic rule already uses this match; edit that rule instead')


def transact(native, original, desired, sid, kind, values, operation):
    pending = native.ubus('uci', 'changes', {'config': 'firewall'})
    if not isinstance(pending, dict) or not isinstance(pending.get('changes'), list):
        raise ApiError(502, 'native_bad_response', 'UCI returned invalid pending changes')
    if pending['changes']:
        raise ApiError(409, 'conflict', 'Existing staged firewall changes require ROOT review; nothing was committed or reverted')
    committed, committing = False, False
    readback = None
    try:
        if operation == 'create':
            native.ubus('uci', 'add', {'config': 'firewall', 'type': kind, 'name': sid, 'values': values})
        elif operation == 'delete':
            native.ubus('uci', 'delete', {'config': 'firewall', 'section': sid})
        else:
            old = original[sid]
            removed = [key for key in old if not key.startswith('.') and key not in desired[sid]]
            for key in removed:
                native.ubus('uci', 'delete', {'config': 'firewall', 'section': sid, 'option': key})
            updated = {key: val for key, val in values.items() if old.get(key) != val}
            if updated:
                native.ubus('uci', 'set', {'config': 'firewall', 'section': sid, 'values': updated})
        if snapshot(native) != desired:
            raise ApiError(502, 'native_readback_mismatch', 'Staged firewall configuration did not match the requested change')
        native.command(['/sbin/fw4', 'check'])
        committing = True
        native.ubus('uci', 'commit', {'config': 'firewall'})
        committed = True
        persisted = snapshot(native)
        readback = view(native, persisted)
        if persisted != desired:
            raise ApiError(502, 'native_readback_mismatch', 'Committed firewall readback differs; reload was not requested')
    except ApiError as error:
        reverted = False
        saved = True if committed else False if not committing else None
        if not committed:
            try:
                native.ubus('uci', 'revert', {'config': 'firewall'})
                reverted = True
                after = snapshot(native)
                if committing:
                    saved = True if after == desired else False if after == original else None
                readback = view(native, after)
            except ApiError:
                pass
        raise ApiError(502, error.code, 'Firewall change failed before reload; inspect saved state and readback',
            {'saved': saved, 'applied': None if committing and saved is not False else False,
             'reload_requested': False, 'staging_reverted': reverted, 'readback': readback, 'cause': error.code})
    try:
        native.command(['/etc/init.d/firewall', 'reload'])
    except ApiError as error:
        state = runtime(native)
        readback['runtime'] = state
        raise ApiError(502, 'native_apply_failed', 'Firewall configuration is saved; reload could not be confirmed. Runtime may differ; no runtime rollback was attempted.',
            {'saved': True, 'applied': None, 'reload_requested': True, 'reload_command_failed': True,
             'readback': readback, 'cause': error.code, 'runtime': state})
    state = runtime(native)
    readback['runtime'] = state
    if not state['available']:
        raise ApiError(502, 'native_apply_unverified', 'Configuration is saved and reload returned success, but the firewall table could not be verified',
            {'saved': True, 'applied': None, 'reload_succeeded': True, 'readback': readback, 'runtime': state})
    return {'saved': True, 'applied': True, 'reload_succeeded': True, 'traffic_verified': False, 'readback': readback, 'runtime': state}


def register(app):
    native = app.adapters.native

    @app.route('GET', '/api/openwrt/firewall')
    def get_firewall(req):
        with native.lock:
            return view(native)

    def mutate(req, kind, operation):
        app.require_tier(req, 2)
        with native.lock:
            original = snapshot(native)
            problem = management_problem(native, original)
            if problem:
                raise unavailable(problem)
            if not runtime(native)['available']:
                raise unavailable('A working firewall4 inet table and installed init service are required before editing')
            sid = req.params.get('section')
            if kind == 'defaults':
                ids = [key for key, item in original.items() if item.get('.type') == kind]
                if len(ids) != 1:
                    raise unavailable('Exactly one defaults section is required')
                sid = ids[0]
            elif operation == 'create':
                sid = req.json.get('section')
            ident('section', sid)
            current = original.get(sid, {})
            if operation == 'create' and current:
                raise ApiError(409, 'conflict', 'A UCI section already has this identifier')
            if operation != 'create' and current.get('.type') != kind:
                raise ApiError(404, 'not_found', 'No such firewall configuration section')
            if kind == 'zone' and zone_is_protected(native, current):
                raise protected_error()
            if operation != 'create' and kind in ('rule', 'redirect', 'forwarding'):
                p = protection(native)
                if any(current.get(k) in ('*', p['zone']) for k in ('src', 'dest')) or any(
                        overlaps_management(native, x) for k in ('src_ip', 'dest_ip', 'src_dip') for x in as_list(current.get(k))):
                    raise protected_error()
            data = dict(req.json)
            if operation == 'create':
                data.pop('section', None)
            desired = copy.deepcopy(original)
            if operation == 'delete':
                v.only_keys(data, [])
                desired.pop(sid)
                values = {}
            else:
                if operation != 'create':
                    editable, reason = supported(native, current, original)
                    if not editable:
                        raise field_error('section', reason)
                values = normalize(native, kind, data, current, original)
                desired[sid] = dict(values, **{'.type': kind})
                if kind == 'zone':
                    desired[sid]['name'] = current['name']
                if kind == 'defaults' and 'synflood_protect' not in data and 'syn_flood' in current:
                    desired[sid]['syn_flood'] = current['syn_flood']
                check_conflicts(native, desired, sid, desired[sid])
            problem = management_problem(native, desired)
            if problem:
                raise ApiError(403, 'management_protected',
                    'The proposed firewall configuration would invalidate USB management: ' + problem)
            try:
                result = transact(native, original, desired, sid, kind, values, operation)
            except ApiError as error:
                app.cache.invalidate()
                app.audit(req, 'openwrt.firewall.' + operation, {'section': sid, 'type': kind, 'keys': sorted(data),
                    'code': error.code, 'saved': (error.details or {}).get('saved'), 'applied': (error.details or {}).get('applied')})
                raise
            app.cache.invalidate()
            app.audit(req, 'openwrt.firewall.' + operation, {'section': sid, 'type': kind, 'keys': sorted(data), 'saved': True, 'applied': True})
            return result

    app.route('PUT', '/api/openwrt/firewall/defaults', tier=2)(lambda req: mutate(req, 'defaults', 'update'))
    app.route('PUT', '/api/openwrt/firewall/zones/{section}', tier=2)(lambda req: mutate(req, 'zone', 'update'))
    for collection, kind in KINDS.items():
        if kind == 'zone':
            continue
        app.route('POST', '/api/openwrt/firewall/' + collection, tier=2)(lambda req, typ=kind: mutate(req, typ, 'create'))
        app.route('PUT', '/api/openwrt/firewall/' + collection + '/{section}', tier=2)(lambda req, typ=kind: mutate(req, typ, 'update'))
        app.route('DELETE', '/api/openwrt/firewall/' + collection + '/{section}', tier=2)(lambda req, typ=kind: mutate(req, typ, 'delete'))
