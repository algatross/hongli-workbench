# -*- coding: utf-8 -*-
"""
生成 PWA 图标（一次性脚本，改配色/造型时重跑即可）
输出：icons/icon-180.png / icon-192.png / icon-512.png / icon-maskable-512.png

依赖：Pillow（本机 default 环境已装 11.3.0）
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "icons")

BG_TOP = (10, 15, 26)
BG_BOT = (24, 36, 58)
CYAN = (34, 211, 238)
GOLD = (251, 191, 36)
WHITE = (232, 238, 248)
BAR_COLORS = [(34, 211, 238), (96, 165, 250), (251, 191, 36)]  # 青 / 蓝 / 金
SS = 4  # 超采样倍数，再缩小，边缘更干净


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def draw_icon(size, content=0.72, rounded=True, transparent_outside=True):
    """content = 图形占比（maskable 要小一些，留安全区）"""
    S = size * SS
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 背景渐变（逐行画）
    grad = Image.new("RGB", (1, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.point((0, y), fill=lerp(BG_TOP, BG_BOT, y / max(1, S - 1)))
    grad = grad.resize((S, S))

    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    if rounded:
        md.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.235), fill=255)
    else:
        md.rectangle([0, 0, S - 1, S - 1], fill=255)

    base.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(base)

    # ---- 图形：三根递增的柱 + 一条上行的折线 ----
    box = S * content
    x0 = (S - box) / 2
    base_y = (S + box) / 2 - box * 0.10          # 基线略高于正中
    unit = box / 4.0

    heights = [unit * 1.05, unit * 1.75, unit * 2.55]
    widths = unit * 0.82
    gap = (box - widths * 3) / 2.0               # 三根柱均分

    for i, h in enumerate(heights):
        left = x0 + i * (widths + gap)
        right = left + widths
        top = base_y - h
        # 青 → 浅蓝 → 金。中间不要过渡到绿色（A股习惯里绿色是跌，观感不对）
        c = BAR_COLORS[i]
        d.rounded_rectangle([left, top, right, base_y], radius=int(widths * 0.28), fill=c)

    # 基线
    d.rounded_rectangle(
        [x0 - unit * 0.12, base_y + unit * 0.16,
         x0 + box + unit * 0.12, base_y + unit * 0.16 + max(2, int(S * 0.012))],
        radius=int(S * 0.006), fill=lerp(WHITE, BG_BOT, 0.55))

    # 上行箭头（从第 1 根柱顶到第 3 根柱顶之上）
    pts = [
        (x0 + widths * 0.5, base_y - heights[0] - unit * 0.42),
        (x0 + widths + gap + widths * 0.5, base_y - heights[1] - unit * 0.34),
        (x0 + (widths + gap) * 2 + widths * 0.5, base_y - heights[2] - unit * 0.46),
    ]
    d.line(pts, fill=WHITE, width=max(2, int(S * 0.016)), joint="curve")
    # 末端小圆点
    r = max(3, int(S * 0.030))
    d.ellipse([pts[-1][0] - r, pts[-1][1] - r, pts[-1][0] + r, pts[-1][1] + r], fill=GOLD)

    out = base.resize((size, size), Image.LANCZOS)
    if not transparent_outside:
        flat = Image.new("RGB", (size, size), BG_TOP)
        flat.paste(out, (0, 0), out)
        return flat
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    jobs = [
        ("icon-192.png", 192, 0.72, True, True),
        ("icon-512.png", 512, 0.72, True, True),
        ("icon-180.png", 180, 0.74, False, False),          # iOS 自己会切圆角，给满幅不透明
        ("icon-maskable-512.png", 512, 0.56, False, False),  # Android 自适应图标，留安全区
    ]
    for name, size, content, rounded, alpha in jobs:
        im = draw_icon(size, content=content, rounded=rounded, transparent_outside=alpha)
        path = os.path.join(OUT, name)
        im.save(path, "PNG", optimize=True)
        print("已生成 %-26s %dx%d" % (name, size, size))


if __name__ == "__main__":
    main()
