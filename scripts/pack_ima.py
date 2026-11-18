#!/usr/bin/env python3
"""把一期产物打包成 ima 能收的形态，落到 outbox/<episode>/。

ima 的 create_media 只认对照表里的类型（pdf/doc/ppt/xls/csv/md/txt/png/jpg/webp/mp3/m4a/wav/html…），
**没有 mp4**，所以视频文件进不去。这里做的是：

  <date>_<档位>_<标题>.md          一期全文：标题/描述/口播/分镜/来源/视频路径
  <date>_<档位>_<标题>_封面.png     3:4 封面
  <date>_<档位>_<标题>_口播.mp3     从成片抽出来的音轨（ima 能收，手机上能听）
  manifest.json                    给上传脚本/agent 用的清单（含 content_type 与大小）

用法：
    python3 scripts/pack_ima.py                  # 最新一期
    python3 scripts/pack_ima.py 2026-11-18-gold-vs-rates
    python3 scripts/pack_ima.py <slug> --no-audio
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "episodes"
OUTBOX = ROOT / "outbox"

KB_ID = "ima_kb_0eJCBmd6"  # ima「视频号」知识库

MIME = {
    ".md": "text/markdown",
    ".png": "image/png",
    ".mp3": "audio/mpeg",
}

SLOTS = [
    ("am", "早间"),
    ("pm", "晚间"),
]


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg  # noqa: PLC0415

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg") or "ffmpeg"


def slot_of(ep_dir: Path) -> str:
    suffix = ep_dir.name.split("-", 3)[-1] if ep_dir.name.count("-") >= 3 else ""
    for key, label in SLOTS:
        if suffix == key:
            return label
    return "首期"


def safe_title(title: str, limit: int = 16) -> str:
    """文件名里只留中文/字母/数字，控制长度。"""
    t = re.sub(r"[，。！？、,.!?：:；;「」“”‘’（）()\[\]《》\s%$#@/\\|*\"'<>~`]+", "", title)
    return (t or "未命名")[:limit]


def mmss(sec: float) -> str:
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


def find_episode(arg: str | None) -> Path | None:
    if arg:
        p = Path(arg)
        if not p.is_absolute():
            p = EPISODES / arg
        return p if (p / "episode.json").exists() else None
    dirs = [d for d in EPISODES.glob("*") if (d / "episode.json").exists()]
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None


def build_md(ep: dict, ep_dir: Path, base: str, slot: str) -> str:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    video = ep_dir / "video.mp4"
    mb = video.stat().st_size / 1024 / 1024 if video.exists() else 0
    tags = ep.get("tags") or []
    secs = ep.get("seconds")
    lines = [
        f"# {ep['date']} {slot} · {ep['title']}",
        "",
        f"- 日期：{ep['date']}（{slot}档）",
        f"- 时长：{secs:.1f} 秒" if seconds_fmt(secs) else "- 时长：-",
        f"- 规格：1080×1920 / 30fps / H.264，{mb:.1f} MB",
        f"- 话题：{' '.join('#' + t for t in tags) if tags else '-'}",
        f"- 主题：{ep.get('topic', '金融科普')}",
        "",
        "## 发布文案",
        "",
        "```text",
        (ep.get("description") or "").strip(),
        "```",
        "",
        "## 口播全文",
        "",
    ]
    t = 0.0
    timings = ep.get("timings") or [0] * len(ep.get("scenes", []))
    for i, (scene, dur) in enumerate(zip(ep.get("scenes", []), timings), 1):
        lines.append(f"{i:>2}. [{mmss(t)}] {scene['text']}")
        t += dur
    lines += ["", "## 分镜", "", "| 时间 | 画面大字 | 口播 |", "| --- | --- | --- |"]
    t = 0.0
    for scene, dur in zip(ep.get("scenes", []), timings):
        cap = scene["caption"].replace("\n", " / ")
        lines.append(f"| {mmss(t)}–{mmss(t + dur)} | {cap} | {scene['text']} |")
        t += dur

    src = ep_dir / "sources.md"
    if src.exists():
        body = src.read_text(encoding="utf-8")
        body = re.sub(r"^# .*\n+", "", body)  # 去掉原标题，别和上面重复
        lines += ["", "## 资料来源", "", body.strip()]

    lines += [
        "",
        "## 成片",
        "",
        f"- 视频：`{video}`（ima 不接受 mp4，成片留在本地，发布时手动上传视频号）",
        f"- 封面：`{ep_dir / 'cover.png'}`",
        "",
        "---",
        "",
        cfg["channel"]["disclaimer"],
        "",
    ]
    return "\n".join(x for x in lines if x is not None)


def seconds_fmt(secs) -> bool:
    return isinstance(secs, (int, float)) and secs > 0


def pack(ep_dir: Path, with_audio: bool = True, with_cover: bool = False) -> dict:
    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    slot = slot_of(ep_dir)
    base = f"{ep['date']}_{slot}_{safe_title(ep['title'])}"
    # ima 里显示的名字：日期 + 档位 + 完整标题，检索时一眼能认
    display = f"{ep['date']} {slot} | {ep['title']}"

    dest = OUTBOX / ep_dir.name
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*"):
        old.unlink()

    files: list[dict] = []

    md_path = dest / f"{base}.md"
    md_path.write_text(build_md(ep, ep_dir, base, slot), encoding="utf-8")
    files.append({"file": md_path, "name": f"{display}.md"})

    cover = ep_dir / "cover.png"
    if with_cover and cover.exists():
        c = dest / f"{base}_封面.png"
        shutil.copy2(cover, c)
        files.append({"file": c, "name": f"{display} · 封面.png"})

    video = ep_dir / "video.mp4"
    if with_audio and video.exists():
        mp3 = dest / f"{base}_口播.mp3"
        subprocess.run(
            [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video),
             "-vn", "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)],
            check=True,
        )
        files.append({"file": mp3, "name": f"{display} · 口播音频.mp3"})

    manifest = {
        "knowledge_base_id": KB_ID,
        "knowledge_base_name": "视频号",
        "episode": ep_dir.name,
        "title": ep["title"],
        "date": ep["date"],
        "slot": slot,
        "video": str(video),
        "video_mb": round(video.stat().st_size / 1024 / 1024, 1) if video.exists() else 0,
        "files": [
            {
                "path": str(f["file"]),
                "name": str(f["name"]),
                "content_type": MIME.get(f["file"].suffix, ""),
                "size": f["file"].stat().st_size,
            }
            for f in files
        ],
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="打包一期产物，供上传到 ima 视频号知识库")
    ap.add_argument("episode", nargs="?", default=None, help="不给就取最新一期")
    ap.add_argument("--no-audio", action="store_true", help="不抽音轨")
    ap.add_argument("--with-cover", action="store_true", help="连封面一起打包上传")
    args = ap.parse_args()

    ep_dir = find_episode(args.episode)
    if not ep_dir:
        print("找不到这一期的 episode.json", file=sys.stderr)
        return 1

    m = pack(ep_dir, with_audio=not args.no_audio, with_cover=args.with_cover)
    print(f"打包完成 → {OUTBOX / ep_dir.name}")
    for f in m["files"]:
        kb = f["size"] / 1024
        print(f"  {f['path'].split('/')[-1]}  ({kb:.0f} KB, {f['content_type']})")
        print(f"    入库名：{f['name']}")
    print(f"\n目标知识库：ima「视频号」 {m['knowledge_base_id']}")
    print("上传：create_media → PUT presigned_url（scripts/ima_put.py）→ add_knowledge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
