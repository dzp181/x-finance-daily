#!/usr/bin/env python3
"""用同一段文案批量试音，挑一个音色。

    python3 scripts/tts_preview.py --text "要试的句子" --out /tmp/voicetest
    python3 scripts/tts_preview.py --episode 2026-11-18-gold-vs-rates
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import edge_tts

ROOT = Path(__file__).resolve().parent.parent

# 中文财经口播的候选：男声偏沉稳，女声偏自然
VOICES = [
    ("zh-CN-YunjianNeural", "云健 · 男 · 沉稳解说"),
    ("zh-CN-YunyangNeural", "云扬 · 男 · 新闻播报"),
    ("zh-CN-YunxiNeural", "云希 · 男 · 年轻口语"),
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 女 · 自然"),
    ("zh-CN-XiaoyiNeural", "晓伊 · 女 · 偏活泼"),
]


async def synth(text: str, voice: str, out: Path, rate: str) -> None:
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(out))


async def main_async(args) -> int:
    text = args.text
    if args.episode:
        ep_dir = ROOT / "episodes" / args.episode
        ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
        text = "".join(s["text"] for s in ep["scenes"][2:4])

    if not text:
        print("给个 --text 或 --episode")
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"文案 {len(text)} 字，语速 {args.rate}\n")

    for voice, label in VOICES:
        path = out_dir / f"{voice}.mp3"
        try:
            await synth(text, voice, path, args.rate)
            print(f"  ✓ {label:<22} {path.name}  {path.stat().st_size // 1024} KB")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {label}: {exc}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="")
    ap.add_argument("--episode", default="")
    ap.add_argument("--out", default="/tmp/voicetest")
    ap.add_argument("--rate", default="+0%")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
