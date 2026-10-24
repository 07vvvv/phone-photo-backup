# -*- coding: utf-8 -*-
"""library.py — 本地照片库索引、缩略图缓存、回收站删除

因为备份时已按 <根>/YYYY/YYYY-MM-DD/ 落盘，年月的分组**直接从路径解析**，
不需要读 EXIF，所以扫描几万个文件也是秒级。

ponytail: 依赖「目录结构 = 时间结构」这个约定。若以后允许用户改目录规则，
这里要改成读 EXIF（Pillow 的 getexif）或落一个索引文件。
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time

import shellthumb

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".3gp", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
MEDIA_EXT = IMAGE_EXT | VIDEO_EXT

THUMB_DIR = ".thumbcache"
UNKNOWN = "未知时间"
UNKNOWN_DEVICE = "未归类"
UNKNOWN_CATEGORY = "未分类"
LEGACY_DEVICE = "未归类（迁移前）"

_RE_YEAR = re.compile(r"^\d{4}$")
_RE_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_error_log_count = 0


def log_error(where, src, exc):
    """把失败原因追加到 exe（或脚本）旁边的 error.log。

    打包成 windowed 程序后异常会被静默吞掉，没有日志就只能靠猜。最多记 5 条，
    避免一次扫描刷出几百行。
    """
    global _error_log_count
    if _error_log_count >= 5:
        return
    _error_log_count += 1
    try:
        import datetime
        base = os.path.dirname(os.path.abspath(
            sys.executable if getattr(sys, "frozen", False) else __file__))
        with open(os.path.join(base, "error.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [{where}] {src} "
                     f"-> {type(exc).__name__}: {exc}\n")
    except Exception:  # noqa: BLE001
        pass


def is_video(name):
    return os.path.splitext(name)[1].lower() in VIDEO_EXT


# 云盘「按需文件」标记。带这个属性的占位文件本地一个字节都没有，数据在云上；
# 云过滤驱动会去召回，客户端没运行就直接失败 —— Windows 错误 362
# (ERROR_CLOUD_FILE_UNSUCCESSFUL)，到 Python 这边是 OSError [Errno 22] Invalid argument。
# 实测：OneDrive 没开时，OneDrive\Microsoft Edge Drop Files 里的 7 张图全都读不出来。
_ATTR_RECALL_ON_DATA = 0x400000
_ATTR_RECALL_ON_OPEN = 0x40000


def is_online_only(path, st=None):
    """这个文件是不是「只存在云上、本地没数据」的占位文件。

    是的话就别去读它了：读不出内容，而且每读一次都得等驱动超时。
    """
    if st is None:
        try:
            st = os.stat(path)
        except OSError:
            return False
    return bool(getattr(st, "st_file_attributes", 0)
                & (_ATTR_RECALL_ON_DATA | _ATTR_RECALL_ON_OPEN))


def _split(rel):
    """把相对目录路径解析成 (设备, 类别, 年, 日)。

    两种结构都要认：
        新：<设备>/<类别>/<年>/<年-月-日>
        老：<年>/<年-月-日>            ← 迁移前的库
    认不出来的一律丢进「未归类 / 未知时间」，不会因此崩掉。
    """
    parts = [] if rel in (".", "") else rel.split(os.sep)
    if len(parts) >= 4 and _RE_YEAR.match(parts[2]) and _RE_DAY.match(parts[3]):
        return parts[0], parts[1], parts[2], parts[3]
    if len(parts) >= 2 and _RE_YEAR.match(parts[0]) and _RE_DAY.match(parts[1]):
        return LEGACY_DEVICE, UNKNOWN_CATEGORY, parts[0], parts[1]
    return UNKNOWN_DEVICE, UNKNOWN_CATEGORY, UNKNOWN, UNKNOWN


def scan(root):
    """扫描备份根，返回条目列表（按时间倒序）。

    每条：{path, name, size, mtime, device, category, year, month, day,
           is_video, timestamp}
    """
    items = []
    if not os.path.isdir(root):
        return items
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath) == THUMB_DIR:
            dirnames[:] = []
            continue
        device, category, year, day = _split(os.path.relpath(dirpath, root))
        month = day[:7] if _RE_DAY.match(day) else UNKNOWN
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() not in MEDIA_EXT:
                continue
            path = os.path.join(dirpath, fn)
            try:
                st = os.stat(path)
            except OSError:
                continue
            items.append({
                "path": path, "name": fn, "size": st.st_size, "mtime": st.st_mtime,
                "device": device, "category": category,
                "year": year, "month": month, "day": day,
                "is_video": is_video(fn), "timestamp": st.st_mtime,
                "online_only": is_online_only(path, st),
            })
    items.sort(key=lambda it: (it["mtime"], it["name"]), reverse=True)
    return items


def group_tree(items):
    """→ {year: {month: [items]}}，年月都按倒序。"""
    tree = {}
    for it in items:
        tree.setdefault(it["year"], {}).setdefault(it["month"], []).append(it)
    ordered = {}
    for year in sorted(tree, reverse=True):
        ordered[year] = {m: tree[year][m] for m in sorted(tree[year], reverse=True)}
    return ordered


def thumb(src, cache_dir, box=200, crop=True):
    """生成/复用缩略图，返回缓存文件路径。

    crop=True 会居中裁剪成正方形 —— 网格里每个格子尺寸一致，视觉才立得住
    （Immich / Google Photos 都是这个做法）。

    视频靠 shellthumb 借 Windows Shell 取首帧（和资源管理器显示的是同一张）；
    「Pillow 打不开的图」也退回同一条路 —— HEIC/HEIF（iPhone 和部分安卓的默认
    格式）要系统解码器，Pillow 并不自带。两边都拿不到才返回 None，由界面显示占位。
    """
    try:
        st = os.stat(src)
    except OSError:
        return None
    if is_online_only(src, st):
        # 云盘占位文件：本地没数据。读了要等驱动超时，还必然失败，
        # 所以直接判它没有缩略图，交给界面显示「仅在线」。
        return None
    key = hashlib.sha1(
        f"{src}|{st.st_mtime_ns}|{st.st_size}|{box}|{int(crop)}".encode("utf-8")
    ).hexdigest()
    out = os.path.join(cache_dir, key[:2], key + ".jpg")
    # 只认非空的缓存文件：进程被杀/磁盘写满会留下 0 字节的半截文件，
    # 光看「文件存在」会把它当成品，那张图就永远是占位符了
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return out
    try:
        from PIL import Image, ImageOps
        if is_video(src):
            im = shellthumb.frame(src, box)
        else:
            try:
                with Image.open(src) as fh:
                    im = ImageOps.exif_transpose(fh)  # 手机照片常带方向标记，必须摆正
                    im.load()                         # 读进内存，后面就不依赖文件句柄了
            except Exception:      # noqa: BLE001
                # Pillow 打不开不等于这张图废了：HEIC/HEIF（iPhone 和部分安卓的
                # 默认格式）要系统解码器，Pillow 不带。实测库里 13 张「坏图」
                # 全是这类 —— 扩展名写着 .jpg/.png，内容其实是 ftyp heic。
                # Windows Shell 装了对应扩展就能解，借它一手，别让格子永远空着。
                im = shellthumb.frame(src, box)
        if im is None:
            return None
        if crop:
            im = ImageOps.fit(im, (box, box), Image.LANCZOS)
        else:
            im.thumbnail((box, box))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        tmp = out + ".part"
        im.convert("RGB").save(tmp, "JPEG", quality=85)
        os.replace(tmp, out)      # 原子落盘：中途出事也只是留下个 .part，不会毒化缓存
        return out
    except Exception as exc:  # noqa: BLE001
        log_error("缩略图生成", src, exc)
        return None


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


# --------------------------------------------------------------------------
# 动态照片（Motion Photo）：一张 JPEG，末尾额外粘了一段 MP4
# --------------------------------------------------------------------------


MOTION_HEAD = 64 * 1024      # XMP 写在 JPEG 开头的 APP1 段里，读这么多够用


def motion_range(path):
    """动态照片 → 内嵌 MP4 的 (起点, 终点)；不是动态照片 → None。

    两种 XMP 写法都认：
      * 新版（Google / 三星 / 小米）：Container:Item 里 Item:Mime="video/mp4" 配 Item:Length="N"
      * 旧版 GCamera：MicroVideoOffset="N"（N = MP4 起点距文件尾的字节数）

    MP4 一律贴在文件末尾，所以起点 = 文件大小 - N。

    ponytail: 只读文件头 64KB，XMP 若被塞到更后面会漏检 —— 手上的样本都在最前面，
    真漏了再改成按 APP1 段长度完整读。
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(MOTION_HEAD)
    except OSError:
        return None
    if b"video/mp4" not in head and b"MicroVideoOffset" not in head:
        return None
    m = (re.search(rb'Item:Mime="video/mp4"[^>]{0,300}Item:Length="(\d+)"', head)
         or re.search(rb'Item:Length="(\d+)"[^>]{0,300}Item:Mime="video/mp4"', head))
    if m:
        n = int(m.group(1))
    else:
        m = re.search(rb'MicroVideoOffset="(\d+)"', head)
        if not m:
            return None
        n = int(m.group(1))
    if not 0 < n < size:
        return None
    return size - n, size


