# -*- coding: utf-8 -*-
"""
Campus Net Guard - 校园网 Portal 认证助手

解决场景：连上校园网 WiFi 后，Windows 的 Captive Portal 检测（NCSI）时灵时不灵，
浏览器不会自动弹出登录页，表现就是"WiFi 已连接但上不了网"。

工作原理：
  1. 主动访问若干"联网探针"地址（generate_204 类），判断是否真的能出外网。
  2. 出不了网时，再探测校园网认证服务器是否可达，据此区分三种状态：
       ONLINE    已联网
       NEED_AUTH 已接入校园网但需要认证（会自动打开登录页）
       OFFLINE   网络本身没连上（没插网线 / 没连 WiFi / 认证服务器也不可达）
  3. 状态变为 NEED_AUTH 时自动拉起浏览器打开登录页（带当前 IP，可设冷却时间防止反复弹窗）。

用法：
  python campus_net_guard.py          启动图形界面
  python campus_net_guard.py --silent 无界面后台常驻（开机自启用这个）
  python campus_net_guard.py --once   只检测一次，需要认证就打开登录页，然后退出
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime

try:
    import netfix  # 网络诊断与自动修复引擎（同目录 netfix.py）
except Exception:  # 模块缺失时降级为纯检测，不让整个程序挂掉
    netfix = None

try:
    import urllib.request
    import urllib.error
except ImportError:  # pragma: no cover
    urllib = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_FILE = os.path.join(BASE_DIR, "campus_net_guard.log")

DEFAULT_CONFIG = {
    # 认证服务器候选列表。实测 portal.example.edu.cn 解析到 10.0.0.1，是同一台服务器。
    # 顺序即优先级：内网 IP 排第一（不依赖校园 DNS，更稳），域名作为兜底。
    "portals": [
        {"name": "校园网（内网 IP）", "host": "10.0.0.1", "path": "/",
         "wlanacip": "", "with_userip": False},
        {"name": "校园网（域名）", "host": "portal.example.edu.cn", "path": "/",
         "wlanacip": "10.0.0.254", "with_userip": True},
    ],
    # "auto" = 哪个可达用哪个；也可写死某个 host 作为首选
    "preferred": "auto",
    "auto_open": True,
    "cooldown": 180,
    "interval_online": 60,
    "interval_auth": 15,
    "interval_offline": 10,
    "timeout": 5,
}

# 联网探针：只要有一个返回预期结果，就认为已经能出外网。
# 全部走 http，避免 https 在未认证时被拦截证书导致误判。
PROBES = [
    ("http://connect.rom.miui.com/generate_204", "status204"),
    ("http://captive.apple.com/hotspot-detect.html", "success_body"),
    ("http://www.gstatic.com/generate_204", "status204"),
    ("http://www.msftconnecttest.com/connecttest.txt", "msft"),
]

ONLINE, NEED_AUTH, OFFLINE = "ONLINE", "NEED_AUTH", "OFFLINE"

STATE_TEXT = {
    ONLINE: ("已联网", "#4EC9B0"),
    NEED_AUTH: ("需要校园网认证", "#DCDCAA"),
    OFFLINE: ("网络未连接", "#F14C4C"),
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return normalize_config(cfg)


def normalize_config(cfg):
    """兼容旧版单地址配置，并补齐候选项的缺省字段。"""
    if not cfg.get("portals"):
        host = cfg.get("portal_host")
        if host:
            cfg["portals"] = [{
                "name": host, "host": host,
                "path": cfg.get("portal_path") or "/",
                "wlanacip": cfg.get("wlanacip") or "",
                "with_userip": True,
            }]
        else:
            cfg["portals"] = [dict(p) for p in DEFAULT_CONFIG["portals"]]
    out = []
    for p in cfg["portals"]:
        item = dict(p)
        item.setdefault("name", item.get("host", ""))
        item.setdefault("path", "/")
        item.setdefault("wlanacip", "")
        item.setdefault("with_userip", bool(item.get("wlanacip")))
        out.append(item)
    cfg["portals"] = out
    cfg.setdefault("preferred", "auto")
    return cfg


def ordered_portals(cfg):
    """按 preferred 排序后的候选列表，首选地址排在最前。"""
    portals = list(cfg.get("portals") or [])
    pref = cfg.get("preferred", "auto")
    if pref and pref != "auto":
        for i, p in enumerate(portals):
            if pref in (p.get("host"), p.get("name")):
                return [p] + portals[:i] + portals[i + 1:]
    return portals


def parse_portal_url(url):
    """从用户给的登录页 URL 反解出配置，方便以后加新地址。"""
    from urllib.parse import urlparse, parse_qs
    if "://" not in url:
        url = "http://" + url
    u = urlparse(url)
    q = parse_qs(u.query)
    host = u.hostname or ""
    return {
        "name": host,
        "host": host,
        "path": u.path or "/",
        "wlanacip": (q.get("wlanacip") or [""])[0],
        "with_userip": "userip" in q,
        "https": u.scheme == "https",
    }


def build_portal_url(portal, local_ip=None):
    """按候选配置拼出登录页 URL。"""
    scheme = "https" if portal.get("https") else "http"
    url = "%s://%s%s" % (scheme, portal.get("host"), portal.get("path") or "/")
    if portal.get("with_userip"):
        params = ["userip=%s" % (local_ip or get_local_ip()), "wlanacname="]
        if portal.get("wlanacip"):
            params.append("wlanacip=%s" % portal["wlanacip"])
        url += "?" + "&".join(params)
    return url


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def console_encoding():
    """控制台命令（ipconfig 等）的输出编码。中文 Windows 是 cp936(GBK)，
    注意不能用 locale.getpreferredencoding —— 它在这台机器上给的是 utf-8，解出来是乱码。"""
    try:
        import ctypes
        return "cp%d" % ctypes.windll.kernel32.GetOEMCP()
    except Exception:
        return "utf-8"


def get_local_ip():
    """获取本机用于出网的 IP（不会真的发包，只是查路由表）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


