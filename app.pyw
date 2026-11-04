# -*- coding: utf-8 -*-
"""app.pyw — 手机照片备份与浏览（tkinter 桌面界面）

视觉：纯白底 + 中性灰阶 + 单点强调蓝（对齐 DeepSeek Harness 的克制白）。
布局：顶栏（标题 / 操作）→ 主体（年月导航 + 缩略图网格）→ 状态栏。
动效：只有按钮做颜色插值；网格里成百上千个格子一律瞬时切换，保证不掉帧。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import backup
import library
import mfplay
import shellthumb
import theme
import welcome
import wireless
from theme import BG, BORDER, BORDER_SOFT, SURFACE, SURFACE_ALT, TEXT, TEXT_DIM, TEXT_FAINT
from theme import DevicePicker, Dialog, PopupMenu, RoundButton, round_rect

DEFAULT_ROOT = backup.default_root()   # 没配置过时的落点（当前用户的「图片」下）

ALL_DEVICES = "全部设备"     # 「查看」下拉里代表不筛选的那一项
# 手机备份 + 这台电脑上的文件，混成一条时间轴（本机文件是个伪设备，见 library.scan_local）
ALL_WITH_LOCAL = "全部（含本机文件）"

THUMB_BOX = 168          # 缩略图最长边
CELL = THUMB_BOX + 2     # 固定格子边长（含 1px 边框）—— 尺寸统一，网格才立得住
MAX_TILES = 400          # 单组最多渲染多少格

# 一轮消息循环最多上屏多少张缩略图（见 App._drain）—— 限量是为了别把主线程占死
THUMBS_PER_DRAIN = 24

BAR_H = 4                # 视频进度条高度
BAR_GAP = 10             # 进度条与视频窗口底边的间距

MAX_ZOOM = 6.0           # 大图最大放大倍数（1.0 = 适应窗口）
ZOOM_STEP = 1.2          # 滚轮一格放大 / 缩小多少


def enable_dpi_awareness():
    """让界面在高 DPI 屏上保持清晰。

    不声明 DPI 感知时 Windows 会把整个窗口位图拉伸（常见 125%/150%），字和照片都发虚。
    必须在创建 Tk 窗口之前调用。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


enable_dpi_awareness()


def icon_path():
    """图标：打包后在解包目录（spec 的 datas 已带上），开发时在源码旁的 assets/。"""
    return os.path.join(backup.base_dir(), "assets", "app.ico")


def apply_icon(win):
    """设置窗口图标。

    exe 自身内嵌了图标（资源管理器/快捷方式用），但 Tk 窗口默认仍显示自带的羽毛图标，
    必须显式加载一次。用 default= 让它同时作用于之后创建的 Toplevel（图片查看器）。
    """
    p = icon_path()
    if not os.path.isfile(p):
        return
    try:
        win.iconbitmap(default=p)
    except tk.TclError:
        pass


# 配置放在 exe（或脚本）旁边：backup.base_dir() 是打包后的临时解包目录，
# 每次运行都变且只读，所以这里用 backup.app_dir()。
CONFIG_PATH = os.path.join(backup.app_dir(), "config.json")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def save_config(cfg):
    # 先写 .tmp 再改名：写到一半断电/磁盘满也不会把 config.json 截成半截
    tmp = CONFIG_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception:  # noqa: BLE001
        pass


def _pil_to_photo(im):
    """把 PIL 图像转成 tkinter 能显示的 PhotoImage。

    刻意不用 PIL.ImageTk：它依赖 _imagingtk C 扩展，打包成 exe 后常因为找不到
    tcl/tk 的 DLL 而加载失败，症状就是缩略图永远停在占位符上。

    走 PPM 而不是 PNG：PNG 要 zlib 压缩、再 base64、再由 Tk 解 base64 和解压，
    单张实测 4.7ms —— 一屏 400 张就是近两秒的主线程冻结（界面彻底不动）。
    PPM 是无压缩裸像素，Tk 收到就是内存拷贝，单张 0.5ms，快 9 倍。
    """
    if im.mode != "RGB":          # PPM 的 P6 只吃 24 位 RGB
        im = im.convert("RGB")
    return tk.PhotoImage(data=b"P6 %d %d 255 " % im.size + im.tobytes())


# --------------------------------------------------------------------------
# 大图查看器
# --------------------------------------------------------------------------