def extract_motion(path):
    """把动态照片里嵌的那段 MP4 切到临时文件。→ (成功?, 临时文件路径 或 错误说明)

    切出来是因为 Media Foundation 只认「一个完整的视频文件 / URL」，
    没法直接读 JPEG 尾部那段。文件很小（几 MB），拷贝一次几毫秒。
    """
    import tempfile

    rng = motion_range(path)
    if not rng:
        return False, "这个文件里没有内嵌视频"
    try:
        # 文件名带 PID：两个实例同时看动态照片时不会互相覆盖同一个临时文件
        out = os.path.join(tempfile.gettempdir(),
                           f"PhonePhotoBackup_motion_{os.getpid()}.mp4")
        with open(path, "rb") as src, open(out, "wb") as dst:
            src.seek(rng[0])
            left = rng[1] - rng[0]
            while left > 0:
                chunk = src.read(min(1 << 20, left))
                if not chunk:
                    break
                dst.write(chunk)
                left -= len(chunk)
        return True, out
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def recycle(paths):
    """把文件移入 Windows 回收站（可还原），而不是永久删除。

    只接受本地文件路径；调用方必须保证绝不把手机上的路径传进来。
    """
    paths = [os.path.abspath(p) for p in paths if os.path.isfile(p)]
    if not paths:
        return True, 0
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        FO_DELETE = 3
        FOF_ALLOWUNDO = 0x40          # 进回收站
        FOF_NOCONFIRMATION = 0x10
        FOF_SILENT = 0x4
        FOF_NOERRORUI = 0x400

        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = "\0".join(paths) + "\0\0"   # 双 NUL 结尾的多字符串
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        # 删东西这一路不靠默认推断：返回码就是「到底删没删掉」的唯一依据
        fn = ctypes.windll.shell32.SHFileOperationW
        fn.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
        fn.restype = ctypes.c_int
        rc = fn(ctypes.byref(op))
        return rc == 0, len(paths)
    except Exception as exc:      # noqa: BLE001
        return False, str(exc)


