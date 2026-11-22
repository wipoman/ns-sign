#!/usr/bin/env python3
"""One-shot NodeSeek browser recovery. Run on the VPS host as root.

Reads /opt/ns-sign/data; uses the existing cloudflyer container. Does not
change systemd, images, passwords, or CloudFlyer source. Makes at most one
login submission and one attendance submission. Saves only after account
verification AND successful/already-completed attendance. No secrets in logs.
Use --diagnose for read-only inspection of the existing browser, without
loading account configuration, navigation, login, or session-state writes.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

VERSION = "1.5"
MARKER = "NS_BROWSER_RECOVER_WORKER_V1"

WORKER = r'''
# NS_BROWSER_RECOVER_WORKER_V1
import json, logging, os, re, signal, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from DrissionPage import ChromiumPage
from DrissionPage.errors import ContextLostError

logging.disable(logging.CRITICAL)
BASE = "https://www.nodeseek.com"
page = None
cfg = {}
stage = "初始化"

def emit(kind, **values):
    print(json.dumps(dict(kind=kind, **values), ensure_ascii=False), flush=True)

def log(message):
    emit("log", message=message)

def safe_message(value):
    text = str(value or "")
    private = [cfg.get("user"), cfg.get("password"), cfg.get("key")]
    private += list((cfg.get("tokens") or {}).values())
    for secret in private:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, "[已隐藏]")
    text = re.sub(r"[A-Za-z0-9_.=-]{64,}", "[长值已隐藏]", text)
    return text[:240]

def timeout(signum, frame):
    log("总运行时间超过360秒，停止；阶段：" + stage)
    emit("result", ok=False)
    os._exit(124)

signal.signal(signal.SIGALRM, timeout)
signal.alarm(360)
emit("hello", pid=os.getpid())

def origin_ok(url=None):
    u = urlsplit(page.url if url is None else url)
    return u.scheme == "https" and u.hostname == "www.nodeseek.com"

def must_origin():
    if not origin_ok():
        raise RuntimeError("unexpected_origin")

def screenshot():
    if page is None or not origin_ok():
        return
    try:
        page.run_js("""
        document.getElementById('ns-recovery-mask')?.remove();
        const s = document.createElement('style');
        s.id = 'ns-recovery-mask';
        s.textContent = 'input,textarea{color:transparent!important;-webkit-text-fill-color:transparent!important;text-shadow:none!important;caret-color:transparent!important}';
        document.documentElement.appendChild(s);
        """, timeout=3)
        page.get_screenshot(path="/tmp/ns-browser-recovery.png", full_page=False)
        os.chmod("/tmp/ns-browser-recovery.png", 0o600)
        log("现场截图已保存到容器 /tmp/ns-browser-recovery.png（输入框文字已隐藏）。")
    except Exception:
        log("现场截图未能生成。")
    finally:
        try:
            page.run_js("document.getElementById('ns-recovery-mask')?.remove();", timeout=3)
        except Exception:
            pass

def fail(message):
    log(message)
    screenshot()
    emit("result", ok=False)
    return 1

def challenge_page():
    title = page.title.lower()
    return any(x in title for x in ("just a moment", "cloudflyer")) or "_cf_chl_opt" in page.html

def has_token():
    return bool(page.run_js(r"""
    const fields = [...document.querySelectorAll('input[name="cf-turnstile-response"],textarea[name="cf-turnstile-response"]')];
    return fields.some(e => Boolean(e.value));
    """, timeout=4))

def click_widget():
    root = page.run_cdp("DOM.getDocument", depth=-1, pierce=True)["root"]
    stack, candidates = [root], set()
    while stack:
        node = stack.pop()
        attrs = node.get("attributes") or []
        attrs = dict(zip(attrs[::2], attrs[1::2]))
        if node.get("nodeName", "").upper() == "IFRAME":
            if urlsplit(attrs.get("src", "")).hostname == "challenges.cloudflare.com":
                candidates.add(node["backendNodeId"])
        stack.extend(node.get("children") or [])
        stack.extend(node.get("shadowRoots") or [])
        if node.get("contentDocument"):
            stack.append(node["contentDocument"])
    visible = []
    for node_id in candidates:
        try:
            q = page.run_cdp("DOM.getBoxModel", backendNodeId=node_id)["model"]["border"]
            w, h = max(q[::2])-min(q[::2]), max(q[1::2])-min(q[1::2])
            if 200 <= w <= 400 and 40 <= h <= 120:
                visible.append(node_id)
        except Exception:
            pass
    log("标准验证框数量：" + str(len(visible)))
    if len(visible) != 1:
        return False
    node_id = visible[0]
    page.run_cdp("DOM.scrollIntoViewIfNeeded", backendNodeId=node_id)
    q = page.run_cdp("DOM.getBoxModel", backendNodeId=node_id)["model"]["border"]
    x, y = min(q[::2])+25, (min(q[1::2])+max(q[1::2]))/2
    return desktop_click(x,y)

def desktop_click(x,y):
    must_origin()
    # Find the display actually inherited by the browser; the container's
    # default DISPLAY can point to a different, unused X server.
    owners=[]
    for p in Path('/proc').glob('[0-9]*'):
        try:
            name=(p/'comm').read_text().strip().lower()
            if name not in ('chrome','chromium','chromium-browser'): continue
            raw=(p/'cmdline').read_bytes()
            if not re.search(rb'--remote-debugging-port(?:=|\x00)\d+',raw): continue
            env={}
            for part in (p/'environ').read_bytes().split(b'\x00'):
                k,sep,v=part.partition(b'=')
                if sep and k in (b'DISPLAY',b'XAUTHORITY'):
                    env[k.decode()]=v.decode()
            if env.get('DISPLAY'): owners.append((p.name,env))
        except (OSError,UnicodeError): continue
    if len(owners)!=1:
        log('无法唯一确定 Chrome 的桌面环境，未点击。'); return False
    pid,desktop=owners[0]
    env=os.environ.copy()
    env.pop('DISPLAY',None); env.pop('XAUTHORITY',None)
    env.update(desktop)
    def xd(*args):
        return subprocess.run(['xdotool',*args],env=env,check=True,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=5).stdout
    viewport=page.run_js('return {w:innerWidth,h:innerHeight,ow:outerWidth,oh:outerHeight,dpr:devicePixelRatio,scale:window.visualViewport?.scale || 1};',timeout=4)
    matches=[]
    for wid in xd('search','--onlyvisible','--pid',pid).split():
        if not wid.isdecimal(): continue
        geom={}
        for line in xd('getwindowgeometry','--shell',wid).splitlines():
            k,sep,v=line.partition('=')
            if sep and re.fullmatch(r'-?\d+',v): geom[k]=int(v)
        if (geom.get('WIDTH'),geom.get('HEIGHT'))==(viewport['ow'],viewport['oh']):
            matches.append(geom)
    # Only the observed borderless app window at 1:1 scale is supported.
    # Never guess an address-bar offset on a differently configured browser.
    if (len(matches)!=1 or viewport['dpr']!=1 or viewport['scale']!=1
        or viewport['w']!=viewport['ow'] or viewport['h']!=viewport['oh']
        or not (0<=x<viewport['w'] and 0<=y<viewport['h'])):
        log('桌面窗口尺寸或缩放与预期不同，未点击：'+json.dumps(viewport)); return False
    geom=matches[0]
    sx,sy=round(geom['X']+x),round(geom['Y']+y)
    log('桌面点击：DISPLAY='+desktop['DISPLAY']+'，坐标='+str((sx,sy)))
    xd('mousemove',str(sx),str(sy),'sleep','0.3','click','1')
    return True

def fetch_api(path, method):
    must_origin()
    return page.run_js(r"""
    const path = arguments[0], method = arguments[1];
    return (async () => {
      if(location.origin !== 'https://www.nodeseek.com') return {error:'unexpected_origin'};
      const headers = {'Accept':'application/json'};
      if(method === 'POST') headers['Content-Type']='application/json';
      for(const [k,h] of [['security_token','x-security-token'],['csrf_token','x-csrf-token']]) {
        const value=localStorage.getItem(k); if(value) headers[h]=value;
      }
      const controller=new AbortController();
      const timer=setTimeout(()=>controller.abort(),12000);
      try {
        const r=await fetch(path,{method,headers,credentials:'include',redirect:'error',signal:controller.signal});
        const text=await r.text(); let body=null;
        try { body=JSON.parse(text); } catch (_) {}
        const object=body!==null && typeof body==='object' && !Array.isArray(body);
        const challenge=r.headers.get('cf-mitigated')==='challenge' || (!object && /<title>\s*(CloudFlyer|Just a moment)/i.test(text));
        if(object && !challenge && r.ok && body.success!==false) {
          for(const [k,h] of [['security_token','x-security-token'],['csrf_token','x-csrf-token']]) {
            const v=r.headers.get(h); if(v) localStorage.setItem(k,v);
          }
        }
        return {status:r.status,body:object?body:null,challenge};
      } catch(e) { return {error:e.name}; }
      finally {clearTimeout(timer);}
    })();
    """, path, method, timeout=16)

def main():
    global page, cfg, stage
    cfg = json.load(sys.stdin)
    if cfg.get("finish_current"):
        return resume_current(verify_only=True)
    if cfg.get("resume"):
        return resume_current()
    stage = "等待 API"
    log("[1/6] 等待 CloudFlyer API……")
    with requests.Session() as client:
        client.trust_env = False
        api = "http://127.0.0.1:3000"
        deadline = time.monotonic()+50
        while time.monotonic() < deadline:
            try:
                if client.get(api+"/", timeout=3).status_code in (200,404):
                    break
            except requests.RequestException:
                pass
            time.sleep(2)
        else:
            return fail("API 未就绪。")
        stage = "整页验证"
        log("[2/6] 创建登录页整页验证任务……")
        r = client.post(api+"/createTask", json={"clientKey":cfg["key"],"type":"CloudflareChallenge","url":BASE+"/signIn.html","content":True}, timeout=20)
        r.raise_for_status()
        task_id = r.json().get("taskId")
        if not task_id:
            return fail("未取得任务编号。")
        deadline = time.monotonic()+130
        while time.monotonic() < deadline:
            r = client.post(api+"/getTaskResult", json={"clientKey":cfg["key"],"taskId":task_id}, timeout=15)
            r.raise_for_status()
            task = r.json()
            if task.get("status") == "completed":
                break
            log("整页验证处理中……")
            time.sleep(4)
        else:
            return fail("整页验证查询超时，不再创建重复任务。")
        result = task.get("result") or {}
        detail = result.get("response") or {}
        html = detail.get("content", "") if isinstance(detail,dict) else ""
        title_match = re.search(r"<title[^>]*>(.*?)</title>",html,re.I|re.S)
        title = title_match.group(1).strip() if title_match else ""
        log("任务返回的页面标题：" + safe_message(title))
        if (not result.get("success") or "nodeseek" not in title.lower()
                or "_cf_chl_opt" in html or not (detail.get("cookies") or {}).get("cf_clearance")):
            return fail("任务未确认取得正常 NodeSeek 页面和通行 Cookie。")

    stage = "打开登录页"
    ports = set()
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            ports.update(int(x) for x in re.findall(rb"--remote-debugging-port(?:=|\x00)(\d+)",p.read_bytes()))
        except OSError:
            pass
    ports.discard(0)
    if len(ports) != 1:
        return fail("调试端口数量不唯一，停止，避免操作错误浏览器。")
    page = ChromiumPage(addr_or_opts="127.0.0.1:"+str(next(iter(ports))), timeout=4)
    log("[3/6] 从内部首页返回 NodeSeek，恢复设备令牌……")
    # task_main() navigates back to internals.cloudflyer.com in finally.
    page.run_cdp("Network.enable")
    page.run_cdp("Network.setCacheDisabled", cacheDisabled=True)
    page.get(BASE+"/signIn.html", timeout=20, retry=0)
    must_origin()
    page.run_js("const t=arguments[0]; for(const k of ['security_token','csrf_token']) if(t[k]) localStorage.setItem(k,t[k]);",cfg["tokens"],timeout=4)
    for c in page.run_cdp("Network.getCookies",urls=[BASE+"/"]).get("cookies",[]):
        if c.get("name") in ("pjwt","session","smac"):
            page.run_cdp("Network.deleteCookies",name=c["name"],domain=c["domain"],path=c["path"])
    page.get(BASE+"/signIn.html", timeout=20, retry=0)
    must_origin()
    if urlsplit(page.url).path != "/signIn.html" or challenge_page():
        return fail("恢复设备状态后仍出现整页验证，本次停止。")
    prepared = page.run_js(r"""
    const visible=e=>!e.disabled && e.getClientRects().length>0;
    const ps=[...document.querySelectorAll('input[type="password"]')].filter(visible);
    if(ps.length!==1) return {ok:false,reason:'密码框不唯一'};
    const scope=ps[0].closest('form') || document;
    let us=[...scope.querySelectorAll('input')].filter(e=>visible(e)&&['text','email'].includes(e.type));
    const named=us.filter(e=>/username|email|account/i.test(e.name+' '+e.id)||e.autocomplete==='username');
    if(named.length===1) us=named;
    if(us.length!==1) return {ok:false,reason:'账号框不唯一'};
    const bs=[...scope.querySelectorAll('button,input[type="submit"]')].filter(e=>(e.tagName==='INPUT'?e.value:e.textContent).replace(/\s/g,'')==='登录');
    if(bs.length!==1) return {ok:false,reason:'登录按钮不唯一'};
    us[0].setAttribute('data-ns-recover','user'); ps[0].setAttribute('data-ns-recover','password'); bs[0].setAttribute('data-ns-recover','login');
    return {ok:true,stylesheets:document.styleSheets.length};
    """, timeout=5)
    if not prepared.get("ok"):
        return fail("表单定位失败："+prepared.get("reason","未知"))
    log("已加载样式表数量："+str(prepared.get("stylesheets")))
    page.ele("css:[data-ns-recover=user]",timeout=3).input(cfg["user"],clear=True,by_js=False)
    page.ele("css:[data-ns-recover=password]",timeout=3).input(cfg["password"],clear=True,by_js=False)

    stage = "登录页 Turnstile 验证"
    log("[4/6] 等待登录页验证码……")
    for _ in range(5):
        if has_token():
            break
        time.sleep(1)
    if not has_token():
        if not click_widget():
            return fail("未唯一定位标准验证框，没有提交登录。")
        log("已通过桌面鼠标点击一次验证框，最多等待45秒；不会重复点击。")
        deadline = time.monotonic()+45
        last_log = time.monotonic()
        while time.monotonic() < deadline:
            if has_token():
                break
            if time.monotonic()-last_log >= 10:
                log("验证码仍在处理中……")
                last_log = time.monotonic()
            time.sleep(1)
        else:
            return fail("未取得登录验证码令牌，没有提交账号密码。")
    log("验证码令牌已取得，不显示具体值。")
    return finish_login()

def resume_current(verify_only=False):
    global page, stage
    stage = "连接当前登录页"
    log("继续当前页面：不创建验证任务、不刷新、不清除 Cookie。")
    ports = set()
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            ports.update(int(x) for x in re.findall(rb"--remote-debugging-port(?:=|\x00)(\d+)", p.read_bytes()))
        except OSError:
            pass
    ports.discard(0)
    if len(ports) != 1:
        return fail("调试端口数量不唯一，停止。")
    address = "127.0.0.1:"+str(next(iter(ports)))
    with requests.Session() as client:
        client.trust_env = False
        response = client.get("http://"+address+"/json/list", timeout=5)
        response.raise_for_status()
        tabs = response.json()
    if not isinstance(tabs, list):
        return fail("未取得标签页列表，停止。")
    tabs = [t for t in tabs if t.get("type") == "page"]
    if len(tabs) != 1 or not origin_ok(tabs[0].get("url", "")):
        return fail("需要唯一的 NodeSeek 页面；本次不切换页面。")
    page = ChromiumPage(addr_or_opts=address, timeout=4)
    if verify_only:
        log("直接校验当前浏览器会话，不重新提交账号密码。")
        return verify_and_attend()
    must_origin()
    if urlsplit(page.url).path != "/signIn.html" or challenge_page():
        return fail("当前不是正常的 NodeSeek 登录页，停止。")
    stage = "检查当前表单与令牌"
    prepared = page.run_js(r"""
    const select = name => [...document.querySelectorAll('[data-ns-recover="'+name+'"]')];
    const us=select('user'), ps=select('password'), bs=select('login');
    if(us.length!==1 || ps.length!==1 || bs.length!==1) return {ok:false};
    const usable=e=>!e.disabled && e.getClientRects().length>0;
    return {
      ok: [us[0],ps[0],bs[0]].every(usable),
      credentials_match: us[0].value===arguments[0] && ps[0].value===arguments[1],
      device_tokens_present: !!localStorage.getItem('security_token') && !!localStorage.getItem('csrf_token')
    };
    """, cfg["user"], cfg["password"], timeout=5)
    if not isinstance(prepared, dict) or not prepared.get("ok"):
        return fail("未找到之前准备好的登录表单；本次不重新填写。")
    if not prepared.get("credentials_match"):
        return fail("页面账号密码与本机配置不一致；本次不提交。")
    if not prepared.get("device_tokens_present"):
        return fail("当前页面的设备令牌不完整；本次不提交。")
    if not has_token():
        return fail("当前没有验证码令牌。请在当前页面完成验证后立即重试 --resume；不要重新运行完整恢复流程。")
    log("当前表单匹配配置，验证码字段非空；是否有效由登录服务器确认。")
    return finish_login()

def finish_login():
    global stage
    stage = "登录请求"
    log("[5/6] 提交一次网页登录……")
    page.listen.start("/api/account/signIn",method="POST")
    login_tokens = {}
    rejection = None
    try:
        if not page.ele("css:[data-ns-recover=login]",timeout=3).click(by_js=False,timeout=3):
            return fail("登录按钮未能点击。")
        packet = page.listen.wait(timeout=25,raise_err=False)
        if packet and not packet.is_failed:
            body = packet.response.body
            if isinstance(body,str):
                try: body=json.loads(body)
                except ValueError: body=None
            log("登录 HTTP 状态："+str(packet.response.status))
            if isinstance(body,dict):
                log("登录消息："+safe_message(body.get("message") or "无"))
                redirect = urlsplit(str(body.get("redirect") or "")).path
                if body.get("need2FA") or any(x in redirect for x in ("emailSignIn","smsSignIn")):
                    rejection = "仍需邮箱或短信验证，停止自动流程。"
                elif not (200 <= packet.response.status < 300 and body.get("success") is True):
                    rejection = "服务器未接受本次登录。"
                for key,header in (("security_token","x-security-token"),("csrf_token","x-csrf-token")):
                    if packet.response.headers.get(header):
                        login_tokens[key] = packet.response.headers[header]
            else:
                rejection = "登录接口没有返回 JSON。"
        else:
            log("未捕获正常登录响应，将通过账号接口校验，不能仅凭页面跳转认定成功。")
    finally:
        try:
            page.listen.stop()
        except ContextLostError:
            log("停止监听时页面正在跳转，继续等待新页面。")
    if rejection:
        return fail(rejection)
    return verify_and_attend(login_tokens)

def wait_current_document():
    """Wait for a stable document; never navigate or resubmit a request."""
    deadline = time.monotonic()+25
    previous = None
    stable = 0
    while time.monotonic() < deadline:
        try:
            state = page.run_js("return {url:location.href,ready:document.readyState,epoch:performance.timeOrigin};", timeout=4)
            if not isinstance(state,dict):
                return False
            if not origin_ok(state.get("url", "")):
                return False
            signature = (state.get("url"),state.get("epoch"))
            if state.get("ready") == "complete":
                stable = stable+1 if signature == previous else 1
                if stable >= 3:
                    return True
            else:
                stable = 0
            previous = signature
        except ContextLostError:
            previous = None
            stable = 0
        time.sleep(1)
    return False

def verify_and_attend(login_tokens=None):
    global stage
    stage = "等待登录跳转结束"
    if not wait_current_document():
        return fail("当前 NodeSeek 页面未稳定，未提交签到；可稍后使用 --finish-current 重试。")
    must_origin()
    if any(x in urlsplit(page.url).path for x in ("emailSignIn","smsSignIn")):
        return fail("页面要求邮箱或短信验证，停止自动流程。")
    if challenge_page():
        return fail("当前仍是整页验证页面，未提交签到。")
    # Keep tokens already saved by the site's login handler. Only fill missing
    # values from this login response, never from the old on-disk session.
    stage = "检查当前设备令牌"
    for attempt in range(3):
        try:
            state = page.run_js("""
            if(location.origin !== 'https://www.nodeseek.com') return {ok:false};
            for(const [k,v] of Object.entries(arguments[0]))
              if(!localStorage.getItem(k)) localStorage.setItem(k,v);
            return {ok:!!localStorage.getItem('security_token') && !!localStorage.getItem('csrf_token')};
            """, login_tokens or {}, timeout=4)
            break
        except ContextLostError:
            if attempt == 2 or not wait_current_document():
                return fail("页面持续跳转，未提交签到；稍后使用 --finish-current 重试。")
    if not isinstance(state,dict) or not state.get("ok"):
        return fail("浏览器当前设备令牌不完整，未提交签到，不用旧令牌覆盖。")
    stage = "账号校验与签到"
    log("[6/6] 校验新会话……")
    for attempt in range(3):
        try:
            must_origin()
            account = fetch_api("/api/notification/unread-count","GET")
            break
        except ContextLostError:
            if attempt == 2 or not wait_current_document():
                return fail("账号校验时页面持续跳转，未提交签到。")
    body = account.get("body")
    if not isinstance(body,dict): body = {}
    verified = (account.get("status")==200 and not account.get("challenge")
                and body.get("success") is not False and isinstance(body.get("unreadCount"),dict))
    log("账号校验："+str(verified)+"；HTTP："+str(account.get("status")))
    if not verified:
        log("账号消息："+safe_message(body.get("message")))
        return fail("新会话未通过校验，旧状态不覆盖。")
    stage = "提交签到（不自动重发）"
    must_origin()
    attendance = fetch_api("/api/attendance?random=true","POST")
    body = attendance.get("body")
    if not isinstance(body,dict): body = {}
    message = str(body.get("message") or "")
    status = attendance.get("status")
    done = (not attendance.get("challenge") and (
        (isinstance(status,int) and 200 <= status < 300 and body.get("success") is True)
        or (status == 500 and message.strip()=="今天已完成签到，请勿重复操作")))
    log("签到 HTTP 状态："+str(status))
    log("签到消息："+safe_message(message))
    if not done:
        return fail("本次未确认签到成功，旧状态不覆盖。")
    stage = "读取已确认的浏览器会话"
    jar = {}
    for c in page.run_cdp("Network.getCookies",urls=[BASE+"/api/attendance"]).get("cookies",[]):
        name, value = c.get("name"),c.get("value")
        if name in jar and jar[name] != value:
            return fail("发现同名不同值 Cookie，停止保存。")
        if name and value:
            jar[name] = value
    if not jar.get("session"):
        return fail("缺少 session Cookie，停止保存。")
    tokens = page.run_js("return {security_token:localStorage.getItem('security_token'),csrf_token:localStorage.getItem('csrf_token')};",timeout=4)
    emit("result",ok=True,account_verified=True,attendance_ok=True,
         cookie="; ".join(k+"="+v for k,v in jar.items()),tokens=tokens)
    return 0

try:
    code = main()
except Exception as exc:
    log("阶段："+stage+"；异常类型："+type(exc).__name__)
    screenshot()
    emit("result",ok=False)
    code = 1
finally:
    signal.alarm(0)
sys.exit(code)
'''


DIAGNOSTIC_WORKER = r'''
# NS_BROWSER_RECOVER_WORKER_V1
import json, os, re, signal, sys, time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit
import requests
import websocket

def emit(kind, **values):
    print(json.dumps(dict(kind=kind, **values), ensure_ascii=False), flush=True)

def log(message):
    emit("log", message=message)

def expired(signum, frame):
    log("只读诊断超过75秒，停止。")
    emit("result", ok=False)
    os._exit(124)

signal.signal(signal.SIGALRM, expired)
signal.alarm(75)
emit("hello", pid=os.getpid())

CF_HOSTS = {"challenges.cloudflare.com", "challenges.cloudflare-cn.com"}
ws = None
serial = 0
sessions = {}
contexts = {}
requests_seen = {}
network_counts = Counter()
console_counts = Counter()
error_codes = set()
capability_errors = Counter()
issue_summaries = Counter()

def safe_resource(value):
    # Never output paths, query strings, fragments, usernames or passwords.
    value = str(value or '')
    if value in ('inline','eval','self','data','blob'):
        return value
    try:
        u = urlsplit(value)
        if u.scheme in ('http','https') and u.hostname:
            return u.scheme+'://'+u.hostname
        if u.scheme in ('data','blob'):
            return u.scheme+':(内容隐藏)'
    except ValueError:
        pass
    return '(未提供或非网址)'

def safe_enum(value):
    value = str(value or '')
    return value if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,100}',value) else '(未知)'

def record_issue(item):
    if len(issue_summaries)<80:
        issue_summaries[json.dumps(item,ensure_ascii=False,sort_keys=True)] += 1

def audit_issue(issue):
    details = issue.get('details') or {}
    csp = details.get('contentSecurityPolicyIssueDetails')
    if isinstance(csp,dict):
        record_issue({'来源':'浏览器结构化问题','类别':'CSP',
            '资源':safe_resource(csp.get('blockedURL')),
            '策略指令':safe_enum(str(csp.get('violatedDirective') or '').split(' ')[0]),
            '仅报告':csp.get('isReportOnly'),
            '违规类型':safe_enum(csp.get('contentSecurityPolicyViolationType')),
            '脚本来源':safe_resource((csp.get('sourceCodeLocation') or {}).get('url'))})
    cors = details.get('corsIssueDetails')
    if isinstance(cors,dict):
        record_issue({'来源':'浏览器结构化问题','类别':'CORS',
            '资源':safe_resource((cors.get('request') or {}).get('url')),
            '发起源':safe_resource(cors.get('initiatorOrigin')),
            '仅警告':cors.get('isWarning'),
            '错误类型':safe_enum((cors.get('corsErrorStatus') or {}).get('corsError'))})

def classify(url):
    u = urlsplit(url)
    if u.hostname not in CF_HOSTS:
        return None
    if u.path.endswith("/api.js"):
        return "Turnstile脚本"
    if "/turnstile/v0/" in u.path:
        return "Turnstile组件资源"
    if "/cdn-cgi/challenge-platform/" in u.path:
        return "Challenge平台请求"
    return "其他Cloudflare验证请求"

def console_summary(text, is_cf=False):
    lower = text.lower()
    if any(s in lower for s in ('content security policy','violates the following','cors policy','cross-origin request blocked')):
        # Structured issues are preferred. This fallback keeps only public origins
        # and recognized directive names, never raw console strings.
        origins = sorted({safe_resource(u) for u in re.findall(r'https?://[^\s\x22\x27<>]+',text)})[:8]
        directives = sorted(set(re.findall(r'\b(?:default-src|script-src(?:-elem|-attr)?|style-src(?:-elem|-attr)?|connect-src|frame-src|img-src|font-src|worker-src|frame-ancestors)\b',lower)))
        record_issue({'来源':'控制台摘要','类别':'CORS' if 'cors' in lower or 'cross-origin' in lower else 'CSP',
            '涉及的源':origins,'策略指令':directives,
            '含report-only标记':'report-only' in lower or 'report only' in lower,
            '含inline标记':'inline' in lower,'含eval标记':'eval' in lower})
    cf = is_cf or "turnstile" in lower or "cloudflare" in lower
    if cf:
        codes = re.findall(r"\b(?:100|110|200|300|400|600)\d{3}\b", text)
        error_codes.update(codes)
        console_counts["Cloudflare/Turnstile控制台消息"] += 1
    for key, words in {
        "内容安全策略CSP": ("content security policy", "violates the following", "connect-src", "script-src"),
        "脚本MIME类型错误": ("mime type", "strict mime"),
        "跨域CORS错误": ("cors policy", "cross-origin request blocked"),
        "脚本未捕获异常": ("uncaught",),
    }.items():
        if any(word in lower for word in words):
            console_counts[key] += 1
    for code in re.findall(r"\bnet::ERR_[A-Z_]+\b", text):
        console_counts[code] += 1

def on_event(event):
    method = event.get("method", "")
    p = event.get("params") or {}
    sid = event.get("sessionId", "")
    target = sessions.get(sid, {})
    cf = target.get("host") in CF_HOSTS
    if method == 'Audits.issueAdded':
        audit_issue(p.get('issue') or {})
    elif method == 'Target.detachedFromTarget':
        dead = p.get('sessionId')
        sessions.pop(dead,None)
        for key in list(contexts):
            if key[0]==dead: contexts.pop(key,None)
    elif method == "Runtime.executionContextCreated":
        c = p.get("context") or {}
        contexts[(sid,c.get("id"))] = c
    elif method == "Runtime.executionContextDestroyed":
        contexts.pop((sid,p.get("executionContextId")),None)
    elif method == "Runtime.executionContextsCleared":
        for key in list(contexts):
            if key[0] == sid: contexts.pop(key,None)
    elif method == "Runtime.consoleAPICalled":
        if p.get("type") in ("error", "warning", "warn", "assert"):
            text = " ".join(str(a.get("value",a.get("description",""))) for a in p.get("args",[]))
            console_summary(text,cf)
    elif method == "Runtime.exceptionThrown":
        detail = p.get("exceptionDetails") or {}
        exc = detail.get("exception") or {}
        console_summary(str(detail.get("text",""))+" "+str(exc.get("description","")),cf)
        console_counts["捕获到JavaScript异常"] += 1
    elif method == "Log.entryAdded":
        e = p.get("entry") or {}
        if e.get("level") in ("warning","error"):
            console_summary(str(e.get("text","")),cf)
    elif method == "Network.requestWillBeSent":
        category = classify((p.get("request") or {}).get("url",""))
        if category:
            requests_seen[(sid,p.get("requestId"))] = category
            network_counts[(category,"请求开始")] += 1
    elif method == "Network.responseReceived":
        response = p.get("response") or {}
        category = classify(response.get("url",""))
        if category:
            network_counts[(category,"HTTP "+str(response.get("status")))] += 1
    elif method == "Network.loadingFailed":
        category = requests_seen.get((sid,p.get("requestId")))
        if category:
            matches = re.findall(r"net::ERR_[A-Z_]+",str(p.get("errorText","")))
            error = matches[0] if matches else "加载失败"
            network_counts[(category,error)] += 1

def command(method, params=None, sid=None, timeout=5):
    global serial
    serial += 1
    ident = serial
    data = {"id":ident,"method":method,"params":params or {}}
    if sid: data["sessionId"] = sid
    ws.send(json.dumps(data))
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        try:
            item = json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:
            continue
        if item.get("id") == ident:
            if "error" in item:
                raise RuntimeError("cdp_command_failed")
            return item.get("result") or {}
        on_event(item)
    raise TimeoutError("cdp_command_timeout")

def attach_targets():
    infos = command("Target.getTargets").get("targetInfos",[])
    live_ids = {v['targetId'] for v in infos}
    for sid,target in list(sessions.items()):
        if target['id'] not in live_ids:
            sessions.pop(sid,None)
            for key in list(contexts):
                if key[0]==sid: contexts.pop(key,None)
    known = {v["id"] for v in sessions.values()}
    for info in infos:
        u = urlsplit(info.get("url",""))
        wanted = (info.get("type")=="page" and u.hostname=="www.nodeseek.com") or (info.get("type")=="iframe" and u.hostname in CF_HOSTS)
        if not wanted or info["targetId"] in known or len(sessions)>=8:
            continue
        try:
            sid = command("Target.attachToTarget",{"targetId":info["targetId"],"flatten":True})["sessionId"]
            sessions[sid] = {"id":info["targetId"],"host":u.hostname,"type":info["type"]}
            for method in ("Audits.enable","Runtime.enable","Log.enable","Network.enable"):
                try: command(method,sid=sid)
                except Exception: capability_errors[method] += 1
        except Exception:
            capability_errors["附加当前验证框"] += 1

SNAPSHOT = r"""
(() => {
  const cf = ['challenges.cloudflare.com','challenges.cloudflare-cn.com'].includes(location.hostname);
  if(cf) {
    const text=document.body?.innerText || '';
    return {scope:'Cloudflare验证框',ready:document.readyState,
      verifying:/verifying|正在验证/i.test(text),
      success:/success|验证成功/i.test(text),
      failure:/fail|error|失败|错误/i.test(text),
      timeout:/timed out|timeout|超时/i.test(text),
      error_codes:[...new Set(text.match(/\b(?:100|110|200|300|400|600)\d{3}\b/g)||[])]};
  }
  if(location.origin==='https://www.nodeseek.com') {
    const input=document.querySelector('[name="cf-turnstile-response"]');
    return {scope:'NodeSeek主页面',path:location.pathname,ready:document.readyState,
      stylesheets:document.styleSheets.length,
      token_present:Boolean(input?.value)};
  }
  return {scope:'其他页面，未读取内容'};
})()
"""

def snapshot_all():
    snapshots = []
    seen = set()
    for (sid,cid), context in list(contexts.items()):
        origin = context.get("origin","")
        host = urlsplit(origin).hostname or sessions.get(sid,{}).get("host")
        default = (context.get("auxData") or {}).get("isDefault")
        if not default or host not in CF_HOSTS | {"www.nodeseek.com"}:
            continue
        try:
            result = command("Runtime.evaluate",{
                "expression":SNAPSHOT,"contextId":cid,"returnByValue":True,
                "silent":True,"timeout":2500},sid=sid,timeout=4)
            value = (result.get("result") or {}).get("value")
            if isinstance(value,dict):
                key = json.dumps(value,sort_keys=True)
                if key not in seen:
                    seen.add(key); snapshots.append(value)
                error_codes.update(value.get("error_codes") or [])
        except Exception:
            capability_errors["读取当前框架状态"] += 1
    return snapshots

def main():
    global ws
    ports = set()
    for p in Path('/proc').glob('[0-9]*/cmdline'):
        try: raw=p.read_bytes()
        except OSError: continue
        ports.update(int(v) for v in re.findall(rb'--remote-debugging-port(?:=|\x00)(\d+)',raw))
    ports.discard(0)
    if len(ports)!=1:
        log('调试端口数量不唯一，停止只读诊断。')
        emit('result',ok=False); return 1
    port = next(iter(ports))
    with requests.Session() as client:
        client.trust_env=False
        r=client.get('http://127.0.0.1:'+str(port)+'/json/version',timeout=4)
        r.raise_for_status()
        url=r.json()['webSocketDebuggerUrl']
    u=urlsplit(url)
    if u.scheme!='ws' or u.hostname not in ('127.0.0.1','localhost') or u.port!=port:
        raise RuntimeError('unexpected_debug_address')
    ws=websocket.create_connection(url,timeout=1,suppress_origin=True,http_no_proxy=['127.0.0.1','localhost'])
    log('只读诊断：不刷新、不点击、不登录、不读取账号配置，也不保存 Cookie。')
    attach_targets()
    if not any(v['host']=='www.nodeseek.com' for v in sessions.values()):
        log('当前没有 NodeSeek 页面，请保留失败现场后再诊断。')
        emit('result',ok=False); return 1
    initial=snapshot_all()
    log('初始页面状态：'+json.dumps(initial,ensure_ascii=False))
    log('正在观察当前验证请求30秒……')
    deadline=time.monotonic()+30
    next_update=time.monotonic()+10
    while time.monotonic()<deadline:
        try: on_event(json.loads(ws.recv()))
        except websocket.WebSocketTimeoutException: pass
        if time.monotonic()>=next_update:
            log('仍在观察验证网络……')
            attach_targets()
            next_update=time.monotonic()+10
    final=snapshot_all()
    report={
        '观察结束的页面状态':final,
        '捕获到的Turnstile错误码':sorted(error_codes),
        '验证网络统计':[{'资源':k[0],'结果':k[1],'次数':v} for k,v in sorted(network_counts.items())],
        '控制台分类统计':dict(console_counts),
        'CSP与CORS详情':[dict(json.loads(k),次数=v) for k,v in issue_summaries.items()],
        '诊断能力限制':dict(capability_errors),
        '说明':'仅包含启用监听后可获取的事件和浏览器可能保留的控制台记录；没有记录不代表没有错误。'
    }
    log('===== 只读诊断结果 =====\n'+json.dumps(report,ensure_ascii=False,indent=2))
    emit('result',ok=True,diagnostic=True)
    return 0

try:
    code=main()
except Exception as exc:
    log('诊断异常类型：'+type(exc).__name__)
    emit('result',ok=False)
    code=1
finally:
    if ws is not None:
        try: ws.close()
        except Exception: pass
    signal.alarm(0)
sys.exit(code)
'''


def write_private(path, content):
    fd, name = tempfile.mkstemp(prefix="."+path.name+"-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_result(result):
    if not (result.get("ok") is True and result.get("account_verified") is True
            and result.get("attendance_ok") is True):
        raise ValueError("账号校验或签到未成功，不保存。")
    cookie = result.get("cookie")
    if not isinstance(cookie, str) or any(c in cookie for c in "\r\n"):
        raise ValueError("Cookie 格式异常，不保存。")
    pairs = {}
    for item in cookie.split(";"):
        name, sep, value = item.strip().partition("=")
        if not sep or not name or not value or any(c.isspace() for c in name) or name in pairs:
            raise ValueError("Cookie 格式异常或名称重复，不保存。")
        pairs[name] = value
    if not pairs.get("session"):
        raise ValueError("缺少 session，不保存。")
    tokens = result.get("tokens")
    if not isinstance(tokens, dict):
        raise ValueError("设备状态格式错误，不保存。")
    for key in ("security_token", "csrf_token"):
        value = tokens.get(key)
        if not isinstance(value, str) or not value or any(c in value for c in "\r\n"):
            raise ValueError("设备令牌不完整，不保存。")
    return cookie, {k: tokens[k] for k in ("security_token", "csrf_token")}


def save_result(data, owner, original, result):
    cookie, tokens = validate_result(result)
    path = data / "session_state.json"
    if path.read_bytes() != original:
        raise ValueError("运行期间磁盘状态发生变化，停止覆盖。")
    backup = data / ("session_state.before-browser-"+time.strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid())+".json")
    write_private(backup, original.decode("utf-8"))
    payload = {"version":1, "owner":owner, "cookie":cookie, "tokens":tokens}
    write_private(path, json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        write_private(data / "cookie.txt", cookie)
    except OSError:
        print("主状态已保存，但兼容文件 cookie.txt 未更新。", flush=True)
    return backup


def stop_worker(container, pid):
    if not isinstance(pid, int) or pid <= 1:
        return
    code = ("import os,signal; from pathlib import Path; "
            f"p={pid}; a=Path('/proc')/str(p)/'cmdline'; "
            f"s=a.read_bytes() if a.exists() else b''; "
            f"os.kill(p,signal.SIGTERM) if {MARKER.encode()!r} in s else None")
    try:
        subprocess.run(["docker","exec",container,"/app/venv/bin/python","-c",code],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
    except (OSError, subprocess.SubprocessError):
        pass


def run_worker(container, bundle, worker=WORKER):
    events = queue.Queue()
    process = subprocess.Popen(
        ["docker","exec","-i",container,"/app/venv/bin/python","-u","-c",worker],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        text=True,encoding="utf-8",errors="replace",bufsize=1)
    def read_stdout():
        try:
            for line in process.stdout:
                events.put(line)
        finally:
            events.put(None)
    threading.Thread(target=read_stdout,daemon=True).start()
    pid, result = None, None
    try:
        process.stdin.write(json.dumps(bundle,ensure_ascii=False))
        process.stdin.close()
        deadline = time.monotonic()+400
        while time.monotonic() < deadline:
            try:
                line = events.get(timeout=1)
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                event=json.loads(line)
            except ValueError:
                # Never echo arbitrary library/exception output: it may contain secrets.
                continue
            if not isinstance(event,dict):
                continue
            if event.get("kind")=="hello":
                pid=event.get("pid")
            elif event.get("kind")=="log":
                print(event.get("message",""),flush=True)
            elif event.get("kind")=="result":
                result=event
        else:
            print("主机等待超时，停止本次测试。",flush=True)
            stop_worker(container,pid)
            return None
        code = process.wait(timeout=5)
        if code != 0:
            print("测试进程退出码：",code,flush=True)
            return None
        return result
    finally:
        if process.poll() is None:
            stop_worker(container,pid)
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",default="/opt/ns-sign/data")
    parser.add_argument("--container",default="cloudflyer")
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument("--diagnose",action="store_true",help="只读诊断当前浏览器验证错误，不加载账号配置，不刷新或登录")
    mode.add_argument("--resume",action="store_true",help="沿用当前已填写的登录页和验证码，提交登录、校验账号、签到并保存成功状态")
    mode.add_argument("--finish-current",action="store_true",help="直接校验当前浏览器登录状态、签到并保存成功状态；不刷新、不重新登录")
    args=parser.parse_args()
    if args.diagnose:
        print("NodeSeek 浏览器只读诊断 v"+VERSION,flush=True)
        result=run_worker(args.container,{},worker=DIAGNOSTIC_WORKER)
        return 0 if result and result.get("ok") is True and result.get("diagnostic") is True else 1
    data=Path(args.data_dir)
    os.umask(0o077)
    print("NodeSeek 浏览器恢复测试 v"+VERSION,flush=True)
    with (data/"session-state.lock").open("a") as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print("另一个签到任务正在使用状态文件，请稍后运行。")
            return 1
        cfg=json.loads((data/"config.json").read_text(encoding="utf-8"))
        if not all(isinstance(cfg.get(k),str) and cfg[k] for k in ("user","password","key")):
            raise ValueError("配置缺少有效的 user/password/key。")
        original=(data/"session_state.json").read_bytes()
        state=json.loads(original)
        owner=hashlib.sha256(cfg["user"].strip().lower().encode()).hexdigest()
        if state.get("owner") != owner:
            raise ValueError("配置账号与保存状态不匹配，停止。")
        tokens=state.get("tokens") or {}
        if not all(isinstance(tokens.get(k),str) and tokens[k] for k in ("security_token","csrf_token")):
            raise ValueError("保存的设备令牌不完整，停止。")
        # cfg/state travel through stdin only, not shell interpolation or command arguments.
        bundle={k:cfg[k] for k in ("user","password","key")}
        bundle["tokens"]={k:tokens[k] for k in ("security_token","csrf_token")}
        bundle["resume"]=args.resume
        bundle["finish_current"]=args.finish_current
        result=run_worker(args.container,bundle)
        if not result or result.get("ok") is not True:
            print("本次未完成恢复；磁盘旧会话未覆盖。",flush=True)
            return 1
        backup=save_result(data,owner,original,result)
        print("签到成功或今日已签到；新 Cookie 和设备令牌已保存。",flush=True)
        print("旧会话备份：",backup,flush=True)
        print("注意：原 systemd 定时任务尚未改用浏览器流程。",flush=True)
        return 0


if __name__=="__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消；未确认成功的状态不会保存。")
        sys.exit(130)
    except Exception as exc:
        print("主机端异常类型：",type(exc).__name__)
        if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError):
            print(str(exc))
        sys.exit(1)
