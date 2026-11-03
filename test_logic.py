#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AutoClicker 核心逻辑测试（Linux/mac 均可运行，mock 掉鼠标点击）"""
import os
import sys
import time
import queue

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import click_clicker as cc

# ---- mock 鼠标点击 ----
clicks = {"n": 0}
def fake_click(x, y):
    clicks["n"] += 1
cc.mouse_click = fake_click

# 测试1：count 模式，200次，间隔0.5ms → 必须精确停止在200
w = cc.ClickWorker(10, 10, 0.5, "count", 200)
w.start()
w._thread.join(timeout=10)
assert w.count == 200, f"count模式失败: {w.count}"
assert clicks["n"] == 200, f"点击数不一致: {clicks['n']}"
assert not w.running
assert any("结束" in l for l in w.drain_logs()), "缺少结束日志"
print(f"[PASS] count模式：精确停止在 {w.count} 次")

# 测试2：duration 模式，1秒，间隔1ms → 运行约1秒
w2 = cc.ClickWorker(10, 10, 1, "duration", 1)
w2.start()
w2._thread.join(timeout=5)
assert w2.count > 0, "duration模式没有点击"
assert not w2.running
print(f"[PASS] duration模式：运行约1秒，点击 {w2.count} 次")

# 测试3：停止请求及时响应（间隔10秒，请求停止后应在毫秒级退出）
w3 = cc.ClickWorker(10, 10, 10000, "count", 1000000)
w3.start()
time.sleep(0.3)
t0 = time.time()
w3.request_stop()
w3._thread.join(timeout=2)
dt = time.time() - t0
assert not w3.running, "停止请求未生效"
assert dt < 1.0, f"停止响应太慢: {dt}s"
print(f"[PASS] 停止响应：{dt*1000:.0f}ms 内退出")

print("\n=== 全部测试通过 ===")
