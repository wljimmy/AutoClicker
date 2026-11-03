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
import threading
import collections

import objc
from Foundation import (
    NSObject, NSMakeRect, NSTimer, NSURL,
)
from AppKit import (
    NSApplication, NSWindow, NSButton, NSTextField, NSTextView, NSScrollView,
    NSPopUpButton, NSFont, NSColor, NSBackingStoreBuffered,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
    NSApplicationActivationPolicyRegular,
    NSAlert, NSWorkspace, NSBox,
    NSEvent, NSCommandKeyMask, NSShiftKeyMask,
    NSEventMaskKeyDown, NSEventMaskMouseMoved,
    NSMutableAttributedString, NSForegroundColorAttributeName,
    NSFontAttributeName,
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


# ======================= 点击控制核心 =======================
class ClickWorker:
    """后台点击线程：循环点击、统计、自动/手动停止。线程安全。"""

    def __init__(self, x, y, interval_ms, mode, value):
        self.x = x
        self.y = y
        self.interval = max(0.0, float(interval_ms)) / 1000.0
        self.mode = mode            # "duration" 或 "count"
        self.value = value
        self.stop_event = threading.Event()
        self.count = 0
        self.start_time = 0.0
        self.running = False
        self.finished_reason = ""
        self._logs = collections.deque()
        self._lock = threading.Lock()

    def request_stop(self):
        self.stop_event.set()

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
        if not self.running or self.start_time <= 0:
            return 0.0
        return self.count / max(0.001, time.time() - self.start_time)

    def start(self):
        self.stop_event.clear()
        self.count = 0
        self.running = True
        t = threading.Thread(target=self._run, daemon=True)
        self._thread = t
        t.start()

    def _run(self):
        self.start_time = time.time()
        self.add_log(f"开始点击：坐标({self.x},{self.y}) 间隔{self.interval*1000:.0f}ms "
                     f"模式={self.mode} 目标={self.value}")
        reason = ""
        while not self.stop_event.is_set():
            # 逃生机制①：硬性时长上限（防呆，杜绝超长锁死）
            if time.time() - self.start_time >= MAX_RUNTIME_SEC:
                reason = "达到最长运行时间(60分钟)，已强制停止"
                break
            # 逃生机制②：stop.flag 文件兜底（另一个终端 touch 即停）
            if os.path.exists(STOP_FLAG):
                reason = "检测到 stop.flag 文件，已停止"
                break

            mouse_click(self.x, self.y)
            self.count += 1

            if self.mode == "count" and self.count >= self.value:
                reason = "达到目标自动停止"
                break
            if self.mode == "duration" and (time.time() - self.start_time) >= self.value:
                reason = "达到目标自动停止"
                break

            if self.interval > 0:
                deadline = time.time() + self.interval
                while time.time() < deadline:
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.0005)
                if self.stop_event.is_set():
                    break

        elapsed = time.time() - self.start_time
        if not reason:
            reason = "手动/热键/鼠标移动停止"
        self.finished_reason = reason
        self.running = False
        self.add_log(f"结束（{reason}）：共投递 {self.count} 次，"
                     f"耗时 {elapsed:.1f}s，投递速率 {self.count/elapsed:.1f} 次/秒")


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
            w.add_log("▶ 收到停止热键（⌘Cmd+Shift+X / F9），正在停止")
            w.request_stop()


