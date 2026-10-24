# -*- coding: utf-8 -*-
"""mfplay.py — 用 Windows 自带的 MFPlay 把视频直接播进窗口（零第三方依赖）

为什么是它
    动态照片里藏的那段 MP4 要在应用内播，而且不能卡。tkinter 自己没有视频解码器；
    自己用 Python 逐帧解码再转成图像送进 Tk，每帧都要一次编解码 + 拷贝，必掉帧。
    mfplay.dll（Windows 7 起随系统提供）走 Media Foundation：硬件解码 + EVR 渲染，
    画面由系统直接画进我们给的窗口句柄，Python 这边一次像素都不碰。

接口来源（抄自 mingw-w64 的 mfplay.h，与官方 SDK 一致）
    IID_IMFPMediaPlayer = {a714590a-58af-430a-85bf-44f5ec838d85}
    MFPCreateMediaPlayer(pwszURL, fStartPlayback, options, pCallback, hWnd, &player)
    传 URL + fStartPlayback=TRUE 时它会自己建 media item 并开播，
    所以用不到 CreateMediaItemFromURL / SetMediaItem。

vtable 下标（IUnknown 占 0/1/2，之后按 mfplay.h 的声明顺序）
    3 Play | 4 Pause | 5 Stop | 6 FrameStep | 7 SetPosition
    8 GetPosition | 9 GetDuration | 10 SetRate | 11 GetRate | 12 GetSupportedRates
    13 GetState | … | 38 Shutdown
"""
from __future__ import annotations

import ctypes
import os
from ctypes import (POINTER, byref, c_int, c_long, c_longlong, c_ulong, c_void_p,
                    c_wchar_p, wintypes)

HRESULT = c_long

MF_VERSION = 0x00020070          # (MF_SDK_VERSION 2 << 16) | MF_API_VERSION 0x0070
MFSTARTUP_LITE = 0x00000001
COINIT_APARTMENTTHREADED = 0x2

_VT_RELEASE = 2
_VT_PLAY = 3
_VT_PAUSE = 4
_VT_STOP = 5
_VT_SET_POSITION = 7
_VT_GET_POSITION = 8
_VT_GET_DURATION = 9
_VT_GET_STATE = 13
_VT_SHUTDOWN = 38

STATE_EMPTY, STATE_STOPPED, STATE_PLAYING, STATE_PAUSED, STATE_SHUTDOWN = range(5)

_mfplat = None
_mfplay = None
_error = None
_started = False


def available():
    """MFPlay 是否可用。老系统或精简版 Windows 可能缺 dll，返回 False 让上层回退。"""
    global _mfplat, _mfplay, _error
    if _mfplay is not None:
        return True
    try:
        ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        _mfplat = ctypes.WinDLL("mfplat.dll")
        _mfplay = ctypes.WinDLL("mfplay.dll")
        _mfplat.MFStartup.argtypes = [c_ulong, wintypes.DWORD]
        _mfplat.MFStartup.restype = HRESULT
        _mfplay.MFPCreateMediaPlayer.argtypes = [
            c_wchar_p, wintypes.BOOL, ctypes.c_uint32,
            c_void_p, wintypes.HWND, POINTER(c_void_p),
        ]
        _mfplay.MFPCreateMediaPlayer.restype = HRESULT
        return True
    except OSError as exc:
        _error = f"{type(exc).__name__}: {exc}"
        return False


def media_path(path):
    """MFPlay 要的「源」：直接给绝对路径。

    别用 file:// URL —— URL 里的中文会被百分号编码成 %E8%A7%86…，而 MF 的 URL
    解析器不按 UTF-8 还原，带中文名的文件会直接 0x80070002（找不到文件）。
    裸路径交给 Win32 文件 API，Unicode 一切正常。
    """
    return os.path.abspath(path)


def _call(ptr, index, argtypes, *args):
    """按 vtable 下标调 COM 方法。ptr 是接口指针（c_void_p）。"""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(HRESULT, c_void_p, *argtypes)
    return proto(vtbl[index])(ptr, *args)


