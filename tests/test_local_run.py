"""Offline regression tests. No credentials, browser, or network required."""
import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class Jar(dict):
    def set(self, name, value, **kwargs):
        self[name] = value

    def get_dict(self):
        return dict(self)


class Response:
    def __init__(self, body, headers, status):
        self.body, self.headers, self.status_code = body, headers, status

    def json(self):
        return self.body


class Session:
    queue = []
    calls = []

    def __init__(self, **kwargs):
        self.cookies = Jar()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs, dict(self.cookies)))
        body, headers, cookies, status = self.queue.pop(0)
        self.cookies.update(cookies)
        return Response(body, headers, status)


class Solver:
    def __init__(self, **kwargs):
        pass

    def solve(self, **kwargs):
        return 'PRIVATE_CAPTCHA'


def load_runner():
    spec = importlib.util.spec_from_file_location('local_runner_under_test', ROOT / 'local_run.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        'curl_cffi': types.SimpleNamespace(requests=types.SimpleNamespace(Session=Session)),
        'turnstile_solver': types.SimpleNamespace(TurnstileSolver=Solver),
    }):
        spec.loader.exec_module(module)
    return module


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.module = load_runner()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.module.DATA = Path(self.temp.name)
        (self.module.DATA / 'config.json').write_text(json.dumps({
            'user': 'test-user', 'password': 'PRIVATE_PASSWORD', 'key': 'PRIVATE_KEY'}))
        self.runner = self.module.Runner()
        self.runner.store('session=PRIVATE_OLD_COOKIE', {
            'security_token': 'PRIVATE_SECURITY', 'csrf_token': 'PRIVATE_CSRF'})
        self.original = self.runner.state_file.read_bytes()
        Session.queue, Session.calls = [], []
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def tearDown(self):
        for secret in ('PRIVATE_PASSWORD', 'PRIVATE_KEY', 'PRIVATE_CAPTCHA',
                       'PRIVATE_OLD_COOKIE', 'PRIVATE_SECURITY', 'PRIVATE_CSRF', 'PRIVATE_NEW_COOKIE'):
            self.assertNotIn(secret, self.output.getvalue())

    def feed(self, body, headers=None, cookies=None, status=200):
        Session.queue.append((body, headers or {}, cookies or {}, status))

    def assert_preserved(self):
        self.assertEqual(self.runner.state_file.read_bytes(), self.original)

    def test_existing_cookie_sign_and_response_rotation(self):
        self.feed({'success': True}, {'x-security-token': 'rotated'}, {'session': 'PRIVATE_NEW_COOKIE'})
        self.assertEqual(self.runner.execute('check'), 0)
        self.assertEqual(len(Session.calls), 1)
        self.assertEqual(self.runner.state['tokens']['security_token'], 'rotated')
        self.assertEqual(self.runner.state_file.stat().st_mode & 0o777, 0o600)

    def test_email_redirect_keeps_original_state(self):
        self.feed({'success': True, 'redirect': '/emailSignIn.html'},
                  {'x-security-token': 'unverified'}, {'session': 'unverified'})
        self.assertEqual(self.runner.execute('login-test'), 2)
        self.assert_preserved()
        call = Session.calls[0]
        self.assertEqual(call[3], {})  # No old Cookie can mask a failed login.
        self.assertFalse(call[2]['allow_redirects'])
        self.assertEqual(call[2]['headers']['x-security-token'], 'PRIVATE_SECURITY')
        self.assertEqual(call[2]['headers']['x-csrf-token'], 'PRIVATE_CSRF')
        self.assertEqual(call[2]['headers']['x-captcha-token'], 'PRIVATE_CAPTCHA')
        self.assertEqual(call[2]['json'], {'username': 'test-user', 'password': 'PRIVATE_PASSWORD'})

    def test_two_factor_preserves_state(self):
        self.feed({'success': True, 'need2FA': True}, cookies={'session': 'unverified'})
        self.assertEqual(self.runner.execute('login-test'), 2)
        self.assert_preserved()

    def test_success_without_session_is_not_login(self):
        self.feed({'success': True})
        self.assertEqual(self.runner.execute('login-test'), 2)
        self.assert_preserved()

    def test_bad_account_probe_does_not_commit(self):
        self.feed({'success': True}, cookies={'session': 'PRIVATE_NEW_COOKIE'})
        self.feed({'success': False}, status=401)
        self.assertEqual(self.runner.execute('login-test'), 2)
        self.assert_preserved()

    def test_verified_login_saves_latest_tokens(self):
        self.feed({'success': True}, {'x-csrf-token': 'first'}, {'session': 'PRIVATE_NEW_COOKIE'})
        self.feed({'unreadCount': {}}, {'x-csrf-token': 'second'})
        self.assertEqual(self.runner.execute('login-test'), 0)
        self.assertEqual(Session.calls[0][3], {})
        self.assertEqual(Session.calls[1][0], 'GET')
        self.assertEqual(Session.calls[1][2]['headers']['x-csrf-token'], 'first')
        self.assertEqual(self.runner.state['tokens']['security_token'], 'PRIVATE_SECURITY')
        self.assertEqual(self.runner.state['tokens']['csrf_token'], 'second')
        self.assertEqual(self.runner.state['cookie'], 'session=PRIVATE_NEW_COOKIE')

    def test_observed_already_done_500_is_success(self):
        self.feed({'success': False, 'message': '今天已完成签到，请勿重复操作'},
                  {'x-csrf-token': 'rotated'}, status=500)
        self.assertEqual(self.runner.execute('check'), 0)
        self.assertEqual(len(Session.calls), 1)
        self.assertEqual(self.runner.state['tokens']['csrf_token'], 'rotated')

    def test_other_server_error_does_not_relogin(self):
        self.feed({'success': False, 'message': '服务器内部错误'}, status=500)
        self.assertEqual(self.runner.execute('run'), 1)
        self.assertEqual(len(Session.calls), 1)
        self.assert_preserved()

    def test_expired_cookie_relogin_then_sign(self):
        self.feed({'success': False, 'message': '未登录'}, status=401)
        self.feed({'success': True}, cookies={'session': 'PRIVATE_NEW_COOKIE'})
        self.feed({'unreadCount': {}})
        self.feed({'success': True})
        self.assertEqual(self.runner.execute('run'), 0)
        self.assertEqual(Session.calls[1][3], {})
        self.assertEqual(len(Session.calls), 4)

    def test_import_keeps_backup(self):
        with patch('builtins.input', side_effect=['session=imported; x=y', 'security', 'csrf']):
            self.assertEqual(self.runner.execute('import-state'), 0)
        self.assertEqual((self.module.DATA / 'session_state.previous.json').read_bytes(), self.original)
        self.assertEqual(self.runner.state['tokens']['csrf_token'], 'csrf')

    def test_changed_account_rejects_other_state(self):
        (self.module.DATA / 'config.json').write_text(json.dumps({
            'user': 'another-user', 'password': 'password', 'key': 'key'}))
        with self.assertRaises(ValueError):
            self.module.Runner()


class LegacyLoginTests(unittest.TestCase):
    def test_schema_and_email_redirect(self):
        # Load just this function, avoiding optional notification imports.
        tree = ast.parse((ROOT / 'nodeseek_sign.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'session_login')
        requests_seen = []
        fake_session = types.SimpleNamespace(
            get=lambda *a, **k: None,
            cookies=Jar(session='unverified'),
            post=lambda *a, **k: (requests_seen.append(k) or Response(
                {'success': True, 'redirect': '/emailSignIn.html'}, {}, 200)))
        scope = {'requests': types.SimpleNamespace(Session=lambda **k: fake_session),
                 'TurnstileSolver': Solver, 'YesCaptchaSolver': Solver}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'legacy_login', 'exec'), scope)
        with contextlib.redirect_stdout(io.StringIO()):
            result = scope['session_login']('user', 'password', 'turnstile', 'http://localhost:3000', 'key')
        self.assertIsNone(result)
        self.assertEqual(requests_seen[0]['json'], {'username': 'user', 'password': 'password'})
        self.assertEqual(requests_seen[0]['headers']['x-captcha-source'], 'turnstile')
        self.assertFalse(requests_seen[0]['allow_redirects'])


if __name__ == '__main__':
    unittest.main()
