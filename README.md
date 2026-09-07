# 校园网认证助手（Campus Net Guard）🛡️

> **校园网登录页不自动弹出？** 连上 WiFi 却打不开网页、浏览器也不跳认证页？
> 这是 Windows 的 Captive Portal 检测（NCSI）失效导致的。本工具在后台常驻，
> 自己判断「需要认证」就自动把登录页送到你面前；登录页也打不开时，先诊断再逐级自动修复。
>
> **Campus Net Guard** — A Windows background daemon that detects captive-portal state
> and auto-opens the login page when your campus Wi-Fi won't redirect you. It also
> diagnoses and auto-repairs common network faults (proxy / DNS / IP / adapter / Winsock).

[![Platform](https://img.shields.io/badge/platform-Windows-blue)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/python-3.8%2B-3776AB)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**关键词 / Keywords**：校园网登录页不弹出 · captive portal not showing · Windows 校园网认证失败 · 校园网自动登录页 · campus network portal login · network auto-repair

---

## 目录

- [解决什么问题](#解决什么问题)
- [工作原理](#工作原理)
- [功能一览](#功能一览)
- [截图 / Screenshots](#截图--screenshots)
- [安装](#安装)
- [配置（config.json）](#配置configjson)
- [使用方式](#使用方式)
  - [图形界面](#图形界面)
  - [命令行参数](#命令行参数)
  - [GUI 按钮说明](#gui-按钮说明)
- [自动修复引擎（netfix.py）](#自动修复引擎netfixpy)
- [认证地址自动发现](#认证地址自动发现)
- [仓库文件说明](#仓库文件说明)
- [隐私说明](#隐私说明)
- [已知边界](#已知边界)
- [许可证](#许可证)
- [⭐ 如果这个工具帮到了你](#-如果这个工具帮到了你)
- [English](#english)

---

## 解决什么问题

连上校园网 WiFi 后，Windows 会访问一个"联网探针"地址判断是否需要认证（Captive Portal）。
这套机制（NCSI）经常失灵，结果是：

- WiFi 显示「已连接」，但所有网页都打不开；
- 浏览器**不**自动弹出登录页，只能自己到处乱点才找到认证地址；
- 极少数情况下，虽然连上了 WiFi，但网络本身有问题（代理、假 IP、DNS、网卡），更找不到入口。

本工具用一套**自己的三态检测**替代 NCSI，并在「需要认证」时主动拉起登录页，
在「连上了却上不了网」时自动诊断并修复。

---

## 工作原理

1. **联网探针**：依次访问若干 `generate_204` 类地址（小米 / Apple / Google / 微软），
   只要有一个返回预期结果，就认为已经能出外网。全部走 `http`，避免未认证时 https 证书被拦截导致误判。
2. **状态判定**：出不了外网时，再探测校园网认证服务器是否可达，区分三种状态：
   - `ONLINE` —— 已联网
   - `NEED_AUTH` —— 已接入校园网但需要认证（自动打开登录页）
   - `OFFLINE` —— 网络本身没连上（没插网线 / 没连 WiFi / 认证服务器也不可达）
3. **自动拉起登录页**：状态变为 `NEED_AUTH` 时，用默认浏览器打开登录页（带本机 IP 参数），
   并带 180 秒冷却，不会反复弹窗骚扰你。

核心代码见 `campus_net_guard.py` 的 `NetworkChecker.check()` 与 `GuardCore.tick()`。

---

## 功能一览

- **三态判定**：区分「已联网 / 需要认证 / 未连接」很重要——否则没连 WiFi 时也会被反复弹浏览器。
- **自动打开登录页**：仅在状态变为「需要认证」时触发，带冷却时间防骚扰。
- **多认证地址 + 自动回退**：域名和内网 IP 都配上，校园 DNS 抽风时自动切到 IP 直连。
- **认证地址自动发现**：连上却没弹页时，捕获网关把请求重定向到的登录页（URL 里通常已带 `userip` 等参数，
  本机 IP 一并解决），自动写入配置并设为首选。
- **分步检测进度**：点「立即检测」会显示正在探测哪一步，不再对着界面干等。
- **诊断 + 分级自动修复**（`netfix.py`）：先查清坏在哪，再按风险从低到高修；**每修一步就复测，恢复了立刻停手**。
- **开机自启**：写入当前用户注册表，不需要管理员权限。
- **纯本地运行**：不联网上传任何数据，不碰你的账号密码。

---

## 截图 / Screenshots

> 把 GUI 运行截图命名为 `docs/screenshot.png` 放到仓库里，然后取消下面一行的注释即可。
> 带截图的仓库转化率和搜索权重都更高。
>
<!-- ![GUI 主界面](docs/screenshot.png) -->

---

## 安装

1. 需要 **Windows** + **Python 3.8+，且带 tkinter**（官方安装包勾选「tcl/tk and IDLE」即可；
   部分精简发行版没有 tkinter，需换用官方版）。
2. 下载本仓库，把 `config.example.json` 复制为 `config.json`，改成你学校的地址（见下）。
3. 双击 `start_gui.bat` 打开图形界面；或双击 `check_once.bat` 检测一次并自动修复。

> 嫌装 Python 麻烦？可以把它打包成单文件 `exe`（用 `pyinstaller campus_net_guard.py`），
> 发到校内群里同学双击即用——这是拉新最有效的方法之一。

---

## 配置（config.json）

仓库里的 `10.0.0.1`、`portal.example.edu.cn` 等都是**示例值**，必须替换成你学校的真实地址。

**最省事**：先把 `portals` 留空或随便填一个，连上校园网 WiFi（未认证状态）后点界面上的
「自动发现」，工具会自动抓出真实地址并填好。已能正常上网时网关不做重定向，发现不了是正常的，那种情况手动填即可。

也可手动编辑（或复制 `config.example.json`）：

```json
{
  "portals": [
    {"name": "校园网（内网 IP）", "host": "10.0.0.1", "path": "/",
     "wlanacip": "", "with_userip": false},
    {"name": "校园网（域名）", "host": "portal.example.edu.cn", "path": "/",
     "wlanacip": "10.0.0.254", "with_userip": true}
  ],
  "preferred": "auto",
  "auto_open": true,
  "cooldown": 180,
  "interval_online": 60,
  "interval_auth": 15,
  "interval_offline": 10,
  "timeout": 5
}
```

| 字段 | 含义 | 说明 |
|---|---|---|
| `portals` | 认证服务器候选列表 | 按顺序优先，排前面的可用就先用它 |
| `portals[].name` | 显示名 | GUI 下拉框里展示 |
| `portals[].host` | 认证服务器主机 | 域名或内网 IP |
| `portals[].path` | 路径 | 一般为 `/` |
| `portals[].wlanacip` | AC（接入控制器）IP | 部分网关需要，没有就留空 |
| `portals[].with_userip` | 是否带 `?userip=` | 地址本身带该参数设 `true`；纯 IP 型网关设 `false`（它自己认源 IP） |
| `preferred` | 首选地址 | `"auto"` = 哪个可达用哪个；也可写死某个 `host` |
| `auto_open` | 是否自动打开登录页 | `false` 时只检测不弹页 |
| `cooldown` | 弹页冷却（秒） | 防止短时间内反复弹窗 |
| `interval_online` | 已联网时检测间隔（秒） | 默认 60 |
| `interval_auth` | 需要认证时检测间隔（秒） | 默认 15（更频繁，好及时发现） |
| `interval_offline` | 未连接时检测间隔（秒） | 默认 10 |
| `timeout` | 单次探测超时（秒） | 默认 5 |

> 不确定填什么？连上校园网后随便访问一个外网地址，被跳转到的那个页面 URL 就是认证地址。
> 不确定 `with_userip`？地址里带 `?userip=...` 就 `true`，否则 `false`。

---

## 使用方式

### 图形界面

双击 `start_gui.bat`（或 `python campus_net_guard.py`）打开 GUI。界面包含状态卡、
检测进度条、主按钮区、工具按钮区、登录地址下拉框、两个开关（自动打开登录页 / 开机自启）和日志区。

### 命令行参数

程序用 `sys.argv` 解析参数（无第三方依赖）：

| 命令 | 作用 |
|---|---|
| `python campus_net_guard.py` | 启动图形界面（默认） |
| `python campus_net_guard.py --silent` | 无界面后台常驻（**开机自启用这个**） |
| `python campus_net_guard.py --once` | 只检测一次，需要认证就打开登录页，然后退出 |
| `python campus_net_guard.py --once --force-open` | 检测一次并**强制**打开登录页（即使已联网） |
| `python campus_net_guard.py --once --fix` | 检测后自动修复（高风险动作默认跳过） |
| `python campus_net_guard.py --once --fix --yes` | 自动修复**且允许**高风险动作（重置 Winsock / TCP-IP 等） |

`check_once.bat` 等价于 `python campus_net_guard.py --once --force-open --fix`。

### GUI 按钮说明

| 按钮 / 控件 | 功能 |
|---|---|
| 立即检测 | 手动触发一次三态检测，显示分步进度 |
| 自动修复 | 诊断当前网络问题并按分级方案修复 |
| 打开登录页 | 立即用浏览器打开当前首选认证地址 |
| 复制地址 | 复制当前登录页 URL 到剪贴板 |
| 自动发现 | 捕获网关重定向出的真实认证地址并写入配置 |
| WiFi 设置 | 打开 Windows 的 WiFi 设置页（`ms-settings:network-wifi`） |
| 刷新 IP | 执行 `ipconfig /renew` 续租本机 IP |
| 清 DNS 缓存 | 执行 `ipconfig /flushdns` |
| 查看日志 | 打开 `campus_net_guard.log` |
| 登录地址（下拉） | 选择「自动选择」或指定某个候选 host |
| 自动打开登录页（开关） | 对应 `auto_open` |
| 开机自启（开关） | 写入/移除当前用户注册表自启项（无需管理员） |

---

## 自动修复引擎（netfix.py）

检测结束且不是 `ONLINE` 时，工具会进入修复流程。它先 `collect_facts()` 收集现场，
再 `diagnose()` 找出问题，最后 `RepairEngine.run()` 按风险**从低到高**执行，且**每步后复测，恢复了立刻停手**。

| 风险等级 | 动作 | 命令 / 说明 |
|---|---|---|
| 低 | 清除系统代理 | 关闭被误开的 HTTP 代理 |
| 低 | 清空 DNS 缓存 | `ipconfig /flushdns` |
| 中 | 续租 IP | `ipconfig /renew` |
| 高（需管理员 + 确认） | 重启无线网卡 | 禁用再启用 WiFi 适配器 |
| 高（需管理员 + 确认） | 重置 Winsock | `netsh winsock reset` |
| 高（需管理员 + 确认） | 重置 TCP/IP | `netsh int ip reset` |

> 高风险动作默认跳过；命令行需加 `--yes`，GUI 会弹确认框。没有管理员权限时跳过并说明，不会强行执行。

关键函数：`collect_facts()`、`diagnose()`、`build_plan()`、`RepairEngine.run()`。

---

## 认证地址自动发现

`netfix.discover_portal(cfg, log, timeout)` 的工作原理：

- 未认证时，网关会把对外网的 http 请求 **302 重定向**到认证页，捕获响应头 `Location`
  即可拿到完整地址（通常已带 `userip` / `wlanacip` 参数），本机 IP 一并解决。
- 同时兼容 `meta refresh` / JS 跳转的 URL 提取。
- 对内网候选（网关 / DHCP / DNS）用强特征（`wlanacname` / `wlanacip` / `userip` / `portal`）过滤，
  避免把普通登录页误判成认证页。
- 已知地址不重复添加；已存在但缺 `userip` 参数则自动补全。

GUI 中点「自动发现」即可；检测结束且非 `ONLINE` 时也会自动跑一次发现。

---

## 仓库文件说明

| 文件 | 作用 |
|---|---|
| `campus_net_guard.py` | **主程序**。状态检测（三态判定）、GUI、自动打开登录页、开机自启。核心类 `NetworkChecker` / `GuardCore`；关键函数 `load_config` / `normalize_config` / `ordered_portals` / `parse_portal_url` / `build_portal_url` / `save_config` / `get_local_ip` / `run_gui` / `main`，以及自启辅助 `set_autostart` 等 |
| `netfix.py` | **诊断与自动修复引擎**（可单独复用）。`collect_facts` / `diagnose` / `build_plan` / `RepairEngine` / `discover_portal` / `run_system_cmd` / `is_admin` 等 |
| `config.example.json` | 配置示例，复制为 `config.json` 后生效 |
| `start_gui.bat` | 启动图形界面（优先 `py -3w`，回退 `pythonw`，无控制台窗口） |
| `check_once.bat` | 检测一次并自动修复（`--once --force-open --fix`） |
| `LICENSE` | MIT 许可证 |
| `README.md` | 本文件 |
| `.gitignore` | 排除 `config.json`、`*.log`、`__pycache__`、`.vscode`、`.idea`（真实配置与日志不会进仓库） |

---

## 隐私说明

- 全程在本机运行，**不联网上传任何数据**，不记录也不接触你的账号密码。
- 仓库中的 IP、域名均为示例值，不含任何真实网络环境信息。
- `config.json` 与 `*.log` 已在 `.gitignore` 中排除，不会被提交。

---

## 已知边界

**不做自动填写账号密码登录。** 校园网门户通常有验证码、多运营商选择、设备绑定，
自动化登录脆弱且容易触发风控。这个工具只负责把登录页准确、及时地送到你面前。

---

## 许可证

本项目以 [MIT 许可证](LICENSE) 发布。自由使用、修改、再分发。

---

## ⭐ 如果这个工具帮到了你

- 点个 **Star**，让更多同学能通过 GitHub 搜到它（搜「校园网登录页不弹出」「captive portal」）。
- 分享到校内群 / 贴吧 / 知乎 / B站。
- 提 Issue 或 PR 一起完善。

---

## English

### Campus Net Guard

A Windows background daemon for campus networks where the OS captive-portal detection (NCSI)
fails and the login page never pops up.

**What it does**
- Replaces NCSI with its own 3-state check: `ONLINE` / `NEED_AUTH` / `OFFLINE`.
- Auto-opens the captive-portal login page (with the local IP) only when `NEED_AUTH`, with a cooldown.
- Supports multiple portal candidates (domain + LAN IP) with automatic fallback.
- Auto-discovers the real portal URL by capturing the gateway's 302 redirect.
- Diagnoses and auto-repairs common faults (proxy / DNS / IP / adapter / Winsock), lowest-risk first,
  re-testing after each step.
- Optional autostart via the current-user registry (no admin needed).
- Purely local; no telemetry, no credentials touched.

**Requirements**: Windows + Python 3.8+ with `tkinter`.

**Quick start**
```bash
pip install tk
cp config.example.json config.json   # edit portals to your school
python campus_net_guard.py           # GUI
python campus_net_guard.py --silent  # background daemon (use for autostart)
```

**CLI flags**: `--silent` · `--once` · `--force-open` · `--fix` · `--yes`

**License**: MIT.
