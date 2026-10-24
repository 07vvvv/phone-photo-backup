# -*- coding: utf-8 -*-
"""dev/_ui_test.py — 界面细节回归：自绘菜单 / 切组不白屏 / 删除立刻消失

跑法：python dev\\_ui_test.py

全程在临时库上跑，不动真库；「移到回收站」也换成真的删临时文件，不往回收站扔垃圾。
"""
import ctypes
import importlib.util
import os
import shutil
import sys
import time
from types import SimpleNamespace

import tkinter.font as tkfont
from PIL import Image, ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HERE)
SHOT_MENU = os.path.join(HERE, "_shot_menu.png")
SHOT_MORE = os.path.join(HERE, "_shot_more.png")
sys.path.insert(0, ROOT_DIR)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    pass

import theme

spec = importlib.util.spec_from_file_location("appmod", os.path.join(ROOT_DIR, "app.pyw"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

checks = []


def ok(label, cond):
    checks.append((label, bool(cond)))


APP_SRC = open(os.path.join(ROOT_DIR, "app.pyw"), encoding="utf-8").read()
THEME_SRC = open(os.path.join(ROOT_DIR, "theme.py"), encoding="utf-8").read()

# --------------------------------------------------------------------------
# 1) 三个菜单都换成了自绘的
# --------------------------------------------------------------------------

ok("app.pyw 里已经没有一个原生 tk.Menu", "tk.Menu(" not in APP_SRC)
ok("「更多」和缩略图右键都走自绘菜单", APP_SRC.count("PopupMenu(") >= 2)
ok("「查看/连接」下拉也走自绘菜单", "PopupMenu(self" in THEME_SRC.split("class DevicePicker")[1])

# --------------------------------------------------------------------------
# 1b) 备份目录的护栏 + config 原子写
# --------------------------------------------------------------------------

ok("盘根不能当备份目录", not mod.root_usable("D:\\"))
ok("用户主目录不能当备份目录", not mod.root_usable(os.path.expanduser("~")))
ok("Windows 目录不能当备份目录",
   not mod.root_usable(os.environ.get("SystemRoot") or r"C:\Windows"))
ok("普通目录可以当备份目录",
   mod.root_usable(os.path.join(HERE, "_uitest_root")))
ok("默认备份位置在当前用户目录下（不写死盘符，分享给别人也能用）",
   mod.DEFAULT_ROOT.startswith(os.path.expanduser("~")))

# 第一次运行：没配过目录就弹一次选择器，取消落到默认位置
sel_calls = []


def fake_askdir(**kw):
    sel_calls.append(kw)
    return fake_askdir.ret


fake_askdir.ret = ""
real_askdir = mod.filedialog.askdirectory
try:
    mod.filedialog.askdirectory = fake_askdir
    ok("首次运行点取消 → 落到默认位置", mod.pick_root(None, {}) == mod.DEFAULT_ROOT)
    ok("目录选择器从默认位置的上层打开",
       sel_calls[-1].get("initialdir") == os.path.dirname(mod.DEFAULT_ROOT))
    fake_askdir.ret = r"D:\某人选的照片目录"
    ok("首次运行选中的目录被采纳",
       mod.pick_root(None, {}) == os.path.normpath(r"D:\某人选的照片目录"))
    n_calls = len(sel_calls)
    ok("已经配过目录就不再弹窗",
       mod.pick_root(None, {"root": r"E:\已有的库"}) == r"E:\已有的库"
       and len(sel_calls) == n_calls)
finally:
    mod.filedialog.askdirectory = real_askdir

CFG_PROBE = os.path.join(HERE, "_uitest_config.json")
real_cfg_path = mod.CONFIG_PATH
mod.CONFIG_PATH = CFG_PROBE
try:
    probe_cfg = {"root": "X", "devices": {"k": {"label": "L", "dir": "D"}}}
    mod.save_config(probe_cfg)
    ok("config 写得进、读得回（原子写没写坏）", mod.load_config() == probe_cfg)
    ok("写完不留 .tmp 半截文件", not os.path.exists(CFG_PROBE + ".tmp"))
finally:
    mod.CONFIG_PATH = real_cfg_path
    for p in (CFG_PROBE, CFG_PROBE + ".tmp"):
        if os.path.exists(p):
            os.remove(p)

# --------------------------------------------------------------------------
# 2) 临时库
# --------------------------------------------------------------------------

tmp_root = os.path.join(HERE, "_uitest")
shutil.rmtree(tmp_root, ignore_errors=True)
COLORS = {"a": (200, 60, 60), "b": (60, 200, 60), "c": (60, 60, 200),
          "d": (200, 200, 60), "e": (200, 60, 200), "f": (60, 200, 200)}


def make(day, name):
    d = os.path.join(tmp_root, "TESTSET", "Camera", day)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".jpg")
    Image.new("RGB", (64, 48), COLORS[name]).save(p, "JPEG", quality=80)
    return p


for n in "abcde":
    make(os.path.join("2024", "2024-01-01"), n)
make(os.path.join("2023", "2023-05-05"), "f")

app = mod.App()
app.geometry("1220x800+60+40")
app.update()
if getattr(app, "_migrate_job", None):      # 启动时的归类提示会卡住自动化
    app.after_cancel(app._migrate_job)
    app._migrate_job = None

real_root = app.root_dir
# 本文件测的是备份库的网格 / 菜单 / 删除，与「查看」默认落在哪个入口无关，
# 显式切回「全部设备」，免得默认的「本机文件」把 tree_data 换成别的年月
app.lib_picker._pick(mod.ALL_DEVICES)
app.root_dir = tmp_root
app.refresh_library()
for _ in range(8):
    app.update()
    time.sleep(0.05)

ok("临时库读进来了", len(app.items) == 6)
ok("树上有 2024 / 2023 两年", app.tree.exists("y:2024") and app.tree.exists("y:2023"))
ok("刷新后已经自动选中一组并画了网格（不用手动点）", len(app.tiles) > 0)

# --------------------------------------------------------------------------
# 3) 切组：格子复用 + 一次刷新（不闪白、不卡顿）
# --------------------------------------------------------------------------

app.show_group(app.tree_data["2024"]["2024-01"])
first_lbl, first_cell = app.tile_widgets[0], app.tile_cells[0]
ok("第一次画完格子全建好", len(app.tiles) == 5 and len(app.tile_widgets) == 5)

app.show_group(app.tree_data["2023"]["2023-05"])
ok("换组时格子控件被复用（不会先销毁再重建，也就没有空白中间态）",
   app.tile_widgets[0] is first_lbl and app.tile_cells[0] is first_cell)
ok("换组后立刻就是新组的内容（同步完成，不靠 after）",
   len(app.tiles) == 1 and len(app.tile_widgets) == 1)
ok("用不完的槽位退场了（不会露出上一组的格子）",
   app._grid_count == 1 and not app._pool[1][0].winfo_ismapped())
app.show_group(app.tree_data["2024"]["2024-01"])
ok("切回去时格子还在池子里（没被销毁重建）",
   len(app.tile_widgets) == 5 and app.tile_widgets[0] is first_lbl)

rows = (len(app.tiles) + app._cols - 1) // app._cols
ok(f"内嵌 frame 已经把 {len(app.tiles)} 个格子都排进去了（{rows} 行）",
   app.grid_frame.winfo_reqheight() >= rows * (mod.CELL + 2 * theme.S2))

# 缩略图缓存命中：重画同一组不该再解码一遍
it0 = app.tiles[0]
thumb_path = mod.library.thumb(it0["path"], app.cache_dir, mod.THUMB_BOX)
app._handle(("thumb", (app.gen, 0, thumb_path)))
app.update()
ok("缩略图到位后记进了按路径的缓存", it0["path"] in app.photos)
app.show_group(app.tree_data["2024"]["2024-01"])
ok("重画同一组时缓存直接命中（格子一建好就有图）",
   app.tile_widgets[0].cget("image") != "")

# 空组不能留着上一组的格子
app.show_group([])
ok("空状态把 tiles 清干净（不会误开上一组的图）", app.tiles == [] and not app.tile_cells)
app.show_group(app.tree_data["2024"]["2024-01"])

# --------------------------------------------------------------------------
# 4) 删除：立刻消失 + 时间柱同步收缩 + 不重扫磁盘
# --------------------------------------------------------------------------

scans = []
real_scan = mod.library.scan
real_recycle = mod.library.recycle
real_ask = mod.Dialog.ask


def fake_recycle(paths):
    for p in paths:
        os.remove(p)         # 真删临时文件，但不往回收站扔
    return True, ""


mod.library.scan = lambda root: (scans.append(root), real_scan(root))[1]
mod.library.recycle = fake_recycle
mod.Dialog.ask = staticmethod(lambda *a, **k: 1)

try:
    before = len(app.tiles)
    app.recycle_one(0)
    app.update()
    ok(f"删一张后网格立刻少一格（{before} → {len(app.tiles)}）",
       len(app.tiles) == before - 1)
    ok("格子控件同步减一（不用再点一次那个日期）",
       len(app.tile_widgets) == len(app.tiles) == 4)
    ok("该月计数跟着变", app.tree.item("m:2024-01", "text").strip().endswith("4"))
    ok("删完没重扫全库", scans == [])
    ok("手机上那边的事一个字都没提错（只删本地副本）",
       "已移到回收站" in app._status.get())

    # 把这一天删空 → 该月节点应该从时间柱上消失，并自动退到最近的一组
    for _ in range(4):
        app.recycle_one(0)
        app.update()
    ok("这一天删空后月份节点从时间柱上消失", not app.tree.exists("m:2024-01"))
    ok("2024 一整年空了 → 年份节点也消失", not app.tree.exists("y:2024"))
    ok("自动退到还有照片的最近一组（2023-05）",
       app.tree.selection() and app.tree.selection()[0] == "m:2023-05")
    ok("网格跟着换成了那一组", len(app.tiles) == 1)

    # 删光 → 收成空状态，不留旧内容
    app.recycle_one(0)
    app.update()
    ok("全删光后网格是空的", app.tiles == [] and not app.tile_cells)
    ok("全删光后树也是空的", not app.tree.get_children())
    ok("一直没重扫全库", scans == [])
finally:
    mod.library.scan = real_scan
    mod.library.recycle = real_recycle
    mod.Dialog.ask = real_ask

# 恢复成有内容的状态，截图用
for n in "abcde":
    make(os.path.join("2024", "2024-01-01"), n)
make(os.path.join("2023", "2023-05-05"), "f")
app.refresh_library()
for _ in range(8):
    app.update()
    time.sleep(0.05)

# --------------------------------------------------------------------------
# 5) 缩略图转换与消息泵：卡顿的两个来源
# --------------------------------------------------------------------------

# 逐像素对比：换 PPM 是为了快，但内容必须一模一样
probe = Image.new("RGB", (3, 2))
probe.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255),
               (10, 20, 30), (40, 50, 60), (70, 80, 90)])
