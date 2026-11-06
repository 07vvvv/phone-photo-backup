# -*- coding: utf-8 -*-
"""wireless.py — 无线（Wi-Fi）连接：mDNS 发现 / 配对 / 连接 / 断开

只管「把手机接上 adb」，一个字节的媒体数据都不碰。接上之后备份走的还是
backup.py 里那套 `adb pull`，所以有线无线共用同一条已经验证过的落盘路径，
不新增任何写手机的动作。

为什么不把连接管理写进 backup.py：
    backup.py 的只读自检会扫描源码里每一处 adb 调用，确认不含
    push / rm / mv / mkdir / delete / uninstall —— 那是「绝不写手机」的硬证据。
    connect / pair 不属于媒体写入，但混进去会让这条自检的语义变模糊。

Android 11+（本应用面向 Android 14/15）的两种姿势：
    已配对过：手机开着「无线调试」，adb connect <ip>:<连接端口>
    首次配对：手机点「使用配对码配对设备」，adb pair <ip>:<配对端口> <6位码>

配对端口和连接端口**不是同一个**，而且每次开关「无线调试」都会重新随机 ——
所以优先用 `adb mdns services` 自动发现，用户只需要抄一遍手机上的 6 位配对码。
"""
from __future__ import annotations

import os
import re
import time

import backup

DEFAULT_PORT = 5555       # 只在用户手输纯 IP 时兜底；Android 11+ 的端口是随机的

# host:port。host 允许 IPv4 / 主机名 / [IPv6 字面量]
_RE_ADDR = re.compile(r"^(\[[0-9a-fA-F:]+\]|[0-9A-Za-z._\-]+):(\d{1,5})$")

# adb connect / pair 的失败措辞。宁可漏判成失败让用户重试，也不能把失败说成成功
_FAIL_WORDS = ("failed", "cannot", "refused", "unreachable", "unable",
               "wrong", "timed out", "unknown host", "authenticate", "error")


# --------------------------------------------------------------------------
# 地址与判定（纯函数，方便自检）
# --------------------------------------------------------------------------


def is_wireless(serial):
    """`192.0.2.100:37001` 这种是无线；`R58M12ABCDE` 这种 USB 序列号不是。"""
    return bool(_RE_ADDR.match((serial or "").strip()))


def normalize_addr(text, default_port=DEFAULT_PORT):
    """把用户输入整理成 host:port。本来就是 host:port 就原样返回。"""
    s = (text or "").strip()
    if not s:
        return ""
    if _RE_ADDR.match(s):
        return s
    if s.startswith("["):                  # IPv6 字面量
        return s if "]:" in s else f"{s}:{default_port}"
    if s.count(":") == 1:                  # host / host:
        host, _, port = s.partition(":")
        return f"{host}:{port}" if port else f"{host}:{default_port}"
    if ":" in s:                           # 裸 IPv6
        return f"[{s}]:{default_port}"
    return f"{s}:{default_port}"


def pair_ok(text):
    """`adb pair` 的成功判据。"""
    return "successfully paired" in (text or "").lower()


def connect_ok(text):
    """`adb connect` 的成功判据。

    成功：`connected to ip:port` / `already connected to ip:port`
    失败：`failed to connect to ...` / `cannot connect to ...` / `failed to authenticate`

    注意 "failed to connect to" 里并没有 "connected to"（是 connect 不是 connected），
    但仍然要显式排掉失败词 —— 免得 adb 哪天换个措辞就被误判成连上了。
    """
    t = (text or "").lower()
    return "connected to" in t and not any(w in t for w in _FAIL_WORDS)


def _join(out, err, rc):
    text = "\n".join(x.strip() for x in (out, err) if (x or "").strip())
    return text or f"adb 返回码 {rc}"


# --------------------------------------------------------------------------
# mDNS 发现
# --------------------------------------------------------------------------


def _parse_mdns_line(line):
    """`adb-XXXX\\t_adb-tls-connect._tcp\\t192.0.2.100:37001` → (服务类型, 地址)。

    表头、别的 app 的服务、没有端口的行一律返回 (None, None)。
    """
    parts = (line or "").split()
    if len(parts) < 3:
        return None, None
    svc, addr = parts[-2], parts[-1]
    if not svc.startswith("_") or not _RE_ADDR.match(addr):
        return None, None
    return svc, addr


