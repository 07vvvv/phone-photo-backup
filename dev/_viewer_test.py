# -*- coding: utf-8 -*-
"""验证大图窗口的三件事：全库翻页、两侧按钮可点、Shift 按天跳。

跑法：python dev\\_viewer_test.py
"""
import ctypes
import importlib.util
import os
import sys
import time
from collections import Counter

from PIL import Image, ImageChops, ImageGrab, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HERE)
SHOT = os.path.join(HERE, "_shot_viewer.png")
sys.path.insert(0, ROOT_DIR)     # app.pyw 会 import backup / library / theme

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    pass

spec = importlib.util.spec_from_file_location("appmod", os.path.join(ROOT_DIR, "app.pyw"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
tk = mod.tk

root = mod.App()
root.update()
# 库里有老结构的照片时，App 会在 1.4 秒后弹「要不要归入设备」的模态框把测试卡死
if getattr(root, "_migrate_job", None):
    root.after_cancel(root._migrate_job)
    root._migrate_job = None
print(f"全库 {len(root.items)} 个媒体文件")

# 挑一个当天有多张的月分组，顺便验证「按天跳」会跳过同一天的其余照片
group = None
for year in sorted(root.tree_data, reverse=True):
    for month, arr in root.tree_data[year].items():
        if len(arr) >= 2:
            group = arr
            break
    if group:
        break
print(f"测试分组：{group[0]['month']}，{len(group)} 张")

root.show_group(group)
root.update()

v = mod.Viewer(root, root.items, root.item_index[id(group[0])])
v.attributes("-topmost", True)
v.update()
v.render()
root.update()

checks = []


def ok(label, cond):
    checks.append((label, bool(cond)))


_u32 = ctypes.windll.user32
_u32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p]


def mfplay_rect(hwnd):
    """Win32 窗口在屏幕上的 (x, y, 宽, 高)。"""
    r = mod.mfplay._RECT()
    _u32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


# 假鼠标事件：落在画面左上角，离底部的进度条很远
PRESS_ON_PHOTO = type("E", (), {"x": 0, "y": 0})()


ok("查看器用的是全库列表（不是当前分组）", v.items is root.items)
ok("总数等于全库总数", len(v.items) == len(root.items))
ok(f"位置显示为全库坐标：{v.pos.get()}", v.pos.get().endswith(f"/ {len(root.items)}"))

navs = [w for w in v.canvas.winfo_children() if isinstance(w, tk.Canvas)]
ok(f"两侧翻页按钮已创建（{len(navs)} 个）", len(navs) == 2)

if len(navs) == 2:
    before = v.idx
    navs[1].event_generate("<Button-1>")      # 右侧 = 下一张
    v.update()
    ok("点右按钮翻到下一张", v.idx == (before + 1) % len(v.items))
    navs[0].event_generate("<Button-1>")      # 左侧 = 上一张
    v.update()
    ok("点左按钮回到原处", v.idx == before)

# 跨天：从最新一张出发，Shift+→ 必须落到不同的一天
v.idx = 0
day0 = v.items[0]["day"]
v.step_day(1)
ok(f"按天跳：{day0} → {v.items[v.idx]['day']}", v.items[v.idx]["day"] != day0)

# 同一天有多张时，按天跳要一次跳过当天剩下的
counts = Counter(it["day"] for it in root.items)
multi_day = next((d for d, n in counts.most_common() if n >= 3), None)
if multi_day:
    i = next(i for i, it in enumerate(root.items) if it["day"] == multi_day)
    v.idx = i
    v.step_day(1)
    ok(f"按天跳跳过同一天（{multi_day} 有 {counts[multi_day]} 张）",
       v.items[v.idx]["day"] != multi_day)

v.idx = root.item_index[id(group[0])]
v.render()
v.update()

# --- 动态照片：识别 + 提示 + 长按绑定 ---
mi = next((i for i, it in enumerate(root.items)
           if mod.library.motion_range(it["path"])), None)
if mi is None:
    ok("库里有动态照片可供测试", False)
else:
    v.idx = mi
    v.render()
    v.update()
    ok("动态照片被识别出内嵌 MP4", v.motion is not None)
    ok("底部信息栏出现「按住图片看动的那段」提示",
       "动态照片" in v.hint.get())
    v._hold_start(PRESS_ON_PHOTO)
    ok("长按会挂上触发定时器", v._hold is not None)
    v._hold_end(None)
    ok("松手会取消定时器", v._hold is None)

    # 真的在应用窗口内播一次（关键：不跳外部播放器）
    box = (v.canvas.winfo_rootx(), v.canvas.winfo_rooty(),
           v.canvas.winfo_rootx() + v.canvas.winfo_width(),
           v.canvas.winfo_rooty() + v.canvas.winfo_height())

    def grab():
        v.update()
        im = ImageGrab.grab(bbox=box).convert("RGB")
        return len(im.getcolors(maxcolors=1 << 22) or []), im

    n_idle, idle_img = grab()

    def moving_frames(count=8, gap=0.2):
        out = []
        for _ in range(count):
            time.sleep(gap)
            out.append(grab()[1])
        return out

    # 测试环境里没有真实按住的鼠标，_poll_mouse 会立刻判定「已松手」把播放器停掉；
    # 这里替掉它，只验证「长按 → 窗口内建播放器 → 画面在动 → 停止后回到静态图」。
    # 「松手即停」由真实的 GetAsyncKeyState 轮询负责。
    v._poll_mouse = lambda: None
    v._play_motion()
    for _ in range(30):
        v.update()
        time.sleep(0.1)
        if v.video and v.video.state() == mod.mfplay.STATE_PLAYING:
            break
    ok("长按后在应用窗口内建起播放器（没有跳外部播放器）", v.video is not None)
    if v.video:
        ok(f"播放器进入 PLAYING（state={v.video.state()}）",
           v.video.state() == mod.mfplay.STATE_PLAYING)
        frames = moving_frames()
        moved = any(ImageChops.difference(a, b).getbbox() is not None
                    for a, b in zip(frames, frames[1:]))
        ok("窗口内画面在动（视频真的在播，不是静态图）", moved)
        frames[-1].save(os.path.join(HERE, "_shot_inline_play.png"))
        v._stop_video()
        time.sleep(0.4)
        back = grab()[1]
        drift = max(ImageStat.Stat(ImageChops.difference(idle_img, back)).mean)
        ok(f"松开后视频窗口销毁、回到原来的静态照片（平均色差 {drift:.2f}）",
           drift < 3)

# --- 视频文件：同样必须在应用窗口内播，不跳系统播放器 ---
vi = next((i for i, it in enumerate(root.items) if it["is_video"]), None)
if vi is None:
    ok("库里有视频文件可供测试", False)
else:
    v.idx = vi
    v.render()
    for _ in range(30):
        v.update()
        time.sleep(0.1)
        if v.video and v.video.state() == mod.mfplay.STATE_PLAYING:
            break
    ok("视频文件在应用窗口内建起播放器（没有跳系统播放器）", v.video is not None)
    if v.video:
        ok(f"视频文件进入 PLAYING（state={v.video.state()}）",
           v.video.state() == mod.mfplay.STATE_PLAYING)
        ok("提示栏提示可点击暂停", "暂停" in v.hint.get())
        for _ in range(20):           # _reveal_video 挂在 after 上，要 update 才会推进
            v.update()
            time.sleep(0.1)
            if v.video.shown:
                break
        ok("视频窗口在拿到画面后显示出来（不是一直藏着）", v.video.shown)

        # 尺寸：视频窗口必须贴着 canvas 排版完的实际大小，而不是建窗口那一刻的尺寸
        ml, mt, mr, mb = mod.mfplay.MARGIN
        v.update()
        vid_rect = mod.mfplay.host_rect(v.canvas.winfo_id())
        want = (v.canvas.winfo_width() - ml - mr, v.canvas.winfo_height() - mt - mb)
        real = mfplay_rect(v.video._hwnd)
        ok(f"视频窗口尺寸 = canvas 减留白（{real[2]}x{real[3]} vs 期望 {want[0]}x{want[1]}）",
           (real[2], real[3]) == want)
        ok(f"视频窗口贴在 canvas 内的 ({ml},{mt})",
           (real[0] - v.canvas.winfo_rootx(), real[1] - v.canvas.winfo_rooty()) == (ml, mt))

        # 进度条：在视频下方，且宽度随时间推进
        bar = v.canvas.coords(v._bar_fill) if v._bar_fill else None
        ok("进度条已画出", bar is not None)
        if bar:
            bar_y = v.canvas.winfo_height() - mb + 10      # BAR_GAP
            ok(f"进度条在视频下方（y={bar[1]:.0f}，期望 {bar_y}）",
               abs(bar[1] - bar_y) < 2 and bar[3] - bar[1] == mod.BAR_H)
            if v.video.state() == mod.mfplay.STATE_STOPPED:
                v.video.play()          # 测试用视频只有几秒，可能已经放完，拨回开头
            samples = []
            for _ in range(8):
                v.update()
                time.sleep(0.4)
                samples.append(round(v.canvas.coords(v._bar_fill)[2]
                                     - v.canvas.coords(v._bar_fill)[0]))
            ok(f"进度条随播放推进（{samples[0]}px → … → {samples[-1]}px）",
               len(set(samples)) > 1)

            # 拖动跳转
            bx0 = mod.mfplay.MARGIN[0]
            bx1 = v.canvas.winfo_width() - mod.mfplay.MARGIN[2]
            mid_x, bar_y = (bx0 + bx1) // 2, int(bar[1])
            ok("命中判定：进度条上 True、画面中央 False",
               v._bar_hit(mid_x, bar_y)
               and not v._bar_hit(v.canvas.winfo_width() // 2, 200))
            v.video.pause()                       # 先停住，位置才不会被播放漂移干扰
            for _ in range(10):
                v.update()
                time.sleep(0.1)
                if v.video.state() == mod.mfplay.STATE_PAUSED:
                    break
            v._seek_to(mid_x)
            for _ in range(15):
                v.update()
                time.sleep(0.1)
                if v.video.position() > 0:
                    break
            pos, dur = v.video.position(), v.video.duration()
            ok(f"拖到中间后位置 ≈ 一半（{pos:.2f}s / {dur:.2f}s）",
               dur > 0 and abs(pos / dur - 0.5) < 0.15)
            v.video.play()

        # 点击画面 = 暂停 / 继续
        v.video.pause()
        for _ in range(15):
            v.update()
            time.sleep(0.1)
            if v.video.state() == mod.mfplay.STATE_PAUSED:
                break
        ok(f"暂停后 state = {v.video.state()}（3 = PAUSED）",
           v.video.state() == mod.mfplay.STATE_PAUSED)
        v.video.play()
        for _ in range(15):        # 状态切换是异步的，等它转过来
            v.update()
            time.sleep(0.1)
            if v.video.state() == mod.mfplay.STATE_PLAYING:
                break
        ok(f"继续后重新 PLAYING（state={v.video.state()}）",
           v.video.state() == mod.mfplay.STATE_PLAYING)

        time.sleep(0.3)
        grab()[1].save(os.path.join(HERE, "_shot_video_file.png"))

        # 改动查看器窗口大小，视频窗口必须跟着变（曾经永远停在建窗口那一刻的尺寸）
        v.geometry("1240x820")
        for _ in range(20):
            v.update()
            time.sleep(0.06)
        r2 = mfplay_rect(v.video._hwnd)
        w2 = (v.canvas.winfo_width() - ml - mr, v.canvas.winfo_height() - mt - mb)
        ok(f"改动查看器窗口后视频尺寸跟随（{r2[2]}x{r2[3]} vs 期望 {w2[0]}x{w2[1]}）",
           (r2[2], r2[3]) == w2)

        old = v.video
        v._hold_end(None)         # 鼠标松开
        v.update()
        ok("松开鼠标不会停掉视频文件（视频是常驻播放，不是长按预览）",
           v.video is old)
        v.step(1)                 # 翻页
        v.update()
        ok("翻页后原来那段视频的播放器已释放", v.video is not old)
        ok("翻页后进度条也被清掉", v._bar_fill is None)

# 中文名的视频同样要能播（曾经把路径转成 file:// URL，中文被百分号编码 → 0x80070002）
cn = next((i for i, it in enumerate(root.items)
           if it["is_video"] and not it["name"].isascii()), None)
if cn is not None:
    v.idx = cn
    v.render()
    for _ in range(30):
        v.update()
        time.sleep(0.1)
        if v.video and v.video.state() == mod.mfplay.STATE_PLAYING:
            break
    ok(f"中文名的视频也能在窗口内播（{v.items[cn]['name'][:16]}…）",
       v.video is not None and v.video.state() == mod.mfplay.STATE_PLAYING)
    v._stop_video(redraw=False)

# 视频缩略图：借 Windows Shell 取首帧（和资源管理器显示的是同一张）
vpath = next((it["path"] for it in root.items if it["is_video"]), None)
if vpath is None:
    ok("库里有视频可供缩略图测试", False)
else:
    cached = mod.library.thumb(vpath, root.cache_dir, mod.THUMB_BOX)
    ok(f"视频能生成缩略图（{os.path.basename(vpath)[:18]}…）",
       bool(cached) and os.path.isfile(cached))
    if cached:
        with Image.open(cached) as im:
            ok(f"视频缩略图被裁成正方形 {im.size}", im.size == (mod.THUMB_BOX, mod.THUMB_BOX))
        ok("二次调用命中缓存",
           mod.library.thumb(vpath, root.cache_dir, mod.THUMB_BOX) == cached)

# 普通照片不该被误判
plain = next((i for i, it in enumerate(root.items)
              if not it["is_video"] and not mod.library.motion_range(it["path"])), None)
if plain is not None:
    v.idx = plain
    v.render()
    ok("普通照片不会被当动态照片", v.motion is None)
    ok("翻到静态照片后没有视频在播", v.video is None)

v.idx = mi if mi is not None else root.item_index[id(group[0])]
v.render()
v.update()
root.update()
img = ImageGrab.grab(bbox=(v.winfo_rootx(), v.winfo_rooty(),
                           v.winfo_rootx() + v.winfo_width(),
                           v.winfo_rooty() + v.winfo_height()))
img.save(SHOT)
print(f"截图 {img.size} -> {SHOT}")

v.destroy()
root.destroy()

print("=" * 56)
for label, passed in checks:
    print(("  PASS  " if passed else "  FAIL  ") + label)
print("=" * 56)
bad = [l for l, p in checks if not p]
print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
raise SystemExit(1 if bad else 0)
