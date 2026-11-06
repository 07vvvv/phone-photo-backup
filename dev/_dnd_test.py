# -*- coding: utf-8 -*-
"""dev/_dnd_test.py — 多选 / 框选 / 右键拖出 回归

不依赖真实鼠标：直接调回调并构造事件对象，跑得快也不会动到用户的鼠标。
真正「拖到资源管理器/回收站」那一步必须人手，见 README 的手工验收清单。

前置：注入一组假条目（40 张 1x1 png），不碰用户的备份库。
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dnd                            # noqa: E402
import library                        # noqa: E402
import theme                          # noqa: E402

spec = importlib.util.spec_from_file_location("appmod", os.path.join(ROOT, "app.pyw"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# 1x1 透明 PNG：只要能让缩略图线程读到真文件就行
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c6360000002000100ff6c49dc00"
    "00000049454e44ae426082")

TMP = tempfile.mkdtemp(prefix="_dndtest_")


class FakeEvent(object):
    """Tk 事件的最小替身：回调只用到坐标和修饰键。"""

    def __init__(self, x_root=0, y_root=0, state=0):
        self.x_root = x_root
        self.y_root = y_root
        self.state = state


def make_items(n, prefix="p"):
    items = []
    for i in range(n):
        name = "%s%02d.png" % (prefix, i)
        path = os.path.join(TMP, name)
        with open(path, "wb") as fh:
            fh.write(PNG)
        items.append({
            "path": path, "name": name, "size": len(PNG),
            "mtime": 1700000000 + i, "device": "Demo", "category": "Camera",
            "is_video": False, "timestamp": 1700000000 + i, "online_only": False,
        })
    return items


def main():
    checks = []

    def ok(label, cond):
        checks.append((label, bool(cond)))

    app = mod.App()
    app.update()
    for job in (getattr(app, "_migrate_job", None),):
        if job:
            try:
                app.after_cancel(job)
            except Exception:      # noqa: BLE001
                pass
    app._migrate_job = None
    if app._tour is not None:
        app._tour.close()

    # 把缓存目录挪到临时区，别往用户的备份库里写缩略图
    app.cache_dir = os.path.join(TMP, ".thumbcache")
    items = make_items(40)
    app.shown = items
    app.item_index = {id(x): i for i, x in enumerate(items)}
    app.items = items
    app._fill_grid(items)
    app.update()

    # ---- 1. 初始状态 ----
    ok("渲染出格子", len(app.tile_cells) == 40)
    ok("默认选中第一张", app.sel == {0})
    ok("选中格是强调蓝", app.tile_cells[0].cget("highlightbackground") == theme.ACCENT)
    ok("未选中格是弱灰", app.tile_cells[1].cget("highlightbackground") == theme.BORDER_SOFT)
    ok("格子边框 2px", int(app.tile_cells[0].cget("highlightthickness")) == 2)

    # ---- 2. 左键单选 / Ctrl 加选 / Shift 范围 ----
    app._sel_down(FakeEvent(), 3)
    ok("单击 = 单选", app.sel == {3})
    ok("单击后锚点跟过去", app.anchor == 3)
    app._sel_up(FakeEvent(), 3)

    app._sel_down(FakeEvent(state=0x0004), 7)      # Control
    ok("Ctrl 点击 = 加选", app.sel == {3, 7})
    app._sel_up(FakeEvent(), 7)
    app._sel_down(FakeEvent(state=0x0004), 3)      # Control 再点一次
    ok("Ctrl 再点 = 减选", app.sel == {7})
    app._sel_up(FakeEvent(), 3)

    app._sel_down(FakeEvent(), 2)
    app._sel_up(FakeEvent(), 2)
    app._sel_down(FakeEvent(state=0x0001), 6)      # Shift
    ok("Shift 点击 = 范围选", app.sel == set(range(2, 7)))
    app._sel_up(FakeEvent(), 6)

    # ---- 3. 框选 ----
    step = mod.CELL + 2 * theme.S2
    right2 = theme.S2 + step + mod.CELL      # 第二格右边界
    iny = theme.S2 + 1                       # 落进第一行（格子从 y=S2 才开始）
    ok("矩形只盖第一行前两格", app._tiles_in_rect(0, 0, right2, iny) == {0, 1})
    ok("矩形跨两行", app._tiles_in_rect(0, 0, theme.S2 + 1, right2) == {0, app._cols})
    ok("矩形在格子外不命中", app._tiles_in_rect(-999, -999, -900, -900) == set())
    ok("矩形只擦到格子边缘也算命中",
       app._tiles_in_rect(0, 0, theme.S2 + 1, iny) == {0})
    app.clear_selection()
    app._press = (0, 0, 0)
    rx, ry = app.canvas.winfo_rootx(), app.canvas.winfo_rooty()
    app._sel_drag(FakeEvent(x_root=rx, y_root=ry), 0)
    ok("未越阈值不画框", app._band is None)
    app._sel_drag(FakeEvent(x_root=rx + right2, y_root=ry + iny), 0)
    ok("越过阈值画了框", app._band is not None)
    ok("框选选中了第一行前两格", app.sel == {0, 1})
    app._sel_up(FakeEvent(), 0)
    ok("松手后矩形消失", app._band is None)

    # ---- 4. 全选 / 清空 / 空格 ----
    app.select_all()
    ok("Ctrl+A 全选", len(app.sel) == 40)
    app.clear_selection()
    ok("Esc 清空", app.sel == set())
    app.select_only(5)
    app.toggle_at(5)
    ok("空格取消当前格", app.sel == set())
    app.toggle_at(5)
    ok("空格再加回来", app.sel == {5})

    # ---- 5. 右键单击 = 菜单，右键拖动 = 拖出 ----
    seen = {"popup": 0, "drag": [], "recycle": [], "status": []}

    def fake_popup(parent, rows, x, y, **kw):
        seen["popup"] += 1
        seen["rows"] = rows
        return None

    orig_popup = mod.PopupMenu
    orig_drag = dnd.drag_files
    orig_recycle = library.recycle
    orig_ask = mod.Dialog.ask
    mod.PopupMenu = fake_popup
    mod.Dialog.ask = staticmethod(lambda *a, **kw: 1)
    library.recycle = lambda paths: (seen["recycle"].append(list(paths)) or (True, len(paths)))
    app._set_status = lambda text, *a, **kw: seen["status"].append(text)

    def fake_drag(hwnd, paths, allow=None):
        seen["drag"].append(list(paths))
        return seen.get("effect", dnd.DROPEFFECT_NONE), list(paths)

    dnd.drag_files = fake_drag

    app.select_only(2)
    app._ctx_down(FakeEvent(x_root=0, y_root=0), 2)
    app._ctx_up(FakeEvent(x_root=0, y_root=0), 2)
    ok("右键单击弹菜单", seen["popup"] == 1)
    ok("右键单击不启动拖动", seen["drag"] == [])
    ok("菜单第一项写着「复制」", "复制" in seen["rows"][0][0])
    ok("菜单里有全选与取消选择",
       any("全选" in r[0] for r in seen["rows"] if isinstance(r, tuple))
       and any("取消选择" in r[0] for r in seen["rows"] if isinstance(r, tuple)))

    # 右键点在未选中的格子上 → 先把选择换成它
    app.select_only(2)
    app._ctx_down(FakeEvent(), 9)
    app._ctx_up(FakeEvent(), 9)
    ok("右键未选中格会先单选它", app.sel == {9})

    # ---- 6. 拖动阈值 ----
    def pump(delay=0.05):
        """_begin_drag 用 after(1) 把 DoDragDrop 推回 mainloop，这里把那一拍跑掉。"""
        time.sleep(delay)
        app.update()

    app.select_only(2)
    app._ctx_down(FakeEvent(x_root=100, y_root=100), 2)
    app._ctx_drag(FakeEvent(x_root=102, y_root=100), 2)
    ok("移动 2px 不算拖动", seen["drag"] == [])
    app._ctx_drag(FakeEvent(x_root=106, y_root=100), 2)
    pump()
    ok("移动 6px 启动拖出", len(seen["drag"]) == 1)
    ok("拖出的正是选中的那个文件", seen["drag"][0] == [items[2]["path"]])

    # ---- 7. 多选一起拖 ----
    seen["drag"] = []
    app.select_all()
    app.sel = {0, 1, 2}
    app._paint_sel()
    app._ctx_down(FakeEvent(x_root=100, y_root=100), 1)
    app._ctx_drag(FakeEvent(x_root=120, y_root=100), 1)
    pump()
    ok("多选一起拖出", len(seen["drag"]) == 1 and len(seen["drag"][0]) == 3)
    ok("拖出的是三条不同的路径", len(set(seen["drag"][0])) == 3)

    # ---- 8. 拖放结果分支 ----
    app.sel = {0, 1}
    app._paint_sel()
    app._keep_scroll = False
    seen["recycle"] = []
    seen["effect"] = dnd.DROPEFFECT_MOVE
    app._do_drag([items[0]["path"], items[1]["path"]])
    ok("MOVE 走回收站而不是 os.remove", len(seen["recycle"]) == 1)
    ok("回收站收到两个文件", len(seen["recycle"][0]) == 2)

    seen["status"] = []
    seen["effect"] = dnd.DROPEFFECT_COPY
    app._do_drag([items[3]["path"]])
    ok("COPY 不动源文件", not any("回收站" in s for s in seen["status"]))
    ok("COPY 给出「副本」提示", any("副本" in s for s in seen["status"]))

    seen["status"] = []
    seen["effect"] = dnd.DROPEFFECT_NONE
    app._do_drag([items[4]["path"]])
    ok("NONE 什么都不做", not seen["status"])

    # ---- 9. 「仅在线」不能拖 ----
    app._fill_grid(make_items(3, prefix="online_"))
    app.update()
    for it in app.tiles:
        it["online_only"] = True
    app.sel = {0}
    seen["drag"] = []
    seen["status"] = []
    app._begin_drag()
    app.update()
    ok("全是在线文件时不启动拖动", seen["drag"] == [])
    ok("给出「拖不出去」提示", any("拖不出去" in s for s in seen["status"]))

    # ---- 10. 菜单作用于多选 ----
    app._fill_grid(items)
    app.update()
    app.sel = {0, 1, 2}
    app._paint_sel()
    seen["popup"] = 0
    app.tile_menu(FakeEvent(), 0)
    ok("多选时菜单首项写数量", "3" in seen["rows"][0][0])
    ok("多选时没有「用系统程序打开」",
       not any(isinstance(r, tuple) and "用系统程序打开" in r[0] for r in seen["rows"]))
    ok("多选时有「打开所在文件夹」",
       any(isinstance(r, tuple) and "打开所在文件夹" in r[0] for r in seen["rows"]))

    # ---- 11. 批量删除只重建一次时间柱 ----
    rebuilds = {"n": 0}
    orig_rebuild = app._rebuild_tree
    app._rebuild_tree = lambda *a, **kw: rebuilds.__setitem__("n", rebuilds["n"] + 1)
    app._remove_items(items[0:5])
    app._rebuild_tree = orig_rebuild
    ok("批量删 5 张只重建一次时间柱", rebuilds["n"] == 1)
    ok("被删的条目从 shown 里消失", len(app.shown) == 35)

    # ---- 12. 换组后选择回到第一张 ----
    app._fill_grid(make_items(6, prefix="grp_"))
    app.update()
    ok("换组后重新选中第一张", app.sel == {0})

    # ---- 恢复打桩 ----
    mod.PopupMenu = orig_popup
    dnd.drag_files = orig_drag
    library.recycle = orig_recycle
    mod.Dialog.ask = orig_ask
    try:
        app.destroy()
    except Exception:      # noqa: BLE001
        pass
    shutil.rmtree(TMP, ignore_errors=True)

    print("=" * 60)
    for label, passed in checks:
        print(("  PASS  " if passed else "  FAIL  ") + label)
    bad = [l for l, p in checks if not p]
    print("=" * 60)
    print("共 %d 项，失败 %d 项" % (len(checks), len(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    sys.exit(main())
