# -*- coding: utf-8 -*-
"""dev/_local_test.py — 「本机文件」回归：懒扫描 / 过滤规则 / 与手机库合并 / 删除口径

跑法：python dev\\_local_test.py

全程用临时 HOME 和临时备份库，不碰真目录；删除也换成假的 recycle，
所以测试跑完你机器上的文件一个都不会少。
"""
import ctypes
import importlib.util
import os
import shutil
import sys
import time

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HERE)
sys.path.insert(0, ROOT_DIR)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    pass

import library  # noqa: E402

spec = importlib.util.spec_from_file_location("appmod", os.path.join(ROOT_DIR, "app.pyw"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

checks = []


def ok(label, cond):
    checks.append((label, bool(cond)))


def ts(y, m, d, hh=12):
    return time.mktime(time.strptime(f"{y}-{m:02d}-{d:02d} {hh}:00:00",
                                     "%Y-%m-%d %H:%M:%S"))


HOME_DIR = os.path.join(HERE, "_localtest_home")
LIB_DIR = os.path.join(HERE, "_localtest_lib")
shutil.rmtree(HOME_DIR, ignore_errors=True)
shutil.rmtree(LIB_DIR, ignore_errors=True)


def mk(rel, color=(200, 30, 30), when=None, when_ts=None):
    """造一个媒体文件：when 写进 EXIF，when_ts 设文件时间。"""
    p = os.path.join(HOME_DIR, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    ext = os.path.splitext(p)[1].lower()
    im = Image.new("RGB", (80, 60), color)
    kw = {}
    if when:
        ex = Image.Exif()
        ex[36867] = when
        kw["exif"] = ex
    im.save(p, "PNG" if ext == ".png" else "JPEG", **kw)
    if when_ts:
        os.utime(p, (when_ts, when_ts))
    return p


# 该收的
p_shot = mk(r"Pictures\Screenshots\屏幕截图 2024-03-05 101112.png", when_ts=ts(2024, 3, 5))
p_cam = mk(r"Downloads\cam.jpg", (30, 200, 30), when="2022:07:08 09:10:11",
           when_ts=ts(2025, 1, 1))          # 文件时间故意设成新的：该以 EXIF 为准
p_clip = mk(r"Videos\clip.mp4", (30, 30, 200), when_ts=ts(2023, 1, 2))
# 不该收的
mk(r"Downloads\temp\垃圾.jpg", when_ts=ts(2024, 1, 1))
mk(r"Documents\Tencent Files\1\nt_qq\nt_data\Pic\2025-01\Thumb\qq.jpg", when_ts=ts(2024, 1, 1))
mk(r"Documents\WXWork\Emoji\e.jpg", when_ts=ts(2024, 1, 1))
with open(os.path.join(HOME_DIR, "Pictures", "note.txt"), "w") as fh:
    fh.write("ignore me")

# 手机库：两个文件，用来验证「合并」视图
for day, name in ((r"TESTSET\Camera\2024\2024-03-05", "IMG_1.jpg"),
                  (r"TESTSET\Camera\2019\2019-05-05", "IMG_2.jpg")):
    d = os.path.join(LIB_DIR, day)
    os.makedirs(d, exist_ok=True)
    Image.new("RGB", (80, 60), (90, 90, 90)).save(os.path.join(d, name), "JPEG")

# --------------------------------------------------------------------------
# 打桩：本机文件只扫临时 HOME；删除只记账不真删
# --------------------------------------------------------------------------

scans = {"n": 0}
_real_roots = library.local_roots


def fake_roots(home=None):
    return [HOME_DIR]


library.local_roots = fake_roots

_real_scan_local = library.scan_local


def counting_scan_local(roots=None, exclude=()):
    scans["n"] += 1
    return _real_scan_local(roots=roots, exclude=exclude)


library.scan_local = counting_scan_local

recycled = []
_real_recycle = library.recycle


def fake_recycle(paths):
    recycled.extend(paths)
    return True, len(paths)


library.recycle = fake_recycle

# 备份库扫描次数：顶栏「刷新」现在会重扫库，但启动路径不该因此扫两遍
full_scans = []
_real_scan = library.scan


def counting_scan(root):
    full_scans.append(root)
    return _real_scan(root)


library.scan = counting_scan

app = mod.App()
app.geometry("1220x800+60+40")
app.update()
if getattr(app, "_migrate_job", None):
    app.after_cancel(app._migrate_job)
    app._migrate_job = None

ok(f"启动路径只扫一遍备份库（实际 {len(full_scans)} 次）",
   len(full_scans) == 1)

app.root_dir = LIB_DIR
app.refresh_library()
for _ in range(8):
    app.update()
    time.sleep(0.05)

# --------------------------------------------------------------------------
# 1) 启动即扫本机（新需求：每次启动都扫一遍，保证文件信息齐全），
#    下拉里三个固定入口都在
# --------------------------------------------------------------------------

ok("启动时就自动扫过本机文件（每次启动都重扫）", scans["n"] >= 1)
options = [v for v, _ in app.lib_picker._items]
ok("「查看」下拉有 全部设备 / 全部（含本机文件）/ 本机文件",
   options[:3] == [mod.ALL_DEVICES, mod.ALL_WITH_LOCAL, library.LOCAL_DEVICE])
ok("下拉里还有库里的设备", "TESTSET" in options)
ok("默认停在「本机文件」", app.lib_picker.value == library.LOCAL_DEVICE)

# --------------------------------------------------------------------------
# 2) 切到「本机文件」
# --------------------------------------------------------------------------

before_pick = scans["n"]
app.lib_picker._pick(library.LOCAL_DEVICE)
for _ in range(10):
    app.update()
    time.sleep(0.05)

ok("已经扫过就不再重复扫（启动时扫过了）", scans["n"] == before_pick)
names = [it["name"] for it in app.shown]
ok("本机文件只收了该收的 3 个", sorted(names) == sorted(
    ["屏幕截图 2024-03-05 101112.png", "cam.jpg", "clip.mp4"]))
ok("QQ 目录整个不要", all("Tencent Files" not in it["path"] for it in app.shown))
ok("表情包/临时目录不要", all(("Emoji" not in it["path"] and "\\temp\\" not in it["path"])
                              for it in app.shown))
ok("device 是伪设备名", all(it["device"] == library.LOCAL_DEVICE for it in app.shown))
ok("有 EXIF 的用拍摄时间（2022-07-08，不是文件时间 2025）",
   next(it for it in app.shown if it["name"] == "cam.jpg")["day"] == "2022-07-08")
ok("按时间倒序（截图 2024-03 在最前）", names[0] == "屏幕截图 2024-03-05 101112.png")
ok("时间柱建起来了", list(app.tree_data) == ["2024", "2023", "2022"])
months = [m for y in app.tree.get_children() for m in app.tree.get_children(y)]
ok("时间柱上的月份对得上",
   months == ["m:2024-03", "m:2023-01", "m:2022-07"])
app.show_group(app.shown)          # 时间柱默认只选了一个月，这里显式铺满整屏
for _ in range(4):
    app.update()
    time.sleep(0.05)
ok(f"网格里铺出来了（实际 {len(app.tile_widgets)} 个格子）",
   len(app.tile_widgets) == 3)
ok("副标题说明这是本机文件", "本机文件" in app.root_lbl.cget("text"))
ok("状态栏标了本机文件", "本机文件" in app._status_var().get())

# 缩略图真的出图了（本机文件的路径也能走同一套缓存）
deadline = time.time() + 8
while time.time() < deadline and sum(1 for l in app.tile_widgets if l._shown) < 2:
    app.update()
    time.sleep(0.05)
ok("本机文件的缩略图能生成并上屏",
   sum(1 for l in app.tile_widgets if l._shown) >= 2)

# --------------------------------------------------------------------------
# 3) 切到「全部（含本机文件）」：两个来源混成一条时间轴
# --------------------------------------------------------------------------

app.lib_picker._pick(mod.ALL_WITH_LOCAL)
for _ in range(10):
    app.update()
    time.sleep(0.05)

ok("合并视图 = 手机库 + 本机文件", len(app.shown) == 5)
ok("合并视图里两个来源都在",
   {"TESTSET", library.LOCAL_DEVICE} == {it["device"] for it in app.shown})
stamps = [it["timestamp"] for it in app.shown]
ok("合并后仍按时间倒序", stamps == sorted(stamps, reverse=True))
ok("已经扫过就不再扫（走缓存）", scans["n"] == before_pick)
years = sorted(app.tree_data)
ok(f"合并视图的时间柱跨了两个来源（实际 {years}）",
   years == ["2019", "2022", "2023", "2024"])
ok("切回「全部设备」= 只有手机备份", True)
app.lib_picker._pick(mod.ALL_DEVICES)
for _ in range(6):
    app.update()
    time.sleep(0.05)
ok("「全部设备」不混本机文件",
   len(app.shown) == 2 and all(it["device"] == "TESTSET" for it in app.shown))
ok("切到别的视图也不重扫", scans["n"] == before_pick)

# --------------------------------------------------------------------------
# 4) 删除口径
# --------------------------------------------------------------------------

app.lib_picker._pick(library.LOCAL_DEVICE)
for _ in range(8):
    app.update()
    time.sleep(0.05)

target = app.tiles[0]
real_path = target["path"]
ok("待删的是本机文件", target["device"] == library.LOCAL_DEVICE)

asked = {}


class FakeDialog:
    @staticmethod
    def ask(_p, title, msg, **_kw):
        asked["title"] = title
        asked["msg"] = msg
        return 1

    @staticmethod
    def error(*_a, **_kw):
        asked["error"] = True


_real_dialog = mod.Dialog
mod.Dialog = FakeDialog
try:
    app.recycle_one(0)
finally:
    mod.Dialog = _real_dialog

ok("删本机文件走的是回收站", recycled == [real_path])
ok("文案没说「仅电脑上的副本」", "副本" not in asked.get("msg", ""))
ok("文案说明了是本机原文件", "原文件" in asked.get("msg", "") and "回收站" in asked.get("msg", ""))
ok("删完立刻从画面上消失", real_path not in [it["path"] for it in app.shown])
ok("删完本机缓存也少一个",
   real_path not in [it["path"] for it in (app.local_items or [])])
ok("时间柱跟着收缩", list(app.tree_data) == ["2023", "2022"])
ok("文件其实还在（测试用的是假 recycle，没真删）", os.path.isfile(real_path))

# 手机库那边删除的文案不能变
app.lib_picker._pick(mod.ALL_DEVICES)
for _ in range(6):
    app.update()
    time.sleep(0.05)
mod.Dialog = FakeDialog
try:
    app.recycle_one(0)
finally:
    mod.Dialog = _real_dialog
ok("手机备份的删除文案还是「仅电脑上的副本」", "副本" in asked.get("msg", ""))

# --------------------------------------------------------------------------
# 5) 「重新扫描」会把本机文件的缓存也清掉
# --------------------------------------------------------------------------

app.refresh_library()
for _ in range(6):
    app.update()
    time.sleep(0.05)
ok("重新扫描后本机缓存被清空", app.local_items is None)

# --------------------------------------------------------------------------
# 6) 云盘「仅在线」文件：格子显示 ☁，不去读它，也不刷 error.log
# --------------------------------------------------------------------------

real_online = library.is_online_only
library.is_online_only = lambda p, st=None: p.lower().endswith("cam.jpg")
try:
    app.refresh_library()
    for _ in range(6):
        app.update()
        time.sleep(0.05)
    app.lib_picker._pick(library.LOCAL_DEVICE)
    for _ in range(10):
        app.update()
        time.sleep(0.05)
    app.photos.clear()          # 模拟「网盘刚关掉、缓存里没有它」的真实情况
    app.show_group(app.shown)
    for _ in range(8):
        app.update()
        time.sleep(0.05)

    by_name = {it["name"]: it for it in app.shown}
    ok("扫描时标出了「仅在线」", by_name["cam.jpg"]["online_only"] is True)
    ok("本地文件不会被误标",
       by_name["屏幕截图 2024-03-05 101112.png"]["online_only"] is False)
    texts = [lbl.cget("text") for lbl in app.tile_widgets]
    ok(f"仅在线的格子显示 ☁（实际 {texts}）", "☁ 仅在线" in texts)
    ok("只标了那一个", texts.count("☁ 仅在线") == 1)
    ok("格子记着自己是仅在线状态",
       any(lbl._online for lbl in app.tile_widgets))
    deadline = time.time() + 10
    while time.time() < deadline and sum(1 for lb in app.tile_widgets
                                         if lb._shown) < 1:
        app.update()
        time.sleep(0.05)
    shown = {app.tiles[i]["name"]: bool(app.tile_widgets[i]._shown)
             for i in range(len(app.tiles))}
    ok(f"本地图片照常出图、仅在线的不出图（实际 {shown}）",
       shown.get("屏幕截图 2024-03-05 101112.png") is True
       and shown.get("cam.jpg") is False)

    # 缩略图线程对「仅在线」文件应该直接跳过，连读都不读一次
    touched = []
    real_thumb = library.thumb
    library.thumb = lambda p, c, b=168: (touched.append(p), real_thumb(p, c, b))[1]
    try:
        app.gen += 1
        app._thumb_worker(app.gen, [{"path": r"X:\nope.jpg", "name": "nope.jpg",
                                     "online_only": True, "is_video": False}])
    finally:
        library.thumb = real_thumb
    ok("缩略图线程对「仅在线」文件直接跳过（一次都没读）", touched == [])
finally:
    library.is_online_only = real_online

# --------------------------------------------------------------------------
# 7) 顶栏「刷新」应该直接重扫本地库，不用再走「更多 → 重新扫描本地库」
# --------------------------------------------------------------------------

real_list = mod.backup.list_devices
mod.backup.list_devices = lambda: []
try:
    app.lib_picker._pick(library.LOCAL_DEVICE)
    for _ in range(8):
        app.update()
        time.sleep(0.05)
    before = scans["n"]
    ok("切到「本机文件」时扫过一次", before >= 1)

    fresh = mk(r"Downloads\新拍的照片.jpg", (11, 22, 33), when_ts=ts(2026, 2, 3))
    ok("新文件确实落在磁盘上", os.path.isfile(fresh))
    ok("刷新前它不在库里", all(it["path"] != fresh for it in (app.local_items or [])))

    app.refresh_devices()          # 就是顶栏那个「刷新」按钮
    for _ in range(10):
        app.update()
        time.sleep(0.05)

    ok("点「刷新」会重扫本机文件", scans["n"] > before)
    ok("新增的文件进了库", any(it["path"] == fresh for it in (app.local_items or [])))
    ok("新增的文件就在当前画面里", any(it["path"] == fresh for it in app.shown))
    ok("刷新顺带也重新检测了手机（列表被重设过）",
       app.device_picker is not None and app.devices == [])
finally:
    mod.backup.list_devices = real_list

app.destroy()
shutil.rmtree(HOME_DIR, ignore_errors=True)
shutil.rmtree(LIB_DIR, ignore_errors=True)

print("=" * 64)
for label, passed in checks:
    print(("  PASS  " if passed else "  FAIL  ") + label)
print("=" * 64)
bad = [l for l, p in checks if not p]
print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
raise SystemExit(0 if not bad else 1)
