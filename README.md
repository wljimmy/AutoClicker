# AutoClicker —— macOS 自动点击器（AppKit 原生界面）

基于 Quartz CGEvent 的高性能鼠标自动点击工具，替代 cliclick（cliclick 有 20ms 强制间隔，且 `t:` 文本输入每个字符也有强制延时，速度远不如原生 CGEvent）。

> UI 使用 macOS 原生 AppKit（不再用 tkinter），规避 Tk 9.0 在 macOS 上控件渲染空白的问题。

## 功能
- ✅ 图形界面（AppKit 原生）：设置坐标 / 间隔 / 时长 / 次数，实时显示投递次数与速率
- ✅ 高速点击：Quartz 原生事件，无 cliclick 的 20ms 硬限制（实测投递 159 次/秒）
- ✅ 三种停止机制（解决"运行中鼠标被锁、无法操作电脑"的痛点）：
  1. **全局热键 F9**：运行中鼠标被锁也能用键盘强制停止（推荐）
  2. **预设时长/次数**：到达自动停止，鼠标自动释放
  3. 界面"■ 停止"按钮
- ✅ 测试按钮：一键跑 10 秒，快速验证游戏实际有效点击

## 文件说明
| 文件 | 说明 |
|---|---|
| `click_clicker.py` | 主程序（UI + 点击引擎） |
| `build_mac.sh` | **Mac 一键打包脚本**，产物为可双击运行的 `.app` |
| `run.sh` | 不打包、直接用 python3 运行 |
| `test_logic.py` | 核心逻辑自测（Linux/Mac 均可跑） |

## 快速开始

### 方式 A：直接运行（不打包）
```bash
cd ~/Downloads/AutoClicker
python3 -m pip install --user pyobjc-framework-Cocoa pyobjc-framework-Quartz -i https://mirrors.huaweicloud.com/repository/pypi/simple/
bash run.sh
```

### 方式 B：打包成可执行程序 .app（推荐）
```bash
cd ~/Downloads/AutoClicker
bash build_mac.sh
```
完成后双击 `dist/AutoClicker.app` 即可。

## 使用步骤
1. **授权辅助功能**：系统设置 → 隐私与安全性 → 辅助功能 → 勾选 Terminal（或 AutoClicker.app）
2. 打开游戏，把鼠标手动放到目标点击位置（脚本不会移动光标，只在当前坐标点击）
3. 界面设置：
   - 坐标 X / Y：默认 `2852, 545`（按你的游戏实际位置修改）
   - 间隔(ms)：默认 `1`（全速投递）。注意游戏有采样上限（实测约 **49~50 次/秒有效**），间隔再小有效数也不变
   - 按时长(秒)：如 `60`（推荐，跑完自动停）
   - 按次数：如 `10000`
4. 点 **▶ 开始**（或 **测试 10 秒** 先验证）
5. 立即切到游戏窗口（脚本会锁定鼠标开始点击）
6. **停止**：按 **F9** 随时强制停止；或等时长/次数到点自动停

## 实测参考数据（你的游戏）
- 游戏有效点击上限：约 **49.4 次/秒**（60 秒投递疯狂事件，游戏只认 2964 次）
- 即：提高投递间隔/速度，不会让游戏认更多点击，有效上限 ≈ 50 CPS

## 注意事项
- ⚠️ 仅限单机本地测试，禁止用于联机游戏
- 运行中鼠标会被锁定到点击坐标，不要手动移动鼠标
- 若点击无效：检查辅助功能授权是否勾选
- 长时间挂机建议：系统设置 → 电池 → 防止 Mac 自动睡眠

## 故障排查
| 问题 | 解决 |
|---|---|
| `No module named 'Quartz'/'AppKit'` | `python3 -m pip install --user pyobjc-framework-Cocoa pyobjc-framework-Quartz`（用华为镜像） |
| 界面空白（Tk 9.0 bug） | 已改为 AppKit 原生 UI，无需 tkinter；若仍空白请重打包 |
| 点击无反应 | 辅助功能授权未勾选 |
| 首次打开 .app 被拦截 | 系统设置→隐私与安全性→仍要打开 |
