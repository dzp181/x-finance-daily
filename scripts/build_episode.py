#!/usr/bin/env python3
"""把一个 episode.json 渲染成竖屏口播视频。

流程：逐句 TTS（带逐字时间戳）→ 逐帧出图 → 每句一段视频 → concat → 烧字幕。
字幕时间轴来自 edge-tts 的 WordBoundary 事件，不是按字数估算。
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
from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

FONT_HEAVY = "/System/Library/Fonts/STHeiti Medium.ttc"
FONT_LIGHT = "/System/Library/Fonts/Hiragino Sans GB.ttc"

# 主题：文字色、强调色；背景优先用 bg_image（相对项目根），缺失时退回渐变
THEMES = {
    "ink": {
        "bg_image": "assets/bg/ink-frost.png",
        "bg_top": (15, 17, 23),
        "bg_bottom": (28, 32, 42),
        "ink": (243, 243, 241),
        "muted": (124, 131, 145),
        "accent": (176, 141, 87),
        "rule": (48, 53, 64),
        "sub_color": "&H00F1F3F3",  # ASS 是 &HAABBGGRR
        "sub_outline": 2,
        "grain": 6,
    },
    "paper": {
        "bg_image": "",
        "bg_top": (247, 245, 241),
        "bg_bottom": (235, 231, 224),
        "ink": (26, 26, 26),
        "muted": (139, 137, 131),
        "accent": (176, 60, 45),
        "rule": (222, 218, 210),
        "sub_color": "&H00262626",
        "sub_outline": 0,
        "grain": 2,
    },
}


def ensure_dir(path: Path) -> Path:
    """沙箱里 mkdir(exist_ok=True) 仍可能抛 EEXIST，这里兜一层。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass
    return path


def run(cmd: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd[:4])}...\n{proc.stderr[-1200:]}")


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype(FONT_LIGHT, size)


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
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


def gradient_bg(w: int, h: int, top: tuple, bottom: tuple, grain: int = 6) -> Image.Image:
    """竖向线性渐变，并叠一层细颗粒。

    纯渐变在 8-bit H.264 下会出明显色带（每 100px 跳一档），
    加 ±6 的高斯噪声可以把它抖散，肉眼看是质感，不是噪点。
    """
    strip = Image.new("RGB", (1, h))
    px = strip.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom))
    img = strip.resize((w, h), Image.BILINEAR)

    noise = Image.effect_noise((w, h), grain).convert("L")
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    # (img + noise) - 128：噪声以 128 为中心，等于零均值抖动
    return ImageChops.add(img, noise_rgb, scale=1.0, offset=-128)


def make_bg(w: int, h: int, theme: dict) -> Image.Image:
    """取背景：优先用主题指定的图片，缺失则退回渐变。

    背景图按「覆盖裁切、顶部对齐」缩放到目标尺寸——光源在右上，裁掉底部
    比裁掉顶部安全（封面 1080x1440 会切掉下方 480px，光源仍完整）。
    """
    rel = theme.get("bg_image") or ""
    if rel:
        fp = Path(rel) if Path(rel).is_absolute() else ROOT / rel
        if fp.exists():
            im = Image.open(fp).convert("RGB")
            if im.size != (w, h):
                scale = max(w / im.width, h / im.height)
                nw, nh = round(im.width * scale), round(im.height * scale)
                im = im.resize((nw, nh), Image.LANCZOS)
                left = (nw - w) // 2
                im = im.crop((left, 0, left + w, h))
            return im
        print(f"! 背景图不存在，退回渐变：{fp}", file=sys.stderr)
    return gradient_bg(w, h, theme["bg_top"], theme["bg_bottom"], theme.get("grain", 6))


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
    theme: dict,
) -> dict:
    f, lines, line_h = _fit_headline(caption, box_w, start_fs, min_fs, max_block_h)
    block_h = line_h * len(lines)
    top = center_y - block_h // 2

    bar_left = left_pad
    d.rectangle(
        [bar_left, top + int(line_h * 0.16), bar_left + max(6, int(f.size * 0.10)), top + block_h - int(line_h * 0.16)],
        fill=theme["accent"],
    )
    for i, line in enumerate(lines):
        d.text((bar_left + int(f.size * 0.52), top + i * line_h), line, font=f, fill=theme["ink"])
    return {"top": top, "bottom": top + block_h, "line_h": line_h, "font": f}


