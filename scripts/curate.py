#!/usr/bin/env python3
"""给推文打分，输出粗筛候选。

打分只做一件事：从 300 多条里捞出「可能值得讲」的 30–60 条。
它不判断选题质量——那是 cluster.py 聚成事件后、由模型来判断的。

v2 相对 v1 的改动（都是为了不再「谁长谁赢」）：
  - 加分词按 sqrt 衰减并封顶，长贴不能靠堆术语刷分
  - 加时效衰减：4 小时内的新消息才算新闻
  - 加账号权重：闲聊/币圈/政治类降权，研报类加权，刷屏账号额外扣
  - 加数字密度信号：要能在 sources.md 写出处，前提是推文里有数字
  - 每项贡献都写进 parts，方便对照和调参
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CST = timezone(timedelta(hours=8))

# 讲机制用的因果/解释类词，和「观点词」分开：前者才是一集能立住的前提
MECH_WORDS = [
    "因为", "所以", "意味着", "本质", "逻辑", "驱动", "传导", "定价", "拆解",
    "取决于", "背后", "而不是", "而非", "反而", "原因", "机制", "对应",
]

NUM_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|个基点|基点|bp|BP|万亿|亿美元|亿美元|亿|万|美元|点|倍)?"
)
DATE_RE = re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}-\d{1,2}-\d{1,2}")
URL_RE = re.compile(r"https?://\S+")


def extract_numbers(text: str, limit: int = 12) -> list[str]:
    """只留「值得核实」的数字：带单位，或绝对值够大；去掉链接与序号。"""
    clean = URL_RE.sub(" ", text)
    out: list[str] = []
    for m in NUM_RE.finditer(clean):
        tok = m.group(0).strip()
        core = float(re.match(r"\d+(?:\.\d+)?", tok).group())
        unit = bool(re.search(r"[%％亿万美元点倍]|基点|bp", tok))
        if not unit and core < 100:
            continue
        if tok not in out:
            out.append(tok)
        if len(out) >= limit:
            break
    return out


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def _age_hours(item: dict, now: float | None = None) -> float:
    ts = item.get("time")
    if not isinstance(ts, (int, float)):
        return 48.0
    now = now if now is not None else datetime.now(CST).timestamp()
    return max(0.0, (now - ts) / 3600.0)


def score_detail(item: dict, cfg: dict, account_volume: dict | None = None) -> dict:
    """返回 {score, parts, signals}，每个加减项都能追溯。"""
    text = item.get("text") or ""
    low = text.lower()
    sc = cfg["scoring"]
    n = len(text)
    parts: dict[str, float] = {}

    # ---- 长度：只做温和修正，长贴不该被重罚（研报本来就长）
    if n < sc["min_chars"]:
        parts["len"] = -6.0
    elif n > sc["max_chars"]:
        parts["len"] = -1.0
    else:
        parts["len"] = 2.0

    # ---- 加分词：sqrt 衰减 + 封顶，堆词无效
    hits = [k for k in sc["boost_keywords"] if k in low]
    cap = float(sc.get("boost_cap", 9.0))
    parts["boost"] = round(min(cap, 2.4 * math.sqrt(len(hits))), 2)

    # ---- 扣分/丢弃词：线性，这类词本身就罕见
    pen = sum(1 for k in sc["penalty_keywords"] if k in low)
    drop = sum(1 for k in sc["drop_keywords"] if k in low)
    parts["penalty"] = round(-5.0 * pen, 2)
    parts["drop"] = round(-8.0 * drop, 2)

    # ---- 机制词：能不能讲因果，比有多少术语更重要
    mech = [k for k in MECH_WORDS if k in text]
    parts["mech"] = round(min(5.0, 1.1 * len(mech)), 2)

    # ---- 数字密度：有具体数字才可能有可核实的一集
    nums = extract_numbers(text)
    parts["numbers"] = 1.2 if len(nums) >= 3 else 0.0
    parts["date"] = 0.6 if DATE_RE.search(text) else 0.0

    # ---- 时效：48 小时窗口里，越新越像新闻
    age = _age_hours(item)
    if age <= 4:
        parts["recency"] = 2.0
    elif age <= 10:
        parts["recency"] = 1.2
    elif age <= 24:
        parts["recency"] = 0.6
    else:
        parts["recency"] = 0.0

    # ---- 账号权重
    handle = item.get("handle") or ""
    if handle in set(sc.get("noise_accounts", [])):
        parts["account"] = -3.5
    elif handle in set(sc.get("trusted_accounts", [])):
        parts["account"] = 1.5
    else:
        parts["account"] = 0.0

    # ---- 刷屏惩罚：单账号在窗口内发太多，自动降权，防止一家霸榜
    vol = (account_volume or {}).get(handle, 0)
    if vol > 35:
        parts["flood"] = -2.5
    elif vol > 20:
        parts["flood"] = -1.5
    else:
        parts["flood"] = 0.0

    # ---- 形态：短、只有数字、没有因果词 = 行情播报
    digits = len(re.findall(r"\d", text))
    if n < 90 and digits > 6 and not mech:
        parts["form"] = -3.0
    elif n < 60 and digits > 0 and not mech:
        parts["form"] = -1.5
    else:
        parts["form"] = 0.0

    if item.get("url"):
        parts["url"] = 0.3

    return {
        "score": round(sum(parts.values()), 2),
        "parts": parts,
        "signals": {
            "chars": n,
            "boost_hits": hits,
            "mech_hits": mech,
            "numbers": nums,
            "has_date": bool(DATE_RE.search(text)),
            "age_hours": round(age, 1),
            "account_volume": vol,
        },
    }


def score(item: dict, cfg: dict, account_volume: dict | None = None) -> float:
    return score_detail(item, cfg, account_volume)["score"]


def account_volumes(rows: list[dict]) -> dict:
    vol: dict[str, int] = {}
    for r in rows:
        h = r.get("handle") or ""
        vol[h] = vol.get(h, 0) + 1
    return vol


def rank(rows: list[dict], cfg: dict, top: int | None = None) -> list[dict]:
    """打分 + 按账号限流，返回有序候选（每项带 score / parts / signals）。"""
    sc = cfg["scoring"]
    vols = account_volumes(rows)
    out = []
    for r in rows:
        d = score_detail(r, cfg, vols)
        if d["score"] < float(sc.get("min_score", 3.0)):
            continue
        item = dict(r)
        item["score"] = d["score"]
        item["parts"] = d["parts"]
        item["signals"] = d["signals"]
        out.append(item)
    out.sort(key=lambda x: x["score"], reverse=True)

    # 每账号最多进 pool 几条，避免一个账号把候选池占满
    cap = int(sc.get("per_account_cap", 6))
    kept, seen = [], {}
    for r in out:
        h = r.get("handle") or ""
        if seen.get(h, 0) >= cap:
            continue
        seen[h] = seen.get(h, 0) + 1
        kept.append(r)
    return kept[:top] if top else kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--handle", default=None, help="只看某个账号")
    ap.add_argument("--detail", action="store_true", help="打印分数构成")
    args = ap.parse_args()

    cfg = load_config()
    raw = sorted((ROOT / "data" / "raw").glob("*.json"))
    if not raw:
        print("data/raw 里没有候选，先跑 fetch.py", file=sys.stderr)
        return 1
    rows = json.loads(raw[-1].read_text(encoding="utf-8"))
    if args.handle:
        rows = [r for r in rows if (r.get("handle") or "").lower() == args.handle.lower()]

    kept = rank(rows, cfg, args.top)
    for i, r in enumerate(kept, 1):
        text = r["text"].replace("\n", " ")
        print(f"[{i:>2}] {r['score']:>6.2f}  @{r['handle']:<18} {len(r['text']):>4}字  {text[:90]}…")
        if args.detail:
            parts = " ".join(f"{k}={v:+g}" for k, v in r["parts"].items() if v)
            print(f"       {parts}")
        print(f"       {r['url']}")
    print(f"\n— 粗筛 {len(kept)} / 全量 {len(rows)}（阈值 {cfg['scoring'].get('min_score', 3.0)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