# --------------------------------------------------------------------------
# 本机文件：这台电脑上的图片视频（只读展示，原文件一个都不动）
# --------------------------------------------------------------------------


LOCAL_DEVICE = "本机文件"       # 「查看」下拉里作为一个伪设备出现
LOCAL_CATEGORY = "本机"

# 只扫用户目录下这几个位置 —— 别去碰程序目录和系统目录
LOCAL_DIRS = ("Pictures", "Desktop", "Downloads", "Documents", "Videos", "OneDrive")

# 整棵子树不要。QQ 是用户明确说不要的：那边 1.4 万个媒体文件里九成是表情包和缩略图
LOCAL_DROP_TREES = ("tencent files",)

# 目录名/文件名里含这些段就丢掉：表情包、缩略图、缓存、程序图标、头像、
# 在线状态图标、更新程序自带的资源图…… 判据是「人会不会想看这张图」
LOCAL_SKIP_PARTS = ("emoji", "sysface", "smiley", "emoticon", "sticker",
                    "thumb", "cache", "flashfransfer", "icon", "avator", "avatar",
                    "onlinestatus", "vasupdatesystem", "resource", "temp", "tmp")

LOCAL_SKIP_DIRS = {"appdata", "node_modules", ".git", "__pycache__", ".venv",
                   "venv", "$recycle.bin", ".gradle", ".npm", ".cache"}

