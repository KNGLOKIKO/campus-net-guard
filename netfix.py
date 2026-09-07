# -*- coding: utf-8 -*-
"""
netfix.py - 网络诊断与自动修复引擎（被 campus_net_guard.py 调用）

思路：
  1. 先把"事实"摸全：IP 是不是假地址、网关通不通、DNS 解不解得开、系统代理有没有开、
     认证门户是 TCP 通还是 HTTP 通。很多"上不了网"其实是代理或 DNS 的小毛病，
     不查清楚就盲目 renew IP，只会把用户网络搞断几秒然后啥也没解决。
  2. 按规则匹配出问题清单，再按风险从低到高排修复链。
  3. 每执行一步就重新检测一次，恢复了立刻停手——不折腾用户。
"""

import ctypes
import re
import socket
import subprocess
import time

try:
    import winreg
except ImportError:  # 非 Windows，诊断退化
    winreg = None

try:
    import urllib.request
    import urllib.error
except ImportError:
    urllib = None

PROXY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def console_encoding():
    """Windows 控制台命令的输出编码（中文是 cp936/GBK）。
    不能用 locale.getpreferredencoding，Windows 上它给的是 utf-8，解 GBK 会乱码。"""
    try:
        return "cp%d" % ctypes.windll.kernel32.GetOEMCP()
    except Exception:
        return "utf-8"


def decode_console(raw):
    """命令输出解码。不同命令编码不一样：ipconfig 是 GBK，netsh/PowerShell 是 UTF-8。
    先试 UTF-8，出现替换字符说明不对，再退回系统 OEM 代码页。"""
    if isinstance(raw, str):
        return raw
    if not raw:
        return ""
    for enc in ("utf-8", console_encoding()):
        try:
            text = raw.decode(enc)
        except Exception:
            continue
        if "\ufffd" not in text:
            return text
    return raw.decode(console_encoding(), "replace")


def run_system_cmd(args, timeout=60):
    """执行命令，返回 (returncode, 输出文本)。隐藏窗口，避免黑框闪一下。"""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        r = subprocess.run(args, capture_output=True, timeout=timeout, startupinfo=si)
        out = decode_console(r.stdout or b"") + "\n" + decode_console(r.stderr or b"")
        return r.returncode, out.strip()
    except Exception as e:
        return -1, "%s: %s" % (type(e).__name__, e)


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_default_gateway():
    """从 ipconfig 里找 IPv4 默认网关，找不到返回 None。"""
    rc, out = run_system_cmd(["ipconfig"], timeout=25)
    if not out:
        return None
    # 中文：默认网关. . . . . . : 192.168.1.1   英文：Default Gateway . . . ：10.0.0.1
    m = re.search(r"(?:默认网关|Default Gateway)[^\d]{0,40}(\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def get_system_proxy():
    """读取当前用户的系统代理设置。"""
    info = {"enabled": False, "server": "", "auto": "", "error": None}
    if not winreg:
        return info
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROXY_KEY, 0, winreg.KEY_READ)
        try:
            info["enabled"] = bool(winreg.QueryValueEx(key, "ProxyEnable")[0])
        except FileNotFoundError:
            pass
        try:
            info["server"] = winreg.QueryValueEx(key, "ProxyServer")[0] or ""
        except FileNotFoundError:
            pass
        try:
            info["auto"] = winreg.QueryValueEx(key, "AutoConfigURL")[0] or ""
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
    except Exception as e:
        info["error"] = str(e)
    return info


def clear_system_proxy():
    """关掉系统代理（保留原值写进返回信息，方便手工还原）。"""
    old = get_system_proxy()
    if winreg is None:
        return False, "非 Windows 系统，无法修改代理设置", old
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROXY_KEY, 0, winreg.KEY_WRITE)
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, "")
        winreg.CloseKey(key)
        return True, "已关闭（原设置：%s %s）" % (
            old.get("server") or "", old.get("auto") or ""), old
    except Exception as e:
        return False, str(e), old


def tcp_reachable(host, port, timeout=3):
    try:
        s = socket.create_connection((host, port), timeout)
        s.close()
        return True
    except Exception:
        return False


