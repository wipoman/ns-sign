# NodeSeek-Signin

<div align="center">
  
![NodeSeek](https://img.shields.io/badge/NodeSeek-自动签到-green)
![GitHub stars](https://img.shields.io/github/stars/yowiv/NodeSeek-Signin?style=flat)
![Python](https://img.shields.io/badge/Language-Python-blue)
![License](https://img.shields.io/github/license/yowiv/NodeSeek-Signin)

</div>

## 📝 项目介绍

这是一个用于 NodeSeek 论坛自动签到的工具。本 fork 新增 Ubuntu AMD64 VPS 本地部署、设备状态持久化与受控浏览器恢复，原上游 GitHub Actions / 青龙入口继续保留。签到模式默认为随机签到，帮助用户轻松获取论坛每日"鸡腿"奖励。


> 下列功能包含原上游入口的能力；本页 VPS 安装使用单账号 `local_run.py`，不等同于上游的通知、多账号和 GitHub 变量保存流程。

## ✨ 功能特点

- 📅 支持 GitHub Actions 自动运行
- 🦉 支持青龙面板定时任务
- 🍪 支持 Cookie 或账号密码登录方式
- 👥 支持多账号批量签到
- 🔐 支持多种验证码解决方案
  - 自建 CloudFreed 服务（免费）
  - YesCaptcha 商业服务（付费/赠送）
- 📱 支持多种通知推送渠道
- 🔄 自动更新Cookie并保存至GitHub变量

## 🚀 使用方法：全新 VPS 从零安装

本教程配套 [wipoman/cloudflyer-oss](https://github.com/wipoman/cloudflyer-oss)，适用于 **Ubuntu 22.04 / 24.04、AMD64（x86_64）、systemd、Docker**。Oracle AMD VPS 可按此安装；当前 `Dockerfile.vps` **不是 ARM64 安装方案**，不要直接用于 Oracle ARM 实例。

部署后：CloudFlyer 常驻运行；ns-sign 在每天**北京时间 08:00** 各执行一次，状态保存在本机。以下按单账号部署，不需要 GitHub PAT、GitHub Secrets 或青龙面板。原上游 GitHub Actions / 青龙用法已折叠到本页下方。

> 首次需要在自己的浏览器完成 NodeSeek 登录，必要时完成邮箱/短信验证，再导入 Cookie 和设备令牌。日常任务会复用会话，但不保证未来遇到 Cloudflare 或账号二次验证时能无人值守恢复。VPS 入口尚未迁移上游多账号、通知和收益统计功能。

### 第 1 步：进入 root，检查系统

如果通过 `ubuntu` 用户登录，先执行一次；已是 root 可跳过：

```bash
sudo -i
```

然后执行：

```bash
cat /etc/os-release
uname -m
free -h
df -h /
```

确认系统是上述 Ubuntu 版本、架构为 `x86_64`。浏览器和构建镜像需要额外内存、磁盘空间；建议至少 2 GB 内存并预留数 GB 磁盘。本次 1 GB 内存 VPS 配合 2 GB Swap 曾成功运行，也曾出现浏览器卡顿，不能保证同配置始终稳定。

**以下每个代码块分开复制执行，出现错误先处理，不要把下一段命令接在上一段末尾。** 不复制终端中的 `root@...#` 或 `>` 提示符。

### 第 2 步：安装基础工具和 Docker

```bash
apt-get update
apt-get install -y ca-certificates curl git python3
```

全新、尚未安装 Docker 的 Ubuntu，按 [Docker 官方 APT 安装方法](https://docs.docker.com/engine/install/ubuntu/) 配置软件源：

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
```

```bash
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
```

```bash
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker --version
docker run --rm hello-world
```

看到 `Hello from Docker!` 表示 Docker 能正常拉取和运行镜像。已有可用 Docker 的 VPS 可跳过软件源和 Docker 安装，仅确认服务运行；不要为了本项目重装 Docker。若已装发行版 `docker.io` 等冲突包，按官方文档处理，不混装两套包。

### 第 3 步：下载两个仓库

```bash
git clone https://github.com/wipoman/cloudflyer-oss.git /opt/cloudflyer-src
git clone https://github.com/wipoman/ns-sign.git /opt/ns-sign-src
install -d -m 700 /opt/ns-sign/data
```

目录用途：

| 路径 | 用途 |
| --- | --- |
| `/opt/cloudflyer-src` | CloudFlyer 源码及镜像配方 |
| `/opt/ns-sign-src` | ns-sign 源码和定时配置 |
| `/opt/ns-sign/data` | 私有账号配置、Cookie、设备令牌和会话备份 |

这些目录已存在时不要重复 clone 或删除数据；已有安装的升级步骤见 [详细部署说明](docs/VPS部署与修改说明.md)。

### 第 4 步：构建并启动 CloudFlyer

**必须使用 `Dockerfile.vps` 构建，直接运行旧 `jackzzs/cloudflyer` 不包含本 fork 的新补丁。**

```bash
docker build --pull \
  -f /opt/cloudflyer-src/Dockerfile.vps \
  -t cloudflyer-local:vps /opt/cloudflyer-src
```

构建会更新 Google 签名密钥和 Chrome stable，并加入 DNS、资源加载、响应缓存等修复及桌面工具。Chrome 版本以构建时软件源为准；构建耗时取决于网络和 VPS 性能。

自己设置一个客户端密钥，例如随机生成的长字符串，记住它，第 6 步需要输入同一个值。执行后按提示输入，**输入时明文可见，不额外加引号**：

```bash
read -r -p '请输入 CloudFlyer 客户端密钥（明文显示）：' NS_CLOUDFLYER_KEY
if [ -n "$NS_CLOUDFLYER_KEY" ]; then
  docker run -d \
    --name cloudflyer \
    --restart unless-stopped \
    --shm-size=256m \
    -p 127.0.0.1:3000:3000 \
    cloudflyer-local:vps \
    -K "$NS_CLOUDFLYER_KEY" -H 0.0.0.0 -P 3000 -M 1 -L
else
  echo '密钥为空，未启动容器。请重新执行本步骤。'
fi
unset NS_CLOUDFLYER_KEY
```

命令中变量外的双引号要保留。`-M 1` 使用单实例；`-L` 创建虚拟显示。API 只供本机访问，**不需要在甲骨文安全列表或防火墙放行 3000、5901、6901 或浏览器调试端口**。当前 CloudFlyer API 未校验 clientKey 的实际值，因此保持本地端口绑定，不将密钥视作公网鉴权保护。

等待启动后检查：

```bash
docker ps --filter name=cloudflyer
docker logs --tail 80 cloudflyer
curl -i --max-time 10 http://127.0.0.1:3000/
```

根路径返回 **`HTTP/1.1 404 Not Found`** 表示 API 已响应，因为它没有根路由；这不代表验证任务一定成功。刚启动时短暂出现 `Connection reset by peer`，稍等后重试；持续失败则先看容器日志，不要继续安装测试。

### 第 5 步：构建 ns-sign 镜像

```bash
docker build -t ns-sign-local:latest /opt/ns-sign-src
```

它是执行后退出的一次性任务镜像，不需要另外启动常驻的 ns-sign 容器。

### 第 6 步：初始化账号配置

```bash
docker run --rm -it \
  -v /opt/ns-sign/data:/data \
  ns-sign-local:latest \
  python /app/init_config.py
```

依次输入：

1. NodeSeek 用户名或邮箱。
2. NodeSeek 密码。
3. 第 4 步设置的 CloudFlyer 客户端密钥。

当前初始化程序按原部署习惯**明文显示输入**。直接输入原值，不自己加引号；程序会生成合法 JSON 并保存到 `/opt/ns-sign/data/config.json`，权限为 600。文件已存在时拒绝覆盖，无需重复初始化。不要把该文件提交 GitHub。

### 第 7 步：首次导入本人浏览器的登录状态

在自己电脑的浏览器中打开 `https://www.nodeseek.com/signIn.html`，完成账号登录和所需验证。

登录成功后按 F12，从**同一账号、同一浏览器、同一站点**取得以下三项：

| 项目 | 获取位置 |
| --- | --- |
| 完整 Cookie 字符串 | Network／网络 → 刷新页面 → 选择发往 `https://www.nodeseek.com` 的请求 → Headers／标头 → Request Headers／请求标头中的 `Cookie` 值。复制整行值，不包含 `Cookie:`，其中须有非空 `session`。 |
| `security_token` | Application／应用 → Local Storage／本地存储 → `https://www.nodeseek.com`，复制该键的值。 |
| `csrf_token` | 同上，复制该键的值。 |

回到 VPS 执行：

```bash
docker run --rm -it \
  -v /opt/ns-sign/data:/data \
  ns-sign-local:latest \
  python /app/local_run.py import-state
```

按程序提示粘贴完整 Cookie、security_token、csrf_token；输入明文可见，不加引号、不换行拆分 Cookie。不要粘贴响应里的 `Set-Cookie` 原文。成功导入只表示文件已保存，还需下一步验证。导出后先保留浏览器登录状态，不立即执行退出登录。

### 第 8 步：先手动签到，成功后再设定时

```bash
docker run --rm --network host \
  -v /opt/ns-sign/data:/data \
  ns-sign-local:latest \
  python /app/local_run.py check
```

`--network host` 让任务容器通过本机 `127.0.0.1:3000` 访问 CloudFlyer。`check` 会实际签到，但失败时不自动重新登录。

| 输出 | 含义与下一步 |
| --- | --- |
| HTTP 200，并显示“今天的签到收益是…个鸡腿”及成功提示 | 签到成功，继续第 9 步。 |
| HTTP 500，消息恰为“今天已完成签到，请勿重复操作” | 今日已签到，属于本项目识别的业务成功，继续第 9 步。 |
| HTTP 403、非 JSON 或 Cloudflare 验证页面 | 不能直接当成 Cookie 过期，先按第 10 步恢复；成功前不要启用定时任务。 |
| `USER NOT FOUND`、需要邮箱/短信验证或其他错误 | 登录状态未确认有效，处理登录/导入或受控恢复后再继续。 |

可选：要测试“旧 Cookie 不参与的新登录”，单独执行一次下列命令；它可能需要验证，不应作为每天的例行测试：

```bash
docker run --rm --network host \
  -v /opt/ns-sign/data:/data \
  ns-sign-local:latest \
  python /app/local_run.py login-test
```

只有输出“新会话已通过账号接口校验，Cookie 和设备令牌已保存”才说明本次重新登录成功；`check` 成功不等于未来自动续登录必定成功。`login-test` 失败会保留旧状态，但应确认正常签到仍成功后再继续。

### 第 9 步：启用北京时间 00:10 / 08:10 定时签到

```bash
install -m 644 /opt/ns-sign-src/deploy/systemd/ns-sign.service \
  /etc/systemd/system/ns-sign.service
install -m 644 /opt/ns-sign-src/deploy/systemd/ns-sign.timer \
  /etc/systemd/system/ns-sign.timer
systemctl daemon-reload
systemctl enable --now ns-sign.timer
systemctl restart ns-sign.timer
```

确认状态：

```bash
systemctl is-enabled ns-sign.timer
systemctl status ns-sign.timer --no-pager -l
systemctl list-timers --all ns-sign.timer
```

应看到 `enabled`、`active (waiting)` 和下一次触发时间。unit 内已明确设置 `Asia/Shanghai`；列表可能用系统时区显示，只列最近的一次触发。`Persistent=true` 会在开机后补执行停机期间错过的任务，不会逐一重放全部历史时间点。

立即测试完整服务链路：

```bash
systemctl start ns-sign.service
systemctl status ns-sign.service --no-pager -l
journalctl -u ns-sign.service --since "5 minutes ago" --no-pager
```

应有签到成功/今日已签到、`status=0/SUCCESS` 和 `Succeeded` 或 `Finished`。一次性服务结束后显示 `inactive (dead)` 正常。之后可以退出 SSH，任务由 systemd 执行。

若此前另外启用了 GitHub Actions 签到，又只想由 VPS 执行，可在 GitHub Actions 中停用旧签到 workflow；两套定时器互相独立。

### 第 10 步：需要时使用浏览器恢复工具

仅在原 HTTP 请求被拦截、旧会话失效等需要恢复时使用。先安装宿主机工具：

```bash
install -m 600 /opt/ns-sign-src/ns_browser_recover.py /root/ns_browser_recover.py
python3 /root/ns_browser_recover.py --help
```

开始恢复前避免任务并发。已启用定时器时先暂停，确认 service 不在 `activating` 或 `active` 状态，且没有其他 CloudFlyer 验证任务在运行：

```bash
systemctl stop ns-sign.timer
systemctl is-active ns-sign.service
```

如果尚未安装 unit，显示 unit 不存在可跳过暂停步骤。下面**根据当前状态只选一条命令**，不是连续执行四条：

| 场景 | 命令 |
| --- | --- |
| 首次开始受控浏览器恢复 | `python3 /root/ns_browser_recover.py` |
| 已准备好的登录页完成验证，继续提交登录 | `python3 /root/ns_browser_recover.py --resume` |
| 已登录或跳转首页，需继续校验、签到及保存 | `python3 /root/ns_browser_recover.py --finish-current` |
| 仅观察当前验证状态和错误 | `python3 /root/ns_browser_recover.py --diagnose` |

完整模式会恢复已导入的设备状态并尝试一次验证框点击；没有令牌就停止。若需要人工完成当前页面验证，操作方法取决于实际浏览器桌面环境，**不能复制别人截图里的固定鼠标坐标**。保留现场，参考 [浏览器恢复指南](docs/BROWSER_RECOVERY.md) 继续排查；不要重复重启、刷新或重跑完整流程。要求邮箱/短信验证时仍需本人完成。

`--finish-current` 已在 VPS 实测成功；“从首次安装到将来 Cookie 失效均完全无人值守恢复”尚未验证。新状态只有在账号接口校验、签到都确认成功后才保存，失败保留旧磁盘状态。

恢复成功后重做第 8 步验证原脚本；原签到成功再安装/恢复定时器：

```bash
systemctl enable --now ns-sign.timer
systemctl restart ns-sign.timer
```

尚未安装 unit 时先完成第 9 步。不要把恢复测试脚本直接替换为定时签到入口。

### 第 11 步：日常查看和维护

```bash
journalctl -u ns-sign.service --since today -n 60 --no-pager
systemctl list-timers --all ns-sign.timer
docker ps --filter name=cloudflyer
```

- 真实状态以签到业务消息和 service 退出结果为准；timer 已启用不代表每次签到都成功。
- `403 + HTML` 通常需要进一步检查 Cloudflare 页面；HTTP 500 仅上述“今日已签到”特例算成功。
- 不公开 `/opt/ns-sign/data`、Cookie、设备令牌或带敏感信息的截图；仅在本机私有备份数据目录。
- GitHub 更新不会自动更新 VPS 镜像或容器。`docker restart` 保留容器内代码修改，删除后从旧镜像重建则不会；已恢复正常时不必立即重建。
- 详细修改、构建迁移和回滚：[ns-sign 部署说明](docs/VPS部署与修改说明.md)、[CloudFlyer 部署说明](https://github.com/wipoman/cloudflyer-oss/blob/main/docs/VPS部署与修改说明.md)、[CloudFlyer 兼容性修复](https://github.com/wipoman/cloudflyer-oss/blob/main/docs/BROWSER_COMPATIBILITY.md)。

本教程依据当前仓库源码和既有 VPS 恢复记录整理；本次文档更新没有在全新 VPS 上执行完整安装，也没有修改任何正在运行的 VPS。


## 原有使用方法（折叠保留）

<details>
<summary>展开原上游使用方法：GitHub Actions、青龙、账号密码登录及变量说明</summary>

> 以下保留原有说明，适用于 `nodeseek_sign.py` 上游入口；外部服务优惠等信息可能过时。全新 VPS 请使用上方教程，不要混用两种配置方式。

## 🚀 使用方法

### 方式一：GitHub Actions

1. Fork 本仓库到自己的 GitHub 账号下
2. 在仓库的 `Settings > Secrets and variables > Actions` 中添加以下必要配置：

| 变量名称 | 必要性 | 说明 |
| :------: | :----: | :--- |
| `NS_COOKIE` | **建议** | NodeSeek 论坛的用户 Cookie，可在浏览器开发者工具(F12)的网络请求中获取，多账号用`&`或换行符分隔 |
| `USER`/`USER1` | 可选 | NodeSeek 论坛用户名，当 Cookie 失效时使用 |
| `PASS`/`PASS1` | 可选 | NodeSeek 论坛密码 |
| `TG_BOT_TOKEN` | 可选 | Telegram 机器人的 Token，用于通知签到结果 |
| `TG_USER_ID` | 可选 | Telegram 用户 ID，用于接收通知 |
| `TG_THREAD_ID` | 可选 | Telegram 超级群组话题 ID，用于在特定话题中发送通知 |
| `GH_PAT` | 可选 | GitHub Personal Access Token，用于自动更新Cookie变量 |

> **注意**：若仅设置 Cookie 但未配置验证码服务，当 Cookie 过期后无法自动登录获取新 Cookie。

### 方式二：青龙面板

在青龙面板中执行以下命令克隆本仓库：

```bash
ql repo https://github.com/yowiv/NodeSeek-Signin.git
```

然后在环境变量中添加所需配置。

### 方式三：账号密码登录（自动获取新Cookie）

当 Cookie 失效时，系统会尝试使用账号密码方式登录并获取新的 Cookie。登录需要通过验证码验证，支持以下两种验证码解决方案：

#### 方案A：CloudFreed 自建服务（推荐家宽用户）

```bash
docker run -itd   --name cloudflyer   -p 3000:3000   --restart unless-stopped   jackzzs/cloudflyer -K 你的客户端密钥 -H 0.0.0.0
```

配置以下环境变量：

| 变量名称 | 说明 |
| :------: | :--- |
| `API_BASE_URL` | CloudFreed 服务地址，如 `http://192.168.1.100:3000` |
| `CLIENTT_KEY` | CloudFreed 服务的客户端密钥 |
| `USER1`/`USER2`... | NodeSeek 论坛用户名 |
| `PASS1`/`PASS2`... | NodeSeek 论坛密码 |
| `SOLVER_TYPE` | 设置为 `turnstile`（默认值） |

#### 方案B：YesCaptcha 商业服务（推荐无法自建服务的用户）

1. 访问 [YesCaptcha](https://yescaptcha.com/i/k2Hy3Q) 注册账号
2. 注册后联系客服可免费获得余额（约可使用60次登录）
3. 配置以下环境变量：

| 变量名称 | 说明 |
| :------: | :--- |
| `CLIENTT_KEY` | YesCaptcha 的 API 密钥 |
| `USER1`/`USER2`... | NodeSeek 论坛用户名 |
| `PASS1`/`PASS2`... | NodeSeek 论坛密码 |
| `SOLVER_TYPE` | 设置为 `yescaptcha` |

> **提示**：YesCaptcha 提供两个服务节点，可根据网络情况选择：
> - 国际节点：`https://api.yescaptcha.com`（默认）
> - 国内节点：`https://cn.yescaptcha.com`

### 多账号配置方法

本脚本支持多账号签到，配置方法如下：

#### 1. Cookie方式

使用 `NS_COOKIE` 环境变量设置多账号，账号之间使用 `&` 或换行符分隔：

```
# 使用 & 分隔多个Cookie
NS_COOKIE=Cookie1&Cookie2&Cookie3

# 或使用换行符分隔
NS_COOKIE=Cookie1
Cookie2
Cookie3
```

#### 2. 用户名密码方式

使用编号变量设置多账号：

```
# 第一个账号
USER1=用户名1
PASS1=密码1

# 第二个账号
USER2=用户名2
PASS2=密码2

# 第三个账号
USER3=用户名3
PASS3=密码3

# 依此类推...
```

> **注意**：基本的 `USER` 和 `PASS` 变量也会被识别，系统会自动检测所有设置的账号，并依次执行签到操作。

## 🔑 GitHub Personal Access Token 设置

为了实现自动更新Cookie功能，脚本需要使用GitHub Personal Access Token (PAT)将获取的新Cookie保存到仓库变量中。

### 1. 创建Personal Access Token

1. 登录您的GitHub账户
2. 点击右上角头像 → 选择 "Settings"（设置）
3. 滚动到页面底部 → 点击 "Developer settings"（开发者设置）
4. 在左侧菜单选择 "Personal access tokens" → 点击 "Tokens (classic)"
5. 点击 "Generate new token" → 选择 "Generate new token (classic)"
6. 配置token：
   - 名称：填写一个描述性名称，如 "NodeSeek签到脚本"
   - 过期时间：根据需要选择（推荐90天或更长）
   - 勾选权限：
     - `repo` (完整的仓库访问权限)
     - `workflow` (用于管理GitHub Actions)
7. 点击页面底部的 "Generate token" 按钮
8. **立即复制生成的token**，关闭页面后将无法再次查看

### 2. 添加到仓库Secrets

1. 进入您的NodeSeek-Signin仓库
2. 点击 "Settings" → "Secrets and variables" → "Actions"
3. 点击 "New repository secret"
4. 名称填写：`GH_PAT`
5. 值填写：刚才复制的Personal Access Token
6. 点击 "Add secret" 保存

完成以上设置后，签到脚本可以自动将有效的Cookie保存到GitHub仓库变量中，下次运行时直接使用，减少重复登录和验证码操作。

## 🔧 环境变量完整说明

| 变量名称 | 必要性 | 默认值 | 说明 |
| :------: | :----: | :----: | :--- |
| `NS_COOKIE` | 建议 | - | NodeSeek 论坛的用户 Cookie，多账号使用`&`或换行符分隔 |
| `USER1`、`USER2`... | 可选 | - | NodeSeek 论坛用户名，当 Cookie 失效时使用 |
| `PASS1`、`PASS2`... | 可选 | - | NodeSeek 论坛密码 |
| `NS_RANDOM` | 可选 | true | 是否随机签到（true/false） |
| `SOLVER_TYPE` | 可选 | turnstile | 验证码解决方案（turnstile/yescaptcha） |
| `API_BASE_URL` | 条件必需 | - | CloudFreed 服务地址，当 SOLVER_TYPE=turnstile 时必填 |
| `CLIENTT_KEY` | 必需 | - | 验证码服务客户端密钥 |
| `GH_PAT` | 可选 | - | GitHub Personal Access Token，用于自动更新Cookie变量 |
| 各类通知变量 | 可选 | - | 支持多种推送通知平台配置 |

## 📊 验证码服务对比

| 服务 | 类型 | 优点 | 缺点 | 推荐指数 |
| :--: | :--: | :--- | :--- | :------: |
| CloudFreed | 自建服务 | 免费、无次数限制 | 需要自行部署维护 | ★★★★☆ |
| YesCaptcha | 商业服务 | 稳定可靠、易于配置 | 付费服务（有免费额度） | ★★★★★ |


</details>

## ⚠️ 免责声明

本项目仅供学习交流使用，请遵守 NodeSeek 论坛的相关规定和条款。

