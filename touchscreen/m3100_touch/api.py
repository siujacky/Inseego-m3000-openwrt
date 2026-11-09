"""Bounded stdlib session client. No credential persistence or request replay."""
import http.cookiejar
import copy
import json
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request


def _clean(value, depth=0):
    if depth > 5: return '[bounded]'
    if isinstance(value, dict):
        return {str(k)[:80]: _clean(v, depth + 1) for k, v in list(value.items())[:50]
                if not re.search(r'password|secret|token|cookie|authorization|pin|imsi|iccid|imei', str(k), re.I)}
    if isinstance(value, list): return [_clean(x, depth + 1) for x in value[:50]]
    if isinstance(value, str): return ''.join(x for x in value[:1024] if x >= ' ' or x == '\n')
    return value if value is None or isinstance(value, (bool, int, float)) else None


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status, self.code = status, str(code)[:80]
        self.message = _clean(message)
        self.details = _clean(details)
        super().__init__(self.message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class _SessionRevocation:
    """One isolated, single-use cleanup; repr never contains the old session."""
    def __init__(self, client): self._client=client
    def __repr__(self): return '<SessionRevocation>'
    def supply_csrf(self,csrf):
        if self._client is not None and csrf: self._client.csrf=csrf
    def discard(self):
        client,self._client=self._client,None
        if client is not None: client.clear_session()
    def __call__(self):
        client,self._client=self._client,None
        if client is None: return
        try:
            try: return client.request('POST','/api/logout',{})
            except ApiError as error:
                if error.status!=401: raise
        finally: client.clear_session()


class ApiClient:
    MAX_RESPONSE = 512 * 1024
    MAX_BODY = 16 * 1024

    def __init__(self, base_url='http://127.0.0.1:8088', timeout=8):
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.path not in ('', '/') or parts.query or parts.fragment:
            raise ValueError('Use an HTTP API origin without credentials')
        self.base_url = base_url.rstrip('/')
        self.timeout = max(0.1, min(float(timeout), 30))
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self._cookies), _NoRedirect())
        self._generation = 0
        self._lock = threading.RLock()
        self.authenticated, self.csrf = False, None

    def clear_session(self):
        """Local lock is immediate; late in-flight login may never unlock it."""
        with self._lock:
            self._generation += 1
            self._cookies.clear()
            self._cookies._touch_csrf=None
            self._cookies = http.cookiejar.CookieJar()
            self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self._cookies), _NoRedirect())
            self.authenticated, self.csrf = False, None

    def _session(self):
        with self._lock: return self._generation,self._cookies,self._opener,self.csrf

    def _revocation(self, cookies, csrf):
        if not list(cookies) and not csrf: return None
        isolated=ApiClient(self.base_url,self.timeout)
        for cookie in cookies: isolated._cookies.set_cookie(copy.copy(cookie))
        isolated.csrf,isolated.authenticated=csrf,True
        return _SessionRevocation(isolated)

    def detach_session(self):
        """Atomically lock locally; return isolated old-session cleanup for worker."""
        with self._lock:
            cookies=self._cookies
            cleanup=self._revocation(cookies,self.csrf or getattr(cookies,'_touch_csrf',None))
            cookies._touch_revocation=cleanup
            self.clear_session()
            return cleanup

    def login(self, password):
        session=self._session()
        generation=session[0]
        data = self._request('POST', '/api/login', {'password': password}, session=session)
        with self._lock:
            if generation != self._generation:
                error=ApiError(401, 'session_cleared', 'Session locked during login')
                error.revocation=self._revocation(session[1],data.get('csrf'))
                raise error
            self.authenticated = data.get('authenticated') is True
            self.csrf = data.get('csrf') if self.authenticated else None
        return data

    def logout(self):
        session=self._session()
        try: return self._request('POST', '/api/logout', {},session=session)
        finally:
            with self._lock:
                if session[0]==self._generation: self.clear_session()

    def request(self, method, path, body=None, confirm=False, password=None):
        return self._request(method,path,body,confirm,password,self._session())

    def _request(self, method, path, body=None, confirm=False, password=None, session=None):
        method = method.upper()
        if method not in ('GET', 'POST', 'PUT', 'DELETE') or not isinstance(path, str) or not path.startswith('/api/') or any(ord(x) < 32 for x in path) or urllib.parse.urlsplit(path).netloc or '#' in path:
            raise ValueError('Invalid API request')
        payload = dict(body) if body is not None else None
        if confirm or password is not None:
            payload = payload or {}
            if confirm: payload['confirm'] = True
            if password is not None: payload['password'] = password
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode() if payload is not None else None
        if raw is not None and len(raw) > self.MAX_BODY:
            raise ApiError(413, 'request_too_large', 'Request exceeds touchscreen limit')
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'Origin': self.base_url}
        generation,cookies,opener,csrf=session
        if csrf and method != 'GET': headers['X-CSRF-Token'] = csrf
        request = urllib.request.Request(self.base_url + path, data=raw, headers=headers, method=method)
        status = 200
        try:
            try:
                response = opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                response, status = exc, exc.code
            with response:
                data = response.read(self.MAX_RESPONSE + 1)
            if len(data) > self.MAX_RESPONSE:
                raise ApiError(status if status >= 400 else 502, 'response_too_large', 'Response exceeds touchscreen limit')
            try: result = json.loads(data.decode('utf-8')) if data else {}
            except (ValueError, UnicodeError):
                raise ApiError(status if status >= 400 else 502, 'invalid_response', 'API returned an invalid response') from None
            if status >= 400:
                error = result.get('error', {}) if isinstance(result, dict) else {}
                if not isinstance(error, dict): error = {}
                code = error.get('code', 'request_failed')
                messages = {'confirm_required': 'Confirmation required', 'unauthorized': 'Login required', 'invalid_field': 'Check the selected field', 'unavailable': 'Function unavailable', 'conflict': 'Configuration changed; reload before applying'}
                message = error.get('message')
                if not isinstance(message, str): message = messages.get(code, 'API request failed; refresh to inspect current state')
                details = _clean(error.get('details'))
                sensitive = [v for k, v in (payload or {}).items() if isinstance(v, str) and v and re.search(r'password|secret|token|pin|puk', k, re.I)]
                def scrub(value):
                    if isinstance(value, str):
                        for item in sensitive: value = value.replace(item, '[redacted]')
                        return re.sub(r'(?i)(password|secret|token|cookie|puk)\s*[:=]\s*[^\s,;]+', r'\1=[redacted]', value)
                    if isinstance(value, dict): return {k: scrub(v) for k, v in value.items()}
                    if isinstance(value, list): return [scrub(v) for v in value]
                    return value
                raise ApiError(status, code, scrub(message), scrub(details))
            if path=='/api/login' and isinstance(result,dict) and result.get('authenticated') is True:
                cookies._touch_csrf=result.get('csrf')
                detached=getattr(cookies,'_touch_revocation',None)
                if detached is not None: detached.supply_csrf(result.get('csrf'))
            with self._lock:
                if generation != self._generation:
                    error=ApiError(401, 'session_cleared', 'Session locked while request was pending')
                    error.revocation=None if getattr(cookies,'_touch_revocation',None) is not None else self._revocation(cookies,getattr(cookies,'_touch_csrf',csrf))
                    raise error
            return result
        except ApiError as exc:
            with self._lock:
                if exc.status == 401 and generation==self._generation: self.clear_session()
            raise
        except (OSError, urllib.error.URLError, socket.timeout):
            raise ApiError(0, 'transport_error', 'Request timed out or connection failed. Changes may have reached the router; refresh before trying again.') from None
        finally:
            if generation != self._generation:
                cookies.clear()
                cookies._touch_csrf=None
                cookies._touch_revocation=None