ph = mod._pil_to_photo(probe)
ok("转出来的尺寸对", (ph.width(), ph.height()) == (3, 2))
bad_px = [(x, y) for y in range(2) for x in range(3)
          if tuple(ph.get(x, y)) != probe.getpixel((x, y))]
ok(f"每个像素都对得上（PPM 通道顺序没搞反）{bad_px if bad_px else ''}", not bad_px)
ok("带透明通道的图也能转（RGBA → RGB）",
   mod._pil_to_photo(Image.new("RGBA", (4, 4), (1, 2, 3, 128))).width() == 4)

# 消息泵限量：几百张缩略图堆在队列里时，一轮不能全做完，否则界面会僵住
for _ in range(mod.THUMBS_PER_DRAIN * 3):
    app.q.put(("thumb", (app.gen, 0, "")))          # 空路径 → _handle 直接返回
real_handle = app._handle
seen = []
app._handle = lambda msg: seen.append(msg[0])
try:
    app._drain()
finally:
    app._handle = real_handle
ok(f"一轮最多只处理 {mod.THUMBS_PER_DRAIN} 张缩略图（实际 {len(seen)}）",
   len(seen) == mod.THUMBS_PER_DRAIN)
ok("没处理完的留在队列里，没有丢", app.q.qsize() == mod.THUMBS_PER_DRAIN * 2)
while not app.q.empty():
    app.q.get_nowait()
