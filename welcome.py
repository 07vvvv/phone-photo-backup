# -*- coding: utf-8 -*-
"""welcome.py — 首次使用的新手指引（聚光灯分步引导）

五步：这是什么 → 连手机 → 备份 → 浏览 → 进阶。
每一步把全窗口压暗，只留一块亮区指向要操作的地方，旁边一张白卡说明这一步做什么。

实现是两层 Toplevel 叠加（都得 overrideredirect + 魔术色挖洞）：
    遮罩层   -transparentcolor MAGIC + -alpha 0.42
             洞外画 4 块 #18181B 矩形，洞内不画 → 魔术色被抠成全透明，其余按 42% 压暗
    内容层   -transparentcolor MAGIC，不设 alpha
             画 2px 强调色高亮框 + 白色圆角卡片（卡片若同层带 alpha 会被一起冲淡）

为什么不用一个窗口：-alpha 作用于整窗，卡片和遮罩没法分开。

两个容易翻车的点（都实测过）：
    · 主窗口若是 -topmost，遮罩层也必须 -topmost 且 lift()，否则永远被压在下面。
    · 主窗口坐标要用「客户区」而非「外框」。Tk 的 winfo_rootx/rooty 给的就是
      客户区左上角（带标题栏的窗口下实测偏移为 0），直接用即可。

对外接口：
    tour = welcome.Tour(app, targets, on_done=cb)   # targets: {"dev": widget, ...}
    tour.show()                                      # 从第 1 步开始
"""
from __future__ import annotations

import tkinter as tk

import theme
from theme import (ACCENT, ACCENT_SOFT, BG, BORDER, DIVIDER, SURFACE, TEXT,
                   TEXT_DIM, TEXT_FAINT, WARN, F_TINY, F_MED, F_SMALL, F_TITLE,
                   S1, S2, S3, S4, S5, RoundButton, round_rect)

MAGIC = "#FF00FE"          # 魔术色：卡片与遮罩以外的地方不该出现这个颜色
CARD_W = 440               # 与 theme.Dialog.WIDTH 一致
CARD_PAD = 18              # 与 theme.CardWindow.PAD 一致
CARD_RADIUS = 16           # 与 theme.CardWindow.RADIUS 一致
VEIL_ALPHA = 0.42          # 遮罩浓淡：与 demo 的 rgba(24,24,27,.42) 等价
MARGIN = 8                 # 卡片与窗口边缘的最小留白


# --------------------------------------------------------------------------
# 步骤数据 —— 改文案只改这里
# --------------------------------------------------------------------------

