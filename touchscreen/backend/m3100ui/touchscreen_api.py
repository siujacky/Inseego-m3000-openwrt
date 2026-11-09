"""Authenticated touchscreen preferences/capability surface; no hardware guesses."""
from . import validate as v
from .errors import unavailable, field_error

DEFAULTS = {'language': 'en', 'theme': 'system', 'display_timeout_s': 120}
UNAVAILABLE = {
    'charging': 'No verified native USB source/charging policy provider exists.',
    'audio': 'No verified native audio output or alert provider exists.',
    'reset': 'Persistent factory reset is outside this RAM-only backend.',
    'update': 'No validated image, rollback and native firmware update provider exists.',
    'power': 'No validated native shutdown/restart provider is exposed by this RAM frontend.',
    'sim_unlock': 'No typed native SIM PIN/PUK/restriction provider exists; modem is currently offline after secure IPA rejection.',
    'sleep': 'Display inactivity is software-only; no validated native suspend/stealth-power provider exists.',
    'battery_preservation': 'No validated battery charge threshold/preservation provider exists.',
    'wps': 'Native API can configure WPS eligibility, but has no verified push-button session/status provider.',
    'file_sharing': 'File sharing is disabled in the inspected stock menu configuration.',
    'speed_test': 'Speed test is disabled in the inspected stock menu configuration.',
    'activation': 'No carrier activation/profile-update provider exists in the native backend.',
    'tether': 'USB management is fixed and protected; no typed safe USB tether-mode provider exists.',
    'roaming': 'No typed native roaming policy provider exists; modem is currently offline after secure IPA rejection.',
}


