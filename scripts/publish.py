#!/usr/bin/env python3
"""把一期内容填进视频号发布页，停在「发表」之前。

用法：
    python3 scripts/publish.py 2026-11-18-gold-vs-rates           # 预演，只打开页面看结构
    python3 scripts/publish.py 2026-11-18-gold-vs-rates --fill    # 真填：上传视频+封面+文案，不点发表

设计原则：任何一步找不到目标就停下、打印当前页面快照，绝不盲点。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POST_URL = "https://channels.weixin.qq.com/platform/post/create"


def bsk(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(["bsk", *args], capture_output=True, text=True, timeout=timeout)


def snapshot(max_tokens: int = 8000) -> str:
    proc = bsk("sync", "--max-tokens", str(max_tokens))
    out = proc.stdout
    # sync 可能直接把 VOM 打在 stdout，也可能包在 JSON 里
    if '"ok"' in out and '"vom"' in out:
        try:
            data = json.loads(out)
            return data.get("vom") or data.get("snapshot") or out
        except json.JSONDecodeError:
            pass
    return out


def find_ref(snap: str, *patterns: str) -> str | None:
    """在快照里找第一个 aria 名包含任一关键词的元素，返回 @eN。"""
    for line in snap.splitlines():
        if not re.search(r"@e\d+", line):
            continue
        for p in patterns:
            if p in line:
                m = re.search(r"(@e\d+)", line)
                if m:
                    return m.group(1)
    return None


def step_click(ref: str) -> bool:
    proc = bsk("step", "--action", "click", "--params", json.dumps({"ref": ref}))
    return '"ok": true' in proc.stdout


def step_fill(ref: str, value: str) -> bool:
    proc = bsk("step", "--action", "fill", "--params", json.dumps({"ref": ref, "value": value}))
    return '"ok": true' in proc.stdout


def upload(path: Path, target: str | None) -> bool:
    args = ["upload", "--file", str(path)]
    if target:
        args.append(target)
    proc = bsk(*args, timeout=600)
    return '"ok": true' in proc.stdout


def needs_login(snap: str) -> bool:
    return any(k in snap for k in ("扫码登录", "请使用微信扫码", "二维码", "Log in", "登录"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--fill", action="store_true", help="真正上传与填写；不带此参数只打开页面")
    ap.add_argument("--dump", action="store_true", help="打印完整快照")
    args = ap.parse_args()

    ep_dir = Path(args.episode)
    if not ep_dir.is_absolute():
        ep_dir = ROOT / "episodes" / args.episode

    video, cover = ep_dir / "video.mp4", ep_dir / "cover.png"
    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))

    for f in (video, cover):
        if not f.exists():
            print(f"缺文件：{f}", file=sys.stderr)
            return 2

    print("→ 打开视频号发布页")
    proc = bsk("open", POST_URL, "--max-tokens", "8000", "--timeout-ms", "30000")
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return 3

    snap = snapshot()
    if needs_login(snap):
        print("\n需要登录：请在打开的窗口里用微信扫码。扫完再跑一次本命令。")
        print(snap[:2500])
        return 4

    if not args.fill:
        print("\n预演模式，页面已打开。当前结构：\n")
        print(snap[:4000])
        return 0

    # 1) 上传视频
    print("→ 上传视频")
    target = find_ref(snap, "添加视频", "上传视频", "选择文件", "拖拽", "Upload")
    if not upload(video, target):
        if not upload(video, None):
            print("视频上传没成功，当前页面：\n" + snap[:3000], file=sys.stderr)
            return 5

    # 2) 等转码
    for i in range(40):
        time.sleep(6)
        snap = snapshot()
        if "发表" in snap and "上传" not in snap.split("发表")[0][-40:]:
            break
        if re.search(r"(上传成功|转码完成|100%)", snap):
            break
        print(f"   等待上传/转码… {(i + 1) * 6}s")

    # 3) 填描述
    desc = ep.get("description") or ep["title"]
    tags = " ".join(f"#{t}" for t in ep.get("tags", []))
    body = f"{desc}\n\n{tags}" if tags else desc
    ref = find_ref(snap, "添加描述", "描述", "简介", "说点什么")
    if ref and step_fill(ref, body):
        print(f"→ 描述已填（{len(body)} 字）")
    else:
        print("! 没找到描述输入框，请手动粘贴 caption.md 的内容")

    # 4) 封面
    cref = find_ref(snap, "选择封面", "更换封面", "设置封面")
    if cref and step_click(cref):
        time.sleep(2)
        snap = snapshot()
        kref = find_ref(snap, "上传封面", "选择文件", "上传图片")
        if kref and upload(cover, kref):
            print("→ 封面已上传")
        else:
            print("! 封面没传上，请手动选 cover.png")

    snap = snapshot()
    if args.dump:
        print(snap)
    print("\n已完成填写。请在窗口里检查后点「发表」。")
    print("停下来之前没有任何发布动作，账号安全。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
