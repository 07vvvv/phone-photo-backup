# -*- coding: utf-8 -*-
"""dev/_wireless_test.py — 无线连接 / 掉线立刻中止 / 同风格弹窗 的回归测试

跑法：python dev\\_wireless_test.py
不需要真机 —— 掉线那几条用「假 adb 进程」模拟，把 backup 模块里的 subprocess
换成假的，不碰全局那个（否则后面的 adb 调用全废）。
"""
import ctypes
import importlib.util
import os
import shutil
import subprocess
import sys
import time

from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HERE)
SHOT = os.path.join(HERE, "_shot_dialog.png")
SHOT2 = os.path.join(HERE, "_shot_wireless.png")
SHOT3 = os.path.join(HERE, "_shot_topbar.png")
SHOT4 = os.path.join(HERE, "_shot_topbar_narrow.png")
sys.path.insert(0, ROOT_DIR)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    pass

import backup
import theme
import wireless

spec = importlib.util.spec_from_file_location("appmod", os.path.join(ROOT_DIR, "app.pyw"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

checks = []


def ok(label, cond):
    checks.append((label, bool(cond)))


class FakeProc:
    """假装 adb pull 挂住了：不自己结束，只能被 kill。"""

    def __init__(self):
        self.returncode = None
        self.killed = False

    def communicate(self, timeout=None):
        if self.killed or timeout is None:
            return b"", b""
        raise subprocess.TimeoutExpired("adb", timeout)

    def kill(self):
        self.killed = True
        self.returncode = 1


class FakeSub:
    """只替掉 backup 模块持有的 subprocess 引用，全局那个不动。"""

    TimeoutExpired = subprocess.TimeoutExpired
    PIPE = subprocess.PIPE
    DEVNULL = subprocess.DEVNULL

    def __init__(self):
        self.proc = FakeProc()

    def Popen(self, *a, **kw):
        return self.proc


# --------------------------------------------------------------------------
# 1) 掉线看门狗：adb pull 挂住 + 手机没了 → 立刻抛 DeviceLost
# --------------------------------------------------------------------------

tmp = os.path.join(ROOT_DIR, "_wtest")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp, exist_ok=True)

real_sub = backup.subprocess
real_alive = backup.device_alive
real_poll = backup.POLL_SECONDS
real_timeout = backup.PULL_TIMEOUT
WIRELESS_SERIAL = "192.168.31.100:37001"

try:
    fake = FakeSub()
    backup.subprocess = fake
    backup.POLL_SECONDS = 0.05
    backup.device_alive = lambda serial, timeout=15: False
    dest = os.path.join(tmp, "2024", "2024-03-15", "a.jpg")
    lost = None
    try:
        backup.pull_one(WIRELESS_SERIAL, "/sdcard/DCIM/Camera/a.jpg", dest)
    except backup.DeviceLost as exc:
        lost = exc
    ok("掉线时立刻抛 DeviceLost（不等 30 分钟超时）", lost is not None)
    ok("提示里说清了「已中止 / 手机不会少东西」",
       bool(lost) and "中止" in str(lost) and "不会少" in str(lost))
    ok("挂住的 adb 进程被杀掉", fake.proc.killed)
    ok("半截的 .part 被清理", not os.path.exists(dest + ".part"))
    ok("没有留下半截成品文件", not os.path.exists(dest))

    # 2) 手机还在的时候不能误杀 —— 只是超时，返回失败而不是抛异常
    fake = FakeSub()
    backup.subprocess = fake
    backup.PULL_TIMEOUT = 0.2
    backup.device_alive = lambda serial, timeout=15: True
    good, info = backup.pull_one(WIRELESS_SERIAL, "/sdcard/b.jpg",
                                 os.path.join(tmp, "b.jpg"))
    ok("手机还在时不误判成掉线", good is False and "仍未完成" in info)
    ok("超时同样把进程收掉了", fake.proc.killed)
finally:
    backup.subprocess = real_sub
    backup.device_alive = real_alive
    backup.POLL_SECONDS = real_poll
    backup.PULL_TIMEOUT = real_timeout

# --------------------------------------------------------------------------
# 3) do_backup 的两条中止线：掉线立刻停、连续失败也停
# --------------------------------------------------------------------------

ROWS = [{"_display_name": f"a{i}.jpg", "relative_path": "DCIM/Camera/",
         "_data": f"/sdcard/DCIM/Camera/a{i}.jpg", "_size": "10"} for i in range(10)]
PLAN = ([(r, os.path.join(tmp, r["_display_name"])) for r in ROWS], [], [], 0)
_patched = ("pick_device", "build_plan", "pull_one", "device_alive")
_saved = {k: getattr(backup, k) for k in _patched}
try:
    backup.pick_device = lambda serial=None: WIRELESS_SERIAL
    backup.build_plan = lambda root, serial=None, device_dir=None, index=None: PLAN
    backup.device_alive = lambda serial, timeout=15: True

    # 3a) 连续失败（不是掉线，是文件本身有问题）
    backup.pull_one = lambda serial, remote, dest, watchdog=True: (False, "模拟失败")
    events = []
    err = None
    try:
        backup.do_backup(tmp, on_event=lambda k, p=None: events.append(k))
    except backup.DeviceLost as exc:
        err = exc
    ok("连续失败会中止备份", err is not None and "连续" in str(err))
    ok(f"只试了 {backup.MAX_STREAK} 个就停手",
       sum(1 for e in events if e == "error") == backup.MAX_STREAK)
    ok("中止前发出了 lost 事件", "lost" in events)

    # 3b) 第一个文件就掉线 → 直接停，不再往后拉
    tried = []

    def dying(serial, remote, dest, watchdog=True):
        tried.append(remote)
        raise backup.DeviceLost("手机连接断开了，备份已中止。")

    backup.pull_one = dying
    events2, err2 = [], None
    try:
        backup.do_backup(tmp, on_event=lambda k, p=None: events2.append(k))
    except backup.DeviceLost as exc:
        err2 = exc
    ok("掉线就停手（第一个文件之后就再没动过）", len(tried) == 1 and err2 is not None)
    ok("掉线也会发 lost 事件给界面", "lost" in events2)
finally:
    for k, v in _saved.items():
        setattr(backup, k, v)

# --------------------------------------------------------------------------
# 4) 弹窗：配色、圆角、按钮返回值
# --------------------------------------------------------------------------

root = mod.tk.Tk()
root.geometry("900x640+120+90")
root.configure(bg="#00FF00")     # 父窗口故意涂亮绿：四角真透过去了才看得到绿
root.update()

dlg = theme.Dialog(root, "手机已断开",
                   "备份已中止。\n\n已经拷到电脑上的 1234 个文件都保留着，"
                   "手机上也不会少任何东西。\n重新连上手机后再点「开始备份」，"
                   "会自动跳过已备份的，从断点接着来。",
                   kind="warn", buttons=["重新连接", "知道了"])
dlg.show()
# 截图验证的是「四角透出父窗口的绿」，所以父窗口和它自己都得在最上面，
# 否则拍到的就是盖在它们上面的别的窗口
root.attributes("-topmost", True)
root.lift()
dlg.attributes("-topmost", True)
dlg.lift()

# 必须等窗口真的合成出来：只 update 一次的话抓到的是一片空白（DWM 还没画）
for _ in range(16):
    root.update()
    time.sleep(0.05)

ok("弹窗宽度 = 设计值", dlg.winfo_width() == theme.Dialog.WIDTH)
ok("弹窗高度随正文自适应", dlg.winfo_height() > 170)
ok("自绘标题栏（没有系统边框）", bool(dlg.overrideredirect()))

x, y = dlg.winfo_rootx(), dlg.winfo_rooty()
w, h = dlg.winfo_width(), dlg.winfo_height()

# 屏幕截图受 DWM 合成时序影响，偶尔会抓到还没画好的那一帧 —— 不对就重抓几次。
# （窗口本身没问题：_base 是魔术色就说明透明色属性设上了）
GREEN = (0, 255, 0)
img = None
for _attempt in range(6):
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h)).convert("RGB")
    if img.getpixel((0, 0)) == GREEN:
        break
    time.sleep(0.3)
    root.update()