_EXIF_TIME_TAGS = (36867, 36868)     # DateTimeOriginal / DateTimeDigitized


def local_roots(home=None):
    """本机文件要扫的根目录。"""
    home = home or os.path.expanduser("~")
    return [os.path.join(home, d) for d in LOCAL_DIRS]


def shot_time(path):
    """一张图的「什么时候拍的」→ (时间戳, 'YYYY', 'YYYY-MM', 'YYYY-MM-DD')。

    EXIF 拍摄时间优先；截图、下载的图、聊天图基本都没有 EXIF（实测这批命中的是 0 张），
    那就退回文件修改时间 —— 对这类图来说，「落到这个文件的时间」本身就是最有意义的。
    """
    ts = None
    if os.path.splitext(path)[1].lower() in (".jpg", ".jpeg", ".heic", ".heif",
                                             ".tif", ".tiff"):
        try:
            from PIL import Image
            with Image.open(path) as im:
                ex = im.getexif()
                raw = next((ex.get(t) for t in _EXIF_TIME_TAGS if ex.get(t)), None)
            if raw:
                ts = time.mktime(time.strptime(str(raw).strip()[:19],
                                               "%Y:%m:%d %H:%M:%S"))
        except Exception:      # noqa: BLE001 —— 坏图/无 EXIF 都走文件时间
            ts = None
    if ts is None:
        try:
            ts = os.path.getmtime(path)
        except OSError:
            ts = 0
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    return ts, day[:4], day[:7], day


def scan_local(roots=None, exclude=()):
    """扫本机图片视频 → 条目列表（字段与 scan() 一致，按时间倒序）。

    和 scan() 的差别：本机图片散在任意目录里，路径不蕴含时间，所以时间去读
    EXIF / 文件时间；device 一律是「本机文件」，category 一律「本机」。

    exclude 里的目录整棵跳过 —— 界面把备份根传进来，免得备份过的又被当成
    「本机文件」收一遍。
    """
    items = []
    seen = set()
    skip = tuple(os.path.abspath(p).lower().rstrip("\\/") for p in exclude)
    for root in (local_roots() if roots is None else roots):
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            here = os.path.abspath(dirpath).lower()
            if any(here == s or here.startswith(s + os.sep) for s in skip):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in LOCAL_SKIP_DIRS
                           and d.lower() not in LOCAL_DROP_TREES
                           and not any(b in d.lower() for b in LOCAL_SKIP_PARTS)]
            for fn in filenames:
                low = fn.lower()
                if os.path.splitext(low)[1] not in MEDIA_EXT:
                    continue
                if any(b in low for b in LOCAL_SKIP_PARTS):
                    continue
                path = os.path.join(dirpath, fn)
                if path.lower() in seen:
                    continue
                seen.add(path.lower())
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                ts, year, month, day = shot_time(path)
                items.append({
                    "path": path, "name": fn, "size": st.st_size, "mtime": st.st_mtime,
                    "device": LOCAL_DEVICE, "category": LOCAL_CATEGORY,
                    "year": year, "month": month, "day": day,
                    "is_video": is_video(fn), "timestamp": ts,
                    "online_only": is_online_only(path, st),
                })
    items.sort(key=lambda it: (it["timestamp"], it["name"]), reverse=True)
    return items


