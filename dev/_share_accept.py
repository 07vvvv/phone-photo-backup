# -*- coding: utf-8 -*-
"""开发侧验收：把分享包当成「别人刚拿到」的样子跑一遍。

解压 → 直接双击（目录里没有 config.json）→ 预期：
  * 主窗口画出来
  * 目录选择对话框弹着、在等用户选目录（判据：进程活着 + config.json 还没写）
  * 强杀之后自己清干净，不留进程和临时目录

注：PyInstaller 单文件 exe 会先起一个 bootloader 父进程再拉一个子进程跑 Python，
窗口属于子进程 —— 所以进程/窗口都要按「按名字查出来的全部 pid」来算。
"""
import ctypes
import os
import shutil
import subprocess
import sys
import time
import zipfile
from ctypes import wintypes

ZIP = r"D:\deepseek\run\photo-tool\dist\手机照片备份-分享包.zip"
WORK = r"D:\deepseek\run\_sharecheck"
EXENAME = "PhonePhotoBackup.exe"

u32 = ctypes.windll.user32
u32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
u32.EnumWindows.restype = wintypes.BOOL
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
u32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]

checks = []


def ok(label, cond, extra=""):
    checks.append((label, bool(cond), extra))


def pids():
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXENAME}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, errors="replace").stdout
    got = set()
    for line in out.splitlines():
        parts = [x.strip().strip('"') for x in line.split(",")]
        if len(parts) >= 2 and parts[0].lower() == EXENAME.lower() and parts[1].isdigit():
            got.add(int(parts[1]))
    return got


def windows_of(want):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _l):
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value in want:
            n = u32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(80)
            u32.GetClassNameW(hwnd, cls, 80)
            found.append((cls.value, buf.value, bool(u32.IsWindowVisible(hwnd))))
        return True

    u32.EnumWindows(cb, 0)
    return found


def kill(want):
    for pid in sorted(want):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True, errors="replace")


def main():
    before = pids()
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()
        z.extractall(WORK)
    app_dir = os.path.join(WORK, "PhonePhotoBackup")
    exe = os.path.join(app_dir, EXENAME)
    cfg_file = os.path.join(app_dir, "config.json")

    ok("解压出来就两样东西：exe + 使用说明",
       sorted(os.listdir(app_dir)) == sorted([EXENAME, "使用说明.txt"]),
       str(os.listdir(app_dir)))
    ok("包内没有任何本机使用数据（config.json / error.log / .adb …）",
       not any(k in n for n in names
               for k in ("config.json", "error.log", ".adb", ".pulltmp", ".thumbcache", "_shot")))

    proc = subprocess.Popen([exe], cwd=app_dir)
    try:
        wins = []
        for _ in range(30):                     # 最多等 9 秒
            time.sleep(0.3)
            wins = windows_of(pids() - before)
            if any("手机照片备份" in t for _c, t, _v in wins):
                break
        time.sleep(2.0)                         # 再给两秒，看它是不是停在对话框上
        alive = pids() - before
        wins = windows_of(alive)
        seen = [f"{c}|{t}" for c, t, _v in wins]
        ok("exe 起来了（没闪退）", bool(alive), f"pids={sorted(alive)}")
        ok("主窗口画出来了", any("手机照片备份" in t for _c, t, _v in wins), str(seen))
        ok("首次运行在等用户选备份目录（config.json 还没写）",
           not os.path.exists(cfg_file),
           "config.json 已经写了 —— 说明没停下来问用户" if os.path.exists(cfg_file) else "")
        ok("没有偷偷把目录选择器跳过（进程还在，没自己退到默认位置收场）",
           proc.poll() is None)
    finally:
        kill(pids() - before)
        time.sleep(1.5)

    ok("测试实例已全部退出", not (pids() - before), str(sorted(pids() - before)))
    for _ in range(5):
        shutil.rmtree(WORK, ignore_errors=True)
        if not os.path.exists(WORK):
            break
        time.sleep(1.0)
    ok("临时目录清干净了（强杀会留下 _MEI，属预期，这里靠 rmtree 兜底）",
       not os.path.exists(WORK))

    for label, passed, extra in checks:
        print(("  PASS  " if passed else "  FAIL  ") + label + (f"   ← {extra}" if extra else ""))
    bad = [l for l, p, _e in checks if not p]
    print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
