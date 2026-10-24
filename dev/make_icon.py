# -*- coding: utf-8 -*-
"""make_icon.py — 生成应用图标 assets/app.ico

纯 Pillow 手绘，没有外部素材：蓝色圆角方底 + 两张叠放的照片卡，
卡里用负空间挖出太阳和山。每个尺寸都按 8 倍超采样再缩下来，边缘才不会有锯齿。

ICO 容器是手写的：PIL 的 `save("ICO")` 会把所有帧重采样成同一张位图，
塞不下「每个尺寸一张独立 PNG」这种正规做法。结构就是
ICONDIR(6 字节) + ICONDIRENTRY(每帧 16 字节) + 各帧 PNG 数据。

跑法：python dev\\make_icon.py
"""
from __future__ import annotations

import io
import os
import struct

from PIL import Image, ImageChops, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
SIZES = (256, 128, 64, 48, 32, 24, 16)
SS = 8                      # 超采样倍数

ACCENT = (0x4D, 0x6B, 0xFE)      # 与 theme.ACCENT 同色
CARD = (255, 255, 255)
BACK_ALPHA = 152
RADIUS = 0.225                   # 圆角半径，占边长的比例
CARD_RADIUS = 0.055
GLOW_ALPHA = 18                  # 顶部高光最浓处

BACK_BOX = (0.310, 0.180, 0.820, 0.690)         # 后面的卡片
FRONT_BOX = (0.180, 0.310, 0.690, 0.820)        # 前面的卡片
SUN_BOX = (0.283, 0.413, 0.367, 0.497)
MOUNTAIN = ((0.270, 0.730), (0.384, 0.540), (0.464, 0.644),
            (0.540, 0.552), (0.600, 0.730))


def _card(n, box, fill):
    """一张独立的圆角矩形图层。

    必须一层一张图再 alpha_composite，不能在同一张图上连着画 ——
    ImageDraw 是**直接写像素**不是混合，半透明的背卡会把蓝底整个抹掉。
    """
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([v * n for v in box],
                                         radius=CARD_RADIUS * n, fill=fill)
    return im


def render(size):
    """画一张 size×size 的图标。"""
    n = size * SS
    base = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(base).rounded_rectangle([0, 0, n - 1, n - 1],
                                           radius=RADIUS * n, fill=ACCENT + (255,))
    img = Image.alpha_composite(base, _card(n, BACK_BOX, CARD + (BACK_ALPHA,)))

    # 前卡：白底，里面用底色画出太阳和山
    front = _card(n, FRONT_BOX, CARD + (255,))
    fd = ImageDraw.Draw(front)
    fd.ellipse([v * n for v in SUN_BOX], fill=ACCENT + (255,))
    fd.polygon([(x * n, y * n) for x, y in MOUNTAIN], fill=ACCENT + (255,))
    img = Image.alpha_composite(img, front)

    # 顶部一层极淡的高光，让底面不那么平；只在已有像素上叠，别糊到图标外面
    glow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    half = n // 2
    for i in range(half):
        a = int(GLOW_ALPHA * (1 - i / half))
        if a <= 0:
            break
        gd.line([(0, i), (n, i)], fill=(255, 255, 255, a))
    glow.putalpha(ImageChops.multiply(glow.getchannel("A"), img.getchannel("A")))

    return Image.alpha_composite(img, glow).resize((size, size), Image.LANCZOS)


def save_ico(imgs, path):
    """按 ICO 的二进制结构写文件，每帧一张 PNG。"""
    pngs = []
    for im in imgs:
        buf = io.BytesIO()
        im.save(buf, "PNG")
        pngs.append(buf.getvalue())

    offset = 6 + 16 * len(pngs)
    out = bytearray(struct.pack("<HHH", 0, 1, len(pngs)))     # reserved, type=icon, count
    for im, data in zip(imgs, pngs):
        # 256 在 ICO 里记作 0（一个字节放不下）
        w = 0 if im.width >= 256 else im.width
        h = 0 if im.height >= 256 else im.height
        out += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for data in pngs:
        out += data
    with open(path, "wb") as fh:
        fh.write(bytes(out))


def make_preview(imgs):
    """各尺寸并排放大，供人眼看效果。"""
    pad, target_h = 16, 96
    scaled = [im.resize((max(1, round(im.width * target_h / im.height)), target_h),
                        Image.NEAREST) for im in imgs]
    width = sum(im.width for im in scaled) + pad * (len(scaled) + 1)
    out = Image.new("RGB", (width, target_h + pad * 2), (245, 245, 247))
    x = pad
    for im in scaled:
        out.paste(im, (x, pad), im)
        x += im.width + pad
    return out


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    imgs = [render(s) for s in SIZES]
    ico = os.path.join(OUT, "app.ico")
    save_ico(imgs, ico)
    make_preview(imgs).save(os.path.join(OUT, "icon_preview.png"))
    print(f"OK  {ico}  {os.path.getsize(ico)} B")
    print("    " + ", ".join(f"{s}px" for s in SIZES))
