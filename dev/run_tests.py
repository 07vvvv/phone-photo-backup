# -*- coding: utf-8 -*-
"""一键跑完项目的全部自检与回归脚本。

这个项目的测试本来就是一堆「python 某个文件」形式的独立脚本，入口只做三件事：
编排、计时、汇总 —— 不引入 pytest 之类的额外依赖。

三档强度：
    python dev/run_tests.py --quick   只跑核心逻辑自检（不开窗口，约 1 分钟）
    python dev/run_tests.py           核心自检 + GUI 回归（会开窗口，不动鼠标）
    python dev/run_tests.py --all     全部，含鼠标交互 / 已安装 exe / 分享包 / 工具

单项跑挂不会带走整轮：每项都是独立子进程，失败时打印它自己的输出尾部。
退出码：0 全过；1 有失败；2 参数错误。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (显示名, 参数, 说明, 超时秒)
CORE = [
    ("backup 自检", ["backup.py", "--selfcheck"], "清单解析 / 分类 / 判重 / 掉线护栏 / 只读约束", 180),
    ("wireless 自检", ["wireless.py"], "mDNS 解析 / 地址整理 / pair·connect 判据", 120),
    ("library 自检", ["library.py"], "归档树 / 缩略图缓存 / 动图 / 本机扫描 / 云盘占位", 180),
    ("theme 自检", ["theme.py"], "颜色插值", 60),
    ("mfplay 自检", ["mfplay.py"], "MFPlay 接口与 vtable 下标", 120),
    ("shellthumb 自检", ["shellthumb.py"], "Shell 缩略图接口层", 120),
]

GUI = [
    ("无线 / 掉线回归", ["dev/_wireless_test.py"], "看门狗 / 熔断 / 弹窗配色 / 顶栏布局", 300),
    ("大图查看器回归", ["dev/_viewer_test.py"], "翻页 / 按天跳 / 动图长按 / 视频内嵌播放", 300),
    ("界面回归", ["dev/_ui_test.py"], "自绘菜单 / 格子复用 / 删除同步 / 消息泵限流", 300),
    ("本机文件回归", ["dev/_local_test.py"], "过滤规则 / EXIF 优先 / 与手机库合并", 300),
    ("内嵌视频回归", ["dev/_inline_video_test.py"], "出画面 / 画面在动 / 停止后复原", 300),
    ("新手指引回归", ["dev/_tour_test.py"], "顶栏入口 / 五步高亮定位 / 缩放跟随 / 降级 / 关闭落标记", 300),
]

EXTRA = [
    ("菜单鼠标交互", ["dev/_popup_test.py"], "真实鼠标点击菜单内外（会移动光标）", 300),
    ("已安装 exe 验收", ["dev/_installed_test.py"], "启动 D:\\PhonePhotoBackup 里的 exe（需已部署）", 300),
    ("分享包验收", ["dev/_share_accept.py"], "解压分享包并首次启动（需 dist 里有 zip）", 300),
    ("本机截图", ["dev/_shot_local.py"], "生成 dev/_shot_local*.png", 300),
    ("动图统计", ["dev/_motion_scan.py"], "扫一遍备份库统计动态照片（只读）", 900),
    ("重做图标", ["dev/make_icon.py"], "重新生成 assets/app.ico 与预览图", 120),
    ("打包分享包", ["dev/make_share_zip.py"], "重新生成 dist/手机照片备份-分享包.zip", 300),
]

COUNT_RE = re.compile(r"共\s*(\d+)\s*项[，,]\s*失败\s*(\d+)\s*项")
PASS_RE = re.compile(r"(\d+)\s*passed", re.I)


def parse_counts(text):
    """从脚本输出里抠出「共 N 项，失败 M 项」。抠不到返回 None。"""
    m = COUNT_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def run_one(name, args, timeout):
    cmd = [sys.executable] + args
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code, out = 124, f"超时（>{timeout}s）"
    except OSError as exc:
        code, out = 127, f"无法启动：{exc}"
    return {"name": name, "code": code, "cost": time.time() - started,
            "counts": parse_counts(out), "out": out}


def status_of(res):
    if res["code"] == 0:
        return "通过"
    return "失败"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="一键跑完手机照片备份的全部自检与回归脚本")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--quick", action="store_true", help="只跑核心逻辑自检，不开窗口")
    g.add_argument("--all", action="store_true", help="全部，含鼠标 / 已安装 exe / 工具")
    g.add_argument("--list", action="store_true", help="只列出清单，不执行")
    args = ap.parse_args(argv)

    if args.quick:
        suites, label = list(CORE), "核心自检"
    elif args.all:
        suites, label = list(CORE) + list(GUI) + list(EXTRA), "全部"
    else:
        suites, label = list(CORE) + list(GUI), "核心自检 + GUI 回归"

    if args.list:
        print(f"共 {len(suites)} 项（{label}）：")
        for i, (name, argv2, desc, _t) in enumerate(suites, 1):
            print(f"  {i:>2}. {name:<16} {desc}")
        return 0

    if not args.quick:
        print("提示：GUI 回归会短暂弹出程序窗口，属正常现象。\n")

    print("=" * 74)
    print(f"手机照片备份 · {label}（{len(suites)} 项）")
    print("=" * 74)

    results = []
    t0 = time.time()
    for i, (name, argv2, desc, timeout) in enumerate(suites, 1):
        print(f"[{i:>2}/{len(suites)}] {name:<16} ... ", end="", flush=True)
        res = run_one(name, argv2, timeout)
        results.append(res)
        counts = res["counts"]
        extra = f"{counts[0]} 项" if counts else ""
        print(f"{status_of(res):<2} {res['cost']:>6.1f}s  {extra}")
        if res["code"] != 0:
            tail = [ln for ln in res["out"].splitlines() if ln.strip()][-25:]
            for ln in tail:
                print("        " + ln)
            if not tail:
                print("        （没有输出，退出码 %s）" % res["code"])

    bad = [r for r in results if r["code"] != 0]
    total_checks = sum(r["counts"][0] for r in results if r["counts"])
    total_failed = sum(r["counts"][1] for r in results if r["counts"])
    print("=" * 74)
    print(f"通过 {len(results) - len(bad)} / {len(results)} 项"
          f" · 断言 {total_checks} 项（失败 {total_failed}）"
          f" · 合计 {time.time() - t0:.1f}s")
    if bad:
        print("失败项：" + "；".join(r["name"] for r in bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
