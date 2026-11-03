#!/bin/bash
# ============================================================
# AutoClicker 一键打包脚本（在 Mac 上运行）
# 用法：终端执行  bash build_mac.sh
# 产物：dist/AutoClicker.app （双击即用的可执行程序，AppKit 原生 UI）
# ============================================================
set -e
cd "$(dirname "$0")"

MIRROR="-i https://mirrors.huaweicloud.com/repository/pypi/simple/"

echo "[1/4] 检查 Python..."
command -v python3 >/dev/null 2>&1 || { echo "❌ 未找到 python3，请先安装 Python3"; exit 1; }
python3 --version

echo "[2/4] 检查/安装依赖 (pyobjc Cocoa + Quartz + PyInstaller)..."
python3 -c "import AppKit, Quartz" >/dev/null 2>&1 || {
    echo "  安装 pyobjc-framework-Cocoa / pyobjc-framework-Quartz ..."
    python3 -m pip install --user $MIRROR pyobjc-framework-Cocoa pyobjc-framework-Quartz
}
python3 -m PyInstaller --version >/dev/null 2>&1 || {
    echo "  安装 pyinstaller ..."
    python3 -m pip install --user $MIRROR pyinstaller
}

echo "[3/4] 校验脚本语法..."
python3 -m py_compile click_clicker.py

echo "[4/4] 打包为 .app ..."
python3 -m PyInstaller --noconfirm --clean --windowed --name AutoClicker click_clicker.py

echo ""
echo "=================================================="
echo "✅ 打包完成：$(pwd)/dist/AutoClicker.app"
echo ""
echo "使用步骤："
echo "  1. 双击 dist/AutoClicker.app 运行"
echo "  2. 首次运行如提示无法打开：系统设置→隐私与安全性→仍要打开"
echo "  3. 到 系统设置→隐私与安全性→辅助功能 勾选 AutoClicker"
echo "  4. 回到界面设置参数，点 ▶ 开始；运行中按 F9 可随时停止"
echo "=================================================="
