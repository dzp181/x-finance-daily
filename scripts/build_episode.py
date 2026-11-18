#!/usr/bin/env python3
"""把一个 episode.json 渲染成竖屏口播视频。

流程：逐句 TTS → 逐帧出图 → 每句生成一段带音轨的视频 → concat 成片。
不依赖网络，不依赖任何在线配音服务。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

FONT_HEAVY = "/System/Library/Fonts/STHeiti Medium.ttc"
FONT_LIGHT = "/System/Library/Fonts/Hiragino Sans GB.ttc"

WHITE = (255, 255, 255)
INK = (17, 17, 17)
MUTED = (150, 150, 150)
ACCENT = (192, 57, 43)  # 红：中文财经语境里的"强调色"


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd[:4])}...\n{proc.stderr[-800:]}")


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", size)


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """按字符宽度折行，保留原文里的显式换行。"""
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            trial = cur + ch
            if font.getbbox(trial)[2] <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def _fit_headline(caption: str, box_w: int, start_fs: int, min_fs: int, max_block_h: int):
    """从大往小试字号，优先找到「每个显式换行都不再被折断」的尺寸。"""
    paras = [p for p in caption.split("\n") if p]
    fs = start_fs
    while fs >= min_fs:
        f = load_font(FONT_HEAVY, fs)
        line_h = int(fs * 1.40)
        no_break = all(f.getbbox(p)[2] <= box_w for p in paras)
        if no_break and line_h * len(paras) <= max_block_h:
            return f, paras, line_h
        fs -= 3
    # 实在放不下，退回折行
    f = load_font(FONT_HEAVY, min_fs)
    return f, wrap(caption, f, box_w), int(min_fs * 1.40)


def _draw_headline(
    d: ImageDraw.ImageDraw,
    caption: str,
    box_w: int,
    start_fs: int,
    min_fs: int,
    max_block_h: int,
    center_y: int,
    left_pad: int,
) -> dict:
    f, lines, line_h = _fit_headline(caption, box_w, start_fs, min_fs, max_block_h)
    block_h = line_h * len(lines)
    top = center_y - block_h // 2

    bar_left = left_pad
    d.rectangle(
        [bar_left, top + int(line_h * 0.16), bar_left + max(6, int(f.size * 0.10)), top + block_h - int(line_h * 0.16)],
        fill=ACCENT,
    )
    for i, line in enumerate(lines):
        d.text((bar_left + int(f.size * 0.52), top + i * line_h), line, font=f, fill=INK)
    return {"top": top, "bottom": top + block_h, "line_h": line_h, "font": f}


def render_frame(
    path: Path,
    caption: str,
    brand: str,
    size: tuple[int, int],
    is_last: bool,
    disclaimer: str,
) -> None:
    w, h = size
    img = Image.new("RGB", (w, h), WHITE)
    d = ImageDraw.Draw(img)

    margin = int(w * 0.085)
    f_brand = load_font(FONT_LIGHT, int(w * 0.030))
    d.text((margin, int(h * 0.062)), brand, font=f_brand, fill=MUTED)

    # 竖屏中文字块略高于几何中心：底部要给视频号的标题/按钮留位置
    _draw_headline(
        d, caption, w - margin * 2 - int(w * 0.055),
        start_fs=int(w * 0.098), min_fs=int(w * 0.050),
        max_block_h=int(h * 0.38), center_y=int(h * 0.452),
        left_pad=margin,
    )

    if is_last:
        f_small = load_font(FONT_LIGHT, int(w * 0.023))
        d.text((margin, h - int(h * 0.115)), disclaimer, font=f_small, fill=MUTED)

    img.save(path, "PNG")


def render_cover(
    path: Path,
    caption: str,
    subtitle: str,
    brand: str,
    size: tuple[int, int] = (1080, 1440),
) -> None:
    """视频号封面单独排版，不走 9:16 帧缩放，避免字被压扁。"""
    w, h = size
    img = Image.new("RGB", (w, h), WHITE)
    d = ImageDraw.Draw(img)
    margin = int(w * 0.085)

    f_brand = load_font(FONT_LIGHT, int(w * 0.028))
    d.text((margin, int(h * 0.072)), brand, font=f_brand, fill=MUTED)

    box = _draw_headline(
        d, caption, w - margin * 2 - int(w * 0.070),
        start_fs=int(w * 0.120), min_fs=int(w * 0.058),
        max_block_h=int(h * 0.46), center_y=int(h * 0.415),
        left_pad=margin,
    )

    if subtitle:
        f_sub = load_font(FONT_LIGHT, int(w * 0.038))
        sub_lines = wrap(subtitle, f_sub, w - margin * 2 - int(w * 0.06))
        y = box["bottom"] + int(h * 0.038)
        for i, line in enumerate(sub_lines):
            d.text((margin + int(w * 0.016), y + i * int(w * 0.058)), line, font=f_sub, fill=(96, 96, 96))

    d.rectangle([margin, h - int(h * 0.080), w - margin, h - int(h * 0.080) + 2], fill=(232, 232, 232))
    img.save(path, "PNG")


def tts(text: str, voice: str, rate: int, aiff: Path, wav: Path) -> float:
    run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), text])
    run([FFMPEG, "-y", "-loglevel", "error", "-i", str(aiff), "-ar", "44100", "-ac", "1", str(wav)])
    with wave.open(str(wav), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def build(ep_dir: Path, skip_tts: bool = False) -> dict:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    out = cfg["output"]
    size = (out["width"], out["height"])
    fps = out["fps"]
    brand = f"{cfg['channel']['name']} · {ep.get('topic', '金融科普')}"

    tmp = ep_dir / "build"
    tmp.mkdir(exist_ok=True)
    voice = ep.get("voice", "Tingting")
    rate = ep.get("rate", 178)

    segments, total = [], 0.0
    timings: list[float] = []
    for i, scene in enumerate(ep["scenes"]):
        aiff, wav, png = tmp / f"s{i}.aiff", tmp / f"s{i}.wav", tmp / f"s{i}.png"
        seg = tmp / f"seg{i}.mp4"

        if skip_tts and wav.exists():
            with wave.open(str(wav), "rb") as wf:
                dur = wf.getnframes() / wf.getframerate()
        else:
            dur = tts(scene["text"], voice, rate, aiff, wav)

        render_frame(
            png,
            scene["caption"],
            brand,
            size,
            is_last=(i == len(ep["scenes"]) - 1),
            disclaimer=cfg["channel"]["disclaimer"],
        )

        run([
            FFMPEG, "-y", "-loglevel", "error",
            "-loop", "1", "-framerate", str(fps), "-i", str(png),
            "-i", str(wav),
            "-c:v", "libx264", "-tune", "stillimage", "-preset", "medium",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "1",
            "-pix_fmt", "yuv420p", "-shortest", "-t", f"{dur:.3f}",
            str(seg),
        ])
        segments.append(seg)
        timings.append(round(dur, 2))
        total += dur
        print(f"  [{i + 1:>2}/{len(ep['scenes'])}] {dur:5.2f}s  {scene['caption'].replace(chr(10), ' ')}")

    lst = tmp / "concat.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in segments), encoding="utf-8")
    video = ep_dir / "video.mp4"
    run([
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", "-movflags", "+faststart", str(video),
    ])

    # 封面 3:4，单独排版
    cover = ep_dir / "cover.png"
    render_cover(
        cover,
        caption=ep.get("cover_caption") or ep["scenes"][0]["caption"],
        subtitle=ep.get("subtitle", ""),
        brand=brand,
    )

    if total > out["max_seconds"]:
        print(f"! 时长 {total:.0f}s 超过上限 {out['max_seconds']}s，建议精简文案", file=sys.stderr)

    ep["timings"] = timings
    ep["seconds"] = round(total, 1)
    (ep_dir / "episode.json").write_text(
        json.dumps(ep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {"video": str(video), "cover": str(cover), "seconds": round(total, 1), "scenes": len(segments)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode", help="episode 目录名或路径")
    ap.add_argument("--skip-tts", action="store_true", help="复用已有音频，只重渲染画面")
    args = ap.parse_args()

    ep_dir = Path(args.episode)
    if not ep_dir.is_absolute():
        ep_dir = ROOT / "episodes" / args.episode
    if not (ep_dir / "episode.json").exists():
        print(f"找不到 {ep_dir}/episode.json", file=sys.stderr)
        return 2

    info = build(ep_dir, skip_tts=args.skip_tts)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    shutil.rmtree(ep_dir / "build", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
