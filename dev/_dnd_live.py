# -*- coding: utf-8 -*-
"""dev/_dnd_live.py — 手动验收：真实鼠标把文件拖出去

自动测试只能验证「回调接线对不对」，验证不了「Windows 真的收下了这批文件」——
后者必须有真人按住鼠标拖。这个脚本就是给人用的：

    1. python dev/_dnd_live.py
    2. 窗口里会有一张卡片，**按住右键把它拖到桌面上**，松手
    3. 桌面出现 `拖放验收.txt` + `拖放验收-2.txt` → 说明 OLE 链路完全通了
    4. 再把卡片拖到**回收站**上松手 → 窗口里的状态会变成「进回收站」
    5. 按 Esc 或点「退出」结束

脚本会把 DoDragDrop 返回的 effect 打出来，方便判断目标到底接受了没有：
    1 = COPY（目标复制走了，源保留）
    2 = MOVE（目标搬走了，我们这边负责送回收站）
    0 = NONE（没落到能接收的地方，或者半路取消了）
"""
import ctypes
import os
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dnd      # noqa: E402
import library  # noqa: E402
import theme    # noqa: E402

TMP = tempfile.mkdtemp(prefix="_dndlive_")


def make_files():
    paths = []
    for name in ("拖放验收.txt", "拖放验收-2.txt"):
        p = os.path.join(TMP, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("这是一次拖放验收，可以安心删除。\n")
        paths.append(p)
    return paths


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    files = make_files()

    root = tk.Tk()
    root.title("拖放验收 · 按住右键拖出去")
    root.geometry("460x300+320+240")
    root.configure(bg=theme.BG)

    tk.Label(root, text="按住右键拖动下面这张卡片", font=theme.F_MED,
             bg=theme.BG, fg=theme.TEXT).pack(pady=(18, 4))
    tk.Label(root, text="拖到桌面 → 桌面应出现 2 个 txt；拖到回收站 → 进回收站",
             font=theme.F_SMALL, bg=theme.BG, fg=theme.TEXT_DIM).pack()

    card = tk.Frame(root, bg=theme.ACCENT_SOFT, width=360, height=120,
                    highlightthickness=2, highlightbackground=theme.ACCENT)
    card.pack(pady=16)
    card.pack_propagate(False)
    tk.Label(card, text="2 个文件\n右键按住拖走", font=theme.F_SMALL,
             bg=theme.ACCENT_SOFT, fg=theme.ACCENT_DEEP).pack(expand=True)

    status = tk.Label(root, text="等待拖动…", font=theme.F_SMALL,
                      bg=theme.BG, fg=theme.TEXT_FAINT)
    status.pack()

    state = {"down": None, "fired": False}

    def on_down(event):
        state["down"] = (event.x_root, event.y_root)
        state["fired"] = False

    def on_motion(event):
        p = state["down"]
        if not p or state["fired"]:
            return
        if abs(event.x_root - p[0]) + abs(event.y_root - p[1]) < 4:
            return
        state["fired"] = True
        root.after(1, fire)

    def fire():
        try:
            root.grab_release()
        except tk.TclError:
            pass
        ctypes.windll.user32.ReleaseCapture()
        status.config(text="拖动中…")
        root.update()
        effect, got = dnd.drag_files(root.winfo_id(), files)
        if effect & dnd.DROPEFFECT_COPY:
            status.config(text="effect=%d  COPY — 目标复制走了，源文件还在" % effect,
                          fg=theme.ACCENT_DEEP)
        elif effect & dnd.DROPEFFECT_MOVE:
            status.config(text="effect=%d  MOVE — 目标搬走了，正在送回收站" % effect,
                          fg=theme.WARN)
            for p in got:
                if os.path.isfile(p):
                    library.recycle([p])
        else:
            status.config(text="effect=%d  NONE — 没落到能接收的地方，或中途取消" % effect,
                          fg=theme.TEXT_FAINT)
        print("effect =", effect, " 文件 =", got)

    card.bind("<Button-3>", on_down)
    card.bind("<B3-Motion>", on_motion)
    root.bind("<Escape>", lambda e: root.destroy())
    tk.Button(root, text="退出", command=root.destroy,
              font=theme.F_SMALL).pack(side="bottom", pady=8)

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
