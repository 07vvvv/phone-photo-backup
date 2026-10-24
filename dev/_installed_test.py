# -*- coding: utf-8 -*-
"""验证「已安装位置」的 exe：D:\\PhonePhotoBackup\\PhonePhotoBackup.exe

与源码目录 dist/ 里那个产物的区别：这个测的是真正给用户用的那一份，
并额外检查启动后是否又刷出 error.log（视频缩略图 bug 的回归哨兵）。

要点：
  * 一切 win32 调用都声明 argtypes —— 不声明时 ctypes 按 32 位 int 传句柄，
    而 HDC/HBITMAP 是 64 位，值一大就 OverflowError，而且这种崩是概率性的。
  * 关闭走 WM_CLOSE（点 X 那条路），这样才轮到 PyInstaller 清理解包目录；
    强杀测不出 "Failed to remove temporary directory" 那个弹框。
  * 全程 try/finally：中途哪怕自己崩了，也不能把 exe 留在那儿跑。
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import POINTER, byref, c_int, c_void_p, wintypes

from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import shellthumb  # noqa: E402

APP_DIR = r"D:\PhonePhotoBackup"
EXE = os.path.join(APP_DIR, "PhonePhotoBackup.exe")
SHOT = os.path.join(HERE, "_shot_installed.png")
SHOT_WIN = os.path.join(HERE, "_shot_installed_win.png")
SHOT_BAR = os.path.join(HERE, "_shot_titlebar.png")

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass

user32 = ctypes.windll.user32
gdi = ctypes.windll.gdi32

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, POINTER(wintypes.RECT)]
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = c_int
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, c_void_p, c_void_p]
gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi.CreateCompatibleDC.restype = wintypes.HDC
gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, c_int, c_int]
gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi.DeleteObject.argtypes = [wintypes.HANDLE]
gdi.DeleteDC.argtypes = [wintypes.HDC]


def log_size():
    p = os.path.join(APP_DIR, "error.log")
    return os.path.getsize(p) if os.path.isfile(p) else 0


def grab_window(hwnd, rect):
    """PrintWindow 抓窗口自己的画面。别的播放器窗口也是 TOPMOST 时，屏幕截图会被盖住。"""
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    try:
        gdi.SelectObject(mem, bmp)
        user32.PrintWindow(hwnd, mem, 2)          # PW_RENDERFULLCONTENT
        return shellthumb._to_pil(bmp)            # 借用它的 HBITMAP → PIL
    finally:
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


def main():
    log_before = log_size()
    proc = subprocess.Popen([EXE], cwd=APP_DIR)
    hwnd = None
    try:
        time.sleep(12)
        print("exe 存活:", proc.poll() is None)

        hwnd = user32.FindWindowW(None, "手机照片备份")
        print("找到窗口:", bool(hwnd))
        if hwnd:
            user32.ShowWindow(hwnd, 9)
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)   # TOPMOST
            user32.SetForegroundWindow(hwnd)
            time.sleep(1.5)

            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, byref(rect))
            img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
            img.save(SHOT)
            print("截图:", img.size, "->", SHOT)

            img2 = grab_window(hwnd, rect)
            if img2:
                img2.save(SHOT_WIN)
                print("窗口自绘:", img2.size, "->", SHOT_WIN)
            else:
                print("窗口自绘: 失败")

            if rect.bottom - rect.top > 40:
                # 标题栏单独存一份放大图，用来看窗口图标
                img.crop((0, 0, 320, 32)).resize((960, 96)).save(SHOT_BAR)
                print("标题栏 ->", SHOT_BAR)
    finally:
        # 不管上面怎么炸，都要把 exe 正常关掉，别留个进程在后台跑
        if hwnd:
            user32.PostMessageW(hwnd, 0x0010, None, None)     # WM_CLOSE
        for _ in range(40):
            time.sleep(0.5)
            if proc.poll() is not None:
                break
        print("正常退出:", proc.poll() is not None)
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    time.sleep(1)
    leftover = sorted(d for d in os.listdir(APP_DIR) if d.startswith("_MEI"))
    print("退出后残留解包目录:", leftover or "无")
    time.sleep(1)
    warn = user32.FindWindowW(None, "Warning")
    print("临时目录清理警告框:", "出现了（清理失败）" if warn else "没有（干净）")

    log_after = log_size()
    print(f"error.log 增量: {log_after - log_before} B"
          f"  ({'干净' if log_after == log_before else '有新错误！'})")
    print("目录内容:", sorted(os.listdir(APP_DIR)))
    print("adb 位置:", os.path.join(APP_DIR, ".adb", "adb.exe"),
          "存在" if os.path.isfile(os.path.join(APP_DIR, ".adb", "adb.exe")) else "缺失")


if __name__ == "__main__":
    main()