class NetworkChecker:
    """负责网络状态检测，带本地日志回调。"""

    def __init__(self, cfg, log=None, progress=None):
        self.cfg = cfg
        self.log = log or (lambda msg, level="info": None)
        self.progress = progress or (lambda **kw: None)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._opener.addheaders = [
            ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CampusNetGuard/1.0"),
            ("Cache-Control", "no-cache"),
        ]

    def _report(self, **kw):
        """上报检测进度；失败不能影响检测本身。"""
        try:
            self.progress(**kw)
        except Exception:
            pass

    def _fetch(self, url):
        """返回 (status, body, final_url)，失败返回 (None, b'', url)。"""
        try:
            resp = self._opener.open(url, timeout=self.cfg["timeout"])
            status = resp.getcode()
            body = resp.read(4096)
            final = resp.geturl()
            resp.close()
            return status, body, final
        except urllib.error.HTTPError as e:
            # 部分认证网关会用 302 之外的手段拦截，HTTPError 也算"有响应"
            try:
                return e.code, e.read(512), url
            except Exception:
                return e.code, b"", url
        except Exception:
            return None, b"", url

    def _probe_passed(self, kind, status, body, final):
        if status is None:
            return False
        if kind == "status204":
            return status == 204
        if kind == "success_body":
            return b"Success" in body and "captive.apple.com" in final
        if kind == "msft":
            return status == 200 and b"Microsoft Connect Test" in body
        return False

    def internet_ok(self):
        """是否能出外网。返回 (是否通, 说明)。"""
        total = len(PROBES)
        for i, (url, kind) in enumerate(PROBES):
            host = url.split("/")[2]
            self._report(stage="internet", index=i, total=total, target=host, phase="start")
            status, body, final = self._fetch(url)
            if self._probe_passed(kind, status, body, final):
                self._report(stage="internet", index=i, total=total, target=host,
                             phase="pass", detail="通过")
                return True, "探针通过：%s" % host
            # 被劫持到认证服务器也算"没通"，明确指出来
            if final and any(p.get("host") in final
                             for p in self.cfg.get("portals", []) if p.get("host")):
                self._report(stage="internet", index=i, total=total, target=host,
                             phase="hijack", detail="被重定向到认证页")
                return False, "请求被重定向到认证服务器"
            self._report(stage="internet", index=i, total=total, target=host, phase="fail",
                         detail="无响应" if status is None else "返回 %s" % status)
        return False, "所有联网探针均无响应"

    def portal_reachable(self, portals=None):
        """返回第一个可达的认证服务器配置（dict），全都不可达返回 None。"""
        candidates = portals if portals is not None else ordered_portals(self.cfg)
        total = len(candidates)
        for i, p in enumerate(candidates):
            self._report(stage="portal", index=i, total=total, target=p["host"], phase="start")
            for port in (80, 443):
                try:
                    s = socket.create_connection((p["host"], port), self.cfg["timeout"])
                    s.close()
                    self._report(stage="portal", index=i, total=total, target=p["host"],
                                 phase="pass", detail="可达")
                    return p
                except Exception:
                    continue
            self._report(stage="portal", index=i, total=total, target=p["host"],
                         phase="fail", detail="不可达")
        return None

    def check(self):
        """综合判定，返回 (状态, 说明, 可用的认证服务器或 None)。"""
        ok, detail = self.internet_ok()
        if ok:
            self._report(stage="done", state=ONLINE, detail=detail)
            return ONLINE, detail, None
        portal = self.portal_reachable()
        if portal:
            detail2 = "已接入校园网（%s），但%s" % (portal["host"], detail)
            self._report(stage="done", state=NEED_AUTH, detail=detail2)
            return NEED_AUTH, detail2, portal
        detail2 = detail + "，且所有认证服务器都不可达"
        self._report(stage="done", state=OFFLINE, detail=detail2)
        return OFFLINE, detail2, None