_user32 = ctypes.windll.user32
_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, c_wchar_p, c_wchar_p, wintypes.DWORD,
    c_int, c_int, c_int, c_int, wintypes.HWND, c_void_p, c_void_p, c_void_p,
]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.ShowWindow.argtypes = [wintypes.HWND, c_int]
_user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
_user32.DefWindowProcW.restype = ctypes.c_longlong
# GetModuleHandleW 返回的是真指针（实测 0x7ff6xxxxxxxx），不声明 restype 会被截成
# 32 位的 0x1cfc0000 再塞进 WNDCLASSEXW.hInstance —— 注册能过，但类实例句柄是错的
_kernel32 = ctypes.windll.kernel32
_kernel32.GetModuleHandleW.argtypes = [c_wchar_p]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
# GDI 句柄是 32 位值，0x80000000 以上会被默认的 c_int 读成负数
_gdi32 = ctypes.windll.gdi32
_gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
_gdi32.CreateSolidBrush.restype = wintypes.HBRUSH


class _RECT(ctypes.Structure):
    _fields_ = [("left", c_long), ("top", c_long), ("right", c_long), ("bottom", c_long)]


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _PROPVARIANT(ctypes.Structure):
    """只用到 hVal 那一路，但整个结构必须按真实大小声明，否则参数会错位。"""
    _fields_ = [("vt", wintypes.WORD), ("r1", wintypes.WORD),
                ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                ("hVal", c_longlong), ("_union_pad", c_longlong)]


VT_I8, VT_UI8 = 20, 21                    # 实测 GetDuration 回来的是 VT_UI8
_POSITIONTYPE_100NS = _GUID()          # 全零 GUID，MFPlay 表示「100 纳秒」

_user32.GetClientRect.argtypes = [wintypes.HWND, POINTER(_RECT)]
_user32.GetClientRect.restype = wintypes.BOOL
_user32.MoveWindow.argtypes = [wintypes.HWND, c_int, c_int, c_int, c_int, wintypes.BOOL]
_user32.MoveWindow.restype = wintypes.BOOL

WS_CHILD, WS_VISIBLE = 0x40000000, 0x10000000
SW_SHOW = 5

# 视频窗口相对查看器客户区的留白：(左, 上, 右, 下)
# 左右 70px 让开翻页按钮；下方 34px 留给进度条
MARGIN = (70, 8, 70, 34)


def host_rect(parent_hwnd):
    """父窗口客户区里视频该占的矩形 → (x, y, 宽, 高)。

    每次都取实时尺寸，所以窗口一变大变小，拿这个重摆一下就跟上了。
    """
    r = _RECT()
    if not _user32.GetClientRect(wintypes.HWND(parent_hwnd), byref(r)):
        return 0, 0, 16, 16
    ml, mt, mr, mb = MARGIN
    return ml, mt, max(r.right - ml - mr, 16), max(r.bottom - mt - mb, 16)

# 视频容器用自注册的窗口类，而不是系统 STATIC：
# STATIC 的默认背景是白色，解码器还没送出第一帧之前，用户会看到一块白板。
VIDEO_CLASS = "PhonePhotoVideoHost"
VIDEO_BG = 0x001A1616          # COLORREF = 0x00BBGGRR，即 #16161A，与查看器底色一致

_class_ready = False
_wndproc_ref = None            # 必须长期持有，否则回调被 GC 掉会直接崩进程
_brush_ref = None


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT),
        ("lpfnWndProc", c_void_p), ("cbClsExtra", c_int), ("cbWndExtra", c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", c_wchar_p), ("lpszClassName", c_wchar_p),
        ("hIconSm", wintypes.HICON),
    ]


