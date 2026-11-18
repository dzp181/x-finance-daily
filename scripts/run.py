#!/usr/bin/env python3
"""一条命令跑完一期：抓取 → 选题 → 建草稿 → 出片 → 出文案。

    python3 scripts/run.py                  # 抓取 + 候选 + 建当天草稿（选第 1 名）
    python3 scripts/run.py --pick 3         # 选第 3 名候选
    python3 scripts/run.py --slug gold      # 指定 slug（默认 draft）
    python3 scripts/run.py --offline        # 不联网，用本地缓存候选
    python3 scripts/run.py build            # 最新一期 → video.mp4 + cover.png + md
    python3 scripts/run.py build <slug> --crf 16
    python3 scripts/run.py status           # 看每一期做到哪一步
    python3 scripts/run.py all              # 抓取 + 建草稿 + 出片（草稿已有文案时）

设计上只做「编排」，不重复实现：抓取调 fetch.py，打分调 curate.py，
出片调 build_episode.py，文档调 render_docs.py。任何一步单独跑都还是能跑。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
EPISODES = ROOT / "episodes"
CST = timezone(timedelta(hours=8))


# ---------------------------------------------------------------- 工具

def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def today() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def auto_slot() -> str:
    """一天两期：12 点前算 am，之后算 pm，同一天不会互相覆盖。"""
    return "am" if datetime.now(CST).hour < 12 else "pm"


def sh(cmd: list[str]) -> int:
    """跑一个子脚本，实时透传输出。"""
    print(f"\n$ {' '.join(cmd[1:])}\n", flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def find_episode(arg: str | None) -> Path | None:
    """按名字/路径找一期；不给参数就取最新一期（有 episode.json 的）。"""
    if arg:
        p = Path(arg)
        if not p.is_absolute():
            p = EPISODES / arg
        return p if (p / "episode.json").exists() else None
    dirs = [d for d in EPISODES.glob("*") if (d / "episode.json").exists()]
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None


def is_ready(ep_dir: Path) -> tuple[bool, str]:
    """草稿是否已经补齐文案，能不能直接出片。"""
    try:
        ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, f"episode.json 读不了: {exc}"
    if not ep.get("scenes"):
        return False, "scenes 是空的——还需要写口播词"
    if not ep.get("title") or "TODO" in json.dumps(ep, ensure_ascii=False):
        return False, "还有 TODO 占位没填"
    for i, s in enumerate(ep["scenes"], 1):
        if not s.get("text") or not s.get("caption"):
            return False, f"第 {i} 句缺 text 或 caption"
    return True, "ok"


# ---------------------------------------------------------------- 选题

def load_ranked(cfg: dict, offline: bool) -> list[dict]:
    sys.path.insert(0, str(SCRIPTS))
    import curate  # noqa: PLC0415

    if not offline:
        sh([sys.executable, str(SCRIPTS / "fetch.py")])
    cache = sorted((ROOT / "data" / "raw").glob("*.json"))
    if not cache:
        raise SystemExit("data/raw 里没有候选，先跑 fetch.py")
    rows = json.loads(cache[-1].read_text(encoding="utf-8"))
    for r in rows:
        r["score"] = curate.score(r, cfg)
    rows.sort(key=lambda r: r["score"], reverse=True)
    return [r for r in rows if r["score"] > 0]


def guess_tags(text: str, cfg: dict, k: int = 4) -> list[str]:
    low = text.lower()
    hits = [kw for kw in cfg["scoring"]["boost_keywords"] if kw in low]
    # 中文词优先，英文缩写（cpi/fed/hbm）当标签不好看
    cn = [h for h in hits if not h.isascii()]
    tags = (cn or hits)[:k]
    return tags or ["金融科普"]


def used_seeds() -> set[str]:
    """已经做过选题的推文 URL，避免一天两期选到同一条。"""
    used = set()
    for d in EPISODES.glob("*"):
        f = d / "episode.json"
        if f.exists():
            try:
                ep = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                ep = {}
            url = (ep.get("seed") or {}).get("url")
            if url:
                used.add(url)
        src = d / "sources.md"
        if src.exists():
            used.update(re.findall(r"https://x\.com/[A-Za-z0-9_]+/status/\d+", src.read_text("utf-8")))
    return used


def cmd_new(args) -> int:
    cfg = load_config()
    ranked = load_ranked(cfg, args.offline)
    if not ranked:
        print("今天没有正分候选，换个窗口：python3 scripts/fetch.py --since 24h")
        return 1

    used = set() if args.allow_repeat else used_seeds()
    if used:
        ranked = [r for r in ranked if r.get("url") not in used]
        print(f"已排除 {len(used)} 条做过的选题")

    print(f"\n候选 {len(ranked)} 条，前 {args.top}：\n")
    for i, r in enumerate(ranked[: args.top], 1):
        text = r["text"].replace("\n", " ")
        print(f"[{i:>2}] {r['score']:>5.1f}  @{r['handle']:<18} {r['url']}")
        print(f"     {text[:150]}{'…' if len(text) > 150 else ''}")

    idx = max(1, args.pick)
    if idx > len(ranked):
        print(f"\n只有 {len(ranked)} 条候选，--pick {idx} 超了")
        return 1
    seed = ranked[idx - 1]

    slug = args.slug or auto_slot()
    ep_dir = EPISODES / f"{today()}-{slug}"
    ep_file = ep_dir / "episode.json"
    if ep_file.exists() and not args.force:
        print(f"\n{ep_dir.name} 已存在。要重建加 --force，要出片跑：python3 scripts/run.py build {ep_dir.name}")
        args.episode = ep_dir.name
        return 0

    ep_dir.mkdir(parents=True, exist_ok=True)
    seed_text = seed["text"]
    ep = {
        "slug": slug,
        "date": today(),
        "status": "draft",
        "title": "TODO 标题：一句反常识的结论",
        "cover_caption": "TODO 封面大字\n（两行，每行 ≤ 7 字）",
        "subtitle": "TODO 副标题",
        "short_title": "TODO 标题：一句反常识的结论",
        "tags": guess_tags(seed_text, cfg),
        "description": "TODO 描述：首行复述标题，中段讲机制，末行留免责声明（config.json 里那段）",
        "topic": "金融科普",
        "tts": {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "+10%"},
        "seed": {
            "handle": seed["handle"],
            "url": seed["url"],
            "score": seed["score"],
            "text": seed_text,
        },
        "references": [
            {"handle": r["handle"], "url": r["url"], "text": r["text"][:600]}
            for r in ranked[:5]
        ],
        "scenes": [],
    }
    ep_file.write_text(json.dumps(ep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    src = [
        f"# 资料来源 · {ep_dir.name}",
        "",
        f"> 选题来自 @{seed['handle']} 的这条推文（评分 {seed['score']}）。"
        "数字必须能在下表找到出处，找不到的就不写进口播。",
        "",
        "| 用于 | 账号 | 推文 | 关键数据 |",
        "| --- | --- | --- | --- |",
        f"| TODO 哪一句 | @{seed['handle']} | [链接]({seed['url']}) | TODO 引用的数字/原文 |",
        "",
        "## 参考候选（同窗口其它高分推文）",
        "",
    ]
    for r in ranked[1:5]:
        src.append(f"- @{r['handle']} · {r['score']} · {r['url']}")
        src.append(f"  - {r['text'][:160].replace(chr(10), ' ')}")
    src += [
        "",
        "## 口径",
        "",
        "- 只讲机制，不预测方向、不给点位、不推荐标的。",
        "- 来源之间冲突时，取最保守的说法，或只作定性描述。",
    ]
    (ep_dir / "sources.md").write_text("\n".join(src) + "\n", encoding="utf-8")

    print(f"\n草稿：{ep_dir}/episode.json")
    print(f"       {ep_dir}/sources.md")
    print("种子推文：")
    print("  " + seed_text.replace("\n", "\n  "))
    print("\n下一步：补 title / scenes[]（每句 text 6–10 秒，caption ≤ 14 字两行），然后")
    print(f"  python3 scripts/run.py build {ep_dir.name} --crf 16")
    args.episode = ep_dir.name
    return 0


# ---------------------------------------------------------------- 出片

def cmd_build(args) -> int:
    ep_dir = find_episode(args.episode)
    if not ep_dir:
        print("找不到这一期的 episode.json")
        return 1
    ok, why = is_ready(ep_dir)
    if not ok:
        print(f"{ep_dir.name} 还不能出片：{why}")
        print(f"先把 {ep_dir / 'episode.json'} 补完")
        return 1

    rc = sh([
        sys.executable, str(SCRIPTS / "build_episode.py"), ep_dir.name,
        "--theme", args.theme, "--crf", str(args.crf),
    ])
    if rc != 0:
        return rc
    rc = sh([sys.executable, str(SCRIPTS / "render_docs.py"), ep_dir.name])
    if rc != 0:
        return rc

    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    video = ep_dir / "video.mp4"
    mb = video.stat().st_size / 1024 / 1024 if video.exists() else 0
    print(f"\n=== {ep['title']} ===")
    print(f"时长 {ep.get('seconds', '?')}s · {mb:.1f} MB · {len(ep['scenes'])} 句")
    print(f"视频 {video}")
    print(f"封面 {ep_dir / 'cover.png'}")
    print(f"文案 {ep_dir / 'caption.md'}")
    print("\n发布：见 README「发布」一节（上传没法脚本化，最后一下要人点）")
    return 0


def cmd_status(args) -> int:
    dirs = sorted([d for d in EPISODES.glob("*") if (d / "episode.json").exists()])
    if not dirs:
        print("还没有任何一期")
        return 0
    print(f"{'期':<34}{'状态':<12}{'时长':>7}{'体积':>9}  文档")
    for d in dirs:
        ep = json.loads((d / "episode.json").read_text(encoding="utf-8"))
        ready, why = is_ready(d)
        state = "已出片" if (d / "video.mp4").exists() else ("待出片" if ready else "草稿")
        video = d / "video.mp4"
        mb = f"{video.stat().st_size / 1024 / 1024:.1f}M" if video.exists() else "-"
        secs = f"{ep.get('seconds')}s" if ep.get("seconds") else "-"
        docs = "".join(
            "S" if (d / "script.md").exists() else "-"
        ) + "".join("C" if (d / "caption.md").exists() else "-")
        print(f"{d.name:<34}{state:<12}{secs:>7}{mb:>9}  {docs}")
        if state == "草稿":
            print(f"{'':<34}└ {why}")
    return 0


def cmd_all(args) -> int:
    rc = cmd_new(args)
    if rc != 0:
        return rc
    ep_dir = find_episode(getattr(args, "episode", None))
    if not ep_dir:
        return 0
    ok, why = is_ready(ep_dir)
    if not ok:
        print(f"\n草稿还没补齐（{why}），先停在这里。补完再跑 build。")
        return 0
    args.episode = ep_dir.name
    return cmd_build(args)


def cmd_sync(args) -> int:
    """打包成 ima 能收的形态（md + 口播音频），并打印上传清单。"""
    sys.path.insert(0, str(SCRIPTS))
    import pack_ima  # noqa: PLC0415

    ep_dir = find_episode(args.episode)
    if not ep_dir:
        print("找不到这一期的 episode.json")
        return 1
    if not (ep_dir / "video.mp4").exists():
        print(f"{ep_dir.name} 还没有 video.mp4，先 build")
        return 1
    m = pack_ima.pack(ep_dir, with_audio=not args.no_audio, with_cover=args.with_cover)
    print(f"打包完成 → outbox/{ep_dir.name}")
    for f in m["files"]:
        print(f"  {f['name']}  ({f['size'] / 1024:.0f} KB, {f['content_type']})")
    print(f"目标知识库：ima「视频号」 {m['knowledge_base_id']}")
    print("上传三步：create_media → scripts/ima_put.py → add_knowledge")
    return 0


# ---------------------------------------------------------------- 入口

def main() -> int:
    ap = argparse.ArgumentParser(description="x-finance-daily 一条命令流水线")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--offline", action="store_true", help="不联网，用本地缓存")
        p.add_argument("--top", type=int, default=8, help="打印前几名候选")
        p.add_argument("--pick", type=int, default=1, help="用第几名候选做草稿")
        p.add_argument("--slug", default=None, help="episode 目录后缀，默认 draft")
        p.add_argument("--force", action="store_true", help="覆盖已存在的草稿")
        p.add_argument("--allow-repeat", action="store_true", help="允许选已做过的推文")

    p_new = sub.add_parser("new", help="抓取 + 候选 + 建草稿")
    common(p_new)
    p_new.set_defaults(func=cmd_new)

    p_build = sub.add_parser("build", help="出片 + 出文案")
    p_build.add_argument("episode", nargs="?", default=None, help="不给就取最新一期")
    p_build.add_argument("--crf", type=int, default=16, help="默认 16（上传档）")
    p_build.add_argument("--theme", default="ink")
    p_build.set_defaults(func=cmd_build)

    p_status = sub.add_parser("status", help="看每期进度")
    p_status.set_defaults(func=cmd_status)

    p_all = sub.add_parser("all", help="new 然后 build（草稿已补齐时）")
    common(p_all)
    p_all.add_argument("--crf", type=int, default=16)
    p_all.add_argument("--theme", default="ink")
    p_all.set_defaults(func=cmd_all)

    p_sync = sub.add_parser("sync", help="打包成 ima 能收的形态（md + 口播音频）")
    p_sync.add_argument("episode", nargs="?", default=None, help="不给就取最新一期")
    p_sync.add_argument("--no-audio", action="store_true")
    p_sync.add_argument("--with-cover", action="store_true")
    p_sync.set_defaults(func=cmd_sync)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        # 裸跑 = new
        args = p_new.parse_args([])
        args.func = cmd_new
        args.episode = None
    if not hasattr(args, "episode"):
        args.episode = None
    return args.func(args)


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