app.update()

# --------------------------------------------------------------------------
# 6) 自绘菜单本身
# --------------------------------------------------------------------------

hits = []
m = theme.PopupMenu(app, [("用系统程序打开", lambda: hits.append("打开")),
                          ("在资源管理器中定位", lambda: hits.append("定位")),
                          "-",
                          ("移到回收站（仅电脑上的副本）",
                           lambda: hits.append("删除"), "danger")],
                    300, 300, current="用系统程序打开")
app.update()
ok("菜单是无系统边框的自绘窗口", bool(m.overrideredirect()))
ok("菜单宽度放得下最长的字",
   m._cw >= tkfont.Font(font=theme.F_SMALL).measure("移到回收站（仅电脑上的副本）")
   + 2 * theme.PopupMenu.PAD_X)
ok("分隔线画了但不占可点行",
   len(m._rows) == 3 and
   len([i for i in m.canvas.find_all() if m.canvas.type(i) == "line"]) == 1)
ok("危险项用红字", m.canvas.itemcget(m._rows[2]["text_id"], "fill") == theme.DANGER)
ok("当前项用强调色标出来",
   m.canvas.itemcget(m._rows[0]["text_id"], "fill") == theme.ACCENT)

m._motion(SimpleNamespace(y=m._rows[1]["y0"] + 3))
ok("hover 垫了强调底色",
   m.canvas.itemcget(m._rows[1]["shape"], "fill") == theme.ACCENT_SOFT)
ok("hover 文字转强调色",
   m.canvas.itemcget(m._rows[1]["text_id"], "fill") == theme.ACCENT)
