#!/usr/bin/env python3
"""把粗筛后的推文按「事件」聚成簇，输出一份可直接阅读的选题清单。

为什么需要这一步：同一个事件（比如某家公司财报）会有七八个账号同时发，
如果按单条推文排序，前十二名可能只覆盖两三个事件。聚类之后，
每个事件只留一条代表，选题是在「事件之间」比较，不是在「文本之间」比较。

相似度 = 0.5 × 字符二元组 Jaccard + 0.5 × 主题词 Jaccard，
合并条件同时要求：综合相似度 ≥ 0.34，且共享至少 2 个主题词。

输出：
  data/shortlist/<日期>-<档>.json   给程序读
  data/shortlist/<日期>-<档>.md     给人/模型读（带数字、覆盖账号、是否做过）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
CST = timezone(timedelta(hours=8))
OUT_DIR = ROOT / "data" / "shortlist"

sys.path.insert(0, str(SCRIPTS))

URL_RE = re.compile(r"https?://\S+")
TICKER_RE = re.compile(r"\$[A-Za-z]{1,6}")
PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)

# 主体词表：用来提示「第 2 个和第 3 个其实是同一件事」，避免选题重复
ENTITY_WORDS = [
    "Anthropic", "OpenAI", "英伟达", "台积电", "腾讯", "阿里巴巴", "阿里", "沃尔玛",
    "谷歌", "微软", "亚马逊", "特斯拉", "苹果", "Meta", "博通", "美光", "三星",
    "海力士", "SpaceX", "币安", "德鲁肯米勒", "巴菲特", "美联储", "日本央行",
    "比特币", "以太坊", "黄金", "白银", "原油", "铜", "科创板", "上证", "恒生",
]


def entities_of(text: str) -> list[str]:
    """主体名 + 美元代码，按出现次数排序，取前两个。"""
    hits = [w for w in ENTITY_WORDS if w in text]
    hits.sort(key=lambda w: -text.count(w))
    tick = [t for t in TICKER_RE.findall(text)]
    return hits[:2] + tick[:2]


def norm(text: str) -> str:
    t = URL_RE.sub(" ", text.lower())
    t = PUNCT_RE.sub("", t)
    return t


def bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


def topic_set(item: dict, boost_words: list[str]) -> set[str]:
    """主题签名：命中的行业/概念词 + 美元代码。"""
    low = (item.get("text") or "").lower()
    kw = {w for w in boost_words if w in low}
    kw |= {t.lower() for t in TICKER_RE.findall(item.get("text") or "")}
    return kw


class DSU:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def cluster_rows(rows: list[dict], cfg: dict) -> list[list[dict]]:
    """按词面相似度合并「近乎同文」的重复。

    实测结论：词面相似只能可靠识别跨账号的转载/改写重复（相似度 ≥0.34），
    再往下调就会把「都提到财报、英伟达」的不相关事件合成一个巨簇。
    不同账号对同一事件的独立评论，交给下一步的模型判断去识别。
    """
    boost_words = cfg["scoring"]["boost_keywords"]
    thr = float(cfg["scoring"].get("cluster_similarity", 0.34))
    min_shared = int(cfg["scoring"].get("cluster_min_shared", 2))

    grams = [bigrams(norm(r["text"])) for r in rows]
    topics = [topic_set(r, boost_words) for r in rows]

    dsu = DSU(len(rows))
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if len(topics[i] & topics[j]) < min_shared:
                continue
            sim = 0.5 * jaccard(grams[i], grams[j]) + 0.5 * jaccard(topics[i], topics[j])
            if sim >= thr:
                dsu.union(i, j)

    groups: dict[int, list[dict]] = {}
    for idx, r in enumerate(rows):
        groups.setdefault(dsu.find(idx), []).append(r)
    return list(groups.values())


def label_of(rep: dict, k: int = 3) -> str:
    hits = rep.get("signals", {}).get("boost_hits") or []
    head = (rep.get("text") or "").replace("\n", " ")[:18]
    tag = "·".join(hits[:k]) if hits else "未命中主题词"
    return f"{tag}｜{head}"


def rank_clusters(rows: list[dict], cfg: dict, used: set[str] | None = None) -> list[dict]:
    """rows 需已带 score / signals / parts（curate.rank 的产物）。"""
    import curate  # noqa: PLC0415

    used = used or set()
    kind_bonus = cfg["scoring"].get("kind_bonus", {})
    out = []
    for members in cluster_rows(rows, cfg):
        members.sort(key=lambda r: r["score"], reverse=True)
        rep = members[0]
        accounts = sorted({m.get("handle") or "" for m in members})
        coverage = len(accounts)
        bonus = 0.8 * min(coverage - 1, 4) + 0.4 * math.log2(1 + len(members))
        done = any((m.get("url") or "") in used for m in members)
        kind = curate.kind_of(rep.get("parts") or {})
        # 簇内最新一条：代表推文可能是事件前的推演，最新那条才决定事件现在处在哪一步
        newest = min(members, key=lambda m: m.get("signals", {}).get("age_hours") or 999)
        out.append(
            {
                "id": hashlib.sha1((rep.get("url") or rep["text"]).encode()).hexdigest()[:8],
                "label": label_of(rep),
                "kind": kind,
                "entities": entities_of(rep.get("text") or ""),
                "score": round(rep["score"] + bonus + float(kind_bonus.get(kind, 0.0)), 2),
                "rep_score": rep["score"],
                "coverage": coverage,
                "accounts": accounts,
                "members": len(members),
                "done": done,
                "numbers": rep.get("signals", {}).get("numbers", []),
                "mech_hits": rep.get("signals", {}).get("mech_hits", []),
                "age_hours": rep.get("signals", {}).get("age_hours"),
                "latest_age": newest.get("signals", {}).get("age_hours"),
                "latest_handle": newest.get("handle"),
                "latest_url": newest.get("url"),
                "fit": rep.get("signals", {}).get("fit"),
                "rep": {
                    "handle": rep.get("handle"),
                    "url": rep.get("url"),
                    "text": rep.get("text"),
                    "score": rep.get("score"),
                    "parts": rep.get("parts"),
                },
                "others": [
                    {"handle": m.get("handle"), "url": m.get("url"), "score": m.get("score"),
                     "text": m.get("text")}
                    for m in members[1:6]
                ],
            }
        )
    out.sort(key=lambda c: (c["done"], -c["score"]))

    # 同一主体降权：同一件事换个账号写，不该在清单里占两个位置
    seen_ent: dict[str, str] = {}
    for c in out:
        dup = next((e for e in c["entities"] if e in seen_ent), None)
        if dup:
            c["score"] = round(c["score"] - 1.2, 2)
            c["same_as"] = seen_ent[dup]
        else:
            if c["entities"]:
                seen_ent[c["entities"][0]] = c["rep"]["url"]
    out.sort(key=lambda c: (c["done"], -c["score"]))

    # 账号多样性：前 scope 名里，同一账号超过 cap 个就扣分下移。
    # 用扣分而不是跳过，是为了让清单的分数始终单调递减，读起来不像出错。
    cap = int(cfg["scoring"].get("per_account_clusters", 3))
    scope = int(cfg["scoring"].get("cap_scope", 8))
    pen = float(cfg["scoring"].get("cap_penalty", 2.0))
    for _ in range(2):
        out.sort(key=lambda c: (c["done"], -c["score"]))
        seen: dict[str, int] = {}
        for c in out[:scope]:
            h = c["rep"].get("handle") or ""
            if seen.get(h, 0) >= cap and not c.get("capped"):
                c["score"] = round(c["score"] - pen, 2)
                c["capped"] = True
            else:
                seen[h] = seen.get(h, 0) + 1
    out.sort(key=lambda c: (c["done"], -c["score"]))
    return out


def render_md(clusters: list[dict], meta: dict) -> str:
    lines = [
        f"# 选题清单 · {meta['stamp']}（{meta['slot']}）",
        "",
        f"> 窗口 {meta['window']}｜原始 {meta['raw']} 条 → 粗筛 {meta['pool']} 条 → 事件 {meta.get('total', len(clusters))} 个"
        f"（列出前 {len(clusters)}）。",
        "> `形态` 是内容类型（拆账/一手/背离/算账/映射/机制/盘面/观点），`最新` 是簇内最新一条距今多久——",
        "> 代表推文可能写于事件发生前，建稿前先看最新那条，事件走完了就换角度。",
        "",
    ]
    for i, c in enumerate(clusters, 1):
        flag = " · 已做" if c["done"] else ""
        if c.get("same_as"):
            idx = next((j for j, x in enumerate(clusters, 1) if x["rep"]["url"] == c["same_as"]), None)
            flag += f" · 同一主体，另见第 {idx} 个" if idx else " · 同一主体（已在前列）"
        age = f"{c['age_hours']:.0f}h 前" if isinstance(c["age_hours"], (int, float)) else "?"
        latest = f"{c['latest_age']:.0f}h 前" if isinstance(c["latest_age"], (int, float)) else "?"
        lines.append(
            f"## {i}. [{c['score']}] {c.get('kind', '?')}｜{c['label']}{flag}"
        )
        lines.append(
            f"- @{c['rep']['handle']}｜{age}｜覆盖 {c['coverage']} 个账号｜簇内 {c['members']} 条｜"
            f"最新 {latest} @{c.get('latest_handle') or '?'}"
        )
        lines.append(f"- 机制词：{'、'.join(c['mech_hits']) or '无'}｜受众契合 {c.get('fit')}")
        if c["numbers"]:
            lines.append(f"- 数字：{'、'.join(c['numbers'][:8])}")
        lines.append(f"- 链接：{c['rep']['url']}")
        if c.get("latest_url") and c["latest_url"] != c["rep"]["url"]:
            lines.append(f"- 最新一条：{c['latest_url']}")
        body = (c["rep"]["text"] or "").replace("\n", " ")
        lines.append("")
        lines.append("  " + body[:400] + ("…" if len(body) > 400 else ""))
        if c["others"]:
            lines.append("")
            lines.append("  同事件其它来源：" + "、".join("@" + o["handle"] for o in c["others"]))
        lines.append("")
    return "\n".join(lines) + "\n"


def build(cfg: dict, rows: list[dict], used: set[str] | None = None) -> list[dict]:
    import curate  # noqa: PLC0415

    pool = curate.rank(rows, cfg)
    return rank_clusters(pool, cfg, used)


def main() -> int:
    ap = argparse.ArgumentParser(description="把候选聚成事件簇，输出选题清单")
    ap.add_argument("--top", type=int, default=12, help="输出前几个事件簇")
    ap.add_argument("--slot", default=None, help="am/pm，默认按当前时间")
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    args = ap.parse_args()

    import curate  # noqa: PLC0415

    cfg = curate.load_config()
    raw_files = sorted((ROOT / "data" / "raw").glob("*.json"))
    if not raw_files:
        print("data/raw 里没有候选，先跑 fetch.py", file=sys.stderr)
        return 1
    rows = json.loads(raw_files[-1].read_text(encoding="utf-8"))

    used: set[str] = set()
    try:
        import run  # noqa: PLC0415

        used = run.used_seeds()
    except Exception:  # noqa: BLE001
        pass

    all_clusters = build(cfg, rows, used)
    clusters = all_clusters[: args.top]
    now = datetime.now(CST)
    meta = {
        "stamp": now.strftime("%Y-%m-%d %H:%M"),
        "slot": args.slot or ("am" if now.hour < 12 else "pm"),
        "window": cfg["source"]["since"],
        "raw": len(rows),
        "pool": len(curate.rank(rows, cfg)),
        "total": len(all_clusters),
    }

    if args.json:
        print(json.dumps({"meta": meta, "clusters": clusters}, ensure_ascii=False, indent=2))
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{now.strftime('%Y-%m-%d')}-{meta['slot']}"
    (OUT_DIR / f"{stem}.json").write_text(
        json.dumps({"meta": meta, "clusters": clusters}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / f"{stem}.md").write_text(render_md(clusters, meta), encoding="utf-8")

    print(f"原始 {meta['raw']} → 粗筛 {meta['pool']} → 事件 {len(all_clusters)} 个（列出前 {len(clusters)}）")
    print(f"清单：data/shortlist/{stem}.md\n")
    for i, c in enumerate(clusters, 1):
        flag = " 已做" if c["done"] else ""
        if c.get("same_as"):
            flag += " 同主体"
        latest = f"{c['latest_age']:.0f}h" if isinstance(c.get("latest_age"), (int, float)) else "?"
        print(f"[{i:>2}] {c['score']:>6.2f}  {c.get('kind', ''):<2} 覆盖{c['coverage']}×{c['members']:<2}  "
              f"{c['age_hours']:.0f}h→最新{latest}{flag}")
        print(f"     {c['label'][:70]}")
    print(f"\n选第 N 个建草稿：python3 scripts/run.py new --cluster N")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
