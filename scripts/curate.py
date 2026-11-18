#!/usr/bin/env python3
"""给推文打分排序，输出候选清单（人工/模型从中选题）。"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VIEW_WORDS = [
    "本质", "其实", "意味着", "逻辑", "原因是", "所以", "因为", "本质上是",
    "拆解", "结构", "对比", "历史上", "数据", "占比", "周期", "估值",
    "我认为", "我的看法", "值得注意", "关键是", "核心是", "误区", "真相",
]


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def score(item: dict, cfg: dict) -> float:
    text = item["text"]
    low = text.lower()
    s = 0.0

    sc = cfg["scoring"]
    n = len(text)
    if n < sc["min_chars"]:
        s -= 6
    elif n > sc["max_chars"]:
        s -= 2
    else:
        s += 2

    for kw in sc["boost_keywords"]:
        if kw in low:
            s += 1.5
    for kw in sc["penalty_keywords"]:
        if kw in low:
            s -= 5
    for kw in sc["drop_keywords"]:
        if kw in low:
            s -= 8

    for kw in VIEW_WORDS:
        if kw in text:
            s += 0.8

    # 纯行情播报：短 + 大量数字 + 无解释
    if n < 90 and len(re.findall(r"\d", text)) > 6:
        s -= 3
    # 有链接/图的更像完整观点
    if item.get("url"):
        s += 0.3

    return round(s, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--handle", default=None, help="只看某个账号")
    args = ap.parse_args()

    cfg = load_config()
    rows = json.loads(sorted((ROOT / "data" / "raw").glob("*.json"))[-1].read_text("utf-8"))

    if args.handle:
        rows = [r for r in rows if r["handle"].lower() == args.handle.lower()]

    for r in rows:
        r["score"] = score(r, cfg)
    rows.sort(key=lambda r: r["score"], reverse=True)

    top = [r for r in rows if r["score"] > 0][: args.top]
    for i, r in enumerate(top, 1):
        text = r["text"].replace("\n", " ")
        if len(text) > 220:
            text = text[:220] + "…"
        print(f"[{i:>2}] {r['score']:>5}  @{r['handle']}")
        print(f"     {text}")
        print(f"     {r['url']}")
        print()

    print(f"— 候选 {len(top)} / 全量 {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