def _ensure_class():
    """注册深色背景的视频容器窗口类。返回 False 表示只能退回系统 STATIC。"""
    global _class_ready, _wndproc_ref, _brush_ref
    if _class_ready:
        return True
    try:
        _wndproc_ref = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM,
        )(lambda h, m, w, l: _user32.DefWindowProcW(h, m, w, l))
        _brush_ref = _gdi32.CreateSolidBrush(VIDEO_BG)
        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(_wndproc_ref, c_void_p)
        wc.hInstance = _kernel32.GetModuleHandleW(None)
        wc.hbrBackground = _brush_ref
        wc.lpszClassName = VIDEO_CLASS
        # 同名类已注册时也返回 0，不当作失败；真有问题会在建窗口那步暴露
        _user32.RegisterClassExW(ctypes.byref(wc))
        _class_ready = True
        return True
    except OSError:
        return False


def make_host_window(parent_hwnd):
    """建一个深色背景的容器子窗口（初始隐藏，要自己 ShowWindow）。失败返回 0。

    深色背景是必须的：解码器送出第一帧之前窗口里什么都没有，
    如果底色是系统默认的白，用户会先看到一块白板。
    """
    cls = VIDEO_CLASS if _ensure_class() else "STATIC"
    x, y, w, h = host_rect(parent_hwnd)
    return _user32.CreateWindowExW(
        0, cls, "", WS_CHILD, x, y, w, h,               # 先不 WS_VISIBLE
        wintypes.HWND(parent_hwnd), None, None, None)


class InlineVideo:
    """把一段视频播到自建的子窗口里。用完必须 close()。

    容器窗口由我们自己建、自己销毁（而不是直接拿 Tk 控件当渲染目标），有三个原因：
      * 背景色可控 —— 换成深色，解码器出第一帧之前不会闪白板
      * 位置可控 —— 左右留出边距，视频不会盖住查看器的翻页按钮
      * close 时 DestroyWindow 保证画面彻底消失，不指望 MFPlay 把 EVR 窗口清干净
        （实测只调 Shutdown 时，最后一帧会留在屏幕上）

    窗口**建好时不显示**，要等调用方确认画面出来了再 show()：
    MFPlay 建 pipeline 要几百毫秒，这段时间窗口里什么都没有。
    """

    def __init__(self, path, parent_hwnd):
        global _started
        if not available():
            raise OSError(_error or "MFPlay 不可用")
        if not _started:
            hr = _mfplat.MFStartup(MF_VERSION, MFSTARTUP_LITE)
            if hr < 0:
                raise OSError(f"MFStartup 失败 hr=0x{hr & 0xFFFFFFFF:08X}")
            _started = True

        self._hwnd = make_host_window(parent_hwnd)
        if not self._hwnd:
            raise OSError("创建视频容器窗口失败")
        self.shown = False

        self._player = c_void_p()
        hr = _mfplay.MFPCreateMediaPlayer(
            media_path(path), True, 0, None, self._hwnd, byref(self._player))
        if hr < 0 or not self._player:
            self._player = None
            self._destroy_window()
            raise OSError(f"MFPCreateMediaPlayer 失败 hr=0x{hr & 0xFFFFFFFF:08X}")

    def state(self):
        if not self._player:
            return -1
        st = c_int()
        if _call(self._player, _VT_GET_STATE, [POINTER(c_int)], byref(st)) >= 0:
            return st.value
        return -1

    def playing(self):
        return self.state() == STATE_PLAYING

    def play(self):
        """继续播放（暂停后用）；播放中调用无事发生。"""
        if self._player:
            _call(self._player, _VT_PLAY, [])

    def pause(self):
        if self._player:
            _call(self._player, _VT_PAUSE, [])

    def _clock(self, index):
        """GetPosition / GetDuration 的公共部分 → 秒。

        签名是 (REFGUID guidPositionType, PROPVARIANT *pv)，不是听起来那样的
        (LONGLONG *)—— 2009 年 MSDN 文档写错过，照那个写会一直拿到 0。
        guidPositionType 传 MFP_POSITIONTYPE_100NS（全零 GUID），回来的是
        VT_I8，单位 100 纳秒。
        """
        if not self._player:
            return 0.0
        pv = _PROPVARIANT()
        hr = _call(self._player, index,
                   [POINTER(_GUID), POINTER(_PROPVARIANT)],
                   byref(_POSITIONTYPE_100NS), byref(pv))
        if hr >= 0 and pv.vt in (VT_I8, VT_UI8):
            return pv.hVal / 10_000_000
        return 0.0

    def position(self):
        return self._clock(_VT_GET_POSITION)

    def duration(self):
        return self._clock(_VT_GET_DURATION)

    def seek(self, seconds):
        """跳到指定秒数（进度条拖动用）。签名同样带 guidPositionType。"""
        if not self._player:
            return
        pv = _PROPVARIANT()
        pv.vt = VT_I8
        pv.hVal = int(seconds * 10_000_000)
        _call(self._player, _VT_SET_POSITION,
              [POINTER(_GUID), POINTER(_PROPVARIANT)],
              byref(_POSITIONTYPE_100NS), byref(pv))

    def fit(self, parent_hwnd):
        """按父窗口的当前尺寸重新摆放视频窗口。

        查看器可以改变大小，而子窗口不会自己跟着变 —— 建好时是多少就永远是多少。
        显示前和每次 <Configure> 都调一次，画面就不会停在初始尺寸上。
        """
        if self._hwnd:
            _user32.MoveWindow(self._hwnd, *host_rect(parent_hwnd), True)

    def show(self):
        """显示视频窗口。调用方应等到 state 变成 PLAYING 之后再调，避免先闪一块空板。"""
        if self._hwnd and not self.shown:
            _user32.ShowWindow(self._hwnd, SW_SHOW)
            self.shown = True

    def _destroy_window(self):
        if self._hwnd:
            _user32.DestroyWindow(self._hwnd)
            self._hwnd = None

    def close(self):
        p, self._player = self._player, None
        if p:
            try:
                _call(p, _VT_STOP, [])
                _call(p, _VT_SHUTDOWN, [])
            finally:
                _call(p, _VT_RELEASE, [])
        self._destroy_window()