def render_frame(
    path: Path,
    caption: str,
    brand: str,
    size: tuple[int, int],
    is_last: bool,
    disclaimer: str,
    theme: dict,
) -> None:
    w, h = size
    img = make_bg(w, h, theme)
    d = ImageDraw.Draw(img)

    margin = int(w * 0.085)
    f_brand = load_font(FONT_LIGHT, int(w * 0.028))
    d.text((margin, int(h * 0.062)), brand, font=f_brand, fill=theme["muted"])

    # 中文字块略高于几何中心：底部要留给字幕和视频号自身 UI
    _draw_headline(
        d, caption, w - margin * 2 - int(w * 0.055),
        start_fs=int(w * 0.098), min_fs=int(w * 0.050),
        max_block_h=int(h * 0.36), center_y=int(h * 0.420),
        left_pad=margin, theme=theme,
    )

    if is_last:
        f_small = load_font(FONT_LIGHT, int(w * 0.022))
        d.text((margin, h - int(h * 0.130)), disclaimer, font=f_small, fill=theme["muted"])

    img.save(path, "PNG")


def render_cover(
    path: Path,
    caption: str,
    subtitle: str,
    brand: str,
    theme: dict,
    size: tuple[int, int] = (1080, 1440),
) -> None:
    """视频号封面单独排版，不走 9:16 帧缩放，避免字被压扁。"""
    w, h = size
    img = make_bg(w, h, theme)
    d = ImageDraw.Draw(img)
    margin = int(w * 0.085)

    f_brand = load_font(FONT_LIGHT, int(w * 0.028))
    d.text((margin, int(h * 0.072)), brand, font=f_brand, fill=theme["muted"])

    box = _draw_headline(
        d, caption, w - margin * 2 - int(w * 0.070),
        start_fs=int(w * 0.120), min_fs=int(w * 0.058),
        max_block_h=int(h * 0.46), center_y=int(h * 0.415),
        left_pad=margin, theme=theme,
    )

    if subtitle:
        f_sub = load_font(FONT_LIGHT, int(w * 0.038))
        sub_lines = wrap(subtitle, f_sub, w - margin * 2 - int(w * 0.06))
        y = box["bottom"] + int(h * 0.038)
        for i, line in enumerate(sub_lines):
            d.text((margin + int(w * 0.016), y + i * int(w * 0.058)), line, font=f_sub, fill=theme["muted"])

    d.rectangle([margin, h - int(h * 0.080), w - margin, h - int(h * 0.080) + 2], fill=theme["rule"])
    img.save(path, "PNG")


def synth_edge(text: str, voice: str, rate: str, mp3: Path) -> list[tuple[str, float, float]]:
    """合成语音，返回时间轴片段 [(text, start, end)]，单位秒。

    注意：中文音色返回的是 SentenceBoundary（整句），英文才返回 WordBoundary（逐词）。
    两种都收，后面统一在字符级重排。
    """
    import asyncio
    import time as _time

    import edge_tts

    events: list[tuple[str, float, float]] = []

    async def _go() -> None:
        comm = edge_tts.Communicate(text, voice, rate=rate)
        with open(mp3, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    start = chunk["offset"] / 1e7
                    dur = chunk["duration"] / 1e7
                    events.append((chunk["text"], start, start + dur))

    last: Exception | None = None
    for attempt in range(4):
        events.clear()
        try:
            asyncio.run(_go())
            return events
        except Exception as exc:  # noqa: BLE001
            last = exc
            _time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"edge-tts 连续失败: {last}")


