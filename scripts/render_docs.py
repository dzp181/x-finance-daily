#!/usr/bin/env python3
"""从 episode.json 生成 script.md（分镜脚本）和 caption.md（发布文案）。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def mmss(sec: float) -> str:
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    args = ap.parse_args()

    ep_dir = Path(args.episode)
    if not ep_dir.is_absolute():
        ep_dir = ROOT / "episodes" / args.episode

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    timings = ep.get("timings") or [0] * len(ep["scenes"])
    total = ep.get("seconds") or sum(timings)

    # ---------- script.md ----------
    tts_conf = ep.get("tts") or {}
    if tts_conf.get("engine") == "edge":
        voice_desc = f"edge-tts {tts_conf.get('voice', '')}（语速 {tts_conf.get('rate', '+0%')}）"
    else:
        voice_desc = f"macOS say {ep.get('voice', 'Tingting')}（语速 {ep.get('rate', 178)}）"

    lines = [
        f"# {ep['title']}",
        "",
        f"> {ep.get('topic', '金融科普')} · 竖屏口播 · 全长 {mmss(total)} · {ep['date']}",
        "",
        f"画面风格：白底黑字，左侧红色竖条，逐句切换。配音：{voice_desc}。",
        "",
        "| 时间 | 画面大字 | 口播 |",
        "| --- | --- | --- |",
    ]
    t = 0.0
    for scene, dur in zip(ep["scenes"], timings):
        cap = scene["caption"].replace("\n", " / ")
        lines.append(f"| {mmss(t)}–{mmss(t + dur)} | {cap} | {scene['text']} |")
        t += dur
    lines += [
        "",
        "## 录制说明",
        "",
        "- 想用自己的声音：按上表逐句录，每句单独一个文件，命名 `s0.m4a`…`s9.m4a` 放进 `build/` 替换 TTS 音轨。",
        "- 语速控制在每句 6–10 秒，整条不超过 90 秒。",
        f"- 结尾必须带免责声明：{cfg['channel']['disclaimer']}",
    ]
    (ep_dir / "script.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------- caption.md ----------
    tags = ep.get("tags") or ["黄金", "美联储", "利率", "金融科普"]
    desc = ep.get("description") or (
        f"{ep['title']}。\n\n"
        "一句话说清：黄金不生息，利率越高，持有它的机会成本就越大。\n"
        "这轮下跌不是「避险失效」，是「贴现率上行」。\n\n"
        f"{cfg['channel']['disclaimer']}"
    )
    cap_lines = [
        "# 视频号发布文案",
        "",
        "## 标题",
        "",
        ep.get("short_title") or ep["title"],
        "",
        "## 描述",
        "",
        desc,
        "",
        "## 话题",
        "",
        " ".join(f"#{t}" for t in tags),
        "",
        "## 封面",
        "",
        "`cover.png`（1080×1440，3:4）",
        "",
        "## 视频",
        "",
        f"`video.mp4`（1080×1920，{mmss(total)}）",
    ]
    (ep_dir / "caption.md").write_text("\n".join(cap_lines) + "\n", encoding="utf-8")

    print(f"script.md / caption.md 已写入 {ep_dir.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