class Viewer(tk.Toplevel):
    """大图窗口：← → 翻页，Esc 关闭。深色底让照片本身成为唯一焦点。"""

    CANVAS_BG = "#16161A"

    def __init__(self, master, items, index):
        super().__init__(master)
        self.items = items
        self.idx = index
        self.photo = None
        self.motion = None          # 当前这张若是动态照片，存它的内嵌 MP4 区间
        self._hold = None           # 长按定时器
        self.video = None           # 正在窗口内播放的视频
        self._video_path = None     # 它对应哪个文件（Configure 抖动时避免重起）
        self._was_down = True       # 上一轮左键状态，用来取「按下」的上升沿
        self._bar_fill = None       # 进度条里那段已播的填充
        self._dragging = False      # 正在拖进度条
        self.zoom = 1.0             # 1.0 = 适应窗口，滚轮放大到 MAX_ZOOM
        self._pan = [0.0, 0.0]      # 放大后相对居中位置的平移量（画布像素）
        self._img = None            # 当前静态图的 PIL 原图（缩放/平移时反复用）
        self._path = None           # self._img 对应哪个文件
        self._fallback_hint = False # 这张图是不是靠系统解码器才显示出来的
        self._last = None           # 上一次 B1-Motion 的坐标
        self.geometry("1040x740")
        self.configure(bg=self.CANVAS_BG)

        bar = tk.Frame(self, bg="#101014", height=40)
        bar.pack(side="bottom", fill="x")     # 先占底部，canvas 才能正确 expand
        bar.pack_propagate(False)

        self.canvas = tk.Canvas(self, bg=self.CANVAS_BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._nav(-1)
        self._nav(1)
        self.info = tk.StringVar()
        tk.Label(bar, textvariable=self.info, font=theme.F_SMALL, bg="#101014",
                 fg="#9A9AA4", anchor="w").pack(side="left", padx=theme.S4)
        self.pos = tk.StringVar()
        tk.Label(bar, textvariable=self.pos, font=theme.F_SMALL, bg="#101014",
                 fg="#6E6E78").pack(side="right", padx=theme.S4)
        self.hint = tk.StringVar()
        tk.Label(bar, textvariable=self.hint, font=theme.F_SMALL, bg="#101014",
                 fg="#7C8BF5").pack(side="right")

        self.bind("<Left>", lambda e: self.step(-1))
        self.bind("<Right>", lambda e: self.step(1))
        self.bind("<Shift-Left>", lambda e: self.step_day(-1))    # 按天翻，跳过当天其余照片
        self.bind("<Shift-Right>", lambda e: self.step_day(1))
        self.bind("<Escape>", lambda e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Configure>", self._on_configure)
        # 长按 = 按住不放 350ms，对应手机上的「长按照片看动图」
        self.canvas.bind("<ButtonPress-1>", self._hold_start)
        self.canvas.bind("<ButtonRelease-1>", self._hold_end)
        self.canvas.bind("<B1-Motion>", self._on_b1_motion)   # 拖进度条 / 放大后平移
        self.canvas.bind("<MouseWheel>", self._on_wheel)      # 缩放（break 挡住全局滚轮）
        self.after(60, self.render)

    def _on_configure(self, _event):
        """窗口尺寸变了：静态图重新缩放；正在播视频则把视频窗口和进度条重新摆位。

        不能直接 render —— 那会把视频窗口收掉重建，画面会闪一下、还要重新缓冲。
        """
        if self.video:
            self.video.fit(self.canvas.winfo_id())
            self._draw_bar()
        else:
            self.render()

    def _hold_start(self, event):
        self._last = (event.x, event.y)         # 放大后平移要用它算增量
        if self._bar_hit(event.x, event.y):     # 按在进度条上 = 拖动，不是长按
            self._dragging = True
            self._seek_to(event.x)
            return
        if self.motion:
            self._hold = self.after(350, self._play_motion)

    def _hold_end(self, _event):
        self._dragging = False
        if self._hold:
            self.after_cancel(self._hold)
            self._hold = None
        if self.motion:              # 只有长按看动图才「松手即停」
            self._stop_video()       # 视频文件是常驻播放，松手不能停掉

    def _on_b1_motion(self, event):
        """按住左键拖动：进度条上是跳转，放大状态是平移画面。"""
        if self._dragging:
            self._seek_to(event.x)
            return
        if self._img is None or self.zoom <= 1.0 or self._last is None:
            return
        dx, dy = event.x - self._last[0], event.y - self._last[1]
        if not dx and not dy:
            return
        self._last = (event.x, event.y)
        if self._hold:                 # 已经在拖了，就别再触发「长按看动图」
            self.after_cancel(self._hold)
            self._hold = None
        self._pan[0] += dx
        self._pan[1] += dy
        self._clamp_pan(*self._img.size)
        self._redraw_static()

    # ---------------- 缩放 ----------------

    def _on_wheel(self, event):
        """滚轮缩放，以光标为锚点。

        必须返回 "break"：主窗口对滚轮用的是 bind_all，不挡住的话
        查看器里每滚一格，背后的缩略图网格也会跟着滚。
        """
        if self.video or self._img is None:
            return "break"
        self._zoom_at(event.x, event.y,
                      ZOOM_STEP if event.delta > 0 else 1.0 / ZOOM_STEP)
        return "break"

    def _zoom_at(self, mx, my, step):
        iw, ih = self._img.size
        fit = self._fit_scale(iw, ih)
        old = self.zoom
        new = min(MAX_ZOOM, max(1.0, old * step))
        if abs(new - old) < 1e-4:
            return
        s_old, s_new = fit * old, fit * new
        W, H = self.winfo_width(), self.winfo_height()
        cx, cy = W // 2, (H - 40) // 2
        x0 = cx - iw * s_old / 2 + self._pan[0]
        y0 = cy - ih * s_old / 2 + self._pan[1]
        ix = (mx - x0) / s_old            # 光标下的那个像素，在原图里的位置
        iy = (my - y0) / s_old
        self.zoom = new
        # 缩完之后，让同一个像素仍落在光标下面
        self._pan[0] = mx - ix * s_new - (cx - iw * s_new / 2)
        self._pan[1] = my - iy * s_new - (cy - ih * s_new / 2)
        self._clamp_pan(iw, ih)
        self._redraw_static()

    def _fit_scale(self, iw, ih):
        """zoom=1 时的基准比例：整张图刚好放进内容区（只缩小，不放大）。

        放大后的一切计算都以 fit*zoom 为「实际显示比例」，这样大图在 1.0
        是「适应窗口」而不是「原尺寸裁掉一半」。
        """
        w = max(self.winfo_width() - 160, 120)
        h = max(self.winfo_height() - 100, 120)
        if iw <= 0 or ih <= 0:
            return 1.0
        return min(1.0, w / iw, h / ih)

    def _clamp_pan(self, iw, ih):
        """平移不许把图片拖出视口：最多贴到边；图片没超出视口时自动归零。"""
        s = self._fit_scale(iw, ih) * self.zoom
        W = max(self.winfo_width(), 120)
        H = max(self.winfo_height() - 40, 120)
        lx = max(0.0, (iw * s - W) / 2)
        ly = max(0.0, (ih * s - H) / 2)
        self._pan[0] = min(lx, max(-lx, self._pan[0]))
        self._pan[1] = min(ly, max(-ly, self._pan[1]))

    def _redraw_static(self):
        self.canvas.delete("all")
        self._bar_fill = None
        self._draw_static()
        self._update_hint()

    def _draw_static(self):
        """按 zoom/pan 把「看得见的那一块」裁出来缩放上屏。

        为什么不整张 resize：放大 6 倍后整图有上万像素，整张缩放要好几百 MB；
        只裁视口那么大一块，内存和耗时只跟窗口尺寸有关。
        """
        img = self._img
        if img is None:
            return
        from PIL import Image
        iw, ih = img.size
        s = self._fit_scale(iw, ih) * self.zoom
        W, H = self.winfo_width(), self.winfo_height()
        cx, cy = W // 2, (H - 40) // 2
        dw, dh = iw * s, ih * s
        x0 = cx - dw / 2 + self._pan[0]
        y0 = cy - dh / 2 + self._pan[1]
        vx0, vy0 = max(0.0, x0), max(0.0, y0)
        vx1, vy1 = min(float(W), x0 + dw), min(float(H - 40), y0 + dh)
        if vx1 - vx0 < 1 or vy1 - vy0 < 1:
            return
        box = (max(0, int((vx0 - x0) / s)), max(0, int((vy0 - y0) / s)),
               min(iw, int((vx1 - x0) / s + 0.5)), min(ih, int((vy1 - y0) / s + 0.5)))
        if box[2] <= box[0] or box[3] <= box[1]:
            return
        tw, th = max(1, int(vx1 - vx0)), max(1, int(vy1 - vy0))
        crop = img.crop(box)
        if crop.size != (tw, th):
            crop = crop.resize((tw, th), Image.LANCZOS)
        self.photo = _pil_to_photo(crop)
        self.canvas.create_image(vx0, vy0, image=self.photo, anchor="nw")

    def _update_hint(self):
        """底部提示：放大时优先显示比例，其余情况维持原有说明。"""
        if self.zoom > 1.001:
            self.hint.set(f"缩放 {int(round(self.zoom * 100))}% · 滚轮缩放 · 按住拖动")
        elif self.motion:
            self.hint.set("● 动态照片 · 按住图片看动的那段")
        elif self._fallback_hint:
            self.hint.set("● 系统解码器预览")
        else:
            self.hint.set("")

    def _load_image(self, it, box):
        """读一张静态图 → (PIL 原图, 是否系统解码器)。读不出来时图为 None。"""
        from PIL import Image, ImageOps
        try:
            with Image.open(it["path"]) as fh:
                im = ImageOps.exif_transpose(fh)
                im.load()
            return im, False
        except Exception as exc:      # noqa: BLE001
            library.log_error("大图显示", it["path"], exc)
            # Pillow 解不开不代表这张图废了：HEIC/HEIF 要系统解码器（见 library.thumb）
            try:
                im = shellthumb.frame(it["path"], max(box, 1))
            except Exception:         # noqa: BLE001
                im = None
            return im, im is not None

    def _bar_hit(self, x, y):
        """鼠标是不是落在进度条上。条只有 4px 高，上下各给 8px 容差才点得中。"""
        if not self._bar_fill:
            return False
        x0, y0, _, y1 = self.canvas.coords(self._bar_fill)
        right = self.canvas.winfo_width() - mfplay.MARGIN[2]
        return x0 - 6 <= x <= right + 6 and y0 - 8 <= y <= y1 + 8

    def _over_bar(self):
        """光标此刻是否停在进度条上（用来区分「点画面」和「拖进度条」）。

        _tick 只看按键状态，分不清鼠标在哪 —— 拖进度条那一下会被它当成点击画面，
        顺手把视频暂停掉。
        """
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return self._bar_hit(pt.x - self.canvas.winfo_rootx(),
                             pt.y - self.canvas.winfo_rooty())

    def _seek_to(self, x):
        if not self.video:
            return
        dur = self.video.duration()
        if dur <= 0:
            return
        x0 = mfplay.MARGIN[0]
        x1 = self.canvas.winfo_width() - mfplay.MARGIN[2]
        frac = min(max((x - x0) / max(x1 - x0, 1), 0.0), 1.0)
        self.video.seek(frac * dur)
        if self.video.state() == mfplay.STATE_STOPPED:
            self.video.play()        # 播完之后往回拖 = 从那儿接着放
            self.hint.set("● 播放中 · 点击画面暂停")
        self._update_bar()

    def _play_motion(self):
        """长按触发：把内嵌 MP4 切出来，在查看器窗口内直接播。"""
        self._hold = None
        it = self.items[self.idx]
        # 先收掉上一段：切出来的临时 MP4 是同一个文件名，别覆盖正在播的那个
        self._stop_video(redraw=False)
        ok, res = library.extract_motion(it["path"])
        if not ok:
            library.log_error("播放动态照片", it["path"], res)
            return
        if self._play_video(res):
            self.hint.set("● 正在播放 · 松开鼠标停止")
            self._poll_mouse()

    def _play_whole_video(self):
        """视频文件（.mp4）：进到这张就自动在窗口内播，不再丢给系统播放器。

        和动态照片不同，这里不跟着鼠标走 —— 一段视频几十秒，松手就停没法看。
        点画面暂停/继续，播完就停在最后一帧（不自动重播）。
        """
        it = self.items[self.idx]
        if self._play_video(it["path"]):
            self._draw_bar()             # render 会清空 canvas，进度条这里补回来
            self.hint.set("● 播放中 · 点击画面暂停")
            self._was_down = True        # 别把打开窗口时那一下点击当成暂停
            self._tick()

    def _play_video(self, path):
        """把一段视频播进查看器窗口。→ 是否成功（失败已回退系统播放器）"""
        # Configure 事件抖动会让 render 被多调一次，别把刚起播的视频从头重建
        if self.video and self._video_path == path:
            return True
        self._stop_video(redraw=False)
        try:
            self.video = mfplay.InlineVideo(path, self.canvas.winfo_id())
        except OSError as exc:
            # 没有 mfplay.dll 的老系统：退回系统播放器，至少能看到
            library.log_error("窗口内播放失败，回退系统播放器", path, exc)
            try:
                os.startfile(path)
            except OSError:
                pass
            return False
        self._video_path = path
        self._reveal_video(self.video)
        return True

    def _draw_bar(self):
        """在视频下方画一条进度条底槽。填充部分由 _tick 按播放位置推进。"""
        self.canvas.delete("bar")
        self._bar_fill = None
        if not self.video or self.motion:   # 动态照片只有一两秒，不配进度条
            return
        w = self.canvas.winfo_width()
        x0, x1 = mfplay.MARGIN[0], w - mfplay.MARGIN[2]
        y = self.canvas.winfo_height() - mfplay.MARGIN[3] + BAR_GAP
        self.canvas.create_rectangle(x0, y, x1, y + BAR_H,
                                     fill="#2A2A33", outline="", tags="bar")
        self._bar_fill = self.canvas.create_rectangle(
            x0, y, x0, y + BAR_H, fill=theme.ACCENT, outline="", tags="bar")
        self._update_bar()      # 立刻按当前位置填一次，别等到下一个心跳

    def _update_bar(self):
        if not self._bar_fill or not self.video:
            return
        x0, y0, _, y1 = self.canvas.coords(self._bar_fill)
        x1 = self.canvas.winfo_width() - mfplay.MARGIN[2]
        if self.video.state() == mfplay.STATE_STOPPED:
            frac = 1.0        # 播完时 position 会归零，进度条画满才说得通
        else:
            dur = self.video.duration()
            frac = self.video.position() / dur if dur > 0 else 0.0
        self.canvas.coords(self._bar_fill, x0, y0,
                           x0 + (x1 - x0) * min(max(frac, 0.0), 1.0), y1)

    def _tick(self):
        """视频文件播放期间的 60ms 心跳：推进进度条 + 盯着鼠标点击。

        和 _poll_mouse 同样的原因：画面窗口盖在 canvas 上，Tk 收不到鼠标事件，
        只能直接问系统要按键状态，取「按下」的上升沿。
        """
        if not self.video:
            return
        self._update_bar()
        down = bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
        if down and not self._was_down and not self._over_bar():
            if self.video.playing():
                self.video.pause()
                self.hint.set("⏸ 已暂停 · 点击画面继续")
            elif self.video.state() == mfplay.STATE_PAUSED:
                self.video.play()
                self.hint.set("● 播放中 · 点击画面暂停")
        self._was_down = down
        self.after(60, self._tick)

    def _reveal_video(self, vid, tries=40):
        """等解码器真的送出画面了，再把视频窗口显示出来。

        容器窗口建好时是隐藏的；MFPlay 搭 pipeline 要几百毫秒，这段时间窗口里空无一物，
        提前显示就会「先闪一块板、再出画面」。这里一直等到 state 变成 PLAYING，
        再多给 70ms 让第一帧落地，然后才 show() —— 用户看到的是静态图直接变视频。

        每轮顺手 fit 一次：窗口刚打开时 canvas 可能还没算出尺寸，
        那时建出来的窗口只有 16x16（见 mfplay.host_rect 的兜底），这里负责纠正。
        """
        if self.video is not vid:
            return
        vid.fit(self.canvas.winfo_id())
        if vid.state() == mfplay.STATE_PLAYING:
            self.after(70, lambda: vid.show() if self.video is vid else None)
            return
        if tries <= 0:
            vid.show()          # 兜底：实在等不到也别一直不显示
            return
        self.after(25, lambda: self._reveal_video(vid, tries - 1))

    def _poll_mouse(self):
        """盯着左键是否还按着。

        视频一旦开播，画面窗口就盖在 canvas 上，鼠标在它上面松开时 Tk 收不到
        ButtonRelease，所以不能只靠事件 —— 直接问系统要按键状态。
        """
        if not self.video:
            return
        if not (ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000):
            self._stop_video()
            return
        self.after(60, self._poll_mouse)

    def _stop_video(self, redraw=True):
        if self.video:
            self.video.close()
            self.video = None
            self._video_path = None
            self._bar_fill = None
            self.canvas.delete("bar")
            if redraw:
                self.render()

    def _close(self):
        self._stop_video(redraw=False)
        self.destroy()

    def _nav(self, side):
        """两侧的翻页按钮。

        画几何三角而不是用字符箭头：字形在不同系统上不一定有，缺字会变成豆腐块。
        按钮坐落在图片两侧的留白里（render 的可用宽度已扣掉 160px），不会压住照片。
        """
        c = tk.Canvas(self.canvas, width=46, height=92, bg=self.CANVAS_BG,
                      highlightthickness=0, bd=0, cursor="hand2")
        pts = [(32, 30), (14, 46), (32, 62)]
        if side > 0:
            pts = [(46 - x, y) for x, y in pts]
        c.create_polygon([v for pt in pts for v in pt], fill="#5C5C68", outline="")
        c.bind("<Button-1>", lambda e: self.step(side))
        c.bind("<Enter>", lambda e: (c.itemconfigure(1, fill="#E6E6EC"),
                                     c.configure(bg="#22222A")))
        c.bind("<Leave>", lambda e: (c.itemconfigure(1, fill="#5C5C68"),
                                     c.configure(bg=self.CANVAS_BG)))
        c.place(relx=0.0 if side < 0 else 1.0, rely=0.5,
                anchor="w" if side < 0 else "e", x=10 if side < 0 else -10)
        return c

    def step(self, delta):
        if not self.items:
            return
        self._stop_video(redraw=False)
        self.idx = (self.idx + delta) % len(self.items)
        self.render()

    def step_day(self, delta):
        """跳到上一天 / 下一天的第一张。"""
        if not self.items:
            return
        day = self.items[self.idx]["day"]
        i = self.idx
        while 0 <= (i := i + delta) < len(self.items):
            if self.items[i]["day"] != day:
                self._stop_video(redraw=False)
                self.idx = i
                self.render()
                return

    def render(self):
        if not self.items:
            return
        it = self.items[self.idx]
        if self.video and self._video_path != it["path"]:
            self._stop_video(redraw=False)   # 换到别的照片了，先把上一段视频收掉
        self.title(it["name"])
        self.pos.set(f"{self.idx + 1} / {len(self.items)}")
        self.info.set(f"{it['path']}     {library.human_size(it['size'])}")
        self.canvas.delete("all")
        self._bar_fill = None            # delete("all") 之后旧引用就失效了
        self.photo = None                # 下面几条路都可能失败，别让上一张的图留着
        online = bool(it.get("online_only"))
        self.motion = (None if (it["is_video"] or online)
                       else library.motion_range(it["path"]))
        self._fallback_hint = False

        w = max(self.winfo_width() - 160, 120)   # 左右各留 80px 给翻页按钮
        h = max(self.winfo_height() - 100, 120)
        cx, cy = self.winfo_width() // 2, (self.winfo_height() - 40) // 2

        if online:
            # 云盘「按需文件」：本地 0 字节，读它只会等驱动超时报错（错误 362），
            # 所以不试了，直接把情况说清楚 —— 以前这里显示的是一句
            # 「[Errno 22] Invalid argument」，没人看得懂。
            self._img, self._path = None, None
            self.hint.set("")
            self.canvas.create_text(
                cx, cy, justify="center", fill=TEXT_DIM, font=(theme.FAMILY, 11),
                text="这张图只存在云上，本地没有数据\n\n"
                     f"{it['path']}\n\n"
                     "启动 OneDrive（或对应的网盘客户端）后，\n"
                     "点「更多 → 重新扫描」就能看到了。")
            return
        if it["is_video"]:
            self._img, self._path = None, None
            self.zoom, self._pan = 1.0, [0.0, 0.0]
            self._play_whole_video()     # 视频文件也在这个窗口里播，不跳系统播放器
            return
        # 换图才重新解码；窗口缩放（Configure 抖动）只按新尺寸重画，不重读文件
        if it["path"] != self._path:
            self._path = it["path"]
            self.zoom, self._pan = 1.0, [0.0, 0.0]
            self._img, self._fallback_hint = self._load_image(it, max(w, h))
            if self._img is None:
                self.hint.set("")
                self.canvas.create_text(cx, cy, justify="center", fill="#C88",
                                        font=(theme.FAMILY, 11),
                                        text="无法显示这张图片\n"
                                             "格式不被支持，或者文件已经损坏。\n"
                                             "（详细原因记在程序旁边的 error.log）")
                return
        self._clamp_pan(*self._img.size)
        self._draw_static()
        self._update_hint()


# --------------------------------------------------------------------------
# 无线连接面板
# --------------------------------------------------------------------------


class WirelessPanel(theme.CardWindow):
    """无线连接面板：自动发现 → 配对 / 连接 / 断开。

    手机上要做的只有一件事：设置 → 开发者选项 → 无线调试 → 打开；
    首次连接再点「使用配对码配对设备」，把屏幕上那 6 位数字填进来。

    Android 11+ 的配对端口和连接端口不是同一个、而且每次开关无线调试都会变，
    所以这里用 mDNS 自动发现端口，用户不必去抄 IP:端口。
    """

    WIDTH = 470

    def __init__(self, master, on_done=None):
        self._closed = False                 # Esc 绑定在 super() 里，这个标志得先就位
        self._busy = False
        super().__init__(master, self.WIDTH, 340, title="无线连接")
        self._on_done = on_done
        wrap = self.WIDTH - 2 * self.PAD - 8

        content = tk.Frame(self.body, bg=BG)
        content.pack(fill="x", anchor="n")
        self._content = content

        tk.Label(content, text="手机上打开「设置 → 开发者选项 → 无线调试」。首次连接时点"
                               "「使用配对码配对设备」，把屏幕上那 6 位数字填到下面。",
                 font=theme.F_SMALL, bg=BG, fg=TEXT_FAINT, justify="left", anchor="w",
                 wraplength=wrap).pack(fill="x")

        # 地址行
        row1 = tk.Frame(content, bg=BG)
        row1.pack(fill="x", pady=(theme.S4, 0))
        tk.Label(row1, text="地址", font=theme.F_SMALL, bg=BG, fg=TEXT_DIM,
                 anchor="w").pack(side="left", padx=(0, theme.S2))
        self.addr_var = tk.StringVar()
        self.addr_entry = self._entry(row1, self.addr_var, width=22)
        self.addr_entry.pack(side="left", fill="x", expand=True, ipady=4)
        RoundButton(row1, "连接", self._connect, parent_bg=BG,
                    height=30, padx=14).pack(side="left", padx=(theme.S2, 0))

        # 配对码行
        row2 = tk.Frame(content, bg=BG)
        row2.pack(fill="x", pady=(theme.S2, 0))
        tk.Label(row2, text="配对码", font=theme.F_SMALL, bg=BG, fg=TEXT_DIM,
                 anchor="w").pack(side="left", padx=(0, theme.S2))
        self.code_var = tk.StringVar()
        self.code_entry = self._entry(row2, self.code_var, width=10)
        self.code_entry.pack(side="left", ipady=4)
        RoundButton(row2, "配对并连接", self._pair, primary=True, parent_bg=BG,
                    height=30, padx=14).pack(side="left", padx=(theme.S2, 0))

        # 状态
        self.status = tk.StringVar(value="")
        tk.Label(content, textvariable=self.status, font=theme.F_SMALL, bg=BG,
                 fg=TEXT_DIM, justify="left", anchor="w",
                 wraplength=wrap).pack(fill="x", pady=(theme.S3, 0))

        # 底部动作
        row3 = tk.Frame(content, bg=BG)
        row3.pack(fill="x", pady=(theme.S3, 0))
        self._bottom = row3
        RoundButton(row3, "重新发现", self._rediscover, parent_bg=BG,
                    height=30, padx=14).pack(side="left")
        RoundButton(row3, "断开全部", self._disconnect, parent_bg=BG,
                    height=30, padx=14).pack(side="left", padx=(theme.S2, 0))
        RoundButton(row3, "完成", self.close, primary=True, parent_bg=BG,
                    height=30, padx=16).pack(side="right")

        self.update_idletasks()
        self._rediscover()               # 先把状态文本填上，fit 才算得准高度
        self.update_idletasks()
        self.fit(self._content.winfo_reqheight() + self._top() + self.PAD)

    # ---- 小工具 ----

    def _entry(self, parent, var, width):
        """原生 Entry 是方角的，这里只把它压扁、配色对齐，不做圆角（不值当）。"""
        return tk.Entry(parent, textvariable=var, font=theme.F_SMALL, width=width,
                        bg=SURFACE, fg=TEXT, relief="flat", highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=theme.ACCENT,
                        insertbackground=TEXT)

    def _say(self, text):
        """状态文本会换行，长短一变窗口就得跟着长高/缩回，不然文字会被裁掉。"""
        if self._closed:
            return
        self.status.set(text)
        self.update_idletasks()
        self.fit(self._content.winfo_reqheight() + self._top() + self.PAD)

    def close(self):
        self._closed = True
        super().close()

    # ---- 后台动作：线程只写 dict，主线程 after 轮询取回 ----

    def _run(self, work, busy_text):
        if self._busy:
            return
        self._busy = True
        self._say(busy_text)
        box = {}

        def runner():
            try:
                box["result"] = work()
            except Exception as exc:      # noqa: BLE001
                box["result"] = (False, f"{type(exc).__name__}: {exc}")
            box["done"] = True

        threading.Thread(target=runner, daemon=True).start()
        self.after(150, lambda: self._poll(box))

    def _poll(self, box, tries=700):     # 700 × 150ms ≈ 105 秒上限
        if self._closed:
            return
        if not box.get("done"):
            if tries <= 0:
                self._busy = False
                self._say("✗ 超时了 —— 检查手机和电脑是不是在同一个 Wi-Fi 下")
                return
            self.after(150, lambda: self._poll(box, tries - 1))
            return
        self._busy = False
        ok, msg = box.get("result") or (False, "没有拿到结果")
        self._say(("✓ " if ok else "✗ ") + str(msg))
        if ok and self._on_done:
            self._on_done()

    # ---- 按钮 ----

    def _rediscover(self):
        """mDNS 发现只要 100ms 出头（读的是 adb server 的缓存），同步跑不卡界面。"""
        found = wireless.discover()
        if found["pairing"]:
            self.addr_var.set(found["pairing"][0])
            self._say("发现一台正在等待配对的手机 —— 把手机上显示的 6 位数字填进「配对码」，"
                      "然后点「配对并连接」。")
            self.code_entry.focus_set()
        elif found["connect"]:
            self.addr_var.set(found["connect"][0])
            self._say("发现一台已经配对过的手机 —— 直接点「连接」。")
        else:
            self._say("暂时没发现设备（路由器可能禁止了组播）。先在手机上点"
                      "「使用配对码配对设备」，等它显示出 6 位数字后再点一次「重新发现」；"
                      "也可以直接把手机上的「IP 地址和端口」填进上面的地址栏。")

    def _connect(self):
        addr = wireless.normalize_addr(self.addr_var.get())
        if not addr:
            return self._say("请先填手机上「无线调试」显示的 IP 地址和端口")
        self.addr_var.set(addr)

        def work():
            ok, msg = wireless.connect_and_wait(addr)
            return ok, (f"已连接 {addr}" if ok else msg)

        self._run(work, f"正在连接 {addr}……")

    def _pair(self):
        addr = wireless.normalize_addr(self.addr_var.get())
        code = self.code_var.get().strip()
        if not addr:
            return self._say("请先填手机上「使用配对码配对设备」窗口里的 IP 地址和端口")
        if not code:
            return self._say("请先把手机上的 6 位配对码填进「配对码」")
        self._run(lambda: wireless.pair_and_connect(addr, code), "正在配对并连接……")

    def _disconnect(self):
        self._run(wireless.disconnect_all, "正在断开……")


# --------------------------------------------------------------------------
# 主窗口
# --------------------------------------------------------------------------


def root_usable(path):
    """这个目录能不能当备份根。

    「更多 → 把老照片归入设备」会在这个目录下找 <年>/<年-月-日>/ 结构并往里搬文件，
    根目录选得太靠上（盘根、用户主目录、Windows 目录）就可能动到用户自己的照片。
    只挡这几种，正常目录一律放行。
    """
    p = os.path.abspath(path).rstrip("\\/").lower()
    if re.fullmatch(r"[a-z]:", p):                     # 盘根，例如 D:\
        return False
    windir = (os.environ.get("SystemRoot") or r"c:\windows").rstrip("\\/").lower()
    home = os.path.expanduser("~").rstrip("\\/").lower()
    return p not in (home, windir) and not p.startswith(windir + os.sep)


def pick_root(parent, cfg):
    """备份目录：配过就用记住的那个，没配过（第一次运行）弹一次目录选择器。

    取消不能用「什么都不做」来收场 —— 落到默认位置，程序照常能用。
    """
    if cfg.get("root"):
        return cfg["root"]
    chosen = filedialog.askdirectory(
        parent=parent, mustexist=False,
        title="第一次使用：选一个文件夹用来存放手机照片（点取消则用默认位置）",
        initialdir=os.path.dirname(DEFAULT_ROOT))
    return os.path.normpath(chosen) if chosen else DEFAULT_ROOT


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("手机照片备份")
        apply_icon(self)
        self.geometry("1220x800")
        # 顶栏有「连接」下拉 + 刷新 + 无线 + 「查看」下拉 + 更多 + 开始备份，
        # 940px 会把「无线」压扁，实测需要约 952px，留点余量
        self.minsize(1000, 620)
        self.configure(bg=BG)

        cfg = load_config()
        self.root_dir = cfg.get("root") or DEFAULT_ROOT   # 真值在 UI 建好后定（见下）
        self.cache_dir = os.path.join(self.root_dir, library.THUMB_DIR)
        self.devices = []
        self.dev_labels = {}       # serial → {label, dir, ...}，认过一次就记住
        self.items = []            # 全库条目
        self.local_items = None    # 本机文件条目（None = 还没扫过，用到才扫）
        self.shown = []            # 当前「查看」筛选后的条目（大图翻页走这个）
        self.item_index = {}       # id(item) → 在 shown 里的下标，供大图窗口定位
        self.tree_data = {}
        self.tiles = []
        self.tile_widgets = []
        self.tile_cells = []
        self._pool = []            # 格子池：建一次反复用，切组时不重建 widget
        self._grid_cols = 0        # 池子当前是按几列排的、排了几个（没变就不动 grid）
        self._grid_count = 0
        self._empty_box = []
        self.photos = {}           # 原图路径 → PhotoImage（重画时直接命中）
        self.busy = False
        self._batch = None         # 多设备备份时的 (第几台, 共几台, 设备名)
        self.gen = 0
        self._cols = 4
        self.focus_idx = 0
        self._keep_scroll = False      # 重画时是否保住滚动位置（删除后用）
        self._suppress_select = False  # 程序性恢复选中时别重复重画网格
        self._thumb_thread = None
        self.q = queue.Queue()
        self._tour = None          # 新手指引的窗口（同一时刻只能有一个）

        self._init_style()
        self._build_ui()
        # 第一次运行（还没配过备份目录）在这里弹一次目录选择器。放在 _build_ui 之后：
        # 主窗口先画出来，选择器浮在它上面，用户不会对着一个空窗口发愣。
        self.update_idletasks()
        self.root_dir = pick_root(self, cfg)
        if cfg.get("root") != self.root_dir:
            cfg["root"] = self.root_dir
            save_config(cfg)
        self.after(80, self._drain)
        self._scan_devices()          # 只认手机；库交给下面那一次扫，别扫两遍
        self.refresh_library()
        # 每次启动都重扫一遍本机文件：它们随时可能被增删，跨次启动沿用旧列表
        # 会出现「新存的图看不见、删掉的还挂着」。实测 150 来个文件 0.2 秒。
        self._ensure_local()
        self.after(220, self._autoselect_first_month)
        # 保存 job id：自动化测试跑在真库上，得能把这声提示取消掉，否则会被模态框卡住
        self._migrate_job = self.after(1400, self._maybe_prompt_migrate)
        # 新手指引：排在迁移提示之前，第一次用时自动走一遍（800ms 等界面先画稳）
        self.after(800, self._maybe_show_tour)

    # ---------------- 样式 ----------------

    def _init_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")   # clam 才能定制 Treeview / Combobox 的颜色
        except tk.TclError:
            pass

        style.configure("Treeview",
                        background=BG, fieldbackground=BG, foreground=TEXT,
                        borderwidth=0, relief="flat",
                        font=theme.F_SMALL, rowheight=30)
        style.map("Treeview",
                  background=[("selected", theme.ACCENT_SOFT)],
                  foreground=[("selected", theme.ACCENT)])
        # 去掉 Treeview 自带的凹陷边框，只留内容区
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        style.configure("TCombobox",
                        fieldbackground=BG, background=BG, foreground=TEXT,
                        bordercolor=BORDER, arrowcolor=TEXT_DIM,
                        lightcolor=BG, darkcolor=BG,
                        borderwidth=1, relief="flat", padding=(8, 6))
        style.map("TCombobox",
                  fieldbackground=[("readonly", BG)],
                  bordercolor=[("focus", theme.ACCENT)],
                  arrowcolor=[("active", TEXT)])

        style.configure("Vertical.TScrollbar",
                        background=SURFACE_ALT, troughcolor=BG,
                        bordercolor=BG, arrowcolor=TEXT_FAINT,
                        darkcolor=SURFACE_ALT, lightcolor=SURFACE_ALT,
                        relief="flat", arrowsize=12, width=10)
        style.map("Vertical.TScrollbar",
                  background=[("active", BORDER)])

        style.configure("TPanedwindow", background=BG)
        style.configure("Sash", sashthickness=10, gripcount=0, background=BG)

    # ---------------- 布局 ----------------

    def _build_ui(self):
        # 顶栏
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=theme.S5, pady=(theme.S4, theme.S3))

        left = tk.Frame(top, bg=BG)
        left.pack(side="left", anchor="w", padx=(0, theme.S5))   # 窄窗口下也要与右侧留白
        tk.Label(left, text="手机照片备份", font=theme.F_TITLE, bg=BG,
                 fg=TEXT).pack(anchor="w")
        self.root_lbl = tk.Label(left, text="", font=theme.F_TINY, bg=BG, fg=TEXT_FAINT)
        self.root_lbl.pack(anchor="w", pady=(2, 0))

        right = tk.Frame(top, bg=BG)
        right.pack(side="right", anchor="e")

        self.backup_btn = RoundButton(right, "开始备份", self.start_backup,
                                      primary=True, parent_bg=BG, height=34, padx=22)
        self.backup_btn.pack(side="right")
        self.more_btn = RoundButton(right, "更多 ▾", self._open_more, parent_bg=BG,
                                    height=34, padx=14)
        self.more_btn.pack(side="right", padx=(0, theme.S2))

        # 看哪台设备的库（跟左边「连哪台手机」是两回事，所以各带一个前缀字）
        lib = tk.Frame(right, bg=BG)
        lib.pack(side="right", padx=(0, theme.S3))
        tk.Label(lib, text="查看", font=theme.F_TINY, bg=BG, fg=TEXT_FAINT).pack(
            side="left", padx=(0, theme.S1))
        self.lib_picker = DevicePicker(lib, width=168, height=34, parent_bg=BG,
                                       on_pick=lambda _v: self._rebuild_library_view())
        self.lib_picker.pack(side="left")

        dev = self.dev_box = tk.Frame(right, bg=BG)
        dev.pack(side="right", padx=(0, theme.S2))
        tk.Label(dev, text="连接", font=theme.F_TINY, bg=BG, fg=TEXT_FAINT).pack(
            side="left", padx=(0, theme.S1))
        self.device_picker = DevicePicker(dev, width=186, height=34, parent_bg=BG)
        self.device_picker.pack(side="left")
        RoundButton(dev, "刷新", self.refresh_devices, parent_bg=BG,
                    height=34, padx=14).pack(side="left", padx=(theme.S2, 0))
        RoundButton(dev, "无线", self.open_wireless, parent_bg=BG,
                    height=34, padx=14).pack(side="left", padx=(theme.S2, 0))

        # 状态栏（先 pack 底部，主体才能正确 expand）
        bar = tk.Frame(self, bg=SURFACE, height=36)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, textvariable=self._status_var(), font=theme.F_SMALL,
                 bg=SURFACE, fg=TEXT_DIM, anchor="w").pack(
                     side="left", padx=(theme.S5, theme.S3))
        self.progress = tk.Canvas(bar, width=150, height=4, bg=SURFACE,
                                  highlightthickness=0, bd=0)
        self.progress.pack(side="left")
        self.right_lbl = tk.Label(bar, text="", font=theme.F_SMALL, bg=SURFACE,
                                  fg=TEXT_FAINT, anchor="e")
        self.right_lbl.pack(side="right", padx=theme.S5)
        theme.hline(self, theme.DIVIDER).pack(side="bottom", fill="x")

        # 主体
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=theme.S5, pady=(0, theme.S4))

        paned = ttk.PanedWindow(body, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # 左：年月导航
        nav = self.nav_card = tk.Frame(paned, bg=BG, width=248)
        nav.pack_propagate(False)
        nav_card = tk.Frame(nav, bg=BG, highlightthickness=1,
                            highlightbackground=BORDER_SOFT)
        nav_card.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(nav_card, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True, padx=theme.S2, pady=theme.S3)
        self.tree.tag_configure("year", font=theme.F_MED, foreground=TEXT)
        self.tree.tag_configure("month", font=theme.F_SMALL, foreground=TEXT_DIM)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        paned.add(nav, weight=0)

        # 右：缩略图网格
        grid_host = tk.Frame(paned, bg=BG)
        self.canvas = tk.Canvas(grid_host, bg=BG, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(grid_host, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(theme.S2, 0))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = tk.Frame(self.canvas, bg=BG)
        self._grid_window = self.canvas.create_window((0, 0), window=self.grid_frame,
                                                      anchor="nw")
        self.grid_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._relayout)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        # 键盘导航：焦点在网格上时方向键移动、回车看大图
        self.canvas.bind("<Left>", lambda e: self._move_focus(-1, 0))
        self.canvas.bind("<Right>", lambda e: self._move_focus(1, 0))
        self.canvas.bind("<Up>", lambda e: self._move_focus(0, -1))
        self.canvas.bind("<Down>", lambda e: self._move_focus(0, 1))
        self.canvas.bind("<Return>", lambda e: self.open_viewer(self.focus_idx))
        paned.add(grid_host, weight=1)

        self._set_status("就绪")

    def _status_var(self):
        if not hasattr(self, "_status"):
            self._status = tk.StringVar(value="就绪")
        return self._status

    # ---------------- 状态 ----------------

    def _set_status(self, text):
        self._status_var().set(text)

    def _set_right(self, text):
        self.right_lbl.config(text=text)

    def _set_progress(self, frac):
        """状态栏里的细进度条：frac=None 隐藏，0~1 显示。"""
        self.progress.delete("all")
        if frac is None:
            return
        frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
        w, h = 150, 4
        round_rect(self.progress, 0, 0, w, h, h / 2, fill=SURFACE_ALT, outline="")
        round_rect(self.progress, 0, 0, max(h, w * frac), h, h / 2,
                   fill=theme.ACCENT, outline="")

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _autoselect_first_month(self):
        """打开就显示一组照片，而不是给用户一个空白网格。"""
        if self.tree.selection():
            return
        for year in self.tree.get_children():
            kids = self.tree.get_children(year)
            if kids:
                self.tree.item(year, open=True)
                self.tree.selection_set(kids[0])
                self.tree.see(kids[0])
                return

    # ---------------- 设备与备份 ----------------

    def refresh_devices(self):
        """顶栏「刷新」：本地库和手机一起重扫。

        以前这里只重新识别手机，新落进库里的文件得走「更多 → 重新扫描本地库」
        才看得到 —— 点了「刷新」却不见新照片，很容易让人以为压根没扫到。
        """
        self.refresh_library()
        self._scan_devices()

    def _scan_devices(self):
        """只重新检测手机连接（「重新读取手机信息」走这个，不动本地库）。"""
        try:
            devs = [s for s, state in backup.list_devices() if state == "device"]
        except Exception as exc:  # noqa: BLE001
            devs = []
            self._set_status(f"adb 不可用：{exc}")
        self.devices = devs
        self._paint_devices()
        if devs:
            self._set_status(f"已连接 {len(devs)} 台设备")
            for serial in devs:
                if serial not in self.dev_labels:
                    threading.Thread(target=self._probe_device, args=(serial,),
                                     daemon=True).start()
        else:
            self._set_status("未检测到手机：插数据线并开 USB 调试，或点「无线」用 Wi-Fi 连接")

    def _paint_devices(self):
        """设备下拉里显示设备名；还没认出来就先显示 serial。"""
        items = []
        for serial in self.devices:
            info = self.dev_labels.get(serial) or {}
            kind = "无线" if wireless.is_wireless(serial) else "USB"
            items.append((serial, f"{info.get('label') or serial} · {kind}"))
        self.device_picker.set_items(items, empty_text="未检测到手机")

    def _probe_device(self, serial):
        """后台认一下这是哪台手机（只读 getprop / settings get），认出来就记住。

        记住的 key 是 android_id 而不是 serial —— 同一台手机 USB 插着和无线连着
        serial 完全不同，但 android_id 一样，这样换连接方式也不会被当成新设备。
        """
        try:
            info = backup.device_info(serial)
        except Exception:      # noqa: BLE001
            return
        info["label"] = backup.device_label(info)
        info["key"] = backup.device_key(info)
        cfg = load_config()
        devices = cfg.get("devices") or {}
        known = devices.get(info["key"]) or {}
        # 用户改过名就用他改的，否则按市场名生成一个
        info["dir"] = known.get("dir") or backup.device_dir_name(info)
        self.dev_labels[serial] = info
        devices[info["key"]] = {"label": info["label"], "dir": info["dir"]}
        cfg["devices"] = devices
        save_config(cfg)
        self.q.put(("device_info", serial))

    def _forget_devices(self):
        """忘掉认过的设备信息，重新读一遍。"""
        self.dev_labels.clear()
        self._scan_devices()

    # ---------------- 新手指引 ----------------

    def _maybe_show_tour(self, tries=0):
        """只在第一次用时自动走一遍；走过就记在 config 里，不再打扰。"""
        if self._tour is not None:
            return
        if load_config().get("tour_done"):
            return
        # 已经开着模态框（比如启动时的迁移提示）就先让开：Tk 的本地 grab 只有一个，
        # 谁后抓谁生效 —— 硬弹上去只会把自己的按钮抓得失灵。最多等 10 秒。
        if self.grab_current() is not None and tries < 20:
            self.after(500, lambda: self._maybe_show_tour(tries + 1))
            return
        self.show_tour()

    def show_tour(self):
        """「更多 ▾ → 使用指引」随时能重看，所以单独开一个入口。"""
        if self._tour is not None:
            return
        self._tour = welcome.Tour(
            self,
            {"dev": self.dev_box, "backup": self.backup_btn,
             "more": self.more_btn, "nav": self.nav_card},
            on_done=self._tour_done)
        self._tour.show()

    def _tour_done(self):
        self._tour = None
        cfg = load_config()
        cfg["tour_done"] = 1
        save_config(cfg)

    def _open_more(self):
        """把不常用的几个动作收进这里，给顶栏腾地方。"""
        PopupMenu(self, [
            ("使用指引", self.show_tour),
            ("重新扫描本地库", self.refresh_library),
            ("更改备份目录…", self.choose_root),
            ("打开备份目录", self.open_root),
            "-",
            ("重命名设备目录…", self.rename_device),
            ("把老照片归入设备…", self.migrate_legacy),
            ("重新读取手机信息", self._forget_devices),
        ], self.more_btn.winfo_rootx(),
            self.more_btn.winfo_rooty() + self.more_btn.winfo_height() + 5)

    def rename_device(self):
        """给某台设备的库目录改个名；config 里的映射跟着改，下次备份不会另建一个。"""
        devs = sorted({it["device"] for it in self.items
                       if it["device"] not in (library.LEGACY_DEVICE,
                                               library.UNKNOWN_DEVICE)})
        if not devs:
            Dialog.info(self, "库里还没有设备目录", "先备份一台手机，再来改名。")
            return
        if len(devs) == 1:
            old = devs[0]
        else:
            picked = theme.PickDialog.ask(
                self, "给哪台设备改名？", [(d, d, "") for d in devs],
                note="只取你勾的第一台。", confirm="下一步")
            if not picked:
                return
            old = picked[0]

        new = theme.InputDialog.ask(
            self, "重命名设备目录", value=old,
            note=f"现在叫：{old}\n\n名字里不能出现 \\ / : * ? \" < > | 这些字符。",
            confirm="改名")
        if not new or new == old:
            return
        new = re.sub(r'[\\/:*?"<>|]+', "-", new).strip("-.")
        if not new:
            Dialog.warn(self, "这个名字不能用", "里面没有可用的字符。")
            return

        src = os.path.join(self.root_dir, old)
        dst = os.path.join(self.root_dir, new)
        if not os.path.isdir(src):
            Dialog.warn(self, "找不到这个目录", f"{src}\n可能已经被移走或改过名了。")
            return
        if os.path.exists(dst):
            Dialog.warn(self, "名字被占了", f"{new}\n这个目录已经存在。")
            return
        self._stop_thumbs()          # 别让正在读图的后台线程把目录占住
        try:
            os.rename(src, dst)
        except OSError as exc:
            Dialog.error(self, "改名失败", f"{type(exc).__name__}: {exc}")
            return

        cfg = load_config()
        for val in (cfg.get("devices") or {}).values():
            if isinstance(val, dict) and val.get("dir") == old:
                val["dir"] = new
        save_config(cfg)
        for info in self.dev_labels.values():
            if info.get("dir") == old:
                info["dir"] = new
        self.refresh_library()
        self._set_status(f"设备目录已改名为 {new}")

    def open_wireless(self):
        """打开无线连接面板；连上/断开后顺手刷新设备下拉。"""
        WirelessPanel(self, on_done=self._scan_devices).show()

    def start_backup(self):
        if self.busy:
            return
        if not self.devices:
            self._scan_devices()
        devs = list(self.devices)
        if not devs:
            Dialog.warn(self, "没有可用的手机",
                        "请插好数据线并打开 USB 调试，或者点「无线」用 Wi-Fi 连接手机。")
            return
        if len(devs) > 1:
            options = []
            for serial in devs:
                info = self.dev_labels.get(serial) or {}
                kind = "无线" if wireless.is_wireless(serial) else "USB"
                options.append((serial, info.get("label") or serial, f"{kind} · {serial}"))
            picked = theme.PickDialog.ask(
                self, "选择要备份的设备", options,
                note="会按顺序一台一台地备份。中途某台掉线只停那一台，其余的继续。",
                confirm="开始备份")
            if not picked:
                return
        else:
            picked = devs
        self.busy = True
        self.backup_btn.set_enabled(False)
        self._batch = None
        self._set_status("正在读取手机媒体库……")
        threading.Thread(target=self._backup_worker, args=(picked,), daemon=True).start()

    def _backup_worker(self, serials):
        """依次备份选中的设备。一台掉线只停那一台，剩下的照跑。"""
        results = []
        for i, serial in enumerate(serials, 1):
            info = self.dev_labels.get(serial) or {}
            entry = {"serial": serial, "dir": info.get("dir") or serial,
                     "label": info.get("label") or serial, "summary": None,
                     "error": None, "lost": False}
            self.q.put(("evt", ("device_start", {"idx": i, "total": len(serials),
                                                 "label": entry["label"]})))
            try:
                entry["summary"] = backup.do_backup(
                    self.root_dir, serial, self._emit, device_dir=entry["dir"])
            except backup.DeviceLost as exc:
                entry["error"], entry["lost"] = str(exc), True
            except Exception as exc:      # noqa: BLE001
                entry["error"] = str(exc)
            results.append(entry)
        self.q.put(("backup_all_done", results))

    def _emit(self, kind, payload=None):
        self.q.put(("evt", (kind, payload)))

    # ---------------- 本地库 ----------------

    def refresh_library(self):
        """重扫磁盘 + 重建视图。"""
        self.root_lbl.config(text=self.root_dir)
        self.cache_dir = os.path.join(self.root_dir, library.THUMB_DIR)
        self.items = library.scan(self.root_dir)
        self.local_items = None         # 重扫 = 本机文件也跟着重新扫一遍
        self._rebuild_library_view()

    def _ensure_local(self):
        """第一次用到「本机文件」时才去扫这台电脑，之后一直用缓存。

        扫的是用户目录下那几个库目录，实测 150 来个文件、0.2 秒就完事。不放在启动时
        做，是因为不点「本机文件」的人没必要为它等这一下。
        """
        if self.local_items is not None:
            return
        self._set_status("正在扫描本机文件……")
        self.update_idletasks()
        # 把备份根排除掉：备份过的（手机上的）不算「本机文件」
        self.local_items = library.scan_local(exclude=[self.root_dir])

    def _rebuild_library_view(self):
        """只重建视图，不重扫盘 —— 切换「查看」的设备时走这里，几万个文件也不用等。"""
        # 「查看」下拉：全部设备 / 全部（含本机文件）/ 本机文件 / 库里实际出现过的每一台
        devs = sorted({it["device"] for it in self.items})
        self.lib_picker.set_items([ALL_DEVICES, ALL_WITH_LOCAL, library.LOCAL_DEVICE] + devs,
                                  empty_text=ALL_DEVICES, default=library.LOCAL_DEVICE)
        sel = self.lib_picker.value or ALL_DEVICES
        if sel in (ALL_WITH_LOCAL, library.LOCAL_DEVICE):
            self._ensure_local()        # 用到才扫；扫完才有东西可显示
        local = self.local_items or []
        if sel == ALL_WITH_LOCAL:
            # 两个来源混成一条时间轴（两边各自都是倒序的，混起来得重排一次）
            self.shown = sorted(self.items + local,
                                key=lambda it: (it["timestamp"], it["name"]),
                                reverse=True)
        elif sel == library.LOCAL_DEVICE:
            self.shown = list(local)
        elif sel == ALL_DEVICES:
            self.shown = list(self.items)
        else:
            self.shown = [it for it in self.items if it["device"] == sel]
        self.item_index = {id(it): i for i, it in enumerate(self.shown)}
        self.tree_data = library.group_tree(self.shown)
        # 副标题只留短文案：完整路径太长时会把顶栏按钮挤扁（1000px 宽下实测会）
        self.root_lbl.config(text=("本机文件 · 只读展示"
                                   if sel == library.LOCAL_DEVICE else self.root_dir))
        self._rebuild_tree()
        if self.tree.selection():          # 换「查看」的设备后网格也要跟着换
            self.on_select()

        if self.shown:
            size = sum(it["size"] for it in self.shown)
            scope = "" if sel == ALL_DEVICES else f"{sel} · "
            self._set_status(f"{scope}库内 {len(self.shown)} 个媒体文件 · "
                             f"{library.human_size(size)}")
            days = sorted({it["day"] for it in self.shown
                           if it["day"] != library.UNKNOWN})
            self._set_right(f"{days[0]} — {days[-1]}" if days else "")
        elif sel == library.LOCAL_DEVICE:
            self._set_status("本机没找到图片或视频")
            self._set_right("")
            self._show_empty("本机没找到图片或视频",
                             "只扫「图片 / 桌面 / 下载 / 文档 / 视频」这几个目录")
        else:
            self._set_status("库里还没有照片")
            self._set_right("")
            self._show_empty("还没有照片",
                             "把手机插上、开启 USB 调试，然后点右上角「开始备份」")

    def _rebuild_tree(self):
        """重建左侧时间柱。

        重建后把原来的选中项恢复回去（那个节点没了就退到时间上最近的一组）——
        以前这里把选中清空就不管了，于是「移到回收站」删掉一张之后，网格还停在
        旧画面上，得手动再点一下那个日期才刷新。
        """
        keep = (self.tree.selection() or [None])[0]
        self.tree.delete(*self.tree.get_children())
        for year, months in self.tree_data.items():
            count = sum(len(v) for v in months.values())
            ynode = self.tree.insert("", "end", iid=f"y:{year}",
                                     text=f"{year}  ·  {count}", tags=("year",))
            for month, arr in months.items():
                self.tree.insert(ynode, "end", iid=f"m:{month}",
                                 text=f"{month}  ·  {len(arr)}", tags=("month",))
        target = keep if keep and self.tree.exists(keep) else self._nearest_iid(keep)
        self._suppress_select = True
        try:
            if target:
                self.tree.selection_set(target)
                self.tree.see(target)      # see() 会顺带展开所在的年份
        finally:
            self._suppress_select = False

    def _nearest_iid(self, keep):
        """原选中项没了 → 退到时间上最近的一组。

        tree 里的节点本来就是时间倒序，所以「比 keep 早的里面第一个」就是最近的那个。
        """
        months = [m for y in self.tree.get_children()
                  for m in self.tree.get_children(y)]
        if not months:
            return None
        if not keep or keep.startswith("m:"):
            older = [m for m in months if keep and m < keep]
            return older[0] if older else months[0]
        years = list(self.tree.get_children())
        older = [y for y in years if y < keep]
        return older[0] if older else years[0]

    def on_select(self, _event=None):
        if self._suppress_select:      # 重建时间柱时程序性恢复选中，别跟着重画一遍
            return
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("m:"):
            month = iid[2:]
            items = self.tree_data.get(month[:4], {}).get(month, [])
        else:
            year = iid[2:]
            items = [it for arr in self.tree_data.get(year, {}).values() for it in arr]
        self.show_group(items)

    # ---------------- 网格 ----------------

    def _show_empty(self, title, hint):
        self._fill_grid([], empty=(title, hint))

    def _calc_cols(self):
        width = max(self.canvas.winfo_width(), 260)
        return max(2, width // (CELL + 2 * theme.S2))

    def _relayout(self, _event=None):
        if not self.tile_widgets:
            return
        cols = self._calc_cols()
        if cols == self._cols:
            return
        self._cols = cols
        self._grid_cols = cols
        for i, cell in enumerate(self.tile_cells):
            cell.grid(row=i // cols, column=i % cols, padx=theme.S2, pady=theme.S2)

    def show_group(self, items):
        self._fill_grid(items, keep_scroll=self._keep_scroll)
        self._keep_scroll = False

    def _fill_grid(self, items, keep_scroll=False,
                   empty=("这一组是空的", "换一个月份看看")):
        """重画网格。

        两个关键点：

        1. **格子复用**。每个格子是个 Frame + Label，一次切换要动 800 个 widget；真的
           建/销一遍要接近一秒，这就是切月份卡顿的主因。所以格子只建一次放进池子，
           之后切组只改内容、不重建（`idx` 是池子里的固定槽位，绑定也只挂一次）。
        2. **一次刷新**。整个过程不调 update，改完内容才 update_idletasks 一次，
           所以屏幕上看不到「先空一下、再冒出来」的中间态。以前是靠把内嵌 frame
           从 canvas 上摘下来再挂回去来遮，但那是 800 个 widget 的重新映射，
           光这一下就 180ms —— 池化之后旧内容压根不会先消失，遮的必要也没有了。

        缩略图按路径缓存（self.photos），所以重画已在缓存里的组直接命中，不重新解码。
        """
        frac = self.canvas.yview()[0] if keep_scroll else 0.0
        for w in self._empty_box:                # 上一轮的空状态盒
            w.destroy()
        self._empty_box = []
        self.tiles = items[:MAX_TILES]
        if not self.tiles:
            self.tile_widgets = []
            self.tile_cells = []
            for _cell, _lbl in self._pool:       # 池子全体退场
                _cell.grid_remove()
            self._grid_count = 0                 # 记着「一格都没排」
            box = tk.Frame(self.grid_frame, bg=BG)
            box.grid(row=0, column=0, pady=(90, 0))
            tk.Label(box, text="◫", font=(theme.FAMILY, 44), bg=BG,
                     fg="#E4E4E8").pack()
            tk.Label(box, text=empty[0], font=theme.F_MED, bg=BG,
                     fg=TEXT_DIM).pack(pady=(theme.S3, theme.S1))
            tk.Label(box, text=empty[1], font=theme.F_SMALL, bg=BG, fg=TEXT_FAINT,
                     justify="center").pack()
            self._empty_box = [box]
            self.gen += 1                # 让上一组的缩略图线程收工，别再白干
            self.canvas.update_idletasks()
            return

        cols = self._cols = self._calc_cols()
        n = len(self.tiles)
        while len(self._pool) < n:               # 只在第一次（或组更大时）补建
            self._pool.append(self._make_tile(len(self._pool)))
        self.tile_cells = [c for c, _l in self._pool[:n]]
        self.tile_widgets = [l for _c, l in self._pool[:n]]
        for i, it in enumerate(self.tiles):
            lbl = self._pool[i][1]
            online = bool(it.get("online_only"))
            # 「仅在线」的一律当没图：缓存里可能还留着它上次读出来的样子，
            # 但现在本地根本读不到（网盘客户端可能已经关了）
            photo = None if online else self.photos.get(it["path"])
            # 格子里现在是哪张图（None = 占位）——没变就一个字都不用改，
            # 每格一次 Tcl 往返，几百格累积起来就是可见的卡顿
            want = it["path"] if photo is not None else None
            if lbl._shown != want or lbl._online != online:
                if photo is None:
                    text = ("☁ 仅在线" if online
                            else ("▶ 视频" if it["is_video"] else "…"))
                    lbl.config(image="", text=text, bg=SURFACE_ALT)
                    lbl.place_configure(width=THUMB_BOX, height=THUMB_BOX)
                else:
                    lbl.config(image=photo, text="", bg=BG)
                    lbl.place_configure(width=photo.width(), height=photo.height())
                lbl._shown = want
                lbl._online = online
        # 只有排布真的变了才动 grid：池子槽位固定，列数和格数没变位置就一定对。
        # 以前每格都问一次 grid_info()，那就是每格一次 Tcl 往返，顶得上整个切换的时间
        if (cols, n) != (self._grid_cols, self._grid_count):
            for i in range(n):
                self._pool[i][0].grid(row=i // cols, column=i % cols,
                                      padx=theme.S2, pady=theme.S2)
            for i in range(n, self._grid_count):   # 这一组用不完的槽位退场
                self._pool[i][0].grid_remove()
            self._grid_cols, self._grid_count = cols, n
        # 缩略图只留最近两组：来回切月份不用把 400 张图重新解码一遍。
        # 代价是常驻内存（约 110KB/张，两组最多 90MB），换来的是切回去立刻有图。
        limit = 2 * MAX_TILES
        if len(self.photos) > limit:
            for p in list(self.photos)[:len(self.photos) - limit]:
                del self.photos[p]

        self.canvas.update_idletasks()
        self.canvas.yview_moveto(frac)
        if not keep_scroll:
            self._set_focus(0)
        if len(items) > MAX_TILES:
            self._set_status(f"本组共 {len(items)} 个 · 仅显示最近 {MAX_TILES} 个")

        self.gen += 1
        self._thumb_thread = threading.Thread(target=self._thumb_worker,
                                              args=(self.gen, list(self.tiles)),
                                              daemon=True)
        self._thumb_thread.start()

    def _make_tile(self, idx):
        """建一个格子（含全部绑定）放进池子。

        idx 是池子里的固定槽位，所以绑定只挂一次；回调里靠 self.tiles[idx] 取当前数据。
        """
        cell = tk.Frame(self.grid_frame, bg=BG, width=CELL, height=CELL,
                        highlightthickness=1, highlightbackground=BORDER_SOFT)
        cell.grid_propagate(False)     # 固定格子尺寸，图片在格内居中
        lbl = tk.Label(cell, bg=SURFACE_ALT, fg=TEXT_FAINT, font=theme.F_TINY,
                       cursor="hand2")
        lbl._shown = None              # 当前显示的是哪张图（None = 占位块）
        lbl._online = False            # 占位块是不是「只存在云上」的那种
        lbl.place(relx=0.5, rely=0.5, anchor="center",
                  width=THUMB_BOX, height=THUMB_BOX)
        for w in (cell, lbl):
            w.bind("<Double-Button-1>", lambda e, i=idx: self.open_viewer(i))
            w.bind("<Button-3>", lambda e, i=idx: self.tile_menu(e, i))
            w.bind("<Button-1>", lambda e, i=idx: self._grab_focus(i))
            w.bind("<Enter>", lambda e, c=cell, i=idx: c.configure(
                highlightbackground=theme.ACCENT if i == self.focus_idx else BORDER))
            w.bind("<Leave>", lambda e, c=cell, i=idx: c.configure(
                highlightbackground=theme.ACCENT if i == self.focus_idx else BORDER_SOFT))
        return cell, lbl

    def _stop_thumbs(self):
        """停掉后台缩略图线程并等它收工。

        它每张图都要打开库里的文件；Windows 下目录里有文件正被读取，目录就改不了名、
        移不了位。所以「重命名设备目录」「把老照片归入设备」这类动目录的操作前必须先停。
        """
        self.gen += 1                 # 线程下次循环自己就退出了
        th = self._thumb_thread
        if th is not None and th.is_alive():
            th.join(timeout=10)       # 最多等它把手上那一张做完

    def _grab_focus(self, idx):
        self.canvas.focus_set()
        self._set_focus(idx)

    def _move_focus(self, dx, dy):
        if not self.tiles:
            return
        target = self.focus_idx + dx + dy * self._cols
        if 0 <= target < len(self.tiles):
            self._set_focus(target)

    def _set_focus(self, idx):
        """键盘焦点格用强调色边框标出，并保证它滚进可视区域。"""
        if not self.tile_cells:
            return
        idx = max(0, min(idx, len(self.tile_cells) - 1))
        prev, self.focus_idx = getattr(self, "focus_idx", 0), idx
        for i in (prev, idx):
            if 0 <= i < len(self.tile_cells):
                self.tile_cells[i].configure(
                    highlightbackground=theme.ACCENT if i == idx else BORDER_SOFT)
        cell = self.tile_cells[idx]
        self.canvas.update_idletasks()
        total = max(self.grid_frame.winfo_height(), 1)
        top = self.canvas.canvasy(0)
        view = self.canvas.winfo_height()
        y, h = cell.winfo_y(), cell.winfo_height()
        if y < top:
            self.canvas.yview_moveto(y / total)
        elif y + h > top + view:
            self.canvas.yview_moveto(max(0.0, (y + h - view) / total))

    def _thumb_worker(self, gen, tiles):
        for i, it in enumerate(tiles):
            if self.gen != gen:
                return
            if it.get("online_only"):
                # 只存在云上、本地没数据：读不出来，别白费力气也别刷 error.log。
                # 格子上显示「☁ 仅在线」，用户开了网盘客户端重扫就能看。
                continue
            if it["path"] in self.photos:     # 缓存里已经有了，格子上次就填好了
                continue
            try:
                path = library.thumb(it["path"], self.cache_dir, THUMB_BOX)
            except Exception as exc:      # noqa: BLE001 —— 一张图坏了不能毁掉整组
                library.log_error("缩略图生成", it["path"], exc)
                path = None
            self.q.put(("thumb", (gen, i, path)))

    def open_viewer(self, idx):
        """从缩略图打开大图。

        传的是**当前「查看」筛选后的** items + 它在该列表里的下标 —— 所以窗口里
        ←/→ 能一路翻过全天 → 全月 → 全年，但不会翻到别的手机的照片上去；
        也顺带绕开了网格最多只渲染 MAX_TILES 个格子的上限。
        """
        if not self.tiles:
            return
        Viewer(self, self.shown, self.item_index.get(id(self.tiles[idx]), 0))

    def tile_menu(self, event, idx):
        it = self.tiles[idx]
        local = it["device"] == library.LOCAL_DEVICE
        PopupMenu(self, [
            # 复制文件本身（CF_HDROP）：资源管理器里 Ctrl+V 就是一份副本
            ("复制", lambda: self.copy_tiles([it])),
            ("用系统程序打开", lambda p=it["path"]: os.startfile(p)),
            # 别用 os.system：windowed 程序下会闪一个黑框，路径里的引号也得自己拼
            ("在资源管理器中定位", lambda p=it["path"]: subprocess.Popen(
                ["explorer", f"/select,{p}"], creationflags=0x08000000)),
            "-",
            # 本机文件就是电脑上的原文件（不是手机的副本），文案不能照抄
            ("移到回收站" if local else "移到回收站（仅电脑上的副本）",
             lambda: self.recycle_one(idx), "danger"),
        ], event.x_root, event.y_root)

    def copy_tiles(self, items):
        """把文件放进剪贴板：资源管理器里 Ctrl+V 就能粘出一份副本。"""
        ok, info = library.copy_to_clipboard([it["path"] for it in items])
        if not ok:
            Dialog.error(self, "复制失败", str(info))
            return
        self._set_status(f"已复制 {info} 个文件 · 到资源管理器里 Ctrl+V 粘贴副本")

    def _remove_item(self, it):
        """从内存索引里摘掉一个条目（本地副本进了回收站后调用，不重扫磁盘）。

        全库 2500+ 个文件重新 scandir 一圈要好几秒，而这里只少了一个文件；
        顺带让时间柱同步收缩 —— 某个月/某一年空了就直接从柱子上消失。
        """
        self.items = [x for x in self.items if x is not it]
        if self.local_items:
            self.local_items = [x for x in self.local_items if x is not it]
        self.shown = [x for x in self.shown if x is not it]
        self.item_index = {id(x): i for i, x in enumerate(self.shown)}
        for year in list(self.tree_data):
            months = self.tree_data[year]
            for month in list(months):
                if any(x is it for x in months[month]):
                    rest = [x for x in months[month] if x is not it]
                    if rest:
                        months[month] = rest
                    else:
                        del months[month]
            if not months:
                del self.tree_data[year]
        self._rebuild_tree()

    def recycle_one(self, idx):
        it = self.tiles[idx]
        local = it["device"] == library.LOCAL_DEVICE
        if Dialog.ask(self, "移到回收站",
                      f"把这张移到回收站？\n\n{it['name']}\n\n"
                      + ("这是这台电脑上的原文件，会进 Windows 回收站，"
                         "想反悔可以从回收站还原。"
                         if local else
                         "只影响电脑上的副本，手机上的原图不会被改动。"),
                      kind="warn", buttons=["取消", "确定"]) != 1:
            return
        ok, info = library.recycle([it["path"]])
        if not ok:
            Dialog.error(self, "移到回收站失败", str(info))
            return
        self._set_status(f"已移到回收站：{it['name']}")
        # 立刻从画面上拿掉：不重扫全库，也不等用户再点一次那个日期
        self._remove_item(it)
        self._keep_scroll = True
        if self.tree.selection():
            self.on_select()
        else:
            self.show_group([])

    # ---------------- 目录 ----------------

    def choose_root(self):
        chosen = filedialog.askdirectory(initialdir=self.root_dir, title="选择备份目录")
        if not chosen:
            return
        chosen = os.path.normpath(chosen)
        if not root_usable(chosen):
            Dialog.warn(self, "这个目录不能当备份目录",
                        f"{chosen}\n\n"
                        "备份目录要是一个普通的文件夹（比如 D:\\PhonePhotos）。\n"
                        "盘根目录、用户主目录、Windows 目录都不行 —— 把老照片归入设备"
                        "时会动到那底下的东西。")
            return
        self.root_dir = chosen
        cfg = load_config()
        cfg["root"] = self.root_dir
        save_config(cfg)        # 只改 root，别把记住的设备信息冲掉
        self.refresh_library()

    def open_root(self):
        if os.path.isdir(self.root_dir):
            os.startfile(self.root_dir)
        else:
            Dialog.info(self, "目录不存在",
                        f"{self.root_dir}\n\n点「开始备份」会自动创建。")

    # ---------------- 把老结构归入设备 ----------------

    def _legacy_items(self):
        return [it for it in self.items if it["device"] == library.LEGACY_DEVICE]

    def _maybe_prompt_migrate(self):
        """启动后如果库里还有按老结构放的（没归到设备下的）照片，问一次。"""
        legacy = self._legacy_items()
        if self.busy or not legacy:
            return
        pick = Dialog.ask(
            self, "有照片还没归入设备",
            f"库里有 {len(legacy)} 个文件还是按老结构放的"
            "（<备份目录>\\2024\\2024-03-15\\…），没区分是哪台手机拍的。\n\n"
            "要不要现在把它们归入设备目录？只移动电脑上的副本，手机不受任何影响。",
            kind="info", buttons=["以后再说", "现在归入"])
        if pick == 1:
            self.migrate_legacy()

    def _pick_migration_device(self):
        """这些老照片是哪台手机的 —— 认得出就自动用，认不出就让用户选。"""
        dirs = [v["dir"] for v in self.dev_labels.values() if v.get("dir")]
        if not dirs:
            Dialog.warn(self, "先连上手机",
                        "归类需要知道这些照片是哪台手机拍的。\n"
                        "请先用数据线或点「无线」连上手机，再回来点这个菜单。")
            return None
        if len(dirs) == 1:
            return dirs[0]
        picked = theme.PickDialog.ask(
            self, "这些照片是哪台手机的？", [(d, d, "勾一台就行") for d in dirs],
            note="只取你勾的第一台。", confirm="继续")
        return picked[0] if picked else None

    def migrate_legacy(self):
        """把老结构（<根>/年/日/）搬到 <根>/<设备>/<类别>/年/日/。"""
        legacy = self._legacy_items()
        if not legacy:
            Dialog.info(self, "不需要归类", "库里没有需要归类的老结构照片。")
            return
        target = self._pick_migration_device()
        if not target:
            return

        moves = backup.plan_migration(self.root_dir, target)
        if not moves:
            Dialog.info(self, "没有可搬的文件", "老结构目录里没找到媒体文件。")
            return

        cats = {}
        for _src, _dst, cat in moves:
            cats[cat] = cats.get(cat, 0) + 1
        detail = "\n".join(f"      {c}   {n} 个" for c, n in sorted(cats.items()))
        pick = Dialog.ask(
            self, "把老照片归入设备",
            f"目标设备目录：{target}\n\n"
            f"共 {len(moves)} 个文件要移动，按类别分：\n{detail}\n\n"
            f"  从  <备份目录>\\2024\\2024-03-15\\IMG_xxx.jpg\n"
            f"  到  <备份目录>\\{target}\\Camera\\2024\\2024-03-15\\IMG_xxx.jpg\n\n"
            "• 只移动电脑上的副本，手机完全不受影响\n"
            "• 目标位置已有同名文件就跳过，绝不覆盖\n"
            "• 搬完只清理空掉的日期目录，不删任何照片",
            kind="warn", buttons=["取消", f"移动这 {len(moves)} 个文件"])
        if pick != 1:
            return

        self.busy = True
        self.backup_btn.set_enabled(False)
        self._set_status(f"正在归入设备 {target}……")
        self._stop_thumbs()          # 同上：搬运前先让读图的线程收工
        threading.Thread(target=self._migrate_worker, args=(moves,), daemon=True).start()

    def _migrate_worker(self, moves):
        def on_event(kind, payload=None):
            if kind == "moved":
                self.q.put(("evt", ("migrated", payload)))

        done, errors, removed = backup.apply_migration(moves, on_event)
        self.q.put(("migrate_done", {"done": done, "errors": errors,
                                     "removed": removed, "total": len(moves)}))

    # ---------------- 消息循环 ----------------

    def _drain(self):
        """处理后台线程发来的消息。

        一轮最多处理 THUMBS_PER_DRAIN 张缩略图：几百张缩略图会在很短时间内把队列堆满，
        一次全做完就等于把主线程占住（界面完全没反应）。限量之后界面一直能响应，
        缩略图照样一张张填上去，总耗时也差不多。
        """
        budget = THUMBS_PER_DRAIN
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            if msg[0] == "thumb":
                if budget <= 0:
                    self.q.put(msg)      # 剩下的下一轮再说；缩略图先后无所谓
                    break
                budget -= 1
            try:
                self._handle(msg)
            except Exception as exc:  # noqa: BLE001
                # 以前这里是 pass：界面一有问题就静默失败，只能靠猜。留个日志
                library.log_error("界面消息处理", str(msg[0]), exc)
        self.after(1 if not self.q.empty() else 80, self._drain)

    def _handle(self, msg):
        kind, payload = msg
        if kind == "thumb":
            gen, idx, path = payload
            if gen != self.gen or idx >= len(self.tile_widgets) or not path:
                return
            try:
                from PIL import Image
                with Image.open(path) as im:
                    photo = _pil_to_photo(im)
                lbl = self.tile_widgets[idx]
                # 缓存键用原图路径，重画同一组时才能直接命中
                src = self.tiles[idx]["path"]
                self.photos[src] = photo
                lbl.config(image=photo, text="", bg=BG)
                lbl.place_configure(width=photo.width(), height=photo.height())
                lbl._shown = src
            except Exception as exc:  # noqa: BLE001
                library.log_error("显示缩略图", path, exc)
                return
        elif kind == "evt":
            evt, data = payload
            prefix = ""
            if self._batch and self._batch[1] > 1:
                prefix = f"[{self._batch[0]}/{self._batch[1]} {self._batch[2]}] "
            if evt == "device_start":
                self._batch = (data["idx"], data["total"], data["label"])
                more = f"（{data['idx']}/{data['total']}）" if data["total"] > 1 else ""
                self._set_status(f"正在读取 {data['label']} 的媒体库……{more}")
            elif evt == "scanning":
                self._set_status(prefix + "正在读取手机媒体库……")
            elif evt == "planned":
                extra = f" · 跳过非相机目录 {data['filtered']} 个" if data.get("filtered") else ""
                self._set_status(prefix + f"待拉取 {data['todo']} 个 · "
                                          f"已备份 {data['skipped']} 个{extra}")
                self._set_progress(0.0 if data["todo"] else None)
            elif evt == "pulled":
                self._set_status(prefix + f"正在拉取 {data['idx']}/{data['total']} · "
                                          f"{data['name']} · {data['speed']}")
                self._set_progress(data["idx"] / max(data["total"], 1))
            elif evt == "error":
                self._set_status(prefix + f"失败：{data['name']} · {data['reason']}")
            elif evt == "lost":
                self._set_status(prefix + f"设备已断开 · 已拷完 {data['done']}/{data['total']} 个")
            elif evt == "migrated":
                self._set_status(f"正在归入设备 {data['idx']}/{data['total']}……")
                self._set_progress(data["idx"] / max(data["total"], 1))
        elif kind == "device_info":
            self._paint_devices()
        elif kind == "backup_all_done":
            self._finish_backup(payload)
        elif kind == "migrate_done":
            self.busy = False
            self.backup_btn.set_enabled(True)
            self._set_progress(None)
            self.refresh_library()
            self._set_status(f"归入设备完成 · 移动 {payload['done']}/{payload['total']} 个 · "
                             f"清掉空目录 {len(payload['removed'])} 个")
            if payload["errors"]:
                Dialog.warn(self, "有文件没能搬过去（其余都搬好了）", "\n".join(
                    f"{os.path.basename(s)}：{e}" for s, e in payload["errors"][:10]))

    def _finish_backup(self, results):
        """所有设备跑完之后统一收尾：汇总 + 掉线/出错各弹一次。"""
        self.busy = False
        self._batch = None
        self.backup_btn.set_enabled(True)
        self._set_progress(None)
        self.refresh_library()

        pulled = sum(r["summary"]["pulled"] for r in results if r["summary"])
        skipped = sum(r["summary"]["skipped"] for r in results if r["summary"])
        failed = sum(r["summary"]["failed"] for r in results if r["summary"])
        lost = [r for r in results if r["lost"]]
        broken = [r for r in results if r["error"] and not r["lost"]]
        self._set_status(f"备份完成 · 新拉取 {pulled} 个 · 跳过已备份 {skipped} 个 · "
                         f"失败 {failed + len(broken)} 个")

        if lost:
            names = "、".join(r["label"] for r in lost)
            tail = "其余设备已经照常备份完毕。" if len(results) > len(lost) else ""
            pick = Dialog.ask(
                self, "手机已断开",
                f"{names} 在备份途中断开了，这一台的备份已经中止。\n\n"
                "已经拷到电脑上的文件都保留着，手机上也不会少任何东西。\n"
                "重新连上后再点一次「开始备份」，会自动跳过已备份的，从断点接着来。"
                + (f"\n\n{tail}" if tail else ""),
                kind="warn", buttons=["重新连接", "知道了"])
            if pick == 0:
                self.open_wireless()
        elif broken:
            Dialog.error(self, "备份出错", "\n".join(
                f"{r['label']}：{r['error']}" for r in broken[:5]))
        else:
            errs = [(r["label"], n, msg) for r in results if r["summary"]
                    for n, msg in (r["summary"].get("errors") or [])]
            if errs:
                Dialog.warn(self, "部分文件失败", "\n".join(
                    f"{lab} · {n}：{m}" for lab, n, m in errs[:10]))


def _selfcheck_motion(photo):
    """在**打包后的真实环境**里实播一次，结果写到 exe 旁的 motion-check.log。

    参数可以是动态照片（.jpg，自动切出内嵌 MP4），也可以是普通视频文件（.mp4，直接播）。
    exe 是无控制台的 windowed 程序，结果只能落文件。这也顺带给用户一个排障入口：
    动图/视频播不出来时跑 `PhonePhotoBackup.exe --selfcheck-motion <文件路径>`，
    看是系统缺 mfplay 还是文件本身的问题。源码环境直接 python app.pyw 一样能用。
    """
    import time

    lines = []

    def say(text):
        lines.append(str(text))

    def dump():
        try:
            with open(os.path.join(backup.app_dir(), "motion-check.log"),
                      "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError:
            pass

    say(f"frozen         = {getattr(sys, 'frozen', False)}")
    say(f"mfplay 可用     = {mfplay.available()}")
    say(f"文件            = {photo}")
    if library.is_video(os.path.basename(photo)):
        ok, mp4 = True, photo            # 普通视频：原文件直接播，不用切
    else:
        ok, mp4 = library.extract_motion(photo)
    say(f"待播片段        = {mp4}")
    if not ok:
        dump()
        return

    win = tk.Tk()
    win.geometry("800x600")
    cv = tk.Canvas(win, bg="#16161A", highlightthickness=0)
    cv.pack(fill="both", expand=True)
    win.update()
    try:
        vid = mfplay.InlineVideo(mp4, cv.winfo_id())
    except OSError as exc:
        say(f"InlineVideo     = 失败：{exc}")
        dump()
        win.destroy()
        return

    state = -1
    for _ in range(40):
        win.update()
        time.sleep(0.1)
        state = vid.state()
        if state == mfplay.STATE_PLAYING:
            break
    say(f"播放状态        = {state}   (2 = PLAYING)")
    dur, pos = vid.duration(), vid.position()
    say(f"时长 / 位置     = {dur:.2f}s / {pos:.2f}s"
        + ("" if dur > 0 else "   ← 时长为 0：这台机器取不到时钟，进度条不会动"))

    vid.pause()
    for _ in range(15):
        win.update()
        time.sleep(0.1)
        if vid.state() == mfplay.STATE_PAUSED:
            break
    say(f"暂停后状态      = {vid.state()}   (3 = PAUSED)")
    vid.close()
    say("关闭            = OK")
    win.destroy()
    dump()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selfcheck-motion":
        _selfcheck_motion(sys.argv[2])
        raise SystemExit(0)
    try:
        App().mainloop()
    except Exception:  # noqa: BLE001
        # 打包成 windowed exe 后没有控制台，异常会被静默吞掉；落一份日志便于排查
        import traceback
        try:
            with open(os.path.join(backup.app_dir(), "error.log"),
                      "w", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
        except Exception:  # noqa: BLE001
            pass
        raise
