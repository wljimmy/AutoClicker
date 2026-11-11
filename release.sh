#!/usr/bin/env bash
# AutoClicker 一键发版
#   升版号 → 归档旧版 → PyInstaller 打包 → 重签校验 → 归档新版 → zip + DMG → 发布记录 → commit/push/tag → GitHub Release
#
# 用法:
#   ./release.sh 0.5.0              完整发版
#   ./release.sh 0.5.0 --local-only 只本地打包归档，不推送
#   ./release.sh 0.5.0 --dry-run    演练，不改任何文件

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

NEW_VER="${1:-}"
MODE="${2:-publish}"

usage() {
  cat <<'EOF'
用法: ./release.sh <新版本号> [选项]

  ./release.sh 0.5.0                完整发版（打包 + 归档 + push + tag + GitHub Release）
  ./release.sh 0.5.0 --local-only   只做本地打包归档，不推送
  ./release.sh 0.5.0 --dry-run      只打印将要做什么，不改文件

环境变量:
  CODESIGN_IDENTITY="Developer ID Application: xxx (TEAMID)"
      有正式证书时填入即可用开发者证书签名（默认 adhoc "-"）
EOF
}

[ -z "${NEW_VER}" ] && { usage; exit 1; }
echo "${NEW_VER}" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9]+)?$' || {
  echo "✗ 版本号格式不对。示例: 0.5.0 / 0.5.0-beta / 0.5.0-alpha"; exit 1;
}

DRY=0; LOCAL_ONLY=0
case "$MODE" in
  publish|"") ;;
  --local-only) LOCAL_ONLY=1 ;;
  --dry-run) DRY=1 ;;
  *) usage; exit 1 ;;
esac

step() { echo; echo "━━━ $* ━━━"; }
run() { if [ "$DRY" = 1 ]; then echo "  [dry-run] $*"; else "$@"; fi; }

# ────────────────────────────── 0. 前置检查
step "0/9 前置检查"
[ -f click_clicker.py ] || { echo "✗ 不在项目根目录"; exit 1; }
[ -f AutoClicker.spec ] || { echo "✗ 缺少 AutoClicker.spec"; exit 1; }
[ -f assets/AutoClicker.icns ] || { echo "✗ 缺少图标 assets/AutoClicker.icns"; exit 1; }

