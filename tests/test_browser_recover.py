"""Offline recovery gates; no browser, network, Docker or real credentials."""
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit

spec = importlib.util.spec_from_file_location('browser_recovery', Path(__file__).resolve().parents[1] / 'ns_browser_recover.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
functions = {n.name: n for n in ast.parse(module.WORKER).body if isinstance(n, ast.FunctionDef)}


class ContextLostError(Exception):
    pass


class Page:
    url = 'https://www.nodeseek.com/'

    def run_js(self, script, *args, **kwargs):
        if 'return {security_token:' in script:
            return {'security_token': 'test-security', 'csrf_token': 'test-csrf'}
        return {'ok': True}

    def run_cdp(self, *args, **kwargs):
        return {'cookies': [{'name': 'session', 'value': 'test-session'}]}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.results, self.queue = [], [], []
        self.ns = {}
        exec(compile(ast.Module(body=[functions['verify_and_attend']], type_ignores=[]), '<verify>', 'exec'), self.ns)
        self.ns.update(page=Page(), BASE='https://www.nodeseek.com', urlsplit=urlsplit,
            wait_current_document=lambda: True, must_origin=lambda: None, challenge_page=lambda: False,
            log=lambda *a: None, fail=lambda *a: 1, fetch_api=self.fetch,
            safe_message=str, ContextLostError=ContextLostError,
            emit=lambda *a, **kw: self.results.append(kw))
        self.account = {'status': 200, 'body': {'unreadCount': {}}}
        self.attendance = {'status': 200, 'body': {'success': True}}

    def fetch(self, path, method):
        self.calls.append(method)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def run_queue(self, *items):
        self.queue[:] = items
        return self.ns['verify_and_attend']()

    def test_worker_sources_compile(self):
        compile(module.WORKER, 'worker', 'exec')
        compile(module.DIAGNOSTIC_WORKER, 'diagnostic', 'exec')

    def test_success_requires_account_then_attendance(self):
        self.assertEqual(self.run_queue(self.account, self.attendance), 0)
        self.assertEqual(self.calls, ['GET', 'POST'])
        self.assertTrue(self.results[0]['account_verified'])
        self.assertTrue(self.results[0]['attendance_ok'])

    def test_invalid_account_skips_attendance(self):
        self.assertEqual(self.run_queue({'status': 500, 'body': {'message': 'USER NOT FOUND'}}), 1)
        self.assertEqual(self.calls, ['GET'])
        self.assertFalse(self.results)

    def test_challenge_does_not_produce_saved_result(self):
        self.assertEqual(self.run_queue(self.account, {'status': 403, 'challenge': True, 'body': None}), 1)
        self.assertFalse(self.results)

    def test_read_only_account_check_retries_lost_context(self):
        self.assertEqual(self.run_queue(ContextLostError(), self.account, self.attendance), 0)
        self.assertEqual(self.calls, ['GET', 'GET', 'POST'])

    def test_post_is_not_repeated_after_lost_context(self):
        with self.assertRaises(ContextLostError):
            self.run_queue(self.account, ContextLostError())
        self.assertEqual(self.calls, ['GET', 'POST'])
        self.assertFalse(self.results)

    def test_two_factor_page_stops(self):
        self.ns['page'].url = 'https://www.nodeseek.com/emailSignIn.html'
        self.assertEqual(self.run_queue(), 1)
        self.assertFalse(self.calls)

    def test_saved_state_has_backup_and_private_permissions(self):
        self.run_queue(self.account, self.attendance)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'session_state.json'
            original = b'{"cookie":"old"}'
            state.write_bytes(original)
            backup = module.save_result(root, 'test-owner', original, self.results[0])
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(json.loads(state.read_text())['cookie'], 'session=test-session')
            for p in (state, backup, root/'cookie.txt'):
                self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_concurrent_state_change_prevents_overwrite(self):
        self.run_queue(self.account, self.attendance)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root/'session_state.json'
            state.write_bytes(b'newer-state')
            with self.assertRaises(ValueError):
                module.save_result(root, 'test-owner', b'original-state', self.results[0])
            self.assertEqual(state.read_bytes(), b'newer-state')
            self.assertEqual(len(list(root.iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
