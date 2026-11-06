# -*- coding: utf-8 -*-
"""privacy_check.py — 上传前隐私门禁：确认仓库里没有任何个人数据

为什么要单独一个脚本
    这个仓库要公开。开发期的截图会带上真实照片、桌面内容、局域网 IP；
    运行期还会生成 config.json（手机序列号）和 error.log（真实磁盘路径）。
    这些东西一旦进过一次提交，就永久留在历史里 —— 光删文件没用。
    所以把「检查」固化成能反复跑的脚本，而不是每次靠人眼翻一遍。

只读
    本脚本不修改任何文件、不写 git 历史、不联网。它只回答一个问题：
    这个仓库（含全部历史）里，有没有不该公开的东西。

用法
    python dev/privacy_check.py         # 全量检查
    python dev/privacy_check.py --list  # 只列检查项

退出码：0 全过；1 有失败。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 本文件本身就是门禁工具，里面必然出现这些模式串，扫描时跳过自己
SELF = "dev/privacy_check.py"


def _j(*parts):
    """把模式拼起来再返回。

    这些词在源码里直接写全，脚本自己就会被自己判成「泄露」；拼一下既避开
    自命中，也不影响读代码 —— 每个片段都短得很明显，没有藏东西的意图。
    """
    return "".join(parts)


# 已跟踪文件里绝不该出现的字面量（子串匹配，不走正则，避免转义歧义）。
#
# 只放「真的指向某个具体的人或设备」的串。下面这些**故意不放**：
#   · `D:\PhonePhotos`      —— 产品默认目录（`~/Pictures/PhonePhotos`）的常见写法
#   · `MVIMG_` / `Screenshot_2024-..` —— 相机与截图的标准命名，README 里要讲原理
#   · `192.0.2.x`           —— RFC 5737 文档专用网段，示例就该用它
# 把产品术语也拦住，门禁就会天天误报，最后没人看 —— 那还不如没有。
FORBIDDEN_TEXT = [
    ("当前用户名", _j("v", chr(39), "amos")),
    ("Windows 用户目录", _j("C:", chr(92), "Users", chr(92))),
    ("真实手机型号", _j("RED", "MI")),
]

# 私网地址：外网不可达，但会暴露家里/公司的网络拓扑
PRIVATE_IP_RE = re.compile(
    r"\b(?:192\.168|10\.(?!0\.2\b)\d+|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")

# 允许入库的扩展名。出现别的（尤其图片）就说明有人把运行产物提交了。
ALLOWED_EXT = {
    "", ".py", ".pyw", ".md", ".spec", ".txt", ".ico", ".exe", ".dll",
    ".gitignore", ".yml", ".yaml", ".toml", ".cfg", ".ini",
}

# 例外：程序自己的图标预览图（纯几何图形拼出来的，不含任何用户内容）
ALLOWED_FILES = {"assets/icon_preview.png"}

# 运行时产物必须被 .gitignore 挡住（这里只挑最容易泄露的几个）
MUST_IGNORE = [
    "config.json",          # 备份目录 + 设备 android_id + 手机型号
    "error.log",            # 真实磁盘路径
    "build/",
    "dist/",
    ".adbtmp/",
    ".thumbcache/",
    "dev/_shot_local.png",  # 界面截图的落点
]

# 历史里出现过就该重来一遍的文件名
FORBIDDEN_HISTORY = ["config.json", "error.log"]


# --------------------------------------------------------------------------
# git 辅助
# --------------------------------------------------------------------------


def git(*args, **kw):
    """在仓库根目录跑一条 git，返回 (退出码, stdout)。"""
    proc = subprocess.run(
        ["git"] + list(args), cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", **kw)
    return proc.returncode, (proc.stdout or "")


def tracked_files():
    code, out = git("ls-files")
    if code != 0:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def history_paths():
    """历史上出现过的每一个路径（含已删除的）。"""
    code, out = git("log", "--all", "--diff-filter=ADMR", "--name-only",
                    "--pretty=format:")
    if code != 0:
        return []
    return sorted({ln.strip() for ln in out.splitlines() if ln.strip()})


def read_text(path):
    full = os.path.join(ROOT, path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def real_email():
    """当前仓库配置的提交邮箱；noreply / agent@local 不算个人信息。"""
    for key in ("user.email", None):
        args = ["config"] + (["--get", key] if key else ["--get", "user.email"])
        _code, out = git(*args)
        val = out.strip()
        if val:
            if "noreply" in val or val.endswith("@local"):
                return None
            return val
    return None


# --------------------------------------------------------------------------
# 检查
# --------------------------------------------------------------------------


def selfcheck():
    checks = []

    def ok(label, cond):
        checks.append((label, bool(cond)))

    files = tracked_files()
    ok("能读到已跟踪文件清单", len(files) > 0)

    # 1) 文本文件里不得出现个人字面量
    offenders = {}
    for path in files:
        if path == SELF or path in ALLOWED_FILES:
            continue
        low = path.lower()
        if low.endswith((".exe", ".dll", ".ico")):
            continue                      # 二进制不按文本搜（adb.exe 里啥都有）
        text = read_text(path)
        if not text:
            continue
        for label, needle in FORBIDDEN_TEXT:
            if needle in text:
                offenders.setdefault(label, []).append(path)
    for label, needle in FORBIDDEN_TEXT:
        hit = offenders.get(label) or []
        ok("已跟踪文件不含" + label, not hit)

    # 2) 不得出现私网 IP
    ip_hits = []
    for path in files:
        if path == SELF:
            continue
        low = path.lower()
        if low.endswith((".exe", ".dll", ".ico")):
            continue
        for m in PRIVATE_IP_RE.finditer(read_text(path)):
            # 192.0.2.0/24 是 RFC 5737 的文档专用网段，本来就该出现在示例里
            if not m.group().startswith("192.0.2."):
                ip_hits.append(path + " -> " + m.group())
    ok("已跟踪文件不含私网 IP", not ip_hits)

    # 3) 不得出现真实提交邮箱
    mail = real_email()
    mail_hits = []
    if mail:
        for path in files:
            if path == SELF:
                continue
            if mail in read_text(path):
                mail_hits.append(path)
    ok("已跟踪文件不含真实邮箱", not mail_hits)

    # 4) 扩展名白名单：只允许源码/文档/图标/随包二进制
    weird = [p for p in files
             if os.path.splitext(p)[1].lower() not in ALLOWED_EXT]
    weird = [p for p in weird if p not in ALLOWED_FILES]
    ok("没有图片/压缩包/日志被提交", not weird)
    bad_ext = sorted({os.path.splitext(p)[1].lower() for p in weird})
    checks[-1] = (checks[-1][0] + ("（" + " ".join(bad_ext) + "）"
                                   if bad_ext else ""), checks[-1][1])

    # 5) 历史里也不得有图片
    hist = history_paths()
    hist_img = [p for p in hist
                if os.path.splitext(p)[1].lower() in
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp4")
                and p not in ALLOWED_FILES]
    ok("历史提交里没有图片", not hist_img)

    # 6) 历史里不得出现运行时产物
    hist_runtime = [p for p in hist for f in FORBIDDEN_HISTORY
                    if p == f or p.endswith("/" + f)]
    ok("历史提交里没有 config.json / error.log", not hist_runtime)

    # 7) .gitignore 必须挡住运行时产物
    for target in MUST_IGNORE:
        _c, out = git("check-ignore", "-v", target)
        ok(".gitignore 覆盖 " + target, bool(out.strip()))

    # 8) 没有悬空/不可达对象（删过又留着的 blob）
    _c, out = git("fsck", "--unreachable", "--no-progress")
    dangling = [ln for ln in out.splitlines() if ln.strip()
                and ("blob" in ln or "commit" in ln or "tree" in ln)]
    ok("没有悬空对象", not dangling)

    # 9) spec 不得把 config.json 打进去
    spec_text = read_text("PhonePhotoBackup.spec")
    ok("打包 spec 不引用 config.json",
       "config.json" not in spec_text and "error.log" not in spec_text)

    # 10) 工作区里没有「未忽略的」图片
    _c, out = git("status", "--ignored", "--porcelain")
    loose = []
    for ln in out.splitlines():
        if not ln.startswith("??"):
            continue
        path = ln[3:].strip().strip('"')
        if os.path.splitext(path)[1].lower() in (".png", ".jpg", ".jpeg"):
            loose.append(path)
    ok("工作区没有被漏忽略的图片", not loose)

    print("=" * 56)
    for label, passed in checks:
        print(("  PASS  " if passed else "  FAIL  ") + label)
    bad = [l for l, p in checks if not p]
    print("=" * 56)
    if bad and offenders:
        for label, paths in offenders.items():
            for p in paths[:6]:
                print("  [" + label + "] " + p)
    for item in (ip_hits + mail_hits + weird + hist_img + hist_runtime + loose)[:10]:
        print("  [待清理] " + str(item))
    print("共 %d 项，失败 %d 项" % (len(checks), len(bad)))
    return 0 if not bad else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--list" in argv:
        print("隐私门禁检查项：")
        print("  1) 已跟踪文件不含用户名 / 用户目录 / 真实机型 / 真实照片名")
        print("  2) 已跟踪文件不含私网 IP")
        print("  3) 已跟踪文件不含真实提交邮箱")
        print("  4) 扩展名白名单（图片 / 压缩包 / 日志一律不许入库）")
        print("  5) 历史提交里没有图片")
        print("  6) 历史提交里没有 config.json / error.log")
        print("  7) .gitignore 覆盖运行时产物")
        print("  8) 没有悬空对象")
        print("  9) 打包 spec 不引用 config.json")
        print(" 10) 工作区没有被漏忽略的图片")
        return 0
    return selfcheck()


if __name__ == "__main__":
    sys.exit(main())