OLD_VER="$(sed -n 's/^VERSION = "\([^"]*\)".*/\1/p' click_clicker.py | head -1)"
echo "  当前版本: ${OLD_VER}  →  新版本: ${NEW_VER}"
[ "${OLD_VER}" = "${NEW_VER}" ] && { echo "✗ 版本号没变，无需发版"; exit 1; }

if [ "$DRY" = 0 ] && [ -n "$(git status --porcelain)" ]; then
  echo "✗ 工作区有未提交改动，请先 commit 再发版（避免打包物与源码对不上号）："
  git status --short
  exit 1
fi

# ────────────────────────────── 1. 归档旧版
step "1/9 归档旧版本"
if [ -d "releases/v${OLD_VER}" ]; then
  echo "  旧版归档已存在: releases/v${OLD_VER}，跳过"
elif [ -d "releases/AutoClicker.app" ]; then
  echo "  把根目录里的旧版移入 releases/v${OLD_VER} 并补 zip"
  if [ "$DRY" = 1 ]; then
    echo "  [dry-run] mkdir releases/v${OLD_VER}; mv app; ditto zip"
  else
    mkdir -p "releases/v${OLD_VER}"
    mv "releases/AutoClicker.app" "releases/v${OLD_VER}/AutoClicker.app"
    ditto -c -k --sequesterRsrc --keepParent "releases/v${OLD_VER}/AutoClicker.app" \
      "releases/v${OLD_VER}/AutoClicker-v${OLD_VER}.zip"
  fi
else
  echo "  无可归档的旧版（根目录没有 releases/AutoClicker.app）"
fi

# ────────────────────────────── 2. 写版本号
step "2/9 写入版本号（VERSION / spec / README）"
if [ "$DRY" = 1 ]; then
  echo "  [dry-run] python 改写 click_clicker.py、AutoClicker.spec、README.md"
else
python3 - "${NEW_VER}" <<'PY'
import re, sys
nv = sys.argv[1]
semver = nv.split('-')[0]
label = "Alpha 版" if 'alpha' in nv else ("Beta 版" if 'beta' in nv else "正式版")

p = 'click_clicker.py'
s = open(p, encoding='utf-8').read()
s = re.sub(r'^VERSION = "[^"]*"', f'VERSION = "{nv}"', s, count=1, flags=re.M)
open(p, 'w', encoding='utf-8').write(s)

p = 'AutoClicker.spec'
s = open(p, encoding='utf-8').read()
s = re.sub(r"version='[^']*'", f"version='{nv}'", s, count=1)
s = re.sub(r"'CFBundleShortVersionString': '[^']*'", f"'CFBundleShortVersionString': '{nv}'", s, count=1)
s = re.sub(r"'CFBundleVersion': '[^']*'", f"'CFBundleVersion': '{semver}'", s, count=1)
open(p, 'w', encoding='utf-8').write(s)

p = 'README.md'
s = open(p, encoding='utf-8').read()
s = re.sub(r'> \*\*v[^*]*\*\*（[^*]*版）', f'> **v{nv}**（{label}）', s, count=1)
open(p, 'w', encoding='utf-8').write(s)
print("  ✓ 三个文件版本号已写入")
PY
fi

# ────────────────────────────── 3. 打包
step "3/9 PyInstaller 打包"
run python3 -m PyInstaller --noconfirm --clean AutoClicker.spec

# ────────────────────────────── 4. 重签
step "4/9 重签名 + 校验"
SIGN_ID="${CODESIGN_IDENTITY:--}"
echo "  签名身份: $SIGN_ID"
if [ "$DRY" = 1 ]; then
  echo "  [dry-run] codesign --force --deep --sign $SIGN_ID dist/AutoClicker.app; codesign -v"
else
  codesign --force --deep --sign "$SIGN_ID" dist/AutoClicker.app
  codesign -v dist/AutoClicker.app && echo "  ✓ 签名校验通过"
  plutil -p dist/AutoClicker.app/Contents/Info.plist | grep -E "CFBundleShortVersionString|CFBundleIdentifier" | sed 's/^/  /'
fi

# ────────────────────────────── 5. 归档新版
step "5/9 归档新版本 + 生成 zip"
if [ "$DRY" = 1 ]; then
  echo "  [dry-run] ditto → releases/v${NEW_VER}/，再打 zip"
else
  mkdir -p "releases/v${NEW_VER}"
  ditto dist/AutoClicker.app "releases/v${NEW_VER}/AutoClicker.app"
  ditto -c -k --sequesterRsrc --keepParent dist/AutoClicker.app \
    "releases/v${NEW_VER}/AutoClicker-v${NEW_VER}.zip"
  echo "  ✓ releases/v${NEW_VER}/AutoClicker.app + AutoClicker-v${NEW_VER}.zip"
fi

# ────────────────────────────── 6. DMG
step "6/9 生成 DMG 安装包"
if [ "$DRY" = 1 ]; then
  echo "  [dry-run] 组装 /tmp/ac_dmg_stage → hdiutil create"
else
  rm -rf /tmp/ac_dmg_stage && mkdir -p /tmp/ac_dmg_stage
  ditto dist/AutoClicker.app /tmp/ac_dmg_stage/AutoClicker.app
  ln -s /Applications /tmp/ac_dmg_stage/Applications
  cat > "/tmp/ac_dmg_stage/首次打开请先右键.txt" <<'TXT'
AutoClicker 首次打开说明
============================
本 App 未做 Apple 公证，macOS 首次打开会提示"无法验证开发者"。
这是正常现象，不是病毒。两种解法，任选其一：

方法一（推荐）
  在「应用程序」文件夹找到 AutoClicker.app，
  按住 Control 点它（或右键）→ 选「打开」→ 弹窗里再点「打开」。
  只需做一次，之后双击即可。

方法二（终端一条命令）
  xattr -d com.apple.quarantine /Applications/AutoClicker.app

如果系统设置里「隐私与安全性」拦住了，去那里点「仍要打开」。
TXT
  hdiutil create -volname "AutoClicker ${NEW_VER}" -srcfolder /tmp/ac_dmg_stage -ov \
    -format UDZO "releases/v${NEW_VER}/AutoClicker-${NEW_VER}.dmg" >/dev/null
  hdiutil verify "releases/v${NEW_VER}/AutoClicker-${NEW_VER}.dmg" | tail -1 | sed 's/^/  /'
  rm -rf /tmp/ac_dmg_stage
  echo "  ✓ releases/v${NEW_VER}/AutoClicker-${NEW_VER}.dmg"
fi

# ────────────────────────────── 7. 根目录最新版
step "7/9 更新 releases 根目录（最新版）"
if [ "$DRY" = 1 ]; then
  echo "  [dry-run] rm -rf releases/AutoClicker.app; ditto 新包"
else
  rm -rf releases/AutoClicker.app
  ditto dist/AutoClicker.app releases/AutoClicker.app
  codesign -v releases/AutoClicker.app && echo "  ✓ 根目录最新版签名完好"
fi

# ────────────────────────────── 8. 发布记录
step "8/9 发布记录（RELEASE_NOTES / CHANGELOG）"
if [ "$DRY" = 1 ]; then
  echo "  [dry-run] 生成 RELEASE_NOTES.md 模板（若不存在）、CHANGELOG 插入新条目"
else
python3 - "${NEW_VER}" <<'PY'
import datetime, os, sys
nv = sys.argv[1]
today = datetime.date.today().isoformat()
notes = f"releases/v{nv}/RELEASE_NOTES.md"
if not os.path.exists(notes):
    with open(notes, 'w', encoding='utf-8') as f:
        f.write(f"""# AutoClicker v{nv}

- **发布日期**：{today}
- **Bundle ID**：`com.bingostudio.autoclicker`

## 本版变更
- （待补充：改完记得回来编辑本文件，Release 正文取自这里）

## 安装方式
1. 下载 `AutoClicker-{nv}.dmg`，双击挂载
2. 把 `AutoClicker.app` 拖进 Applications 文件夹
3. 首次打开若被系统拦截：右键 → 打开（详见包内「首次打开请先右键.txt」）

## 使用注意
- 每次重新打包后，辅助功能授权需重做（系统设置 → 隐私与安全性 → 辅助功能）
""")
    print("  ✓ 已生成 RELEASE_NOTES.md 模板")
else:
    print("  · RELEASE_NOTES.md 已存在，保持不变")

if os.path.exists('CHANGELOG.md'):
    s = open('CHANGELOG.md', encoding='utf-8').read()
    if f"## v{nv}" not in s:
        entry = f"## v{nv} — {today}\n\n**变更**\n- （待补充）\n\n"
        idx = s.find('## v')
        s = (s[:idx] + entry + s[idx:]) if idx >= 0 else (s.rstrip() + "\n\n" + entry)
        open('CHANGELOG.md', 'w', encoding='utf-8').write(s)
        print("  ✓ CHANGELOG 已插入新条目")
PY
fi

# ────────────────────────────── 9. 版本控制 + 发布
step "9/9 版本控制（commit / push / tag / Release）"
if [ "$DRY" = 1 ]; then
  echo "  [dry-run] git add -A && commit; push; tag v${NEW_VER}; GitHub Release 上传 zip + dmg"
else
  git add -A
  git commit -q -m "发布 v${NEW_VER}：打包归档 + DMG/zip 分发" || echo "  · 没有新增文本变更，跳过 commit"

  if [ "$LOCAL_ONLY" = 1 ]; then
    echo "  --local-only：跳过 push / tag / Release"
    echo "  本地产物: releases/v${NEW_VER}/"
  else
    git push origin main
    if git rev-parse "v${NEW_VER}" >/dev/null 2>&1; then
      echo "  · tag v${NEW_VER} 已存在，跳过创建"
    else
      git tag -a "v${NEW_VER}" -m "AutoClicker ${NEW_VER}"
    fi
    git push origin "v${NEW_VER}"
    python3 scripts/publish_release.py "${NEW_VER}"
  fi
fi

echo
echo "✅ 完成：v${NEW_VER}"
if [ "$LOCAL_ONLY" = 0 ] && [ "$DRY" = 0 ]; then
  echo "   https://github.com/wljimmy/AutoClicker/releases/tag/v${NEW_VER}"
fi