def dns_resolves(host):
    try:
        return socket.gethostbyname(host)
    except Exception:
        return None


def http_status(host, timeout=5, path="/"):
    """门户页面 HTTP 状态码；失败返回 None。走 http，且绕过系统代理。"""
    if urllib is None:
        return None
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.addheaders = [("User-Agent", "Mozilla/5.0 CampusNetGuard")]
        r = opener.open("http://%s%s" % (host, path), timeout=timeout)
        code = r.getcode()
        r.close()
        return code
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def _wlan_interfaces():
    """返回 netsh wlan show interfaces 的输出文本，失败返回 ''。"""
    rc, out = run_system_cmd(["netsh", "wlan", "show", "interfaces"], timeout=25)
    return out if rc == 0 else ""


def has_wifi_adapter():
    """是否存在无线网卡。只匹配 ASCII 关键字（SSID / 802.11），躲开中文编码问题。"""
    out = _wlan_interfaces()
    if not out:
        return False
    if re.search(r"^\s*SSID\s*:", out, re.M | re.I):
        return True
    return "802.11" in out


def get_wifi_ssid():
    """当前连接的 WiFi 名称，连的是有线或没连上则返回 ''。"""
    out = _wlan_interfaces()
    if not out:
        return ""
    m = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M | re.I)
    return m.group(1).strip() if m else ""


# --------------------------------------------------------------------------
# 事实收集
# --------------------------------------------------------------------------

def collect_facts(cfg, log=None):
    """把判断需要的底层事实一次性摸清。"""
    log = log or (lambda *a, **k: None)
    portals = cfg.get("portals") or []
    facts = {
        "ip": cfg.get("_local_ip") or _local_ip(),
        "gateway": None,
        "gateway_ok": False,
        "proxy": get_system_proxy(),
        "admin": is_admin(),
        "wifi": False,
        "portals": [],
    }
    facts["apipa"] = facts["ip"].startswith("169.254.")
    facts["gateway"] = get_default_gateway()
    if facts["gateway"]:
        facts["gateway_ok"] = _ping(facts["gateway"])
    facts["wifi"] = has_wifi_adapter()
    facts["ssid"] = get_wifi_ssid() if facts["wifi"] else ""

    for p in portals:
        host = p.get("host")
        if not host:
            continue
        ip = dns_resolves(host)
        tcp = tcp_reachable(host, 80) or tcp_reachable(host, 443)
        code = http_status(host, cfg.get("timeout", 5)) if tcp else None
        facts["portals"].append({
            "host": host, "dns": ip, "tcp": tcp, "http": code,
            "is_ip": bool(re.match(r"^\d+\.\d+\.\d+\.\d+$", host)),
        })
    log("诊断：IP=%s 网关=%s 代理=%s WiFi=%s"
        % (facts["ip"], facts["gateway"],
           "开" if facts["proxy"].get("enabled") else "关",
           facts["ssid"] or ("有" if facts["wifi"] else "无")))
    return facts


def _local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _ping(host, timeout=1000):
    """用系统的 ping 探一下（比自己拼 ICMP 省事，且不需要管理员）。"""
    rc, _ = run_system_cmd(["ping", "-n", "1", "-w", str(timeout), host], timeout=10)
    return rc == 0


# --------------------------------------------------------------------------
# 诊断规则
# --------------------------------------------------------------------------

