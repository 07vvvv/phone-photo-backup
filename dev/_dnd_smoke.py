# -*- coding: utf-8 -*-
"""一次性冒烟：确认 DoDragDrop 能在 Tk 主线程里被调用、不崩、按时返回。

不做真实拖动（那要人手），只验证 OLE 链路本身是通的：
没有按键按着时 DoDragDrop 应当立刻返回 DROPEFFECT_NONE。
"""
import ctypes
import os
import sys
import tempfile
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dnd  # noqa: E402


def main():
    tmp = os.path.join(tempfile.gettempdir(), "_dnd_smoke_canary.txt")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("smoke")
    root = tk.Tk()
    root.title("dnd 冒烟")
    root.geometry("320x120+200+200")
    tk.Label(root, text="冒烟测试中，窗口会自动消失").pack(expand=True)
    result = {}

    def go():
        try:
            root.grab_release()
            ctypes.windll.user32.ReleaseCapture()
            hwnd = root.winfo_id()
            eff, files = dnd.drag_files(hwnd, [tmp])
            result["effect"] = eff
            result["files"] = files
        except Exception as exc:                       # noqa: BLE001
            result["error"] = "%s: %s" % (type(exc).__name__, exc)
        finally:
            root.after(50, root.destroy)

    root.after(400, go)
    root.mainloop()
    try:
        os.remove(tmp)
    except OSError:
        pass
    if "error" in result:
        print("FAIL", result["error"])
        return 1
    print("effect =", result["effect"], "(0 = NONE，无按键时正确)")
    print("files  =", result["files"])
    return 0 if result["effect"] == dnd.DROPEFFECT_NONE else 1


if __name__ == "__main__":
    sys.exit(main())