img.save(SHOT)

ok(f"透明色属性设置成功（_base={dlg._base}）", dlg._base == theme.CardWindow.MAGIC)
corner_tl, corner_br = img.getpixel((0, 0)), img.getpixel((w - 1, h - 1))
ok(f"四角透出父窗口的绿（左上 {corner_tl}，右下 {corner_br}）",
   corner_tl == GREEN and corner_br == GREEN)

dot = img.getpixel((theme.CardWindow.PAD + 4, theme.CardWindow.PAD + 11))
ok(f"标题前是琥珀色圆点 {dot}（warn 语义）",
   dot[0] > 170 and dot[1] < 190 and dot[2] < 100)

card = img.getpixel((w - 30, theme.CardWindow.PAD + 14))
ok(f"卡片底色是纯白 {card}", all(v > 248 for v in card))

dlg._pick(1)
root.update()
ok("点第 2 个按钮 → result = 1", dlg.result == 1)
# 销毁后 winfo_exists() 返回 0（不是抛异常），别指望它炸
ok("按钮点完窗口自己关掉", dlg.winfo_exists() == 0)

# --------------------------------------------------------------------------
# 5) 无线面板
# --------------------------------------------------------------------------

pan = mod.WirelessPanel(root, on_done=None)
pan.show()
pan.attributes("-topmost", True)
pan.lift()
for _ in range(16):
    root.update()
    time.sleep(0.05)