m._motion(SimpleNamespace(y=-9))
ok("鼠标移开就恢复",
   m.canvas.itemcget(m._rows[1]["shape"], "fill") == theme.BG)

shot_bbox = (m.winfo_rootx(), m.winfo_rooty(),
             m.winfo_rootx() + m._cw, m.winfo_rooty() + m._ch)
m._motion(SimpleNamespace(y=m._rows[0]["y0"] + 3))
for _ in range(6):
    app.update()
    time.sleep(0.05)
ImageGrab.grab(bbox=shot_bbox).convert("RGB").save(SHOT_MENU)

m._click(SimpleNamespace(x=6, y=m._rows[2]["y0"] + 3))
app.update()
ok("点危险项执行了命令", hits == ["删除"])
ok("执行完菜单自己关掉", m.winfo_exists() == 0)

m2 = theme.PopupMenu(app, [("甲", lambda: hits.append("不该执行"))], 300, 300)
app.update()
m2._click(SimpleNamespace(x=-9, y=-9))
app.update()
ok("点菜单以外只关不执行", m2.winfo_exists() == 0 and hits == ["删除"])

m3 = theme.PopupMenu(app, [("甲", lambda: hits.append("不该执行"))], 300, 300)
app.update()
m3.event_generate("<Escape>", when="now")
app.update()
ok("Esc 能关掉菜单", m3.winfo_exists() == 0 and hits == ["删除"])

m4 = theme.PopupMenu(app, [("甲", lambda: None)], 99999, 99999)
app.update()
ok("贴着屏幕角弹出也不会跑出屏幕",
   m4.winfo_rootx() + m4._cw <= app.winfo_screenwidth()
   and m4.winfo_rooty() + m4._ch <= app.winfo_screenheight())
m4.close()

# 「更多」菜单：真点一次那个按钮
app._open_more()
for _ in range(6):
    app.update()
    time.sleep(0.05)
mores = [w for w in app.winfo_children() if isinstance(w, theme.PopupMenu)]
ok("「更多 ▾」弹出的是自绘菜单", len(mores) == 1)
if mores:
    more = mores[0]
    labels = [r["label"] for r in more._rows]
    ok("六个动作一个没少",
       len(labels) == 6 and "重新扫描本地库" in labels and "重命名设备目录…" in labels)
    more._motion(SimpleNamespace(y=more._rows[1]["y0"] + 3))
    app.update()
    time.sleep(0.15)
    ImageGrab.grab(bbox=(more.winfo_rootx(), more.winfo_rooty(),
                         more.winfo_rootx() + more._cw,
                         more.winfo_rooty() + more._ch)).convert("RGB").save(SHOT_MORE)
    more.close()
app.update()

# --------------------------------------------------------------------------
# 6) 后台正在读图时动目录：Windows 会因为「目录被占用」直接失败，必须先停线程
# --------------------------------------------------------------------------

stops = []
real_stop = app._stop_thumbs
real_load = mod.load_config
real_save = mod.save_config
real_input = mod.theme.InputDialog.ask
app._stop_thumbs = lambda: (stops.append(True), real_stop())[1]
mod.load_config = lambda: {"devices": {}}
mod.save_config = lambda cfg: None
mod.theme.InputDialog.ask = staticmethod(lambda *a, **k: "RENAMED")
try:
    app.refresh_library()               # 网格重新渲染 → 缩略图线程正在读文件
    for _ in range(4):
        app.update()
        time.sleep(0.03)
    app.rename_device()
    app.update()
    ok("改名之前先把缩略图线程停了", bool(stops))
    ok("目录真的改成了新名字",
       os.path.isdir(os.path.join(tmp_root, "RENAMED"))
       and not os.path.exists(os.path.join(tmp_root, "TESTSET")))
    ok("改名没把文件弄丢",
       os.path.isfile(os.path.join(tmp_root, "RENAMED", "Camera",
                                   "2024", "2024-01-01", "a.jpg")))
finally:
    app._stop_thumbs = real_stop
    mod.load_config = real_load
    mod.save_config = real_save
    mod.theme.InputDialog.ask = real_input

# --------------------------------------------------------------------------

app.destroy()
shutil.rmtree(tmp_root, ignore_errors=True)

print("=" * 56)
for label, passed in checks:
    print(("  PASS  " if passed else "  FAIL  ") + label)
print("=" * 56)
bad = [l for l, p in checks if not p]
print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
if bad:
    print("失败项：" + "；".join(bad))
print(f"右键菜单截图：{SHOT_MENU}")
print(f"更多菜单截图：{SHOT_MORE}")
sys.exit(1 if bad else 0)
