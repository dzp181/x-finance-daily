#!/usr/bin/env python3
"""把一期成片送进视频号草稿箱 —— 只存草稿，绝不点发表。

底层用 vendor/social-auto-upload 的 tencent uploader，原因：
  - 视频号发表页整张表单在 iframe 里，bsk 的 click 会被「目标被遮挡」拦死；
    这个 uploader 遍历所有 frame 找 input[type=file] 再 set_input_files，能过。
  - is_draft=True 时最后点的是「保存草稿」，不是「发表」。
  - 顺带把三件手工活也做了：视频标注「含AI生成内容」、3:4 封面、短标题。

必须用 .venv-sau（Python 3.12 + patchright）启动，且启动前清掉 PYTHONPATH：
WorkBuddy 注入的 sandbox sitecustomize 会让第三方库的 mkdir(exist_ok=True) 抛 EEXIST。
统一入口是 scripts/push_draft.sh，别直接拿系统 python3 跑这个文件。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "social-auto-upload"
EPISODES = ROOT / "episodes"
OUTBOX = ROOT / "outbox"

DEFAULT_ACCOUNT = "doge"


def die(msg: str, code: int = 1):
    print(f"× {msg}", file=sys.stderr)
    sys.exit(code)


def check_runtime() -> None:
    if "sandbox-center-runtime-kit" in os.environ.get("PYTHONPATH", ""):
        die(
            "检测到 WorkBuddy 注入的 PYTHONPATH，会让 patchright/setuptools 抛 EEXIST。\n"
            "  用入口脚本：bash scripts/push_draft.sh ..."
        )
    if not VENDOR.exists():
        die(f"没找到 {VENDOR}\n  先跑：bash scripts/setup_sau.sh")
    if not (VENDOR / "conf.py").exists():
        die("vendor 里缺 conf.py，跑一次 scripts/setup_sau.sh 会补上")
    sys.path.insert(0, str(VENDOR))
    os.chdir(VENDOR)  # conf 里的 logs/ cookies/ 都落在 vendor 下，别散到项目根


def find_episode(name: str | None) -> Path:
    if name:
        p = EPISODES / name
        if not (p / "episode.json").exists():
            die(f"{p} 里没有 episode.json")
        return p
    cands = sorted(
        (d for d in EPISODES.iterdir() if (d / "episode.json").exists()),
        key=lambda d: d.name,
    )
    if not cands:
        die("episodes/ 下还没有任何一期")
    return cands[-1]


def build_payload(ep_dir: Path, cover: Path | None) -> dict:
    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    video = ep_dir / "video.mp4"
    if not video.exists():
        die(f"{ep_dir.name} 还没出片，先跑 python3 scripts/run.py build {ep_dir.name}")

    raw = (ep.get("description") or "").strip()
    if not raw:
        die("episode.json 里没有 description，视频号描述没法填")

    # 描述第一行就是标题句；剩下的正文原样跟在其后
    head, _, body = raw.partition("\n")
    title = head.strip()
    body = body.strip("\n")

    tags = [t.lstrip("#").strip() for t in (ep.get("tags") or []) if t.strip()]
    hashtags = " ".join(f"#{t}" for t in tags)
    # 话题接在正文末尾（不挂在标题下面），视频号编辑器会把 # 开头的串识别成话题
    desc = f"{body}\n\n{hashtags}".strip() if hashtags else body

    return {
        "title": title,
        "desc": desc,
        "short_title": (ep.get("short_title") or title).strip(),
        "video": video,
        "cover": cover if cover and cover.exists() else None,
        "episode": ep_dir.name,
        "tags": tags,
        "date": ep.get("date"),
        "seconds": ep.get("seconds"),
    }


def account_file(account: str) -> Path:
    return VENDOR / "cookies" / f"tencent_{account}.json"


def copy_qr(payload: dict) -> None:
    src = Path(payload.get("image_path") or "")
    if not src.exists():
        return
    OUTBOX.mkdir(exist_ok=True)
    dst = OUTBOX / "login_qr.png"
    shutil.copy2(src, dst)
    print(f"二维码：{dst}", flush=True)


def cmd_login(args) -> int:
    from uploader.tencent_uploader.main import tencent_cookie_gen

    af = account_file(args.account)
    print(f"账号文件：{af}")
    # 直接调 cookie_gen：能指定等待轮次，且二维码一落盘就复制到 outbox 供展示
    res = asyncio.run(
        tencent_cookie_gen(
            str(af),
            qrcode_callback=copy_qr,
            poll_interval=3,
            max_checks=int(args.wait_minutes * 20),
            headless=not args.show,
        )
    )
    if res.get("success"):
        print(f"√ 登录成功，cookie 已存 {af}")
        return 0
    print(f"× 登录失败：{res.get('message')}（status={res.get('status')}）", file=sys.stderr)
    return 1


def cmd_check(args) -> int:
    from uploader.tencent_uploader.main import cookie_auth

    af = account_file(args.account)
    if not af.exists():
        print("× 还没有 cookie，先跑 --login")
        return 1
    ok = asyncio.run(cookie_auth(str(af)))
    print("√ cookie 有效" if ok else "× cookie 已失效，需要 --login")
    return 0 if ok else 1


def cmd_push(args) -> int:
    from uploader.tencent_uploader.main import TencentVideo

    ep_dir = find_episode(args.episode)
    cover = None if args.no_cover else ep_dir / "cover.png"
    p = build_payload(ep_dir, cover)

    print(f"期次：{p['episode']}（{p['seconds']}s）")
    print(f"视频：{p['video']}  {p['video'].stat().st_size / 1024 / 1024:.1f} MB")
    print(f"封面：{p['cover'] or '（不传）'}")
    print(f"短标题：{p['short_title']}")
    print(f"描述首行：{p['title']}")
    print(f"话题：{' '.join('#' + t for t in p['tags']) or '（无）'}")
    print(f"描述字数：{len(p['desc'])}")

    if args.dry_run:
        print("\n--- 描述全文 ---")
        print(p["desc"])
        print("--- 干跑结束，没有上传 ---")
        return 0

    af = account_file(args.account)
    if not af.exists():
        die(f"没有 {af}，先跑一次登录：bash scripts/push_draft.sh --login", 2)

    app = TencentVideo(
        title=p["title"],
        file_path=str(p["video"]),
        tags=[],  # 话题已写进 desc 末尾，避免被插到标题下面
        publish_date=0,
        account_file=str(af),
        is_draft=True,  # ← 只存草稿，不发表
        desc=p["desc"],
        thumbnail_portrait_path=str(p["cover"]) if p["cover"] else None,
        short_title=p["short_title"],
        headless=not args.show,
    )
    asyncio.run(app.tencent_upload_video())
    print("\n√ 已存进视频号草稿箱，去后台检查后自己点发表")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把成片存进视频号草稿箱（不发表）")
    ap.add_argument("episode", nargs="?", help="期目录名，不给就取最新一期")
    ap.add_argument("--account", default=DEFAULT_ACCOUNT, help=f"账号名，默认 {DEFAULT_ACCOUNT}")
    ap.add_argument("--login", action="store_true", help="扫码登录，保存 cookie")
    ap.add_argument("--check", action="store_true", help="检查 cookie 是否还有效")
    ap.add_argument("--show", action="store_true", help="显示浏览器窗口，便于盯流程")
    ap.add_argument("--wait-minutes", type=int, default=10, help="扫码等待分钟数，默认 10")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要提交的内容，不上传")
    ap.add_argument("--no-cover", action="store_true", help="不设置 3:4 封面")
    args = ap.parse_args()

    check_runtime()

    if args.login:
        return cmd_login(args)
    if args.check:
        return cmd_check(args)
    return cmd_push(args)


if __name__ == "__main__":
    sys.exit(main())