def collect_services(text):
    """从 `adb mdns services` 的输出里挑出 adb 自己的两类服务。"""
    found = {"connect": [], "pairing": []}
    for line in (text or "").splitlines():
        svc, addr = _parse_mdns_line(line)
        if not svc:
            continue
        if "_adb-tls-connect" in svc and addr not in found["connect"]:
            found["connect"].append(addr)
        elif "_adb-tls-pairing" in svc and addr not in found["pairing"]:
            found["pairing"].append(addr)
    return found


def discover(timeout=20):
    """mDNS 发现正在广播无线调试的设备。

    路由器开了 AP 隔离或禁了组播时两个列表都是空的 —— 那不是错误，只是要用户手输。
    顺带一提：这个命令读的是 adb server 里已有的缓存，实测 100ms 量级，同步调用不卡。
    """
    try:
        rc, out, _ = backup.run_adb(["mdns", "services"], timeout=timeout)
    except Exception:      # noqa: BLE001 —— 老版 adb 根本没有 mdns 子命令
        return {"connect": [], "pairing": []}
    return collect_services(out) if rc == 0 else {"connect": [], "pairing": []}


# --------------------------------------------------------------------------
# 配对 / 连接 / 断开
# --------------------------------------------------------------------------


def pair(addr, code, timeout=60):
    """用手机上的 6 位配对码完成配对。→ (成功?, 给用户看的说明)"""
    code = (code or "").strip().replace(" ", "")
    if not code:
        return False, "请先填手机上显示的 6 位配对码"
    try:
        rc, out, err = backup.run_adb(["pair", addr, code], timeout=timeout)
    except Exception as exc:      # noqa: BLE001
        return False, f"配对失败：{exc}"
    text = _join(out, err, rc)
    return pair_ok(text), text


def connect(addr, timeout=30):
    """连接一台已经配对过的手机。→ (成功?, 说明)"""
    try:
        rc, out, err = backup.run_adb(["connect", addr], timeout=timeout)
    except Exception as exc:      # noqa: BLE001
        return False, f"连接失败：{exc}"
    text = _join(out, err, rc)
    return connect_ok(text), text


def disconnect(addr=None, timeout=30):
    """断开无线设备；不给 addr 就是全部断开。"""
    try:
        rc, out, err = backup.run_adb(["disconnect"] + ([addr] if addr else []),
                                      timeout=timeout)
    except Exception as exc:      # noqa: BLE001
        return False, f"断开失败：{exc}"
    return rc == 0, _join(out, err, rc)


def connect_and_wait(addr, timeout=25):
    """连接，并等到设备真的变成 device 才算成功。

    `adb connect` 报成功不等于马上能用 —— 刚连上那几秒 `adb devices` 里常是 offline。
    等它就绪再让用户去点「开始备份」，省得点下去才发现设备没准备好。
    """
    ok, msg = connect(addr)
    if not ok:
        return False, msg
    for _ in range(max(int(timeout), 1)):
        if backup.device_alive(addr):
            return True, msg
        time.sleep(1)
    return False, msg + "\n设备已连上但一直没就绪，请在手机上确认「无线调试」仍然开着。"


def pair_and_connect(pair_addr, code, timeout=25):
    """配对 → 重新发现连接端口 → 连接。首次连接的完整流程。"""
    ok, msg = pair(pair_addr, code)
    if not ok:
        return False, "配对失败：" + msg
    ip = pair_addr.rsplit(":", 1)[0]
    found = discover()
    # 只认同一台设备的连接端口，免得局域网上有第二台手机时连错
    cands = [a for a in found["connect"] if a.rsplit(":", 1)[0] == ip]
    if not cands:
        return False, ("配对成功了，但没找到连接端口。\n"
                       "请把手机上「无线调试」页面显示的「IP 地址和端口」填进上面的"
                       "地址栏，再点「连接」。")
    ok2, msg2 = connect_and_wait(cands[0], timeout=timeout)
    if not ok2:
        return False, "配对成功，但连接失败：" + msg2
    return True, f"已配对并连接：{cands[0]}"


def wireless_serials():
    """当前已连上的无线设备。"""
    try:
        return [s for s, st in backup.list_devices() if st == "device" and is_wireless(s)]
    except Exception:      # noqa: BLE001
        return []


def disconnect_all():
    """断开所有无线设备。→ (成功?, 说明)"""
    current = wireless_serials()
    if not current:
        return True, "当前没有无线设备连着"
    ok_all, msgs = True, []
    for s in current:
        ok, msg = disconnect(s)
        ok_all = ok_all and ok
        msgs.append(f"{s}：{'已断开' if ok else msg}")
    return ok_all, "；".join(msgs)


