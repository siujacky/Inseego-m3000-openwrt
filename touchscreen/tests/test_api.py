import io
import json
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from m3100_touch.api import ApiClient, ApiError


class Reply(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


class ApiTests(unittest.TestCase):
    def test_login_csrf_confirmation_and_logout(self):
        c = ApiClient()
        self.assertEqual(c.base_url, 'http://127.0.0.1:8088')
        with patch.object(c._opener, 'open', side_effect=[Reply({'authenticated': True, 'csrf': 'abc'}), Reply({'ok': True}), Reply({'ok': True})]) as op:
            c.login('private')
            c.request('PUT', '/api/settings', {'theme': 'dark'}, confirm=True)
            req = op.call_args.args[0]
            self.assertEqual(req.get_header('X-csrf-token'), 'abc')
            self.assertTrue(json.loads(req.data)['confirm'])
            c.logout()
            self.assertFalse(c.authenticated)
            self.assertIsNone(c.csrf)

    def test_timeout_never_replayed(self):
        c = ApiClient()
        with patch.object(c._opener, 'open', side_effect=socket.timeout('password=secret')) as op:
            with self.assertRaises(ApiError) as caught:
                c.request('PUT', '/api/settings', {})
            self.assertEqual(op.call_count, 1)
            self.assertNotIn('secret', str(caught.exception))

    def test_401_clears_session_and_428_preserves_status(self):
        c = ApiClient()
        for status in (428, 401):
            c.authenticated, c.csrf = True, 'token'
            err = HTTPError('url', status, 'bad', {}, Reply({'error': {'code': 'confirm_required', 'message': 'failed', 'details': {'password': 'leak', 'tier': 2}}}))
            with patch.object(c._opener, 'open', side_effect=err):
                with self.assertRaises(ApiError) as caught:
                    c.request('PUT', '/api/settings', {})
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn('leak', repr(caught.exception.details))
        self.assertFalse(c.authenticated)

    def test_bounds_and_paths(self):
        c = ApiClient()
        for path in ('https://evil/api/x', '//evil/api/x', '/api/x\r\nInjected: yes'):
            with self.assertRaises(ValueError): c.request('GET', path)
        with patch.object(c._opener, 'open', return_value=io.BytesIO(b'x' * (c.MAX_RESPONSE + 1))):
            with self.assertRaises(ApiError): c.request('GET', '/api/status')
        with self.assertRaises(ApiError): c.request('POST', '/api/login', {'x': 'x' * c.MAX_BODY})

    def test_malformed_error_and_late_lock_are_bounded(self):
        c = ApiClient()
        err = HTTPError('url', 500, 'bad', {}, Reply({'error': 'broken'}))
        with patch.object(c._opener, 'open', side_effect=err):
            with self.assertRaises(ApiError): c.request('GET', '/api/status')
        def late(*args, **kwargs):
            c.clear_session()
            return Reply({'authenticated': True, 'csrf': 'late-token'})
        with patch.object(c._opener, 'open', side_effect=late):
            with self.assertRaises(ApiError): c.login('private')
        self.assertFalse(c.authenticated)

    def test_error_reason_preserved_with_submitted_secret_redacted(self):
        c = ApiClient()
        err = HTTPError('url', 400, 'bad', {}, Reply({'error': {'code': 'invalid_field', 'message': 'USB management protected; received privatepw', 'details': {'cause': 'privatepw'}}}))
        with patch.object(c._opener, 'open', side_effect=err):
            with self.assertRaises(ApiError) as caught: c.request('PUT', '/api/wifi/primary', {'password': 'privatepw'})
        self.assertIn('USB management protected', caught.exception.message)
        self.assertNotIn('privatepw', caught.exception.message + repr(caught.exception.details))