if __name__ == "__main__":
    # 自检：只验接口层（不建窗口、不播视频）
    checks = []
    ok = lambda label, cond: checks.append((label, bool(cond)))  # noqa: E731
    ok("mfplay.dll / mfplat.dll 可加载", available())
    ok("中文路径不被转义（转成 file:// 会 0x80070002 打不开）",
       media_path(r"D:\a b\中文 名.mp4") == r"D:\a b\中文 名.mp4")
    ok("vtable 下标与 mfplay.h 一致",
       (_VT_RELEASE, _VT_PLAY, _VT_PAUSE, _VT_STOP, _VT_SET_POSITION,
        _VT_GET_POSITION, _VT_GET_DURATION, _VT_GET_STATE, _VT_SHUTDOWN)
       == (2, 3, 4, 5, 7, 8, 9, 13, 38))
    ok("视频四周留白：左右给翻页按钮、下方给进度条",
       MARGIN == (70, 8, 70, 34))
    ok("视频容器窗口类可注册（深色底，避免先闪白板）", _ensure_class())
    ok("容器底色是查看器同款深色", VIDEO_BG == 0x001A1616)
    # 返回句柄 / 指针的调用必须声明类型：默认的 c_int 会把 64 位值截断，
    # 这种错不会报异常，只会让窗口类拿到一个错的 HINSTANCE / HBRUSH
    for fn in (_kernel32.GetModuleHandleW, _gdi32.CreateSolidBrush,
               _user32.CreateWindowExW, _user32.GetClientRect, _user32.MoveWindow,
               _user32.RegisterClassExW, _user32.DefWindowProcW):
        ok(f"{fn.__name__} 声明了参数/返回类型", bool(fn.argtypes) and bool(fn.restype))
    hr = _mfplat.MFStartup(MF_VERSION, MFSTARTUP_LITE)
    ok(f"MFStartup 成功 hr=0x{hr & 0xFFFFFFFF:08X}", hr >= 0)
    if hr >= 0:
        _mfplat.MFShutdown()
    print("=" * 56)
    for label, passed in checks:
        print(("  PASS  " if passed else "  FAIL  ") + label)
    print("=" * 56)
    bad = [l for l, p in checks if not p]
    print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
    raise SystemExit(1 if bad else 0)