ok("无线面板宽度 = 设计值", pan.winfo_width() == mod.WirelessPanel.WIDTH)
ok("面板高度自适应到内容", pan.winfo_height() > 240)
ok("有「地址」和「配对码」两个输入框",
   isinstance(pan.addr_entry, mod.tk.Entry) and isinstance(pan.code_entry, mod.tk.Entry))
ok("状态行给出了下一步提示", len(pan.status.get()) > 0)
ok("底部那排按钮没有被裁掉",
   pan._bottom.winfo_rooty() + pan._bottom.winfo_height()
   <= pan.winfo_rooty() + pan.winfo_height())

h1 = pan.winfo_height()
pan._say("状态文字变长的时候窗口必须跟着长高，否则最后一行会被裁掉。" * 4)
root.update()
h2 = pan.winfo_height()
ok(f"状态文字变长 → 窗口长高（{h1} → {h2}）", h2 > h1)
pan._say("短提示")
root.update()
h3 = pan.winfo_height()
ok(f"状态文字变短 → 窗口缩回（{h2} → {h3}）", h3 < h2)

# 截图前把父窗口换回白色（绿底是为了验透明，出图不好看）
root.configure(bg=theme.BG)
for _ in range(6):
    root.update()
    time.sleep(0.05)
px, py = pan.winfo_rootx(), pan.winfo_rooty()
pw, ph = pan.winfo_width(), pan.winfo_height()
ImageGrab.grab(bbox=(px, py, px + pw, py + ph)).convert("RGB").save(SHOT2)

pan.close()
root.update()
ok("面板能正常关掉", pan.winfo_exists() == 0)
root.destroy()

# --------------------------------------------------------------------------
# 6) 主程序接线
# --------------------------------------------------------------------------

app = mod.App()
app.update()
if getattr(app, "_migrate_job", None):      # 别让启动时的归类提示把测试卡住
    app.after_cancel(app._migrate_job)
    app._migrate_job = None


def _all_buttons(win, out=None):
    out = [] if out is None else out
    for ch in win.winfo_children():
        if isinstance(ch, theme.RoundButton):
            out.append(ch)
        _all_buttons(ch, out)
    return out


def button_texts(win):
    return [b.itemcget(b._text_id, "text") for b in _all_buttons(win)]