def diagnose(facts, state):
    """根据事实 + 当前状态，得出问题清单。每项含 fixes（建议的修复动作 code）。"""
    issues = []
    portals = facts.get("portals") or []
    any_tcp = any(p["tcp"] for p in portals)
    any_http = any(p["http"] and p["http"] < 500 for p in portals)
    named = [p for p in portals if not p["is_ip"]]   # 域名型
    ip_based = [p for p in portals if p["is_ip"]]    # 纯 IP 型

    # 1. 系统代理：最常见也最容易被忽略，浏览器会走代理，认证页自然打不开
    if facts["proxy"].get("enabled") or facts["proxy"].get("auto"):
        issues.append({
            "code": "proxy_on",
            "title": "系统代理处于开启状态",
            "detail": "代理：%s %s。代理会拦截认证页请求，导致登录页打不开。"
                      % (facts["proxy"].get("server") or "",
                         facts["proxy"].get("auto") or ""),
            "fixes": ["clear_proxy"],
        })

    # 2. 假 IP：DHCP 没拿到地址，典型表现是 169.254.x.x
    if facts["apipa"]:
        issues.append({
            "code": "apipa",
            "title": "没有拿到有效 IP（169.254.x.x）",
            "detail": "DHCP 获取地址失败，通常是校园网认证过期或地址池耗尽。",
            "fixes": ["renew_ip", "restart_wifi"],
        })

    # 3. 没有网关：只在确实上不了网时才算问题。
    #    注意：不能用"ping 不通网关"判断断网——校园网网关普遍禁 ICMP 和管理端口，
    #    实测这台机器网关 ping/TCP 全无响应但网络正常，据此修只会把网搞断。
    if not facts["gateway"] and state != "ONLINE":
        issues.append({
            "code": "no_gateway",
            "title": "网卡没有默认网关",
            "detail": "当前网卡没有配置网关，通常意味着没有真正连上校园网。",
            "fixes": ["renew_ip", "restart_wifi"],
        })

    # 4. DNS 解析失败但 IP 直连可用 —— 典型的校园 DNS 抽风
    dns_failed = [p for p in named if not p["dns"]]
    ip_ok = [p for p in ip_based if p["tcp"]]
    if dns_failed and ip_ok:
        issues.append({
            "code": "dns_fail",
            "title": "校园 DNS 解析失败（%s）" % "、".join(p["host"] for p in dns_failed),
            "detail": "域名解析不出 IP，但同服务器的 IP 直连是通的，典型的 DNS 故障。",
            "fixes": ["flushdns"],
        })

    # 5. 门户 TCP 能连但 HTTP 打不开
    if any_tcp and not any_http:
        issues.append({
            "code": "portal_http_fail",
            "title": "认证页面打不开（能连上但无响应）",
            "detail": "认证服务器端口通着，但页面请求没响应。常见于代理劫持或浏览器缓存问题。",
            "fixes": ["clear_proxy", "flushdns", "renew_ip"],
        })

    # 6. 门户完全不可达：用"本机有没有拿到有效 IP"区分
    #    "压根没连上网"和"连上了但网络栈有问题"，两种情况都要给修复动作
    if not any_tcp and state != "ONLINE":
        ip_valid = bool(facts["ip"]) and not facts["apipa"] \
            and not facts["ip"].startswith("127.")
        if ip_valid:
            issues.append({
                "code": "portal_unreachable",
                "title": "连不上认证服务器",
                "detail": "本机已拿到 IP（%s），但两个认证地址都连不上，"
                          "通常是 DNS 或网络栈异常。" % facts["ip"],
                "fixes": ["flushdns", "renew_ip", "reset_winsock"],
            })
        else:
            issues.append({
                "code": "not_connected",
                "title": "没有连上校园网",
                "detail": "本机没有有效 IP（%s），也连不上认证服务器。"
                          "请确认已连接校园网 WiFi 或插好网线。" % facts["ip"],
                "fixes": ["renew_ip", "restart_wifi"],
                "manual": "打开 WiFi 设置手动连接校园网",
            })

    # 7. 已认证服务器可达但就是上不了网（NEED_AUTH）——这不算故障，交给主流程开登录页
    return issues


# --------------------------------------------------------------------------
# 修复动作库
# --------------------------------------------------------------------------

def _act_clear_proxy(log):
    ok, detail, old = clear_system_proxy()
    return ok, detail


def _act_flushdns(log):
    rc, out = run_system_cmd(["ipconfig", "/flushdns"], timeout=30)
    tail = out.splitlines()[-1] if out else ""
    return rc == 0, tail or "返回码 %s" % rc


def _act_renew_ip(log):
    rc1, _ = run_system_cmd(["ipconfig", "/release"], timeout=40)
    time.sleep(1)
    rc2, out = run_system_cmd(["ipconfig", "/renew"], timeout=60)
    tail = out.splitlines()[-1] if out else ""
    return rc2 == 0, tail or "返回码 %s" % rc2