def register(app):
    @app.router.route('GET', '/api/touchscreen/wifi/{name}/secret')
    def ap_secret(req):
        from .native import section
        native = app.adapters.native
        with native.lock:
            entry = section(native, 'wireless', req.params['name'], 'wifi-iface')
            if entry.get('mode', 'ap') != 'ap': raise unavailable('Credentials may only be revealed for a configured access point.')
            result = {'ssid': entry.get('ssid', ''), 'password': entry.get('key', '')}
        app.audit(req, 'touchscreen.wifi.secret.read', {'ap': req.params['name']})
        return result

    def blocked_state(selected=None):
        from .native import access_points, identifier
        native = app.adapters.native
        config = native.config('wireless')
        aps = access_points(config)
        if selected is not None:
            identifier(selected)
            aps = [pair for pair in aps if pair[0] == selected]
        aps = [(name, entry) for name, entry in aps if entry.get('disabled', '0') != '1'
               and config.get(entry.get('device'), {}).get('.type') == 'wifi-device'
               and config.get(entry.get('device'), {}).get('disabled', '0') != '1']
        if not aps: raise unavailable('No selected enabled native access point/radio exists; station and disabled interfaces cannot be blocked.')
        name, current = aps[0]
        mode = current.get('macfilter', '')
        if mode not in ('', 'disable', 'deny'):
            raise unavailable('Selected AP uses an allow-list or unknown MAC policy. Refusing to replace its access policy.')
        original = current.get('maclist', [])
        if isinstance(original, str): original = original.split()
        if not isinstance(original, list) or len(original) > 128: raise unavailable('Existing AP MAC list is not a bounded editable list.')
        normalized = []
        for item in original:
            try: mac = v.mac('mac', item).lower()
            except Exception: raise unavailable('Existing AP MAC list contains invalid entries; refusing to change it.') from None
            if int(mac[:2], 16) & 1 or mac == '00:00:00:00:00:00': raise unavailable('Existing AP MAC list contains a non-unicast entry; refusing to change it.')
            normalized.append(mac)
        # Inactive saved allow/deny entries must never become silently active.
        if mode != 'deny' and normalized: raise unavailable('Selected AP has an inactive saved MAC list. Refusing to activate it implicitly.')
        return {'ap': name, 'policy': mode or 'disabled', 'macs': normalized, 'available': True,
                'aps': [n for n, e in access_points(config) if e.get('disabled', '0') != '1' and config.get(e.get('device'), {}).get('.type') == 'wifi-device' and config.get(e.get('device'), {}).get('disabled', '0') != '1']}

    @app.router.route('GET', '/api/touchscreen/blocked')
    def get_blocked(req):
        with app.adapters.native.lock:
            return blocked_state(getattr(req, 'query', {}).get('ap'))

    @app.router.route('PUT', '/api/touchscreen/blocked', tier=2)
    def put_blocked(req):
        app.require_tier(req, 2)
        data = v.only_keys(req.json, ['ap', 'mac', 'blocked'])
        mac = v.mac('mac', data.get('mac')).lower()
        if int(mac[:2], 16) & 1 or mac == '00:00:00:00:00:00': raise field_error('mac', 'Use a unicast device MAC address')
        blocked = v.boolean('blocked', data.get('blocked'))
        native = app.adapters.native
        with native.lock:
            previous = blocked_state(data.get('ap'))
            values = list(previous['macs'])
            if blocked and mac not in values:
                if len(values) >= 128: raise field_error('mac', 'AP deny list is full')
                values.append(mac)
            elif not blocked: values = [entry for entry in values if entry != mac]
            if values != previous['macs']:
                from .errors import ApiError
                pending = native.ubus('uci', 'changes', {'config': 'wireless'})
                if not isinstance(pending, dict) or not isinstance(pending.get('changes'), list):
                    raise unavailable('Cannot verify that wireless configuration has no staged changes; refusing to commit.')
                if pending['changes']:
                    raise ApiError(409, 'conflict', 'Existing staged wireless changes require review; nothing was committed or reverted.')
                native.configure('wireless', [(previous['ap'], {'macfilter': 'deny', 'maclist': values})], ['wifi', 'reload'])
                app.cache.invalidate()
            result = blocked_state(previous['ap'])
            if result['macs'] != values: raise unavailable('Saved AP MAC list differs from the requested list; refresh before trying again.')
        app.audit(req, 'touchscreen.blocked.update', {'ap': result['ap'], 'blocked': blocked, 'count': len(values)})
        result['traffic_verified'] = False
        return result

    def preferences():
        saved = app.state.read_json('touchscreen.json', {})
        out = dict(DEFAULTS)
        if isinstance(saved, dict):
            if saved.get('language') in ('en', 'es'): out['language'] = saved['language']
            if saved.get('theme') in ('system', 'light', 'dark'): out['theme'] = saved['theme']
            timeout = saved.get('display_timeout_s')
            if type(timeout) is int and 30 <= timeout <= 3600: out['display_timeout_s'] = timeout
        return out

    @app.router.route('GET', '/api/touchscreen/preferences')
    def get_preferences(req): return preferences()

    @app.router.route('PUT', '/api/touchscreen/preferences')
    def put_preferences(req):
        data = v.only_keys(req.json, DEFAULTS)
        changes = {}
        if 'language' in data: changes['language'] = v.choice('language', data['language'], ('en', 'es'))
        if 'theme' in data: changes['theme'] = v.choice('theme', data['theme'], ('system', 'light', 'dark'))
        if 'display_timeout_s' in data: changes['display_timeout_s'] = v.integer('display_timeout_s', data['display_timeout_s'], 30, 3600)
        # One state lock covers read/merge/atomic write, including concurrent browsers.
        with app.state._lock:
            result = preferences()
            result.update(changes)
            app.state.write_json('touchscreen.json', result)
        app.audit(req, 'touchscreen.preferences.update', {'keys': sorted(changes)})
        return result

    @app.router.route('GET', '/api/touchscreen/capabilities')
    def get_capabilities(req):
        return {'ram_only': True, 'functions': {key: {'available': False, 'reason': reason} for key, reason in UNAVAILABLE.items()},
                'presentation': {'available': True, 'hardware_power_control': False, 'languages': ['en', 'es']}}

    @app.router.route('POST', '/api/touchscreen/actions/{name}', tier=2)
    def missing_action(req):
        raise unavailable(UNAVAILABLE.get(req.params.get('name'), 'No supported touchscreen hardware action with this identifier exists.'))
