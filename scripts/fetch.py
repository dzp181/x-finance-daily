#!/usr/bin/env python3
"""从 x2ding 的 ntfy 主题拉取推文，落成本地 JSON。

不登录 X、不走任何私有接口：只读你自己的 ntfy 订阅。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CST = timezone(timedelta(hours=8))

# 推文正文尾部会带 "\n\n— 11-18 01:05 CST"，抓取时去掉
TAIL_RE = re.compile(r"\n*—\s*\d{1,2}-\d{1,2}\s+\d{2}:\d{2}\s+CST\s*$")


def load_config() -> dict:
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def http_get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "x-finance-daily/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_watchlist(text: str) -> list[dict]:
    """watchlist.txt: `handle topic   # 说明`；`#!` 开头表示停用。"""
    rows = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        disabled = False
        body, _, note = raw.partition("#")
        if raw.startswith("#!"):
            disabled = True
            body = raw[2:]
        parts = body.split()
        if not parts:
            continue
        handle = parts[0]
        topic = parts[1] if len(parts) > 1 else "xtweet-9f4bq2wp"
        if disabled:
            continue
        rows.append({"handle": handle, "topic": topic, "note": note.strip()})
    return rows


def fetch_topic(base: str, topic: str, since: str) -> list[dict]:
    url = f"{base}/{urllib.parse.quote(topic)}/json?poll=1&since={urllib.parse.quote(since)}"
    try:
        body = http_get(url)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"  ! {topic}: {exc}", file=sys.stderr)
        return []

    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("event") != "message":
            continue
        text = TAIL_RE.sub("", msg.get("message") or "").strip()
        click = msg.get("click") or ""
        if not text:
            continue
        out.append(
            {
                "id": msg.get("id"),
                "time": msg.get("time"),
                "topic": topic,
                "title": msg.get("title") or "",
                "text": text,
                "url": click,
                "image": (msg.get("attachment") or {}).get("url"),
            }
        )
    return out


def handle_from_title(title: str) -> str:
    m = re.search(r"\(@([A-Za-z0-9_]+)\)", title)
    return m.group(1) if m else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="ntfy since 参数，默认读 config")
    ap.add_argument("--offline", action="store_true", help="只解析本地缓存，不联网")
    args = ap.parse_args()

    cfg = load_config()
    since = args.since or cfg["source"]["since"]
    base = cfg["source"]["ntfy_base"]

    if args.offline:
        cache = sorted((ROOT / "data" / "raw").glob("*.json"))[-1]
        rows = json.loads(cache.read_text(encoding="utf-8"))
    else:
        try:
            watchlist = parse_watchlist(http_get(cfg["source"]["watchlist_raw"]))
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"watchlist 拉取失败，用本地缓存: {exc}", file=sys.stderr)
            watchlist = json.loads((ROOT / "data" / "watchlist.json").read_text("utf-8"))

        (ROOT / "data" / "watchlist.json").write_text(
            json.dumps(watchlist, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"监控账号 {len(watchlist)} 个，窗口 {since}")

        rows, seen = [], set()
        for w in watchlist:
            items = fetch_topic(base, w["topic"], since)
            for it in items:
                it["handle"] = w["handle"]
                it["account_note"] = w["note"]
                key = it["url"] or hashlib.sha1(it["text"].encode()).hexdigest()
                if key in seen:
                    continue
                seen.add(key)
                rows.append(it)
            print(f"  {w['handle']:<18} {len(items):>3} 条")

    rows.sort(key=lambda r: r.get("time") or 0, reverse=True)
    stamp = datetime.now(CST).strftime("%Y-%m-%dT%H%M")
    out_path = ROOT / "data" / "raw" / f"{stamp}.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    # 追加进总库，按 url 去重
    ledger = ROOT / "data" / "tweets.jsonl"
    known = set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                known.add(json.loads(line).get("url"))
            except json.JSONDecodeError:
                pass
    with open(ledger, "a", encoding="utf-8") as f:
        for r in rows:
            if r["url"] not in known:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                known.add(r["url"])

    print(f"\n本轮 {len(rows)} 条 → {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
