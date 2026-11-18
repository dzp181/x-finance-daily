#!/usr/bin/env python3
"""把生成的背景原图处理成可直接用的 1080x1920 底图。

做三件事：
1. 抹掉生成图自带的右下角水印（inpaint，背景是均匀暗色，修补后无痕）
2. 裁成 9:16 并放大到 1080x1920
3. 底部叠加垂直压暗，给烧字幕留对比度

用法：
    .venv-lipsync/bin/python scripts/prep_bg.py assets/bg/xxx.png assets/bg/ink.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

W, H = 1080, 1920


def remove_watermark(img: np.ndarray, pad: int = 14) -> np.ndarray:
    """自动找右下角明显亮于底色的像素，用 inpaint 填掉。"""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    y0, x0 = int(h * 0.90), int(w * 0.72)
    roi = gray[y0:, x0:]
    bg = float(np.median(roi))
    ys, xs = np.where(roi > bg + 22)
    if len(ys) == 0:
        print("  ! 没检测到水印，跳过")
        return img

    mask = np.zeros((h, w), np.uint8)
    mask[
        max(0, y0 + ys.min() - pad) : min(h, y0 + ys.max() + pad),
        max(0, x0 + xs.min() - pad) : min(w, x0 + xs.max() + pad),
    ] = 255
    out = cv2.inpaint(img, mask, 7, cv2.INPAINT_TELEA)
    print(f"  水印已修补：{len(ys)} px")
    return out


def crop_9x16(img: np.ndarray, keep: str = "right") -> np.ndarray:
    """裁成 9:16。keep=right 时保留右侧（光在右边），左侧暗部可丢。"""
    h, w = img.shape[:2]
    tw = round(h * 9 / 16)
    if tw > w:
        th = round(w * 16 / 9)
        top = 0 if keep == "right" else h - th
        img = img[top : top + th, :]
    else:
        left = w - tw if keep == "right" else 0
        img = img[:, left : left + tw]
    return cv2.resize(img, (W, H), interpolation=cv2.INTER_LANCZOS4)


def darken_bottom(img: np.ndarray, strength: float = 0.30, start: float = 0.62) -> np.ndarray:
    """底部垂直压暗，字幕压在上面更清楚。"""
    h = img.shape[0]
    ramp = np.clip((np.linspace(0, 1, h) - start) / (1 - start), 0, 1) ** 1.5
    mult = (1 - strength * ramp)[:, None, None]
    return np.clip(img.astype(np.float32) * mult, 0, 255).astype(np.uint8)


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("用法: prep_bg.py <输入图> <输出图> [--keep left|right] [--darken 0.3]")

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    keep = "right"
    darken = 0.30
    args = sys.argv[3:]
    for i, a in enumerate(args):
        if a == "--keep":
            keep = args[i + 1]
        elif a == "--darken":
            darken = float(args[i + 1])

    img = cv2.imread(str(src))
    if img is None:
        raise SystemExit(f"读不到图：{src}")
    print(f"输入 {img.shape[1]}x{img.shape[0]}")

    img = remove_watermark(img)
    img = crop_9x16(img, keep=keep)
    if darken > 0:
        img = darken_bottom(img, strength=darken)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dst), img)
    print(f"输出 {dst}  {W}x{H}")


if __name__ == "__main__":
    main()
