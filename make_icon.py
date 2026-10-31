# -*- coding: utf-8 -*-
"""
生成古风图标：朱砂圆印 + 白月牙 + 水波
纯标准库（zlib + struct）手写 PNG / ICO，无第三方依赖。
"""
import os
import math
import zlib
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "static")

SS = 3                      # 超采样倍率
BASE = 256
S = BASE * SS

PAPER = (247, 242, 230)
CIN1 = (188, 74, 60)
CIN2 = (168, 56, 46)
CIN3 = (130, 39, 31)


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def seal_color(nx, ny):
    """朱砂径向渐变：左上偏亮"""
    d = math.hypot(nx - 0.38, ny - 0.32) / 0.72
    if d < 0.62:
        return mix(CIN1, CIN2, d / 0.62)
    return mix(CIN2, CIN3, (d - 0.62) / 0.38)


def render(size):
    """渲染 size x size 的 RGBA 像素行"""
    n = size
    px = [[(0, 0, 0, 0)] * n for _ in range(n)]
    cx = cy = n / 2.0
    R = n * 0.468           # 外圆
    R2 = (n * 0.406)        # 内细圈
    # 月牙：大圆 - 偏移小圆
    moon_c = (n * 0.478, n * 0.432)
    moon_r = n * 0.243
    cut_c = (n * 0.582, n * 0.432)
    cut_r = n * 0.214

    for y in range(n):
        ny = y / n
        for x in range(n):
            nx = x / n
            dist = math.hypot(x - cx, y - cy)
            if dist > R:
                continue
            col = seal_color(nx, ny)
            a = 255
            # 内细圈
            ring = abs(dist - R2)
            if ring < n * 0.009:
                k = 1.0 - ring / (n * 0.009)
                col = mix(col, PAPER, 0.55 * min(1.0, k * 1.6))
            # 月牙
            d1 = math.hypot(x - moon_c[0], y - moon_c[1])
            d2 = math.hypot(x - cut_c[0], y - cut_c[1])
            if d1 < moon_r and d2 > cut_r:
                col = mix(col, PAPER, 0.96)
            # 水波一
            wy = n * 0.735
            wave = wy + math.sin((x / n) * math.pi * 1.75 - 0.5) * n * 0.042
            wd = abs(y - wave)
            if n * 0.235 < x < n * 0.775 and wd < n * 0.0125:
                col = mix(col, PAPER, 0.88)
            # 水波二
            wy2 = n * 0.802
            wave2 = wy2 + math.sin((x / n) * math.pi * 1.75 - 0.5) * n * 0.038
            wd2 = abs(y - wave2)
            if n * 0.315 < x < n * 0.700 and wd2 < n * 0.0088:
                col = mix(col, PAPER, 0.54)
            # 边缘柔化
            if dist > R - 1.2:
                a = int(255 * max(0.0, (R - dist) / 1.2))
            px[y][x] = (int(col[0]), int(col[1]), int(col[2]), a)
    return px


def downsample(px, factor):
    n = len(px) // factor
    out = []
    for y in range(n):
        row = []
        for x in range(n):
            r = g = b = a = 0
            for dy in range(factor):
                for dx in range(factor):
                    p = px[y * factor + dy][x * factor + dx]
                    al = p[3]
                    r += p[0] * al
                    g += p[1] * al
                    b += p[2] * al
                    a += al
            cnt = factor * factor
            if a:
                # 用未平均的 alpha 总量做预乘还原
                row.append((min(255, r // a), min(255, g // a), min(255, b // a), a // cnt))
            else:
                row.append((0, 0, 0, 0))
        out.append(row)
    return out


def png_bytes(rgba):
    h = len(rgba)
    w = len(rgba[0])
    raw = b"".join(b"\x00" + b"".join(struct.pack("BBBB", *p) for p in row) for row in rgba)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico_bytes(images):
    """images: [(size, png_bytes)]"""
    n = len(images)
    head = struct.pack("<HHH", 0, 1, n)
    entries = b""
    offset = 6 + 16 * n
    data = b""
    for size, png in images:
        entry = struct.pack("<BBBBHHII",
                            0 if size >= 256 else size,
                            0 if size >= 256 else size,
                            0, 0, 1, 32, len(png), offset)
        entries += entry
        offset += len(png)
        data += png
    return head + entries + data


def main():
    print(f"[图标] 超采样渲染 {S}x{S} ...")
    big = render(S)
    print("[图标] 逐级降采样 ...")
    sizes = [256, 128, 64, 48, 32, 16]
    imgs = []
    cache = {256: downsample(big, SS)}
    for sz in sizes:
        if sz == 256:
            rgba = cache[256]
        else:
            rgba = downsample(cache[256], 256 // sz)
        png = png_bytes(rgba)
        imgs.append((sz, png))
        if sz == 256:
            with open(os.path.join(OUT, "icon.png"), "wb") as f:
                f.write(png)
        print(f"        {sz}x{sz}  {len(png)} bytes")
    with open(os.path.join(OUT, "icon.ico"), "wb") as f:
        f.write(ico_bytes(imgs))
    print("[图标] 完成：static/icon.png, static/icon.ico")


if __name__ == "__main__":
    main()
