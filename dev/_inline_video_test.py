# -*- coding: utf-8 -*-
"""验证动态照片的视频能在应用窗口内播放（MFPlay 把画面直接渲染到 Tk 窗口）。

三项都要成立才算成：
  1. MFPlay 能在 Tk 的 canvas 窗口句柄上建起播放器并进入 PLAYING
  2. 窗口里真的出现了画面（截图颜色数远多于纯色底）
  3. 画面在动（隔 0.4 秒抓两帧，像素有差异）—— 「动图」的意义就在这里
"""
import ctypes
import os
import sys
import time

from PIL import ImageChops, ImageGrab, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HERE)
sys.path.insert(0, ROOT_DIR)

import library      # noqa: E402
import mfplay       # noqa: E402

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    pass

SHOT = os.path.join(HERE, "_shot_inline_video.png")
checks = []


def ok(label, cond):
    checks.append((label, bool(cond)))


def find_motion():
    for dirpath, _d, filenames in os.walk(r"D:\PhonePhotos"):
        for fn in filenames:
            if fn.lower().endswith((".jpg", ".jpeg")):
                p = os.path.join(dirpath, fn)
                if library.motion_range(p):
                    return p
    return None


src = find_motion()
if not src:
    print("库里没有动态照片，无法测试")
    raise SystemExit(1)
print("样本:", src)

ex_ok, mp4 = library.extract_motion(src)
ok("从 JPEG 切出内嵌 MP4", ex_ok and os.path.isfile(mp4))
print(f"切出的 MP4: {mp4}  {os.path.getsize(mp4)/1e6:.2f} MB")

import tkinter as tk  # noqa: E402

root = tk.Tk()
root.title("inline video test")
root.geometry("860x560+80+80")
root.attributes("-topmost", True)
cv = tk.Canvas(root, bg="#16161A", highlightthickness=0, bd=0)
cv.pack(fill="both", expand=True)
root.update()
time.sleep(0.3)
root.update()

box = (cv.winfo_rootx(), cv.winfo_rooty(),
       cv.winfo_rootx() + cv.winfo_width(), cv.winfo_rooty() + cv.winfo_height())


def grab():
    root.update()
    return ImageGrab.grab(bbox=box).convert("RGB")


def colors(im):
    return len(im.getcolors(maxcolors=1 << 22) or [])


# 容器窗口的底色必须是深色 —— 之前的「先白屏再播放」就是系统 STATIC 类的白底造成的
host = mfplay.make_host_window(cv.winfo_id())
ok("能建出视频容器窗口", bool(host))
if host:
    ctypes.windll.user32.ShowWindow(host, 5)          # SW_SHOW
    root.update()
    time.sleep(0.3)
    probe = grab()
    # 只看中心区域：边缘可能带上鼠标光标，用它当「最亮像素」会误判
    center = probe.crop((80, 80, probe.width - 80, probe.height - 80)).convert("L")
    stat = ImageStat.Stat(center)
    mean, top = stat.mean[0], stat.extrema[0][1]
    print(f"空容器：中心最亮 {top}，平均亮度 {mean:.1f}")
    ok(f"空容器显示时是深色底、不发白（平均亮度 {mean:.1f}）", mean < 60)
    ctypes.windll.user32.DestroyWindow(host)
    root.update()
    time.sleep(0.2)

before = grab()
print("播放前颜色数:", colors(before), " 窗口:", cv.winfo_width(), "x", cv.winfo_height(),
      " hwnd:", hex(cv.winfo_id()))

vid = None
try:
    vid = mfplay.InlineVideo(mp4, cv.winfo_id())
    ok("MFPlay 在 Tk 窗口上建起播放器", True)
    ok("容器窗口建好时是隐藏的（不会先闪一块空板）", not vid.shown)
except OSError as exc:
    ok(f"MFPlay 在 Tk 窗口上建起播放器（{exc}）", False)

played = False
if vid:
    for _ in range(30):
        root.update()
        time.sleep(0.1)
        if vid.state() == mfplay.STATE_PLAYING:
            played = True
            break
    ok(f"进入 PLAYING（state={vid.state()}）", played)

    vid.show()                     # 实际使用中由 _reveal_video 等出画面后才调
    ok("show() 之后窗口才显示", vid.shown)
    time.sleep(0.6)
    after = grab()
    after.save(SHOT)
    n_before, n_after = colors(before), colors(after)
    print(f"播放后颜色数: {n_after} -> {SHOT}")
    ok(f"窗口里出现画面（颜色 {n_before} → {n_after}）", n_after > n_before * 20)

    # 画面在动
    moved = False
    frames = [after]
    for _ in range(4):
        time.sleep(0.4)
        frames.append(grab())
    for a, b in zip(frames, frames[1:]):
        if ImageChops.difference(a, b).getbbox() is not None:
            moved = True
            break
    ok("相邻两帧像素有差异（画面在动）", moved)

    vid.close()
    vid = None
    time.sleep(0.5)
    back = grab()
    n_back = colors(back)
    print("停止后颜色数:", n_back)
    ok(f"停止后画面消失、回到纯色底（{n_back}）", n_back < max(50, n_after // 10))

root.destroy()

print("=" * 60)
for label, passed in checks:
    print(("  PASS  " if passed else "  FAIL  ") + label)
print("=" * 60)
bad = [l for l, p in checks if not p]
print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
raise SystemExit(1 if bad else 0)
