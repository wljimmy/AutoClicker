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
import sys
import time
import json
import threading
import collections

import objc
from Foundation import (
    NSObject, NSMakeRect, NSTimer, NSURL, NSIndexSet, NSMutableIndexSet,
)
from AppKit import (
    NSApplication, NSWindow, NSButton, NSTextField, NSTextView, NSScrollView,
    NSTableView, NSTableColumn, NSTableCellView,
    NSPopUpButton, NSFont, NSColor, NSBackingStoreBuffered, NSSound,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
    NSApplicationActivationPolicyRegular,
    NSAlert, NSWorkspace, NSBox, NSAlertFirstButtonReturn,
    NSEvent, NSCommandKeyMask, NSShiftKeyMask,
    NSEventMaskKeyDown, NSEventMaskMouseMoved, NSLeftMouseDownMask,
    NSMutableAttributedString, NSAttributedString, NSForegroundColorAttributeName,
    NSFontAttributeName,
    NSLineBreakByWordWrapping,
    NSTextAlignmentRight,
    NSSavePanel, NSOpenPanel, NSModalResponseOK,
    NSStackView, NSGridView, NSLayoutConstraint, NSNull, NSView,
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
def _data_dir():
    """数据目录：源码运行=脚本所在目录；打包运行=~/Library/Application Support/AutoClicker（apps 包内不可持久）。"""
    if getattr(sys, "frozen", False):
        d = os.path.expanduser("~/Library/Application Support/AutoClicker")
    else:
        d = os.path.dirname(os.path.abspath(__file__))
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


DATA_DIR = _data_dir()
STOP_FLAG = os.path.join(DATA_DIR, "stop.flag")

VERSION = "0.8.0"   # 版本号：UI 副标题 + 关于面板展示
GITHUB_URL = "https://github.com/wljimmy/AutoClicker"
# 版权三行（verbatim，用户锁定）
COPYRIGHT_1 = "Bingo Studio"
COPYRIGHT_2 = "wljimmy@hotmail.com"
COPYRIGHT_3 = "本项目是由wljimmy使用doubao AI完成"
SAVED_COORDS_PATH = os.path.join(DATA_DIR, "saved_coords.json")   # 旧版单点文件（仅用于迁移）
MULTI_POINTS_PATH = os.path.join(DATA_DIR, "multi_points.json")     # 旧版队列文件（仅用于迁移）
DB_PATH = os.path.join(DATA_DIR, "database.json")                  # 工作库：队列 / 单点 / 当前队列
DEF_PATH = os.path.join(DATA_DIR, "default_profile.json")          # 默认库：只有「设为默认」才写


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _empty_db():
    return {"schema": 1, "current": [], "queues": [], "singles": [], "updated": _now_str()}


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_db(path=None):
    """读工作库。文件不存在 → 从旧版 multi_points/saved_coords 迁移一份；再不然给空库。"""
    path = path or DB_PATH
    data = _read_json(path, None)
    if isinstance(data, dict) and "schema" in data:
        db = _empty_db()
        db.update({k: data.get(k, db[k]) for k in ("current", "queues", "singles")})
        db["updated"] = data.get("updated", db["updated"])
        return db
    # 首次运行 / 迁移
    db = _empty_db()
    legacy_q = _read_json(MULTI_POINTS_PATH, None)
    if isinstance(legacy_q, list):
        db["current"] = [q for q in legacy_q if isinstance(q, dict)]
    legacy_c = _read_json(SAVED_COORDS_PATH, None)
    if isinstance(legacy_c, list):
        db["singles"] = [c for c in legacy_c if isinstance(c, dict) and "name" in c]
    save_db(db, path)
    return db


def save_db(db, path=None):
    """原子写：先写 .tmp 再 replace，避免写一半断电留下半个文件。"""
    path = path or DB_PATH
    db["updated"] = _now_str()
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def ensure_default_db(path=None):
    """默认库不存在时写入一份内置默认；已存在则一个字都不动。"""
    path = path or DEF_PATH
    if not os.path.exists(path):
        save_db(_empty_db(), path)


def read_default_db(path=None):
    path = path or DEF_PATH
    data = _read_json(path, None)
    if isinstance(data, dict) and "schema" in data:
        return data
    return _empty_db()

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
    "lbl_int":     {"zh": "间隔", "en": "Gap"},
    "lbl_name":    {"zh": "名称", "en": "Name"},
    "lbl_saved":   {"zh": "已存", "en": "Saved"},
    "tip_interval": {"zh": "越小越快 · 游戏约 50 次/秒有效",
                     "en": "Smaller = Faster · ~50/s effective"},
    "btn_pick":    {"zh": "📌 拾取 (5s)", "en": "📌 Pick (5s)"},
    "btn_test":    {"zh": "🎯 测试", "en": "🎯 Test"},
    "btn_rec":     {"zh": "🎯 连采", "en": "🎯 Record"},
    "btn_rec_stop": {"zh": "⏹ 停止采集", "en": "⏹ Stop Rec"},
    "tip_pick_tt": {"zh": "点击后 5 秒倒计时，结束时读取鼠标位置并填入坐标框",
                    "en": "5s countdown then read mouse pos"},
    "tip_test_tt": {"zh": "在坐标框当前位置执行一次点击，校验坐标是否准确",
                    "en": "click once at coords"},
    "tip_rec_tt":  {"zh": "在目标窗口里连续点击，自动按点击顺序与时间差生成队列（只监听不拦截）",
                    "en": "Click in the target app; builds the queue from order + gaps (listen-only)"},
    "lbl_pick_ready": {"zh": "就绪：拾取=5秒取点 · 连采=连续记录点击",
                       "en": "Ready: Pick=5s sample · Record=capture clicks"},
    # ---- ④ 多点队列 / 静音 / 倒计时 ----
    "grp4":         {"zh": "④ 多点队列", "en": "④ Multi-Point Queue"},
    "btn_qadd":     {"zh": "➕ 加入", "en": "➕ Add"},
    "btn_qsave":    {"zh": "💾 保存行", "en": "💾 Save Row"},
    "btn_qdel":     {"zh": "🗑 删除", "en": "🗑 Delete"},
    "btn_qclear":   {"zh": "清空", "en": "Clear"},
    "btn_qdup":     {"zh": "⧉ 复制", "en": "⧉ Duplicate"},
    # 表格列名
    "col_no":       {"zh": "#", "en": "#"},
    "col_tag":      {"zh": "标签", "en": "Tag"},
    "col_x":        {"zh": "X", "en": "X"},
    "col_y":        {"zh": "Y", "en": "Y"},
    "col_count":    {"zh": "次数", "en": "Times"},
    "col_iv":       {"zh": "间隔", "en": "Gap"},
    "col_delay":    {"zh": "等待", "en": "Wait"},
    # 批量设置区
    "lbl_bcnt":     {"zh": "次数", "en": "Times"},
    "lbl_bint":     {"zh": "间隔", "en": "Gap"},
    "lbl_bdelay":   {"zh": "等待", "en": "Wait"},
    "btn_bapply":   {"zh": "⇉ 应用到所选", "en": "⇉ Apply to rows"},
    "btn_selall":   {"zh": "全选", "en": "All"},
    "lbl_sel_n":    {"zh": "已选 {n} 行", "en": "{n} selected"},
    "lbl_sel_0":    {"zh": "未选中行", "en": "No row selected"},
    "err_sel_none": {"zh": "先在表格里选中至少一行（Shift/⌘ 可多选，或点『全选』）",
                     "en": "Select at least one row (Shift/⌘ multi-select)"},
    "err_cell_bad": {"zh": "「{f}」的值不合法（{v}），已还原。次数 0~1000000，间隔 0~600000，坐标 ±100000",
                     "en": "Invalid {f}: {v} (reverted). Times 0-1000000, gap 0-600000, coord ±100000"},
    "log_q_batch_del": {"zh": "🗑 已删除 {n} 行", "en": "🗑 Deleted {n} rows"},
    "log_q_batch":  {"zh": "⇉ 已批量设置 {n} 行：×{c}，间隔 {ms}ms",
                     "en": "⇉ Applied to {n} rows: ×{c} gap {ms}ms"},
    "log_q_dup":    {"zh": "⧉ 已复制第 {i} 行（插在其后）", "en": "⧉ Duplicated row {i}"},
    "log_row_edit": {"zh": "✎ 第 {i} 行 {f}：{a} → {b}", "en": "✎ Row {i} {f}: {a} -> {b}"},
    "log_q_move":   {"zh": "↕ 第 {i} 行已移到第 {j} 位", "en": "↕ Row {i} moved to {j}"},
    # ---- 队列标签 + 数据文档 ----
    "lbl_qtag":     {"zh": "标签", "en": "Tag"},
    "ph_qtag":      {"zh": "这个点是干嘛的（可选）", "en": "What this point does (opt.)"},
    "lbl_qcount_s": {"zh": "次数", "en": "Times"},
    "lbl_qint_s":   {"zh": "间隔", "en": "Gap"},
    "tip_inf":      {"zh": "∞=一直点；每轮按整表循环\n标签可留空", "en": "∞=forever; loops whole list\nTag can be empty"},
    "btn_data":     {"zh": "📚 数据", "en": "📚 Data"},
    "panel_title":  {"zh": "数据文档", "en": "Data Library"},
    "lbl_db_path":  {"zh": "库：{p}", "en": "File: {p}"},
    "lbl_store_q":  {"zh": "队列名", "en": "Queue name"},
    "ph_store_q":   {"zh": "给这条队列起个名", "en": "Name this queue"},
    "btn_store_q":  {"zh": "💾 存为队列", "en": "💾 Save Queue"},
    "lbl_saved_q":  {"zh": "已存队列", "en": "Saved queues"},
    "btn_load_q":   {"zh": "载入", "en": "Load"},
    "btn_del_q":    {"zh": "删除", "en": "Delete"},
    "lbl_singles":  {"zh": "单点库", "en": "Saved points"},
    "btn_export":   {"zh": "📤 导出", "en": "📤 Export"},
    "btn_import":   {"zh": "📥 导入", "en": "📥 Import"},
    "btn_set_def":  {"zh": "★ 设为默认", "en": "★ Set default"},
    "btn_reset_def": {"zh": "↺ 恢复默认", "en": "↺ Restore default"},
    "ph_none":      {"zh": "（空）", "en": "(empty)"},
    "msg_q_over":   {"zh": "已存在同名队列「{n}」，覆盖它吗？", "en": "Queue \"{n}\" exists. Overwrite?"},
    "msg_q_load":   {"zh": "载入「{n}」会替换当前队列（{k} 个点），继续？", "en": "Loading \"{n}\" replaces current queue ({k} points). Continue?"},
    "msg_q_del":    {"zh": "删除队列「{n}」？不可撤销。", "en": "Delete queue \"{n}\"? Cannot undo."},
    "msg_set_def":  {"zh": "把当前数据（队列 {q} 条 / 单点 {s} 个 / 当前队列 {c} 点）写为默认文档？\n默认文档平时不会被改动。",
                     "en": "Set current data ({q} queues / {s} points / {c} in current) as default?\nDefault is never touched otherwise."},
    "msg_reset_def": {"zh": "用默认文档覆盖当前数据？当前队列会被替换（导入前会自动备份）。",
                      "en": "Overwrite current data with default? Current queue will be replaced (auto backup first)."},
    "msg_import_bad": {"zh": "这个文件不是 AutoClicker 数据文档（缺少 schema 字段）。", "en": "Not an AutoClicker data file (no schema field)."},
    "msg_import_ok": {"zh": "已导入：队列 {q} 条 / 单点 {s} 个。当前队列同步为导入版本。", "en": "Imported: {q} queues / {s} points. Current queue updated."},
    "msg_export_ok": {"zh": "已导出到：{p}", "en": "Exported to: {p}"},
    "log_q_store":  {"zh": "💾 队列已存为「{n}」（{k} 个点）", "en": "💾 Queue saved as \"{n}\" ({k} points)"},
    "log_q_load":   {"zh": "📂 已载入队列「{n}」（{k} 个点）", "en": "📂 Queue loaded: \"{n}\" ({k} points)"},
    "log_q_del":    {"zh": "🗑 队列「{n}」已删除", "en": "🗑 Queue \"{n}\" deleted"},
    "log_db_setdef": {"zh": "★ 当前数据已设为默认文档", "en": "★ Default document updated"},
    "log_db_reset": {"zh": "↺ 已恢复默认文档（旧数据备份在 database.bak.json）", "en": "↺ Restored default (old data -> database.bak.json)"},
    "log_db_import": {"zh": "📥 已导入数据文档：队列 {q} 条 / 单点 {s} 个", "en": "📥 Imported: {q} queues / {s} points"},
    "log_db_export": {"zh": "📤 已导出数据文档：{p}", "en": "📤 Exported: {p}"},
    "alert_bad_file": {"zh": "文件不对", "en": "Bad file"},
    "err_need_name": {"zh": "先给这条队列起个名字（队列名不能为空）。", "en": "Give this queue a name first."},
    "err_empty_queue": {"zh": "当前队列是空的，没东西可存。先拾取/加入几个点。", "en": "Current queue is empty. Add some points first."},
    "lbl_qcount":   {"zh": "次数:", "en": "Times:"},
    "lbl_qint":     {"zh": "间隔(ms):", "en": "Gap(ms):"},
    "tip_queue":    {"zh": "双击单元格直接改；Shift/⌘ 多选；拖动可换序；等待=进这一点前先停一下；次数=0=一直点",
                     "en": "Double-click to edit; Shift/⌘ multi-select; drag to reorder; Times=0=forever; top-down order"},
    "que_empty":    {"zh": "队列为空：拾取→加入，或点『🎯 连采』连续记录点击",
                     "en": "Queue empty — pick & add, or hit 🎯 Record"},
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
    "log_queue_save": {"zh": "💾 第 {i} 行已更新：({x}, {y}) ×{n} 间隔 {ms}ms",
                       "en": "💾 Row {i} saved: ({x}, {y}) ×{n} gap {ms}ms"},
    "log_queue_ins": {"zh": "➕ 第 {i} 行（插在选中行后）：({x}, {y}) ×{n} 间隔 {ms}ms",
                      "en": "➕ Inserted row {i}: ({x}, {y}) ×{n} gap {ms}ms"},
    "log_queue_del_row": {"zh": "🗑 已删除第 {i} 行", "en": "🗑 Deleted row {i}"},
    "lbl_rec_n": {"zh": "采集中… 已记录 {n} 点（去目标窗口点）",
                  "en": "Recording… {n} pts (click in target app)"},
    "err_rec_running": {"zh": "正在连采，先结束采集再开始执行",
                        "en": "Recording active — stop it first"},
    "log_rec_start": {"zh": "🎯 连采开始：去目标窗口正常点击，F9 / ⌘⇧X 或回到本窗口结束",
                      "en": "🎯 Recording — click in target app; F9 / ⌘⇧X or return here"},
    "log_rec_done": {"zh": "🎯 连采结束：新增 {n} 项，队列共 {t} 项（间隔按点击时间差自动填）",
                     "en": "🎯 Recorded {n} points; queue now {t} (gap = click delta)"},
    "log_rec_none": {"zh": "🎯 连采结束：没有记录到任何点击", "en": "🎯 Recording ended: no clicks"},
    "err_select_row": {"zh": "先在上方列表里点选一行", "en": "Pick a row in the list first"},
    "log_mute":     {"zh": "🔇 提示音已静音", "en": "🔇 Beeps muted"},
    "log_unmute":   {"zh": "🔊 提示音已恢复", "en": "🔊 Beeps on"},
    # ---- ② 运行设置 ----
    "grp2":        {"zh": "② 运行设置", "en": "② Run Settings"},
    "lbl_mode":    {"zh": "模式", "en": "Mode"},
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
    "field_name":   {"zh": "名称", "en": "Name"},
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
                # 前置等待：进入该点之前先等（下一组的延迟就写在下一点的这里）
                dly = max(0.0, float(p.get("delay_ms", 0) or 0)) / 1000.0
                if dly > 0:
                    deadline = time.time() + dly
                    while time.time() < deadline and not self.stop_event.is_set():
                        time.sleep(0.001)
                if self.stop_event.is_set():
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
        hook = getattr(self, "on_stop", None)
        if hook is not None:
            try:
                if hook():
                    return
            except Exception:
                pass
        w = self.get_worker()
        if w is not None:
            w.add_log(T("log_hotkey"))
            w.request_stop()


# ======================= AppKit 原生 UI =======================
class QueueTableView(NSTableView):
    """队列表格：拦 ⌘A（无菜单绑定，selectAll 不会自动触发）；其余交回系统。"""

    def keyDown_(self, ev):
        try:
            if ev.modifierFlags() & 1048576:      # NSEventModifierFlagCommand
                if ev.charactersIgnoringModifiers() == "a":
                    self.selectAll_(None)
                    return
        except Exception:
            pass
        NSTableView.keyDown_(self, ev)


class FlippedView(NSView):
    """坐标原点在左上角的容器：滚动内容与纵向排列都从顶部开始算。"""
    def isFlipped(self):
        return True


class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, notification):
        # ---- 数据文档：工作库 + 默认库（默认库不参与日常写入）----
        self._db = load_db()
        ensure_default_db()
        self.data_panel = None
        self.worker = None
        self.log_view = None
        self._beep_snd = None   # 倒计时"滴"音效缓存实例
        self.muted = False      # 提示音静音开关（复选框控制）
        # ---- 连采状态 ----
        self.recording = False
        self._rec_mon = None
        self._rec_points = []
        self._rec_last_ts = None
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
        try:
            self.build_ui()
        except Exception:
            import traceback
            open("/tmp/cc_ui_err.txt", "w").write(traceback.format_exc())
            raise
        # 全局热键
        self.hotkey = HotkeyListener(lambda: self.worker)
        self.hotkey.on_stop = self._hotkey_stop_hook
        self.hotkey.start()
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
        """原生网格布局：窗口可缩放 + 内容区滚动 + 日志/控制栏吸底。
        横向由 NSGridView 固定列宽统一，纵向由 NSStackView 自动累加，不再手写坐标。
        """
        self.CW = 552.0           # 统一内容宽：区块 / 表格 / 日志 / 控制栏同宽同边界
        self.SEC_W = self.CW
        self.LBL = 40.0           # 字段标签列宽
        self.FLD = 84.0           # 字段控件列宽
        self.COL = 132.0          # 一组 = 标签40 + 间距8 + 控件84，正好等于跨两列的按钮
        self.COLS = [self.LBL, self.FLD] * 4    # 8 列：4×(40+8+84) = 552
        CTRL_H, LOG_H = 108.0, 150.0
        W, H = 600.0, 880.0

        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
            NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered, False)
        self.window.setTitle_("AutoClicker - 自动点击器 / Auto Clicker")
        self.window.setMinSize_((600, 560))
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)

        def _place_window():
            try:
                b = CGDisplayBounds(CGMainDisplayID())
                sw, sh = float(b.size.width), float(b.size.height)
            except Exception:
                sw, sh = 1440.0, 900.0
            wx = max(0.0, (sw - W) / 2)
            wy = max(0.0, (sh - (H + 28)) / 2)
            self.window.setFrame_display_(NSMakeRect(wx, wy, W, H + 28), True)
        self._place_window = _place_window
        _place_window()

        content = self.window.contentView()
        self._ui_keys = []
        self._grids = []

        def keep(ctrl, key):
            if key:
                self._ui_keys.append((ctrl, key))
            return ctrl

        # ---------------- 内容滚动区 ----------------
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, CTRL_H + LOG_H, W, H - CTRL_H - LOG_H))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setDrawsBackground_(False)
        content.addSubview_(scroll)
        self._cscroll = scroll

        root = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 1))
        self._root = root
        self._sections = []
        scroll.setDocumentView_(root)

        def add_section(view):
            """新增一节：只往列表里追加，纵向位置由 _relayout 统一算。"""
            self._sections.append(view)
            root.addSubview_(view)
            return view

        self._add_section = add_section

        # ---------------- 页头 ----------------
        title = NSTextField.labelWithString_(T("app_title"))
        title.setFont_(NSFont.boldSystemFontOfSize_(22))
        title.setTextColor_(NSColor.controlAccentColor())
        title.setFrame_(NSMakeRect(0, 0, 320, 30))
        keep(title, "app_title")

        self.btn_about = NSButton.buttonWithTitle_target_action_(
            T("btn_about"), self, objc.selector(self.about_))
        self.btn_about.setFrame_(NSMakeRect(0, 0, 72, 26))
        keep(self.btn_about, "btn_about")

        self.popup_lang = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0, 0, 132, 26), False)
        self.popup_lang.addItemsWithTitles_(LANGUAGES)
        try:
            self.popup_lang.selectItemAtIndex_(LANG_KEYS.index(current_lang))
        except Exception:
            self.popup_lang.selectItemAtIndex_(0)
        self.popup_lang.menu().setAutoenablesItems_(False)
        self.popup_lang.setTarget_(self)
        self.popup_lang.setAction_(objc.selector(self.changeLang_))

        head1 = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.CW, 30))
        title.setFrame_(NSMakeRect(0, 0, 320, 30))
        head1.addSubview_(title)
        self.popup_lang.setFrame_(NSMakeRect(self.CW - 132, 2, 132, 26))
        head1.addSubview_(self.popup_lang)
        self.btn_about.setFrame_(NSMakeRect(self.CW - 132 - 8 - 72, 2, 72, 26))
        head1.addSubview_(self.btn_about)

        sub = NSTextField.labelWithString_(T("app_sub"))
        sub.setFont_(NSFont.systemFontOfSize_(12))
        sub.setTextColor_(NSColor.secondaryLabelColor())
        sub.setFrame_(NSMakeRect(0, 0, 364, 18))
        keep(sub, "app_sub")

        self.lbl_version = NSTextField.labelWithString_("v" + VERSION)
        self.lbl_version.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, 4))
        self.lbl_version.setTextColor_(NSColor.tertiaryLabelColor())
        self.lbl_version.setAlignment_(NSTextAlignmentRight)
        self.lbl_version.setFrame_(NSMakeRect(0, 0, 180, 18))

        head2 = self._hrow([sub, self.lbl_version])
        head2.setFrameSize_((self.CW, 18))

        self._add_section(self._vrow([head1, head2], spacing=6))
        self._add_section(self._hsep())

        # ---------------- ① 坐标拾取 ----------------
        self.field_x = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, self.FLD, 26))
        self.field_x.setStringValue_("2852")
        self.field_y = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, self.FLD, 26))
        self.field_y.setStringValue_("545")
        self.field_interval = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, self.FLD, 26))
        self.field_interval.setStringValue_("1")

        self.tip_interval = self._tip2("tip_interval", "tip_saved")

        self.btn_pick = NSButton.buttonWithTitle_target_action_(T("btn_pick"), self, objc.selector(self.pick_))
        self.btn_pick.setToolTip_(T("tip_pick_tt"))
        keep(self.btn_pick, "btn_pick")

        self.btn_test_pos = NSButton.buttonWithTitle_target_action_(T("btn_test"), self, objc.selector(self.testPos_))
        self.btn_test_pos.setToolTip_(T("tip_test_tt"))
        keep(self.btn_test_pos, "btn_test")

        self.btn_rec = NSButton.buttonWithTitle_target_action_(T("btn_rec"), self, objc.selector(self.record_))
        self.btn_rec.setToolTip_(T("tip_rec_tt"))
        keep(self.btn_rec, "btn_rec")

        self.lbl_pick = self._wide_label(T("lbl_pick_ready"), self.COL, 32, size=10)
        self.lbl_pick.setTextColor_(NSColor.secondaryLabelColor())
        self.lbl_pick.setLineBreakMode_(NSLineBreakByWordWrapping)
        self.lbl_pick.cell().setWraps_(True)

        self.field_saved_name = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, self.FLD, 26))
        self.field_saved_name.setPlaceholderString_(T("field_name"))

        self.btn_save = NSButton.buttonWithTitle_target_action_(T("btn_save"), self, objc.selector(self.saveCoord_))
        keep(self.btn_save, "btn_save")

        self.popup_saved = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0, 0, self.FLD, 26), False)
        self.popup_saved.addItemWithTitle_(T("ph_saved"))
        self.popup_saved.menu().setAutoenablesItems_(False)
        self.popup_saved.setTarget_(self)
        self.popup_saved.setAction_(objc.selector(self.coordsChanged_))

        sec1_grid = self._grid(
            [[self._lab("lbl_x"), self.field_x, self._lab("lbl_y"), self.field_y,
              self._lab("lbl_int"), (self.field_interval, 3)],
             [(self.btn_pick, 2), (self.btn_test_pos, 2), (self.btn_rec, 2), (self.lbl_pick, 2)],
             [self._lab("lbl_name"), self.field_saved_name, (self.btn_save, 2),
              (self.popup_saved, 4)],
             [(self.tip_interval, 8)]],
            [32, 32, 30, 16], widths=self.COLS)
        self._add_section(self._vrow([self._gtitle("grp1"), sec1_grid], spacing=8))

        # ---------------- ② 多点队列 ----------------
        self.queue_table = QueueTableView.alloc().initWithFrame_(
            NSMakeRect(0, 0, self.CW - 16, 112))
        self.queue_table.setRowHeight_(17)
        self.queue_table.setAllowsMultipleSelection_(True)
        self.queue_table.setAllowsColumnReordering_(False)
        self.queue_table.setAllowsColumnResizing_(True)
        self.queue_table.setAllowsColumnSelection_(False)
        self.queue_table.setUsesAlternatingRowBackgroundColors_(True)
        self.queue_table.setSelectionHighlightStyle_(1)
        self.queue_table.setDelegate_(self)
        self.queue_table.setDataSource_(self)
        self.queue_table.setTarget_(self)
        self.queue_table.setDoubleAction_(objc.selector(self.queueTableDoubleClick_))
        self.queue_table.registerForDraggedTypes_(["public.utf8-plain-text"])
        self.queue_table.setDraggingSourceOperationMask_forLocal_(16, True)
        self._build_queue_columns()

        qbox = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.CW, 112))
        qscroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, self.CW, 112))
        qscroll.setHasVerticalScroller_(True)
        qscroll.setAutohidesScrollers_(True)
        qscroll.setBorderType_(2)
        qscroll.setDocumentView_(self.queue_table)
        qbox.addSubview_(qscroll)
        self._qscroll = qscroll

        self.field_bcount = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, self.FLD, 26))
        self.field_bcount.setStringValue_("1")
        self.field_bint = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, self.FLD, 26))
        self.field_bint.setStringValue_("100")
        self.field_bdelay = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, self.FLD, 26))
        self.field_bdelay.setStringValue_("0")

        self.btn_bapply = NSButton.buttonWithTitle_target_action_(T("btn_bapply"), self, objc.selector(self.applyBatch_))
        keep(self.btn_bapply, "btn_bapply")

        self.btn_selall = NSButton.buttonWithTitle_target_action_(T("btn_selall"), self, objc.selector(self.selectAllRows_))
        keep(self.btn_selall, "btn_selall")

        self.lbl_selinfo = self._wide_label(T("lbl_sel_0"), self.COL, 22, size=11)
        self.lbl_selinfo.setTextColor_(NSColor.secondaryLabelColor())

        batch_grid = self._grid(
            [[self._lab("lbl_bcnt"), self.field_bcount, self._lab("lbl_bint"), self.field_bint,
              self._lab("lbl_bdelay"), self.field_bdelay, (self.btn_bapply, 2)],
             [(NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 1)), 4),
              (self.btn_selall, 2), (self.lbl_selinfo, 2)]],
            [30, 30], widths=self.COLS)

        self.btn_qadd = NSButton.buttonWithTitle_target_action_(T("btn_qadd"), self, objc.selector(self.queueAdd_))
        self.btn_qdup = NSButton.buttonWithTitle_target_action_(T("btn_qdup"), self, objc.selector(self.queueDup_))
        self.btn_qdel = NSButton.buttonWithTitle_target_action_(T("btn_qdel"), self, objc.selector(self.queueDel_))
        self.btn_qclear = NSButton.buttonWithTitle_target_action_(T("btn_qclear"), self, objc.selector(self.queueClear_))
        for b, k in ((self.btn_qadd, "btn_qadd"), (self.btn_qdup, "btn_qdup"),
                     (self.btn_qdel, "btn_qdel"), (self.btn_qclear, "btn_qclear")):
            keep(b, k)

        btn_grid = self._grid([[(self.btn_qadd, 2), (self.btn_qdup, 2),
                                (self.btn_qdel, 2), (self.btn_qclear, 2)]],
                              [30], widths=self.COLS)

        self.btn_data = NSButton.buttonWithTitle_target_action_(T("btn_data"), self, objc.selector(self.openDataPanel_))
        self.btn_data.setFrame_(NSMakeRect(self.CW - 112, 0, 112, 26))
        keep(self.btn_data, "btn_data")

        qhead = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.CW, 26))
        gt4 = self._gtitle("grp4")
        gt4.setFrameOrigin_((0, 4))
        qhead.addSubview_(gt4)
        qhead.addSubview_(self.btn_data)

        self._add_section(self._vrow([qhead, qbox, batch_grid, btn_grid,
                                      self._tip("tip_queue")], spacing=8))

        # ---------------- ③ 运行设置 ----------------
        self.popup_mode = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0, 0, self.COL, 26), False)
        self.popup_mode.addItemsWithTitles_([T("mode_duration"), T("mode_count"), T("mode_queue")])
        self.popup_mode.menu().setAutoenablesItems_(False)

        self.field_value = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 26))
        self.field_value.setStringValue_("60")

        self.chk_mute = NSButton.checkboxWithTitle_target_action_(
            T("chk_mute"), self, objc.selector(self.toggleMute_))
        self.chk_mute.setState_(0)
        self.chk_mute.setFont_(NSFont.systemFontOfSize_(12))
        keep(self.chk_mute, "chk_mute")

        sec3_grid = self._grid(
            [[self._lab("lbl_mode"), self.popup_mode, self._lab("lbl_value"),
              self.field_value, self.chk_mute]],
            [30], widths=[40, 132, 40, 100, 208])
        self._add_section(self._vrow([self._gtitle("grp2"), sec3_grid], spacing=8))

        # ---------------- 控制栏（吸底） ----------------
        ctrl_box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, CTRL_H))
        content.addSubview_(ctrl_box)
        self._ctrl_box = ctrl_box
        ctrl_sep = NSBox.alloc().initWithFrame_(NSMakeRect(0, CTRL_H - 1, W, 1))
        ctrl_sep.setBoxType_(2)
        ctrl_box.addSubview_(ctrl_sep)
        self._ctrl_sep = ctrl_sep

        self.btn_start = NSButton.buttonWithTitle_target_action_(T("btn_start"), self, objc.selector(self.start_))
        self.btn_test = NSButton.buttonWithTitle_target_action_(T("btn_test10"), self, objc.selector(self.test_))
        self.btn_stop = NSButton.buttonWithTitle_target_action_(T("btn_stop"), self, objc.selector(self.stop_))
        self.btn_stop.setEnabled_(False)
        for b, k in ((self.btn_start, "btn_start"), (self.btn_test, "btn_test10"), (self.btn_stop, "btn_stop")):
            keep(b, k)

        self.lbl_status = self._wide_label(T("status_idle"), 179, 20, size=11)
        self.lbl_count = self._wide_label(T("lbl_count", n=0), 179, 20, size=11)
        self.lbl_rate = self._wide_label(T("lbl_rate", r=0.0), 178, 20, size=11)

        self.lbl_elapsed = self._wide_label(T("lbl_elapsed", t="00:00:00"), 179, 18, bold=True)
        self.lbl_left = self._wide_label(T("lbl_left", t="00:00:00"), 179, 18, bold=True)
        self.lbl_total = self._wide_label(T("lbl_total", t="00:00:00"), 178, 18, align=NSTextAlignmentRight)

        ctrl_grid = self._grid(
            [[self.btn_start, self.btn_test, self.btn_stop],
             [self.lbl_status, self.lbl_count, self.lbl_rate],
             [self.lbl_elapsed, self.lbl_left, self.lbl_total]],
            [32, 20, 18], widths=[179, 179, 178])
        ctrl_grid.setFrameOrigin_((16, 8))
        ctrl_box.addSubview_(ctrl_grid)
        self._ctrl_grid = ctrl_grid

        # ---------------- 日志（吸底，在控制栏之上） ----------------
        log_box = NSView.alloc().initWithFrame_(NSMakeRect(0, CTRL_H, W, LOG_H))
        content.addSubview_(log_box)
        self._log_box = log_box

        log_sep = NSBox.alloc().initWithFrame_(NSMakeRect(0, LOG_H - 1, W, 1))
        log_sep.setBoxType_(2)
        log_box.addSubview_(log_sep)
        self._log_sep = log_sep

        lt = NSTextField.labelWithString_(T("log_title"))
        lt.setFont_(NSFont.boldSystemFontOfSize_(13))
        lt.setFrame_(NSMakeRect(24, LOG_H - 24, 200, 18))
        log_box.addSubview_(lt)
        keep(lt, "log_title")
        self._log_title = lt

        log_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16, 8, self.CW, LOG_H - 34))
        log_scroll.setHasVerticalScroller_(True)
        log_scroll.setAutohidesScrollers_(True)
        self.log_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, self.CW - 16, LOG_H - 34))
        self.log_view.setEditable_(False)
        self.log_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11, 0))
        self.log_view.setBackgroundColor_(NSColor.textBackgroundColor())
        self.log_view.setTextColor_(NSColor.textColor())
        self.log_view.setTextContainerInset_((4, 6))
        log_scroll.setDocumentView_(self.log_view)
        log_box.addSubview_(log_scroll)
        self._log_scroll = log_scroll

        # ---------------- 状态 ----------
        self._queue = self._load_queue()
        self._queue_sel = None
        self._refresh_queue_view()
        self._reload_saved_list()

        self.window.makeKeyAndOrderFront_(None)
        self._relayout()
        try:
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                0.15, False, lambda t: self._relayout())
        except Exception:
            pass

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    # ---- NSWindowDelegate（ObjC 回调，勿加 python_method） ----
    def windowDidResize_(self, note):
        """窗口缩放：滚动区/日志/控制栏贴底重排，内容栈高度贴合。"""
        try:
            self._relayout()
        except Exception:
            pass

    def changeLang_(self, sender):
        """语言下拉切换：刷新所有静态控件 + 模式项 + 动态状态。
        注意：不能加 @objc.python_method（否则无法作为 ObjC action 被分发）。"""
        global current_lang
        idx = sender.indexOfSelectedItem()
        if 0 <= idx < len(LANG_KEYS):
            current_lang = LANG_KEYS[idx]
            self._apply_lang()

    @objc.python_method
    def _est_text_w(self, text):
        """粗估文本像素宽：ASCII 按 0.56×字号，其它按 1.0×字号。"""
        size = 13.0
        w = 0.0
        for ch in str(text):
            w += size * (0.50 if ord(ch) < 128 else 1.0)
        return w

    @objc.python_method
    def _set_btn_title(self, btn, key):
        """按钮标题统一入口：定宽内能放下就用全称，放不下逐字截断加省略号，
        完整文案进 tooltip。为多语言（德语/俄语等长词）预留。"""
        txt = T(key)
        try:
            btn.setToolTip_(txt)
        except Exception:
            pass
        try:
            maxw = float(btn.frame().size.width) - 12.0
        except Exception:
            maxw = 0.0
        try:
            if maxw <= 24.0 or self._est_text_w(txt) <= maxw:
                btn.setTitle_(txt)
                return
            cut = ""
            for ch in txt:
                if self._est_text_w(cut + ch + "…") > maxw:
                    break
                cut += ch
            btn.setTitle_((cut.rstrip() + "…") if cut else "…")
        except Exception:
            pass

    @objc.python_method
    def _apply_lang(self):
        for ctrl, key in self._ui_keys:
            txt = T(key)
            # 按钮：定宽 + 超长缩写 + tooltip 完整文案；其它控件走 setStringValue_
            try:
                if ctrl.isKindOfClass_(NSButton):
                    self._set_btn_title(ctrl, key)
                    continue
            except Exception:
                pass
            for setter in ("setStringValue_", "setTitle_"):
                if not hasattr(ctrl, setter):
                    continue
                try:
                    getattr(ctrl, setter)(txt)
                    break
                except Exception:
                    continue
        # 运行模式下拉项（保留当前选中）
        sel = self.popup_mode.indexOfSelectedItem()
        self.popup_mode.removeAllItems()
        self.popup_mode.addItemsWithTitles_([T("mode_duration"), T("mode_count"), T("mode_queue")])
        if 0 <= sel < 3:
            self.popup_mode.selectItemAtIndex_(sel)
        self._refresh_queue_view()
        # 表格列名随语言切换
        try:
            for col in self.queue_table.tableColumns():
                col.setTitle_(T("col_" + str(col.identifier())))
        except Exception:
            pass
        # 坐标保存：占位项 + 名称输入占位
        self.field_saved_name.setPlaceholderString_(T("field_name"))
        if hasattr(self, "popup_saved"):
            if self._saved_coords is not None:
                self._reload_saved_list()
            else:
                try:
                    self.popup_saved.removeAllItems()
                    self.popup_saved.addItemWithTitle_(T("ph_saved"))
                except Exception:
                    pass
        # 按钮 tooltip
        self.btn_pick.setToolTip_(T("tip_pick_tt"))
        self.btn_test_pos.setToolTip_(T("tip_test_tt"))
        # 倒计时三行（空闲时不会跑 refresh_，必须在这里补）
        self.lbl_elapsed.setStringValue_(T("lbl_elapsed", t="00:00:00"))
        self.lbl_left.setStringValue_(T("lbl_left", t="00:00:00"))
        self.lbl_total.setStringValue_(T("lbl_total", t="00:00:00"))
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

    # ---------- 原生网格构建（新增）：不再手写坐标 ----------
    @objc.python_method
    def _grid(self, rows, heights, widths=None, spacing=8):
        """定宽网格：每格宽度锁死、行高锁死，行内纵向居中。
        用普通容器自行定位（NSGridView 在无约束环境下不会给子视图排位，会全叠在原点）。"""
        ws = list(widths or ([self.COL] * max(len(r) for r in rows)))
        total_w = sum(ws) + spacing * max(0, len(ws) - 1)
        total_h = sum(heights) + spacing * max(0, len(heights) - 1)
        box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, total_w, total_h))
        y = total_h
        for r, row in enumerate(rows):
            h = float(heights[r])
            y -= h
            x = 0.0
            c = 0
            for cell in row:
                v, span = cell if isinstance(cell, tuple) else (cell, 1)
                w = sum(ws[c:c + span]) + spacing * (span - 1)
                vh = float(v.frame().size.height) or h
                v.setFrame_(NSMakeRect(x, y + max(0.0, (h - vh) / 2.0), w, vh))
                box.addSubview_(v)
                x += w + spacing
                c += span
            y -= spacing
        return box

    @objc.python_method
    def _tip2(self, k1, k2, w=None):
        """一行两段说明，左右并排，宽度铺满。"""
        ww = float(w or self.CW)
        box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, ww, 16))
        half = (ww - 12.0) / 2.0
        a = self._tip(k1, w=half)
        b = self._tip(k2, w=half)
        a.setFrameOrigin_((0.0, 0.0))
        b.setFrameOrigin_((half + 12.0, 0.0))
        box.addSubview_(a)
        box.addSubview_(b)
        return box

    @objc.python_method
    def _lab(self, text_key, w=None, h=16):
        """一格里的字段标签：宽度固定，与右侧控件严格同起点。"""
        lbl = NSTextField.labelWithString_(T(text_key))
        lbl.setFont_(NSFont.systemFontOfSize_(11))
        lbl.setFrame_(NSMakeRect(0, 0, float(w or self.LBL), h))
        self._ui_keys.append((lbl, text_key))
        return lbl

    @objc.python_method
    def _gtitle(self, text_key):
        lbl = NSTextField.labelWithString_(T(text_key))
        lbl.setFont_(NSFont.systemFontOfSize_weight_(13, 600))
        lbl.setTextColor_(NSColor.controlAccentColor())
        lbl.setFrame_(NSMakeRect(0, 0, 300, 22))
        self._ui_keys.append((lbl, text_key))
        return lbl

    @objc.python_method
    def _tip(self, text_key=None, text=None, w=None):
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, float(w or self.SEC_W), 16))
        lbl.setStringValue_(text if text is not None else T(text_key))
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setBordered_(False)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setFont_(NSFont.systemFontOfSize_(9))
        lbl.setTextColor_(NSColor.secondaryLabelColor())
        lbl.setLineBreakMode_(NSLineBreakByWordWrapping)
        lbl.cell().setWraps_(True)
        if text_key:
            self._ui_keys.append((lbl, text_key))
        return lbl

    @objc.python_method
    def _wide_label(self, text, w=None, h=20, bold=False, align=None, size=13):
        lbl = NSTextField.labelWithString_(text)
        lbl.setFrame_(NSMakeRect(0, 0, float(w or self.SEC_W), h))
        lbl.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
        if align is not None:
            lbl.setAlignment_(align)
        return lbl

    @objc.python_method
    def _hrow(self, views, spacing=8.0):
        """一行：横向依次排开，纵向各自居中。"""
        w = sum(float(v.frame().size.width) for v in views) + spacing * max(0, len(views) - 1)
        hh = max([float(v.frame().size.height) for v in views] or [26.0])
        box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, hh))
        x = 0.0
        for v in views:
            vh = float(v.frame().size.height)
            v.setFrameOrigin_((x, max(0.0, (hh - vh) / 2.0)))
            box.addSubview_(v)
            x += float(v.frame().size.width) + spacing
        return box

    @objc.python_method
    def _vrow(self, views, spacing=8.0, width=None):
        """一列：纵向依次排开（左上原点，自上而下）。"""
        w = max([float(x.frame().size.width) for x in views] or [float(width or self.SEC_W)])
        if width:
            w = float(width)
        hh = sum(float(x.frame().size.height) for x in views) + spacing * max(0, len(views) - 1)
        box = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, w, hh))
        y = 0.0
        for x in views:
            x.setFrameOrigin_((0, y))
            box.addSubview_(x)
            y += float(x.frame().size.height) + spacing
        return box

    @objc.python_method
    def _relayout(self):
        """窗口尺寸变化后：把滚动区/日志/控制栏贴到底部，并让内容栈高度贴合。"""
        try:
            fr = self.window.contentView().frame()
            W, H = float(fr.size.width), float(fr.size.height)
            CTRL_H = 108.0
            LOG_H = max(110.0, min(150.0, H - 560.0))
            self._cscroll.setFrame_(NSMakeRect(0, CTRL_H + LOG_H, W, max(120.0, H - CTRL_H - LOG_H)))
            self._log_box.setFrame_(NSMakeRect(0, CTRL_H, W, LOG_H))
            self._ctrl_box.setFrame_(NSMakeRect(0, 0, W, CTRL_H))
            m = max(16.0, (W - self.CW) / 2.0)
            self._log_scroll.setFrame_(NSMakeRect(m, 8, self.CW, LOG_H - 34))
            try:
                self._log_title.setFrameOrigin_((m, LOG_H - 24.0))
                self._log_sep.setFrame_(NSMakeRect(0, LOG_H - 1, W, 1))
                self._ctrl_sep.setFrame_(NSMakeRect(0, CTRL_H - 1, W, 1))
            except Exception:
                pass
            try:
                self._ctrl_grid.setFrameOrigin_((m, 8.0))
            except Exception:
                pass
            docw = max(self.CW + 32.0, float(self._cscroll.contentSize().width))
            root = self._root
            y = 16.0
            if os.environ.get("CC_DUMP_BOXES"):
                self._dump_boxes()
            for sec in self._sections:
                sec.setFrameOrigin_((m, y))
                y += float(sec.frame().size.height) + 10.0
            root.setFrameSize_((docw, max(y + 8.0, float(self._cscroll.contentSize().height))))
            if getattr(self, "_need_scroll_top", True):
                self._need_scroll_top = False
                try:
                    self._cscroll.contentView().scrollToPoint_((0.0, 0.0))
                    self._cscroll.reflectScrolledClipView_(self._cscroll.contentView())
                except Exception:
                    pass
            pass
            pass
        except Exception:
            pass

    @objc.python_method
    def _dump_boxes(self):
        """把每个可见控件的窗口坐标与宽度打到文件，用来核对左右边界是否一致。"""
        lines = []
        W = float(self.window.contentView().frame().size.width)

        def walk(v, depth, tag):
            if depth > 7:
                return
            try:
                r = v.convertRect_toView_(v.bounds(), None)
                cls = v.className()
                extra = ""
                if cls == "NSTextField":
                    extra = " | " + str(v.stringValue())[:18]
                elif cls == "NSButton":
                    extra = " | " + str(v.title())[:18]
                lines.append("%s%s x=%.1f right=%.1f w=%.1f y=%.1f h=%.1f%s" % (
                    "  " * depth, cls, r.origin.x, r.origin.x + r.size.width,
                    r.size.width, r.origin.y, r.size.height, extra))
            except Exception:
                pass
            for sub in v.subviews():
                walk(sub, depth + 1, tag)

        walk(self.window.contentView(), 0, "")
        lines.insert(0, "WINDOW content W=%.1f" % W)
        open("/tmp/cc_boxes.txt", "w").write("\n".join(lines))

    # ---------- 辅助构建 ----------
    @objc.python_method
    def _hsep(self):
        """横向分隔线：整宽 1px，交给 stack 自动排位。"""
        box = NSBox.alloc().initWithFrame_(NSMakeRect(0, 0, self.SEC_W, 1))
        box.setBoxType_(2)      # NSBoxSeparator
        return box

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

    # ================= 数据文档（独立面板） =================

    def openDataPanel_(self, sender):
        if getattr(self, "data_panel", None) is None:
            self._build_data_panel()
        self._refresh_data_panel()
        self.data_panel.makeKeyAndOrderFront_(None)

    @objc.python_method
    def _build_data_panel(self):
        rect = NSMakeRect(0, 0, 520, 420)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False)
        win.setTitle_(T("panel_title"))
        win.setReleasedWhenClosed_(False)
        win.setFrame_display_(NSMakeRect(1170, 420, 520, 420), True)
        content = win.contentView()

        content.addSubview_(self._separator(16, 360, 488))

        # ---- 存队列 ----
        content.addSubview_(self._label(T("lbl_store_q"), 16, 318, 64, 22))
        self.field_store_q = self._input("", 84, 316, 236, 26)
        self.field_store_q.setPlaceholderString_(T("ph_store_q"))
        content.addSubview_(self.field_store_q)
        self.btn_store_q = self._button(T("btn_store_q"), 328, 314, 122, 30, self.storeQueue_)
        content.addSubview_(self.btn_store_q)

        # ---- 已存队列 ----
        content.addSubview_(self._label(T("lbl_saved_q"), 16, 282, 200, 22))
        self.popup_dq = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(16, 252, 300, 26), False)
        self.popup_dq.menu().setAutoenablesItems_(False)
        content.addSubview_(self.popup_dq)
        self.btn_load_q = self._button(T("btn_load_q"), 326, 250, 78, 28, self.loadQueue_)
        content.addSubview_(self.btn_load_q)
        self.btn_del_q = self._button(T("btn_del_q"), 412, 250, 78, 28, self.delQueue_)
        content.addSubview_(self.btn_del_q)
        content.addSubview_(self._separator(16, 238, 488))

        # ---- 单点库 ----
        content.addSubview_(self._label(T("lbl_singles"), 16, 212, 200, 22))
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16, 140, 488, 64))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        self.singles_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 470, 64))
        self.singles_view.setEditable_(False)
        self.singles_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11, 0))
        self.singles_view.setBackgroundColor_(NSColor.textBackgroundColor())
        self.singles_view.setTextColor_(NSColor.textColor())
        self.singles_view.setTextContainerInset_((4, 4))
        scroll.setDocumentView_(self.singles_view)
        content.addSubview_(scroll)
        content.addSubview_(self._separator(16, 130, 488))

        # ---- 文件操作 ----
        self.btn_export = self._button(T("btn_export"), 16, 90, 116, 32, self.exportDB_)
        content.addSubview_(self.btn_export)
        self.btn_import = self._button(T("btn_import"), 140, 90, 116, 32, self.importDB_)
        content.addSubview_(self.btn_import)
        self.btn_set_def = self._button(T("btn_set_def"), 262, 90, 120, 32, self.setDefaultDB_)
        content.addSubview_(self.btn_set_def)
        self.btn_reset_def = self._button(T("btn_reset_def"), 390, 90, 114, 32, self.restoreDefaultDB_)
        content.addSubview_(self.btn_reset_def)

        self.lbl_panel_info = self._multiline_label("", 16, 44, 488, 40, font_size=9)
        content.addSubview_(self.lbl_panel_info)
        self.lbl_panel_path = self._label("", 16, 14, 488, 18, small=True)
        content.addSubview_(self.lbl_panel_path)

        self.data_panel = win

    @objc.python_method
    def _refresh_data_panel(self):
        if getattr(self, "data_panel", None) is None:
            return
        # 队列下拉
        try:
            self.popup_dq.removeAllItems()
            self.popup_dq.addItemWithTitle_(T("lbl_saved_q") + (" (%d)" % len(self._db.get("queues") or [])))
            for q in (self._db.get("queues") or []):
                n = len(q.get("points") or [])
                self.popup_dq.addItemWithTitle_("%s (%d)" % (q.get("name", "?"), n))
            self.popup_dq.selectItemAtIndex_(0)
        except Exception:
            pass
        # 单点库
        try:
            items = self._db.get("singles") or []
            if items:
                txt = "\n".join("%d. %s  (%d, %d)" % (i + 1, c.get("name", "?"), c.get("x", 0), c.get("y", 0))
                                for i, c in enumerate(items))
            else:
                txt = T("ph_none")
            self.singles_view.setString_(txt)
        except Exception:
            pass
        # 路径与统计
        try:
            self.lbl_panel_path.setStringValue_(T("lbl_db_path", p="database.json  ·  default_profile.json（同目录）"))
            self.lbl_panel_info.setStringValue_(
                "最后保存：%s\n导出/导入都是整库；默认文档只有「设为默认」才动它" % self._db.get("updated", "-"))
        except Exception:
            pass

    def storeQueue_(self, sender):
        try:
            name = str(self.field_store_q.stringValue()).strip()[:32]
            if not name:
                self.show_alert(T("alert_notice"), T("err_need_name")); return
            if not getattr(self, "_queue", None):
                self.show_alert(T("alert_notice"), T("err_empty_queue")); return
            queues = self._db.setdefault("queues", [])
            idx = next((i for i, q in enumerate(queues) if q.get("name") == name), None)
            pts = [dict(p) for p in self._queue]
            if idx is not None:
                if self.show_alert_choice(T("alert_notice"), T("msg_q_over", n=name),
                                          T("btn_continue"), T("btn_stop")) != "continue":
                    return
                queues[idx] = {"name": name, "points": pts, "updated": _now_str()}
            else:
                queues.append({"name": name, "points": pts, "updated": _now_str()})
            save_db(self._db)
            self.field_store_q.setStringValue_("")
            self.add_log(T("log_q_store", n=name, k=len(pts)))
            self._refresh_data_panel()
        except Exception as e:
            self.add_log("storeQueue err: %r" % (e,))

    def loadQueue_(self, sender):
        try:
            i = self.popup_dq.indexOfSelectedItem()
            if i <= 0:
                return
            q = (self._db.get("queues") or [])[i - 1]
            pts = q.get("points") or []
            if self.show_alert_choice(T("alert_notice"),
                                      T("msg_q_load", n=q.get("name", "?"), k=len(pts)),
                                      T("btn_continue"), T("btn_stop")) != "continue":
                return
            self._queue = [{"x": int(p.get("x", 0)), "y": int(p.get("y", 0)),
                            "count": int(p.get("count", 0) or 0),
                            "interval_ms": float(p.get("interval_ms", 0) or 0),
                            "name": str(p.get("name", "") or "")} for p in pts]
            self._queue_sel = None
            self.field_bcount.setStringValue_("1")
            self.field_bint.setStringValue_("100")
            self.field_bdelay.setStringValue_("0")
            self._save_queue(self._queue)
            self._refresh_queue_view()
            self.add_log(T("log_q_load", n=q.get("name", "?"), k=len(self._queue)))
        except Exception as e:
            self.add_log("loadQueue err: %r" % (e,))

    def delQueue_(self, sender):
        try:
            i = self.popup_dq.indexOfSelectedItem()
            if i <= 0:
                return
            q = (self._db.get("queues") or [])[i - 1]
            if self.show_alert_choice(T("alert_notice"), T("msg_q_del", n=q.get("name", "?")),
                                      T("btn_continue"), T("btn_stop")) != "continue":
                return
            name = q.get("name", "?")
            self._db["queues"].pop(i - 1)
            save_db(self._db)
            self.add_log(T("log_q_del", n=name))
            self._refresh_data_panel()
        except Exception as e:
            self.add_log("delQueue err: %r" % (e,))

    def exportDB_(self, sender):
        try:
            sp = NSSavePanel.savePanel()
            sp.setNameFieldStringValue_("AutoClicker-data.json")
            try:
                sp.setAllowedFileTypes_(["json"])
            except Exception:
                pass
            if sp.runModal() != NSModalResponseOK:
                return
            path = sp.URL().path()
            if save_db(dict(self._db), path):
                self.add_log(T("log_db_export", p=path))
                self.show_alert(T("panel_title"), T("msg_export_ok", p=path))
            else:
                self.add_log("exportDB failed")
        except Exception as e:
            self.add_log("exportDB err: %r" % (e,))

    def importDB_(self, sender):
        try:
            op = NSOpenPanel.openPanel()
            op.setCanChooseFiles_(True)
            op.setCanChooseDirectories_(False)
            op.setAllowsMultipleSelection_(False)
            try:
                op.setAllowedFileTypes_(["json"])
            except Exception:
                pass
            if op.runModal() != NSModalResponseOK:
                return
            path = op.URL().path()
            data = _read_json(path, None)
            if not (isinstance(data, dict) and "schema" in data):
                self.show_alert(T("alert_bad_file"), T("msg_import_bad"))
                return
            # 先备份当前库，再覆盖
            save_db(self._db, DB_PATH + ".bak.json")
            ndb = _empty_db()
            ndb["current"] = [p for p in (data.get("current") or []) if isinstance(p, dict)]
            ndb["queues"] = [q for q in (data.get("queues") or []) if isinstance(q, dict)]
            ndb["singles"] = [c for c in (data.get("singles") or []) if isinstance(c, dict)]
            self._db = ndb
            save_db(self._db)
            self._queue = self._load_queue()
            self._queue_sel = None
            self._refresh_queue_view()
            self._reload_saved_list()
            self.add_log(T("log_db_import", q=len(ndb["queues"]), s=len(ndb["singles"])))
            self._refresh_data_panel()
            self.show_alert(T("panel_title"), T("msg_import_ok", q=len(ndb["queues"]), s=len(ndb["singles"])))
        except Exception as e:
            self.add_log("importDB err: %r" % (e,))

    def setDefaultDB_(self, sender):
        try:
            if self.show_alert_choice(
                    T("alert_notice"),
                    T("msg_set_def", q=len(self._db.get("queues") or []),
                      s=len(self._db.get("singles") or []),
                      c=len(self._db.get("current") or [])),
                    T("btn_continue"), T("btn_stop")) != "continue":
                return
            save_db(self._db, DEF_PATH)
            self.add_log(T("log_db_setdef"))
            self._refresh_data_panel()
        except Exception as e:
            self.add_log("setDefaultDB err: %r" % (e,))

    def restoreDefaultDB_(self, sender):
        try:
            if self.show_alert_choice(T("alert_notice"), T("msg_reset_def"),
                                      T("btn_continue"), T("btn_stop")) != "continue":
                return
            save_db(self._db, DB_PATH + ".bak.json")   # 备份现有
            self._db = read_default_db()
            save_db(self._db)
            self._queue = self._load_queue()
            self._queue_sel = None
            self._refresh_queue_view()
            self._reload_saved_list()
            self.add_log(T("log_db_reset"))
            self._refresh_data_panel()
        except Exception as e:
            self.add_log("restoreDefaultDB err: %r" % (e,))

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
        if getattr(self, "recording", False):
            self.show_alert(T("alert_notice"), T("err_rec_running"))
            return
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
        self._db["singles"] = [dict(c) for c in coords]
        save_db(self._db)

    @objc.python_method
    def _load_coords(self):
        return [dict(c) for c in (self._db.get("singles") or []) if isinstance(c, dict)]

    @objc.python_method
    def _set_running(self, running):
        self.btn_start.setEnabled_(not running)
        self.btn_test.setEnabled_(not running)
        try:
            if getattr(self, "btn_rec", None) is not None:
                self.btn_rec.setEnabled_(not running)
        except Exception:
            pass
        self.btn_stop.setEnabled_(running)
        self.lbl_status.setStringValue_(T("status_run") if running else T("status_idle"))
        if running:
            self.lbl_elapsed.setStringValue_(T("lbl_elapsed", t="00:00:00"))
            self.lbl_left.setStringValue_(T("lbl_left", t="00:00:00"))
            self.lbl_total.setStringValue_(T("lbl_total", t="00:00:00"))

    # ---------- 多点队列（NSTableView） ----------
    _QCOLS = ("no", "tag", "x", "y", "delay", "count", "iv")

    @objc.python_method
    def _build_queue_columns(self):
        """建列：# / 标签 / X / Y / 等待 / 次数 / 间隔。
        宽度用原生比例分配（比例来自各列的内容量），不手算；用户可拖拽调整。"""
        spec = [("no", T("col_no"), 0.06, 28, False),
                ("tag", T("col_tag"), 0.20, 60, True),
                ("x", T("col_x"), 0.13, 44, True),
                ("y", T("col_y"), 0.13, 44, True),
                ("delay", T("col_delay"), 0.16, 48, True),
                ("count", T("col_count"), 0.13, 42, True),
                ("iv", T("col_iv"), 0.19, 46, True)]
        n = len(spec)
        sp = float(self.queue_table.intercellSpacing().width)
        avail = float(self.queue_table.frame().size.width) - sp * max(0, n - 1) - 4.0
        for ident, title, ratio, minw, editable in spec:
            col = NSTableColumn.alloc().initWithIdentifier_(ident)
            col.setTitle_(title)
            col.setMinWidth_(minw)
            col.setMaxWidth_(4000)
            col.setWidth_(max(float(minw), avail * ratio))
            col.setResizingMask_(1 | 2)            # AutoresizingMask: 用户可调 + 跟随表格
            col.setEditable_(editable)
            col.headerCell().setAlignment_(2)       # 表头居中（用户明确要求）
            self.queue_table.addTableColumn_(col)
        # 列宽用比例初值，剩下的像素补给最后一列，表格右缘不留白
        used = sum(float(c.width()) for c in self.queue_table.tableColumns())
        rest = avail - used
        if rest > 0:
            cols = self.queue_table.tableColumns()
            cols[-1].setWidth_(float(cols[-1].width()) + rest)
        # 只让最后一列吸收后续宽度变化，其余列宽保持
        self.queue_table.setColumnAutoresizingStyle_(4)

    @objc.python_method
    def _row_cells(self, row):
        """一行要显示的值（dict），序号动态算，次数 0 显示 ∞。"""
        q = self._queue[row]
        c = int(q.get("count", 0) or 0)
        return {"no": str(row + 1),
                "tag": str(q.get("name", "") or ""),
                "x": str(q.get("x", "")),
                "y": str(q.get("y", "")),
                "delay": "%g" % float(q.get("delay_ms", 0) or 0),
                "count": ("∞" if not c else str(c)),
                "iv": "%g" % float(q.get("interval_ms", 0) or 0)}

    # ---- NSTableView 数据源 / 委托（ObjC 回调，勿加 python_method） ----
    def numberOfRowsInTableView_(self, tv):
        return len(getattr(self, "_queue", []) or [])

    def tableView_viewForTableColumn_row_(self, tv, col, row):
        """view-based 单元格：复用 NSTableCellView，里面一枚无边框 NSTextField。"""
        try:
            ident = str(col.identifier())
            if ident not in self._QCOLS:
                return None
            cell = tv.makeViewWithIdentifier_owner_(ident, self)
            if cell is None:
                w = float(col.width())
                cell = NSTableCellView.alloc().initWithFrame_(NSMakeRect(0, 0, w, 18))
                cell.setIdentifier_(ident)
                tf = NSTextField.alloc().initWithFrame_(NSMakeRect(2, 0, w - 4, 18))
                tf.setBordered_(False)
                tf.setDrawsBackground_(False)
                tf.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11, 0))
                tf.setEditable_(ident != "no")
                tf.setSelectable_(True)
                tf.setAlignment_(2 if ident in ("no", "x", "y", "delay", "count", "iv") else 0)
                tf.setAutoresizingMask_(2 | 16)     # 宽高随父视图
                tf.setDelegate_(self)
                cell.addSubview_(tf)
                cell.setTextField_(tf)
            tf = cell.textField()
            vals = self._row_cells(row)
            tf.setStringValue_(vals.get(ident, ""))
            tf.setTag_((row + 1) * 10 + self._QCOLS.index(ident))
            return cell
        except Exception:
            return None

    def tableViewSelectionDidChange_(self, note):
        """选中变化：更新标记、批量区提示；单选时回填该行数值。"""
        try:
            tv = self.queue_table
            rows = tv.selectedRowIndexes()
            n = rows.count()
            self._queue_sel = tv.selectedRow() if tv.selectedRow() >= 0 else None
            self.lbl_selinfo.setStringValue_(T("lbl_sel_n", n=n) if n else T("lbl_sel_0"))
            self.btn_bapply.setEnabled_(n > 0)
            if n == 1 and self._queue_sel is not None and 0 <= self._queue_sel < len(self._queue):
                q = self._queue[self._queue_sel]
                self.field_bcount.setStringValue_(str(int(q.get("count", 0) or 0)))
                self.field_bint.setStringValue_("%g" % float(q.get("interval_ms", 0) or 0))
                self.field_bdelay.setStringValue_("%g" % float(q.get("delay_ms", 0) or 0))
        except Exception:
            pass

    def controlTextDidEndEditing_(self, note):
        """单元格编辑提交：按 tag 还原出 (行, 列) 再校验写回。"""
        try:
            tf = note.object()
            tag = int(tf.tag())
            if tag < 10:
                return
            row, cidx = divmod(tag, 10)
            row -= 1
            if not (0 <= cidx < len(self._QCOLS)):
                return
            key = self._QCOLS[cidx]
            if key == "no" or not (0 <= row < len(self._queue)):
                return
            self._commit_cell(row, key, str(tf.stringValue()))
        except Exception:
            pass

    def queueTableDoubleClick_(self, sender):
        """双击 → 该格进入编辑态。"""
        try:
            r, c = sender.clickedRow(), sender.clickedColumn()
            if r < 0 or c < 0:
                return
            ident = str(sender.tableColumns().objectAtIndex_(c).identifier())
            if ident == "no":
                return
            sender.editColumn_row_withEvent_select_(c, r, None, True)
        except Exception:
            pass

    # ---- 拖拽换序 ----
    def tableView_writeRowsWithIndexes_toPasteboard_(self, tv, rows, pb):
        try:
            data = [int(i) for i in rows]
            pb.declareTypes_owner_(["public.utf8-plain-text"], None)
            pb.setString_forType_(json.dumps(data), "public.utf8-plain-text")
            return True
        except Exception:
            return False

    def tableView_validateDrop_proposedRow_proposedDropOperation_(self, tv, info, row, op):
        return 16 if getattr(self, "_queue", None) else 0     # Move / None

    def tableView_acceptDrop_row_dropOperation_(self, tv, info, row, op):
        try:
            src = json.loads(info.draggingPasteboard().stringForType_("public.utf8-plain-text") or "[]")
            return self._move_rows(src, row)
        except Exception:
            return False

    @objc.python_method
    def _move_rows(self, src, row):
        """把 src 行整体移动到 row 位置（row 为插入点，按剩余行计）。"""
        src = [i for i in src if 0 <= i < len(self._queue)]
        if not src:
            return False
        first = src[0]
        items = [self._queue[i] for i in src]
        for i in sorted(src, reverse=True):
            self._queue.pop(i)
        target = row - sum(1 for i in src if i < row)
        target = max(0, min(target, len(self._queue)))
        for k, it in enumerate(items):
            self._queue.insert(target + k, it)
        self._save_queue(self._queue)
        self._reload_queue_table(keep_selection=list(range(target, target + len(items))))
        self.add_log(T("log_q_move", i=first + 1, j=target + 1))
        return True

    # ---- 单元格写回 ----
    @objc.python_method
    def _commit_cell(self, row, key, text):
        """单元格写回：key 是列标识（tag/delay/iv/count/x/y），落到对应数据字段。"""
        fld = {"tag": "name", "delay": "delay_ms", "iv": "interval_ms"}.get(key, key)
        q = self._queue[row]
        old = q.get(fld)
        text = (text or "").strip()
        try:
            if key == "tag":
                val = text[:24]
            elif key in ("x", "y"):
                val = int(float(text))
                if not (-100000 <= val <= 100000):
                    raise ValueError
            elif key == "count":
                val = int(float(text or "0"))
                if not (0 <= val <= 1000000):
                    raise ValueError
            elif key == "delay":
                val = float(text or "0")
                if not (0 <= val <= 600000):
                    raise ValueError
            elif key == "iv":
                val = float(text or "0")
                if not (0 <= val <= 600000):
                    raise ValueError
            else:
                return
        except Exception:
            self.add_log(T("err_cell_bad", f=key, v=text))
            self._reload_queue_table()
            return
        if key in ("iv", "delay"):
            same = abs(float(val) - float(old or 0)) < 1e-9
        else:
            same = (val == old)
        if same:
            return
        q[fld] = val
        self._save_queue(self._queue)
        self._reload_queue_table()
        self.add_log(T("log_row_edit", i=row + 1, f=key, a=old, b=val))

    # ---- 选中 / 批量 ----
    @objc.python_method
    def _selected_rows(self):
        try:
            return [int(i) for i in self.queue_table.selectedRowIndexes()]
        except Exception:
            return []

    def selectAllRows_(self, sender):
        try:
            self.queue_table.selectAll_(None)
        except Exception:
            pass

    def applyBatch_(self, sender):
        """把批量区的次数/间隔套到所有选中行（action）。"""
        rows = self._selected_rows()
        if not rows:
            self.show_alert(T("alert_notice"), T("err_sel_none"))
            return
        try:
            cnt = int(float(self.field_bcount.stringValue() or "0"))
            iv = float(self.field_bint.stringValue() or "0")
            dly = float(self.field_bdelay.stringValue() or "0")
        except Exception:
            self.show_alert(T("alert_bad"), T("msg_coordnum"))
            return
        if not (0 <= cnt <= 1000000) or not (0 <= iv <= 600000) or not (0 <= dly <= 600000):
            self.show_alert(T("alert_bad"), T("err_interval"))
            return
        for i in rows:
            if 0 <= i < len(self._queue):
                self._queue[i]["count"] = cnt
                self._queue[i]["interval_ms"] = iv
                self._queue[i]["delay_ms"] = dly
        self._save_queue(self._queue)
        self._reload_queue_table(keep_selection=rows)
        self.add_log(T("log_q_batch", n=len(rows), c=("∞" if not cnt else cnt),
                       ms="%g / 等待 %g" % (iv, dly)))

    @objc.python_method
    def _batch_vals(self):
        """读批量区的次数/间隔（也作为「加入」新点时的默认值）。"""
        try:
            cnt = int(float(self.field_bcount.stringValue() or "0"))
            iv = float(self.field_bint.stringValue() or "0")
            dly = float(self.field_bdelay.stringValue() or "0")
        except Exception:
            cnt, iv, dly = 1, 100.0, 0.0
        cnt = max(0, min(cnt, 1000000))
        iv = max(0.0, min(iv, 600000.0))
        dly = max(0.0, min(dly, 600000.0))
        return cnt, iv, dly

    def queueAdd_(self, sender):
        """X/Y 入队（次数/间隔取批量区值）；有选中行则插到其后（action）。"""
        try:
            x = int(self.field_x.stringValue())
            y = int(self.field_y.stringValue())
        except Exception:
            self.show_alert(T("alert_bad"), T("msg_coordnum"))
            return
        if len(getattr(self, "_queue", [])) >= 200:
            self.show_alert(T("alert_notice"), T("err_count", n=200))
            return
        cnt, iv, dly = self._batch_vals()
        item = {"x": x, "y": y, "count": cnt, "interval_ms": iv,
                "delay_ms": dly, "name": ""}
        sel = getattr(self, "_queue_sel", None)
        if sel is not None and 0 <= sel < len(self._queue):
            self._queue.insert(sel + 1, item)
            pos = sel + 1
            key = "log_queue_ins"
        else:
            self._queue.append(item)
            pos = len(self._queue) - 1
            key = "log_queue_add"
        self._save_queue(self._queue)
        self._reload_queue_table(keep_selection=[pos])
        self.add_log(T(key, i=pos + 1, x=x, y=y,
                       n=("∞" if not cnt else cnt), ms="%g / 等待 %g" % (iv, dly)))

    def queueDup_(self, sender):
        """复制选中行（同坐标同参数），插在原行之后（action）。"""
        sel = getattr(self, "_queue_sel", None)
        if sel is None or not (0 <= sel < len(getattr(self, "_queue", []))):
            self.show_alert(T("alert_notice"), T("err_select_row"))
            return
        self._queue.insert(sel + 1, dict(self._queue[sel]))
        self._save_queue(self._queue)
        self._reload_queue_table(keep_selection=[sel + 1])
        self.add_log(T("log_q_dup", i=sel + 1))

    def queueDel_(self, sender):
        """删除所有选中行；没选中则删末项（action）。"""
        if not getattr(self, "_queue", None):
            return
        rows = self._selected_rows()
        if rows:
            first = rows[0]
            for i in sorted(rows, reverse=True):
                if 0 <= i < len(self._queue):
                    self._queue.pop(i)
            self._save_queue(self._queue)
            self._reload_queue_table()
            if len(rows) == 1:
                self.add_log(T("log_queue_del_row", i=first + 1))
            else:
                self.add_log(T("log_q_batch_del", n=len(rows)))
            return
        self._queue.pop()
        self._queue_sel = None
        self._save_queue(self._queue)
        self._reload_queue_table()
        self.add_log(T("log_queue_del"))

    def queueClear_(self, sender):
        """清空队列（action）。"""
        self._queue = []
        self._queue_sel = None
        self._save_queue([])
        self._reload_queue_table()
        self.add_log(T("log_queue_clear"))

    # ---------- 连采（连续记录真实点击） ----------
    def record_(self, sender):
        """连采按钮（action）：开始 / 结束。"""
        if getattr(self, "recording", False):
            self._stop_recording("button")
        else:
            self._start_recording()

    @objc.python_method
    def _start_recording(self):
        if getattr(self, "recording", False):
            return
        if not HAVE_QUARTZ:
            self.show_alert(T("alert_error"), T("msg_noquartz"))
            return
        if self.worker is not None and self.worker.running:
            self.show_alert(T("alert_notice"), T("msg_already"))
            return
        if len(getattr(self, "_queue", [])) >= 200:
            self.show_alert(T("alert_notice"), T("err_count", n=200))
            return
        self.recording = True
        self._rec_points = []
        self._rec_last_ts = None
        try:
            self._rec_mon = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSLeftMouseDownMask, self._rec_handler)
        except Exception:
            self._rec_mon = None
        if self._rec_mon is None:
            self.recording = False
            self.show_alert(T("alert_perm"), T("msg_perm"))
            return
        self.btn_rec.setTitle_(T("btn_rec_stop"))
        self.btn_pick.setEnabled_(False)
        self.btn_test_pos.setEnabled_(False)
        self.btn_start.setEnabled_(False)
        self.btn_test.setEnabled_(False)
        self._beep()
        self.lbl_pick.setStringValue_(T("lbl_rec_n", n=0))
        self.add_log(T("log_rec_start"))

    @objc.python_method
    def _stop_recording(self, reason=""):
        if not getattr(self, "recording", False):
            return False
        self.recording = False
        try:
            if self._rec_mon is not None:
                NSEvent.removeMonitor_(self._rec_mon)
        except Exception:
            pass
        self._rec_mon = None
        self.btn_rec.setTitle_(T("btn_rec"))
        self.btn_pick.setEnabled_(True)
        self.btn_test_pos.setEnabled_(True)
        self.btn_start.setEnabled_(True)
        self.btn_test.setEnabled_(True)
        self.lbl_pick.setStringValue_(T("lbl_pick_ready"))
        pts = list(self._rec_points)
        self._rec_points = []
        if not pts:
            self._chime()
            self.add_log(T("log_rec_none"))
            return True
        # 末项间隔沿用前一项（间隔语义=点完再等，最后一击没有下一击可等）
        if len(pts) >= 2:
            pts[-1]["interval_ms"] = pts[-2]["interval_ms"]
        else:
            pts[-1]["interval_ms"] = 100.0
        room = max(0, 200 - len(self._queue))
        pts = pts[:room]
        self._queue.extend(pts)
        self._queue_sel = None
        self._save_queue(self._queue)
        self._refresh_queue_view()
        self._chime()
        self.add_log(T("log_rec_done", n=len(pts), t=len(self._queue)))
        return True

    @objc.python_method
    def _rec_handler(self, event):
        """全局左键监听：只抄录不拦截；落在本窗口内视为回 App → 收工。"""
        if not getattr(self, "recording", False):
            return
        try:
            x, y = self._event_cg_point(event)
        except Exception:
            return
        try:
            sh = float(CGDisplayBounds(CGMainDisplayID()).size.height)
            wf = self.window.frame()
            ax, ay = x, sh - y
            if (wf.origin.x <= ax <= wf.origin.x + wf.size.width and
                    wf.origin.y <= ay <= wf.origin.y + wf.size.height):
                self._stop_recording("window")
                return
        except Exception:
            pass
        now = time.time()
        gap = 0.0 if self._rec_last_ts is None else max(0.0, (now - self._rec_last_ts) * 1000.0)
        if self._rec_points:
            self._rec_points[-1]["interval_ms"] = round(gap, 1)   # 回填上一项的真实等待
        self._rec_points.append({"x": int(x), "y": int(y), "count": 1,
                                 "interval_ms": 100.0, "delay_ms": 0.0})
        self._rec_last_ts = now
        self.lbl_pick.setStringValue_(T("lbl_rec_n", n=len(self._rec_points)))
        if len(self._rec_points) >= 200:
            self._stop_recording("limit")

    @objc.python_method
    def _event_cg_point(self, event):
        try:
            loc = CGEventGetLocation(event.CGEvent())
            return int(loc.x), int(loc.y)
        except Exception:
            b = CGDisplayBounds(CGMainDisplayID())
            ns = event.locationInWindow()
            return int(ns.x), int(float(b.size.height) - ns.y)

    @objc.python_method
    def _hotkey_stop_hook(self):
        """F9 / ⌘⇧X：正在连采时优先结束采集，否则返回 False 交给停止执行逻辑。"""
        if getattr(self, "recording", False):
            self._stop_recording("hotkey")
            return True
        return False

    def applicationDidBecomeActive_(self, notification):
        """回到 App（点 Dock / ⌘Tab）时若在连采，自动收工。"""
        try:
            if getattr(self, "recording", False):
                self._stop_recording("activate")
        except Exception:
            pass

    def toggleMute_(self, sender):
        """静音复选框（action）：勾选后倒计时提示音不再播放。"""
        self.muted = bool(sender.state())
        self.add_log(T("log_mute") if self.muted else T("log_unmute"))

    @objc.python_method
    def _refresh_queue_view(self):
        """兼容旧调用点：统一转到表格刷新。"""
        self._reload_queue_table()

    @objc.python_method
    def _reload_queue_table(self, keep_selection=None):
        """重载表格数据；keep_selection 给行号列表则重新选中它们。"""
        tv = getattr(self, "queue_table", None)
        if tv is None:
            return
        if not getattr(self, "_queue", None):
            self._queue_sel = None
        tv.reloadData()
        try:
            if keep_selection:
                ms = NSMutableIndexSet.alloc().init()
                for i in keep_selection:
                    if 0 <= i < len(self._queue):
                        ms.addIndex_(i)
                tv.selectRowIndexes_byExtendingSelection_(ms, False)
                first = min(keep_selection) if keep_selection else -1
                if 0 <= first < len(self._queue):
                    tv.scrollRowToVisible_(first)
            else:
                tv.deselectAll_(None)
        except Exception:
            pass
        # 拖动后 / 编辑后都刷新一次选中提示
        try:
            n = tv.selectedRowIndexes().count()
            self._queue_sel = tv.selectedRow() if tv.selectedRow() >= 0 else None
            if getattr(self, "lbl_selinfo", None) is not None:
                self.lbl_selinfo.setStringValue_(T("lbl_sel_n", n=n) if n else T("lbl_sel_0"))
            if getattr(self, "btn_bapply", None) is not None:
                self.btn_bapply.setEnabled_(n > 0)
        except Exception:
            pass

    @objc.python_method
    def _save_queue(self, q):
        self._db["current"] = [dict(i) for i in q]
        save_db(self._db)

    @objc.python_method
    def _load_queue(self):
        out = []
        for q in (self._db.get("current") or []):
            if not isinstance(q, dict):
                continue
            out.append({"x": int(q.get("x", 0)), "y": int(q.get("y", 0)),
                        "count": int(q.get("count", 0) or 0),
                        "interval_ms": float(q.get("interval_ms", 0) or 0),
                        "delay_ms": float(q.get("delay_ms", 0) or 0),
                        "name": str(q.get("name", "") or "")})
        return out

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