names = button_texts(app)
ok("顶栏多了「无线」按钮", "无线" in names)
ok("原来的「刷新」「开始备份」都还在", "刷新" in names and "开始备份" in names)
ok("多了一个「更多 ▾」收起了次要动作", "更多 ▾" in names)
ok("次要按钮已经收进菜单，不在顶栏占位",
   not any(n in names for n in ("重新扫描", "更改目录", "打开目录")))
ok("顶栏多了一个「查看」用的库设备下拉", hasattr(app, "lib_picker"))
ok("库设备下拉里有「全部设备」",
   mod.ALL_DEVICES in [v for v, _lab in app.lib_picker._items])
# 「未归类（迁移前）」的识别本身在 library.py 自检里测；这里只关心筛选覆盖全
ok("每一台库里的设备都能成为筛选项",
   all(d in [v for v, _lab in app.lib_picker._items]
       for d in {it["device"] for it in app.items}))
ok("筛完之后大图翻页用的是筛选后的列表",
   len(app.shown) == len(app.items) or app.lib_picker.value != mod.ALL_DEVICES)

# 无线设备的 serial 就是 ip:port，得能塞进那个胶囊里
app.device_picker.set_items([WIRELESS_SERIAL])
ok("设备下拉能显示 ip:port 形式的设备", app.device_picker.value == WIRELESS_SERIAL)
ok("识别得出这是无线设备", mod.wireless.is_wireless(app.device_picker.value))

app.geometry("1220x800+80+60")
for _ in range(12):
    app.update()
    time.sleep(0.05)
x0, y0 = app.winfo_rootx(), app.winfo_rooty()
ImageGrab.grab(bbox=(x0, y0, x0 + app.winfo_width(), y0 + 76)).convert("RGB").save(SHOT3)

# 顶栏塞了两个下拉 + 更多菜单，最窄的时候也得每个按钮都露全 —— 拍一张留给人看
app.geometry(f"{app.minsize()[0]}x620")
for _ in range(10):
    app.update()
    time.sleep(0.05)
n0x, n0y = app.winfo_rootx(), app.winfo_rooty()
ImageGrab.grab(bbox=(n0x, n0y, n0x + app.winfo_width(), n0y + 76)).convert("RGB").save(SHOT4)

# 每个顶栏按钮的宽度都得够放下自己的字，否则就是被压扁了
squeezed = [w for w in _all_buttons(app) if w.winfo_width() < w._bw - 1]
ok(f"最窄宽度下顶栏按钮没被压扁（{app.minsize()[0]}px）", not squeezed)

picked, opened = [], []


def fake_ask(master, title, body, *a, **kw):
    picked.append((title, kw.get("kind") or (a[0] if a else None)))
    return 0          # 0 = 第一个按钮（对掉线弹窗来说就是「重新连接」）


mod.Dialog.ask = staticmethod(fake_ask)
app.open_wireless = lambda: opened.append(True)

# 一台设备掉线：只终止它自己，并且弹同风格的告警框
app.busy = True
app._handle(("backup_all_done", [
    {"serial": WIRELESS_SERIAL, "dir": "REDMI-K80-Ultra", "label": "REDMI K80 Ultra",
     "summary": None, "error": "手机连接断开了，备份已中止。", "lost": True},
]))
ok("掉线后解除忙碌、按钮恢复可用", app.busy is False)
ok("掉线弹的是同风格告警框", picked == [("手机已断开", "warn")])
ok("点「重新连接」会打开无线面板", opened == [True])

# 多台里只有一台掉线：另一台的成果不该被当成失败
picked.clear()
app.busy = True
app._handle(("backup_all_done", [
    {"serial": "A:1", "dir": "REDMI-A", "label": "手机A", "summary": None,
     "error": "断开了", "lost": True},
    {"serial": "B:1", "dir": "samsung-S24", "label": "手机B",
     "summary": {"pulled": 7, "skipped": 3, "failed": 0, "errors": []},
     "error": None, "lost": False},
]))
ok("多台里一台掉线仍按掉线告警", picked == [("手机已断开", "warn")])
ok("状态栏汇总了两台的结果",
   "新拉取 7" in app._status.get() and "跳过已备份 3" in app._status.get())

