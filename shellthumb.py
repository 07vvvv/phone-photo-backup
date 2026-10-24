# -*- coding: utf-8 -*-
"""shellthumb.py — 借 Windows Shell 的缩略图提供程序给视频取首帧

为什么是它
    网格里视频格子原本只能显示「▶ 视频」占位，因为 Pillow 不带视频解码器。
    但资源管理器明明能显示视频首帧 —— 那正是 Shell 的 thumbnail provider 干的活。
    直接调同一个接口，效果和资源管理器一致，零第三方依赖，也不用自己搭解码器
    （用 Media Foundation 的 IMFSourceReader 自己抓帧要多写一百多行 ctypes）。

接口来源（shobjidl_core.h）
    IID_IShellItemImageFactory = {bcc18b79-ba16-442f-80c4-8a59c30c463b}
    SHCreateItemFromParsingName(pszPath, pbc, riid, ppv)
    GetImage(SIZE size, SIIGBF flags, HBITMAP *phbm)      ← vtable 下标 3
"""
from __future__ import annotations

import ctypes
import os
from ctypes import POINTER, byref, c_int, c_long, c_void_p, c_wchar_p, wintypes

COINIT_APARTMENTTHREADED = 0x2
RPC_E_CHANGED_MODE = -2147417850        # 0x80010106，线程已经在 MTA 里了

IID_ISHELLITEMIMAGEFACTORY = "{bcc18b79-ba16-442f-80c4-8a59c30c463b}"

SIIGBF_RESIZETOFIT = 0x00
SIIGBF_THUMBNAILONLY = 0x08             # 只接受真缩略图，不拿通用图标糊弄

_VT_RELEASE = 2
_VT_GET_IMAGE = 3

_ole32 = ctypes.windll.ole32
_shell32 = ctypes.windll.shell32
_gdi32 = ctypes.windll.gdi32
_user32 = ctypes.windll.user32


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", c_long), ("cy", c_long)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", c_long), ("bmWidth", c_long), ("bmHeight", c_long),
                ("bmWidthBytes", c_long), ("bmPlanes", wintypes.WORD),
                ("bmBitsPixel", wintypes.WORD), ("bmBits", c_void_p)]


class _BITMAPINFOHEADER(ctypes.Structure):
    """BI_RGB + 32bpp 时不需要调色板，所以这个 header 直接当 BITMAPINFO 用。"""
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", c_long), ("biHeight", c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", c_long), ("biYPelsPerMeter", c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


_shell32.SHCreateItemFromParsingName.argtypes = [
    c_wchar_p, c_void_p, POINTER(_GUID), POINTER(c_void_p)]
_shell32.SHCreateItemFromParsingName.restype = c_long
_ole32.IIDFromString.argtypes = [c_wchar_p, POINTER(_GUID)]
_ole32.IIDFromString.restype = c_long
_ole32.CoInitializeEx.argtypes = [c_void_p, wintypes.DWORD]
_ole32.CoInitializeEx.restype = c_long
_gdi32.GetObjectW.argtypes = [wintypes.HANDLE, c_int, c_void_p]
_gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                             wintypes.UINT, c_void_p, POINTER(_BITMAPINFOHEADER),
                             wintypes.UINT]
_gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
# ReleaseDC 必须声明 argtypes：不声明时 ctypes 按 32 位 int 传句柄，
# 而 HDC 是 64 位指针，值一大就 OverflowError —— 这个错误是概率性的，
# 取决于当次 GetDC 拿到的句柄值，最容易在「明明取到了缩略图却偶尔报错」时被误判成格式问题。
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = c_int


def _call(ptr, index, argtypes, *args):
    """按 vtable 下标调 COM 方法。"""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)
    return proto(vtbl[index])(ptr, *args)


def _to_pil(hbm):
    """HBITMAP → PIL Image。"""
    from PIL import Image

    bmp = _BITMAP()
    if not _gdi32.GetObjectW(hbm, ctypes.sizeof(_BITMAP), byref(bmp)):
        return None
    w, h = bmp.bmWidth, bmp.bmHeight
    if w <= 0 or h <= 0:
        return None

    hdr = _BITMAPINFOHEADER()
    hdr.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    hdr.biWidth = w
    hdr.biHeight = -h          # 负数 = top-down，省得再上下翻一次
    hdr.biPlanes = 1
    hdr.biBitCount = 32
    hdr.biCompression = 0      # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    hdc = _user32.GetDC(None)
    try:
        if not _gdi32.GetDIBits(hdc, hbm, 0, h, buf, byref(hdr), 0):
            return None
    finally:
        _user32.ReleaseDC(None, hdc)
    # 高位是没用的填充字节，所以是 BGRX 而不是 RGBA
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


def frame(path, size=256):
    """取文件的首帧缩略图 → PIL Image，失败（含系统没装该格式的解码器）→ None。

    后台线程里调用没问题，这里自己管 COM 的初始化。
    """
    hr = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    if hr < 0 and hr != RPC_E_CHANGED_MODE:
        return None
    must_uninit = hr >= 0
    try:
        iid = _GUID()
        if _ole32.IIDFromString(IID_ISHELLITEMIMAGEFACTORY, byref(iid)) < 0:
            return None
        item = c_void_p()
        if _shell32.SHCreateItemFromParsingName(
                os.path.abspath(path), None, byref(iid), byref(item)) < 0 or not item:
            return None
        try:
            hbm = c_void_p()
            hr = _call(item, _VT_GET_IMAGE,
                       [_SIZE, c_int, POINTER(c_void_p)],
                       _SIZE(size, size),
                       SIIGBF_RESIZETOFIT | SIIGBF_THUMBNAILONLY, byref(hbm))
            if hr < 0 or not hbm:
                return None
            try:
                return _to_pil(hbm)
            finally:
                _gdi32.DeleteObject(hbm)
        finally:
            _call(item, _VT_RELEASE, [])
    except OSError:
        return None
    finally:
        if must_uninit:
            _ole32.CoUninitialize()


if __name__ == "__main__":
    import sys

    checks = []
    ok = lambda label, cond: checks.append((label, bool(cond)))   # noqa: E731
    ok("vtable 下标与 shobjidl_core.h 一致",
       (_VT_RELEASE, _VT_GET_IMAGE) == (2, 3))
    # 任何拿句柄当参数又没声明 argtypes 的调用都会在句柄值偏大时溢出，这种错是概率性的
    for fn in (_user32.GetDC, _user32.ReleaseDC, _gdi32.GetDIBits,
               _gdi32.GetObjectW, _gdi32.DeleteObject):
        ok(f"{fn.__name__} 声明了参数类型", bool(fn.argtypes))

    paths = sys.argv[1:]
    if not paths:
        print("用法: python shellthumb.py <视频或图片>…   （不给路径就只验接口层）")
    for p in paths:
        im = frame(p)
        ok(f"{os.path.basename(p)} → {im.size if im else '取不到'}", im is not None)

    print("=" * 56)
    for label, passed in checks:
        print(("  PASS  " if passed else "  FAIL  ") + label)
    print("=" * 56)
    bad = [l for l, p in checks if not p]
    print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
    raise SystemExit(1 if bad else 0)