def _act_restart_wifi(log):
    cmd = ("$a = Get-NetAdapter -Physical | Where-Object {"
           "$_.PhysicalMediaType -like '802.11*' -or $_.MediaType -like '802.11*'"
           "} | Select-Object -First 1; "
           "if ($a) { Restart-NetAdapter -Name $a.Name -Confirm:$false; "
           "\"已重启网卡: \" + $a.Name } else { \"未找到无线网卡\" }")
    rc, out = run_system_cmd(["powershell", "-NoProfile", "-Command", cmd], timeout=90)
    time.sleep(4)  # 给网卡一点重连时间
    return rc == 0, (out.splitlines()[-1] if out else "返回码 %s" % rc)


def _act_reset_winsock(log):
    rc, out = run_system_cmd(["netsh", "winsock", "reset"], timeout=60)
    return rc == 0, (out.splitlines()[-1] if out else "返回码 %s" % rc) + "（需重启电脑生效）"


def _act_reset_tcpip(log):
    rc, out = run_system_cmd(["netsh", "int", "ip", "reset"], timeout=60)
    return rc == 0, (out.splitlines()[-1] if out else "返回码 %s" % rc) + "（需重启电脑生效）"


REPAIRS = {
    "clear_proxy": {
        "name": "关闭系统代理", "risk": "low", "admin": False, "act": _act_clear_proxy,
        "desc": "代理会拦截认证页请求，关掉后浏览器才能直连校园网",
    },
    "flushdns": {
        "name": "清除 DNS 缓存", "risk": "low", "admin": False, "act": _act_flushdns,
        "desc": "解决域名解析错误导致的页面打不开",
    },
    "renew_ip": {
        "name": "重新获取 IP 地址", "risk": "medium", "admin": False, "act": _act_renew_ip,
        "desc": "重新向校园网申请 IP，会短暂断网几秒",
    },
    "restart_wifi": {
        "name": "重启无线网卡", "risk": "high", "admin": True, "act": _act_restart_wifi,
        "desc": "禁用再启用无线网卡，会断开 WiFi 后自动重连",
    },
    "reset_winsock": {
        "name": "重置 Winsock 目录", "risk": "high", "admin": True, "act": _act_reset_winsock,
        "desc": "修复网络编程接口损坏，需重启电脑生效",
    },
    "reset_tcpip": {
        "name": "重置 TCP/IP 协议栈", "risk": "high", "admin": True, "act": _act_reset_tcpip,
        "desc": "最后手段，修复协议栈异常，需重启电脑生效",
    },
}

# 兜底升级链：问题清单里没点名、但确实上不了网时，按这个顺序逐级尝试
ESCALATION = ["clear_proxy", "flushdns", "renew_ip", "restart_wifi", "reset_winsock"]


def build_plan(issues, facts):
    """把问题清单里的修复项去重、按风险排序，再补上兜底升级链。"""
    plan = []
    for it in issues:
        plan.extend(it.get("fixes") or [])
    # 保底：只要不是"压根没连网"，就把升级链补上，避免诊断漏判时无从下手
    codes = [i["code"] for i in issues]
    if "not_connected" not in codes:
        plan.extend(ESCALATION)
    if not issues:
        return []
    order = {"low": 0, "medium": 1, "high": 2}
    # dict.fromkeys 保住插入顺序（问题点名的动作排前面），再用稳定排序按风险分级，
    # 注意别直接 sorted(set(...))——set 的迭代顺序不确定，会导致同级动作顺序乱跳
    unique = [c for c in dict.fromkeys(plan) if c in REPAIRS]
    ordered = sorted(unique, key=lambda x: order.get(REPAIRS[x].get("risk", "low"), 0))
    # 无线都没装就别重启网卡
    if not facts.get("wifi") and "restart_wifi" in ordered:
        ordered.remove("restart_wifi")
    return ordered


# --------------------------------------------------------------------------
# 修复引擎
# --------------------------------------------------------------------------

