# -*- coding: utf-8 -*-
"""_tour_test.py — 新手指引回归：高亮定位 / 缩放跟随 / 降级 / 关闭落标记。

不依赖真实备份库，自己搭一个与 app.pyw 同构的宿主（真实 theme 控件），
验的是「引导层能不能准确套住真实控件」以及几条容易退化的边界。

    python dev/_tour_test.py           跑一遍并窗口截图到 dev/_tour_shot_*.png
"""
from __future__ import annotations

import ctypes
import os
import sys

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:                                   # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import tkinter as tk                                # noqa: E402

import theme                                        # noqa: E402
import welcome                                      # noqa: E402
from theme import (ACCENT, ACCENT_SOFT, BG, BORDER_SOFT, F_MED, F_SMALL,  # noqa: E402
                   F_TINY, F_TITLE, S1, S2, S3, S4, S5, SURFACE, TEXT,
                   TEXT_DIM, TEXT_FAINT, RoundButton)

PASS = []
FAIL = []


def ok(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  OK   %s" % name)
    else:
        FAIL.append(name)
        print("  FAIL %s   %s" % (name, detail))


class Host(tk.Tk):
    """与 app.pyw 顶栏/主体同构的宿主，用真实 theme 控件。"""

    def __init__(self):
        tk.Tk.__init__(self)
        self.overrideredirect(True)
        self.geometry("1220x800+80+60")
        self.attributes("-topmost", True)
        self.configure(bg=BG)
        self._build()

    def _build(self):
        tk.Frame(self, bg=BG, height=32).pack(fill="x")
        top = tk.Frame(self, bg=BG, height=72)
        top.pack(fill="x", padx=S5, pady=(S4, S3))
        top.pack_propagate(False)

        left = tk.Frame(top, bg=BG)
        left.pack(side="left", anchor="w")
        head = tk.Frame(left, bg=BG)
        head.pack(anchor="w")
        self.title_lbl = tk.Label(head, text="手机照片备份", font=F_TITLE, bg=BG, fg=TEXT)
        self.title_lbl.pack(side="left")
        self.tour_btn = RoundButton(head, "新手指引", None, parent_bg=BG,
                                    height=26, padx=12, font=F_TINY)
        self.tour_btn.pack(side="left", padx=(S3, 0), pady=(2, 0))

        right = tk.Frame(top, bg=BG)
        right.pack(side="right", anchor="n")
        self.backup_btn = RoundButton(right, "开始备份", None, primary=True,
                                      height=34, padx=22)
        self.backup_btn.pack(side="right")
        self.more_btn = RoundButton(right, "更多 ▾", None, height=34, padx=14)
        self.more_btn.pack(side="right", padx=(0, S2))

        view = tk.Frame(right, bg=BG)
        view.pack(side="right", padx=(0, S3))
        tk.Label(view, text="查看", font=F_TINY, bg=BG, fg=TEXT_FAINT).pack(side="left")

        self.dev_box = tk.Frame(right, bg=BG)
        self.dev_box.pack(side="right", padx=(0, S2))
        tk.Label(self.dev_box, text="连接", font=F_TINY, bg=BG,
                 fg=TEXT_FAINT).pack(side="left")
        dp = theme.DevicePicker(self.dev_box, width=186, height=34, parent_bg=BG)
        dp.pack(side="left", padx=(S1, 0))
        dp.set_items(["Pixel-8-Pro"], default="Pixel-8-Pro")
        RoundButton(self.dev_box, "刷新", None, parent_bg=BG,
                    height=34, padx=14).pack(side="left", padx=(S2, 0))
        self.wireless_btn = RoundButton(self.dev_box, "无线", None, parent_bg=BG,
                                        height=34, padx=14)
        self.wireless_btn.pack(side="left", padx=(S2, 0))

        bar = tk.Frame(self, bg=SURFACE, height=36)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(self, height=1, bg=theme.DIVIDER).pack(side="bottom", fill="x")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=S5, pady=(0, S4))
        self.nav_card = tk.Frame(body, bg=BG, width=248, highlightthickness=1,
                                 highlightbackground=BORDER_SOFT)
        self.nav_card.pack(side="left", fill="y")
        self.nav_card.pack_propagate(False)
        inner = tk.Frame(self.nav_card, bg=BG)
        inner.pack(fill="both", expand=True, padx=S2, pady=S3)
        tk.Label(inner, text="2026", font=F_MED, bg=BG, fg=TEXT).pack(anchor="w")
        row = tk.Frame(inner, bg=ACCENT_SOFT)
        row.pack(fill="x")
        tk.Label(row, text="2026-10", font=F_SMALL, bg=ACCENT_SOFT,
                 fg=ACCENT).pack(side="left", padx=(26, 0))
        grid = tk.Canvas(body, bg=BG, highlightthickness=0, bd=0)
        grid.pack(side="left", fill="both", expand=True, padx=(S3, 0))