STEPS = [
    dict(
        title="三步把手机相册搬到电脑",
        target=None,
        card=(400, 262),
        primary="开始了解",
        body=[
            ("p", "这个程序只做一件事 —— 用数据线或 Wi-Fi，把手机里的照片视频"
                  "按拍摄日期备份到这台电脑，然后按时间浏览。"),
            ("facts", [("只读", "绝不修改、也不删除手机上的任何东西"),
                       ("增量", "备份过的自动跳过，中断后从断点接着来"),
                       ("离线", "不联网、不登录、不上传任何地方")]),
        ],
    ),
    dict(
        title="先用数据线连一次手机",
        target="dev",
        pad=8,
        card=(300, 188),
        primary="下一步",
        body=[
            ("p", "手机连上电脑，按顺序做三步："),
            ("num", ["下拉通知栏，把 USB 用途选成 文件传输 / MTP",
                     "设置 → 开发者选项 → 打开 USB 调试",
                     "弹出「允许 USB 调试吗」时点 允许（建议勾「一律允许」）"]),
            ("tip", ("九成的失败都卡在这里", "：不开 USB 调试，电脑永远看不到设备。"), "warn"),
        ],
    ),
    dict(
        title="点「刷新」认出手机，再点「开始备份」",
        target="backup",
        pad=6,
        card=(700, 146),
        primary="下一步",
        body=[
            ("p", "顶栏「刷新」同时做两件事：重扫这台电脑上的图片，和重新识别手机。"
                  "认出来之后，设备下拉里会出现你的手机名。"),
            ("p", "第一次是全量，约每秒 20 MB，慢是正常的；之后只拉新增的，几秒钟就完。"
                  "中途拔线不要紧，重新点一次从断点继续，已经拷好的不会重来。"),
            ("tip", ("第一次备份前会问你 ", "照片存哪个文件夹"), "info",
                    " —— 挑空间大的盘，别放在 C 盘。"),
        ],
    ),
    dict(
        title="左边按年月找，右边看大图",
        target="nav",
        pad=0,
        card=(300, 150),
        primary="下一步",
        body=[
            ("p", "备份完的照片按「年 → 月」排在左边：点年份看全年，点月份看当月。"),
            ("p", "右边单击选中、双击开大图，方向键在格子间移动。大图里滚轮放大到 6 倍、"
                  "按住左键拖动平移，视频直接在窗口里播。"),
            ("p", "翻页沿着全库时间线走，从最新一路翻到最早，跨月跨年都不会停。"),
        ],
    ),
    dict(
        title="不用插线也能备份",
        target="more",
        pad=6,
        card=(560, 150),
        primary="开始使用",
        body=[
            ("p", "Android 11 及以上可以走 Wi-Fi：手机开「无线调试」→ 点"
                  "「使用配对码配对设备」→ 屏幕上出 6 位数字；电脑上点「无线」"
                  "填进去配对一次，以后就不插线了。"),
            ("tip", ("这份指引随时能重看：", "更多 ▾ → 使用指引"), "info"),
        ],
    ),
]


# --------------------------------------------------------------------------
# 引导层
# --------------------------------------------------------------------------


