#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoClicker —— macOS 自动点击器（AppKit 原生 UI）
=================================================
- 点击引擎：Quartz CGEvent（无 cliclick 20ms 硬限制）
- 界面：macOS 原生 AppKit（不再用 tkinter，规避 Tk 9.0 渲染空白问题）
- 停止机制：
    ① 预设时长 / 次数到达后自动停止
    ② 全局热键 F9 随时强制停止（运行中鼠标被锁时用键盘触发）
    ③ 界面"停止"按钮
- 平台：macOS（需辅助功能权限：系统设置→隐私与安全性→辅助功能→勾选本程序）
- 仅限单机本地测试

Bingo Studio · wljimmy@hotmail.com
本项目由 wljimmy 使用豆包 AI 完成
"""

import os
import time
import json
import threading
import collections

import objc
from Foundation import (
    NSObject, NSMakeRect, NSTimer, NSURL,
)
from AppKit import (
    NSApplication, NSWindow, NSButton, NSTextField, NSTextView, NSScrollView,
    NSPopUpButton, NSFont, NSColor, NSBackingStoreBuffered, NSSound,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
    NSApplicationActivationPolicyRegular,
    NSAlert, NSWorkspace, NSBox, NSAlertFirstButtonReturn,
    NSEvent, NSCommandKeyMask, NSShiftKeyMask,
    NSEventMaskKeyDown, NSEventMaskMouseMoved,
    NSMutableAttributedString, NSForegroundColorAttributeName,
    NSFontAttributeName,
    NSLineBreakByWordWrapping,
    NSTextAlignmentRight,
)

# ======================= Quartz（macOS 专用） =======================
try:
    from Quartz import (
        CGEventCreateMouseEvent, CGEventCreate, CGEventGetLocation, CGPointMake,
        kCGEventLeftMouseDown, kCGEventLeftMouseUp,
        CGEventPost, kCGHIDEventTap, kCGSessionEventTap,
        CGEventTapCreate, CGEventTapEnable, CGEventMaskBit,
        kCGEventKeyDown,
        kCGHeadInsertEventTap, kCGEventTapOptionDefault,
        CFMachPortCreateRunLoopSource, CFRunLoopGetCurrent,
        CFRunLoopGetMain, CFRunLoopAddSource, CFRunLoopRun,
        kCFRunLoopCommonModes, kCFAllocatorDefault,
        CGPreflightPostEventAccess, CGRequestPostEventAccess,
        CGMainDisplayID, CGDisplayBounds,
    )
    HAVE_QUARTZ = True
except ImportError:
    HAVE_QUARTZ = False


def mouse_click(x, y):
    """发送一次鼠标左键按下+抬起事件（不移动光标）。
    用 Session 层注入：与热键监听(HID层)分离，避免高速点击挤占键盘监听。"""
    if not HAVE_QUARTZ:
        return
    pt = CGPointMake(x, y)
    down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, pt, 0)
    CGEventPost(kCGSessionEventTap, down)
    up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, pt, 0)
    CGEventPost(kCGSessionEventTap, up)


STOP_KEYCODE = 7              # X 键（配合 Cmd+Shift）
STOP_MODS = 0x100000 | 0x20000  # Cmd(0x100000) + Shift(0x20000)
STOP_KEYCODE_F9 = 99          # 兜底：F9 单键也能停

# ---- 防呆 / 逃生机制 ----
MAX_RUNTIME_SEC = 3600        # 硬性最长运行 60 分钟，杜绝超长任务锁死电脑
MAX_COUNT = 1_000_000         # 单次最多点击次数上限
STOP_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stop.flag")

VERSION = "0.5.0"   # 版本号：UI 副标题 + 关于面板展示
GITHUB_URL = "https://github.com/wljimmy/AutoClicker"
# 版权三行（verbatim，用户锁定）
COPYRIGHT_1 = "Bingo Studio"
COPYRIGHT_2 = "wljimmy@hotmail.com"
COPYRIGHT_3 = "本项目是由wljimmy使用doubao AI完成"
SAVED_COORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_coords.json")
MULTI_POINTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi_points.json")

# ---- 鼠标晃动打断检测（打断性晃动 = 2 秒内 ≥3 次来回，每段 ≥200px） ----
SWING_MIN_DIST = 200.0        # 相邻两个方向顶点之间最小距离(px)，>= 才算一次晃动段
SWING_REQUIRED = 3            # 2 秒窗口内需要的晃动次数
SWING_WINDOW = 2.0            # 晃动计数时间窗(秒)
SWING_IDLE_SEC = 0.5          # 暂停后鼠标停止多久无晃动 → 自动恢复(秒)


# ======================= 国际化 / 多语言（可扩展） =======================
# 新增语言：在 LANGUAGES 与 I18N 各 key 里补一项即可，UI 无需改动。
LANGUAGES = ["中文", "English"]          # 对应 I18N 的 key 顺序：zh / en
LANG_KEYS = ["zh", "en"]
current_lang = "zh"                        # 默认中文

I18N = {
    # ---- 标题 ----
    "app_title":   {"zh": "AutoClicker", "en": "AutoClicker"},
    "app_sub":     {"zh": "macOS 自动点击器 · 坐标拾取 · 全局热键停止",
                    "en": "macOS Auto Clicker · Pick Coords · Global Hotkey Stop"},
    # ---- ① 坐标拾取 ----
    "grp1":        {"zh": "① 坐标拾取", "en": "① Pick Coordinates"},
    "lbl_x":       {"zh": "坐标 X:", "en": "X:"},
    "lbl_y":       {"zh": "Y:", "en": "Y:"},
    "lbl_int":     {"zh": "间隔(ms):", "en": "Interval(ms):"},
    "tip_interval": {"zh": "越小越快 · 游戏约 50 次/秒有效",
                     "en": "Smaller = Faster · ~50/s effective"},
    "btn_pick":    {"zh": "📌 拾取坐标 (5s)", "en": "📌 Pick (5s)"},
    "btn_test":    {"zh": "🎯 测试坐标", "en": "🎯 Test"},
    "tip_pick_tt": {"zh": "点击后 5 秒倒计时，结束时读取鼠标位置并填入坐标框",
                    "en": "5s countdown then read mouse pos"},
    "tip_test_tt": {"zh": "在坐标框当前位置执行一次点击，校验坐标是否准确",
                    "en": "click once at coords"},
    "lbl_pick_ready": {"zh": "倒计时：就绪（点后把鼠标移到目标点）",
                       "en": "Countdown: Ready (move mouse to target)"},
    # ---- ④ 多点队列 / 静音 / 倒计时 ----
    "grp4":         {"zh": "④ 多点队列", "en": "④ Multi-Point Queue"},
    "btn_qadd":     {"zh": "➕ 加入队列", "en": "➕ Add to Queue"},
    "btn_qdel":     {"zh": "🗑 删末项", "en": "🗑 Del Last"},
    "btn_qclear":   {"zh": "清空", "en": "Clear"},
    "lbl_qcount":   {"zh": "次数:", "en": "Times:"},
    "lbl_qint":     {"zh": "间隔(ms):", "en": "Gap(ms):"},
    "tip_queue":    {"zh": "坐标取上方 X/Y；次数=0 表示一直点；每轮按顺序点完整个队列再循环",
                     "en": "Uses X/Y above; Times=0 = forever; one pass = one round"},
    "que_empty":    {"zh": "队列为空。格式：序号 (X, Y) ×次数 间隔ms",
                     "en": "Queue empty. Row: # (X, Y) ×times gap ms"},
    "mode_queue":   {"zh": "多点队列(按时长)", "en": "Multi-Point (by duration)"},
    "chk_mute":     {"zh": "静音提示音", "en": "Mute beeps"},
    "lbl_elapsed":  {"zh": "已过 {t}", "en": "Elapsed {t}"},
    "lbl_left":     {"zh": "剩余 {t}", "en": "Left {t}"},
    "lbl_total":    {"zh": "总计 {t}", "en": "Total {t}"},
    "err_queue_empty": {"zh": "多点队列为空：先拾取坐标再点『加入队列』",
                        "en": "Queue is empty — pick a point and add it first"},
    "log_queue_add": {"zh": "➕ 队列 #{i}：({x}, {y}) ×{n} 次，间隔 {ms}ms",
                      "en": "➕ Queue #{i}: ({x}, {y}) ×{n}, gap {ms}ms"},
    "log_queue_del": {"zh": "🗑 已删除队列末项", "en": "🗑 Removed last item"},
    "log_queue_clear": {"zh": "🗑 队列已清空", "en": "🗑 Queue cleared"},
    "log_mute":     {"zh": "🔇 提示音已静音", "en": "🔇 Beeps muted"},
    "log_unmute":   {"zh": "🔊 提示音已恢复", "en": "🔊 Beeps on"},
    # ---- ② 运行设置 ----
    "grp2":        {"zh": "② 运行设置", "en": "② Run Settings"},
    "lbl_mode":    {"zh": "运行模式:", "en": "Mode:"},
    "mode_duration": {"zh": "按时长(秒)", "en": "Duration(s)"},
    "mode_count":  {"zh": "按次数", "en": "Count"},
    "lbl_value":   {"zh": "数值:", "en": "Value:"},
    "tip_stop":    {"zh": "停止：⌘Cmd+Shift+X / 停止按钮 / 自动到点",
                    "en": "Stop: ⌘Cmd+Shift+X / Button / Auto"},
    # ---- ③ 控制 ----
    "grp3":        {"zh": "③ 控制", "en": "③ Controls"},
    "btn_start":   {"zh": "▶ 开始", "en": "▶ Start"},
    "btn_test10":  {"zh": "测试 10 秒", "en": "Test 10s"},
    "btn_stop":    {"zh": "■ 停止", "en": "■ Stop"},
    "status_idle": {"zh": "状态：空闲", "en": "Status: Idle"},
    "status_run":  {"zh": "状态：运行中", "en": "Status: Running"},
    "lbl_count":   {"zh": "已投递：{n} 次", "en": "Sent: {n}"},
    "lbl_rate":    {"zh": "速率：{r:.1f} 次/秒", "en": "Rate: {r:.1f}/s"},
    "log_title":   {"zh": "运行日志", "en": "Run Log"},
    # ---- 日志动态文字 ----
    "log_ready":    {"zh": "就绪。", "en": "Ready"},
    "log_stopways": {"zh": "停止方式：动鼠标 / ⌘Cmd+Shift+X / F9 / 按钮 / 最长60分钟",
                     "en": "Stop: move mouse / ⌘Cmd+Shift+X / F9 / Button / max 60min"},
    "log_pause":    {"zh": "🖱 检测到鼠标移动，已暂停（晃动3次弹窗 / 停住0.5秒自动恢复 / 可划去窗口点按钮）",
                     "en": "🖱 Mouse moved — paused (shake×3 dialog / idle 0.5s auto-resume / move to window)"},
    "log_swing":    {"zh": "🌀 检测到剧烈晃动，弹确认框…",
                     "en": "🌀 Vigorous shake detected — asking…"},
    "log_swing_count": {"zh": "🔄 晃动段 +1（2秒内 {n} 次）",
                        "en": "🔄 Swing +1 ({n} in 2s)"},
    "log_resumed":  {"zh": "▶ 已继续（从暂停点恢复）", "en": "▶ Resumed (from pause)"},
    "log_autoresume": {"zh": "⏩ 鼠标已停止，自动恢复", "en": "⏩ Mouse idle — auto resumed"},
    "log_stopped_swing": {"zh": "⏹ 已中断（晃动打断）", "en": "⏹ Stopped (shake interrupt)"},
    "alert_pause":  {"zh": "点击已暂停", "en": "Clicking paused"},
    "msg_swing":    {"zh": "检测到鼠标剧烈晃动。\n『继续』从暂停点恢复点击，『中断』停止。",
                     "en": "Vigorous mouse shake detected.\nContinue to resume, or Stop to end."},
    "btn_continue": {"zh": "继续", "en": "Continue"},
    "btn_stop":     {"zh": "中断", "en": "Stop"},
    "log_countdown": {"zh": "倒计时：{n}s（请把鼠标移到目标点）",
                      "en": "Countdown: {n}s (move mouse to target)"},
    "log_picked":   {"zh": "✅ 拾取完成：({x}, {y})", "en": "✅ Picked: ({x}, {y})"},
    "log_pickcoord": {"zh": "📌 拾取坐标：({x}, {y})（已填入坐标框）",
                      "en": "📌 Picked: ({x}, {y}) (filled in)"},
    "log_testclick": {"zh": "🎯 测试坐标点击：({x}, {y})（若弹窗/按钮有反应即坐标正确）",
                      "en": "🎯 Test click: ({x}, {y}) (UI reacts = correct)"},
    "log_start":    {"zh": "▶ 开始：坐标({x},{y}) 间隔{i:.0f}ms 模式={m} 目标={v}",
                     "en": "▶ Start: ({x},{y}) gap {i:.0f}ms mode={m} target={v}"},
    "log_start_queue": {"zh": "▶ 开始：多点队列 {n} 个点，模式={m} 目标={v}",
                        "en": "▶ Start: queue of {n} points, mode={m} target={v}"},
    "log_finish":   {"zh": "■ 结束（{reason}）：共投递 {n} 次，耗时 {d}，速率 {r:.1f}/s",
                     "en": "■ End ({reason}): sent {n}, duration {d}, rate {r:.1f}/s"},
    "reason_target": {"zh": "达到目标自动停止", "en": "Target reached"},
    "reason_max":    {"zh": "达到最长运行时间(60分钟)", "en": "Max 60min reached"},
    "reason_flag":   {"zh": "检测到 stop.flag", "en": "stop.flag found"},
    "reason_manual": {"zh": "手动/热键/鼠标移动停止", "en": "Manual·hotkey·mouse"},
    "log_hotkey":   {"zh": "▶ 收到停止热键（⌘Cmd+Shift+X / F9），正在停止",
                     "en": "▶ Stop hotkey (⌘Cmd+Shift+X / F9) — stopping"},
    # ---- 报警框 ----
    "alert_notice": {"zh": "提示", "en": "Notice"},
    "msg_already":  {"zh": "已经在运行中，请先停止", "en": "Already running — please stop first"},
    "alert_error":  {"zh": "错误", "en": "Error"},
    "msg_noquartz": {"zh": "缺少 Quartz(pyobjc)。请先安装 pyobjc-framework-Quartz",
                     "en": "Quartz missing — install pyobjc-framework-Quartz"},
    "alert_perm":   {"zh": "没有辅助功能权限", "en": "Accessibility permission needed"},
    "msg_perm":     {"zh": "点击事件无法送达（会被系统静默丢弃）。\n请到 系统设置→隐私与安全性→辅助功能 勾选 AutoClicker，然后重新启动程序。",
                     "en": "Clicks are dropped silently without permission.\nEnable: System Settings → Privacy & Security → Accessibility → AutoClicker, then relaunch."},
    "alert_bad":    {"zh": "参数错误", "en": "Bad input"},
    "msg_coordnum": {"zh": "坐标必须是数字", "en": "Coordinates must be numbers"},
    "err_interval": {"zh": "间隔不能为负数", "en": "Interval must be ≥ 0"},
    "err_value":    {"zh": "时长/次数必须大于0", "en": "Duration or count must be > 0"},
    "err_duration": {"zh": "时长不能超过 {n} 分钟（防呆上限，防止锁死电脑）",
                     "en": "Max {n} min (safety cap)"},
    "err_count":    {"zh": "次数不能超过 {n:,}（防呆上限）", "en": "Max {n:,} clicks (safety cap)"},
    # ---- 权限检测 ----
    "log_noquartz": {"zh": "⚠️ 当前环境缺少 Quartz，点击无法生效。",
                     "en": "⚠️ Quartz missing — clicks won't work"},
    "log_perm_ok":  {"zh": "✅ 辅助功能权限正常，点击事件可送达。",
                     "en": "✅ Accessibility OK — clicks delivered"},
    "log_perm_no":  {"zh": "⚠️ 缺少辅助功能权限！点击事件将被系统静默丢弃（点了没反应）。",
                     "en": "⚠️ Accessibility permission missing! Clicks will be silently dropped."},
    "alert_perm2":  {"zh": "需要辅助功能授权", "en": "Accessibility authorization needed"},
    "msg_perm2":    {"zh": "AutoClicker 需要『辅助功能』权限才能发送鼠标点击。\n\n"
                           "即将打开系统设置 → 隐私与安全性 → 辅助功能。\n"
                           "请在列表中勾选 AutoClicker，然后关闭设置、重新启动本程序。",
                     "en": "AutoClicker needs Accessibility permission to send clicks.\n\n"
                           "Opening System Settings → Privacy & Security → Accessibility.\n"
                           "Check AutoClicker in the list, then close Settings and relaunch."},
    # ---- 坐标保存 ----
    "btn_save":     {"zh": "💾 保存", "en": "💾 Save"},
    "field_name":   {"zh": "名称 Name", "en": "Name 名称"},
    "ph_saved":     {"zh": "— 已保存坐标 —", "en": "— Saved —"},
    "tip_saved":    {"zh": "命名后保存当前坐标，可从下拉直接载入",
                     "en": "Name & save current coords, load from dropdown"},
    "log_saved":    {"zh": "💾 已保存坐标『{name}』：({x}, {y})",
                     "en": "💾 Saved '{name}': ({x}, {y})"},
    "log_load":     {"zh": "📂 载入坐标『{name}』：({x}, {y})",
                     "en": "📂 Loaded '{name}': ({x}, {y})"},
    "err_noname":   {"zh": "请输入坐标名称", "en": "Enter a coordinate name"},
    "err_coordnum": {"zh": "坐标格式无效，请输入整数", "en": "Invalid coordinates, enter integers"},
    "alert_saved":  {"zh": "坐标保存", "en": "Save coordinates"},
    # ---- 版本号 + 关于 ----
    "btn_about":    {"zh": "关于", "en": "About"},
    "alert_about":  {"zh": "关于 AutoClicker", "en": "About AutoClicker"},
    "msg_about":    {"zh": "macOS 自动点击器（AppKit 原生 UI + Quartz CGEvent）\n"
                           "版本 v{n}\n\n"
                           "GitHub：{url}\n\n"
                           "版权：\n"
                           "{c1}\n{c2}\n{c3}",
                     "en": "macOS Auto Clicker (AppKit native UI + Quartz CGEvent)\n"
                           "Version v{n}\n\n"
                           "GitHub: {url}\n\n"
                           "Copyright:\n"
                           "{c1}\n{c2}\n{c3}"},
    "btn_openrepo": {"zh": "打开仓库", "en": "Open Repo"},
    "btn_close":    {"zh": "关闭", "en": "Close"},
}


def T(key, **kw):
    """取当前语言文本；带占位符时格式化。"""
    s = I18N[key][current_lang]
    return s.format(**kw) if kw else s


def fmt_hms(sec):
    """秒 → HH:MM:SS（倒计时显示统一格式，从小时开始）。"""
    sec = max(0, int(sec))
    return "%02d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)


# ======================= 点击控制核心 =======================
class ClickWorker:
    """后台点击线程：按 points 列表循环点击、统计、自动/手动停止。线程安全。

    points: [{"x": int, "y": int, "count": int(0=无限), "interval_ms": float}]
    mode:   "duration"（按总时长秒）| "count"（按总次数）
    队列模式：每轮按列表顺序把每个点按其次数点完，再进入下一轮。
    计时只统计"活跃运行时间"：暂停期间冻结，恢复后继续累加。
    """

    def __init__(self, points, mode, value):
        self.points = list(points)
        self.mode = mode
        self.value = value
        self.stop_event = threading.Event()
        self._paused = threading.Event()
        self._paused.clear()
        self.count = 0
        self.rounds = 0
        self.start_time = 0.0
        self._accum = 0.0          # 已累计的活跃秒数（不含当前段）
        self._seg_start = 0.0
        self.running = False
        self.finished_reason = ""
        self._logs = collections.deque()
        self._lock = threading.Lock()

    def request_stop(self):
        self.stop_event.set()

    def pause(self):
        """暂停点击（不终止，计数保留，可 resume）。暂停即冻结计时。"""
        if not self._paused.is_set() and self._seg_start > 0:
            self._accum += time.time() - self._seg_start
        self._paused.set()

    def resume(self):
        if self._paused.is_set():
            self._paused.clear()
            self._seg_start = time.time()

    @property
    def elapsed(self):
        """活跃运行秒数（暂停冻结）。"""
        e = self._accum
        if self.running and not self._paused.is_set() and self._seg_start > 0:
            e += time.time() - self._seg_start
        return e

    def add_log(self, text):
        with self._lock:
            self._logs.append(f"[{time.strftime('%H:%M:%S')}] {text}")

    def drain_logs(self):
        with self._lock:
            out = list(self._logs)
            self._logs.clear()
        return out

    @property
    def rate(self):
        e = self.elapsed
        if not self.running or e <= 0:
            return 0.0
        return self.count / max(0.001, e)

    def start(self):
        self.stop_event.clear()
        self.count = 0
        self.rounds = 0
        self._accum = 0.0
        self._seg_start = 0.0
        self.running = True
        t = threading.Thread(target=self._run, daemon=True)
        self._thread = t
        t.start()

    def _run(self):
        self.start_time = time.time()
        self._seg_start = self.start_time
        now_str = time.strftime("%H:%M:%S")
        if len(self.points) == 1 and not self.points[0].get("count", 0):
            p0 = self.points[0]
            self.add_log(T("log_start", x=p0["x"], y=p0["y"],
                           i=float(p0.get("interval_ms", 0)), m=self.mode, v=self.value))
        else:
            self.add_log(T("log_start_queue", n=len(self.points),
                           m=self.mode, v=self.value))

        reason = ""
        while not self.stop_event.is_set() and not reason:
            self.rounds += 1
            for p in self.points:
                if self.stop_event.is_set() or reason:
                    break
                px, py = p["x"], p["y"]
                limit = int(p.get("count", 0) or 0)
                iv = max(0.0, float(p.get("interval_ms", 0) or 0)) / 1000.0
                done_in_item = 0
                while not self.stop_event.is_set():
                    # 暂停态：不投递、不计数、不计时，鼠标自由
                    while self._paused.is_set() and not self.stop_event.is_set():
                        time.sleep(0.02)
                    if self.stop_event.is_set():
                        break
                    # 逃生①：硬性时长上限
                    if self.elapsed >= MAX_RUNTIME_SEC:
                        reason = T("reason_max")
                        break
                    # 逃生②：stop.flag 文件兜底
                    if os.path.exists(STOP_FLAG):
                        reason = T("reason_flag")
                        break

                    mouse_click(px, py)
                    self.count += 1
                    done_in_item += 1

                    if self.mode == "count" and self.count >= self.value:
                        reason = T("reason_target")
                        break
                    if self.mode == "duration" and self.elapsed >= self.value:
                        reason = T("reason_target")
                        break
                    if limit and done_in_item >= limit:
                        break
                    if iv > 0:
                        deadline = time.time() + iv
                        while time.time() < deadline:
                            if self.stop_event.is_set():
                                break
                            time.sleep(0.0005)
                if reason:
                    break

        self._accum = self.elapsed
        elapsed = self._accum
        self.running = False
        if not reason:
            reason = T("reason_manual")
        self.finished_reason = reason
        self.add_log(T("log_finish", reason=reason,
                       n=self.count, d=fmt_hms(elapsed),
                       r=(self.count / elapsed) if elapsed > 0 else 0.0))


# ======================= 全局热键监听 =======================
class HotkeyListener:
    """全局热键：⌘Cmd+Shift+X 强制停止（F9 兜底）。
    用 NSEvent 全局监听 + Quartz event tap 双保险，与鼠标注入(Session层)分离。"""

    def __init__(self, get_worker):
        self.get_worker = get_worker
        self.tap = None
        self._mon = None

    def start(self):
        """在主线程调用。挂上两条独立的键盘监听通道（任一触发即停）。"""
        # 通道①：NSEvent 全局键盘监听（AppKit 层，独立于 Quartz 注入通道）
        try:
            self._mon = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSEventMaskKeyDown, self._ns_callback)
        except Exception:
            self._mon = None
        # 通道②：Quartz event tap（HID 层键盘）
        if not HAVE_QUARTZ:
            return
        try:
            mask = CGEventMaskBit(kCGEventKeyDown)
            self.tap = CGEventTapCreate(
                kCGHIDEventTap, kCGHeadInsertEventTap,
                kCGEventTapOptionDefault, mask, self._callback, None)
            if self.tap is None:
                return
            CGEventTapEnable(self.tap, True)   # 关键：不 enable 监听不会工作
            src = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, self.tap, 0)
            CFRunLoopAddSource(CFRunLoopGetMain(), src, kCFRunLoopCommonModes)
        except Exception:
            pass

    def _ns_callback(self, event):
        try:
            keycode = event.keyCode()
            mods = event.modifierFlags()
            hit = False
            if keycode == STOP_KEYCODE and (mods & (NSShiftKeyMask | NSCommandKeyMask)) == (NSShiftKeyMask | NSCommandKeyMask):
                hit = True
            elif keycode == STOP_KEYCODE_F9:
                hit = True
            if hit:
                self._trigger_stop()
        except Exception:
            pass

    def _callback(self, proxy, etype, event, refcon):
        try:
            keycode = event.getIntegerValueField(0)   # kCGKeyboardEventKeycode
            flags = event.getIntegerValueField(1)     # kCGKeyboardEventEventFlags
            hit = False
            if keycode == STOP_KEYCODE and (flags & STOP_MODS) == STOP_MODS:
                hit = True
            elif keycode == STOP_KEYCODE_F9:
                hit = True
            if hit:
                self._trigger_stop()
        except Exception:
            pass
        return event

    def _trigger_stop(self):
        w = self.get_worker()
        if w is not None:
            w.add_log(T("log_hotkey"))
            w.request_stop()


# ======================= AppKit 原生 UI =======================
class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, notification):
        self.worker = None
        self.log_view = None
        self._beep_snd = None   # 倒计时"滴"音效缓存实例
        self.muted = False      # 提示音静音开关（复选框控制）
        # ---- 鼠标晃动打断状态机初始状态 ----
        self._int_state = "idle"            # idle | paused_wait | dialog
        self._last_move_ts = 0.0
        self._swing_count = 0
        self._swing_times = collections.deque()
        self._axis_accum = {"x": 0.0, "y": 0.0}
        self._axis_dir = {"x": 0, "y": 0}
        self._prev_vertex = None
        self._last_pos = None
        self._mouse_mon = None
        self.build_ui()
        # 全局热键
        HotkeyListener(lambda: self.worker).start()
        # 定时刷新 UI
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.3, self, objc.selector(self.refresh_), None, True)
        self.add_log(T("log_ready"))
        self.check_accessibility()
        # 逃生通道③：真实鼠标移动即停（键盘被游戏抢走时，动一下鼠标就能夺回控制权）
        try:
            self._mouse_mon = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSEventMaskMouseMoved, self._on_mouse_moved)
        except Exception:
            self._mouse_mon = None
        self.add_log(T("log_stopways"))

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        """关闭主窗口即退出整个程序，避免后台残进程导致下次启动失效。"""
        try:
            if self.worker is not None:
                self.worker.request_stop()
        except Exception:
            pass
        try:
            if self._mouse_mon is not None:
                NSEvent.removeMonitor_(self._mouse_mon)
                self._mouse_mon = None
        except Exception:
            pass
        return True

    def windowWillClose_(self, notification):
        """窗口关闭兜底：直接请求退出整个 app，不依赖单一 delegate 判定。"""
        try:
            if self.worker is not None:
                self.worker.request_stop()
        except Exception:
            pass
        try:
            if self._mouse_mon is not None:
                NSEvent.removeMonitor_(self._mouse_mon)
                self._mouse_mon = None
        except Exception:
            pass
        NSApplication.sharedApplication().terminate_(None)

    @objc.python_method
    def _on_mouse_moved(self, event):
        """鼠标移动检测状态机：
        idle →(移动) paused_wait →(2秒内≥3次晃动) dialog
        paused_wait →(鼠标停止0.5秒无晃动) 自动恢复 idle
        dialog → 用户选 继续(恢复) / 中断(停止)，死等，不再自动恢复。
        运行中才响应；暂停后鼠标归还用户，可直接划去窗口点按钮。"""
        w = self.worker
        if w is None or not w.running:
            return
        now = time.time()
        self._last_move_ts = now

        if self._int_state == "idle":
            # 运行中检测到鼠标移动 → 立即暂停（先暂停再测，解掉游戏拉扯）
            w.pause()
            self._int_state = "paused_wait"
            self._swing_count = 0
            self._swing_times = collections.deque()
            self._axis_accum = {"x": 0.0, "y": 0.0}
            self._axis_dir = {"x": 0, "y": 0}
            self._prev_vertex = None
            w.add_log(T("log_pause"))
            return

        if self._int_state == "paused_wait":
            self._detect_swing(event, now)
            return

        # dialog 态：只更新时间戳，等待用户选择（弹窗内不再做检测）

    @objc.python_method
    def _detect_swing(self, event, now):
        """主导轴方向反转 → 顶点；相邻顶点距离≥SWING_MIN_DIST → 一次晃动；
        2 秒窗口内累计 SWING_REQUIRED 次 → 打断性晃动，进入弹窗。
        用绝对坐标（暂停后鼠标自由，绝对位移可靠；全局 monitor 的 delta 常为 0）。"""
        try:
            p = NSEvent.mouseLocation()
            x, y = p.x, p.y
        except Exception:
            return
        if self._last_pos is not None:
            dx = x - self._last_pos[0]
            dy = y - self._last_pos[1]
            if abs(dx) >= abs(dy):
                axis, val = "x", dx
            else:
                axis, val = "y", dy
            self._axis_accum[axis] += val
            cur_sign = 1 if self._axis_accum[axis] > 0 else (-1 if self._axis_accum[axis] < 0 else 0)
            prev_sign = self._axis_dir[axis]
            if cur_sign != 0 and prev_sign != 0 and cur_sign != prev_sign:
                # 到达方向反转顶点
                if self._prev_vertex is not None:
                    d = ((x - self._prev_vertex[0]) ** 2 +
                         (y - self._prev_vertex[1]) ** 2) ** 0.5
                    if d >= SWING_MIN_DIST:
                        self._swing_count += 1
                        self._swing_times.append(now)
                        while self._swing_times and now - self._swing_times[0] > SWING_WINDOW:
                            self._swing_times.popleft()
                        w = self.worker
                        if w is not None:
                            w.add_log(T("log_swing_count", n=len(self._swing_times)))
                        if len(self._swing_times) >= SWING_REQUIRED:
                            self._enter_dialog()
                            return
                self._prev_vertex = (x, y)
            if cur_sign != 0:
                self._axis_dir[axis] = cur_sign
        self._last_pos = (x, y)

    @objc.python_method
    def _enter_dialog(self):
        """已暂停；弹模态框死等用户选 继续/中断（不再自动恢复）。"""
        self._int_state = "dialog"
        w = self.worker
        if w is None:
            self._int_state = "idle"
            return
        w.add_log(T("log_swing"))
        choice = self.show_alert_choice(T("alert_pause"), T("msg_swing"),
                                        T("btn_continue"), T("btn_stop"))
        if choice == "continue":
            w.resume()
            self._int_state = "idle"
            w.add_log(T("log_resumed"))
        else:
            w.request_stop()
            self._int_state = "idle"
            w.add_log(T("log_stopped_swing"))

    @objc.python_method
    def _auto_resume(self):
        """鼠标停止 SWING_IDLE_SEC 秒且未触发晃动 → 自动恢复原流程。"""
        if self._int_state != "paused_wait":
            return
        w = self.worker
        if w is None or not w.running:
            self._int_state = "idle"
            return
        if len(self._swing_times) >= SWING_REQUIRED:
            return  # 已满足晃动，交给 _enter_dialog
        w.resume()
        self._int_state = "idle"
        w.add_log(T("log_autoresume"))

    @objc.python_method
    def show_alert_choice(self, title, msg, btn_continue, btn_stop):
        """模态对话框：两个按钮，死等用户选择。返回 'continue' 或 'stop'。"""
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(msg)
        alert.addButtonWithTitle_(btn_continue)
        alert.addButtonWithTitle_(btn_stop)
        try:
            ret = alert.runModal()
            return "continue" if ret == NSAlertFirstButtonReturn else "stop"
        except Exception:
            return "stop"

    @objc.python_method
    def check_accessibility(self):
        """检测辅助功能权限：无权限时 CGEventPost 会静默丢弃事件（表现为点了没反应）。"""
        if not HAVE_QUARTZ:
            self.add_log(T("log_noquartz"))
            return
        try:
            ok = CGPreflightPostEventAccess()
        except Exception:
            ok = False
        if ok:
            self.add_log(T("log_perm_ok"))
            return
        self.add_log(T("log_perm_no"))
        self.show_alert(T("alert_perm2"), T("msg_perm2"))
        try:
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"))
        except Exception:
            pass
        try:
            CGRequestPostEventAccess()
        except Exception:
            pass

    # ---------- 控件 ----------
    @objc.python_method
    def build_ui(self):
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 600, 900),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
            NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered, False)
        self.window.setTitle_("AutoClicker - 自动点击器 / Auto Clicker")
        # 固定 600×900，按主显示器物理尺寸居中
        # （CGDisplayBounds 不依赖 NSScreen，启动早期也可靠；center() 在多屏环境会把窗口甩到屏外）
        def _place_window():
            try:
                b = CGDisplayBounds(CGMainDisplayID())
                sw, sh = float(b.size.width), float(b.size.height)
            except Exception:
                sw, sh = 1440.0, 900.0
            wx = max(0.0, (sw - 600) / 2)
            wy = max(0.0, (sh - 928) / 2)   # 928 = 900 内容高 + 标题栏
            # 928 = 900 内容高 + 28 标题栏；setFrame 设的是含标题栏的 frame，少了会把顶部控件裁掉
            self.window.setFrame_display_(NSMakeRect(wx, wy, 600, 928), True)
        self._place_window = _place_window
        _place_window()
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)   # 关窗即退出的兜底回调
        content = self.window.contentView()
        self._ui_keys = []          # (控件, i18n key)：语言切换时统一刷新

        def keep(ctrl, key):
            content.addSubview_(ctrl)
            if key:
                self._ui_keys.append((ctrl, key))
            return ctrl

        # ---------- 大标题 + 语言切换 ----------
        title = NSTextField.labelWithString_(T("app_title"))
        title.setFont_(NSFont.boldSystemFontOfSize_(22))
        title.setTextColor_(NSColor.controlAccentColor())
        title.setFrame_(NSMakeRect(16, 854, 300, 30))
        keep(title, "app_title")
        sub = NSTextField.labelWithString_(T("app_sub"))
        sub.setFont_(NSFont.systemFontOfSize_(12))
        sub.setTextColor_(NSColor.secondaryLabelColor())
        sub.setFrame_(NSMakeRect(16, 830, 420, 18))
        keep(sub, "app_sub")
        # 版本号（副标题右侧，mono 等宽）
        self.lbl_version = NSTextField.labelWithString_("v" + VERSION)
        self.lbl_version.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, 4))
        self.lbl_version.setTextColor_(NSColor.tertiaryLabelColor())
        self.lbl_version.setAlignment_(NSTextAlignmentRight)
        self.lbl_version.setFrame_(NSMakeRect(440, 830, 144, 18))
        content.addSubview_(self.lbl_version)
        # 关于按钮（右上角，语言下拉左侧）
        self.btn_about = self._button(T("btn_about"), 372, 856, 72, 26, self.about_)
        keep(self.btn_about, "btn_about")
        # 语言下拉（右上角）
        self.popup_lang = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(452, 856, 132, 26), False)
        self.popup_lang.addItemsWithTitles_(LANGUAGES)
        try:
            self.popup_lang.selectItemAtIndex_(LANG_KEYS.index(current_lang))
        except Exception:
            self.popup_lang.selectItemAtIndex_(0)
        self.popup_lang.menu().setAutoenablesItems_(False)
        self.popup_lang.setTarget_(self)
        self.popup_lang.setAction_(objc.selector(self.changeLang_))
        content.addSubview_(self.popup_lang)
        content.addSubview_(self._separator(16, 816, 568))

        # ---------- ① 坐标拾取 ----------
        keep(self._group_title(T("grp1"), 16, 780), "grp1")
        keep(self._label(T("lbl_x"), 16, 744, 78, 24), "lbl_x")
        self.field_x = self._input("2852", 96, 744, 62, 26)
        keep(self.field_x, None)
        keep(self._label(T("lbl_y"), 164, 744, 42, 24), "lbl_y")
        self.field_y = self._input("545", 208, 744, 62, 26)
        keep(self.field_y, None)
        keep(self._label(T("lbl_int"), 276, 744, 92, 24), "lbl_int")
        self.field_interval = self._input("1", 370, 744, 62, 26)
        keep(self.field_interval, None)
        keep(self._multiline_label(T("tip_interval"), 438, 736, 154, 40, font_size=9), "tip_interval")

        self.btn_pick = self._button(T("btn_pick"), 16, 706, 176, 34, self.pick_)
        self.btn_pick.setToolTip_(T("tip_pick_tt"))
        keep(self.btn_pick, "btn_pick")
        self.btn_test_pos = self._button(T("btn_test"), 200, 706, 140, 34, self.testPos_)
        self.btn_test_pos.setToolTip_(T("tip_test_tt"))
        keep(self.btn_test_pos, "btn_test")
        self.lbl_pick = self._multiline_label(T("lbl_pick_ready"), 350, 701, 240, 40, font_size=10)
        keep(self.lbl_pick, "lbl_pick_ready")

        # ---- 坐标保存行 ----
        self.field_saved_name = self._input("", 16, 670, 150, 26)
        self.field_saved_name.setPlaceholderString_(T("field_name"))
        keep(self.field_saved_name, None)
        self.btn_save = self._button(T("btn_save"), 172, 670, 90, 26, self.saveCoord_)
        keep(self.btn_save, "btn_save")
        self.popup_saved = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(268, 670, 150, 26), False)
        self.popup_saved.addItemWithTitle_(T("ph_saved"))
        self.popup_saved.menu().setAutoenablesItems_(False)
        self.popup_saved.setTarget_(self)
        self.popup_saved.setAction_(objc.selector(self.coordsChanged_))
        content.addSubview_(self.popup_saved)
        keep(self._multiline_label(T("tip_saved"), 424, 662, 160, 32, font_size=9), "tip_saved")
        content.addSubview_(self._separator(16, 660, 568))
        self._reload_saved_list()

        # ---------- ④ 多点队列 ----------
        keep(self._group_title(T("grp4"), 16, 626), "grp4")
        qscroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16, 520, 568, 98))
        qscroll.setHasVerticalScroller_(True)
        qscroll.setAutohidesScrollers_(True)
        self.queue_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 548, 98))
        self.queue_view.setEditable_(False)
        self.queue_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11, 0))
        self.queue_view.setBackgroundColor_(NSColor.textBackgroundColor())
        self.queue_view.setTextColor_(NSColor.textColor())
        self.queue_view.setTextContainerInset_((4, 6))
        qscroll.setDocumentView_(self.queue_view)
        content.addSubview_(qscroll)

        keep(self._label(T("lbl_qcount"), 16, 490, 54, 24), "lbl_qcount")
        self.field_qcount = self._input("1", 74, 490, 58, 26)
        content.addSubview_(self.field_qcount)
        keep(self._label(T("lbl_qint"), 136, 490, 70, 24), "lbl_qint")
        self.field_qint = self._input("100", 210, 490, 64, 26)
        content.addSubview_(self.field_qint)
        self.btn_qadd = self._button(T("btn_qadd"), 272, 488, 140, 30, self.queueAdd_)
        keep(self.btn_qadd, "btn_qadd")
        self.btn_qdel = self._button(T("btn_qdel"), 416, 488, 96, 30, self.queueDel_)
        keep(self.btn_qdel, "btn_qdel")
        self.btn_qclear = self._button(T("btn_qclear"), 512, 488, 72, 30, self.queueClear_)
        keep(self.btn_qclear, "btn_qclear")
        keep(self._multiline_label(T("tip_queue"), 16, 464, 568, 18, font_size=9), "tip_queue")
        content.addSubview_(self._separator(16, 458, 568))

        # ---------- ② 运行设置 ----------
        keep(self._group_title(T("grp2"), 16, 424), "grp2")
        keep(self._label(T("lbl_mode"), 16, 392, 96, 24), "lbl_mode")
        self.popup_mode = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(116, 392, 190, 26), False)
        self.popup_mode.addItemsWithTitles_([T("mode_duration"), T("mode_count"), T("mode_queue")])
        self.popup_mode.menu().setAutoenablesItems_(False)
        content.addSubview_(self.popup_mode)
        keep(self._label(T("lbl_value"), 312, 392, 60, 24), "lbl_value")
        self.field_value = self._input("60", 372, 392, 62, 26)
        keep(self.field_value, None)
        # 静音开关
        self.chk_mute = NSButton.checkboxWithTitle_target_action_(
            T("chk_mute"), self, objc.selector(self.toggleMute_))
        self.chk_mute.setFrame_(NSMakeRect(446, 390, 138, 24))
        self.chk_mute.setState_(0)
        self.chk_mute.setFont_(NSFont.systemFontOfSize_(12))
        keep(self.chk_mute, "chk_mute")
        keep(self._multiline_label(T("tip_stop"), 16, 362, 568, 18, font_size=9), "tip_stop")
        content.addSubview_(self._separator(16, 354, 568))

        # ---------- ③ 控制 ----------
        keep(self._group_title(T("grp3"), 16, 320), "grp3")
        self.btn_start = self._button(T("btn_start"), 16, 276, 140, 34, self.start_)
        keep(self.btn_start, "btn_start")
        self.btn_test = self._button(T("btn_test10"), 166, 276, 148, 34, self.test_)
        keep(self.btn_test, "btn_test10")
        self.btn_stop = self._button(T("btn_stop"), 324, 276, 140, 34, self.stop_)
        self.btn_stop.setEnabled_(False)
        keep(self.btn_stop, "btn_stop")

        self.lbl_status = self._label(T("status_idle"), 16, 240, 180, 22)
        content.addSubview_(self.lbl_status)
        self.lbl_count = self._label(T("lbl_count", n=0), 204, 240, 150, 22)
        content.addSubview_(self.lbl_count)
        self.lbl_rate = self._label(T("lbl_rate", r=0.0), 360, 240, 220, 22)
        content.addSubview_(self.lbl_rate)

        # ---- 倒计时状态行：已过 / 剩余 / 总计 ----
        self.lbl_elapsed = self._label(T("lbl_elapsed", t="00:00:00"), 16, 212, 190, 20, bold=True)
        content.addSubview_(self.lbl_elapsed)
        self.lbl_left = self._label(T("lbl_left", t="00:00:00"), 214, 212, 190, 20, bold=True)
        content.addSubview_(self.lbl_left)
        self.lbl_total = self._label(T("lbl_total", t="00:00:00"), 410, 212, 174, 20)
        self.lbl_total.setAlignment_(NSTextAlignmentRight)
        content.addSubview_(self.lbl_total)

        # ---------- 日志 ----------
        keep(self._label(T("log_title"), 16, 186, 200, 20, bold=True), "log_title")
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(10, 10, 580, 168))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        self.log_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 560, 168))
        self.log_view.setEditable_(False)
        self.log_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11, 0))
        self.log_view.setBackgroundColor_(NSColor.textBackgroundColor())
        self.log_view.setTextColor_(NSColor.textColor())
        self.log_view.setTextContainerInset_((4, 6))
        scroll.setDocumentView_(self.log_view)
        content.addSubview_(scroll)

        self._queue = self._load_queue()
        self._refresh_queue_view()
        self.window.makeKeyAndOrderFront_(None)
        try:
            self._place_window()
        except Exception:
            pass
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def changeLang_(self, sender):
        """语言下拉切换：刷新所有静态控件 + 模式项 + 动态状态。
        注意：不能加 @objc.python_method（否则无法作为 ObjC action 被分发）。"""
        global current_lang
        idx = sender.indexOfSelectedItem()
        if 0 <= idx < len(LANG_KEYS):
            current_lang = LANG_KEYS[idx]
            self._apply_lang()

    @objc.python_method
    def _apply_lang(self):
        for ctrl, key in self._ui_keys:
            ctrl.setStringValue_(T(key))
        # 运行模式下拉项（保留当前选中）
        sel = self.popup_mode.indexOfSelectedItem()
        self.popup_mode.removeAllItems()
        self.popup_mode.addItemsWithTitles_([T("mode_duration"), T("mode_count"), T("mode_queue")])
        if 0 <= sel < 3:
            self.popup_mode.selectItemAtIndex_(sel)
        self._refresh_queue_view()
        # 坐标保存：占位项 + 名称输入占位
        self.field_saved_name.setPlaceholderString_(T("field_name"))
        if hasattr(self, "popup_saved") and self._saved_coords is not None:
            self._reload_saved_list()
        # 按钮 tooltip
        self.btn_pick.setToolTip_(T("tip_pick_tt"))
        self.btn_test_pos.setToolTip_(T("tip_test_tt"))
        # 动态状态（运行中/空闲、投递数、速率）
        running = self.worker is not None and self.worker.running
        self.lbl_status.setStringValue_(T("status_run") if running else T("status_idle"))
        w = self.worker
        if w is not None:
            self.lbl_count.setStringValue_(T("lbl_count", n=w.count))
            self.lbl_rate.setStringValue_(T("lbl_rate", r=w.rate))
        else:
            self.lbl_count.setStringValue_(T("lbl_count", n=0))
            self.lbl_rate.setStringValue_(T("lbl_rate", r=0.0))

    # ---------- 辅助构建 ----------
    @objc.python_method
    def _group_title(self, text, x, y):
        lbl = NSTextField.labelWithString_(text)
        lbl.setFont_(NSFont.systemFontOfSize_weight_(13, 600))
        lbl.setTextColor_(NSColor.controlAccentColor())
        lbl.setFrame_(NSMakeRect(x, y, 300, 22))
        return lbl

    @objc.python_method
    def _separator(self, x, y, w):
        box = NSBox.alloc().initWithFrame_(NSMakeRect(x, y, w, 1))
        box.setBoxType_(2)  # NSBoxSeparator
        return box

    @objc.python_method
    def _label(self, text, x, y, w, h, bold=False, small=False):
        lbl = NSTextField.labelWithString_(text)
        lbl.setFrame_(NSMakeRect(x, y, w, h))
        if bold:
            lbl.setFont_(NSFont.boldSystemFontOfSize_(13))
        elif small:
            lbl.setFont_(NSFont.systemFontOfSize_(11))
            lbl.setTextColor_(NSColor.secondaryLabelColor())
        return lbl

    @objc.python_method
    def _multiline_label(self, text, x, y, w, h, font_size=11, bold=False, color=None):
        """说明文字用：自动按单词换行（word wrapping），多行显示，不再截断/溢出界面。"""
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        lbl.setStringValue_(text)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setBordered_(False)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setLineBreakMode_(NSLineBreakByWordWrapping)
        lbl.setUsesSingleLineMode_(False)
        lbl.cell().setWraps_(True)
        lbl.setFont_(NSFont.boldSystemFontOfSize_(font_size) if bold else NSFont.systemFontOfSize_(font_size))
        lbl.setTextColor_(color if color is not None else NSColor.secondaryLabelColor())
        return lbl

    @objc.python_method
    def _input(self, value, x, y, w, h):
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        f.setStringValue_(value)
        return f

    @objc.python_method
    def _button(self, title, x, y, w, h, action):
        b = NSButton.buttonWithTitle_target_action_(title, self, objc.selector(action))
        b.setFrame_(NSMakeRect(x, y, w, h))
        return b

    @objc.python_method
    def add_log(self, text):
        if self.log_view is None:
            return
        # 用 attributed string 显式指定文字颜色（动态跟随系统外观），避免丢失
        line = text + "\n"
        attr = NSMutableAttributedString.alloc().initWithString_(line)
        attr.addAttribute_value_range_(
            NSForegroundColorAttributeName, NSColor.textColor(), (0, len(line)))
        attr.addAttribute_value_range_(
            NSFontAttributeName,
            NSFont.monospacedSystemFontOfSize_weight_(11, 0), (0, len(line)))
        self.log_view.textStorage().appendAttributedString_(attr)
        self.log_view.scrollToEndOfDocument_(None)

    @objc.python_method
    def show_alert(self, title, msg):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(msg)
        alert.runModal()

    # ---------- 动作 ----------
    @objc.python_method
    def _read_params(self):
        x = int(self.field_x.stringValue())
        y = int(self.field_y.stringValue())
        interval = float(self.field_interval.stringValue())
        if interval < 0:
            raise ValueError(T("err_interval"))
        idx = self.popup_mode.indexOfSelectedItem()
        mode = "duration" if idx == 0 else ("count" if idx == 1 else "queue")
        value = int(self.field_value.stringValue())
        if value <= 0:
            raise ValueError(T("err_value"))
        if mode == "duration" and value > MAX_RUNTIME_SEC:
            raise ValueError(T("err_duration", n=MAX_RUNTIME_SEC // 60))
        if mode == "count" and value > MAX_COUNT:
            raise ValueError(T("err_count", n=MAX_COUNT))
        if mode == "queue" and value > MAX_RUNTIME_SEC:
            raise ValueError(T("err_duration", n=MAX_RUNTIME_SEC // 60))
        return x, y, interval, mode, value

    def start_(self, sender):
        self._start(None, None)

    def test_(self, sender):
        self._start("duration", 10)

    # ---------- 坐标拾取 ----------
    @objc.python_method
    def _beep(self):
        """倒计时进行中：每秒一声短促'滴'（Tink 音效，不依赖系统 alert 声音设置）。静音时直接跳过。"""
        if getattr(self, "muted", False):
            return
        try:
            if self._beep_snd is None:
                self._beep_snd = NSSound.soundNamed_("Tink") or NSSound.soundNamed_("Pop")
            snd = self._beep_snd
            if snd is not None:
                try:
                    snd.stop()
                except Exception:
                    pass
                snd.play()
            else:
                NSSound.beep()
        except Exception:
            pass

    @objc.python_method
    def _chime(self):
        """倒计时结束：一声'叮'（系统 Glass 音），提示时间到。静音时直接跳过。"""
        if getattr(self, "muted", False):
            return
        try:
            s = NSSound.soundNamed_("Glass")
            if s is not None:
                s.play()
            else:
                NSSound.beep()
        except Exception:
            self._beep()

    def pick_(self, sender):
        """点击后开始 5 秒倒计时，到时读取鼠标位置填入坐标框。"""
        if not HAVE_QUARTZ:
            self.show_alert(T("alert_error"), T("msg_noquartz"))
            return
        self.countdown_remaining = 5
        self.lbl_pick.setStringValue_(T("log_countdown", n=self.countdown_remaining))
        self.btn_pick.setEnabled_(False)
        self._beep()   # 开始即响一声，提示倒计时已启动
        self.countdown_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, objc.selector(self.countdown_), None, True)

    def countdown_(self, timer):
        """每秒回调一次；到 0 时读取鼠标位置。每秒'滴'，结束时'叮'。"""
        self.countdown_remaining -= 1
        if self.countdown_remaining > 0:
            self.lbl_pick.setStringValue_(T("log_countdown", n=self.countdown_remaining))
            self._beep()   # 进行中提示
            return
        if timer is not None:
            timer.invalidate()
        self._chime()      # 时间到提示
        x, y = self.get_mouse_pos()
        self.field_x.setStringValue_(str(x))
        self.field_y.setStringValue_(str(y))
        self.lbl_pick.setStringValue_(T("log_picked", x=x, y=y))
        self.btn_pick.setEnabled_(True)
        self.add_log(T("log_pickcoord", x=x, y=y))

    def testPos_(self, sender):
        """在坐标框的当前值位置执行一次点击，用于校验坐标准不准。"""
        try:
            x = int(self.field_x.stringValue())
            y = int(self.field_y.stringValue())
        except Exception:
            self.show_alert(T("alert_bad"), T("msg_coordnum"))
            return
        mouse_click(x, y)
        self.add_log(T("log_testclick", x=x, y=y))

    @objc.python_method
    def get_mouse_pos(self):
        if not HAVE_QUARTZ:
            return 0, 0
        ev = CGEventCreate(None)
        loc = CGEventGetLocation(ev)
        return int(loc.x), int(loc.y)

    @objc.python_method
    def _start(self, mode_override, duration_override):
        if self.worker is not None and self.worker.running:
            self.show_alert(T("alert_notice"), T("msg_already"))
            return
        if not HAVE_QUARTZ:
            self.show_alert(T("alert_error"), T("msg_noquartz"))
            return
        # 运行前再查一次权限，避免"点了没反应"
        if HAVE_QUARTZ:
            try:
                if not CGPreflightPostEventAccess():
                    self.show_alert(T("alert_perm"), T("msg_perm"))
                    return
            except Exception:
                pass
        try:
            x, y, interval, mode, value = self._read_params()
        except Exception as e:
            self.show_alert(T("alert_bad"), str(e))
            return
        if mode_override == "duration":
            # 测试按钮：沿用当前模式（队列模式仍按队列跑），只把目标改成 10 秒
            if mode == "count":
                mode = "duration"
            value = duration_override

        # 构造点击计划：队列模式用列表，单点模式等价于一个无限重复的点
        if mode == "queue":
            if not getattr(self, "_queue", None):
                self.show_alert(T("alert_bad"), T("err_queue_empty"))
                return
            plan = [dict(p) for p in self._queue]
            run_mode = "duration"        # 队列模式按总时长跑
        else:
            plan = [{"x": x, "y": y, "count": 0, "interval_ms": interval}]
            run_mode = mode

        self.worker = ClickWorker(plan, run_mode, value)
        self.worker.start()
        self._set_running(True)
        self.add_log("=" * 40)

    def stop_(self, sender):
        self._int_state = "idle"   # 手动停止时退出暂停/弹窗态
        if self.worker is not None:
            self.worker.request_stop()

    def saveCoord_(self, sender):
        """保存当前坐标 + 自定义命名 → saved_coords.json（action，勿加 @objc.python_method）。"""
        name = self.field_saved_name.stringValue().strip()
        if not name:
            self.show_alert(T("alert_saved"), T("err_noname"))
            return
        try:
            x = int(self.field_x.stringValue())
            y = int(self.field_y.stringValue())
        except Exception:
            self.show_alert(T("alert_saved"), T("err_coordnum"))
            return
        self._saved_coords = [c for c in self._saved_coords if c.get("name") != name]  # 同名覆盖
        self._saved_coords.append({"name": name, "x": x, "y": y})
        self._save_coords(self._saved_coords)
        self._reload_saved_list()
        self.field_saved_name.setStringValue_("")
        self.add_log(T("log_saved", name=name, x=x, y=y))

    def coordsChanged_(self, sender):
        """从下拉选中已保存坐标 → 载入坐标框（action，勿加 @objc.python_method）。"""
        idx = sender.indexOfSelectedItem()
        if idx <= 0:
            return
        c = self._saved_coords[idx - 1]
        self.field_x.setStringValue_(str(c.get("x", "")))
        self.field_y.setStringValue_(str(c.get("y", "")))
        self.add_log(T("log_load", name=c.get("name", ""), x=c.get("x", ""), y=c.get("y", "")))

    def about_(self, sender):
        """关于面板：版本 + GitHub 链接 + 版权三行（action，勿加 @objc.python_method）。"""
        msg = T("msg_about", n=VERSION, url=GITHUB_URL,
                c1=COPYRIGHT_1, c2=COPYRIGHT_2, c3=COPYRIGHT_3)
        alert = NSAlert.alloc().init()
        alert.setMessageText_(T("alert_about"))
        alert.setInformativeText_(msg)
        alert.addButtonWithTitle_(T("btn_openrepo"))
        alert.addButtonWithTitle_(T("btn_close"))
        ret = alert.runModal()
        if ret == NSAlertFirstButtonReturn:
            try:
                NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(GITHUB_URL))
            except Exception:
                pass

    @objc.python_method
    def _reload_saved_list(self):
        self._saved_coords = self._load_coords()
        self.popup_saved.removeAllItems()
        self.popup_saved.addItemWithTitle_(T("ph_saved"))
        for c in self._saved_coords:
            self.popup_saved.addItemWithTitle_(
                "{} ({}, {})".format(c.get("name", "?"), c.get("x", 0), c.get("y", 0)))
        self.popup_saved.selectItemAtIndex_(0)

    @objc.python_method
    def _save_coords(self, coords):
        try:
            with open(SAVED_COORDS_PATH, "w", encoding="utf-8") as f:
                json.dump(coords, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @objc.python_method
    def _load_coords(self):
        try:
            with open(SAVED_COORDS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    @objc.python_method
    def _set_running(self, running):
        self.btn_start.setEnabled_(not running)
        self.btn_test.setEnabled_(not running)
        self.btn_stop.setEnabled_(running)
        self.lbl_status.setStringValue_(T("status_run") if running else T("status_idle"))
        if running:
            self.lbl_elapsed.setStringValue_(T("lbl_elapsed", t="00:00:00"))
            self.lbl_left.setStringValue_(T("lbl_left", t="00:00:00"))
            self.lbl_total.setStringValue_(T("lbl_total", t="00:00:00"))

    # ---------- 多点队列 ----------
    def queueAdd_(self, sender):
        """把坐标框的 X/Y + 次数 + 间隔加入队列（action，勿加 @objc.python_method）。"""
        try:
            x = int(self.field_x.stringValue())
            y = int(self.field_y.stringValue())
            cnt = int(self.field_qcount.stringValue() or "0")
            iv = float(self.field_qint.stringValue() or "0")
        except Exception:
            self.show_alert(T("alert_bad"), T("msg_coordnum"))
            return
        if cnt < 0 or iv < 0:
            self.show_alert(T("alert_bad"), T("err_interval"))
            return
        if len(getattr(self, "_queue", [])) >= 200:
            self.show_alert(T("alert_notice"), T("err_count", n=200))
            return
        self._queue.append({"x": x, "y": y, "count": cnt, "interval_ms": iv})
        self._save_queue(self._queue)
        self._refresh_queue_view()
        self.add_log(T("log_queue_add", i=len(self._queue), x=x, y=y,
                       n=("∞" if not cnt else cnt), ms=("%g" % iv)))

    def queueDel_(self, sender):
        """删除队列末项（action）。"""
        if getattr(self, "_queue", None):
            self._queue.pop()
            self._save_queue(self._queue)
            self._refresh_queue_view()
            self.add_log(T("log_queue_del"))

    def queueClear_(self, sender):
        """清空队列（action）。"""
        self._queue = []
        self._save_queue([])
        self._refresh_queue_view()
        self.add_log(T("log_queue_clear"))

    def toggleMute_(self, sender):
        """静音复选框（action）：勾选后倒计时提示音不再播放。"""
        self.muted = bool(sender.state())
        self.add_log(T("log_mute") if self.muted else T("log_unmute"))

    @objc.python_method
    def _refresh_queue_view(self):
        if getattr(self, "queue_view", None) is None:
            return
        if not getattr(self, "_queue", None):
            self.queue_view.setString_(T("que_empty"))
            return
        lines = []
        for i, q in enumerate(self._queue):
            c = int(q.get("count", 0) or 0)
            lines.append("%d. (%d, %d)  ×%s  %gms" % (
                i + 1, q["x"], q["y"], ("∞" if not c else c),
                float(q.get("interval_ms", 0) or 0)))
        self.queue_view.setString_("\n".join(lines))

    @objc.python_method
    def _save_queue(self, q):
        try:
            with open(MULTI_POINTS_PATH, "w", encoding="utf-8") as f:
                json.dump(q, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @objc.python_method
    def _load_queue(self):
        try:
            with open(MULTI_POINTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            out = []
            for q in (data if isinstance(data, list) else []):
                out.append({"x": int(q.get("x", 0)), "y": int(q.get("y", 0)),
                            "count": int(q.get("count", 0) or 0),
                            "interval_ms": float(q.get("interval_ms", 0) or 0)})
            return out
        except Exception:
            return []

    @objc.python_method
    def _update_clock(self, w):
        """倒计时三件套：已过 / 剩余 / 总计（HH:MM:SS；暂停时 elapsed 冻结，数值不动）。"""
        e = w.elapsed
        self.lbl_elapsed.setStringValue_(T("lbl_elapsed", t=fmt_hms(e)))
        if w.mode == "duration":
            total = float(w.value)
            self.lbl_left.setStringValue_(T("lbl_left", t=fmt_hms(max(0.0, total - e))))
            self.lbl_total.setStringValue_(T("lbl_total", t=fmt_hms(total)))
        else:
            r = w.rate
            left = ((w.value - w.count) / r) if r > 0 else 0.0
            self.lbl_left.setStringValue_(T("lbl_left", t="≈" + fmt_hms(left)))
            self.lbl_total.setStringValue_(T("lbl_total", t="—"))

    def refresh_(self, timer):
        w = self.worker
        if w is None:
            return
        # 自动恢复：暂停态且鼠标停止 0.5 秒无晃动 → 恢复原流程
        if self._int_state == "paused_wait" and time.time() - self._last_move_ts >= SWING_IDLE_SEC:
            self._auto_resume()
        for line in w.drain_logs():
            self.add_log(line)
        if w.running:
            self.lbl_count.setStringValue_(T("lbl_count", n=w.count))
            self.lbl_rate.setStringValue_(T("lbl_rate", r=w.rate))
            self._update_clock(w)
        elif self.btn_stop.isEnabled():
            self._set_running(False)
            self.lbl_count.setStringValue_(T("lbl_count", n=w.count))
            self.lbl_rate.setStringValue_(T("lbl_rate", r=w.rate))
            self._update_clock(w)
            self.worker = None

def main():
    if not HAVE_QUARTZ:
        print("警告：缺少 Quartz(pyobjc)，点击将无效。请安装 pyobjc-framework-Quartz。")
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()


if __name__ == "__main__":
    main()
