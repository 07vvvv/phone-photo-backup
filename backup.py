# -*- coding: utf-8 -*-
"""backup.py — 手机照片/视频备份核心（只读，绝不修改手机）

安全约束（硬性，勿破坏）
    只使用这几类 adb 命令：`devices`、`shell content query`、`pull`。
    本文件中不存在 push、rm、mv、mkdir、uninstall 等任何会写手机的命令。

落盘与幂等
    手机时间优先取 MediaStore 的 datetaken（Android 从 EXIF DateTimeOriginal 提取），
    取不到则回退 date_modified。
    目标路径 = <备份根>/YYYY/YYYY-MM-DD/<原文件名>，同名冲突追加 _1.._99。
    「是否已备份」的判定规则：
        某候选路径已存在、且字节大小与手机文件一致 → 已备份，跳过；
        某候选路径不存在                          → 选它。
    所以重复备份天然幂等，不需要数据库，也不需要任何状态文件。

    ponytail: 判重只比「大小」，不比内容哈希。极罕见情况下（不同照片同名同大小）
    会被误判为已备份；真遇到再加 BLAKE3 校验。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time

# --------------------------------------------------------------------------
# adb 定位与调用
# --------------------------------------------------------------------------


def base_dir() -> str:
    """打包成单文件 exe 后，资源在 sys._MEIPASS；开发时就是脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def app_dir() -> str:
    """应用自身的目录：打包后是 exe 所在目录，开发时是脚本目录。

    config.json / error.log / .adb 都放这儿 —— 跟 base_dir()（解包临时目录）不是一回事。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


ADB_HOME = os.path.join(app_dir(), ".adb")

# 受限环境（沙箱、只读介质）里 adb 写不进目标目录时，先拉到这儿再搬
STAGING_DIR = os.path.join(app_dir(), ".pulltmp")

_ADB_FILES = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")


def default_root() -> str:
    """默认备份位置：当前用户的「图片」文件夹下。

    不写死盘符 —— 这个程序要能直接分享给别人，对方未必有 D 盘。
    """
    return os.path.join(os.path.expanduser("~"), "Pictures", "PhonePhotos")


def _sync_adb_bin(src_adb: str) -> None:
    """把 bin/ 里的 adb 三件套复制到 .adb\\，已经一样大就跳过。"""
    os.makedirs(ADB_HOME, exist_ok=True)
    src_dir = os.path.dirname(src_adb)
    for fn in _ADB_FILES:
        src = os.path.join(src_dir, fn)
        dst = os.path.join(ADB_HOME, fn)
        if not os.path.isfile(src):
            continue
        if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
            continue
        shutil.copyfile(src, dst)


def find_adb() -> str:
    """定位 adb.exe。

    打包后**不直接用解包目录里的那份**（base_dir() 指向 sys._MEIPASS）。
    adb 第一次运行会常驻一个 server 进程，而那个进程锁住的就是它自己的 exe ——
    解包目录在程序退出时由 PyInstaller 删除，删不掉就弹
    "Failed to remove temporary directory"。所以先复制一份到 exe 旁边的 .adb\，
    让 server 锁一个固定位置的文件，临时目录就能干净删掉。
    """
    bundled = os.path.join(base_dir(), "bin", "adb.exe")
    if os.path.isfile(bundled):
        if not getattr(sys, "frozen", False):
            return bundled                       # 开发时直接跑源码目录那份，不复制
        dst = os.path.join(ADB_HOME, "adb.exe")
        try:
            _sync_adb_bin(bundled)
        except OSError:
            pass                                 # 复制失败就退回解包目录（最坏是退出时弹框）
        return dst if os.path.isfile(dst) else bundled
    for p in (
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk", "platform-tools", "adb.exe"),
        r"C:\platform-tools\adb.exe",
    ):
        if os.path.isfile(p):
            return p
    return "adb"  # 交给 PATH


ADB = find_adb()


_ADB_TMP = os.path.join(app_dir(), ".adbtmp")
_adb_env_cache = None


def adb_env():
    """给 adb 一个确定可写的临时目录。

    adb 启动时会在 %TEMP% 下写 adb.log；有些机器上该目录权限异常，会导致
    "could not read ok from ADB Server / failed to start daemon" 而整个应用不可用。
    这里把 TEMP/TMP 指到应用自己的目录，绕开这个坑。
    """
    global _adb_env_cache
    if _adb_env_cache is None:
        env = os.environ.copy()
        try:
            os.makedirs(_ADB_TMP, exist_ok=True)
            probe = os.path.join(_ADB_TMP, ".probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("1")
            os.remove(probe)
            env["TEMP"] = _ADB_TMP
            env["TMP"] = _ADB_TMP
        except OSError:
            pass  # 目录不可写（例如装在 Program Files）就退回系统默认
        _adb_env_cache = env
    return _adb_env_cache


def run_adb(args, timeout=180):
    """执行 adb。参数以列表传递（不经 shell），避免路径/文件名里的特殊字符出问题。"""
    kwargs = {}
    if os.name == "nt":
        # 打包成 windowed（无控制台）程序后，调用命令行程序会闪黑框，用这个标记避免
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    proc = subprocess.run(
        [ADB] + list(args),
        capture_output=True,
        timeout=timeout,
        env=adb_env(),
        **kwargs,
    )
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    return proc.returncode, out, err


# --------------------------------------------------------------------------
# 设备
# --------------------------------------------------------------------------


class DeviceLost(RuntimeError):
    """手机在备份途中掉线：无线断了、数据线松了、adbd 挂了。

    单独留一个类型，是为了让界面能分清两件事 ——「手机掉了」要立刻停手并弹窗，
    「这个文件拉不下来」只记一笔、继续下一个。
    """


def list_devices(timeout=30):
    """返回 [(serial, state)]，state 通常是 device / unauthorized / offline。

    无线设备在这里长得就是 `192.0.2.100:37001`，和 USB 序列号一视同仁。
    """
    rc, out, err = run_adb(["devices"], timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"adb devices 失败：{err.strip()}")
    result = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            result.append((parts[0], parts[1]))
    return result


def device_state(serial, timeout=15):
    """serial 此刻在 adb 眼里是什么状态（device / offline / unauthorized …）；查不到返回 None。"""
    try:
        return dict(list_devices(timeout=timeout)).get(serial)
    except Exception:      # noqa: BLE001 —— 连 adb server 都问不上，就按「不可用」算
        return None


def device_alive(serial, timeout=15):
    """手机此刻还能不能用。无线掉线时它会变成 offline，或者干脆从列表里消失。"""
    return device_state(serial, timeout=timeout) == "device"


def device_lost(serial, timeout=8):
    """手机是不是真的掉了。

    要探两次：单次探测可能因为 adb server 一时忙不过来而失败，据此就把正在正常
    拉文件的 pull 杀掉、把整个备份中止掉，代价太大。复核一次只多花半秒，
    而真掉线时「5 秒轮询 + 半秒复核」也足够快了。
    """
    if device_alive(serial, timeout=timeout):
        return False
    time.sleep(0.5)
    return not device_alive(serial, timeout=timeout)


def pick_device(serial=None):
    """选一台可用设备。多台且未指定时报错，而不是随便挑一台。"""
    devices = list_devices()
    ready = [s for s, st in devices if st == "device"]
    if serial:
        if serial in ready:
            return serial
        state = dict(devices).get(serial, "未连接")
        raise RuntimeError(f"设备 {serial} 不可用（当前状态：{state}）")
    if not ready:
        detail = "、".join(f"{s}({st})" for s, st in devices) or "无任何设备"
        raise RuntimeError(
            f"没有可用的手机（当前：{detail}）。\n"
            "有线：插好数据线、USB 用途选「文件传输/MTP」，并允许 USB 调试授权；\n"
            "无线：点顶栏「无线」，先在手机上打开「开发者选项 → 无线调试」。"
        )
    if len(ready) > 1:
        raise RuntimeError(f"检测到多台设备：{', '.join(ready)}，请用 --serial 指定一台。")
    return ready[0]


# --------------------------------------------------------------------------
# 设备身份（只读：getprop / settings get）
# --------------------------------------------------------------------------

_DEVICE_PROPS = ("ro.product.marketname", "ro.product.brand", "ro.product.model")


def device_info(serial, timeout=60):
    """读设备身份：市场名 / 品牌 / 型号 / android_id。

    android_id 是关键 —— 同一台手机 USB 插着叫 `ABCD1234EFGH5678`，无线连着叫
    `192.0.2.5:37001`，serial 完全对不上，但 android_id 永远一样。
    靠它才能判断「这回连的还是上次那台」，进而决定照片落进哪个设备目录。
    """
    info = {}
    for prop in _DEVICE_PROPS:
        key = prop.rsplit(".", 1)[-1]
        try:
            _rc, out, _err = run_adb(["-s", serial, "shell", "getprop", prop],
                                     timeout=timeout)
            info[key] = (out or "").strip()
        except Exception:      # noqa: BLE001
            info[key] = ""
    try:
        _rc, out, _err = run_adb(
            ["-s", serial, "shell", "settings", "get", "secure", "android_id"],
            timeout=timeout)
        info["android_id"] = (out or "").strip()
    except Exception:          # noqa: BLE001
        info["android_id"] = ""
    return info


def _useful(value):
    return bool(value) and value.strip().lower() not in ("unknown", "null", "none", "0")


def device_label(info):
    """给人看的名字：优先市场名（`Pixel 8 Pro`），退回「品牌 型号」。"""
    if _useful(info.get("marketname")):
        return info["marketname"].strip()
    label = f"{(info.get('brand') or '').strip()} {(info.get('model') or '').strip()}".strip()
    return label or "未知设备"


def device_dir_name(info, fallback="device"):
    """目录名：把市场名改成英文安全形式（`Pixel 8 Pro` → `Pixel-8-Pro`）。

    保留中文（个别品牌可能返回中文名），但要是转完连一个 ASCII 字母数字都没有
    （比如拿到的是「未知设备」），就用英文兜底名，免得目录名整个是中文或空的。
    """
    raw = device_label(info)
    name = re.sub(r"[^0-9A-Za-z._\u4e00-\u9fff-]+", "-", raw).strip("-.")
    return name if re.search(r"[0-9A-Za-z]", name) else fallback


def device_key(info):
    """跨连接方式稳定的设备标识；拿不到 android_id 就退回名字。"""
    return (info.get("android_id") or "").strip() or device_label(info)


# --------------------------------------------------------------------------
# 归档分类
# --------------------------------------------------------------------------


def classify(relative_path):
    """按手机上的 relative_path 归到 Camera / Screenshots / ScreenRecorder / Other。"""
    rel = (relative_path or "").replace("\\", "/").lower()
    if not rel.endswith("/"):
        rel += "/"
    for category, prefixes in _CATEGORY_RULES:
        if any(rel.startswith(p) for p in prefixes):
            return category
    return CATEGORY_OTHER


def classify_by_name(name):
    """手机不在手边时的兜底：按文件名猜分类。"""
    for category, pattern in _NAME_RULES:
        if pattern.search(name or ""):
            return category
    return CATEGORY_OTHER


# --------------------------------------------------------------------------
# 读取手机媒体清单（只读：content query）
# --------------------------------------------------------------------------

MEDIA_URIS = {
    "image": "content://media/external/images/media",
    "video": "content://media/external/video/media",
}
_PROJECTION = "_display_name:_size:datetaken:date_modified:relative_path:_data"
_ROW_RE = re.compile(r"^Row:\s*\d+\s*(.*)$")

# 备份范围：空元组 = 手机媒体库里的照片和视频全收。
# 全收也不会乱：落盘时会在设备目录下按 Camera / Screenshots / ScreenRecorder / Other
# 分开存放（见 classify），相机照片不会和微信缓存混在一起。
BACKUP_PREFIXES = ()

# 设备目录下的一级分类
CATEGORY_CAMERA = "Camera"
CATEGORY_SHOTS = "Screenshots"
CATEGORY_REC = "ScreenRecorder"
CATEGORY_OTHER = "Other"

# 按手机上的 relative_path 分类。全部小写比较 —— 实测有些机器会出现
# DCIM/camera/ 甚至 dcim/camera/ 这种大小写不一致的目录。
_CATEGORY_RULES = (
    (CATEGORY_SHOTS, ("dcim/screenshots/", "pictures/screenshots/",
                      "pictures/screen_shots/")),
    (CATEGORY_REC, ("dcim/screenrecorder/", "pictures/screenrecords/",
                    "pictures/screenrecorder/", "movies/screenrecords/",
                    "movies/screenrecorder/")),
    (CATEGORY_CAMERA, ("dcim/camera/",)),
)

# 手机不在手边时（例如只做迁移）的兜底：按文件名猜
# 注意录屏是 screenrec_ 而不是 screenrecord，所以只匹配到 rec
_NAME_RULES = (
    (CATEGORY_SHOTS, re.compile(r"screenshot", re.I)),
    (CATEGORY_REC, re.compile(r"screen[-_]?rec", re.I)),
    (CATEGORY_CAMERA, re.compile(r"^(IMG|VID|MVIMG|PANO|BURST|DSCF)[_\-]", re.I)),
)

# 扫描备份库时要跳过的目录（缩略图缓存）
SKIP_DIRS = (".thumbcache",)


def parse_rows(text):
    """解析 content query 输出，形如：  Row: 0 _display_name=a.jpg, _size=123

    ponytail: 以 ", " 切分键值对，文件名里若含 ", " 会解析错（现实中几乎不会出现）。
    """
    rows = []
    for line in text.splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        row = {}
        for chunk in m.group(1).split(", "):
            if "=" not in chunk:
                continue
            k, v = chunk.split("=", 1)
            row[k.strip()] = v.strip()
        if row:
            rows.append(row)
    return rows


def query_media(serial, kinds=("image", "video"), prefixes=BACKUP_PREFIXES):
    """取回手机媒体条目（无需递归遍历文件系统）。

    prefixes 非空时只保留 relative_path 落在这些前缀下的条目；
    返回 (items, 被过滤掉的条目数)。
    """
    items, filtered = [], 0
    for kind in kinds:
        rc, out, err = run_adb(
            ["-s", serial, "shell", "content", "query",
             "--uri", MEDIA_URIS[kind], "--projection", _PROJECTION]
        )
        text = (out or "") + "\n" + (err or "")
        # 注意：adb shell 会把 Android 端的错误也以「返回码 0」交回来，错误只出现在输出里。
        # 不显式检查的话，provider 报错会被误当成「手机里没有照片」而静默漏掉全部文件。
        if rc != 0 or "Error" in text or "Exception" in text:
            if not device_alive(serial):
                raise DeviceLost(
                    "读手机媒体库的时候连接就断了。\n\n"
                    "手机上不会有任何改动。重新连上手机后再点「开始备份」就好。")
            raise RuntimeError(
                f"读取手机媒体库失败（{kind}）。\n"
                f"手机返回：{text.strip()[:300]}"
            )
        for row in parse_rows(out):
            rel = (row.get("relative_path") or "").replace("\\", "/")
            if prefixes and not any(rel.startswith(p) for p in prefixes):
                filtered += 1
                continue
            row["_kind"] = kind
            items.append(row)
    return items, filtered


def remote_path_of(row):
    """手机上的绝对路径：优先 _data，回退 relative_path 拼接。"""
    data = row.get("_data") or ""
    if data.startswith("/"):
        return data
    rel = (row.get("relative_path") or "").strip("/")
    return "/sdcard/" + (rel + "/" if rel else "") + row["_display_name"]


def pick_time(row):
    """拍摄时间。datetaken 单位毫秒（EXIF 来源），date_modified 单位秒。"""
    taken = row.get("datetaken") or ""
    if taken.lstrip("-").isdigit() and int(taken) > 0:
        return _dt.datetime.fromtimestamp(int(taken) / 1000.0)
    modified = row.get("date_modified") or ""
    if modified.lstrip("-").isdigit() and int(modified) > 0:
        return _dt.datetime.fromtimestamp(int(modified))
    return None


# --------------------------------------------------------------------------
# 目标路径（按年月日归档 + 确定性冲突处理）
# --------------------------------------------------------------------------


def candidate_paths(root, when, name, limit=100, device=None, category=None):
    """给出候选落盘路径，从原名开始，冲突时依次 _1、_2……

    新结构：<根>/<设备>/<类别>/<年>/<年-月-日>/<文件名>
    device / category 都给 None 时退回老结构 <根>/<年>/<年-月-日>/<文件名>。
    """
    parts = [root]
    if device:
        parts.append(device)
    if category:
        parts.append(category)
    parts.append(when.strftime("%Y"))
    parts.append(when.strftime("%Y-%m-%d"))
    folder = os.path.join(*parts)
    stem, ext = os.path.splitext(name)
    out = []
    for i in range(limit):
        out.append(os.path.join(folder, name if i == 0 else f"{stem}_{i}{ext}"))
    return out


def choose_slot(root, when, name, size, device=None, category=None):
    """选定落盘路径。

    返回 (dest, already)：
        already=True  → 该文件已备份过，跳过；
        already=False → dest 是要写入的路径。
    找不到槽位时返回 (None, False)。
    """
    for cand in candidate_paths(root, when, name, device=device, category=category):
        if os.path.isfile(cand):
            try:
                if os.path.getsize(cand) == size:
                    return cand, True
            except OSError:
                pass
            continue  # 被别的照片占了（同名不同大小），试下一个
        return cand, False
    return None, False


def existing_index(root):
    """扫一遍备份库，返回 {(文件名, 字节数)}。

    这是「跨设备、跨目录」的判重：同一个文件不管落进哪台设备、哪个日期目录，
    只要库里已经有同名同大小的，就不再拉第二份。比只查目标路径宽一层，
    顺手治好了「换算时间的方式一变、同一张照片就被重复拉一份」的老毛病。

    ponytail: 全库扫一遍存成集合。10 万文件约占 30 MB 内存、几秒扫完；
    真到百万级再换成落盘索引。
    """
    seen = set()
    if not os.path.isdir(root):
        return seen
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            try:
                seen.add((fn, os.path.getsize(os.path.join(dirpath, fn))))
            except OSError:
                pass
    return seen


# --------------------------------------------------------------------------
# 备份主流程
# --------------------------------------------------------------------------


def build_plan(root, serial=None, device_dir=None, index=None):
    """扫描手机 → 返回 (todo, skipped, failed, filtered)。

    todo:     [(row, dest)]    需要拉取（dest 已带设备/类别两层）
    skipped:  已备份过的（库里同名同大小，或目标槽位已存在同样大小）
    failed:   [(name, reason)] 无法定位槽位
    filtered: int              被前缀过滤掉的条目数（现在全收，恒为 0）

    index 传入 existing_index(root) 可避免重复扫库（多台设备连着时只扫一次）。
    """
    serial = serial or pick_device()
    rows, filtered = query_media(serial)
    seen = existing_index(root) if index is None else index
    todo, skipped, failed = [], [], []
    for row in rows:
        name = row.get("_display_name")
        if not name:
            continue
        try:
            size = int(row.get("_size") or 0)
        except ValueError:
            size = 0
        # 库里已经有同名同大小的 → 不管它在哪台设备、哪个目录，都不再拉第二份
        if (name, size) in seen:
            skipped.append(name)
            continue
        when = pick_time(row) or _dt.datetime.fromtimestamp(0)
        category = classify(row.get("relative_path"))
        dest, already = choose_slot(root, when, name, size, device_dir, category)
        if dest is None:
            failed.append((name, "同名文件超过 100 个，未能分配文件名"))
        elif already:
            skipped.append(dest)
        else:
            todo.append((row, dest))
            seen.add((name, size))      # 同一次里手机上有两份同名同大小的也只拉一份
    return todo, skipped, failed, filtered


POLL_SECONDS = 5          # 拉取期间每隔多久探一次手机还在不在
PULL_TIMEOUT = 1800       # 单个文件的硬上限（30 分钟，够拉几十 GB 的单个视频）
MAX_STREAK = 5            # 连续这么多个文件拉不下来，就不是「单个文件有问题」了

# 「adb 写不进备份目录」是环境属性（沙箱 / 只读介质 / 某些网络盘），试出一次就记住
_STAGING_ONLY = False


def _drop(tmp):
    """删掉半截的 .part，免得下次被当成已备份。"""
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass


def _staging_path(dest):
    """中转文件路径（放在应用目录旁边的 .pulltmp 里）。

    adb 是独立进程，它拿到的写权限不一定和应用一样 —— 沙箱、只读介质、某些网络盘
    都会出现「应用自己能写、adb 写不了」。这时就先拉到暂存区，再由 Python 搬过去：
    Python 是应用自己的进程，写目标目录的权限和生成缩略图时用的是同一套。
    """
    os.makedirs(STAGING_DIR, exist_ok=True)
    key = hashlib.sha1(os.path.abspath(dest).encode("utf-8")).hexdigest()[:12]
    return os.path.join(STAGING_DIR, key + os.path.splitext(dest)[1] + ".part")


def _clean_staging():
    """清掉暂存区里的残留（上一次跑到一半崩了会留下）。"""
    try:
        if os.path.isdir(STAGING_DIR):
            for fn in os.listdir(STAGING_DIR):
                _drop(os.path.join(STAGING_DIR, fn))
    except OSError:
        pass


def _is_create_error(err):
    """adb 说「建不了文件」—— 典型就是目标目录它写不进去。"""
    text = (err or b"").decode("utf-8", "replace").lower()
    return "cannot create" in text or "no such file or directory" in text


def _adb_pull(serial, remote, target, watchdog=True):
    """跑一次 `adb pull` 到指定路径。→ (返回码, stdout, stderr)。

    期间每 POLL_SECONDS 秒探一次手机还在不在；掉线就杀掉子进程并抛 DeviceLost，
    而不是干等 30 分钟超时。
    """
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000      # CREATE_NO_WINDOW：windowed 程序下别闪黑框
    proc = subprocess.Popen(
        [ADB, "-s", serial, "pull", "-a", remote, target],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=adb_env(), **kwargs)

    deadline = time.monotonic() + PULL_TIMEOUT
    out = err = b""
    while True:
        try:
            # 反复带超时地 communicate 是安全的：已读到的输出不会丢
            out, err = proc.communicate(timeout=POLL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            if watchdog and device_lost(serial):
                proc.kill()
                proc.communicate()
                _drop(target)
                raise DeviceLost(
                    "手机连接断开了，备份已中止。\n\n"
                    "已经拷到电脑上的文件都保留着，手机上也不会少任何东西。\n"
                    "重新连上手机后再点「开始备份」，会自动跳过已备份的，从断点接着来。")
            if time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                _drop(target)
                return 1, out, f"单个文件超过 {PULL_TIMEOUT // 60} 分钟仍未完成，已放弃".encode()
    return proc.returncode, out, err


def pull_one(serial, remote, dest, watchdog=True):
    """拉取单个文件：先写 .part，成功后原子改名，避免半截文件被当成完整文件。

    先试着直接写在目标旁边（正常机器上都是这条路径，快且原子）；
    万一 adb 写不进去，自动退到应用目录旁的暂存区拉，再由 Python 搬进去。
    「写不进去」是环境属性不是文件属性，所以试出一次就记住，后面不再白试。
    """
    global _STAGING_ONLY
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"

    if _STAGING_ONLY:
        stage = _staging_path(dest)
        _drop(stage)
        rc, out, err = _adb_pull(serial, remote, stage, watchdog)
    else:
        if os.path.exists(tmp):
            os.remove(tmp)
        rc, out, err = _adb_pull(serial, remote, tmp, watchdog)
        stage = tmp
        if rc != 0 and not os.path.isfile(tmp) and _is_create_error(err):
            _STAGING_ONLY = True
            stage = _staging_path(dest)
            _drop(stage)
            rc, out, err = _adb_pull(serial, remote, stage, watchdog)

    text_out = (out or b"").decode("utf-8", "replace")
    text_err = (err or b"").decode("utf-8", "replace")
    if rc != 0 or not os.path.isfile(stage):
        _drop(stage)
        if stage != tmp:
            _drop(tmp)
        text = (text_err + "\n" + text_out).strip()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return False, (lines[-1] if lines else "pull 失败")

    if stage != tmp:
        # 暂存区 → 目标旁边：同盘是原子改名，跨盘才会真搬一次
        try:
            os.replace(stage, tmp)
        except OSError:
            try:
                shutil.move(stage, tmp)
            except OSError as exc:
                _drop(stage)
                return False, f"从暂存区搬进备份目录失败：{exc}"
    os.replace(tmp, dest)
    speed = ""
    m = re.search(r"([\d.]+\s*[KMG]?B/s)", text_out)
    if m:
        speed = m.group(1)
    return True, speed


def do_backup(root, serial=None, on_event=None, dry_run=False, device_dir=None):
    """执行备份。on_event(kind, payload) 用于向 UI/控制台报进度。

    device_dir 是这台设备的目录名（如 `Pixel-8-Pro`）；给 None 时退回老结构。
    """
    def emit(kind, payload=None):
        if on_event:
            on_event(kind, payload)

    serial = serial or pick_device()
    emit("device", serial)
    emit("scanning")
    _clean_staging()        # 清掉上次跑崩留下的中转文件

    todo, skipped, failed, filtered = build_plan(root, serial, device_dir)
    emit("planned", {"todo": len(todo), "skipped": len(skipped),
                     "failed": len(failed), "filtered": filtered})

    done, errors, streak = 0, [], 0
    for idx, (row, dest) in enumerate(todo, 1):
        name = row["_display_name"]
        if dry_run:
            emit("would_pull", {"idx": idx, "total": len(todo), "name": name, "dest": dest})
            continue
        try:
            ok, info = pull_one(serial, remote_path_of(row), dest)
        except DeviceLost:
            # 掉线：立刻中止，不再碰后面的文件（pull_one 已把 .part 清干净）
            emit("lost", {"idx": idx, "total": len(todo), "done": done, "name": name})
            raise
        if ok:
            streak = 0
            done += 1
            emit("pulled", {"idx": idx, "total": len(todo), "name": name, "speed": info})
        else:
            errors.append((name, info))
            emit("error", {"name": name, "reason": info})
            streak += 1
            if streak >= MAX_STREAK:
                emit("lost", {"idx": idx, "total": len(todo), "done": done, "name": name})
                raise DeviceLost(
                    f"连续 {streak} 个文件都拉不下来，备份已中止。\n\n"
                    f"最近一次的原因：{info}\n\n"
                    f"已经拷到电脑上的 {done} 个文件都保留着，手机上也不会少任何东西。\n"
                    "确认手机连接正常后再点「开始备份」，会自动跳过已备份的，从断点接着来。")

    summary = {
        "pulled": done,
        "skipped": len(skipped),
        "failed": len(failed) + len(errors),
        "errors": errors,
    }
    emit("done", summary)
    return summary


# --------------------------------------------------------------------------
# 老结构 → 新结构 的迁移
# --------------------------------------------------------------------------


def plan_migration(root, device_dir):
    """列出「老结构 → 新结构」的搬迁计划：[(src, dst, category)]。

        老：<根>/2022/2022-11-26/a.jpg
        新：<根>/<设备>/<类别>/2022/2022-11-26/a.jpg

    只认顶层是 4 位数字年份的老结构，所以已经迁过的库不会被再迁一次。
    类别按文件名猜（手机不在手边也能算），认不出来归 Other。
    """
    moves = []
    if not os.path.isdir(root):
        return moves
    for year in sorted(os.listdir(root)):
        year_dir = os.path.join(root, year)
        if not (re.fullmatch(r"\d{4}", year) and os.path.isdir(year_dir)):
            continue
        for day in sorted(os.listdir(year_dir)):
            day_dir = os.path.join(year_dir, day)
            if not os.path.isdir(day_dir):
                continue
            for fn in sorted(os.listdir(day_dir)):
                src = os.path.join(day_dir, fn)
                if not os.path.isfile(src):
                    continue
                try:
                    os.stat(src)
                except OSError:
                    continue
                category = classify_by_name(fn)
                dst = os.path.join(root, device_dir, category, year, day, fn)
                if os.path.abspath(src) != os.path.abspath(dst):
                    moves.append((src, dst, category))
    return moves


def apply_migration(moves, on_event=None):
    """执行搬迁：逐个 os.rename（同盘瞬间完成）。

    安全约定（硬性）：
      * 目标已存在 → 跳过，**绝不覆盖**
      * 只动 plan_migration 列出的文件，不删任何数据文件
      * 搬完只清掉**空**的日期/年份目录

    返回 (done, errors, removed_dirs)。
    """
    def emit(kind, payload=None):
        if on_event:
            on_event(kind, payload)

    done, errors = 0, []
    touched = set()
    for i, (src, dst, _cat) in enumerate(moves, 1):
        if os.path.exists(dst):
            errors.append((src, "目标已存在，跳过（不覆盖）"))
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)
            done += 1
            touched.add(os.path.dirname(src))
            emit("moved", {"idx": i, "total": len(moves), "src": src, "dst": dst})
        except OSError as exc:
            errors.append((src, f"{type(exc).__name__}: {exc}"))

    removed = []
    for path in sorted(touched, key=len, reverse=True):     # 先深后浅
        for candidate in (path, os.path.dirname(path)):
            try:
                if os.path.isdir(candidate) and not os.listdir(candidate):
                    os.rmdir(candidate)
                    removed.append(candidate)
            except OSError:
                pass
    return done, errors, removed


# --------------------------------------------------------------------------
# 自检（不需要手机）：python backup.py --selfcheck
# --------------------------------------------------------------------------


def selfcheck():
    checks = []

    def ok(label, cond):
        checks.append((label, bool(cond)))

    # 1) content query 解析
    sample = (
        "Row: 0 _display_name=IMG_20240101_120000.jpg, _size=2048000, "
        "datetaken=1704106800000, date_modified=1704106800, "
        "relative_path=DCIM/Camera/, _data=/storage/emulated/0/DCIM/Camera/IMG_20240101_120000.jpg\n"
    )
    rows = parse_rows(sample)
    ok("解析出行数=1", len(rows) == 1)
    ok("解析出文件名", rows and rows[0]["_display_name"] == "IMG_20240101_120000.jpg")
    ok("解析出大小", rows and rows[0]["_size"] == "2048000")

    # 2) 时间优先 EXIF(datetaken)，回退 date_modified
    when = pick_time(rows[0])
    ok("优先用 datetaken", when == _dt.datetime.fromtimestamp(1704106800000 / 1000.0))
    ok("回退用 date_modified",
       pick_time({"_display_name": "a.jpg", "datetaken": "0", "date_modified": "1704106800"})
       == _dt.datetime.fromtimestamp(1704106800))
    ok("时间全缺失返回 None", pick_time({"_display_name": "a.jpg"}) is None)

    # 3) _data 缺失时能拼出手机路径
    ok("回退拼接手机路径",
       remote_path_of({"_display_name": "a.jpg", "relative_path": "DCIM/Camera/"})
       == "/sdcard/DCIM/Camera/a.jpg")

    # 4) 落盘路径按年月日分层
    cands = candidate_paths(r"D:\backup", _dt.datetime(2024, 3, 15, 10, 0, 0), "a.jpg")
    ok("路径按 YYYY/YYYY-MM-DD 分层",
       cands[0] == os.path.join(r"D:\backup", "2024", "2024-03-15", "a.jpg"))
    ok("冲突候选追加 _1", cands[1].endswith("a_1.jpg"))

    # 5) 幂等：已存在且大小一致 → 跳过；同名不同大小 → 用 _1
    # 自建临时目录（不用 tempfile：沙箱对 %TEMP% 与 mkdtemp 目录有限制）
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selfcheck_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        when = _dt.datetime(2024, 3, 15)
        day_dir = os.path.join(tmp, "2024", "2024-03-15")
        os.makedirs(day_dir, exist_ok=True)
        first = os.path.join(day_dir, "a.jpg")
        with open(first, "wb") as f:
            f.write(b"x" * 10)

        dest, already = choose_slot(tmp, when, "a.jpg", 10)
        ok("已备份过 → 判定为已存在", already and dest == first)

        dest2, already2 = choose_slot(tmp, when, "a.jpg", 99)
        ok("同名不同大小 → 改用 _1", (not already2) and dest2.endswith("a_1.jpg"))

        # 模拟拉取第二张后再判定：仍应幂等
        with open(dest2, "wb") as f:
            f.write(b"y" * 99)
        dest3, already3 = choose_slot(tmp, when, "a.jpg", 99)
        ok("第二张重复备份仍幂等", already3 and dest3 == dest2)

        # 本地文件被删掉 → 应重新列入待拉取
        os.remove(first)
        dest4, already4 = choose_slot(tmp, when, "a.jpg", 10)
        ok("本地删除后重新拉取", (not already4) and dest4 == first)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 6) 只读约束：扫描每一处 adb 调用的实参，确认没有任何写手机的动作
    #    pull 走的是 Popen（要能在中途 kill 掉），所以两种调用形式都得扫到
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    calls = re.findall(r"(?:run_adb|Popen)\(\s*\[([^\]]*)\]", src, re.S)
    forbidden = ("push", "rm", "mv", "mkdir", "delete", "uninstall", "reboot", "install")
    hits = sorted({w for call in calls for w in forbidden if f'"{w}"' in call})
    ok(f"只读约束：{len(calls)} 处 adb 调用均无写入动作", not hits and len(calls) >= 3)
    joined = " ".join(calls)
    needed = ("devices", "content", "pull", "getprop", "settings")
    ok("只用到 devices / content query / pull / getprop / settings get 这些只读命令",
       all(f'"{w}"' in joined for w in needed))
    if hits:
        print("        违规命令：", hits)

    # 7) 掉线护栏
    ok("DeviceLost 是可捕获的异常", issubclass(DeviceLost, Exception))
    ok("探测够密（间隔远小于单文件超时）", POLL_SECONDS * 10 <= PULL_TIMEOUT)
    ok("连续失败阈值合理", 2 <= MAX_STREAK <= 20)
    ok("没有的设备判为不存活", device_alive("203.0.113.9:5555", timeout=20) is False)
    ok("掉线要复核两次才算数", device_lost("203.0.113.9:5555", timeout=20) is True)

    # 8) 归档分类与设备目录名
    ok("备份范围 = 全收", BACKUP_PREFIXES == ())
    ok("DCIM/Camera → Camera", classify("DCIM/Camera/") == CATEGORY_CAMERA)
    ok("大小写不一致也认（dcim/camera/）", classify("dcim/camera/") == CATEGORY_CAMERA)
    ok("DCIM/Screenshots → Screenshots",
       classify("DCIM/Screenshots/") == CATEGORY_SHOTS)
    ok("别家的截图目录也认（Pictures/Screenshots/）",
       classify("Pictures/Screenshots/") == CATEGORY_SHOTS)
    ok("录屏目录 → ScreenRecorder", classify("DCIM/ScreenRecorder/") == CATEGORY_REC)
    ok("微信 / QQ / 互传 → Other",
       classify("Pictures/WeiXin/") == CATEGORY_OTHER
       and classify("tencent/QQ_Images/") == CATEGORY_OTHER
       and classify("Download/MiShare/") == CATEGORY_OTHER)
    ok("反斜杠路径也认", classify("DCIM\\Camera") == CATEGORY_CAMERA)
    ok("空路径 → Other", classify("") == CATEGORY_OTHER)

    ok("文件名兜底：Screenshot_ → Screenshots",
       classify_by_name("Screenshot_2024-01-01-12-00-00-123_a.jpg") == CATEGORY_SHOTS)
    ok("文件名兜底：screenrec → ScreenRecorder",
       classify_by_name("screenrec_20240101.mp4") == CATEGORY_REC)
    ok("文件名兜底：IMG_ → Camera",
       classify_by_name("IMG_20240101_120000.jpg") == CATEGORY_CAMERA)
    ok("文件名兜底：认不出来 → Other",
       classify_by_name("-1e644450a15163a4.jpg") == CATEGORY_OTHER)

    ok("市场名转目录名",
       device_dir_name({"marketname": "Pixel 8 Pro"}) == "Pixel-8-Pro")
    ok("没有市场名退回「品牌 型号」",
       device_label({"brand": "samsung", "model": "SM-S9180"}) == "samsung SM-S9180")
    ok("什么都没有也不炸",
       device_label({}) == "未知设备" and device_dir_name({}) == "device")
    ok("android_id 优先当设备标识",
       device_key({"android_id": "abc123", "marketname": "别的"}) == "abc123")

    # 9) 新落盘结构
    cands = candidate_paths(r"D:\backup", _dt.datetime(2024, 3, 15), "a.jpg",
                            device="Pixel-8-Pro", category=CATEGORY_CAMERA)
    ok("按 设备/类别/年/日 分层",
       cands[0] == os.path.join(r"D:\backup", "Pixel-8-Pro", "Camera",
                                "2024", "2024-03-15", "a.jpg"))
    ok("老结构仍然可选（不传设备/类别）",
       candidate_paths(r"D:\backup", _dt.datetime(2024, 3, 15), "a.jpg")[0]
       == os.path.join(r"D:\backup", "2024", "2024-03-15", "a.jpg"))

    # 10) 全局去重 + 迁移
    mroot = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_migrate_tmp")
    shutil.rmtree(mroot, ignore_errors=True)
    try:
        old = os.path.join(mroot, "2024", "2024-03-15")
        os.makedirs(old, exist_ok=True)
        for fn, blob in (("IMG_20240315_101533.jpg", b"x" * 10),
                         ("Screenshot_2024-03-15-10-00-00.jpg", b"y" * 20),
                         ("-1e644450a15163a4.jpg", b"z" * 30)):
            with open(os.path.join(old, fn), "wb") as fh:
                fh.write(blob)

        idx = existing_index(mroot)
        ok("全局去重索引认得库里的文件",
           len(idx) == 3 and ("IMG_20240315_101533.jpg", 10) in idx)

        moves = plan_migration(mroot, "Pixel-8-Pro")
        cats = {}
        for _s, _d, c in moves:
            cats[c] = cats.get(c, 0) + 1
        ok("迁移计划算出 3 个文件", len(moves) == 3)
        ok("按文件名分别归到 Camera / Screenshots / Other",
           cats == {CATEGORY_CAMERA: 1, CATEGORY_SHOTS: 1, CATEGORY_OTHER: 1})
        ok("目标路径带设备目录", all("Pixel-8-Pro" in d for _s, d, _c in moves))

        done, errors, _removed = apply_migration(moves)
        ok("迁移搬走 3 个且无错误", done == 3 and not errors)
        ok("搬空的老日期目录被清掉",
           not os.path.exists(os.path.join(mroot, "2024", "2024-03-15")))
        ok("文件都落在新结构里", all(os.path.isfile(d) for _s, d, _c in moves))
        with open(os.path.join(mroot, "Pixel-8-Pro", "Camera", "2024", "2024-03-15",
                               "IMG_20240315_101533.jpg"), "rb") as fh:
            ok("搬完内容一字不差", fh.read() == b"x" * 10)

        # 已经在新结构里的文件不该被再迁一次
        again = plan_migration(mroot, "Pixel-8-Pro")
        ok("新结构不会被重复迁移",
           all(not s.startswith(os.path.join(mroot, "Pixel-8-Pro"))
               for s, _d, _c in again))

        # 目标已存在 → 绝不覆盖
        os.makedirs(old, exist_ok=True)
        with open(os.path.join(old, "IMG_20240315_101533.jpg"), "wb") as fh:
            fh.write(b"NEW")
        done2, errors2, _ = apply_migration(plan_migration(mroot, "Pixel-8-Pro"))
        with open(os.path.join(mroot, "Pixel-8-Pro", "Camera", "2024", "2024-03-15",
                               "IMG_20240315_101533.jpg"), "rb") as fh:
            kept = fh.read()
        ok("目标已存在 → 跳过且不覆盖原文件", done2 == 0 and bool(errors2) and kept == b"x" * 10)
    finally:
        shutil.rmtree(mroot, ignore_errors=True)

    # 11) 受限环境下「adb 写不进目标目录」的降级
    ok("暂存区在应用目录下（不会跑去用户的照片库里）",
       os.path.dirname(os.path.abspath(STAGING_DIR)) == os.path.abspath(app_dir()))
    ok("认出 adb 的「建不了文件」错误",
       _is_create_error(b"adb: error: cannot create 'D:\\x\\y.part': No such file or directory")
       and not _is_create_error(b"error: device offline"))
    s1 = _staging_path(os.path.join(mroot, "a", "c.jpg"))
    s2 = _staging_path(os.path.join(mroot, "b", "c.jpg"))
    ok("暂存文件名唯一、保留扩展名、都在暂存区",
       s1 != s2 and os.path.dirname(s1) == STAGING_DIR and s1.endswith(".jpg.part"))
    shutil.rmtree(STAGING_DIR, ignore_errors=True)

    print("=" * 56)
    for label, passed in checks:
        print(("  PASS  " if passed else "  FAIL  ") + label)
    bad = [l for l, p in checks if not p]
    print("=" * 56)
    print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
    return 0 if not bad else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv=None):
    # 让中文在 Windows 控制台正常显示（打包成窗口程序后无控制台，不影响）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="手机照片/视频备份（只读）")
    ap.add_argument("--root", default=default_root(), help="电脑上的备份根目录")
    ap.add_argument("--serial", help="指定设备序列号（多台设备时必填）")
    ap.add_argument("--dry-run", action="store_true", help="只列出会拉取哪些文件，不真的拉")
    ap.add_argument("--selfcheck", action="store_true", help="跑自检，不需要手机")
    args = ap.parse_args(argv)

    if args.selfcheck:
        return selfcheck()

    def on_event(kind, payload=None):
        if kind == "device":
            print(f"设备：{payload}")
        elif kind == "scanning":
            print("正在读取手机媒体库……")
        elif kind == "planned":
            extra = f"，跳过非相机目录 {payload['filtered']} 个" if payload.get("filtered") else ""
            print(f"待拉取 {payload['todo']} 个，已备份跳过 {payload['skipped']} 个，"
                  f"异常 {payload['failed']} 个{extra}")
        elif kind == "pulled":
            print(f"  [{payload['idx']}/{payload['total']}] {payload['name']}  {payload['speed']}")
        elif kind == "would_pull":
            if payload["idx"] <= 20:
                print(f"  [预演] {payload['name']}  ->  {payload['dest']}")
            elif payload["idx"] == 21:
                print(f"  ……（其余 {payload['total'] - 20} 条略）" if payload.get("total") else "  ……")
        elif kind == "error":
            print(f"  失败：{payload['name']}  {payload['reason']}")
        elif kind == "done":
            print(f"\n完成：新拉取 {payload['pulled']}，跳过 {payload['skipped']}，"
                  f"失败 {payload['failed']}")

    try:
        do_backup(args.root, args.serial, on_event, dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("错误：adb 命令超时。请确认手机已解锁并保持连接。", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
