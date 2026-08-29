#!/usr/bin/env python3
"""生成 VideoPlayer 图标 icon.ico（需要 Pillow）"""

from PIL import Image, ImageDraw, ImageFilter
import math


def make_icon(out_path: str = "icon.ico"):
    SIZE = 256

    # ── 底层画布 ──────────────────────────────────────────────────────────────
    base = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)

    pad = 6
    inner = SIZE - pad * 2

    # 深色圆形背景（多圈叠加模拟渐变）
    for i, (r, g, b, a) in enumerate([
        (8,  10, 20, 255),
        (12, 14, 28, 240),
        (16, 18, 36, 220),
    ]):
        offset = i * 8
        draw.ellipse(
            [pad + offset, pad + offset,
             SIZE - pad - offset, SIZE - pad - offset],
            fill=(r, g, b, a),
        )

    # ── 蓝色发光环（先画模糊光晕，再画实线）────────────────────────────────────
    ring_pad = pad + 10
    ring_w   = 6

    # 光晕层
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(glow)
    for extra in range(10, 0, -2):
        alpha = int(60 * (extra / 10))
        gd.ellipse(
            [ring_pad - extra, ring_pad - extra,
             SIZE - ring_pad + extra, SIZE - ring_pad + extra],
            outline=(80, 160, 255, alpha),
            width=ring_w + extra,
        )
    glow = glow.filter(ImageFilter.GaussianBlur(6))
    base = Image.alpha_composite(base, glow)
    draw = ImageDraw.Draw(base)

    # 实线环
    draw.ellipse(
        [ring_pad, ring_pad, SIZE - ring_pad, SIZE - ring_pad],
        outline=(13, 110, 253, 255),
        width=ring_w,
    )
    # 高光弧（右上角亮点，模拟光泽）
    draw.arc(
        [ring_pad + 2, ring_pad + 2,
         SIZE - ring_pad - 2, SIZE - ring_pad - 2],
        start=300, end=60,
        fill=(180, 220, 255, 120),
        width=ring_w // 2,
    )

    # ── 播放三角 ──────────────────────────────────────────────────────────────
    cx  = SIZE // 2 + SIZE // 20       # 稍微右移使视觉居中
    cy  = SIZE // 2
    tri = SIZE * 0.30

    pts = [
        (cx - tri * 0.45, cy - tri * 0.54),
        (cx - tri * 0.45, cy + tri * 0.54),
        (cx + tri * 0.70, cy),
    ]

    # 三角阴影（模拟立体感）
    shadow_pts = [(x + 3, y + 3) for x, y in pts]
    draw.polygon(shadow_pts, fill=(0, 0, 0, 80))

    # 主三角（白色带轻微蓝色偏移）
    draw.polygon(pts, fill=(240, 245, 255, 255))

    # 三角内高光（左侧略亮）
    hi_pts = [
        (pts[0][0],              pts[0][1]),
        (pts[1][0],              pts[1][1] * 0.6 + pts[0][1] * 0.4),
        (pts[2][0] * 0.6 + pts[0][0] * 0.4,
         pts[2][1] * 0.6 + pts[0][1] * 0.4),
    ]
    draw.polygon(hi_pts, fill=(255, 255, 255, 60))

    # ── 导出多尺寸 ICO ────────────────────────────────────────────────────────
    sizes  = [256, 128, 64, 48, 32, 16]
    frames = []
    for s in sizes:
        frames.append(base.resize((s, s), Image.LANCZOS) if s != SIZE else base)

    frames[0].save(
        out_path, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print(f"图标已生成: {out_path}")


if __name__ == "__main__":
    make_icon()
