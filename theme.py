# -*- coding: utf-8 -*-
"""theme.py — 设计令牌与少量自绘组件

配色：纯白底 + 中性灰阶（zinc 系）+ 单点强调蓝，与 DeepSeek Harness 的克制白一致。

tkinter 没有 CSS：圆角、hover 过渡、细边框都得自己画。这里只封装真正用到的部分，
不引入多余抽象。动效只加在「数量少、频率低」的控件上 —— 列表里成百上千个缩略图
格子一律不做动画（那会直接掉帧）。
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

# --------------------------------------------------------------------------
# 令牌
# --------------------------------------------------------------------------

# 中性灰阶
BG          = "#FFFFFF"   # 应用底色
SURFACE     = "#FCFCFD"   # 次级面（状态栏等）
SURFACE_ALT = "#F4F4F5"   # 占位块 / hover 底
BORDER      = "#E4E4E8"   # 常规细边框
BORDER_SOFT = "#ECECEF"   # 网格格的边框
DIVIDER     = "#EFEFF1"

TEXT        = "#18181B"   # 主文字
TEXT_DIM    = "#6B6B74"   # 次要文字
TEXT_FAINT  = "#A6A6AE"   # 最弱文字

ACCENT      = "#4D6BFE"   # 强调（选中 / 主按钮）
ACCENT_DEEP = "#3D57E8"   # 主按钮 hover
ACCENT_SOFT = "#EEF1FF"   # 选中底

WARN        = "#E08A1E"   # 注意（弹窗圆点）
DANGER      = "#E5484D"   # 出错（弹窗圆点）

# 间距（8pt 网格）
S1, S2, S3, S4, S5, S6 = 4, 8, 12, 16, 24, 32

# 字体
FAMILY  = "Microsoft YaHei UI"
F_TITLE = (FAMILY, 12, "bold")
F_MED   = (FAMILY, 10, "bold")
F_SMALL = (FAMILY, 9)
F_TINY  = (FAMILY, 8)


def lerp(c1, c2, t):
    """两个 #RRGGBB 之间线性插值。"""
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    """在 Canvas 上画圆角矩形。

    坐标必须作为独立位置参数传入；传列表在 smooth 模式下会被 Tcl 解析错。
    """
    return canvas.create_polygon(
        x1 + r, y1, x2 - r, y1,
        x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r,
        x1, y1 + r, x1, y1,
        smooth=True, splinesteps=24, **kw)


def hline(master, color=DIVIDER, **pack_kw):
    """1px 分隔线。tkinter 没有 border 概念，用一条 Frame 代替。"""
    line = tk.Frame(master, height=1, bg=color)
    if pack_kw:
        line.pack(**pack_kw)
    return line


class _Fader:
    """极简 hover 颜色过渡：16ms 一帧插值。

    只给按钮、下拉这类「少而小」的控件用。列表内容永远用瞬时切换。
    """

    def __init__(self, canvas, shape_id, c_from, c_to):
        self.canvas = canvas
        self.shape = shape_id
        self.c_from = c_from
        self.c_to = c_to
        self.t = 0.0
        self.job = None

    def to(self, target):
        if self.job:
            self.canvas.after_cancel(self.job)
            self.job = None
        self._step(target)

    def _step(self, target):
        delta = target - self.t
        if abs(delta) < 0.02:
            self.t = target
            self.paint()
            return
        self.t += delta * 0.3
        self.paint()
        self.job = self.canvas.after(16, lambda: self._step(target))

    def paint(self):
        self.canvas.itemconfig(self.shape, fill=lerp(self.c_from, self.c_to, self.t))


# --------------------------------------------------------------------------
# 圆角胶囊按钮
# --------------------------------------------------------------------------


