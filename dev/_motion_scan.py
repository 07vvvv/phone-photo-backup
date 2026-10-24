# -*- coding: utf-8 -*-
"""用 library.motion_range 全量扫描备份库，统计真实的动态照片。

（早先那次粗检只读文件尾，会漏掉 MP4 起点靠前的样本，结论偏低。）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import library  # noqa: E402

ROOT = sys.argv[1] if len(sys.argv) > 1 else r"D:\PhonePhotos"

hits, scanned = [], 0
for dirpath, dirnames, filenames in os.walk(ROOT):
    if os.path.basename(dirpath).startswith("."):
        dirnames[:] = []
        continue
    for fn in filenames:
        if os.path.splitext(fn)[1].lower() not in (".jpg", ".jpeg"):
            continue
        scanned += 1
        p = os.path.join(dirpath, fn)
        rng = library.motion_range(p)
        if rng:
            hits.append((p, rng[1] - rng[0], os.path.getsize(p)))

print("=" * 72)
print(f"扫过 {scanned} 张 JPEG，识别出真动态照片 {len(hits)} 张")
print("=" * 72)
for p, vlen, size in sorted(hits, key=lambda x: -x[2]):
    print(f"  {os.path.basename(p):<34} 全 {size/1e6:5.2f} MB  视频段 {vlen/1e6:5.2f} MB"
          f"  ({vlen/size*100:.0f}%)")
print("\n目录分布：")
from collections import Counter  # noqa: E402
for d, n in Counter(os.path.dirname(p) for p, _, _ in hits).most_common():
    print(f"  {n:>3}  {d}")
