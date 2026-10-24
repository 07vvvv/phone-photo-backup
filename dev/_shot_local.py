# -*- coding: utf-8 -*-
"""dev/_shot_local.py — 给「本机文件」出实机截图（用真实的用户目录）

跑法：python dev\_shot_local.py
产物：dev\_shot_local.png / _shot_local_menu.png / _shot_local_all.png
"""
import ctypes
import importlib.util
import os
import sys
import time

from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HERE)
sys.path.insert(0, ROOT_DIR)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    pass

import theme  # noqa: E402

spec = importlib.util.spec_from_file_location("appmod", os.path.join(ROOT_DIR, "app.pyw"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
library = mod.library

app = mod.App()
app.geometry("1220x800+40+20")
app.attributes("-topmost", True)
app.update()
if getattr(app, "_migrate_job", None):
    app.after_cancel(app._migrate_job)
    app._migrate_job = None
for _ in range(40):
    app.update()
    time.sleep(0.05)


def shot(name):
    app.update()
    time.sleep(0.5)
    app.update()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    box = (x - 8, y - 31, x + w + 8, y + h + 8)
    ImageGrab.grab(bbox=box).save(os.path.join(HERE, name))
    print("已保存", name)


def settle(seconds=6.0, want=None):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.05)
        if want is not None and want():
            return
    return


t0 = time.time()
app.lib_picker._pick(library.LOCAL_DEVICE)
scan_dt = time.time() - t0
print(f"切到「本机文件」耗时 {scan_dt:.2f}s，{len(app.shown)} 个媒体文件，"
      f"时间柱 {list(app.tree_data)}")

settle(25, lambda: sum(1 for l in app.tile_widgets if l._shown) >= 20)
shot("_shot_local.png")

# 合并视图
app.lib_picker._pick(mod.ALL_WITH_LOCAL)
settle(20, lambda: sum(1 for l in app.tile_widgets if l._shown) >= 20)
print(f"合并视图 {len(app.shown)} 个，时间柱 {list(app.tree_data)}")
shot("_shot_local_all.png")


# 「查看」下拉里三个入口 —— 菜单要跑在真主循环里才画得出来
def open_menu():
    app.lib_picker._popup(None)
    app.after(700, grab_menu)


def grab_menu():
    shot("_shot_local_menu.png")
    for w in app.lib_picker.winfo_children():
        if isinstance(w, theme.PopupMenu):
            w.close()
    app.after(300, app.destroy)


app.after(300, open_menu)
app.mainloop()