class RoundButton(tk.Canvas):
    """ttk.Button 在 Windows 上既不能圆角也不能控色，所以直接画。"""

    def __init__(self, master, text, command=None, *, primary=False,
                 height=32, padx=18, parent_bg=BG, font=None):
        self._cmd = command
        self._primary = primary
        self._enabled = True
        self._font = font or F_MED

        f = tkfont.Font(font=self._font)
        w = f.measure(text) + padx * 2
        super().__init__(master, width=w, height=height, bg=parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        # 别用 _w/_h：tkinter 内部用 _w 存 widget 路径名
        self._bw, self._bh = w, height

        c0 = ACCENT if primary else BG
        c1 = ACCENT_DEEP if primary else SURFACE_ALT
        self._shape = round_rect(self, 0, 0, w, height, height / 2, fill=c0)
        self._ring = None
        if not primary:
            self._ring = round_rect(self, 0.5, 0.5, w - 0.5, height - 0.5,
                                    height / 2, fill="", outline=BORDER)
        self._text_id = self.create_text(w / 2, height / 2 + 1, text=text,
                                         font=self._font,
                                         fill="#FFFFFF" if primary else TEXT)
        self._fader = _Fader(self, self._shape, c0, c1)

        self.bind("<Enter>", lambda e: self._enabled and self._fader.to(1.0))
        self.bind("<Leave>", lambda e: self._enabled and self._fader.to(0.0))
        self.bind("<Button-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def _press(self, _event):
        if self._enabled:
            self.itemconfig(self._shape, fill=lerp(self._fader.c_to, "#000000", 0.08))

    def _release(self, event):
        if not self._enabled:
            return
        self._fader.paint()
        inside = 0 <= event.x <= self._bw and 0 <= event.y <= self._bh
        if inside and self._cmd:
            self._cmd()

    def set_enabled(self, on):
        self._enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        base = ACCENT if self._primary else BG
        self.itemconfig(self._shape, fill=base if on else SURFACE_ALT)
        self.itemconfig(self._text_id,
                        fill=("#FFFFFF" if self._primary else TEXT) if on else TEXT_FAINT)


# --------------------------------------------------------------------------
# 自绘弹出菜单
# --------------------------------------------------------------------------


def menu_layout(items, font, width=None):
    """算出菜单尺寸和每一行的纵向区间 → (宽, 高, rows)。

    rows 里普通项是 {y0, y1, label, command, kind, sep=False}，分隔线是
    {sep=True, y}。纯计算，不碰 Tk，方便单独验算。
    """
    f = tkfont.Font(font=font)
    labels = [it[0] for it in items if it != "-"]
    w = width or max(PopupMenu.MIN_W,
                     max((f.measure(s) for s in labels), default=0) + PopupMenu.PAD_X * 2 + 16)
    rows, y = [], PopupMenu.TOUCH
    for it in items:
        if it == "-":
            rows.append({"sep": True, "y": y + PopupMenu.SEP_H / 2})
            y += PopupMenu.SEP_H
            continue
        label, cmd = it[0], it[1]
        kind = it[2] if len(it) > 2 else ""
        rows.append({"sep": False, "y0": y, "y1": y + PopupMenu.ITEM_H,
                     "label": label, "command": cmd, "kind": kind})
        y += PopupMenu.ITEM_H
    return w, y + PopupMenu.TOUCH, rows


class PopupMenu(tk.Toplevel):
    """圆角白卡弹出菜单 —— 替掉 tk.Menu。

    原生 tk.Menu 在 Windows 上是系统主题的直角灰菜单（左侧还留一条图标栏），
    跟这套圆角白卡放一起像两个软件，所以自己画：白底 + 1px 细边 + 圆角，
    hover 用 ACCENT_SOFT 垫底、文字转强调色，分隔线用 DIVIDER。

    items 每项：`"-"` 分隔线；`(label, command)`；`(label, command, "danger")` 危险项。
    点空白处、Esc、窗口失焦都会关掉；命令在菜单销毁之后才执行，免得它弹出的
    对话框跟这里的鼠标抓取打架。
    """

    ITEM_H = 32
    PAD_X = 14
    MIN_W = 156
    SEP_H = 9
    TOUCH = 6          # 卡片上下内边距

    def __init__(self, master, items, x, y, current=None, width=None):
        super().__init__(master)
        self._closed = False
        self.withdraw()
        self.overrideredirect(True)
        try:
            self.attributes("-transparentcolor", "#FF00FE")
            self._base = "#FF00FE"      # 圆角以外的像素
        except tk.TclError:
            self._base = BG
        self.configure(bg=self._base)

        w, h, rows = menu_layout(items, F_SMALL, width)
        self._cw, self._ch = w, h
        self.canvas = tk.Canvas(self, width=w, height=h, bg=self._base,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        round_rect(self.canvas, 0.5, 0.5, w - 0.5, h - 0.5, 12,
                   fill=BG, outline=BORDER)

        self._rows = []
        for row in rows:
            if row["sep"]:
                self.canvas.create_line(self.PAD_X, row["y"], w - self.PAD_X, row["y"],
                                        fill=DIVIDER)
                continue
            shape = round_rect(self.canvas, 6, row["y0"] + 2, w - 6, row["y1"] - 2, 8,
                               fill=BG, outline="")
            fg = DANGER if row["kind"] == "danger" else TEXT
            if current is not None and row["label"] == current:
                fg = ACCENT
            tid = self.canvas.create_text(self.PAD_X, (row["y0"] + row["y1"]) / 2 + 1,
                                          anchor="w", text=row["label"],
                                          font=F_SMALL, fill=fg)
            row.update(shape=shape, text_id=tid, fg=fg)
            self._rows.append(row)
        self._hover = None

        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Button-1>", self._click)
        # 点菜单以外的地方：Tk 的本地 grab 会把事件重定向到「拿 grab 的那个窗口」，
        # 也就是这个 Toplevel；此时坐标落在 canvas 之外（常常是负数），所以事件目标是
        # Toplevel 而不是 canvas —— 光绑 canvas 的话这些点击谁也收不到，
        # 表现就是「点空白关不掉菜单」。这里再绑一层兜住它们。
        self.bind("<Button-1>", lambda _e: self.close())
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<FocusOut>", lambda e: self.close())
        self.canvas.configure(cursor="hand2")

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        # 贴边弹出时往里收，别让菜单跑到屏幕外
        self.geometry(f"{w}x{h}+{max(4, min(x, sw - w - 4))}+"
                                 f"{max(4, min(y, sh - h - 4))}")
        self.deiconify()
        self.lift()
        try:
            self.grab_set()          # 点菜单以外的地方也要能收到事件，才知道该关掉
            self.focus_force()
        except tk.TclError:
            pass

    def _row_at(self, y):
        for row in self._rows:
            if row["y0"] <= y < row["y1"]:
                return row
        return None

    def _motion(self, event):
        row = self._row_at(event.y)
        if row is self._hover:
            return
        if self._hover is not None:
            self.canvas.itemconfig(self._hover["shape"], fill=BG)
            self.canvas.itemconfig(self._hover["text_id"], fill=self._hover["fg"])
        self._hover = row
        if row is not None:
            self.canvas.itemconfig(row["shape"], fill=ACCENT_SOFT)
            self.canvas.itemconfig(row["text_id"], fill=ACCENT)

    def _click(self, event):
        row = self._row_at(event.y)
        cmd = row and row["command"]
        self.close()
        if cmd:
            cmd()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.master.focus_set()      # 焦点还给主窗口，免得键盘导航失灵
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------
# 圆角设备选择器
# --------------------------------------------------------------------------


class DevicePicker(tk.Canvas):
    """自绘的设备选择胶囊：白底圆角 + 右侧 ▾，点击弹出菜单。

    ttk.Combobox 在 Windows 上是方角 + 深边框，与整体圆角体系冲突，所以自己画。
    """

    def __init__(self, master, width=240, height=34, parent_bg=BG, on_pick=None):
        super().__init__(master, width=width, height=height, bg=parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._pw, self._ph = width, height
        self._on_pick = on_pick
        self._items = []
        self._value = ""

        self._shape = round_rect(self, 0.5, 0.5, width - 0.5, height - 0.5,
                                 height / 2, fill=BG, outline=BORDER)
        self._label = self.create_text(14, height / 2 + 1, anchor="w",
                                       text="未检测到手机", font=F_SMALL, fill=TEXT_FAINT)
        self.create_text(width - 14, height / 2, anchor="e", text="▾",
                         font=F_SMALL, fill=TEXT_DIM)
        self._fader = _Fader(self, self._shape, BG, SURFACE_ALT)

        self.bind("<Enter>", lambda e: self._fader.to(1.0))
        self.bind("<Leave>", lambda e: self._fader.to(0.0))
        self.bind("<Button-1>", self._popup)

    @property
    def value(self):
        return self._value

    def set_items(self, items, empty_text="未检测到手机", default=None):
        """items 里每项可以是字符串，也可以是 (值, 显示名)。

        多设备场景下「值」得是 adb 的 serial（`ip:端口` 或 USB 序列号），
        但那个东西给人看太难看，所以显示名单独给。

        default 只在「当前值不在列表里」时生效（首次填充、或选中的设备已经
        从列表里消失）；用户手动选过的值不会被它覆盖。
        """
        pairs = []
        for it in items:
            if isinstance(it, (tuple, list)) and len(it) >= 2:
                pairs.append((str(it[0]), str(it[1])))
            else:
                pairs.append((str(it), str(it)))
        self._items = pairs
        values = [v for v, _ in pairs]
        if self._value not in values:
            # 默认项优先；它也不在列表里（比如那台手机没接）才退回第一项
            self._value = (str(default) if default is not None and str(default) in values
                           else (values[0] if values else ""))
        self.refresh_label(empty_text)

    def refresh_label(self, empty_text="未检测到手机"):
        if self._items:
            self.itemconfig(self._label, text=dict(self._items).get(self._value, self._value),
                            fill=TEXT)
        else:
            self.itemconfig(self._label, text=empty_text, fill=TEXT_FAINT)

    def _popup(self, _event):
        if not self._items:
            return
        PopupMenu(self, [(label, lambda v=value: self._pick(v))
                         for value, label in self._items],
                  self.winfo_rootx(), self.winfo_rooty() + self._ph + 4,
                  current=dict(self._items).get(self._value))

    def _pick(self, value):
        self._value = value
        self.refresh_label()
        if self._on_pick:
            self._on_pick(value)


# --------------------------------------------------------------------------
# 圆角卡片窗口 / 同风格弹窗
# --------------------------------------------------------------------------


class CardWindow(tk.Toplevel):
    """无系统边框的圆角卡片窗口：自绘标题、可拖动、Esc 关闭、居中于父窗口。

    圆角靠 `-transparentcolor` 抠出来：Windows 的 Tk 能把某个「魔术色」整片变成透明
    像素，卡片四角才不会露出一块方形底色。少数环境没这个属性，就退化成直角白卡 ——
    只是不好看，功能不受影响。

    内容挂在 self.body 上随便 pack；高度事先估不准，pack 完用 fit() 再校正一次。
    """

    MAGIC = "#FF00FE"      # 魔术色：卡片以外的地方不该出现这个颜色
    PAD = 18               # 卡片内边距（内容内缩这么多，正好避开圆角）
    TITLE_H = 36           # 有标题时给标题留的高度
    RADIUS = 16

    def __init__(self, master, width, height, title="", closable=True, dot=None):
        super().__init__(master)
        self.withdraw()                      # 先藏好，摆完位置再显示，免得闪在屏幕左上角
        self.overrideredirect(True)
        # 千万别用 self._w / self._h：tkinter 内部拿 _w 存窗口的路径名，撞上就是
        # _tkinter.TclError: invalid command name "440"（RoundButton 当初栽过同一个坑）
        self._cw, self._ch = int(width), int(height)
        self._title, self._dot = title, dot
        self.result = None
        try:
            self.attributes("-transparentcolor", self.MAGIC)
            self._base = self.MAGIC
        except tk.TclError:
            self._base = BG                  # 不支持透明色 → 直角白卡
        self.configure(bg=self._base)

        self.canvas = tk.Canvas(self, width=self._cw, height=self._ch, bg=self._base,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._card = None
        self._paint_card()
        self._draw_title(closable)

        top = self._top()
        self.body = tk.Frame(self.canvas, bg=BG)
        self._body_item = self.canvas.create_window(
            self.PAD, top, window=self.body, anchor="nw",
            width=self._cw - 2 * self.PAD, height=self._ch - top - self.PAD)

        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.bind("<Escape>", lambda _e: self.close())

    # ---- 画 ----

    def _top(self):
        return self.PAD + (self.TITLE_H if self._title else 0)

    def _paint_card(self):
        if self._card is not None:
            self.canvas.delete(self._card)
        self._card = round_rect(self.canvas, 0.5, 0.5, self._cw - 0.5, self._ch - 0.5,
                                self.RADIUS, fill=BG, outline=BORDER)
        self.canvas.tag_lower(self._card)

    def _draw_title(self, closable=True):
        if not self._title:
            return
        x = self.PAD
        if self._dot:
            self.canvas.create_oval(x, self.PAD + 7, x + 9, self.PAD + 16,
                                    fill=self._dot, outline="")
            x += 17
        self.canvas.create_text(x, self.PAD + 3, anchor="nw", text=self._title,
                                font=F_TITLE, fill=TEXT)
        if closable:
            self._close_item = self.canvas.create_text(
                self._cw - self.PAD, self.PAD + 4, anchor="ne", text="✕",
                font=F_SMALL, fill=TEXT_FAINT)
            self.canvas.tag_bind(self._close_item, "<Button-1>", lambda _e: self.close())
            self.canvas.tag_bind(self._close_item, "<Enter>", lambda _e: self.canvas.itemconfig(
                self._close_item, fill=TEXT))
            self.canvas.tag_bind(self._close_item, "<Leave>", lambda _e: self.canvas.itemconfig(
                self._close_item, fill=TEXT_FAINT))

    def fit(self, height):
        """校正卡片高度（内容 pack 完才能算准）。已经显示出来的话同步改窗口尺寸。"""
        height = max(int(height), self._top() + self.PAD)
        if height == self._ch:
            return
        self._ch = height
        self.canvas.configure(height=height)
        self._paint_card()
        self.canvas.itemconfigure(self._body_item, height=height - self._top() - self.PAD)
        if self.winfo_ismapped():
            self.geometry(f"{self._cw}x{self._ch}")     # 不给位置 = 原地长高

    # ---- 拖动 / 开关 ----

    def _drag_start(self, event):
        self._dx = event.x_root - self.winfo_rootx()
        self._dy = event.y_root - self.winfo_rooty()

    def _drag_move(self, event):
        self.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")

    def show(self):
        self.update_idletasks()
        mw = max(self.master.winfo_width(), 1)
        mh = max(self.master.winfo_height(), 1)
        x = self.master.winfo_rootx() + (mw - self._cw) // 2
        y = self.master.winfo_rooty() + max((mh - self._ch) // 3, 0)
        self.geometry(f"{self._cw}x{self._ch}+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        self.lift()
        try:
            self.grab_set()          # 模态：不点掉它，父窗口不响应
        except tk.TclError:
            pass
        self.focus_force()

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


_KIND_DOT = {"info": ACCENT, "warn": WARN, "error": DANGER}


class Dialog(CardWindow):
    """应用内消息弹窗 —— 替掉系统 messagebox（灰色系统皮肤，跟这套白底圆角不搭）。

    阻塞式用法（返回值 = 用户点了第几个按钮；被 ✕ / Esc 关掉则是 None）：
        pick = Dialog.ask(self, "手机已断开", "……", kind="warn",
                          buttons=["重新连接", "知道了"])
    """

    WIDTH = 440

    def __init__(self, master, title, body, kind="info", buttons=None, default=0):
        self._buttons = list(buttons or ["知道了"])
        self._default = default
        super().__init__(master, self.WIDTH, 200, title=title,
                         dot=_KIND_DOT.get(kind, ACCENT))

        content = tk.Frame(self.body, bg=BG)
        content.pack(fill="x", anchor="n")

        tk.Label(content, text=body, font=F_SMALL, bg=BG, fg=TEXT_DIM,
                 justify="left", anchor="w",
                 wraplength=self.WIDTH - 2 * self.PAD - 6).pack(fill="x")

        row = tk.Frame(content, bg=BG)
        row.pack(fill="x", pady=(S4, 0))
        for i in range(len(self._buttons) - 1, -1, -1):
            # 只有一个按钮时用描边样式，蓝色实心留给真正的「主操作」
            RoundButton(row, self._buttons[i], lambda i=i: self._pick(i),
                        primary=(i == self._default and len(self._buttons) > 1),
                        parent_bg=BG, height=32, padx=16).pack(side="right", padx=(S2, 0))

        self.update_idletasks()
        self.fit(content.winfo_reqheight() + self._top() + self.PAD)

    def _pick(self, index):
        self.result = index
        self.close()

    @classmethod
    def ask(cls, master, title, body, kind="info", buttons=None, default=0):
        dlg = cls(master, title, body, kind=kind, buttons=buttons, default=default)
        dlg.show()
        master.wait_window(dlg)
        return dlg.result

    @classmethod
    def info(cls, master, title, body):
        return cls.ask(master, title, body, "info")

    @classmethod
    def warn(cls, master, title, body):
        return cls.ask(master, title, body, "warn")

    @classmethod
    def error(cls, master, title, body):
        return cls.ask(master, title, body, "error")


class _CheckRow(tk.Canvas):
    """一行可勾选的条目。

    原生 Checkbutton 在 Windows 上是方角的系统皮肤，跟这套圆角白底对不上，所以自绘。
    """

    BOX = 18

    def __init__(self, master, label, sub="", width=400, on_toggle=None):
        height = 48 if sub else 34
        super().__init__(master, width=width, height=height, bg=BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.checked = True
        self._on_toggle = on_toggle
        box_y = (height - self.BOX) / 2
        self._box = round_rect(self, 10, box_y, 10 + self.BOX, box_y + self.BOX, 5,
                               fill=ACCENT, outline=ACCENT)
        cx, cy = 10 + self.BOX / 2, box_y + self.BOX / 2
        self._tick = self.create_line(cx - 5, cy, cx - 1.5, cy + 3.5, cx + 5, cy - 4,
                                      fill="#FFFFFF", width=2,
                                      capstyle="round", joinstyle="round")
        if sub:
            self.create_text(40, height / 2 - 12, anchor="w", text=label,
                             font=F_MED, fill=TEXT)
            self.create_text(40, height / 2 + 8, anchor="w", text=sub,
                             font=F_TINY, fill=TEXT_FAINT)
        else:
            self.create_text(40, height / 2, anchor="w", text=label,
                             font=F_SMALL, fill=TEXT)

        self.bind("<Button-1>", lambda _e: self.toggle())
        self.bind("<Enter>", lambda _e: self.configure(bg=SURFACE_ALT))
        self.bind("<Leave>", lambda _e: self.configure(bg=BG))
        self._paint()

    def toggle(self):
        self.set_checked(not self.checked)

    def set_checked(self, on):
        self.checked = bool(on)
        self._paint()
        if self._on_toggle:
            self._on_toggle()

    def _paint(self):
        self.itemconfigure(self._box, fill=ACCENT if self.checked else BG,
                           outline=ACCENT if self.checked else BORDER)
        self.itemconfigure(self._tick, state="normal" if self.checked else "hidden")


class PickDialog(CardWindow):
    """多选弹窗：给一串候选项，让用户勾选要处理哪些。

    用在「同时连着好几台手机，这次备份哪几台」这种场景。
    用法：PickDialog.ask(self, 标题, [(key, 主标题, 副标题), …]) → 选中的 key 列表，取消则 None。
    """

    WIDTH = 470

    def __init__(self, master, title, options, note="", confirm="确定"):
        super().__init__(master, self.WIDTH, 260, title=title)
        self._rows = []
        wrap = self.WIDTH - 2 * self.PAD - 8

        content = tk.Frame(self.body, bg=BG)
        content.pack(fill="x", anchor="n")
        self._content = content

        if note:
            tk.Label(content, text=note, font=F_SMALL, bg=BG, fg=TEXT_FAINT,
                     justify="left", anchor="w", wraplength=wrap).pack(
                         fill="x", pady=(0, S2))

        for key, label, sub in options:
            row = _CheckRow(content, label, sub,
                            width=self.WIDTH - 2 * self.PAD, on_toggle=self._sync)
            row.key = key
            row.pack(fill="x")
            self._rows.append(row)

        self.status = tk.StringVar()
        tk.Label(content, textvariable=self.status, font=F_SMALL, bg=BG, fg=TEXT_DIM,
                 anchor="w").pack(fill="x", pady=(S3, 0))

        bottom = tk.Frame(content, bg=BG)
        bottom.pack(fill="x", pady=(S2, 0))
        RoundButton(bottom, "全选 / 全不选", self._toggle_all, parent_bg=BG,
                    height=32, padx=14).pack(side="left")
        RoundButton(bottom, confirm, lambda: self._done(True), primary=True,
                    parent_bg=BG, height=32, padx=16).pack(side="right")
        RoundButton(bottom, "取消", lambda: self._done(False), parent_bg=BG,
                    height=32, padx=16).pack(side="right", padx=(0, S2))

        self.update_idletasks()
        self.fit(content.winfo_reqheight() + self._top() + self.PAD)
        self._sync()

    def _sync(self):
        n = sum(1 for r in self._rows if r.checked)
        self.status.set(f"已选 {n} / {len(self._rows)} 台")

    def _toggle_all(self):
        want = not all(r.checked for r in self._rows)
        for row in self._rows:
            row.set_checked(want)      # set_checked 会回调 _sync，不用再来一次
        self._sync()

    def _done(self, ok):
        self.result = [r.key for r in self._rows if r.checked] if ok else None
        self.close()

    @classmethod
    def ask(cls, master, title, options, note="", confirm="确定"):
        dlg = cls(master, title, options, note=note, confirm=confirm)
        dlg.show()
        master.wait_window(dlg)
        return dlg.result


class InputDialog(CardWindow):
    """单行输入弹窗。

    系统的 simpledialog 是灰色方角皮肤，跟这套白底圆角不搭，所以自己画一个。
    """

    WIDTH = 420

    def __init__(self, master, title, note="", value="", confirm="确定"):
        super().__init__(master, self.WIDTH, 220, title=title)
        content = tk.Frame(self.body, bg=BG)
        content.pack(fill="x", anchor="n")
        self._content = content

        if note:
            tk.Label(content, text=note, font=F_SMALL, bg=BG, fg=TEXT_FAINT,
                     justify="left", anchor="w",
                     wraplength=self.WIDTH - 2 * self.PAD - 8).pack(fill="x", pady=(0, S3))

        self.var = tk.StringVar(value=value)
        self.entry = tk.Entry(content, textvariable=self.var, font=F_SMALL, bg=SURFACE,
                              fg=TEXT, relief="flat", highlightthickness=1,
                              highlightbackground=BORDER, highlightcolor=ACCENT,
                              insertbackground=TEXT)
        self.entry.pack(fill="x", ipady=5)
        self.entry.bind("<Return>", lambda _e: self._done(True))

        bottom = tk.Frame(content, bg=BG)
        bottom.pack(fill="x", pady=(S4, 0))
        RoundButton(bottom, confirm, lambda: self._done(True), primary=True,
                    parent_bg=BG, height=32, padx=16).pack(side="right")
        RoundButton(bottom, "取消", lambda: self._done(False), parent_bg=BG,
                    height=32, padx=16).pack(side="right", padx=(0, S2))

        self.update_idletasks()
        self.fit(content.winfo_reqheight() + self._top() + self.PAD)

    def _done(self, ok):
        self.result = self.var.get().strip() if ok else None
        self.close()

    @classmethod
    def ask(cls, master, title, note="", value="", confirm="确定"):
        dlg = cls(master, title, note=note, value=value, confirm=confirm)
        dlg.show()
        dlg.entry.focus_set()
        dlg.entry.select_range(0, "end")
        master.wait_window(dlg)
        return dlg.result


if __name__ == "__main__":
    # 自检：颜色插值是最容易写错的一环（分量顺序、越界）
    assert lerp("#000000", "#FFFFFF", 0.0) == "#000000"
    assert lerp("#000000", "#FFFFFF", 1.0) == "#FFFFFF"
    assert lerp("#000000", "#FFFFFF", 0.5) == "#808080"
    assert lerp("#FF0000", "#00FF00", 0.5) == "#808000"     # R/G 分量各半
    assert lerp("#123456", "#123456", 0.7) == "#123456"
    assert lerp("#000000", "#FFFFFF", -5) == "#000000"      # 越界钳到 0
    assert lerp("#000000", "#FFFFFF", 9) == "#FFFFFF"       # 越界钳到 1
    print("theme selfcheck: PASS")