class Tour:
    def __init__(self, master, targets, on_done=None, steps=None):
        """targets: {"dev" / "backup" / "more" / "nav": 控件}，对应 STEPS 里的 target。"""
        self.master = master
        self.targets = targets
        self.on_done = on_done
        self.steps = steps or STEPS
        self.i = 0
        self._job = None
        self._last_geo = None
        self._closed = False
        self._shown = False
        self._watch_job = None
        self._build()

    # ---------- 建立两层 ----------

    def _geom(self):
        m = self.master
        return (m.winfo_rootx(), m.winfo_rooty(), m.winfo_width(), m.winfo_height())

    def _build(self):
        x, y, w, h = self._geom()
        geo = "%dx%d+%d+%d" % (w, h, x, y)

        self.veil = tk.Toplevel(self.master)
        self.veil.overrideredirect(True)
        self.veil.transient(self.master)   # 让系统带着一起最小化/恢复（见 _watch 注释）
        self.veil.geometry(geo)
        self.veil.configure(bg=MAGIC)
        self.veil.attributes("-transparentcolor", MAGIC)
        self.veil.attributes("-alpha", VEIL_ALPHA)
        self.vc = tk.Canvas(self.veil, width=w, height=h, bg=MAGIC,
                            highlightthickness=0, bd=0)
        self.vc.pack()

        self.layer = tk.Toplevel(self.master)
        self.layer.overrideredirect(True)
        self.layer.transient(self.master)  # 同上
        self.layer.geometry(geo)
        self.layer.configure(bg=MAGIC)
        self.layer.attributes("-transparentcolor", MAGIC)
        self.lc = tk.Canvas(self.layer, width=w, height=h, bg=MAGIC,
                            highlightthickness=0, bd=0)
        self.lc.pack()

        # 主窗口若自己是置顶的（自动化验收、或用户其它设置），两层也必须置顶，
        # 否则永远被压在下面、整个引导都看不见 —— 这个坑实测踩过一次。
        try:
            pin = bool(self.master.attributes("-topmost"))
        except tk.TclError:
            pin = False
        if pin:
            self.veil.attributes("-topmost", True)
            self.layer.attributes("-topmost", True)

        # 主窗口一拖一缩，两层必须跟着走，否则高亮框立刻错位
        self.master.bind("<Configure>", self._on_config, add="+")
        self.master.bind("<Destroy>", self._on_master_destroy, add="+")
        self.master.bind("<Escape>", lambda e: self.close(), add="+")
        for win in (self.veil, self.layer):
            win.bind("<Escape>", lambda e: self.close())

    # ---------- 跟随主窗口 ----------

    def _watch(self):
        """每 400ms 看一眼主窗口状态。

        两层是独立顶层窗口，不跟着主窗口一起最小化，会单独飘在桌面上。
        正解是 _build 里的 `transient(master)` —— 由系统带着一起最小化/恢复。
        这里再兜一层轮询，防的是「最小化到托盘」这类 transient 也管不到的情况。
        （另两条路都试过不行：`<Unmap>` 在 Windows 上不为最小化触发；
        withdraw + deiconify 恢复后分层窗口的 -alpha 会被冲掉，遮罩再也画不出来。）
        """
        if self._closed:
            return
        try:
            if self.master.state() == "iconic":
                self.close()
                return
        except tk.TclError:
            return
        self._watch_job = self.master.after(400, self._watch)

    def _on_master_destroy(self, ev):
        """主窗口被关掉时别让 after 回调继续动已销毁的窗口。"""
        if ev.widget is self.master:
            self._closed = True
            self._job = None

    def _on_config(self, ev):
        if ev.widget is not self.master:
            return
        geo = self._geom()
        if geo == self._last_geo:
            return
        self._last_geo = geo
        if self._job:
            self.master.after_cancel(self._job)
        self._job = self.master.after(60, self._resync)   # 拖窗口时别每帧重画

    def _resync(self):
        self._job = None
        if self._closed:
            return
        x, y, w, h = self._geom()
        if w < 50 or h < 50:            # 最小化时 winfo_width() 会退化成 1
            return
        geo = "%dx%d+%d+%d" % (w, h, x, y)
        for win, cv in ((self.veil, self.vc), (self.layer, self.lc)):
            win.geometry(geo)
            cv.configure(width=w, height=h)
        self.render(self.i)

    # ---------- 画 ----------

    def _target_box(self, key, pad):
        """目标控件的客户区坐标框；目标不可见时返回 None，退回整屏压暗。"""
        wdg = self.targets.get(key)
        if wdg is None or not wdg.winfo_ismapped():
            return None
        if wdg.winfo_width() <= 1 or wdg.winfo_height() <= 1:
            return None
        mx, my = self.master.winfo_rootx(), self.master.winfo_rooty()
        x0 = wdg.winfo_rootx() - mx - pad
        y0 = wdg.winfo_rooty() - my - pad
        x1 = x0 + wdg.winfo_width() + pad * 2
        y1 = y0 + wdg.winfo_height() + pad * 2
        w, h = self.master.winfo_width(), self.master.winfo_height()
        if w <= 1 or h <= 1:
            return None
        x0, y0 = max(0, x0), max(0, y0)          # 钳到窗口内，别画到窗外
        x1, y1 = min(w, x1), min(h, y1)
        if x1 - x0 < 8 or y1 - y0 < 8:           # 被挤成一条缝，不值得高亮
            return None
        return x0, y0, x1, y1

    def _paint_veil(self, hole):
        c = self.vc
        c.delete("all")
        w, h = self.master.winfo_width(), self.master.winfo_height()
        if hole is None:
            c.create_rectangle(0, 0, w, h, fill=TEXT, outline="")
            return
        x0, y0, x1, y1 = hole
        c.create_rectangle(0, 0, w, y0, fill=TEXT, outline="")      # 上
        c.create_rectangle(0, y1, w, h, fill=TEXT, outline="")      # 下
        c.create_rectangle(0, y0, x0, y1, fill=TEXT, outline="")    # 左
        c.create_rectangle(x1, y0, w, y1, fill=TEXT, outline="")    # 右

    def _draw_card(self, step, x, y):
        c = self.lc
        # 注意：这里不能 delete("card") —— render() 刚画的高亮框是 hole tag，卡片是 card tag，
        # 删自己不碍事；但绝不能再连带把 hole 删掉。
        body = tk.Frame(c, bg=BG)
        wrap = CARD_W - 2 * CARD_PAD

        # 标题必须先 pack，否则会排到正文后面
        tk.Label(body, text=step["title"], font=F_TITLE, bg=BG, fg=TEXT,
                 justify="left", anchor="w", wraplength=wrap).pack(
                     fill="x", anchor="w", pady=(0, S2))

        for blk in step["body"]:
            kind = blk[0]
            if kind == "p":
                tk.Label(body, text=blk[1], font=F_SMALL, bg=BG, fg=TEXT_DIM,
                         justify="left", anchor="w", wraplength=wrap).pack(
                             fill="x", anchor="w", pady=(0, S2))
            elif kind == "facts":
                box = tk.Frame(body, bg=BG)
                box.pack(fill="x", pady=(S1, 0))
                for name, desc in blk[1]:
                    row = tk.Frame(box, bg=BG)
                    row.pack(fill="x", pady=2)
                    dot = tk.Canvas(row, width=12, height=18, bg=BG,
                                    highlightthickness=0, bd=0)
                    dot.pack(side="left")
                    dot.create_oval(3, 7, 8, 12, fill=ACCENT, outline="")
                    tk.Label(row, text="%s　%s" % (name, desc), font=F_SMALL,
                             bg=BG, fg=TEXT_DIM, justify="left", anchor="w",
                             wraplength=wrap - 14).pack(side="left", fill="x")
            elif kind == "num":
                box = tk.Frame(body, bg=BG)
                box.pack(fill="x")
                for n, line in enumerate(blk[1], 1):
                    row = tk.Frame(box, bg=BG)
                    row.pack(fill="x", pady=3)
                    num = tk.Canvas(row, width=18, height=18, bg=BG,
                                    highlightthickness=0, bd=0)
                    num.pack(side="left", anchor="n")
                    num.create_oval(0, 1, 16, 17, fill=ACCENT_SOFT, outline="")
                    num.create_text(8, 9, text=str(n), font=(theme.FAMILY, 7),
                                    fill=ACCENT)
                    tk.Label(row, text=line, font=F_SMALL, bg=BG, fg=TEXT_DIM,
                             justify="left", anchor="w",
                             wraplength=wrap - 24).pack(side="left", fill="x",
                                                        padx=(6, 0))
            elif kind == "tip":
                head, emph = blk[1]
                warn = blk[2] == "warn"
                fill = SURFACE if warn else ACCENT_SOFT
                box = tk.Frame(body, bg=fill, highlightthickness=1,
                               highlightbackground=BORDER if warn else ACCENT_SOFT)
                box.pack(fill="x", pady=(S3, 0))
                tk.Frame(box, width=3, bg=WARN if warn else ACCENT).pack(
                    side="left", fill="y", padx=(2, 0), pady=2)
                tail = blk[3] if len(blk) > 3 else ""
                tk.Label(box, text=head + emph + tail, font=F_SMALL, bg=fill,
                         fg=TEXT_DIM, justify="left", anchor="w",
                         wraplength=wrap - 22).pack(side="left", fill="x",
                                                    padx=8, pady=7)

        foot = tk.Frame(body, bg=BG)
        foot.pack(fill="x", pady=(S4, 0))
        skip = tk.Label(foot, text="跳过指引" if self.i else "用过，直接开始使用",
                        font=F_SMALL, bg=BG, fg=TEXT_FAINT, cursor="hand2")
        skip.pack(side="left")
        skip.bind("<Button-1>", lambda e: self.close())

        RoundButton(foot, step["primary"], self._primary_action, primary=True,
                    parent_bg=BG, height=32, padx=18).pack(side="right")
        if self.i > 0:
            RoundButton(foot, "上一步", self.prev, parent_bg=BG,
                        height=32, padx=16).pack(side="right", padx=(0, S2))

        # 先按目标宽度落位，量出来的高度才准（wraplength 依赖实际宽度）
        c.create_window(x + CARD_PAD, y + CARD_PAD + 18, window=body, anchor="nw",
                        width=wrap, tags="card")
        body.update_idletasks()
        card_h = 18 + body.winfo_reqheight() + CARD_PAD

        bg_id = round_rect(c, x, y, x + CARD_W, y + card_h, CARD_RADIUS,
                           fill=BG, outline=BORDER, tags="card")
        c.tag_lower(bg_id)

        # 进度点
        dots = tk.Canvas(c, width=120, height=12, bg=BG, highlightthickness=0, bd=0)
        xoff = 0
        for n in range(len(self.steps)):
            if n == self.i:
                dots.create_oval(xoff, 3, xoff + 16, 9, fill=ACCENT, outline="")
                xoff += 21
            else:
                dots.create_oval(xoff, 3, xoff + 6, 9, fill=BORDER, outline="")
                xoff += 11
        c.create_window(x + CARD_PAD, y + CARD_PAD, window=dots, anchor="nw",
                        tags="card")

        # ✕
        close = tk.Label(c, text="✕", font=F_SMALL, bg=BG, fg=TEXT_FAINT,
                         cursor="hand2")
        close.bind("<Button-1>", lambda e: self.close())
        c.create_window(x + CARD_W - CARD_PAD, y + CARD_PAD, window=close,
                        anchor="ne", tags="card")

    def _clamp_card(self, margin=MARGIN):
        """窗口缩小/拖动后卡片可能跑出边界，整体挪回来（只动 card，不动 hole）。"""
        c = self.lc
        box = c.bbox("card")
        if not box:
            return
        w, h = self.master.winfo_width(), self.master.winfo_height()
        dx = dy = 0
        if box[2] > w - margin:
            dx = (w - margin) - box[2]
        if box[0] + dx < margin:
            dx = margin - box[0]
        if box[3] > h - margin:
            dy = (h - margin) - box[3]
        if box[1] + dy < margin:
            dy = margin - box[1]
        if dx or dy:
            c.move("card", dx, dy)

    # ---------- 状态机 ----------

    def render(self, i):
        if self._closed:
            return
        self.i = i
        step = self.steps[i]
        c = self.lc
        c.delete("card")
        c.delete("hole")

        hole = None
        if step.get("target"):
            hole = self._target_box(step["target"], step.get("pad", 6))
            if hole:
                round_rect(c, hole[0], hole[1], hole[2], hole[3], 10,
                           fill="", outline=ACCENT, width=2, tags="hole")
        self._paint_veil(hole)
        self._draw_card(step, *step["card"])
        self._clamp_card()
        self.veil.lift()
        self.layer.lift()

    def show(self):
        self._shown = True
        self.render(0)
        # 分层窗口（WS_EX_LAYERED）里用 create_window 嵌进去的子控件，鼠标命中并不可靠 ——
        # 实测点卡片上的按钮，事件会直接穿到主界面的缩略图上。CardWindow 之所以没这问题，
        # 是因为它 show() 里抓了本地 grab。这里照做：引导期间所有点击都进内容层。
        self.layer.update_idletasks()
        try:
            self.layer.grab_set()
            self.layer.focus_force()
        except tk.TclError:
            pass
        self._watch()          # 主窗口最小化时自动收掉（见 _watch 的注释）

    def next(self):
        if self.i < len(self.steps) - 1:
            self.render(self.i + 1)
        else:
            self.close()

    def prev(self):
        if self.i > 0:
            self.render(self.i - 1)

    def _primary_action(self):
        self.next()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._job:
            try:
                self.master.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self._watch_job:
            try:
                self.master.after_cancel(self._watch_job)
            except tk.TclError:
                pass
            self._watch_job = None
        try:
            self.layer.grab_release()
        except tk.TclError:
            pass
        for win in (self.veil, self.layer):
            try:
                win.destroy()
            except tk.TclError:
                pass
        if self.on_done:
            try:
                self.on_done()
            except Exception:                       # noqa: BLE001
                pass