def tts(
    text: str, ep: dict, aiff: Path, wav: Path, cache_dir: Path | None = None
) -> tuple[float, list[tuple[str, float, float]] | None]:
    """合成一句并转成 44.1k 单声道 wav。返回 (时长, 逐词时间轴或 None)。"""
    import hashlib

    conf = ep.get("tts") or {}
    engine = conf.get("engine", "say")
    voice = conf.get("voice", "zh-CN-XiaoxiaoNeural")
    rate = conf.get("rate", "+0%")

    key = hashlib.sha1(f"{engine}|{voice}|{rate}|{text}".encode()).hexdigest()[:16]
    cached_wav = cache_dir / f"{key}.wav" if cache_dir else None
    cached_words = cache_dir / f"{key}.words.json" if cache_dir else None

    has_words = bool(cached_words and cached_words.exists())
    if cached_wav and cached_wav.exists() and (engine != "edge" or has_words):
        wav.write_bytes(cached_wav.read_bytes())
        words = [tuple(w) for w in json.loads(cached_words.read_text())] if has_words else None
        with wave.open(str(wav), "rb") as wf:
            return wf.getnframes() / wf.getframerate(), words

    words = None
    if engine == "edge":
        mp3 = aiff.with_suffix(".mp3")
        words = synth_edge(text, voice, rate, mp3)
        run([FFMPEG, "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "44100", "-ac", "1", str(wav)])
    else:
        run(["say", "-v", ep.get("voice", "Tingting"), "-r", str(ep.get("rate", 178)), "-o", str(aiff), text])
        run([FFMPEG, "-y", "-loglevel", "error", "-i", str(aiff), "-ar", "44100", "-ac", "1", str(wav)])

    if cache_dir:
        ensure_dir(cache_dir)
        cached_wav.write_bytes(wav.read_bytes())
        if words:
            cached_words.write_text(json.dumps(words, ensure_ascii=False))

    with wave.open(str(wav), "rb") as wf:
        return wf.getnframes() / wf.getframerate(), words


BREAK_CHARS = "，。！？；：、,"
STRIP_END = "，。、；：,.;: "


def chunk_words(
    events: list[tuple[str, float, float]] | None, text: str, duration: float, max_chars: int = 12
) -> list[tuple[float, float, str]]:
    """把 TTS 时间轴切成字幕行。

    先把每个片段的时长均摊到它的每个字，得到字符级时间轴；
    再按标点和最大字数合并成行。这样 SentenceBoundary / WordBoundary 都能处理。
    """
    if not events:
        # 完全没时间轴：按标点切句，按字数均分时长
        parts, cur = [], ""
        for ch in text:
            cur += ch
            if ch in "。！？，；":
                parts.append(cur)
                cur = ""
        if cur:
            parts.append(cur)
        total_chars = sum(len(p) for p in parts) or 1
        out, t = [], 0.0
        for p in parts:
            d = duration * len(p) / total_chars
            label = p.strip(STRIP_END)
            if label:
                out.append((t, t + d, label))
            t += d
        return out

    chars: list[tuple[str, float, float]] = []
    for seg_text, seg_start, seg_end in events:
        if not seg_text:
            continue
        step = (seg_end - seg_start) / len(seg_text)
        for i, ch in enumerate(seg_text):
            chars.append((ch, seg_start + i * step, seg_start + (i + 1) * step))

    cues: list[tuple[float, float, str]] = []
    buf: list[tuple[str, float, float]] = []
    for ch, s, e in chars:
        buf.append((ch, s, e))
        label = "".join(c[0] for c in buf).strip()
        if (ch in BREAK_CHARS and len(label) >= 6) or len(label) >= max_chars:
            text_out = "".join(c[0] for c in buf).strip(STRIP_END)
            if text_out:
                cues.append((buf[0][1], buf[-1][2], text_out))
            buf = []
    if buf:
        text_out = "".join(c[0] for c in buf).strip(STRIP_END)
        if text_out:
            cues.append((buf[0][1], buf[-1][2], text_out))
    return cues


def ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(cues: list[tuple[float, float, str]], theme: dict, path: Path) -> None:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,PingFang SC,58,{theme['sub_color']},{theme['sub_color']},&H00000000,&H00000000,-1,0,0,0,100,100,1,0,1,{theme['sub_outline']},0,2,100,100,400,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for start, end, text in cues:
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Sub,,0,0,0,,{text}\n")
    path.write_text("".join(lines), encoding="utf-8")


def build(
    ep_dir: Path,
    skip_tts: bool = False,
    theme_name: str = "ink",
    subtitles: bool = True,
    bg: str | None = None,
) -> dict:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    out = cfg["output"]
    size = (out["width"], out["height"])
    fps = out["fps"]
    theme = dict(THEMES[theme_name])
    if bg is not None:
        theme["bg_image"] = bg
    brand = f"{cfg['channel']['name']} · {ep.get('topic', '金融科普')}"

    tmp = ensure_dir(ep_dir / "build")
    cache_dir = ep_dir / ".cache"
    segments, total = [], 0.0
    timings: list[float] = []
    cues: list[tuple[float, float, str]] = []

    for i, scene in enumerate(ep["scenes"]):
        aiff, wav, png = tmp / f"s{i}.aiff", tmp / f"s{i}.wav", tmp / f"s{i}.png"
        seg = tmp / f"seg{i}.mp4"

        # tts() 自带按 (文本,音色,语速) 的缓存，重跑不会重复联网
        dur, words = tts(scene["text"], ep, aiff, wav, cache_dir=cache_dir)

        for start, end, text in chunk_words(words, scene["text"], dur):
            cues.append((total + start, total + end, text))

        render_frame(
            png,
            scene["caption"],
            brand,
            size,
            is_last=(i == len(ep["scenes"]) - 1),
            disclaimer=cfg["channel"]["disclaimer"],
            theme=theme,
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
    raw = tmp / "raw.mp4"
    run([
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", str(raw),
    ])

    suffix = "" if theme_name == "ink" else f"-{theme_name}"
    video = ep_dir / f"video{suffix}.mp4"
    if subtitles:
        build_ass(cues, theme, tmp / "subs.ass")
        run([
            FFMPEG, "-y", "-loglevel", "error", "-i", str(raw),
            "-vf", "ass=subs.ass",
            "-c:v", "libx264", "-crf", "20", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-movflags", "+faststart", str(video),
        ], cwd=tmp)
    else:
        run([
            FFMPEG, "-y", "-loglevel", "error", "-i", str(raw),
            "-c", "copy", "-movflags", "+faststart", str(video),
        ])

    cover = ep_dir / f"cover{suffix}.png"
    render_cover(
        cover,
        caption=ep.get("cover_caption") or ep["scenes"][0]["caption"],
        subtitle=ep.get("subtitle", ""),
        brand=brand,
        theme=theme,
    )

    if total > out["max_seconds"]:
        print(f"! 时长 {total:.0f}s 超过上限 {out['max_seconds']}s，建议精简文案", file=sys.stderr)

    if theme_name == "ink":
        ep["timings"] = timings
        ep["seconds"] = round(total, 1)
        (ep_dir / "episode.json").write_text(
            json.dumps(ep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    return {
        "video": str(video),
        "cover": str(cover),
        "seconds": round(total, 1),
        "scenes": len(segments),
        "cues": len(cues),
        "theme": theme_name,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode", help="episode 目录名或路径")
    ap.add_argument("--theme", default="ink", choices=sorted(THEMES), help="主题配色")
    ap.add_argument("--bg", default=None, help="覆盖背景图路径（相对项目根或绝对路径）")
    ap.add_argument("--no-subs", action="store_true", help="不烧字幕")
    ap.add_argument("--keep", action="store_true", help="保留 build 中间件")
    args = ap.parse_args()

    ep_dir = Path(args.episode)
    if not ep_dir.is_absolute():
        ep_dir = ROOT / "episodes" / args.episode
    if not (ep_dir / "episode.json").exists():
        print(f"找不到 {ep_dir}/episode.json", file=sys.stderr)
        return 2

    info = build(ep_dir, theme_name=args.theme, subtitles=not args.no_subs, bg=args.bg)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if not args.keep:
        shutil.rmtree(ep_dir / "build", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
