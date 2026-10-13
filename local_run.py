#!/usr/bin/env python3
"""NodeSeek local runner: retained cookies/device tokens, explicit login diagnosis.

Uses the existing ns-sign image (curl_cffi, requests, turnstile_solver).
Does not generate x-integrity-token, bypass email/OTP, or promise device portability.
Commands: run (default), import-state, check, login-test.
login-test sends saved device tokens in a fresh cookie jar; failures keep old state.
"""
import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from curl_cffi import requests
from turnstile_solver import TurnstileSolver

DATA = Path('/data')
BASE = 'https://www.nodeseek.com'
TOKEN_HEADERS = {'security_token': 'x-security-token', 'csrf_token': 'x-csrf-token'}


def write_private(path, value):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def cookie_pairs(text):
    if '\r' in text or '\n' in text:
        raise ValueError('Cookie 必须为单行')
    if text.lower().startswith('cookie:'):
        text = text.split(':', 1)[1].strip()
    pairs = {}
    for item in text.split(';'):
        if not item.strip():
            continue
        name, sep, value = item.strip().partition('=')
        if not sep or not name or any(c.isspace() for c in name):
            raise ValueError('Cookie 格式不正确')
        pairs[name] = value
    return pairs


def validate_token(value):
    if '\r' in value or '\n' in value:
        raise ValueError('设备令牌必须为单行')
    return value