def box_of(w, app):
    mx, my = app.winfo_rootx(), app.winfo_rooty()
    return (w.winfo_rootx() - mx, w.winfo_rooty() - my,
            w.winfo_width(), w.winfo_height())


def near(a, b, tol=2):
    return abs(a - b) <= tol


def main():
    print("== 新手指引回归 ==")
    app = Host()
    app.update()
    app.update_idletasks()

    done = {"n": 0}
    tour = welcome.Tour(app,
                        {"dev": app.dev_box, "backup": app.backup_btn,
                         "nav": app.nav_card, "tour": app.tour_btn,
                         "wireless": app.wireless_btn},
                        on_done=lambda: done.update(n=done["n"] + 1))

    # ---- 0. 顶栏常驻入口「新手指引」 ----
    app.update()
    tx, ty, tw, th = box_of(app.tour_btn, app)
    lx, ly, lw, lh = box_of(app.title_lbl, app)
    ok("顶栏有这个按钮（RoundButton 实例）", isinstance(app.tour_btn, RoundButton))
    ok("按钮与标题同一行、紧随标题右侧",
       tx >= lx + lw and tx - (lx + lw) <= 24 and abs((ty + th / 2.0) - (ly + lh / 2.0)) <= 6,
       "按钮=(%d,%d,%d,%d) 标题右=%d" % (tx, ty, tw, th, lx + lw))
    ok("按钮比主按钮小一号（辅助动作不抢注意力）",
       th < app.backup_btn.winfo_height() and tw < app.backup_btn.winfo_width(),
       "指引 %sx%s vs 备份 %sx%s" % (tw, th, app.backup_btn.winfo_width(),
                                     app.backup_btn.winfo_height()))
    ok("按钮在顶栏内（没有掉到主体区）", ty < 100)

    clicks = {"n": 0}
    app.tour_btn._cmd = lambda: clicks.update(n=clicks["n"] + 1)
    app.tour_btn._release(type("E", (), {"x": tw // 2, "y": th // 2})())
    ok("点按钮会触发它的命令（重看入口接得上）", clicks["n"] == 1)

    # ---- 1. 两层窗口尺寸与主窗口一致 ----
    app.update()
    w, h = app.winfo_width(), app.winfo_height()
    ok("遮罩层尺寸与主窗口一致",
       tour.veil.winfo_width() == w and tour.veil.winfo_height() == h,
       "%sx%s vs %sx%s" % (tour.veil.winfo_width(), tour.veil.winfo_height(), w, h))
    ok("内容层尺寸与主窗口一致",
       tour.layer.winfo_width() == w and tour.layer.winfo_height() == h)

    # ---- 2. 遮罩属性 ----
    ok("遮罩层带 alpha（半透明压暗）",
       abs(float(tour.veil.attributes("-alpha")) - welcome.VEIL_ALPHA) < 1e-6)
    ok("遮罩层带魔术色透明",
       tour.veil.attributes("-transparentcolor").lower() == welcome.MAGIC.lower())

    # ---- 3. 四步高亮都精确套住目标控件 ----
    tour.show()
    app.update()
    cases = [("dev", 8), ("backup", 6), ("nav", 0), ("wireless", 6), ("tour", 6)]
    for key, pad in cases:
        wdg = {"dev": app.dev_box, "backup": app.backup_btn,
               "nav": app.nav_card, "wireless": app.wireless_btn,
               "tour": app.tour_btn}[key]
        bx, by, bw, bh = box_of(wdg, app)
        hole = tour._target_box(key, pad)
        if hole is None:
            ok("高亮「%s」定位" % key, False, "返回 None")
            continue
        good = (near(hole[0], bx - pad) and near(hole[1], by - pad)
                and near(hole[2], bx + bw + pad) and near(hole[3], by + bh + pad))
        ok("高亮「%s」定位" % key, good,
           "hole=%s 控件=(%d,%d,%d,%d) pad=%d" % (hole, bx, by, bw, bh, pad))

    # ---- 4. 第 1 步无高亮 = 整屏压暗 ----
    tour.render(0)
    app.update()
    ok("第 1 步整屏压暗（无洞口）", tour.lc.bbox("hole") is None)

    # ---- 5. 步骤切换 ----
    tour.render(0)
    tour.next()
    ok("next() 前进到第 2 步", tour.i == 1)
    tour.prev()
    ok("prev() 退回第 1 步", tour.i == 0)
    tour.prev()
    ok("第 1 步再 prev() 不越界", tour.i == 0)

    # ---- 6. 缩放后高亮重新计算 ----
    tour.render(1)
    app.update()
    before = tour._target_box("dev", 8)
    app.geometry("1000x700+140+90")
    app.update()
    app.update_idletasks()
    tour._resync()
    app.update()
    after = tour._target_box("dev", 8)
    ok("窗口缩小后高亮框重算", before != after,
       "before=%s after=%s" % (before, after))
    ok("缩放后遮罩层跟随新尺寸",
       tour.veil.winfo_width() == app.winfo_width(),
       "%s vs %s" % (tour.veil.winfo_width(), app.winfo_width()))

    # 顶栏多了一个控件，得确认最窄时不会把右侧那排挤出去
    app.update()
    bx2, _, bw2, _ = box_of(app.backup_btn, app)
    ok("最窄窗口（1000）下「开始备份」仍完整可见",
       bx2 + bw2 <= app.winfo_width() - S5 + 2,
       "主按钮右边界=%d，窗口宽=%d" % (bx2 + bw2, app.winfo_width()))
    tx2, _, tw2, th2 = box_of(app.tour_btn, app)
    ok("最窄窗口下顶部入口仍完整可见",
       tw2 > 0 and tx2 + tw2 <= app.winfo_width() - S5 + 2,
       "入口=(%d,%d) 宽=%d，窗口宽=%d" % (tx2, th2, tw2, app.winfo_width()))

    # ---- 7. 目标不可见时降级 ----
    hole_none = tour._target_box("no_such_key", 6)
    ok("未知目标返回 None（降级为整屏压暗）", hole_none is None)

    # ---- 8. 关闭 ----
    tour.render(2)
    tour.close()
    ok("close() 后遮罩层已销毁", not tour.veil.winfo_exists())
    ok("close() 后内容层已销毁", not tour.layer.winfo_exists())
    ok("close() 触发 on_done 回调一次", done["n"] == 1)

    # ---- 9. 数据完整性 ----
    ok("五个步骤都定义了标题/正文/主按钮",
       all(s.get("title") and s.get("body") and s.get("primary")
           for s in welcome.STEPS))
    ok("步骤里的 target 都能在 targets 里找到",
       all(s.get("target") in (None, "dev", "backup", "wireless", "nav", "tour")
           for s in welcome.STEPS))
    ok("最后一步的文案指向顶栏入口（改名时不会漂移）",
       "新手指引" in str(welcome.STEPS[-1]),
       "第 5 步正文里没出现「新手指引」四个字")

    app.destroy()
    print("\n共 %d 项，失败 %d 项" % (len(PASS) + len(FAIL), len(FAIL)))
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