# --------------------------------------------------------------------------
# 自检：python wireless.py（不需要手机）
# --------------------------------------------------------------------------


def selfcheck():
    checks = []

    def ok(label, cond):
        checks.append((label, bool(cond)))

    # 1) mDNS 输出解析
    sample = (
        "List of discovered mdns services\n"
        "adb-3D4F5A6B7C8D\t_adb-tls-connect._tcp\t192.0.2.100:37001\n"
        "adb-3D4F5A6B7C8D\t_adb-tls-pairing._tcp\t192.0.2.100:41234\n"
        "some-other-app\t_ipp._tcp\t192.0.2.55:631\n"
    )
    found = collect_services(sample)
    ok("发现 connect 服务", found["connect"] == ["192.0.2.100:37001"])
    ok("发现 pairing 服务", found["pairing"] == ["192.0.2.100:41234"])
    ok("忽略无关服务与表头", len(found["connect"]) + len(found["pairing"]) == 2)
    ok("重复广播只留一条",
       collect_services(sample + sample)["connect"] == ["192.0.2.100:37001"])
    ok("没带端口的行被丢弃",
       _parse_mdns_line("a\t_b._tcp\thost-without-port") == (None, None))
    ok("空输出不炸", collect_services("") == {"connect": [], "pairing": []})

    # 2) 无线 / USB 判定
    ok("识别 ip:port", is_wireless("192.0.2.100:37001"))
    ok("识别 IPv6 字面量", is_wireless("[fe80::1]:37001"))
    ok("USB 序列号不算无线", not is_wireless("R58M12ABCDE"))
    ok("空串不算无线", not is_wireless(""))

    # 3) 地址整理
    ok("纯 IP 补默认端口", normalize_addr("192.0.2.100") == "192.0.2.100:5555")
    ok("已有端口原样保留", normalize_addr("192.0.2.100:37001") == "192.0.2.100:37001")
    ok("两端空格被去掉", normalize_addr("  192.0.2.100:37001  ") == "192.0.2.100:37001")
    ok("空输入返回空串", normalize_addr("") == "")

    # 4) pair / connect 的成功判据（用的是真机输出样例）
    ok("pair 成功", pair_ok("Successfully paired to 192.0.2.100:41234 [guid=adb-3D4F]"))
    ok("pair 密码错 → 失败", not pair_ok("Failed: Wrong password"))
    ok("connect 成功", connect_ok("connected to 192.0.2.100:37001"))
    ok("connect 已连过", connect_ok("already connected to 192.0.2.100:37001"))
    ok("connect 被拒绝 → 失败",
       not connect_ok("failed to connect to 192.0.2.100:37001: Connection refused"))
    ok("connect 没配对 → 失败",
       not connect_ok("failed to authenticate to 192.0.2.100:37001"))
    ok("connect 网络不可达 → 失败",
       not connect_ok("cannot connect to 192.0.2.100:37001: Network is unreachable"))

    # 5) 本机 adb 真的支持这几个子命令
    try:
        _, helptext, _ = backup.run_adb(["--help"], timeout=25)
    except Exception:      # noqa: BLE001
        helptext = ""
    for sub in ("pair", "connect", "disconnect", "mdns"):
        ok(f"本机 adb 支持 {sub}", sub in helptext)

    # 6) 本模块同样不含任何会写手机的命令
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    calls = re.findall(r"run_adb\(\s*\[([^\]]*)\]", src, re.S)
    forbidden = ("push", "rm", "mv", "mkdir", "delete", "uninstall", "reboot", "install")
    hits = sorted({w for call in calls for w in forbidden if f'"{w}"' in call})
    ok(f"只读约束：{len(calls)} 处 adb 调用均无写手机动作", not hits and len(calls) >= 4)
    if hits:
        print("        违规命令：", hits)

    print("=" * 56)
    for label, passed in checks:
        print(("  PASS  " if passed else "  FAIL  ") + label)
    print("=" * 56)
    bad = [l for l, p in checks if not p]
    print(f"共 {len(checks)} 项，失败 {len(bad)} 项")
    return 0 if not bad else 1


if __name__ == "__main__":
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:      # noqa: BLE001
            pass
    raise SystemExit(selfcheck())
