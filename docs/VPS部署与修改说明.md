# Oracle AMD VPS：NodeSeek 登录与定时签到

本分支把配套项目：[wipoman/cloudflyer-oss](https://github.com/wipoman/cloudflyer-oss)。

## 修改内容

| 文件 | 修改及原因 |
| --- | --- |
| `nodeseek_sign.py` | 登录 JSON 删除 `token/source`，改用 `x-captcha-token/x-captcha-source` 请求头；拒绝把邮箱、短信或二次验证跳转当作登录成功；要求非空 `session`；添加登录超时及禁止自动跳转；关闭验证码详细输出。保留原 GitHub Actions/青龙、多账号、通知和统计逻辑。 |
| `local_run.py` | 新增 VPS 单账号入口。保存 Cookie、`security_token`、`csrf_token`；携带设备请求头；吸收响应中的非空更新令牌；已有 Cookie 优先，缺失或失效时重新登录。 |
| `local_run.py` | 登录不只判断 `success`，还检查验证跳转、新 `session` Cookie，并通过只读 `/api/notification/unread-count` 接口校验。失败不覆盖已保存状态。 |
| `local_run.py` | 提供 `import-state/check/login-test/run`；重新登录测试不带旧 Cookie；文件锁避免并发写入；状态文件权限 600，使用原子替换。 |
| `local_run.py` | 特别处理 HTTP 500 且消息恰为“今天已完成签到，请勿重复操作”；其他 500 不作为签到成功。这修复的是本地适配初版过严的状态码判断，上游原 `sign()` 已能识别“已完成签到”。 |
| `Dockerfile`、`requirements-vps.txt`、`init_config.py` | 构建本地一次性任务镜像，初始化本地配置；镜像只复制运行所需文件。 |
| `.gitignore`、`.dockerignore` | 排除账号配置、Cookie、令牌、诊断私有文件与本地数据。 |
| `deploy/systemd/` | 每天执行；错过时补执行。收尾先确认容器存在，避免正常退出后出现 `No such container` 噪声。 |
| `tests/` | 无真实账号、无需网络的登录与签到回归测试。 |

最终 VPS 入口是 `local_run.py`，复用原 `turnstile_solver.py`，不运行 `nodeseek_sign.py` 主流程。它固定调用本机 `http://127.0.0.1:3000`，签到使用 `random=true`，未迁移多账号、通知、收益统计或 YesCaptcha 选择功能。

## 1. 前提

- Linux VPS 已安装 Docker、Git；以下命令用 root 执行。
- CloudFlyer 已按配套仓库文档启动，宿主机 `127.0.0.1:3000` 可访问。
- 浏览器已完成本人 NodeSeek 登录，必要时先手动完成邮箱验证。
- CloudFlyer 的真人验证令牌不等于 NodeSeek 登录 Cookie；账号登录和保存状态由本脚本完成。

## 2. 下载和构建

源码目录使用 `/opt/ns-sign-src`，数据目录沿用 `/opt/ns-sign/data`，避免与早期手工部署目录冲突。

```bash
git clone https://github.com/wipoman/ns-sign.git /opt/ns-sign-src
cd /opt/ns-sign-src
docker build -t ns-sign-local:latest .
install -d -m 700 /opt/ns-sign/data
```

已经克隆的源码目录不必再 clone，更新时使用下方“已有 VPS 升级”步骤。

## 3. 初始化配置（仅首次）

```bash
docker run --rm -it \
  -v /opt/ns-sign/data:/data \
  ns-sign-local:latest \
  python /app/init_config.py
```

按提示输入用户名或邮箱、密码、CloudFlyer 客户端密钥。按本次部署偏好，输入会明文显示；直接输入原值，不额外加引号。文件已存在时初始化程序拒绝覆盖。早期部署已有 `config.json` 可直接沿用。

配置结构仅供说明，不要把真实值提交到仓库：

```json
{"user": "你的用户名或邮箱", "password": "你的密码", "key": "你的CloudFlyer密钥"}
```

JSON 字符串需要双引号；交互输入时不需要自己加引号。

## 4. 导入浏览器登录状态（仅首次或需要人工恢复时）

在同一个已登录浏览器、同一个 NodeSeek 账号中复制：

1. 开发者工具 Network → 发往 `https://www.nodeseek.com` 的已登录请求 → Request Headers 中的完整 `Cookie` 值，须包含 `session`。
2. Application → Local Storage → `https://www.nodeseek.com` → `security_token` 的值。
3. 同一位置的 `csrf_token` 的值。

```bash
docker run --rm -it \
  -v /opt/ns-sign/data:/data \
  ns-sign-local:latest \
  python /app/local_run.py import-state
```

输入明文可见，不加引号，不把这些值粘贴到 GitHub Issue 或聊天。导入只是保存，尚未证明状态在 VPS 上有效。

## 5. 验证签到与重新登录

先检查导入的 Cookie。此命令会实际签到；当天签到过则报告已签到：

```bash
docker run --rm --network host \
  -v /opt/ns-sign/data:/data \
  ns-sign-local:latest python /app/local_run.py check
```

再单独测试重新登录。它不携带旧 Cookie，只带设备令牌和新获取的验证码；失败不覆盖原状态：

```bash
docker run --rm --network host \
  -v /opt/ns-sign/data:/data \
  ns-sign-local:latest python /app/local_run.py login-test
```

出现 `新会话已通过账号接口校验，Cookie 和设备令牌已保存。` 才能确认本次重新登录成功。仅 `check` 成功不能证明自动续登录成功。不要每天重复运行 `login-test`。

正常签到命令：

```bash
docker run --rm --name ns-sign-job --network host \
  -v /opt/ns-sign/data:/data \
  ns-sign-local:latest
```

默认流程：已有 Cookie → 签到；Cookie 缺失或判定失效 → CloudFlyer 验证 → 携带设备状态登录 → 校验新会话 → 保存 → 签到。非登录失效的普通网络或接口错误直接报告失败，避免无意义地反复登录。

## 6. 设置每天两次定时任务

```bash
cd /opt/ns-sign-src
install -m 644 deploy/systemd/ns-sign.service /etc/systemd/system/ns-sign.service
install -m 644 deploy/systemd/ns-sign.timer /etc/systemd/system/ns-sign.timer
systemctl daemon-reload
systemctl enable --now ns-sign.timer
systemctl restart ns-sign.timer
systemctl is-enabled ns-sign.timer
systemctl status ns-sign.timer --no-pager -l
systemctl list-timers --all ns-sign.timer
```

应看到 `enabled`、`active (waiting)` 和具体下一次执行时间。定时器明确使用 `Asia/Shanghai`，每天 00:10、08:10 各执行一次；列表只显示最近的一次。第一次成功后第二次报告“已完成签到”是正常的。`Persistent=true` 只会为停机期间错过的任务补执行一次，不会重放每一个错过的时间点。

立即验证服务及查看日志：

```bash
systemctl start ns-sign.service
journalctl -u ns-sign.service -n 40 --no-pager
journalctl -u ns-sign.service --since today --no-pager
```

`ns-sign.service` 执行后为 `inactive (dead)` 可以正常；应检查退出结果及 timer 的等待状态。无需保持 SSH 连接。上游 `.github/workflows/blank.yml` 是独立的 GitHub Actions 定时流程，不会使用 VPS 的 `/data`；若此前启用了该流程又只想由 VPS 执行，请在 GitHub Actions 中停用旧签到工作流。

## 7. 已有 VPS 升级与回滚

本次更新 GitHub 不会自动改变正在运行的 VPS。已经稳定运行时无需立即重建 CloudFlyer。更新 ns-sign 时：

1. 暂停 timer，并确认服务没有正在运行。
2. 备份旧镜像、数据与现有 unit 文件；备份目录不要放进仓库。
3. 如果 `/opt/ns-sign-src` 尚不存在，按第 2 节 clone；已经存在则执行 `git -C /opt/ns-sign-src pull --ff-only`。
4. 构建新镜像，使用现有 `/opt/ns-sign/data` 运行 `check`，不必重新初始化或导入。
5. 按第 6 节安装 unit 并重新启用 timer。

参考命令（备份名已存在时请改用新的名字）：

```bash
systemctl stop ns-sign.timer
systemctl is-active ns-sign.service
docker tag ns-sign-local:latest ns-sign-local:before-repo-update
install -d -m 700 /root/ns-sign-backup
cp -a /opt/ns-sign/data /root/ns-sign-backup/data
cp -a /etc/systemd/system/ns-sign.service /etc/systemd/system/ns-sign.timer /root/ns-sign-backup/
docker build -t ns-sign-local:latest /opt/ns-sign-src
docker run --rm --network host -v /opt/ns-sign/data:/data ns-sign-local:latest python /app/local_run.py check
```

若服务显示 `activating`，等本次任务结束后再更新。回滚镜像可执行 `docker tag ns-sign-local:before-repo-update ns-sign-local:latest`，然后恢复备份的 unit、执行 `systemctl daemon-reload` 与 `systemctl restart ns-sign.timer`。通常不必恢复旧 Cookie；新版本保存的当前会话仍可使用。

## 8. 数据、诊断与限制

| 宿主机文件 | 内容 |
| --- | --- |
| `/opt/ns-sign/data/config.json` | 账号、密码、CloudFlyer 密钥；权限 600 |
| `/opt/ns-sign/data/session_state.json` | Cookie、两个设备令牌、账号标识；权限 600 |
| `/opt/ns-sign/data/cookie.txt` | 为早期本地入口保留的 Cookie 文件 |
| `/opt/ns-sign/data/session_state.previous.json` | 再次导入前的旧状态备份 |
| `/opt/ns-sign/data/login-summary.json` | 不包含令牌值的登录摘要 |
| `/opt/ns-sign/data/session-state.lock` | 防并发锁文件 |

响应只返回新 CSRF 令牌、没有返回新 security 令牌时，保留已有 security 令牌。脚本保存服务端返回的 Cookie/令牌，不会自行延长服务端会话期限。当前用 Cookie 字符串持久化，不是完整浏览器用户目录，也不持久保存完整 Cookie 属性或 Local Storage 的其他字段。

- 实测：无旧 Cookie 的重新登录返回 `pjwt/session/smac`，账号接口确认有效，无邮箱跳转；重复签到 HTTP 500 正确处理；systemd 服务成功。
- 初次浏览器登录和状态导入仍需本人完成。设备状态可复用是本次环境的实测结果，不能保证所有账号、IP 或未来风控策略都适用。
- 没有实现动态 `x-integrity-token`，没有实现邮箱/短信验证码读取。返回 `/emailSignIn.html`、`/smsSignIn.html` 或 `need2FA` 时停止并保留原状态。
- 保留 `curl_cffi` 的 `chrome110` impersonation；这是 HTTP 客户端配置，不是 CloudFlyer 内 Chrome 的实际版本。
- 本地测试：`python3 -m unittest discover -s tests -v`，不调用真实站点、不使用真实账号。仓库新增 Docker 构建配方在本次整理环境中未实际构建，部署时需用第 5 节命令确认。