# --------------------------------------------------------------------------
# 自检：python library.py
# --------------------------------------------------------------------------


def selfcheck():
    import shutil

    checks = []

    def ok(label, cond):
        checks.append((label, bool(cond)))

    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_libcheck")
    shutil.rmtree(base, ignore_errors=True)
    old_dir = os.path.join(base, "2024", "2024-03-15")                 # 老结构
    cam_dir = os.path.join(base, "REDMI-K80-Ultra", "Camera", "2024", "2024-03-15")
    oth_dir = os.path.join(base, "REDMI-K80-Ultra", "Other", "2024", "2024-03-16")
    for d in (old_dir, cam_dir, oth_dir, os.path.join(base, THUMB_DIR, "aa")):
        os.makedirs(d, exist_ok=True)
    try:
        from PIL import Image
        img = Image.new("RGB", (800, 600), (200, 30, 30))
        img.save(os.path.join(old_dir, "a.jpg"), "JPEG")
        img.save(os.path.join(old_dir, "b.png"), "PNG")
        with open(os.path.join(old_dir, "note.txt"), "w") as f:
            f.write("ignore me")
        with open(os.path.join(old_dir, "c.mp4"), "wb") as f:
            f.write(b"\x00" * 64)
        img.save(os.path.join(cam_dir, "IMG_20240315_101533.jpg"), "JPEG")
        img.save(os.path.join(oth_dir, "wx_camera_1713514261465.jpg"), "JPEG")

        items = scan(base)
        by_name = {it["name"]: it for it in items}
        ok("只收录媒体文件（忽略 txt）",
           sorted(by_name) == sorted(["a.jpg", "b.png", "c.mp4",
                                      "IMG_20240315_101533.jpg",
                                      "wx_camera_1713514261465.jpg"]))
        ok("识别视频", by_name["c.mp4"]["is_video"])
        ok("不把缩略图缓存算进库", all(THUMB_DIR not in it["path"] for it in items))

        leg = by_name["a.jpg"]
        ok("老结构：年月日照样解析",
           leg["year"] == "2024" and leg["month"] == "2024-03" and leg["day"] == "2024-03-15")
        ok("老结构：归到「未归类（迁移前）」", leg["device"] == LEGACY_DEVICE)

        cam = by_name["IMG_20240315_101533.jpg"]
        ok("新结构：设备和类别都解析出来",
           cam["device"] == "REDMI-K80-Ultra" and cam["category"] == "Camera"
           and cam["year"] == "2024" and cam["day"] == "2024-03-15")
        oth = by_name["wx_camera_1713514261465.jpg"]
        ok("新结构：Other 类也认",
           oth["device"] == "REDMI-K80-Ultra" and oth["category"] == "Other"
           and oth["day"] == "2024-03-16")

        ok("能列出库里的设备",
           sorted({it["device"] for it in items}) == ["REDMI-K80-Ultra", LEGACY_DEVICE])
        ok("能列出库里的类别",
           sorted({it["category"] for it in items}) == ["Camera", "Other", UNKNOWN_CATEGORY])

        tree = group_tree(items)
        ok("分组树结构正确", "2024" in tree and "2024-03" in tree["2024"])
        only_cam = group_tree([it for it in items if it["device"] == "REDMI-K80-Ultra"])
        ok("按设备筛完再分组（多设备视图就是这么做的）",
           list(only_cam) == ["2024"] and len(only_cam["2024"]["2024-03"]) == 2)

        tp = thumb(os.path.join(old_dir, "a.jpg"), os.path.join(base, THUMB_DIR), box=100)
        ok("JPEG 能生成缩略图", tp and os.path.isfile(tp))
        if tp:
            with Image.open(tp) as t:
                ok("缩略图被缩到 100px 以内", max(t.size) <= 100)
            tp2 = thumb(os.path.join(old_dir, "a.jpg"),
                        os.path.join(base, THUMB_DIR), box=100)
            ok("二次调用命中缓存（同一路径）", tp2 == tp)
            # 进程被杀/磁盘写满会留下 0 字节的半截文件；只认「存在」的话那张图永远修不好
            with open(tp, "wb"):
                pass
            tp3 = thumb(os.path.join(old_dir, "a.jpg"),
                        os.path.join(base, THUMB_DIR), box=100)
            ok("缓存文件是 0 字节时不认它，重新生成",
               tp3 == tp and os.path.getsize(tp3) > 0)
            leftovers = [f for _r, _d, fs in os.walk(os.path.join(base, THUMB_DIR))
                         for f in fs if f.endswith(".part")]
            ok("生成过程没有留下 .part 半截文件", not leftovers)

        ok("坏掉的视频取不到缩略图，返回 None 且不炸",
           thumb(os.path.join(old_dir, "c.mp4"), os.path.join(base, THUMB_DIR)) is None)

        # 动态照片：伪造一张「主图 + 尾部 MP4」，XMP 里声明 MP4 的字节数
        n = 100
        fake_mp4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * (n - 12)
        main = (b"\xff\xd8\xff\xe1"
                + b'Item:Mime="video/mp4" Item:Length="%d"' % n + b"\x00" * 60)
        mp = os.path.join(base, "2024", "2024-03-15", "motion.jpg")
        with open(mp, "wb") as f:
            f.write(main + fake_mp4)
        ok("动态照片：算出内嵌 MP4 区间",
           motion_range(mp) == (len(main), len(main) + n))
        okm, mfile = extract_motion(mp)
        ok("动态照片：切出来的临时文件带 PID（多开不会互相覆盖）",
           okm and str(os.getpid()) in mfile)
        if okm and os.path.isfile(mfile):
            os.remove(mfile)
        ok("普通 JPEG 不会被误判成动态照片",
           motion_range(os.path.join(base, "2024", "2024-03-15", "a.jpg")) is None)

        ok("human_size 可读", human_size(1536) == "1.5 KB")
        ok("recycle 拒绝不存在的路径不会崩", recycle(["Z:\\nope\\none.jpg"])[0] is True)

        # ---- 本机文件（伪设备） ----
        lt = os.path.join(base, "_localhome")
        shots = os.path.join(lt, "Pictures", "Screenshots")
        qq = os.path.join(lt, "Documents", "Tencent Files", "123", "nt_data", "Pic", "Thumb")
        emo = os.path.join(lt, "Documents", "WXWork", "Emoji")
        dl = os.path.join(lt, "Downloads")
        for d in (shots, qq, emo, dl):
            os.makedirs(d, exist_ok=True)
        ts0 = time.mktime(time.strptime("2023-06-15 12:00:00", "%Y-%m-%d %H:%M:%S"))
        p_shot = os.path.join(shots, "屏幕截图 2025-01-02 030405.png")
        Image.new("RGB", (40, 30), (10, 20, 30)).save(p_shot, "PNG")
        os.utime(p_shot, (ts0, ts0))
        Image.new("RGB", (40, 30), (1, 2, 3)).save(os.path.join(qq, "q.jpg"), "JPEG")
        Image.new("RGB", (40, 30), (4, 5, 6)).save(os.path.join(emo, "e.jpg"), "JPEG")
        p_exif = os.path.join(dl, "cam.jpg")
        exif = Image.Exif()
        exif[36867] = "2021:05:04 03:02:01"            # DateTimeOriginal
        Image.new("RGB", (40, 30), (9, 9, 9)).save(p_exif, "JPEG", exif=exif)
        os.utime(p_exif, (ts0, ts0))

        loc = scan_local(roots=[lt])
        by = {it["name"]: it for it in loc}
        ok("本机文件：普通图片照收", "屏幕截图 2025-01-02 030405.png" in by)
        ok("本机文件：整个 QQ 目录不要",
           all("Tencent Files" not in it["path"] for it in loc))
        ok("本机文件：表情包目录不要", all("Emoji" not in it["path"] for it in loc))
        ok("本机文件：伪设备名固定", all(it["device"] == LOCAL_DEVICE for it in loc))
        ok("本机文件：没有 EXIF 就用文件时间",
           by["屏幕截图 2025-01-02 030405.png"]["day"] == "2023-06-15"
           and by["屏幕截图 2025-01-02 030405.png"]["month"] == "2023-06"
           and by["屏幕截图 2025-01-02 030405.png"]["year"] == "2023")
        ok("本机文件：有 EXIF 就用拍摄时间而不是文件时间",
           by["cam.jpg"]["day"] == "2021-05-04")
        ok("本机文件：按时间倒序（新的在前）", [it["name"] for it in loc][0]
           == "屏幕截图 2025-01-02 030405.png")
        only_dl = scan_local(roots=[lt], exclude=[dl])
        ok("本机文件：exclude 里的目录整棵跳过",
           len(only_dl) == len(loc) - 1 and all(it["path"] != p_exif for it in only_dl))
        ok("本机文件：exclude 传个不存在的目录也不会炸",
           len(scan_local(roots=[lt], exclude=[os.path.join(lt, "nope")])) == len(loc))
        ok("本机文件：不存在的根目录直接返回空",
           scan_local(roots=[os.path.join(lt, "nope")]) == [])
        ok("本机文件：分组后能进时间柱",
           group_tree(loc)["2023"]["2023-06"][0]["name"].startswith("屏幕截图"))

        # ---- 云盘「仅在线」占位文件 ----
        class _FakeStat:
            st_file_attributes = 0x400020      # ARCHIVE | RECALL_ON_DATA_ACCESS

        class _PlainStat:
            st_file_attributes = 0x20          # 只有 ARCHIVE

        ok("认出云盘「仅在线」文件", is_online_only("x", _FakeStat()))
        ok("本地有数据的文件不会被当成仅在线", not is_online_only("x", _PlainStat()))
        ok("没有 st_file_attributes 的对象不会误判", not is_online_only("x", object()))
        ok("scan 的条目带 online_only 字段", all("online_only" in it for it in items))
        ok("scan_local 的条目带 online_only 字段",
           all("online_only" in it for it in loc))
        me = sys.modules[__name__]
        real_online = me.is_online_only
        me.is_online_only = lambda p, st=None: True
        try:
            ok("仅在线文件不生成缩略图（不读文件、不刷日志）",
               thumb(os.path.join(old_dir, "a.jpg"), os.path.join(base, THUMB_DIR)) is None)
        finally:
            me.is_online_only = real_online

        # ---- Pillow 解不开的图（HEIC/HEIF 那类）退回 Windows Shell ----
        from PIL import Image as _PILImage
        import shellthumb as _shellthumb
        real_open, real_frame = _PILImage.open, _shellthumb.frame
        shell_calls = []

        def _boom(*_a, **_k):
            raise OSError("Pillow 不认识这个格式")

        try:
            _PILImage.open = staticmethod(_boom)
            _shellthumb.frame = lambda p, size=256: (
                shell_calls.append(size), _PILImage.new("RGB", (60, 40), (7, 8, 9)))[1]
            tp = thumb(os.path.join(old_dir, "b.png"), os.path.join(base, THUMB_DIR),
                       box=100)
            ok(f"Pillow 解不开时退回 Windows Shell 取图（实际调用 {shell_calls}）",
               bool(tp) and shell_calls == [100])
            _shellthumb.frame = lambda p, size=256: None
            ok("Shell 也取不到时返回 None，不炸",
               thumb(os.path.join(old_dir, "b.png"), os.path.join(base, THUMB_DIR),
                     box=101) is None)
        finally:
            _PILImage.open = real_open
            _shellthumb.frame = real_frame
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("=" * 56)
    for label, passed in checks:
        print(("  PASS  " if passed else "  FAIL  ") + label)
    print("=" * 56)
    bad = [l for l, p in checks if not p]
    print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(selfcheck())