# ======================= AppKit 原生 UI =======================
class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, notification):
        self.worker = None
        self.log_view = None
        self.build_ui()
        # 全局热键
        HotkeyListener(lambda: self.worker).start()
        # 定时刷新 UI
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.3, self, objc.selector(self.refresh_), None, True)
        self.add_log("就绪。")
        self.check_accessibility()
        # 逃生通道③：真实鼠标移动即停（键盘被游戏抢走时，动一下鼠标就能夺回控制权）
        try:
            self._mouse_mon = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSEventMaskMouseMoved, self._on_mouse_moved)
        except Exception:
            self._mouse_mon = None
        self.add_log("停止方式：动一下鼠标 / ⌘Cmd+Shift+X / F9 / 停止按钮 / 最长60分钟自动停")

    @objc.python_method
    def _on_mouse_moved(self, event):
        w = self.worker
        if w is not None and w.running:
            w.add_log("🖱 检测到真实鼠标移动，已自动停止（想继续可重新点『开始』）")
            w.request_stop()

    @objc.python_method
    def check_accessibility(self):
        """检测辅助功能权限：无权限时 CGEventPost 会静默丢弃事件（表现为点了没反应）。"""
        if not HAVE_QUARTZ:
            self.add_log("⚠️ 当前环境缺少 Quartz，点击无法生效。")
            return
        try:
            ok = CGPreflightPostEventAccess()
        except Exception:
            ok = False
        if ok:
            self.add_log("✅ 辅助功能权限正常，点击事件可送达。")
            return
        self.add_log("⚠️ 缺少辅助功能权限！点击事件将被系统静默丢弃（点了没反应）。")
        self.show_alert(
            "需要辅助功能授权",
            "AutoClicker 需要『辅助功能』权限才能发送鼠标点击。\n\n"
            "即将打开系统设置 → 隐私与安全性 → 辅助功能。\n"
            "请在列表中勾选 AutoClicker，然后关闭设置、重新启动本程序。",
        )
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
            NSMakeRect(0, 0, 600, 680),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
            NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered, False)
        self.window.setTitle_("AutoClicker - 自动点击器")
        self.window.center()
        self.window.setReleasedWhenClosed_(False)
        content = self.window.contentView()

        # ---------- 大标题 ----------
        title = NSTextField.labelWithString_("AutoClicker")
        title.setFont_(NSFont.boldSystemFontOfSize_(22))
        title.setTextColor_(NSColor.controlAccentColor())
        title.setFrame_(NSMakeRect(16, 634, 300, 30))
        content.addSubview_(title)
        sub = NSTextField.labelWithString_("macOS 自动点击器 · 坐标拾取 · 全局热键停止")
        sub.setFont_(NSFont.systemFontOfSize_(12))
        sub.setTextColor_(NSColor.secondaryLabelColor())
        sub.setFrame_(NSMakeRect(16, 610, 420, 18))
        content.addSubview_(sub)
        content.addSubview_(self._separator(16, 596, 568))

        # ---------- ① 坐标拾取 ----------
        content.addSubview_(self._group_title("①  坐标拾取", 16, 570))
        content.addSubview_(self._label("坐标 X:", 16, 540, 54, 24))
        self.field_x = self._input("2852", 72, 540, 80, 26)
        content.addSubview_(self.field_x)
        content.addSubview_(self._label("Y:", 168, 540, 22, 24))
        self.field_y = self._input("545", 192, 540, 80, 26)
        content.addSubview_(self.field_y)
        content.addSubview_(self._label("间隔(ms):", 292, 540, 62, 24))
        self.field_interval = self._input("1", 356, 540, 80, 26)
        content.addSubview_(self.field_interval)
        content.addSubview_(self._label("越小越快 · 游戏约50次/秒有效", 448, 542, 150, 22, small=True))

        self.btn_pick = self._button("📌 拾取坐标 (5s)", 16, 498, 176, 34, self.pick_)
        self.btn_pick.setToolTip_("点击后 5 秒倒计时，结束时读取鼠标位置并填入坐标框")
        content.addSubview_(self.btn_pick)
        self.btn_test_pos = self._button("🎯 测试坐标", 200, 498, 140, 34, self.testPos_)
        self.btn_test_pos.setToolTip_("在坐标框当前位置执行一次点击，校验坐标是否准确")
        content.addSubview_(self.btn_test_pos)
        self.lbl_pick = self._label("倒计时：就绪（点后把鼠标移到目标点）", 350, 498, 248, 26, small=True)
        content.addSubview_(self.lbl_pick)
        content.addSubview_(self._separator(16, 484, 568))

        # ---------- ② 运行设置 ----------
        content.addSubview_(self._group_title("②  运行设置", 16, 458))
        content.addSubview_(self._label("运行模式:", 16, 424, 72, 24))
        self.popup_mode = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(92, 424, 126, 26), False)
        self.popup_mode.addItemsWithTitles_(["按时长(秒)", "按次数"])
        content.addSubview_(self.popup_mode)
        content.addSubview_(self._label("数值:", 236, 424, 34, 24))
        self.field_value = self._input("60", 272, 424, 84, 26)
        content.addSubview_(self.field_value)
        content.addSubview_(self._label("停止：⌘Cmd+Shift+X / 停止按钮 / 自动到点", 372, 424, 220, 22, small=True))
        content.addSubview_(self._separator(16, 410, 568))

        # ---------- ③ 控制 ----------
        content.addSubview_(self._group_title("③  控制", 16, 384))
        self.btn_start = self._button("▶ 开始", 16, 344, 132, 36, self.start_)
        content.addSubview_(self.btn_start)
        self.btn_test = self._button("测试 10 秒", 158, 344, 132, 36, self.test_)
        content.addSubview_(self.btn_test)
        self.btn_stop = self._button("■ 停止", 300, 344, 132, 36, self.stop_)
        self.btn_stop.setEnabled_(False)
        content.addSubview_(self.btn_stop)

        self.lbl_status = self._label("状态：空闲", 16, 302, 170, 22)
        content.addSubview_(self.lbl_status)
        self.lbl_count = self._label("已投递：0 次", 200, 302, 150, 22)
        content.addSubview_(self.lbl_count)
        self.lbl_rate = self._label("速率：0.0 次/秒", 360, 302, 220, 22)
        content.addSubview_(self.lbl_rate)

        # ---------- 日志 ----------
        content.addSubview_(self._label("运行日志", 16, 278, 200, 20, bold=True))
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(10, 10, 580, 260))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        self.log_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 560, 260))
        self.log_view.setEditable_(False)
        self.log_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11, 0))
        # 系统动态颜色：自动跟随深色/浅色模式（夜间→黑底白字，浅色→白底黑字）
        self.log_view.setBackgroundColor_(NSColor.textBackgroundColor())
        self.log_view.setTextColor_(NSColor.textColor())
        scroll.setDocumentView_(self.log_view)
        content.addSubview_(scroll)

        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

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
            raise ValueError("间隔不能为负数")
        mode = "duration" if self.popup_mode.indexOfSelectedItem() == 0 else "count"
        value = int(self.field_value.stringValue())
        if value <= 0:
            raise ValueError("时长/次数必须大于0")
        if mode == "duration" and value > MAX_RUNTIME_SEC:
            raise ValueError(f"时长不能超过 {MAX_RUNTIME_SEC//60} 分钟（防呆上限，防止锁死电脑）")
        if mode == "count" and value > MAX_COUNT:
            raise ValueError(f"次数不能超过 {MAX_COUNT:,}（防呆上限）")
        return x, y, interval, mode, value

    def start_(self, sender):
        self._start(None, None)

    def test_(self, sender):
        self._start("duration", 10)

    # ---------- 坐标拾取 ----------
    def pick_(self, sender):
        """点击后开始 5 秒倒计时，到时读取鼠标位置填入坐标框。"""
        if not HAVE_QUARTZ:
            self.show_alert("错误", "当前环境缺少 Quartz，无法读取鼠标位置。")
            return
        self.countdown_remaining = 5
        self.lbl_pick.setStringValue_(f"倒计时：{self.countdown_remaining}s（请把鼠标移到目标点）")
        self.btn_pick.setEnabled_(False)
        self.countdown_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, objc.selector(self.countdown_), None, True)

    def countdown_(self, timer):
        """每秒回调一次；到 0 时读取鼠标位置。"""
        self.countdown_remaining -= 1
        if self.countdown_remaining > 0:
            self.lbl_pick.setStringValue_(f"倒计时：{self.countdown_remaining}s（请把鼠标移到目标点）")
            return
        if timer is not None:
            timer.invalidate()
        x, y = self.get_mouse_pos()
        self.field_x.setStringValue_(str(x))
        self.field_y.setStringValue_(str(y))
        self.lbl_pick.setStringValue_(f"✅ 拾取完成：({x}, {y})")
        self.btn_pick.setEnabled_(True)
        self.add_log(f"📌 拾取坐标：({x}, {y})（已填入坐标框，可点『🎯测试坐标』校验）")

    def testPos_(self, sender):
        """在坐标框的当前值位置执行一次点击，用于校验坐标准不准。"""
        try:
            x = int(self.field_x.stringValue())
            y = int(self.field_y.stringValue())
        except Exception:
            self.show_alert("参数错误", "坐标必须是数字。")
            return
        mouse_click(x, y)
        self.add_log(f"🎯 测试坐标点击：({x}, {y})（若弹窗/按钮有反应即坐标正确）")

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
            self.show_alert("提示", "已经在运行中，请先停止。")
            return
        if not HAVE_QUARTZ:
            self.show_alert("错误", "缺少 Quartz(pyobjc)。请先安装 pyobjc-framework-Quartz。")
            return
        # 运行前再查一次权限，避免"点了没反应"
        if HAVE_QUARTZ:
            try:
                if not CGPreflightPostEventAccess():
                    self.show_alert(
                        "没有辅助功能权限",
                        "点击事件无法送达（会被系统静默丢弃）。\n"
                        "请到 系统设置→隐私与安全性→辅助功能 勾选 AutoClicker，然后重新启动程序。",
                    )
                    return
            except Exception:
                pass
        try:
            x, y, interval, mode, value = self._read_params()
        except Exception as e:
            self.show_alert("参数错误", str(e))
            return
        if mode_override:
            mode = mode_override
            value = duration_override

        self.worker = ClickWorker(x, y, interval, mode, value)
        self.worker.start()
        self._set_running(True)
        self.add_log("=" * 40)

    def stop_(self, sender):
        if self.worker is not None:
            self.worker.request_stop()

    @objc.python_method
    def _set_running(self, running):
        self.btn_start.setEnabled_(not running)
        self.btn_test.setEnabled_(not running)
        self.btn_stop.setEnabled_(running)
        self.lbl_status.setStringValue_("状态：运行中" if running else "状态：空闲")

    def refresh_(self, timer):
        w = self.worker
        if w is None:
            return
        for line in w.drain_logs():
            self.add_log(line)
        if w.running:
            self.lbl_count.setStringValue_(f"已投递：{w.count} 次")
            self.lbl_rate.setStringValue_(f"速率：{w.rate:.1f} 次/秒")
        elif self.btn_stop.isEnabled():
            self._set_running(False)
            self.lbl_count.setStringValue_(f"已投递：{w.count} 次")
            self.lbl_rate.setStringValue_(f"速率：{w.rate:.1f} 次/秒")
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