class GuardCore:
    """状态机 + 定时轮询，与界面解耦。"""

    def __init__(self, cfg=None, log=None):
        self.cfg = cfg or load_config()
        self.log = log or (lambda msg, level="info": None)
        self.checker = NetworkChecker(self.cfg, self.log)
        self.state = None
        self.current_portal = None  # 最近一次检测中实际可达的认证服务器
        self._stop = threading.Event()
        self._last_open = 0.0
        self.on_state_change = None

    # ---------- 对外动作 ----------

    def portal_url(self):
        """当前应使用的登录地址：优先用实测可达的那个，否则用首选/第一个候选。"""
        candidates = ordered_portals(self.cfg)
        p = self.current_portal or (candidates[0] if candidates else None)
        if not p:
            return "about:blank"
        return build_portal_url(p)

    def open_login_page(self, force=False):
        url = self.portal_url()
        now = time.time()
        if not force and now - self._last_open < self.cfg["cooldown"]:
            self.log("仍在冷却期内，跳过弹窗（上次打开于 %d 秒前）"
                     % int(now - self._last_open), "info")
            return False
        try:
            webbrowser.open(url)
            self._last_open = now
            self.log("已打开登录页：%s" % url, "ok")
            return True
        except Exception as e:
            self.log("打开浏览器失败：%s" % e, "error")
            return False

    def tick(self, progress=None):
        """执行一次检测并处理状态变化。

        progress 只在手动检测时传入（用于界面显示步骤），后台轮询传 None 保持安静。
        """
        prev = self.checker.progress
        if progress is not None:
            self.checker.progress = progress
        try:
            state, detail, portal = self.checker.check()
        finally:
            self.checker.progress = prev
        if portal is not None:
            self.current_portal = portal
        changed = state != self.state
        if changed:
            self.log("状态变化：%s -> %s（%s）"
                     % (STATE_TEXT.get(self.state, (self.state,))[0],
                        STATE_TEXT[state][0], detail),
                     "ok" if state == ONLINE else ("warn" if state == NEED_AUTH else "error"))
        self.state = state
        if self.on_state_change:
            try:
                self.on_state_change(state, detail, changed)
            except Exception:
                pass
        if changed and state == NEED_AUTH and self.cfg["auto_open"]:
            self.open_login_page()
        return state, detail

    def interval(self):
        return {
            ONLINE: self.cfg["interval_online"],
            NEED_AUTH: self.cfg["interval_auth"],
            OFFLINE: self.cfg["interval_offline"],
        }.get(self.state, 30)

    def loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:
                self.log("检测异常：%s" % e, "error")
            self._stop.wait(self.interval())

    def start(self):
        t = threading.Thread(target=self.loop, daemon=True)
        t.start()
        return t

    def stop(self):
        self._stop.set()


def file_logger():
    lock = threading.Lock()

    def _log(msg, level="info"):
        line = "[%s][%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), level, msg)
        with lock:
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
    return _log


# --------------------------------------------------------------------------
# 图形界面
# --------------------------------------------------------------------------

