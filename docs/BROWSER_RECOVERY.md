# Cloudflare 拦截后的浏览器会话恢复

更新日期：2026-11-22。配套仓库：[wipoman/cloudflyer-oss](https://github.com/wipoman/cloudflyer-oss)。

## 本次修复和验证范围

新增宿主机脚本 `ns_browser_recover.py`，版本 **1.5**。它使用本机 Docker 中 CloudFlyer 已有的 Chrome，不另启一套浏览器。原 `local_run.py`、Docker 默认入口、通知入口和 systemd 定时任务不变。

| 改动 | 作用 |
| --- | --- |
| 实际 DISPLAY 探测和桌面点击 | 从 Chrome 进程环境读取 DISPLAY/XAUTHORITY，避免容器默认 `:1` 与实际 `:0` 不一致；结合窗口位置计算坐标，不把某次截图坐标写死到脚本。 |
| `--resume` | 保留当前登录页和已取得的令牌，检查表单与配置一致后提交一次登录；不重新创建任务、刷新或清空 Cookie。 |
| 跳转等待 | 登录响应后等待文档稳定；只读账号校验遇到 `ContextLostError` 时有限重试，签到 POST 不自动重发。 |
| `--finish-current` | 已登录但后续处理出错时，直接校验当前会话、签到、保存，不重新提交账号密码。 |
| 安全保存 | 校验账号有效且签到成功/今日已签到后才保存 Cookie 和设备令牌；检查状态文件是否被其他任务修改，备份旧状态，文件权限 600。 |
| `--diagnose` | 不读取账号配置、不点击、不登录；只观察当前浏览器的验证状态、网络和控制台分类，输出中隐藏令牌值。 |

本次 VPS 实测链路：手动补点一次验证框 → `--resume` 登录响应 200、跳转首页 → v1.4 在跳转期间发生 `ContextLostError` → v1.5 `--finish-current` 账号校验 200、签到 200（2 个鸡腿）、保存新状态 → 原 `local_run.py check` 与 systemd 服务均成功识别今日已签到。

**这不是已验证的无人值守续登录方案。** 本次恢复仍需要一次人工补点；v1.5 的 `--finish-current` 已有实机成功记录，完整流程从头到尾自动通过、重启后的浏览器会话恢复、未来 Cookie 失效后的自动恢复尚未验证。没有把本脚本自动接到 403 错误分支或定时任务中。

## 前提

- Linux 宿主机有 Python 3、Docker，使用有权访问 Docker 的用户；下列命令按 root 使用。
- CloudFlyer 容器名默认 `cloudflyer`，API 为宿主机 `127.0.0.1:3000`；使用配套仓库更新后的 `Dockerfile.vps`。容器内需有 `/app/venv/bin/python`、DrissionPage、requests、websocket-client、xdotool。
- `/opt/ns-sign/data/config.json` 已有 `user/password/key`；`session_state.json` 已有匹配账号的 owner 标识及两个设备令牌。首次导入按 [原部署说明](VPS部署与修改说明.md) 操作。
- 恢复时不要同时向 CloudFlyer 提交其他任务，也不要同时运行签到任务；宿主机文件锁只能协调使用同一锁的本地脚本，不能锁住其他 CloudFlyer 客户端。
- 当前桌面点击实现要求唯一浏览器、唯一匹配窗口、缩放/DPR 为 1、无窗口装饰造成的内容区偏移；不满足时会停止。

## 安装脚本

源码已在 `/opt/ns-sign-src` 时：

```bash
git -C /opt/ns-sign-src pull --ff-only
install -m 600 /opt/ns-sign-src/ns_browser_recover.py /root/ns_browser_recover.py
python3 /root/ns_browser_recover.py --help
```

尚无源码目录时先执行 `git clone https://github.com/wipoman/ns-sign.git /opt/ns-sign-src`。此脚本运行在宿主机，不复制进 ns-sign 任务镜像；仅更新它不需要重建 ns-sign 镜像。已经恢复成功的 VPS 不必重新登录或重新创建 CloudFlyer。

## 按当前阶段选择一个命令

### 1. 从头进行受控恢复

仅在当前会话确实需要恢复时使用：会创建登录页验证任务，恢复设备状态，清理浏览器旧登录 Cookie，填写账号并尝试一次验证框点击，最多等待 45 秒。

```bash
python3 /root/ns_browser_recover.py
```

未取得令牌时不提交登录、不覆盖磁盘旧会话。页面会保留；不要为了等待令牌不断重跑完整流程。通过当前浏览器完成验证后使用下一条命令。

### 2. 当前登录页已经取得令牌，继续登录

```bash
python3 /root/ns_browser_recover.py --resume
```

此模式要求上次脚本准备好的表单仍在且内容与配置一致。验证码有时效；字段非空不等于服务端一定接受。不要先花几十秒运行诊断，再提交登录。若令牌缺失，脚本停止，需先在当前页面完成验证。

### 3. 已跳转首页或登录后出现 ContextLostError

保留当前浏览器，不刷新、不重启容器，执行：

```bash
python3 /root/ns_browser_recover.py --finish-current
```

只有账号接口确认登录有效后才签到；邮箱/短信验证页、账号无效、普通网络错误或签到结果不明都会停止，不覆盖旧状态。该命令会实际签到，不是只读检查。

### 4. 只读诊断

```bash
python3 /root/ns_browser_recover.py --diagnose
```

观察约 30 秒；控制台可能含历史记录。CSP/CORS 消息或无网络事件不能单独证明验证失败。根路径 API 返回 404 也只代表 HTTP 服务响应，不证明浏览器任务成功。

失败现场截图保存在容器 `/tmp/ns-browser-recovery.png`，表单文字已隐藏。截图仍可能包含其他页面信息，只在确需排查时私下查看，不提交仓库。

## 成功后验证原定时入口

```bash
docker run --rm --network host \
  -v /opt/ns-sign/data:/data \
  ns-sign-local:latest python /app/local_run.py check
systemctl start ns-sign.service
journalctl -u ns-sign.service --since "5 minutes ago" --no-pager
systemctl status ns-sign.timer --no-pager -l
```

签到收益消息或 HTTP 500 且消息恰为“今天已完成签到，请勿重复操作”表示业务成功。其他 500/403 不应当作成功。一次性 service 成功后显示 `inactive (dead)` 正常；timer 应为 `enabled`、`active (waiting)`。原日程仍是北京时间 00:10/08:10。

旧手工 service 的 `ExecStopPost=docker stop` 可能在 `--rm` 已删除容器后输出 `No such container`；主进程退出 0 且服务 `Succeeded` 时，这不是签到失败。仓库现有 service 已带存在性判断，本轮未改动 unit。

## 故障结论与数据边界

- 已捕获的漏签到直接原因是签到接口返回 Cloudflare 403 HTML；定时器有按时触发。
- 另有旧 Cookie 返回 `USER NOT FOUND`、第三方 DNS 解析失败、浏览器短时繁忙和登录跳转时序错误，不能把所有 403 归为 Cookie 过期。
- Cookie、两个设备令牌和备份仅保存在 `/opt/ns-sign/data`；不打印真实值，不上传 GitHub。保存的是 Cookie 字符串，不是完整浏览器用户目录。
- 成功恢复后原 HTTP 脚本当前可用，不代表今后不会再次触发 Cloudflare，也不保证跳过邮箱或短信验证。
- 更新 GitHub 不会更新 Docker 引擎、正在运行的容器或你的定时任务。容器内手工补丁需通过配套镜像构建固化，重建迁移见 CloudFlyer 文档。

## 离线测试

```bash
python3 -m unittest discover -s tests -v
```

测试不连接 NodeSeek、不使用真实账号，不能替代实际站点验证。
