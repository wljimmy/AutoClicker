# 发布目录规范（releases/）

## 目录结构

```
releases/
├── AutoClicker.app                    ← 始终是最新版本（直接拷一份，最省事、不会失效）
├── v0.4.0-alpha/                      ← 每个版本一个文件夹，永久保留
│   ├── AutoClicker.app
│   ├── AutoClicker-v0.4.0-alpha.zip   ← 分发包（发人/备份/上传 Release 用这个）
│   └── RELEASE_NOTES.md               ← 该版变更与验证记录
└── v0.5.0/                            ← 下一版
```

## 规则

1. **一版一目录**：版本号即目录名，与 `VERSION` 常量、`CFBundleShortVersionString` 三处必须一致。
2. **根目录只放最新**：`releases/AutoClicker.app` 每次发版覆盖为最新构建，历史只能从版本子目录取。
3. **每个版本三件套齐**：`AutoClicker.app` + `AutoClicker-<版本>.zip` + `RELEASE_NOTES.md`，缺一不算发布。
4. **变更记录同步**：根目录 `CHANGELOG.md` 追加一条，`RELEASE_NOTES.md` 写本版详情。
5. **归档顺序**：打包 → `codesign --force --deep --sign -` 重签并 `codesign -v` 校验 → `ditto` 复制（保留签名与扩展属性）→ `ditto -c -k --keepParent` 打 zip → 写 notes。
6. **二进制不入 git**：`releases/` 已加入 `.gitignore`；源码与文本记录入 git，App/zip 走本地归档或 GitHub Release。

## 新增一个版本的命令模板

```bash
cd "/Users/wanglin/工作_本地/豆包工作/自动点击器"
VER="0.5.0"                     # 改成新版本号
python3 -m PyInstaller --noconfirm --clean AutoClicker.spec
codesign --force --deep --sign - dist/AutoClicker.app
codesign -v dist/AutoClicker.app
mkdir -p "releases/v$VER"
ditto dist/AutoClicker.app "releases/v$VER/AutoClicker.app"
ditto -c -k --sequesterRsrc --keepParent dist/AutoClicker.app "releases/v$VER/AutoClicker-v$VER.zip"
rm -rf releases/AutoClicker.app && ditto dist/AutoClicker.app releases/AutoClicker.app
```

然后手动补 `RELEASE_NOTES.md` 与 `CHANGELOG.md`。