def run_gui(cfg):
    import tkinter as tk
    from tkinter import ttk, messagebox

    try:  # 高 DPI 下别糊
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    BG, PANEL, BORDER = "#1e1e1e", "#252526", "#3c3c3c"
    FG, SUB, ACCENT = "#cccccc", "#808080", "#4A9EFF"
    LEVEL_COLOR = {"info": "#9cdcfe", "ok": "#4EC9B0",
                   "warn": "#DCDCAA", "error": "#F14C4C"}

    root = tk.Tk()
    root.title("校园网认证助手")
    root.geometry("500x680")
    root.minsize(450, 560)
    root.configure(bg=BG)

    log_lines = []
    log_widget = None
    auto_open_var = tk.BooleanVar(value=cfg.get("auto_open", True))
    autostart_var = tk.BooleanVar(value=is_autostart_enabled())

    def ui_log(msg, level="info"):
        line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
        log_lines.append((line, level))
        if len(log_lines) > 300:
            del log_lines[0]
        if log_widget:
            root.after(0, _render_log)

    def _render_log():
        log_widget.configure(state="normal")
        log_widget.delete("1.0", "end")
        for line, level in log_lines[-200:]:
            log_widget.insert("end", line + "\n", level)
        log_widget.configure(state="disabled")
        log_widget.see("end")

    core = GuardCore(cfg, ui_log)

    # ---------- 头部状态卡 ----------
    header = tk.Frame(root, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
    header.pack(fill="x", padx=12, pady=(12, 8))

    dot = tk.Canvas(header, width=18, height=18, bg=PANEL, highlightthickness=0)
    dot.pack(side="left", padx=(16, 10), pady=18)
    dot_oval = dot.create_oval(3, 3, 15, 15, fill="#808080", outline="")

    text_col = tk.Frame(header, bg=PANEL)
    text_col.pack(side="left", fill="x", expand=True, pady=14)
    state_label = tk.Label(text_col, text="检测中…", bg=PANEL, fg=FG,
                           font=("Microsoft YaHei UI", 13, "bold"), anchor="w")
    state_label.pack(fill="x")
    detail_label = tk.Label(text_col, text="正在判断网络状态", bg=PANEL, fg=SUB,
                            font=("Microsoft YaHei UI", 9), anchor="w", wraplength=380,
                            justify="left")
    detail_label.pack(fill="x", pady=(2, 0))
    ip_label = tk.Label(text_col, text="本机 IP：%s" % get_local_ip(), bg=PANEL, fg=SUB,
                        font=("Microsoft YaHei UI", 9), anchor="w")
    ip_label.pack(fill="x", pady=(4, 0))

    # ---------- 检测进度 ----------
    # 先切到 clam 主题，否则 Progressbar / Combobox 的深色配色不生效
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    prog_frame = tk.Frame(root, bg=BG)
    prog_frame.pack(fill="x", padx=12, pady=(0, 6))
    step_label = tk.Label(prog_frame, text="尚未手动检测", bg=BG, fg=SUB,
                          font=("Microsoft YaHei UI", 9), anchor="w")
    step_label.pack(fill="x")
    ttk.Style().configure("TProgressbar", background=ACCENT, troughcolor="#2b2b2b",
                          bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)
    prog = ttk.Progressbar(prog_frame, mode="determinate", maximum=1, value=0)
    prog.pack(fill="x", pady=(3, 0))

    # ---------- 按钮区 ----------
    def btn(parent, text, cmd, primary=False, padx=14):
        b = tk.Label(parent, text=text, bg=ACCENT if primary else "#333333",
                     fg="#ffffff" if primary else FG, padx=padx, pady=7,
                     font=("Microsoft YaHei UI", 9), cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e, w=b: w.configure(bg="#3d7fd4" if primary else "#3f3f3f"))
        b.bind("<Leave>", lambda e, w=b: w.configure(
            bg=ACCENT if primary else "#333333"))
        return b

    actions = tk.Frame(root, bg=BG)
    actions.pack(fill="x", padx=12, pady=(0, 6))

    checking = [False]
    buttons = []

    def set_buttons_enabled(enabled):
        # 任一长时间操作进行中时，按钮保持禁用，避免被误点或在操作间打架
        active = checking[0] or repairing[0] or discovering[0]
        actual = enabled and not active
        for b, primary in buttons:
            b.configure(bg=(ACCENT if actual else "#2f5a86") if primary
                        else ("#333333" if actual else "#2a2a2a"))
            b.configure(fg=("#ffffff" if primary else FG) if actual else "#6a6a6a")
            b.configure(cursor="hand2" if actual else "arrow")

    def _maybe_enable():
        set_buttons_enabled(True)

    def do_check():
        if checking[0]:
            return
        checking[0] = True
        set_buttons_enabled(False)
        total_steps = len(PROBES) + len(ordered_portals(cfg)) + 1
        prog.configure(maximum=total_steps, value=0)
        step_label.configure(text="正在检测…", fg=SUB)
        state_label.configure(text="检测中…", fg=SUB)
        detail_label.configure(text="正在逐个探测联网状态与认证服务器")
        dot.itemconfigure(dot_oval, fill="#808080")
        ui_log("开始检测（共 %d 步）" % total_steps)

        started = time.time()

        def on_progress(**kw):
            root.after(0, lambda kw=kw: apply_progress(kw))

        def worker():
            try:
                core.tick(progress=on_progress)
            except Exception as e:
                ui_log("检测异常：%s" % e, "error")
            finally:
                root.after(0, lambda: finish_check(time.time() - started))

        threading.Thread(target=worker, daemon=True).start()

    def do_open():
        core.open_login_page(force=True)

    b_check = btn(actions, "立即检测", do_check, padx=10)
    b_check.pack(side="left")
    b_fix = btn(actions, "自动修复", lambda: start_repair("手动触发自动修复"), padx=10)
    b_fix.pack(side="left", padx=(6, 0))
    b_open = btn(actions, "打开登录页", do_open, primary=True, padx=10)
    b_open.pack(side="left", padx=(6, 0))
    b_copy = btn(actions, "复制地址", lambda: copy_url(root), padx=10)
    b_copy.pack(side="left", padx=(6, 0))
    buttons.extend([(b_check, False), (b_fix, False), (b_open, True), (b_copy, False)])

    def apply_progress(kw):
        """把检测步骤反映到进度条、步骤文字和日志上。"""
        stage = kw.get("stage")
        idx = kw.get("index") or 0
        total = kw.get("total") or 1
        target = kw.get("target") or ""
        phase = kw.get("phase")
        detail = kw.get("detail") or ""
        if stage == "internet":
            base, prefix = 0, "探测外网"
        elif stage == "portal":
            base, prefix = len(PROBES), "探测认证服务器"
        else:
            return
        text = "%s %d/%d：%s" % (prefix, idx + 1, total, target)
        prog.configure(value=base + idx)
        if phase == "start":
            step_label.configure(text=text + " …", fg=SUB)
            ui_log(text + " …")
            return
        prog.configure(value=base + idx + 1)
        if phase == "pass":
            step_label.configure(text=text + " → %s" % detail, fg="#4EC9B0")
            ui_log(text + " → %s" % detail, "ok")
        elif phase == "hijack":
            step_label.configure(text=text + " → 被重定向到认证页", fg="#DCDCAA")
            ui_log(text + " → 被重定向到认证页", "warn")
        else:
            step_label.configure(text=text + " → %s" % detail, fg="#F14C4C")
            ui_log(text + " → %s" % detail, "warn")

    def finish_check(elapsed):
        prog.configure(value=prog["maximum"])
        checking[0] = False
        _maybe_enable()
        text, color = STATE_TEXT.get(core.state, ("未知", SUB))
        step_label.configure(text="检测完成 · %s · 用时 %.1fs" % (text, elapsed), fg=color)
        ui_log("检测完成：%s（用时 %.1fs）" % (text, elapsed),
               "ok" if core.state == ONLINE else
               ("warn" if core.state == NEED_AUTH else "error"))
        # 只要不是"已联网"，就接着走诊断 + 自动修复 + 自动发现地址
        if core.state != ONLINE:
            start_repair("网络异常，开始自动诊断与修复…")
            do_discover()

    # ---------- 自动修复 ----------
    repairing = [False]
    discovering = [False]

    def start_repair(reason=""):
        """先诊断再分级修复，每执行一步复测一次，网络恢复就收工。"""
        if netfix is None:
            ui_log("修复模块不可用（netfix.py 缺失或损坏）", "error")
            return
        if repairing[0]:
            return
        repairing[0] = True
        set_buttons_enabled(False)
        if reason:
            ui_log(reason, "warn")
        prog.configure(maximum=6, value=0)
        step_label.configure(text="正在诊断网络问题…", fg=SUB)

        def on_progress(text, level="info", index=None, total=None):
            root.after(0, lambda t=text, l=level, i=index, tt=total:
                       _show_repair_step(t, l, i, tt))

        def ask_confirm(tip):
            """子线程里要弹确认框：丢给主线程执行，然后等结果。"""
            res = [False]
            ev = threading.Event()

            def _ask():
                try:
                    res[0] = messagebox.askyesno("需要确认", tip)
                except Exception:
                    res[0] = False
                finally:
                    ev.set()

            root.after(0, _ask)
            ev.wait(180)
            return res[0]

        def worker():
            try:
                engine = netfix.RepairEngine(
                    cfg, check_fn=core.checker.check,
                    log=lambda m, l="info": ui_log(m, l),
                    progress=on_progress, confirm=ask_confirm)
                summary = engine.run(core.state or OFFLINE)
            except Exception as e:
                ui_log("修复流程异常：%s" % e, "error")
                summary = {"fixed": False}
            finally:
                root.after(0, lambda s=summary: finish_repair(s))

        threading.Thread(target=worker, daemon=True).start()

    def _show_repair_step(text, level, index, total):
        step_label.configure(text=text, fg=LEVEL_COLOR.get(level, SUB))
        if index is not None and total:
            prog.configure(maximum=total, value=index)

    def finish_repair(summary):
        repairing[0] = False
        _maybe_enable()
        if summary.get("fixed"):
            step_label.configure(text="自动修复完成 · 网络已恢复", fg="#4EC9B0")
            ui_log("自动修复完成：网络已恢复", "ok")
        else:
            step_label.configure(text="自动修复未成功，详见日志建议", fg="#F14C4C")
            ui_log("自动修复未能恢复网络，请按日志中的建议处理。", "error")
        threading.Thread(target=core.tick, daemon=True).start()

    def copy_url(_root):
        url = core.portal_url()
        _root.clipboard_clear()
        _root.clipboard_append(url)
        ui_log("已复制登录地址到剪贴板：" + url, "ok")

    # ---------- 网络工具 ----------
    tools = tk.Frame(root, bg=BG)
    tools.pack(fill="x", padx=12, pady=(0, 6))
    tk.Label(tools, text="工具", bg=BG, fg=SUB,
             font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 8))

    def _hidden_startupinfo():
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE：别让命令行窗口在前面闪
        return si

    def run_cmd(name, args, admin_hint=False):
        """后台跑系统命令，输出进日志，期间禁用按钮防止连点。"""
        def _run():
            ui_log("执行：%s" % name)
            try:
                r = subprocess.run(args, capture_output=True, text=True,
                                   encoding=console_encoding(), errors="replace",
                                   timeout=40, startupinfo=_hidden_startupinfo())
                out = ((r.stdout or "") + (r.stderr or "")).strip()
                if r.returncode == 0:
                    ui_log("%s 完成" % name, "ok")
                else:
                    ui_log("%s 失败，返回码 %s %s"
                           % (name, r.returncode,
                              "（可能需要以管理员身份运行本程序）" if admin_hint else ""),
                           "error")
                for line in (out.splitlines()[-3:] if out else ["(无输出)"]):
                    ui_log("  | " + line.strip())
            except Exception as e:
                ui_log("%s 执行异常：%s" % (name, e), "error")
            finally:
                root.after(0, lambda: set_buttons_enabled(True))
        set_buttons_enabled(False)
        threading.Thread(target=_run, daemon=True).start()

    def open_wifi_settings():
        try:
            os.startfile("ms-settings:network-wifi")
            ui_log("已打开 Windows WiFi 设置", "ok")
        except Exception as e:
            ui_log("打开 WiFi 设置失败：%s" % e, "error")

    def open_log():
        try:
            if not os.path.exists(LOG_FILE):
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.write("")
            os.startfile(LOG_FILE)
        except Exception as e:
            ui_log("打开日志失败：%s" % e, "error")

    b_wifi = btn(tools, "WiFi 设置", open_wifi_settings, padx=8)
    b_wifi.pack(side="left", padx=(0, 6))
    b_renew = btn(tools, "刷新 IP",
                  lambda: run_cmd("ipconfig /renew", ["ipconfig", "/renew"], True), padx=8)
    b_renew.pack(side="left", padx=(0, 6))
    b_dns = btn(tools, "清 DNS 缓存",
                lambda: run_cmd("ipconfig /flushdns", ["ipconfig", "/flushdns"], True), padx=8)
    b_dns.pack(side="left", padx=(0, 6))
    b_log = btn(tools, "查看日志", open_log, padx=8)
    b_log.pack(side="left")
    buttons.extend([(b_wifi, False), (b_renew, False), (b_dns, False), (b_log, False)])

    # ---------- 登录地址选择 ----------
    prefer_row = tk.Frame(root, bg=BG)
    prefer_row.pack(fill="x", padx=12, pady=(2, 2))
    tk.Label(prefer_row, text="登录地址", bg=BG, fg=SUB,
             font=("Microsoft YaHei UI", 9)).pack(side="left")

    label_to_value = {"自动选择（推荐）": "auto"}
    for p in cfg.get("portals", []):
        label_to_value["%s · %s" % (p.get("name"), p.get("host"))] = p.get("host")
    value_to_label = {v: k for k, v in label_to_value.items()}

    preferred_var = tk.StringVar(
        value=value_to_label.get(cfg.get("preferred", "auto"), "自动选择（推荐）"))

    ttk.Style().configure("TCombobox", fieldbackground="#333333", background="#333333",
                          foreground=FG, arrowcolor=FG, bordercolor=BORDER,
                          selectbackground="#333333", selectforeground=FG)
    root.option_add("*TCombobox*Listbox*Background", "#2b2b2b")
    root.option_add("*TCombobox*Listbox*Foreground", FG)
    root.option_add("*TCombobox*Listbox*selectBackground", ACCENT)

    combo = ttk.Combobox(prefer_row, values=list(label_to_value.keys()),
                         textvariable=preferred_var, state="readonly", width=26,
                         font=("Microsoft YaHei UI", 9))
    combo.pack(side="left", padx=8)

    def on_prefer_change(_event=None):
        val = label_to_value.get(preferred_var.get(), "auto")
        cfg["preferred"] = val
        core.cfg["preferred"] = val
        save_config(cfg)
        ui_log("登录地址切换为：%s" % preferred_var.get(), "ok")
        threading.Thread(target=core.tick, daemon=True).start()

    combo.bind("<<ComboboxSelected>>", on_prefer_change)

    # ---------- 自动发现认证地址 ----------
    def refresh_portal_combo():
        """重建下拉框选项，返回 标签->host 的映射。"""
        nonlocal label_to_value, value_to_label
        mapping = {"自动选择（推荐）": "auto"}
        for p in cfg.get("portals", []):
            mapping["%s · %s" % (p.get("name"), p.get("host"))] = p.get("host")
        label_to_value = mapping
        value_to_label = {v: k for k, v in mapping.items()}
        combo["values"] = list(mapping.keys())
        if preferred_var.get() not in mapping:
            preferred_var.set("自动选择（推荐）")
        return mapping

    def finish_discover(portal):
        discovering[0] = False
        _maybe_enable()
        if not portal or not portal.get("host"):
            ui_log("未发现新的认证地址（已联网时网关不重定向，属正常现象）", "info")
            step_label.configure(text="未发现新的认证地址", fg=SUB)
            return
        host = portal["host"]
        known_hosts = {p.get("host") for p in cfg.get("portals", [])}
        if host in known_hosts:
            # 已存在：若新发现带了 userip 参数，则补上参数
            for p in cfg["portals"]:
                if p.get("host") == host and portal.get("with_userip") and not p.get("with_userip"):
                    p["with_userip"] = True
                    p["wlanacip"] = portal.get("wlanacip", "") or p.get("wlanacip", "")
                    save_config(cfg)
                    ui_log("已为已有地址 %s 补全认证参数" % host, "ok")
                    break
            ui_log("发现的地址 %s 已在列表中" % host, "ok")
        else:
            cfg["portals"].append({
                "name": portal.get("name") or host,
                "host": host,
                "path": portal.get("path") or "/",
                "wlanacip": portal.get("wlanacip") or "",
                "with_userip": portal.get("with_userip", False),
                "https": portal.get("https", False),
            })
            save_config(cfg)
            ui_log("已添加并切换到新发现的认证地址：%s" % host, "ok")
        # 自动切到新发现的地址，方便立刻用
        cfg["preferred"] = host
        core.cfg["preferred"] = host
        save_config(cfg)
        mapping = refresh_portal_combo()
        for lbl, val in mapping.items():
            if val == host:
                preferred_var.set(lbl)
                break
        step_label.configure(text="发现并保存认证地址：%s" % host, fg="#4EC9B0")
        threading.Thread(target=core.tick, daemon=True).start()

    def do_discover():
        if checking[0]:
            return  # 正在检测就别并发发现
        discovering[0] = True
        set_buttons_enabled(False)
        step_label.configure(text="正在自动发现认证地址…", fg=SUB)
        ui_log("开始自动发现校园网认证地址（捕获网关重定向 / 探测内网地址）…", "info")

        def worker():
            try:
                portal = netfix.discover_portal(
                    cfg, log=lambda m, l="info": ui_log(m, l),
                    timeout=cfg.get("timeout", 5))
            except Exception as e:
                ui_log("自动发现异常：%s" % e, "error")
                portal = None
            root.after(0, lambda p=portal: finish_discover(p))

        threading.Thread(target=worker, daemon=True).start()

    b_discover = btn(prefer_row, "自动发现", do_discover, padx=8)
    b_discover.pack(side="left", padx=(8, 0))
    buttons.extend([(b_discover, False)])

    options = tk.Frame(root, bg=BG)
    options.pack(fill="x", padx=12, pady=(4, 10))

    def toggle_auto_open():
        cfg["auto_open"] = auto_open_var.get()
        save_config(cfg)
        ui_log("自动打开登录页：%s" % ("开" if cfg["auto_open"] else "关"))

    def toggle_autostart():
        ok = set_autostart(autostart_var.get())
        if not ok:
            autostart_var.set(not autostart_var.get())
            messagebox.showerror("失败", "设置开机自启失败，请以普通用户权限重试。")
        else:
            ui_log("开机自启：%s" % ("开" if autostart_var.get() else "关"), "ok")

    for text, var, cmd in (
        ("检测到需要认证时自动打开登录页", auto_open_var, toggle_auto_open),
        ("开机自动启动（后台静默运行）", autostart_var, toggle_autostart),
    ):
        cb = tk.Checkbutton(options, text=text, variable=var, command=cmd,
                            bg=BG, fg=FG, selectcolor="#1a1a1a",
                            activebackground=BG, activeforeground=FG,
                            font=("Microsoft YaHei UI", 9), anchor="w")
        cb.pack(fill="x", pady=2)

    # ---------- 日志区 ----------
    # 放最后并 expand：让它吸收剩余空间，保证上面的按钮永远不会被挤出可视区
    log_frame = tk.Frame(root, bg=BG)
    log_frame.pack(fill="both", expand=True, padx=12, pady=(4, 10))
    tk.Label(log_frame, text="运行日志", bg=BG, fg=SUB,
             font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 4))
    log_widget = tk.Text(log_frame, bg="#1a1a1a", fg=FG, insertbackground=FG,
                         relief="solid", bd=1, highlightbackground=BORDER,
                         font=("Consolas", 9), wrap="word", height=6)
    log_widget.pack(fill="both", expand=True)
    for lvl, color in LEVEL_COLOR.items():
        log_widget.tag_configure(lvl, foreground=color)
    log_widget.configure(state="disabled")
    _render_log()  # 补渲染控件创建前产生的日志

    def on_state(state, detail, changed):
        def _apply():
            text, color = STATE_TEXT[state]
            state_label.configure(text=text, fg=color)
            detail_label.configure(text=detail)
            ip_label.configure(text="本机 IP：%s" % get_local_ip())
            dot.itemconfigure(dot_oval, fill=color)
        root.after(0, _apply)

    core.on_state_change = on_state

    def on_close():
        core.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    ui_log("校园网认证助手已启动，认证服务器：%s"
           % "、".join(p["host"] for p in ordered_portals(cfg)))
    ui_log("提示：连上校园网后若浏览器没自动弹登录页，这里会自动帮你打开。")
    core.start()
    root.mainloop()


