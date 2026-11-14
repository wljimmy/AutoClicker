# 变更记录（CHANGELOG）

版本号规则：主版本.次版本.修订版本，预发布追加 `-alpha` / `-beta`。UI 版本号、`CFBundleShortVersionString`、`releases/v<版本>` 目录名三处保持一致。

## v0.5.0 — 2026-11-14

**新增**
- 多点点击队列：`ClickWorker` 由单点循环升级为 points 列表循环，每项带坐标 / 次数 / 项内间隔，整表点完算一轮再循环。
- 队列 UI（④ 多点队列）：加入队列、删末项、清空，持久化到 `multi_points.json`；模式下拉新增「多点队列(按时长)」。
- 提示音静音：复选框一键屏蔽 Tink / Glass。
- 全程倒计时状态栏：已过 / 剩余 / 总计，`HH:MM:SS`；暂停时冻结。
- 日志统一 `[HH:MM:SS]` 前缀，开始 / 暂停 / 恢复 / 结束全部留痕。

**修复**
- `btn_stop` i18n key 重复导致控制区按钮显示「中断」→ 拆分独立 key。
- 语言切换后模式下拉丢失第三项与选中态 → 补齐并保留选中。
- 英文下部分按钮文字截断 → 重排宽度。

## v0.4.0-alpha — 2026-11-11（Alpha 首个版本）

**新增**
- 坐标拾取倒计时提示音：开始「嘀」→ 每秒「嘀」（`Tink`）→ 归零「叮」（`Glass`）。
- 按 Bingo Studio 工作室视觉定制的应用图标（青蓝渐变 + 白描边鼠标与点击波纹），已打包进 App。

**修复**
- 关闭窗口后进程不退出、第二次启动失效 → 双层退出保险（`applicationShouldTerminateAfterLastWindowClosed_` + `windowWillClose_` 中 `NSApp.terminate`）。
- 倒计时「哔」声不响 → 弃用 `NSSound.beep()`，改播系统音效文件。
- `Info.plist` 版本号恒为 0.0.0 → 打包时写入 `CFBundleShortVersionString` / `CFBundleVersion`。

**其他**
- 打包 spec 纳入版本库；README 更新版本与功能说明。

## v0.3.0 — 2026-11-03

- 坐标保存与命名（`saved_coords.json`，同名覆盖，可回填）。
- 版本号显示 + 「关于」面板（版权三行、GitHub 仓库链接）。
- Bundle ID 改为工作室名 `com.bingostudio.autoclicker`，避免暴露个人姓名。
- 界面双语参数化（中/英下拉切换）、窗口固定尺寸、说明文字自动换行与排版。

## v0.2.x — 2026-10 下旬

- 鼠标晃动打断：改检测绝对坐标 + 顶点反转算法（2 秒内 3 次、相邻顶点 ≥200px）。
- 暂停→确认弹窗（继续/中断）状态机；鼠标静止 0.5 秒自动恢复。
- 语言下拉菜单可选项被置灰修复（`autoenablesItems`）。

## v0.1.0 — 2026-10 上旬

- 初版：Quartz CGEvent 高速点击、坐标拾取倒计时、时长/次数模式、多层逃生（热键 F9/⌘⇧X、鼠标移动、60 分钟上限、`stop.flag`）。
