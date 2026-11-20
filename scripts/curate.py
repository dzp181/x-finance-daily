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

v3 加的是「受众契合」这一维（见 FIT_* 词表）：
  频道发小红书和视频号，看的人是 30+、有自己见解、不缺信息也不缺观点。
  他们不缺「发生了什么」，缺的是「这桩交易的结构是什么、这笔账怎么算、
  跟我有什么关系」。所以：

  加：deal（能被拆开的交易结构）、calc（数字之间能互相推导）、
      first_hand（公司/监管自己说的，不是二传）、contrast（同一消息两种反应）、
      mapping（能落到持仓/汇率/生活）
  减：opinion（抄底、目标价、必涨——替观众做决定，是频道红线）、
      broadcast（只有涨跌幅和点位，没有结构，观众自己开行情软件就有）、
      stale（>36 小时且没有结果词，说明还是事件前的推演）

  boost 系数同时从 2.4 降到 1.9、封顶从 9 降到 6.5——术语密度不再是主导项。
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

# ---- v3 受众契合词表 ----------------------------------------------------
# 交易结构：一条内容能被「拆开」的标志。有这些词，说明里面有别人不知道的细节。
DEAL_WORDS = [
    "招股书", "定价", "发行价", "发行规模", "配售", "认购", "超额认购", "解禁",
    "锁定期", "锁定", "并购", "要约", "回购", "增持", "减持", "大宗交易",
    "调仓", "纳入", "权重", "换股", "拆分", "承销", "募资", "流通盘", "老股",
]
# 算账关系：两个数字之间能互相推出来，而不是各自独立。
CALC_WORDS = [
    "对应", "折算", "相当于", "合计", "共计", "换算", "摊薄", "扣除", "净额",
    "成本", "占比", "市销率", "市盈率", "倍", "等于", "合计约", "口径",
]
# 钱包映射：能不能落到观众的持仓、汇率、房贷、日常开销上。
MAPPING_WORDS = [
    "A股", "a股", "港股", "沪指", "上证", "深成指", "创业板", "科创50", "北向",
    "人民币", "汇率", "美元指数", "房贷", "存款", "理财", "黄金", "油价", "通胀",
    "关税", "出口", "映射", "传导", "国内", "老百姓", "消费", "工资", "社保",
]
# 一手信源：公司、监管、交易所自己说的，不是二手解读。
FIRSTHAND_WORDS = [
    "电话会", "业绩会", "纪要", "招股书", "13F", "财报", "指引", "公告",
    "官方", "原文", "披露", "答问", "问答", "声明", "路演", "调研",
]
# 背离：同一条消息，两个资产反应不同——这是「见解型」内容最好的入口。
CONTRAST_WORDS = ["反而", "却", "只有", "唯独", "分化", "背离", "一边", "同时"]
ASSET_WORDS = [
    "黄金", "白银", "原油", "美元", "美债", "收益率", "纳指", "标普", "道指",
    "A股", "港股", "比特币", "铜", "半导体", "银行", "地产",
]
# 红线：替观众做决定。这个人群最反感的就是被人指挥。
OPINION_WORDS = [
    "抄底", "梭哈", "目标价", "必涨", "必跌", "稳赚", "我建议", "建议买入",
    "建议卖出", "加仓", "减仓", "止盈", "止损", "满仓", "清仓建议", "闭眼买",
]
# 盘面播报形态：只有价格动作，没有结构。
BROADCAST_RE = re.compile(
    r"(?:涨|跌)[\d.]+\s*[%％]|收[报于盘]|报[\d.]+\s*点|成交额|成交[\d.]+\s*亿|"
    r"涨跌幅|涨停|跌停|创[新近][高低]"
)
# 结果词：事件已经落地的标志，用来区分「事件前推演」和「事件结果」。
RESULT_WORDS = [
    "最终", "收盘", "收报", "确认", "官宣", "公告", "完成", "落地", "已上市",
    "首日", "开盘报", "定于", "敲定",
]
# 清单体：一篇里并列好几件不相关的事，是素材库不是单集选题。
# 注意：只用「条目数」判会误伤深度长文（一个主体、分点论证），
# 所以要求栏目词或条目数特别多，两者取一。
LIST_RE = re.compile(r"[（(][一二三四五六七八九十][）)]|[一二三四五六七八九十]、|\d{1,2}\s*、")
LIST_MARK_WORDS = [
    "热点", "二十条", "十条", "盘点", "汇总", "速览", "早报", "晚报",
    "一览", "必读", "消息面", "今日判断", "复盘", "快照", "财经日历", "要闻",
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

    # ---- 加分词：sqrt 衰减 + 封顶，堆词无效（v3 把系数和封顶都调低了）
    hits = [k for k in sc["boost_keywords"] if k in low]
    cap = float(sc.get("boost_cap", 6.5))
    coef = float(sc.get("boost_coef", 1.9))
    parts["boost"] = round(min(cap, coef * math.sqrt(len(hits))), 2)

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

    # ---- v3 受众契合：拆得开 / 算得清 / 跟他有关
    fw = sc.get("fit", {})
    deal_hits = [k for k in DEAL_WORDS if k in text]
    parts["deal"] = round(
        min(float(fw.get("deal_cap", 4.0)), float(fw.get("deal", 0.85)) * len(deal_hits)), 2
    )

    calc_hits = [k for k in CALC_WORDS if k in text]
    calc_w = float(fw.get("calc", 2.6))
    if len(nums) >= 3 and len(calc_hits) >= 1:
        parts["calc"] = calc_w
    elif len(nums) >= 2 and len(calc_hits) >= 2:
        parts["calc"] = round(calc_w * 0.6, 2)
    else:
        parts["calc"] = 0.0

    mapping_hits = [k for k in MAPPING_WORDS if k in text]
    parts["mapping"] = round(
        min(float(fw.get("mapping_cap", 2.2)), float(fw.get("mapping", 0.7)) * len(mapping_hits)), 2
    )

    fh_hits = [k for k in FIRSTHAND_WORDS if k in text]
    parts["first_hand"] = round(
        min(float(fw.get("first_hand_cap", 2.0)), float(fw.get("first_hand", 0.6)) * len(fh_hits)), 2
    )

    contrast_hits = [k for k in CONTRAST_WORDS if k in text]
    assets = sorted({a for a in ASSET_WORDS if a in text})
    parts["contrast"] = float(fw.get("contrast", 1.5)) if (contrast_hits and len(assets) >= 2) else 0.0

    opinion_hits = [k for k in OPINION_WORDS if k in text]
    parts["opinion"] = round(
        max(float(fw.get("opinion_cap", -6.0)), float(fw.get("opinion", -3.0)) * len(opinion_hits)), 2
    )

    # 盘面播报：有价格动作，却没有结构、没有算账、没有一手信源
    price_hits = len(BROADCAST_RE.findall(text))
    if price_hits >= 2 and not deal_hits and not fh_hits and parts["calc"] <= 0:
        parts["broadcast"] = float(fw.get("broadcast", -4.0))
    else:
        parts["broadcast"] = 0.0

    # 清单体：并列好几件事的长文，适合当素材库，不适合当单集选题
    list_marks = len(LIST_RE.findall(text))
    list_words = [k for k in LIST_MARK_WORDS if k in text]
    if list_marks >= 3 and n > 500 and (list_words or list_marks >= 8):
        parts["listing"] = float(fw.get("listing", -2.5))
    else:
        parts["listing"] = 0.0

    # ---- 时效：48 小时窗口里，越新越像新闻
    age = _age_hours(item)
    if age <= 4:
        parts["recency"] = 2.0
    elif age <= 10:
        parts["recency"] = 1.2
    elif age <= 24:
        parts["recency"] = 0.6
    elif age <= 36:
        parts["recency"] = 0.2
    else:
        parts["recency"] = 0.0

    # 事件已经走完还停留在「如果……就会……」的推演，对观众没有增量
    if age > 36 and not any(k in text for k in RESULT_WORDS):
        parts["stale"] = float(fw.get("stale", -1.0))
    else:
        parts["stale"] = 0.0

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

    fit_keys = ("deal", "calc", "mapping", "first_hand", "contrast",
                "opinion", "broadcast", "listing")
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
            "deal_hits": deal_hits,
            "calc_hits": calc_hits,
            "mapping_hits": mapping_hits,
            "first_hand_hits": fh_hits,
            "contrast_hits": contrast_hits,
            "assets": assets,
            "opinion_hits": opinion_hits,
            "price_hits": price_hits,
            "fit": round(sum(parts[k] for k in fit_keys), 2),
        },
    }


def score(item: dict, cfg: dict, account_volume: dict | None = None) -> float:
    return score_detail(item, cfg, account_volume)["score"]


# 内容形态：决定它在选题配比里占什么位置，不是质量高低
KINDS = ("拆账", "一手", "背离", "算账", "映射", "机制", "清单", "盘面", "观点")


def kind_of(parts: dict) -> str:
    """看分数构成判断形态。拆账/一手/背离优先，盘面和观点直接标出来。"""
    if parts.get("broadcast", 0) < 0:
        return "盘面"
    if parts.get("listing", 0) < 0:
        return "清单"
    if parts.get("opinion", 0) < 0:
        return "观点"
    if parts.get("deal", 0) >= 1.7:
        return "拆账"
    if parts.get("first_hand", 0) >= 1.2:
        return "一手"
    if parts.get("contrast", 0) > 0:
        return "背离"
    if parts.get("calc", 0) > 0:
        return "算账"
    if parts.get("mapping", 0) >= 0.7:
        return "映射"
    return "机制"


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
