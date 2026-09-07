# 校园网认证助手（Campus Net Guard）🛡️

> 校园网登录页不自动弹出？连上 WiFi 却打不开网页、浏览器也不跳认证页？
> Windows 后台守护工具，自动检测 Captive Portal 状态并帮你把登录页拉起来。
>
> **Campus Net Guard** — A Windows background daemon that detects captive-portal
> state and auto-opens the login page when your campus Wi-Fi won't redirect you.

[English](#english) ｜ 中文

![Platform](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/python-3.8%2B-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)
![Topic](https://img.shields.io/badge/keywords-校园网%20%7C%20captive--portal-orange)

---

## 这是什么 / What it solves

连上校园网 WiFi 后，Windows 的 Captive Portal 检测（NCSI）经常失效——表现就是
**「WiFi 已连接，但所有网页打不开，浏览器也不弹登录页」**（英文社区常搜
*captive portal not showing* / *Windows campus network login page won't pop up*）。

这个工具自己做网络检测替代 NCSI：判断出「需要认证」就自动把登录页拉起来；
如果登录页也打不开，它会先诊断原因，再逐级自动修复。

关键词：**校园网登录页不弹出 · 校园网认证失败 · Captive Portal 不跳转 ·
Windows 连上 WiFi 却没网 · 校园网自动登录助手（仅负责弹页，不碰账号密码）**

## 功能 / Features

- **三态判定**：已联网 / 需要认证 / 网络未连接。区分后两种很重要，否则没连 WiFi 时也会被反复弹浏览器
- **自动打开登录页**：仅在状态变为「需要认证」时触发，带 180 秒冷却，不会一直骚扰你
- **多认证地址 + 自动回退**：域名和内网 IP 都配上，校园 DNS 抽风时自动切到 IP 直连
- **自动发现认证地址**：连上却没弹页时，点「自动发现」会捕获网关把请求重定向到的登录页
  （重定向 URL 里通常已带 `userip` 等参数，本机 IP 一并解决），自动写入配置并设为首选；
  检测发现「需要认证」时也会自动跑一次发现
- **分步检测进度**：点「立即检测」会显示正在探测哪一步，不再对着界面干等
- **诊断 + 分级自动修复**（`netfix.py`）：
  - 先查清到底坏在哪：系统代理、假 IP（169.254）、DNS、门户页面、网卡
  - 再按风险从低到高修：清代理 → 清 DNS → 续租 IP → 重启无线网卡 → 重置网络栈
  - **每修一步就复测，恢复了立刻停手**；需要管理员的操作没权限就跳过并说明
- **开机自启**：写当前用户注册表，不需要管理员权限

## 截图 / Screenshots

> 把 GUI 运行截图命名为 `docs/screenshot.png` 放到仓库里，然后取消下面这行注释即可。
> 带图 README 在 GitHub 搜索和社媒分享时转化率高很多。

<!-- ![GUI](docs/screenshot.png) -->

## 使用前必做：改成你学校的地址 / Configure your school's portal

仓库里的 `10.0.0.1`、`portal.example.edu.cn` 都是**示例值**，必须替换。

**最省事的办法**：先把 `config.json` 里的 `portals` 留空或随便填一个，连上校园网 WiFi（未认证状态）
后直接点界面上的「自动发现」，工具会自动抓出真实地址并填好。已能正常上网时网关不做重定向，
发现不了是正常的，那种情况下手动填一下即可。

或者手动编辑 `campus_net_guard.py` 顶部的 `DEFAULT_CONFIG`（或复制 `config.example.json` 为 `config.json`）：

```python
"portals": [
    {"name": "校园网（内网 IP）", "host": "10.0.0.1", "path": "/",
     "wlanacip": "", "with_userip": False},
    {"name": "校园网（域名）", "host": "portal.example.edu.cn", "path": "/",
     "wlanacip": "10.0.0.254", "with_userip": True},
],
```

- `portals` 按顺序优先，排前面的可用就先用它
- `with_userip`：地址本身带 `?userip=...` 参数就设 `True`，纯 IP 型网关设 `False`（它自己认源 IP）
- 不确定填什么？连上校园网后随便访问一个外网地址，被跳转到的那个页面 URL 就是

## 运行 / Run

| 方式 | 说明 |
|---|---|
| 双击 `start_gui.bat` | 打开图形界面（推荐） |
| 双击 `check_once.bat` | 检测一次，直接开登录页，异常时顺带修复 |
| `python campus_net_guard.py` | 同上 GUI |
| `python campus_net_guard.py --once` | 检测一次就退出 |
| `python campus_net_guard.py --silent` | 无界面后台常驻（开机自启用这个） |
| 加 `--fix` | 检测后自动修复（高风险动作默认跳过，加 `--yes` 才执行） |

## 依赖 / Requirements

- Windows
- Python 3.8+，**需带 tkinter**（官方安装包勾选即可；部分精简发行版没有，需换用官方版）

## 隐私说明 / Privacy

- 全程在本机运行，不联网上传任何数据，不记录也不接触你的账号密码
- 仓库中的 IP、域名均为示例值，不含任何真实网络环境信息
- `config.json` 与 `*.log` 已在 `.gitignore` 中排除，不会被提交

## 已知边界 / Limitations

**不做自动填写账号密码登录。** 校园网门户通常有验证码、多运营商选择、设备绑定，
自动化登录脆弱且容易触发风控。这个工具只负责把登录页准确、及时地送到你面前。

## 文件说明 / Files

| 文件 | 作用 |
|---|---|
| `campus_net_guard.py` | 主程序：状态检测、GUI、自动打开登录页、开机自启 |
| `netfix.py` | 诊断与自动修复引擎（可单独复用） |
| `config.example.json` | 配置示例，改成 `config.json` 后生效 |

---

## ⭐ 如果这个工具帮到了你 / If this helped you

- 点个 **Star**，让更多同学能通过 GitHub 搜索找到它
- 分享到校内群 / 贴吧 / 知乎 / B站 / V2EX
- 提 Issue 或 PR 一起完善，适配更多学校

## 给仓库加「搜索标签」和简介（提高被发现率，重要）

GitHub 的 **Topics** 和 **Description** 无法用 git 提交设置，需要在网页点几下：

1. 仓库主页 → 右侧 **About** 旁的 ⚙ → 填 **Description**：
   `校园网登录页不自动弹出？Windows 后台守护，自动检测并打开 Captive Portal 认证页`
2. 同一处加 **Topics**（英文标签更易被搜到）：
   `captive-portal` `campus-network` `windows` `python` `network-tool` `portal-login`

---

## English

### Campus Net Guard

A small Windows daemon for when your campus Wi-Fi connects but never redirects you to the
login page (the classic "connected, no internet, no captive portal" problem).

It replaces Windows' flaky NCSI check with its own network probe: when it detects
"needs authentication", it opens the portal login page for you. If the portal itself is
unreachable, it diagnoses the cause and applies tiered auto-repairs (clear proxy → flush
DNS → renew IP → restart Wi‑Fi adapter → reset network stack), stopping as soon as the
network recovers.

**Highlights**

- Three-state detection: online / need-auth / offline
- Auto-opens the login page only on a need-auth transition (180s cooldown)
- Multiple portal URLs with automatic failover (domain → IP)
- "Auto-discover" captures the gateway redirect target and fills in the real portal + your IP
- Step-by-step detection progress
- Tiered auto-repair engine (`netfix.py`)
- Startup-at-login via the current-user registry (no admin needed)

**Note:** it only brings the login page to you — it does **not** auto-fill credentials.

### Quick start

1. Edit `DEFAULT_CONFIG` in `campus_net_guard.py` (or copy `config.example.json` → `config.json`)
   with your school's portal host/IP.
2. Double-click `start_gui.bat`, or run `python campus_net_guard.py`.
3. Best trick: connect to campus Wi‑Fi (unauthenticated), click **自动发现 / Auto-discover**.

### License

[MIT](LICENSE) © KNGLOKIKO
