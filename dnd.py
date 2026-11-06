# -*- coding: utf-8 -*-
"""dnd.py — 把应用里的文件拖到资源管理器 / 其他程序（Windows OLE 拖放，零第三方依赖）

为什么要手写 COM
    拖到「其他应用」不是自己画个跟随图标就行的：目标程序（微信、Word、浏览器上传框、
    资源管理器、回收站）只听 Windows 的 OLE 拖放协议 —— 拖源必须实现 IDataObject 和
    IDropSource 两个 COM 接口，再调 DoDragDrop 把控制权交给系统。tkinter 没有这一层，
    第三方包又都要额外二进制。这里直接用 ctypes 按 vtable 手工建对象。

接口来源（oleidl.h / objidl.h）
    IID_IDataObject  {0000010e-0000-0000-c000-000000000046}
    IID_IDropSource  {00000121-0000-0000-c000-000000000046}
    IID_IUnknown     {00000000-0000-0000-c000-000000000046}
    CFSTR_PREFERREDDROPEFFECT = "Preferred DropEffect"，值是一个 DWORD 的 DROPEFFECT

vtable 下标（IUnknown 占 0/1/2，之后按头文件声明顺序）
    IDataObject    3 GetData | 4 GetDataHere | 5 QueryGetData | 6 GetCanonicalFormatEtc
                   7 SetData | 8 EnumFormatEtc | 9 DAdvise | 10 DUnadvise | 11 EnumDAdvise
    IEnumFORMATETC 3 Next | 4 Skip | 5 Reset | 6 Clone
    IDropSource    3 QueryContinueDrag | 4 GiveFeedback

右键拖动为什么能成立
    DoDragDrop 自己不判断「鼠标松开没有」—— 它把 GetKeyState 的结果喂给
    IDropSource::QueryContinueDrag，由**我们**决定继续、取消还是结束。所以里面判
    MK_RBUTTON 就行：右键还按着返回 S_OK，松了返回 DRAGDROP_S_DROP。
    （左键按下时同样认，这样以后要换成左键拖动不用改协议层。）

收尾永远不永久删
    DoDragDrop 回来时 pdwEffect 是目标**实际接受**的效果。MOVE 的语义是「目标已经
    搬走了，源该动手了」—— 调用方拿它去走 library.recycle()（Windows 回收站，可还原），
    这里绝不做 os.remove。
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import POINTER, byref, c_int, c_long, c_uint, c_ulong, c_void_p, c_wchar_p, wintypes

HRESULT = c_long
ULONG = c_ulong

S_OK = 0
S_FALSE = 1
E_NOINTERFACE = -2147467262
E_NOTIMPL = -2147467263
E_POINTER = -2147467261
E_FAIL = -2147467259

DRAGDROP_S_DROP = 0x00040100
DRAGDROP_S_CANCEL = 0x00040101
DRAGDROP_S_USEDEFAULTCURSORS = 0x00040102

DROPEFFECT_NONE = 0
DROPEFFECT_COPY = 1
DROPEFFECT_MOVE = 2
DROPEFFECT_LINK = 4

CF_HDROP = 15
CF_TEXT = 1
DVASPECT_CONTENT = 1
TYMED_HGLOBAL = 1
GMEM_MOVEABLE = 0x0002
DATADIR_GET = 1
COINIT_APARTMENTTHREADED = 0x2
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
POLL_KEY = MK_LBUTTON | MK_RBUTTON

# vtable 下标：三处断言都从这里取，改错一个数整个拖放就静默失灵
_VT_RELEASE = 2
_VT_GET_DATA = 3
_VT_QUERY_GET_DATA = 5
_VT_ENUM_FORMAT_ETC = 8
_VT_NEXT = 3
_VT_RESET = 5
_VT_QUERY_CONTINUE_DRAG = 3
_VT_GIVE_FEEDBACK = 4


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class FORMATETC(ctypes.Structure):
    _fields_ = [("cfFormat", wintypes.WORD),
                ("ptd", c_void_p),
                ("dwAspect", wintypes.DWORD),
                ("lindex", c_long),
                ("tymed", wintypes.DWORD)]


class _STGMEDIUM_UNION(ctypes.Union):
    _fields_ = [("hGlobal", c_void_p),
                ("pstm", c_void_p),
                ("pstg", c_void_p),
                ("pUnkForRelease", c_void_p)]


class STGMEDIUM(ctypes.Structure):
    _fields_ = [("tymed", wintypes.DWORD),
                ("u", _STGMEDIUM_UNION),
                ("pUnkForRelease", c_void_p)]


class DROPFILES(ctypes.Structure):
    _fields_ = [("pFiles", wintypes.DWORD),
                ("pt", wintypes.POINT),
                ("fNC", wintypes.BOOL),
                ("fWide", wintypes.BOOL)]


class _Iface(ctypes.Structure):
    _fields_ = [("lpVtbl", POINTER(c_void_p))]


def _load():
    ole32 = ctypes.WinDLL("ole32")
    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")

    ole32.CoInitializeEx.argtypes = [c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = HRESULT
    ole32.DoDragDrop.argtypes = [c_void_p, c_void_p, wintypes.DWORD,
                                 POINTER(wintypes.DWORD)]
    ole32.DoDragDrop.restype = HRESULT
    ole32.ReleaseStgMedium.argtypes = [POINTER(STGMEDIUM)]
    ole32.ReleaseStgMedium.restype = None

    user32.RegisterClipboardFormatW.argtypes = [c_wchar_p]
    user32.RegisterClipboardFormatW.restype = c_uint
    user32.GetKeyState.argtypes = [c_int]
    user32.GetKeyState.restype = ctypes.c_short

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = c_void_p
    kernel32.GlobalLock.argtypes = [c_void_p]
    kernel32.GlobalLock.restype = c_void_p
    kernel32.GlobalUnlock.argtypes = [c_void_p]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [c_void_p]
    kernel32.GlobalFree.restype = c_void_p
    return ole32, user32, kernel32


_ole32, _user32, _kernel32 = _load()

_iid_datetime = None            # 占位，避免未使用告警
_CF_PREFERRED = _user32.RegisterClipboardFormatW("Preferred DropEffect") or CF_TEXT


def dropfiles_blob(paths):
    """CF_HDROP 的负载：DROPFILES 头 + UTF-16LE 双 NUL 结尾的文件名表。

    和 library._dropfiles_blob 是同一份格式；这里独立实现是因为拖放要自己
    分配 HGLOBAL，不能借用剪贴板那条路径的临时内存。
    """
    head = DROPFILES()
    head.pFiles = ctypes.sizeof(DROPFILES)
    head.fWide = True
    names = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"
    return bytes(head) + names.encode("utf-16-le")


def _hgblob(data):
    """把 bytes 装进一块 HGLOBAL，返回句柄；失败返回 None。"""
    h = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return None
    p = _kernel32.GlobalLock(h)
    if not p:
        _kernel32.GlobalFree(h)
        return None
    ctypes.memmove(p, data, len(data))
    _kernel32.GlobalUnlock(h)
    return h


def _hgdword(value):
    buf = ctypes.c_uint32(value)
    return _hgblob(bytes(buf))


def _iid_equals(ptr, guid):
    if not ptr:
        return False
    a = ctypes.cast(ptr, POINTER(GUID)).contents
    return ctypes.string_at(byref(a), ctypes.sizeof(GUID)) == \
        ctypes.string_at(byref(guid), ctypes.sizeof(GUID))


def _guid(d1, d2, d3, tail):
    g = GUID()
    g.Data1, g.Data2, g.Data3 = d1, d2, d3
    for i, b in enumerate(tail):
        g.Data4[i] = b
    return g


IID_IUnknown = _guid(0x00000000, 0x0000, 0x0000,
                     [0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46])
IID_IDataObject = _guid(0x0000010E, 0x0000, 0x0000,
                        [0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46])
IID_IDropSource = _guid(0x00000121, 0x0000, 0x0000,
                        [0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46])


class _ComObject(object):
    """把一组 Python 函数焊成一块 C 风格的 vtable，托管成 COM 对象。

    COM 只认「接口指针 -> vtable -> 函数指针」这一种形状，Python 对象它不认识。
    所以这里手动摆一张函数指针表，再 fake 一个只含 lpVtbl 的结构体指过去。
    所有回调对象和指针数组都必须挂在实例上 —— 一旦被 GC，系统下一次回调就直接崩。
    """

    def __init__(self, specs):
        self.refs = 1
        self._protos = []
        self._ptrs = []
        for restype, argtypes, fn in specs:
            cb = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(fn)
            self._protos.append(cb)
            self._ptrs.append(ctypes.cast(cb, c_void_p))
        self._arr = (c_void_p * len(self._ptrs))(*self._ptrs)
        self._iface = _Iface()
        self._iface.lpVtbl = ctypes.cast(self._arr, POINTER(c_void_p))
        self.addr = ctypes.cast(byref(self._iface), c_void_p)

    def query_interface(self, this, riid, ppv):
        for g in (IID_IUnknown, IID_IDataObject, IID_IDropSource):
            if _iid_equals(riid, g):
                if ppv:
                    ctypes.cast(ppv, POINTER(c_void_p))[0] = self.addr
                self.refs += 1
                return S_OK
        if ppv:
            ctypes.cast(ppv, POINTER(c_void_p))[0] = None
        return E_NOINTERFACE

    def add_ref(self, this):
        self.refs += 1
        return self.refs

    def release(self, this):
        self.refs -= 1
        return max(self.refs, 0)


class _EnumFormatEtc(_ComObject):
    """IEnumFORMATETC：告诉系统「这个拖源能提供哪几种格式」。"""

    def __init__(self, formats):
        self._formats = list(formats)
        self._pos = 0
        super(_EnumFormatEtc, self).__init__([
            (HRESULT, [c_void_p, c_void_p], self.query_interface),
            (ULONG, [c_void_p], self.add_ref),
            (ULONG, [c_void_p], self.release),
            (HRESULT, [wintypes.ULONG, POINTER(FORMATETC), POINTER(wintypes.ULONG)],
             self.next),
            (HRESULT, [wintypes.ULONG], self.skip),
            (HRESULT, [], self.reset),
            (HRESULT, [POINTER(c_void_p)], self.clone),
        ])

    def next(self, this, celt, rgelt, fetched):
        got = 0
        while got < celt and self._pos < len(self._formats):
            rgelt[got] = self._formats[self._pos]
            self._pos += 1
            got += 1
        if fetched:
            fetched[0] = got
        return S_OK if got == celt else S_FALSE

    def skip(self, this, celt):
        self._pos = min(self._pos + celt, len(self._formats))
        return S_OK

    def reset(self, this):
        self._pos = 0
        return S_OK

    def clone(self, this, ppenum):
        other = _EnumFormatEtc(self._formats)
        other._pos = self._pos
        if ppenum:
            ppenum[0] = other.addr
        _keep.append(other)          # 系统释放前不能让它被 GC
        other.refs += 1
        return S_OK


# 所有活着的 COM 对象都挂这儿：系统持有指针期间若被 GC，下一次回调就是野指针
_keep = []


class DataObject(_ComObject):
    """IDataObject：真正提供文件数据的那一方。"""

    def __init__(self, paths, effect=DROPEFFECT_MOVE):
        self.paths = [os.path.abspath(p) for p in paths]
        self.effect = effect
        self._blob = dropfiles_blob(self.paths) if self.paths else b""
        self._dword = ctypes.c_uint32(effect)
        self.formats = [
            FORMATETC(CF_HDROP, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL),
            FORMATETC(_CF_PREFERRED, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL),
        ]
        super(DataObject, self).__init__([
            (HRESULT, [c_void_p, c_void_p], self.query_interface),
            (ULONG, [c_void_p], self.add_ref),
            (ULONG, [c_void_p], self.release),
            (HRESULT, [POINTER(FORMATETC), POINTER(STGMEDIUM)], self.get_data),
            (HRESULT, [POINTER(FORMATETC), POINTER(STGMEDIUM)], self.get_data_here),
            (HRESULT, [POINTER(FORMATETC)], self.query_get_data),
            (HRESULT, [POINTER(FORMATETC), POINTER(FORMATETC)],
             self.get_canonical_format_etc),
            (HRESULT, [POINTER(FORMATETC), POINTER(STGMEDIUM), wintypes.BOOL],
             self.set_data),
            (HRESULT, [wintypes.DWORD, POINTER(c_void_p)], self.enum_format_etc),
            (HRESULT, [POINTER(FORMATETC), wintypes.DWORD, c_void_p,
                       wintypes.DWORD, POINTER(wintypes.DWORD)], self.d_advise),
            (HRESULT, [wintypes.DWORD], self.d_unadvise),
            (HRESULT, [POINTER(c_void_p)], self.enum_d_advise),
        ])

    def _match(self, fmt):
        if not fmt:
            return None
        f = fmt.contents
        if f.cfFormat == CF_HDROP and f.tymed & TYMED_HGLOBAL:
            return self._blob
        if f.cfFormat == _CF_PREFERRED and f.tymed & TYMED_HGLOBAL:
            return bytes(self._dword)
        return None

    def get_data(self, this, fmt, medium):
        data = self._match(fmt)
        if data is None or not medium:
            return E_POINTER if not medium else S_FALSE
        h = _hgblob(data)
        if not h:
            return E_FAIL
        medium[0].tymed = TYMED_HGLOBAL
        medium[0].u.hGlobal = h
        medium[0].pUnkForRelease = None     # 交给 OLE 用 ReleaseStgMedium 释放
        return S_OK

    def get_data_here(self, this, fmt, medium):
        return E_NOTIMPL          # 系统从不要求拖源填这里

    def query_get_data(self, this, fmt):
        return S_OK if self._match(fmt) is not None else S_FALSE

    def get_canonical_format_etc(self, this, fin, fout):
        return E_NOTIMPL          # 允许返回 E_NOTIMPL，系统会自己用原格式

    def set_data(self, this, fmt, medium, release):
        return E_NOTIMPL

    def enum_format_etc(self, this, direction, ppenum):
        if direction != DATADIR_GET or not ppenum:
            return E_NOTIMPL
        enum = _EnumFormatEtc(self.formats)
        ppenum[0] = enum.addr
        enum.refs += 1
        _keep.append(enum)
        return S_OK

    def d_advise(self, this, fmt, advf, sink, conn, reserved):
        return E_NOTIMPL

    def d_unadvise(self, this, conn):
        return E_NOTIMPL

    def enum_d_advise(self, this, ppenum):
        return E_NOTIMPL


def continue_decision(escaped, key_state):
    """纯函数：根据 Esc 与键态给出 QueryContinueDrag 的返回值（便于自检）。"""
    if escaped:
        return DRAGDROP_S_CANCEL
    if key_state & POLL_KEY:
        return S_OK
    return DRAGDROP_S_DROP


class DropSource(_ComObject):
    """IDropSource：决定拖动什么时候结束，并给出光标反馈。"""

    def __init__(self):
        self.ended = None
        super(DropSource, self).__init__([
            (HRESULT, [c_void_p, c_void_p], self.query_interface),
            (ULONG, [c_void_p], self.add_ref),
            (ULONG, [c_void_p], self.release),
            (HRESULT, [wintypes.BOOL, wintypes.DWORD], self.query_continue_drag),
            (HRESULT, [wintypes.DWORD], self.give_feedback),
        ])

    def query_continue_drag(self, this, escaped, key_state):
        hr = continue_decision(bool(escaped), int(key_state))
        if hr != S_OK and self.ended is None:
            self.ended = hr
        return hr

    def give_feedback(self, this, effect):
        # 用系统的「复制 / 移动」光标即可，自己画反而跟目标窗口的提示打架
        return DRAGDROP_S_USEDEFAULTCURSORS


def live_key_state():
    """当前鼠标左右键有没有按下（拖放启动时用于兜底判断）。"""
    return int(_user32.GetKeyState(0x01) & 0x8000 and MK_LBUTTON or 0) | \
        int(_user32.GetKeyState(0x02) & 0x8000 and MK_RBUTTON or 0)


def drag_files(hwnd, paths, allow=DROPEFFECT_COPY | DROPEFFECT_MOVE):
    """把 paths 拖出去，返回目标实际接受的效果（DROPEFFECT_*）。

    调用前请先释放 Tk 的隐式 grab（`self.grab_release()` + `ReleaseCapture()`），
    否则 Tk 的 SetCapture 会和 OLE 抢鼠标。这个函数会阻塞到拖动结束。
    """
    files = [p for p in (os.path.abspath(x) for x in paths) if os.path.isfile(p)]
    if not files:
        return DROPEFFECT_NONE, []
    _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)   # 已初始化会返回 S_FALSE，无害
    data = DataObject(files, DROPEFFECT_MOVE)
    src = DropSource()
    effect = wintypes.DWORD(allow)
    try:
        hr = _ole32.DoDragDrop(data.addr, src.addr, allow, byref(effect))
    finally:
        data.refs -= 1
        src.refs -= 1
    if hr < 0:
        return DROPEFFECT_NONE, files
    return int(effect.value) & allow, files


# --------------------------------------------------------------------------
# 自检（python dnd.py）
# --------------------------------------------------------------------------


def _vt_call(obj, idx, restype, argtypes, *args):
    """按 vtable 下标直接调一次，用来验证那张函数指针表摆对了位置。"""
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    fn = proto(obj._arr[idx])          # c_void_p 元素直接当整数地址喂进去
    return fn(obj.addr, *args)


def selfcheck():
    checks = []

    def ok(label, cond):
        checks.append((label, bool(cond)))

    # 1) 结构体尺寸必须和 C 头文件一致，错一个字节传过去就是内存越界
    ok("DROPFILES = 20 字节", ctypes.sizeof(DROPFILES) == 20)
    ok("FORMATETC = 32 字节（64 位）", ctypes.sizeof(FORMATETC) == 32)
    ok("STGMEDIUM = 24 字节（64 位）", ctypes.sizeof(STGMEDIUM) == 24)

    # 2) vtable 下标：写错不会报错，只会静默失灵
    ok("IDataObject.GetData 在 3", _VT_GET_DATA == 3)
    ok("IDataObject.QueryGetData 在 5", _VT_QUERY_GET_DATA == 5)
    ok("IDataObject.EnumFormatEtc 在 8", _VT_ENUM_FORMAT_ETC == 8)
    ok("IEnumFORMATETC.Next 在 3", _VT_NEXT == 3)
    ok("IDropSource.QueryContinueDrag 在 3", _VT_QUERY_CONTINUE_DRAG == 3)
    ok("IDropSource.GiveFeedback 在 4", _VT_GIVE_FEEDBACK == 4)

    # 3) CF_HDROP 的字节结构
    blob = dropfiles_blob([r"C:\a.jpg"])
    head = DROPFILES.from_buffer_copy(blob[:20])
    ok("pFiles 指向文件名表起点", head.pFiles == 20)
    ok("fWide=1（UTF-16LE）", head.fWide == 1)
    ok("文件名按 UTF-16LE 存且双 NUL 结尾",
       blob.endswith(b"\x00\x00\x00\x00")
       and blob[20:] == (r"C:\a.jpg" + "\0\0").encode("utf-16-le"))
    two = dropfiles_blob([r"C:\a.jpg", r"C:\b.png"])
    ok("多文件用单个 NUL 分隔",
       two[20:] == (r"C:\a.jpg" + "\0" + r"C:\b.png" + "\0\0").encode("utf-16-le"))

    # 4) 键态判定
    ok("Esc 取消", continue_decision(True, MK_RBUTTON) == DRAGDROP_S_CANCEL)
    ok("右键按住 → 继续", continue_decision(False, MK_RBUTTON) == S_OK)
    ok("左键按住 → 继续", continue_decision(False, MK_LBUTTON) == S_OK)
    ok("全松开 → 放手", continue_decision(False, 0) == DRAGDROP_S_DROP)

    # 5) 两个格式都能查
    data = DataObject([r"C:\a.jpg"])
    fmt = FORMATETC(CF_HDROP, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL)
    fpref = FORMATETC(_CF_PREFERRED, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL)
    unknown = FORMATETC(CF_TEXT, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL)
    ok("QueryGetData 认 CF_HDROP", _vt_call(
        data, _VT_QUERY_GET_DATA, HRESULT, [POINTER(FORMATETC)], byref(fmt)) == S_OK)
    ok("QueryGetData 认 Preferred DropEffect", _vt_call(
        data, _VT_QUERY_GET_DATA, HRESULT, [POINTER(FORMATETC)], byref(fpref)) == S_OK)
    ok("QueryGetData 拒绝未知格式", _vt_call(
        data, _VT_QUERY_GET_DATA, HRESULT, [POINTER(FORMATETC)], byref(unknown)) != S_OK)

    # 6) GetData 真的能交出正确的 HGLOBAL
    med = STGMEDIUM()
    hr = _vt_call(data, _VT_GET_DATA, HRESULT,
                  [POINTER(FORMATETC), POINTER(STGMEDIUM)], byref(fmt), byref(med))
    got = b""
    if hr == S_OK and med.u.hGlobal:
        p = _kernel32.GlobalLock(med.u.hGlobal)
        got = ctypes.string_at(p, len(data._blob))
        _kernel32.GlobalUnlock(med.u.hGlobal)
        _ole32.ReleaseStgMedium(byref(med))
    ok("GetData(CF_HDROP) 返回 S_OK", hr == S_OK)
    ok("GetData 交出的字节与源一致", got == data._blob)

    # 7) 枚举器给得出两种格式
    penum = c_void_p()
    hr = _vt_call(data, _VT_ENUM_FORMAT_ETC, HRESULT,
                  [wintypes.DWORD, POINTER(c_void_p)], DATADIR_GET, byref(penum))
    ok("EnumFormatEtc 返回 S_OK", hr == S_OK and penum.value)
    if hr == S_OK and penum.value:
        enum = ctypes.cast(penum, POINTER(_Iface))
        arr = enum.contents.lpVtbl
        proto = ctypes.WINFUNCTYPE(HRESULT, c_void_p, wintypes.ULONG,
                                   POINTER(FORMATETC), POINTER(wintypes.ULONG))
        fn = proto(arr[_VT_NEXT])
        buf = (FORMATETC * 2)()
        fetched = wintypes.ULONG(0)
        fn(penum.value, 2, buf, byref(fetched))
        ok("枚举出 2 种格式", fetched.value == 2)
        ok("第一种是 CF_HDROP", buf[0].cfFormat == CF_HDROP)

    # 8) 空输入与不存在的文件都不该启动拖动
    ok("空列表直接返回 NONE", drag_files(None, [])[0] == DROPEFFECT_NONE)
    ok("不存在的文件被过滤掉",
       drag_files(None, [r"C:\__definitely_missing__.jpg"])[0] == DROPEFFECT_NONE)

    print("=" * 56)
    for label, passed in checks:
        print(("  PASS  " if passed else "  FAIL  ") + label)
    bad = [l for l, p in checks if not p]
    print("=" * 56)
    print("共 %d 项，失败 %d 项" % (len(checks), len(bad)))
    return 0 if not bad else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    return selfcheck()


if __name__ == "__main__":
    sys.exit(main())
