#!/usr/bin/env python3
"""把指定版本的 zip / dmg 上传到 GitHub Release（本机没装 gh CLI，走 REST API）。

用法: python3 scripts/publish_release.py 0.5.0
凭据: 复用 git 的 osxkeychain 凭据（只传 protocol/host，不传 username —— key 是账号 id）。
"""
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNER_REPO = "wljimmy/AutoClicker"


def get_token():
    p = subprocess.run(
        ["git", "credential", "fill"],
        input=b"protocol=https\nhost=github.com\n\n",
        capture_output=True, cwd=ROOT,
    )
    for line in p.stdout.decode(errors="ignore").splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    return ""


def api(url, token, method="GET", data=None, ctype="application/json", timeout=120):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    if data is not None:
        req.add_header("Content-Type", ctype)
        req.add_header("Content-Length", str(len(data)))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw.strip() else {}


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/publish_release.py <版本号>")
        return 1
    ver = sys.argv[1]
    tag = "v" + ver
    vdir = os.path.join(ROOT, "releases", tag)
    notes_path = os.path.join(vdir, "RELEASE_NOTES.md")

    assets = [
        (os.path.join(vdir, f"AutoClicker-v{ver}.zip"), "application/zip"),
        (os.path.join(vdir, f"AutoClicker-{ver}.dmg"), "application/x-apple-diskimage"),
    ]
    for path, _ in assets:
        if not os.path.exists(path):
            print("✗ 缺少分发包: %s" % path)
            return 1

    token = get_token()
    if not token:
        print("✗ 取不到 GitHub 凭据（git credential fill 无 password）")
        return 1

    body = open(notes_path, encoding="utf-8").read() if os.path.exists(notes_path) else ""
    prerelease = ("alpha" in ver) or ("beta" in ver)

    try:
        rel = api("https://api.github.com/repos/%s/releases/tags/%s" % (OWNER_REPO, tag), token)
        print("· Release 已存在，复用: %s" % rel["html_url"])
    except Exception:
        payload = json.dumps({
            "tag_name": tag,
            "name": "AutoClicker " + tag,
            "body": body,
            "draft": False,
            "prerelease": prerelease,
        }).encode()
        rel = api("https://api.github.com/repos/%s/releases" % OWNER_REPO, token, "POST", payload)
        print("· Release 已创建: %s" % rel["html_url"])
    rel = api(rel["url"], token, "PATCH", json.dumps({"body": body}).encode())

    existing = {a["name"] for a in rel.get("assets", [])}
    upload_base = rel["upload_url"].split("{")[0]
    for path, ctype in assets:
        name = os.path.basename(path)
        if name in existing:
            print("· 已存在，跳过: %s" % name)
            continue
        blob = open(path, "rb").read()
        asset = api(upload_base + "?name=" + name, token, "POST", blob, ctype)
        print("· 已上传: %s (%d bytes)" % (asset["name"], asset["size"]))

    rel = api(rel["url"], token)
    print("✅ %s" % rel["html_url"])
    for a in rel.get("assets", []):
        print("   %s  %s" % (a["name"], a["browser_download_url"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
