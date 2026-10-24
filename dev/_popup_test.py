# -*- coding: utf-8 -*-
"""dev/_popup_test.py — 弹窗关闭行为回归（真鼠标点击）

跑法：python dev\_popup_test.py

覆盖：点空白关掉「更多」/「查看」菜单、点菜单项正常触发、Esc 关闭。
用真鼠标（SetCursorPos + mouse_event）而不是 event_generate —— 后者不走 Tk 的
本地 grab 那条路，测不出真实行为。跑完会把鼠标位置还原。
"""
import ctypes
import importlib.util
import os
import sys
import time
from ctypes import wintypes

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

u32 = ctypes.windll.user32

# 必须声明 argtypes：mouse_event 最后一个参数是 ULONG_PTR（64 位），
# 不声明时 ctypes 按 32 位 int 传，参数错位，调用静默失效 —— 会让人
# 误以为「点击没关掉菜单」，其实鼠标事件压根没注入进去。
u32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u32.SetCursorPos.restype = wintypes.BOOL
u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
u32.GetCursorPos.restype = wintypes.BOOL
u32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                            wintypes.DWORD, ctypes.c_void_p]
u32.mouse_event.restype = None


def cursor():
    pt = wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def click(x, y):
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    u32.mouse_event(0x0002, 0, 0, 0, None)      # LEFTDOWN
    time.sleep(0.04)
    u32.mouse_event(0x0004, 0, 0, 0, None)      # LEFTUP
    time.sleep(0.35)


saved = cursor()

app = mod.App()
app.geometry("1220x800+60+40")
app.attributes("-topmost", True)
app.update()
if getattr(app, "_migrate_job", None):
    app.after_cancel(app._migrate_job)
    app._migrate_job = None
for _ in range(40):
    app.update()
    time.sleep(0.05)

# 事件探针：看真实点击最后落到了哪个控件上（排查时非常有用）
events = []
app.bind_all("<Button-1>",
             lambda e: events.append((str(e.widget), e.x_root, e.y_root)), add="+")


def menus():
    out = []
    for parent in (app, app.lib_picker, app.device_picker, app.more_btn):
        try:
            for w in parent.winfo_children():
                if isinstance(w, theme.PopupMenu) and w.winfo_exists():
                    out.append(w)
        except Exception:  # noqa: BLE001
            pass
    return out


def pump(sec=0.4):
    end = time.time() + sec
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def open_menu(fn):
    """打开菜单，并把菜单也顶到最上层。

    真实使用中主窗口不是 topmost，菜单自然在它之上；测试里主窗口是 topmost，
    不这样做菜单会被主窗口盖住，点击就落不到它身上。
    """
    fn()
    pump(0.4)
    for m in menus():
        try:
            m.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
    pump(0.2)


def canvas_center():
    return (app.canvas.winfo_rootx() + app.canvas.winfo_width() // 2,
            app.canvas.winfo_rooty() + app.canvas.winfo_height() // 2)


checks = []


def ok(label, cond):
    checks.append((label, bool(cond)))


# ---- 0) 先确认鼠标注入真的生效：真点「更多」按钮，菜单该弹出来 ----
btn = app.more_btn
click(btn.winfo_rootx() + btn.winfo_width() // 2,
      btn.winfo_rooty() + btn.winfo_height() // 2)
pump(0.6)
ok("鼠标注入生效（真点「更多」按钮能把菜单点出来）", len(menus()) == 1)
print(f"[注入自检] 真点「更多」按钮 → 菜单数 {len(menus())}")
for m in menus():
    m.close()
pump(0.3)

# ---- 1) 「更多」菜单：点各种空白都要关掉 ----
open_menu(app._open_more)
print(f"\n[更多] 打开后菜单数 {len(menus())}  grab_current={app.grab_current()}")
if menus():
    m = menus()[0]
    print(f"       菜单窗口 {m.winfo_rootx()},{m.winfo_rooty()} "
          f"{m.winfo_width()}x{m.winfo_height()}  映射={m.winfo_ismapped()}")

    x, y = canvas_center()
    events.clear()
    click(x, y)
    pump(0.5)
    print(f"       探针：{events[:3]}")
    ok("点缩略图区中央关掉「更多」", len(menus()) == 0)

    if menus():
        click(app.tree.winfo_rootx() + 60,
              app.tree.winfo_rooty() + app.tree.winfo_height() - 30)
        pump(0.5)
    ok("点时间柱下方空白关掉「更多」", len(menus()) == 0)

    if menus():
        click(app.winfo_rootx() + 400, app.winfo_rooty() + app.winfo_height() - 20)
        pump(0.5)
    ok("点状态栏关掉「更多」", len(menus()) == 0)

    for m in menus():
        m.close()
    pump(0.3)

# ---- 2) 「查看」下拉同上 ----
open_menu(lambda: app.lib_picker._popup(None))
print(f"\n[查看] 打开后菜单数 {len(menus())}")
ok("「查看」下拉能打开", len(menus()) == 1)
if menus():
    x, y = canvas_center()
    click(x, y)
    pump(0.5)
    ok("点缩略图区中央关掉「查看」", len(menus()) == 0)

    if menus():
        click(app.tree.winfo_rootx() + 60,
              app.tree.winfo_rooty() + app.tree.winfo_height() - 30)
        pump(0.5)
    ok("点时间柱空白关掉「查看」", len(menus()) == 0)
    for m in menus():
        m.close()
    pump(0.3)

# ---- 3) 缩略图右键菜单也该能被空白点掉 ----
if app.tile_widgets:
    cell = app.tile_widgets[0].master
    app.tile_menu(type("E", (), {"x_root": cell.winfo_rootx() + 20,
                                 "y_root": cell.winfo_rooty() + 20})(), 0)
    pump(0.5)
    ok("右键菜单能打开", len(menus()) == 1)
    if menus():
        x, y = canvas_center()
        click(x, y)
        pump(0.5)
    ok("点空白关掉右键菜单", len(menus()) == 0)
    for m in menus():
        m.close()

# ---- 4) 点菜单项本身还要能正常触发 ----
open_menu(app._open_more)
if menus():
    m = menus()[0]
    y = m.winfo_rooty() + theme.PopupMenu.TOUCH + theme.PopupMenu.ITEM_H // 2
    click(m.winfo_rootx() + 60, y)
    pump(1.2)
    ok("点菜单项之后菜单关掉", len(menus()) == 0)
    ok("点菜单项真的执行了命令（状态栏有反应）",
       "库内" in app._status_var().get() or "个媒体文件" in app._status_var().get())
    print(f"\n[菜单项] 之后菜单数 {len(menus())}  状态栏={app._status_var().get()!r}")

# ---- 5) Esc ----
open_menu(app._open_more)
if menus():
    menus()[0].event_generate("<Escape>")
    pump(0.5)
    ok("Esc 关掉菜单", len(menus()) == 0)
    for m in menus():
        m.close()

u32.SetCursorPos(saved[0], saved[1])
app.destroy()

print("\n" + "=" * 60)
for label, passed in checks:
    print(("  PASS  " if passed else "  FAIL  ") + label)
print("=" * 60)
bad = [l for l, p in checks if not p]
print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
raise SystemExit(1 if bad else 0)