# 普通出错（不是掉线）不该被当成设备断开
picked.clear()
app.busy = True
app._handle(("backup_all_done", [
    {"serial": "x", "dir": "d", "label": "某台手机", "summary": None,
     "error": "读取手机媒体库失败", "lost": False},
]))
ok("普通出错走「备份出错」弹窗，没被当掉线",
   picked == [("备份出错", "error")] and app.busy is False)

# --------------------------------------------------------------------------
# 7) 输入框弹窗 + 设备目录改名（在临时库上验证，不动真库）
# --------------------------------------------------------------------------

inp = mod.theme.InputDialog(app, "重命名设备目录", value="OLD", note="说明文字")
inp.show()
for _ in range(10):
    app.update()
    time.sleep(0.05)
ok("输入框弹窗能开", inp.winfo_width() == mod.theme.InputDialog.WIDTH)
ok("输入框预填了当前值", inp.var.get() == "OLD")
inp.var.set("  NEW-NAME  ")
inp._done(True)
app.update()
ok("输入框返回去掉两端空格的内容", inp.result == "NEW-NAME")
ok("关掉之后就没了", inp.winfo_exists() == 0)

tmp_root = os.path.join(HERE, "_renametest")
shutil.rmtree(tmp_root, ignore_errors=True)
day = os.path.join(tmp_root, "OLD-NAME", "Camera", "2024", "2024-01-01")
os.makedirs(day)
with open(os.path.join(day, "a.jpg"), "wb") as fh:
    fh.write(b"x" * 8)

real_root = app.root_dir
real_save = mod.save_config
real_load = mod.load_config
saved_cfg = []
try:
    app.root_dir = tmp_root
    app.refresh_library()
    ok("临时库里的设备被识别出来", app.lib_picker.value == mod.ALL_DEVICES
       and "OLD-NAME" in [v for v, _l in app.lib_picker._items])

    # 假装 config 里已经记着这台设备（真机上就是 _probe_device 写进去的那条）
    mod.load_config = lambda: {"devices": {"fakekey": {"label": "假设备", "dir": "OLD-NAME"}}}
    mod.save_config = lambda cfg: saved_cfg.append(cfg)
    mod.theme.InputDialog.ask = staticmethod(lambda *a, **k: "NEW:NAME")   # 带非法字符
    app.rename_device()
    ok("改名时非法字符被替换掉",
       os.path.isdir(os.path.join(tmp_root, "NEW-NAME"))
       and not os.path.exists(os.path.join(tmp_root, "OLD-NAME")))
    ok("改名后 config 里的映射跟着更新",
       any(v.get("dir") == "NEW-NAME"
           for cfg in saved_cfg for v in (cfg.get("devices") or {}).values()))
    ok("文件没丢",
       os.path.isfile(os.path.join(tmp_root, "NEW-NAME", "Camera",
                                   "2024", "2024-01-01", "a.jpg")))

    # 名字被占用时不能硬来
    os.makedirs(os.path.join(tmp_root, "TAKEN"))
    mod.theme.InputDialog.ask = staticmethod(lambda *a, **k: "TAKEN")
    app.rename_device()
    ok("目标目录已存在时不改名、也不覆盖",
       os.path.isdir(os.path.join(tmp_root, "NEW-NAME"))
       and os.path.isdir(os.path.join(tmp_root, "TAKEN")))
finally:
    mod.save_config = real_save
    mod.load_config = real_load
    app.root_dir = real_root
    app.refresh_library()
    shutil.rmtree(tmp_root, ignore_errors=True)

app.destroy()
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------

print("=" * 56)
for label, passed in checks:
    print(("  PASS  " if passed else "  FAIL  ") + label)
print("=" * 56)
bad = [l for l, p in checks if not p]
print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
if bad:
    print("失败项：" + "；".join(bad))
print(f"弹窗截图：{SHOT}")
print(f"面板截图：{SHOT2}")
print(f"顶栏截图：{SHOT3}")
print(f"窄窗口顶栏截图：{SHOT4}")
sys.exit(1 if bad else 0)
