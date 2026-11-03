# AutoClicker —— macOS 自动点击器（AppKit 原生界面）

> **Bingo Studio** · wljimmy@hotmail.com
> 本项目由 wljimmy 使用豆包 AI 完成

基于 Quartz CGEvent 的高性能鼠标自动点击工具。替代 cliclick（cliclick 有 20ms 强制间隔，且 `t:` 文本输入每个字符也有强制延时，速度远不如原生 CGEvent）。

> UI 使用 macOS 原生 AppKit（不用 tkinter），规避 Tk 9.0 在 macOS 上控件渲染空白的问题。

## 功能

- ✅ **坐标拾取**：点「📌 拾取坐标(5s)」→ 5 秒倒计时 → 自动读取鼠标位置并填入坐标框；「🎯 测试坐标」单点校验
- ✅ **图形界面（AppKit 原生）**：三区布局（坐标拾取 / 运行设置 / 控制），设置坐标、间隔、时长、次数，实时显示投递次数与速率
- ✅ **高速点击**：Quartz 原生事件，无 cliclick 的 20ms 硬限制（实测投递 159 次/秒）
- ✅ **多层逃生机制**（彻底解决"运行中鼠标被锁、只能重启电脑"的痛点）：
  1. **动一下鼠标即停**（最可靠：游戏抢不走你的物理鼠标）
  2. **全局热键 ⌘Cmd+Shift+X / F9**（NSEvent + Quartz 双通道监听，任一触发即停）
  3. **硬性 60 分钟时长上限**（无论设多少，到点强制停，杜绝锁死）
  4. `stop.flag` 文件兜底（另一终端 `touch stop.flag` 即停）
  5. 界面「■ 停止」按钮 + 预设时长/次数到点自动停
- ✅ **深色/浅色模式自适应**：日志文字颜色跟随系统外观（夜间黑底白字，浅色白底黑字）
- ✅ **防呆输入校验**：时长超 60 分钟、次数超 100 万直接弹窗拒绝

## 文件说明

| 文件 | 说明 |
|---|---|
| `click_clicker.py` | 主程序（UI + 点击引擎 + 逃生机制） |
| `build_mac.sh` | **Mac 一键打包脚本**，产物为可双击运行的 `.app` |
| `run.sh` | 不打包、直接用 python3 运行 |
| `test_logic.py` | 核心逻辑自测（count 精确停止 / duration / 停止响应） |
| `README.md` | 本说明 |
| `.gitignore` | Git 忽略规则（排除 dist/build 等产物） |

## 快速开始

### 方式 A：直接运行（不打包）
```bash
cd /Users/wanglin/工作_本地/豆包工作/自动点击器
python3 -m pip install --user pyobjc-framework-Cocoa pyobjc-framework-Quartz -i https://mirrors.huaweicloud.com/repository/pypi/simple/
bash run.sh
```

### 方式 B：打包成可执行程序 .app（推荐）
```bash
cd /Users/wanglin/工作_本地/豆包工作/自动点击器
bash build_mac.sh
```
完成后双击 `dist/AutoClicker.app` 即可。

## 使用步骤

1. **授权辅助功能**：系统设置 → 隐私与安全性 → 辅助功能 → 勾选 AutoClicker.app（运行中若日志提示无权限，点「▶ 开始」会再自查一次）
2. 打开游戏
3. **拾取坐标**：点「📌 拾取坐标(5s)」→ 5 秒内把鼠标移到游戏目标点位 → 倒计时结束自动填入 X/Y → 点「🎯 测试坐标」确认位置
4. 设置参数：
   - 间隔(ms)：默认 `1`（全速投递）。注意游戏有采样上限（实测约 **49~50 次/秒有效**），间隔再小有效数也不变
   - 按时长(秒)：如 `60`（推荐，跑完自动停）
   - 按次数：如 `10000`
5. 点 **▶ 开始**（或 **测试 10 秒** 先验证）
6. 立即切到游戏窗口（脚本会锁定鼠标开始点击）
7. **随时停止**：动一下鼠标 / 按 ⌘Cmd+Shift+X 或 F9 / 点停止按钮

## 实测参考数据（你的游戏）

- 游戏有效点击上限：约 **49.4 次/秒**（60 秒疯狂投递，游戏只认 2964 次）
- 即：提高投递速度不会让游戏认更多点击，有效上限 ≈ 50 CPS

## 注意事项

- ⚠️ 仅限单机本地测试，禁止用于联机游戏
- 运行中鼠标会被锁定到点击坐标——**想停就动一下鼠标**，这是最保险的脱身方式
- 若点击无效：检查辅助功能授权是否勾选
- 长时间挂机建议：系统设置 → 电池 → 防止 Mac 自动睡眠

## 故障排查

| 问题 | 解决 |
|---|---|
| `No module named 'Quartz'/'AppKit'` | `python3 -m pip install --user pyobjc-framework-Cocoa pyobjc-framework-Quartz`（用华为镜像） |
| 界面空白 | 已用 AppKit 原生 UI 规避 Tk 9.0 bug；若仍空白请重打包 |
| 点击无反应 | 辅助功能授权未勾选，或需在「开始」前授权 |
| 热键无效 | 已用双通道（NSEvent + Quartz）加固；**改用"动一下鼠标"停止** |
| 重打包后要重新授权 | adhoc 签名下代码改动会改签名哈希；已固定 Bundle ID `com.wanglin.autoclicker` 缓解 |
| 首次打开 .app 被拦截 | 系统设置 → 隐私与安全性 → 仍要打开 |

## 打包（build_mac.sh）

- 固定 Bundle ID：`com.wanglin.autoclicker`
- 平台：macOS arm64，adhoc 签名
- 用 `--noconfirm` 自动覆盖旧包，配合固定 Bundle ID 降低重授权频率

## 版权 / License

- **Bingo Studio** · wljimmy@hotmail.com
- 本项目由 wljimmy 使用豆包 AI 完成
- 仅限单机本地测试使用，禁止用于联机游戏或任何违反游戏规则的自动化场景