# --------------------------------------------------------------------------
# 开机自启（当前用户注册表 Run 项，无需管理员权限）
# --------------------------------------------------------------------------

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "CampusNetGuard"


def _autostart_command():
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    return '"%s" "%s" --silent' % (pyw, os.path.join(BASE_DIR, "campus_net_guard.py"))


def is_autostart_enabled():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return bool(value)
    except Exception:
        return False


def set_autostart(enabled):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_WRITE)
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------

def main():
    cfg = load_config()
    args = sys.argv[1:]

    if "--once" in args:
        log = file_logger()
        core = GuardCore(cfg, log)
        state, detail = core.tick()
        log("检测完成：%s（%s）" % (STATE_TEXT[state][0], detail))
        if state == NEED_AUTH or "--force-open" in args:
            core.open_login_page(force=True)
        # --fix 自动修复；高风险动作默认跳过，加 --yes 才执行
        if "--fix" in args and state != ONLINE:
            if netfix is None:
                log("修复模块不可用（netfix.py 缺失）", "error")
            else:
                engine = netfix.RepairEngine(cfg, lambda: core.checker.check(), log=log,
                                             confirm=(lambda t: "--yes" in args))
                engine.run(state)
        return 0

    if "--silent" in args:
        log = file_logger()
        log("静默模式启动")
        core = GuardCore(cfg, log)
        core.start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            core.stop()
        return 0

    run_gui(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