class RepairEngine:
    """逐步执行修复，每步之后重新检测，恢复即停。

    check_fn   : 重新检测网络，返回 (state, detail, portal)
    log        : 日志回调 (msg, level)
    progress   : 进度回调 (text, level, index, total)
    confirm    : 高风险动作确认回调 (text) -> bool，返回 None 视为跳过
    """

    def __init__(self, cfg, check_fn, log=None, progress=None, confirm=None):
        self.cfg = cfg
        self.check_fn = check_fn
        self.log = log or (lambda msg, level="info": None)
        self.progress = progress or (lambda *a, **k: None)
        self.confirm = confirm or (lambda text: False)

    def run(self, state, max_steps=6):
        cfg = dict(self.cfg)
        cfg["_local_ip"] = _local_ip()
        facts = collect_facts(cfg, self.log)
        issues = diagnose(facts, state)

        if not issues:
            self.log("诊断完成：未发现明确的异常配置", "info")
            return {"fixed": state == "ONLINE", "issues": [], "steps": [], "state": state}

        self.log("诊断发现 %d 个问题：" % len(issues), "warn")
        for it in issues:
            self.log("  · %s —— %s" % (it["title"], it["detail"]), "warn")
            if it.get("manual"):
                self.log("    → 需要手动处理：%s" % it["manual"], "error")

        plan = build_plan(issues, facts)
        if not plan:
            return {"fixed": False, "issues": issues, "steps": [], "state": state,
                    "note": "没有可自动执行的修复项，需要手动处理"}

        self.log("修复计划（按风险从低到高）：%s"
                 % " → ".join(REPAIRS[c]["name"] for c in plan), "info")

        total = min(len(plan), max_steps)
        done, applied = [], []
        for i, code in enumerate(plan[:max_steps]):
            step = REPAIRS[code]
            self.progress("修复 %d/%d：%s" % (i + 1, total, step["name"]), "info", i, total)
            self.log("执行修复 %d/%d：%s（%s）" % (i + 1, total, step["name"], step["desc"]))

            if step["admin"] and not facts.get("admin"):
                msg = "「%s」需要管理员权限，已跳过" % step["name"]
                self.log(msg, "warn")
                self.progress(msg, "warn", i + 1, total)
                done.append({"code": code, "name": step["name"], "ok": False, "skipped": "需要管理员权限"})
                continue

            if step["risk"] == "high":
                tip = ("即将执行「%s」：%s\n\n这会短暂断开网络，是否继续？"
                       % (step["name"], step["desc"]))
                if not self.confirm(tip):
                    self.log("已跳过高风险操作：%s" % step["name"], "info")
                    done.append({"code": code, "name": step["name"], "ok": False, "skipped": "用户取消"})
                    continue

            try:
                ok, detail = step["act"](self.log)
            except Exception as e:
                ok, detail = False, "执行异常：%s" % e

            done.append({"code": code, "name": step["name"], "ok": ok, "detail": detail})
            applied.append(code)
            self.log("  → %s（%s）" % ("成功" if ok else "未成功", detail),
                     "ok" if ok else "warn")
            self.progress("修复 %d/%d：%s → %s"
                          % (i + 1, total, step["name"], "完成" if ok else "未成功"),
                          "ok" if ok else "warn", i + 1, total)

            # 每步之后复测，恢复了就收工
            try:
                new_state, detail2, _p = self.check_fn()
            except Exception as e:
                new_state, detail2 = state, "复测失败：%s" % e
            self.log("  复测：%s" % _state_cn(new_state), "ok" if new_state == "ONLINE" else "info")
            state = new_state
            if new_state == "ONLINE":
                self.log("网络已恢复，停止后续修复。", "ok")
                break
            time.sleep(2)  # 给网络一点收敛时间

        fixed = state == "ONLINE"
        if not fixed:
            self.log("自动修复未能恢复网络。建议：以管理员身份运行本程序后重试，"
                     "或手动打开 WiFi 设置重新连接；仍不行则联系校园网管。", "error")
        return {"fixed": fixed, "issues": issues, "steps": done, "state": state}


def _state_cn(state):
    return {"ONLINE": "已联网", "NEED_AUTH": "需要认证", "OFFLINE": "网络未连接"}.get(state, state)