class Runner:
    def __init__(self):
        self.cfg = json.loads((DATA / 'config.json').read_text(encoding='utf-8'))
        for key in ('user', 'password', 'key'):
            if not isinstance(self.cfg.get(key), str) or not self.cfg[key]:
                raise ValueError('config.json 中缺少有效配置：' + key)
        self.owner = hashlib.sha256(self.cfg['user'].strip().lower().encode()).hexdigest()
        self.state_file = DATA / 'session_state.json'
        self.state = {'cookie': '', 'tokens': {}, 'owner': self.owner}
        if self.state_file.exists():
            saved = json.loads(self.state_file.read_text(encoding='utf-8'))
            if saved.get('owner') != self.owner:
                raise ValueError('账号配置已改变。请恢复原账号配置后再运行，勿混用其他账号的登录状态。')
            self.state = saved
        elif (DATA / 'cookie.txt').exists():
            self.state['cookie'] = (DATA / 'cookie.txt').read_text(encoding='utf-8').strip()

    def import_state(self):
        print('仅在本机输入；不要额外加引号。请使用同一账号、同一浏览器中的值。')
        cookie = input('完整 Cookie（明文显示）：').strip()
        pairs = cookie_pairs(cookie)
        if not pairs.get('session'):
            raise ValueError('未找到非空 session Cookie，请复制已登录页面请求中的完整 Cookie')
        security = validate_token(input('security_token 的值（明文显示）：').strip())
        csrf = validate_token(input('csrf_token 的值（明文显示）：').strip())
        if not security or not csrf:
            raise ValueError('两个令牌均不能为空，未保存')
        cookie = '; '.join(f'{k}={v}' for k, v in pairs.items())
        # Keep a private rollback snapshot when replacing an existing imported state.
        if self.state_file.exists():
            write_private(DATA / 'session_state.previous.json', self.state_file.read_text(encoding='utf-8'))
        self.store(cookie, {'security_token': security, 'csrf_token': csrf})
        print('Cookie 和设备令牌已保存。尚未验证它们是否适用于 VPS，请运行 check。')
        return 0

    def store(self, cookie, tokens):
        payload = {'version': 1, 'owner': self.owner, 'cookie': cookie,
                   'tokens': {k: v for k, v in tokens.items() if k in TOKEN_HEADERS and v}}
        write_private(self.state_file, json.dumps(payload, ensure_ascii=False, indent=2))
        # Compatibility with the previous local runner and rollback instructions.
        write_private(DATA / 'cookie.txt', cookie)
        self.state = payload

    def session(self, restore_cookies=True):
        session = requests.Session(impersonate='chrome110')
        if restore_cookies:
            for name, value in cookie_pairs(self.state.get('cookie', '')).items():
                session.cookies.set(name, value, domain='www.nodeseek.com', path='/')
        return session

    def headers(self, tokens, extra=None):
        headers = {'Origin': BASE, 'Referer': BASE + '/signIn.html',
                   'Accept': 'application/json', 'Content-Type': 'application/json'}
        for key, name in TOKEN_HEADERS.items():
            if tokens.get(key):
                headers[name] = validate_token(tokens[key])
        headers.update(extra or {})
        return headers

    def call(self, session, tokens, method, path, payload=None, extra=None):
        # Fixed same-origin API paths; never forward credentials across redirects.
        if not path.startswith('/api/') or path.startswith('//'):
            raise ValueError('非预期API路径')
        response = session.request(method, BASE + path, headers=self.headers(tokens, extra),
                                   json=payload, timeout=60, allow_redirects=False)
        for key, header in TOKEN_HEADERS.items():
            value = response.headers.get(header)
            if value:
                tokens[key] = validate_token(value)
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            body = None
        return response, body

    def commit_session(self, session, tokens):
        jar = session.cookies.get_dict()
        if not jar.get('session'):
            raise ValueError('未找到有效 session Cookie，不覆盖已保存状态')
        self.store('; '.join(f'{k}={v}' for k, v in jar.items()), tokens)

    def attendance(self, session, tokens):
        response, body = self.call(session, tokens, 'POST', '/api/attendance?random=true')
        if body is None:
            print('签到接口未返回JSON，HTTP状态码：', response.status_code)
            return 'error'
        msg = str(body.get('message') or '')
        print('签到HTTP状态码：', response.status_code)
        print('签到消息：', msg[:300])
        already_done = response.status_code == 500 and msg.strip() == '今天已完成签到，请勿重复操作'
        if already_done or (200 <= response.status_code < 300 and (body.get('success') is True or '已完成签到' in msg)):
            self.commit_session(session, tokens)
            print('签到成功或今日已签到；Cookie 和响应中更新的设备令牌已保存。')
            return 'ok'
        if response.status_code == 401 or body.get('status') == 404 or '未登录' in msg or '未登陆' in msg:
            return 'signed-out'
        return 'error'

    def summary(self, response, body, session, sent_tokens):
        detail = {
            'http_status': response.status_code,
            'json_fields': sorted(body) if body else [],
            'success': body.get('success') if body else None,
            'need2FA': bool(body.get('need2FA')) if body else False,
            'redirect_path': urlsplit(str(body.get('redirect') or '')).path if body else '',
            'cookie_names': sorted(session.cookies.get_dict()),
            'sent_device_header_names': [TOKEN_HEADERS[k] for k in TOKEN_HEADERS if sent_tokens.get(k)],
            'response_device_header_presence': {h: bool(response.headers.get(h)) for h in TOKEN_HEADERS.values()},
            'integrity_header_sent': False,
        }
        write_private(DATA / 'login-summary.json', json.dumps(detail, ensure_ascii=False, indent=2))
        print('登录诊断摘要（无令牌值）：')
        print(json.dumps(detail, ensure_ascii=False, indent=2))

    def login(self, session, tokens):
        print('正在获取真人验证令牌……')
        solver = TurnstileSolver(api_base_url='http://127.0.0.1:3000', client_key=self.cfg['key'])
        token = solver.solve(url=BASE + '/signIn.html',
                             sitekey='0x4AAAAAAAaNy7leGjewpVyR', verbose=False)
        if not token:
            print('未取得真人验证令牌')
            return False
        print('已取得真人验证令牌，提交登录请求。')
        sent_tokens = dict(tokens)
        response, body = self.call(session, tokens, 'POST', '/api/account/signIn',
                                   {'username': self.cfg['user'], 'password': self.cfg['password']},
                                   {'x-captcha-source': 'turnstile', 'x-captcha-token': token})
        self.summary(response, body, session, sent_tokens)
        if not body:
            print('登录响应不是JSON，保留原登录状态')
            return False
        redirect = urlsplit(str(body.get('redirect') or '')).path
        if body.get('need2FA'):
            print('仍需二次验证码，停止自动登录并保留原登录状态')
            return False
        if 'emailSignIn' in redirect or 'smsSignIn' in redirect:
            print('仍需邮箱或短信验证；仅补充设备令牌不足以完成本次登录。原状态未覆盖。')
            return False
        if not (200 <= response.status_code < 300 and body.get('success') is True):
            print('登录未获接受：', str(body.get('message') or '未提供原因')[:300])
            return False
        if not session.cookies.get_dict().get('session'):
            print('接口返回success但没有session Cookie，不认定为登录成功')
            return False
        # Read-only account endpoint: do not trust success:true or cookie presence alone.
        probe_response, probe = self.call(session, tokens, 'GET', '/api/notification/unread-count')
        if (probe_response.status_code != 200 or not probe or probe.get('success') is False
                or not isinstance(probe.get('unreadCount'), dict)):
            print('账号接口未确认新会话有效，保留原状态。HTTP：', probe_response.status_code,
                  'JSON字段：', sorted(probe) if probe else [])
            return False
        self.commit_session(session, tokens)
        print('新会话已通过账号接口校验，Cookie 和设备令牌已保存。')
        return True

    def execute(self, command):
        if command == 'import-state':
            return self.import_state()
        tokens = dict(self.state.get('tokens') or {})
        if command == 'login-test':
            if not tokens.get('security_token'):
                print('尚未导入设备令牌，请先运行 import-state')
                return 1
            print('受控重新登录测试：不发送旧Cookie，仅带已保存的设备令牌。失败不覆盖旧状态。')
            with self.session(restore_cookies=False) as session:
                return 0 if self.login(session, tokens) else 2

        with self.session() as session:
            if self.state.get('cookie'):
                result = self.attendance(session, tokens)
                if result == 'ok':
                    return 0
                if command == 'check' or result != 'signed-out':
                    print('本次未完成签到；不把普通网络或接口错误直接当作Cookie过期。')
                    return 1
            elif command == 'check':
                print('没有保存的Cookie，请先导入登录状态')
                return 1

        # A new jar ensures an old authenticated session cannot mask a failed login.
        tokens = dict(self.state.get('tokens') or {})
        with self.session(restore_cookies=False) as session:
            if not self.login(session, tokens):
                return 2
            return 0 if self.attendance(session, tokens) == 'ok' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='run',
                        choices=['run', 'import-state', 'check', 'login-test'])
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    with (DATA / 'session-state.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('另一个任务正在使用登录状态，请稍后再运行')
            return 1
        return Runner().execute(args.command)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n已取消')
        sys.exit(130)
    except Exception as exc:
        # Exceptions may contain HTTP headers; print the type without private details.
        print('运行异常：', type(exc).__name__)
        if isinstance(exc, (ValueError, FileNotFoundError)):
            print(str(exc))
        sys.exit(1)
