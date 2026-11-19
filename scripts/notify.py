#!/usr/bin/env python3
"""把一期成片推到微信。**当前未接入定时任务**，只在需要时手工跑（见 README「推送到微信（暂缓）」）。

保留原因：产线全部跑在本机，电脑不开机时出不了片，推送通道解决不了「随时能收」的问题；
真要脱离本机得先把产线搬上常开服务器。所以这个脚本先放着，等产线云端化之后再接进自动化。

两种通道，按配置自动选择（config.json 的 notify 段，或环境变量）：

1) 群机器人 webhook（最省事，文字 + 封面图）
   config: {"notify": {"wecom": {"webhook_key": "..."}}}
   env:    WECOM_WEBHOOK_KEY
   限制：群机器人只支持 text / markdown / image(≤2MB) / news / file(需 media_id)，
         发不了本地视频文件。

2) 自建应用（文字 + 视频，能真把 mp4 推过去）
   config: {"notify": {"wecom": {"corp_id": "...", "agent_id": "...", "secret": "...",
                                  "to_user": "@all"}}}
   env:    WECOM_CORP_ID / WECOM_AGENT_ID / WECOM_SECRET / WECOM_TO_USER
   限制：临时素材 video ≤10MB 且仅 mp4，所以自动挑体积合格的归档副本。

用法：
    python3 scripts/notify.py                     # 最新一期，能推什么推什么
    python3 scripts/notify.py 2026-11-18-pm
    python3 scripts/notify.py --dry-run           # 只打印将要发送的内容
    python3 scripts/notify.py --only text          # text|image|video|all
    python3 scripts/notify.py --test               # 发一条连通性测试

设计原则：失败只报错不中断产线（退出码非 0 但打印明确原因），
绝不在推送环节修改文案、码率或音轨。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "episodes"
API = "https://qyapi.weixin.qq.com/cgi-bin"
VIDEO_LIMIT_MB = 10.0      # 临时素材 video 上限
IMAGE_LIMIT_MB = 2.0       # 群机器人图片上限（base64 前）
TEXT_LIMIT_BYTES = 2048    # 群机器人 text.content 上限（utf-8 字节）


# ---------------------------------------------------------------- 配置

def load_cfg() -> dict:
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def wecom_conf() -> dict:
    cfg = load_cfg().get("notify", {}).get("wecom", {}) or {}
    return {
        "webhook_key": os.environ.get("WECOM_WEBHOOK_KEY") or cfg.get("webhook_key", ""),
        "corp_id": os.environ.get("WECOM_CORP_ID") or cfg.get("corp_id", ""),
        "agent_id": os.environ.get("WECOM_AGENT_ID") or cfg.get("agent_id", ""),
        "secret": os.environ.get("WECOM_SECRET") or cfg.get("secret", ""),
        "to_user": os.environ.get("WECOM_TO_USER") or cfg.get("to_user", "@all"),
    }


def has_webhook(c: dict) -> bool:
    return bool(c["webhook_key"])


def has_app(c: dict) -> bool:
    return bool(c["corp_id"] and c["secret"] and c["agent_id"])


# ---------------------------------------------------------------- 取料

def find_episode(arg: str | None) -> Path | None:
    if arg:
        p = Path(arg)
        if not p.is_absolute():
            p = EPISODES / arg
        return p if (p / "episode.json").exists() else None
    dirs = [d for d in EPISODES.glob("*") if (d / "episode.json").exists()]
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None


def parse_caption(ep_dir: Path) -> dict:
    """从 caption.md 抠出 标题 / 描述 / 话题；没有就退回 episode.json。"""
    out = {"title": "", "description": "", "tags": ""}
    f = ep_dir / "caption.md"
    if f.exists():
        cur = None
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("## "):
                cur = s[3:].strip()
                continue
            if not s or cur is None:
                continue
            if cur == "标题" and not out["title"]:
                out["title"] = s
            elif cur == "描述":
                out["description"] = (out["description"] + "\n" + line).strip("\n")
            elif cur == "话题" and not out["tags"]:
                out["tags"] = s
    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    out["title"] = out["title"] or ep.get("title", "")
    out["description"] = out["description"] or ep.get("description", "")
    out["seconds"] = ep.get("seconds")
    out["scenes"] = len(ep.get("scenes", []))
    return out


def pick_video(ep_dir: Path) -> Path | None:
    """优先归档副本（体积小、带语音），原片超 10MB 时不能用临时素材。"""
    cands = [
        ep_dir / "video_归档.mp4",
        ROOT / "outbox" / ep_dir.name / "video_归档.mp4",
        ep_dir / "video.mp4",
    ]
    for p in cands:
        if p.exists() and p.stat().st_size / 1024 / 1024 <= VIDEO_LIMIT_MB:
            return p
    return None


def pick_cover(ep_dir: Path) -> Path | None:
    for p in [ep_dir / "cover_wecom.jpg", ROOT / "outbox" / ep_dir.name / "cover.jpg",
              ep_dir / "cover.png"]:
        if p.exists() and p.stat().st_size / 1024 / 1024 <= IMAGE_LIMIT_MB:
            return p
    return None


# ---------------------------------------------------------------- 文本

def build_text(info: dict, ep_dir: Path) -> str:
    """群机器人 text 通道：标题 + 描述 + 话题。超长就截描述。"""
    body = info["description"].strip()
    head = f"【{info['title']}】"
    tail = info.get("tags", "")
    secs = info.get("seconds")
    meta = f"{secs}s · {info['scenes']} 句" if secs else ""

    def compose(b: str) -> str:
        parts = [head]
        if meta:
            parts.append(meta)
        parts.append(b)
        if tail:
            parts.append(tail)
        return "\n\n".join([p for p in parts if p])

    text = compose(body)
    while len(text.encode("utf-8")) > TEXT_LIMIT_BYTES and len(body) > 120:
        body = body[: int(len(body) * 0.85)].rstrip() + "…"
        text = compose(body)
    return text


def build_markdown(info: dict, ep_dir: Path) -> str:
    """自建应用 markdown 通道，保留多段落。"""
    desc = info["description"].strip()
    return (
        f"## {info['title']}\n"
        f"> {info.get('seconds', '?')}s · {info['scenes']} 句\n\n"
        f"{desc}\n\n"
        f"<font color=\"comment\">{info.get('tags', '')}</font>"
    )


# ---------------------------------------------------------------- HTTP

def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {"errcode": exc.code, "errmsg": exc.read().decode("utf-8", "replace")[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"errcode": -1, "errmsg": str(exc)}


def get_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return {"errcode": -1, "errmsg": str(exc)}


def multipart(field: str, path: Path) -> tuple[bytes, str]:
    boundary = "----wb" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode()
    body += path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def post_multipart(url: str, path: Path) -> dict:
    body, ctype = multipart("media", path)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {"errcode": exc.code, "errmsg": exc.read().decode("utf-8", "replace")[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"errcode": -1, "errmsg": str(exc)}


# ---------------------------------------------------------------- 通道

def send_webhook_text(key: str, text: str) -> dict:
    url = f"{API}/webhook/send?key={urllib.parse.quote(key)}"
    return post_json(url, {"msgtype": "text", "text": {"content": text}})


def send_webhook_image(key: str, path: Path) -> dict:
    raw = path.read_bytes()
    if len(raw) / 1024 / 1024 > IMAGE_LIMIT_MB:
        return {"errcode": -2, "errmsg": f"图片 {len(raw)/1048576:.1f}MB 超 2MB"}
    url = f"{API}/webhook/send?key={urllib.parse.quote(key)}"
    return post_json(url, {
        "msgtype": "image",
        "image": {"base64": base64.b64encode(raw).decode(),
                  "md5": hashlib.md5(raw).hexdigest()},
    })


def get_token(c: dict) -> tuple[str, dict]:
    r = get_json(f"{API}/gettoken?corpid={urllib.parse.quote(c['corp_id'])}"
                 f"&corpsecret={urllib.parse.quote(c['secret'])}")
    if r.get("errcode") == 0:
        return r.get("access_token", ""), r
    return "", r


def upload_media(token: str, path: Path, mtype: str) -> tuple[str, dict]:
    r = post_multipart(f"{API}/media/upload?access_token={token}&type={mtype}", path)
    if r.get("errcode", 0) == 0 and r.get("media_id"):
        return r["media_id"], r
    return "", r


def send_app(token: str, c: dict, msgtype: str, body: dict) -> dict:
    payload = {"touser": c["to_user"], "msgtype": msgtype, "agentid": int(c["agent_id"])}
    payload[msgtype] = body
    return post_json(f"{API}/message/send?access_token={token}", payload)


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="把一期成片推到微信")
    ap.add_argument("episode", nargs="?", default=None)
    ap.add_argument("--only", default="all", choices=["all", "text", "image", "video"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test", action="store_true", help="只发一条连通性测试")
    args = ap.parse_args()

    c = wecom_conf()
    if not has_webhook(c) and not has_app(c):
        print("没配微信通道。二选一填进 config.json 的 notify.wecom：")
        print('  群机器人：{"webhook_key": "企业微信群机器人 key"}')
        print('  自建应用：{"corp_id": "...", "agent_id": "...", "secret": "...", "to_user": "@all"}')
        print("或设环境变量 WECOM_WEBHOOK_KEY / WECOM_CORP_ID + WECOM_AGENT_ID + WECOM_SECRET")
        return 2

    if args.test:
        txt = "【连通测试】超级DOGE 金融一分钟 · 推送通道正常"
        if args.dry_run:
            print("dry-run 跳过发送")
            return 0
        if has_webhook(c):
            r = send_webhook_text(c["webhook_key"], txt)
            print("webhook:", r)
            return 0 if r.get("errcode") == 0 else 1
        token, tr = get_token(c)
        if not token:
            print("取 token 失败:", tr)
            return 1
        r = send_app(token, c, "text", {"content": txt})
        print("app:", r)
        return 0 if r.get("errcode") == 0 else 1

    ep_dir = find_episode(args.episode)
    if not ep_dir:
        print("找不到这一期的 episode.json")
        return 1
    info = parse_caption(ep_dir)
    video = pick_video(ep_dir)
    cover = pick_cover(ep_dir)

    print(f"期：{ep_dir.name}")
    print(f"标题：{info['title']}")
    print(f"视频：{video.name if video else '无合格文件（超 10MB 或缺失）'}")
    print(f"封面：{cover.name if cover else '无合格文件（超 2MB 或缺失）'}")
    print(f"通道：webhook={'有' if has_webhook(c) else '无'} / app={'有' if has_app(c) else '无'}")

    results: list[tuple[str, dict]] = []

    # 文字：webhook 走 text，app 走 markdown（都可）
    if args.only in ("all", "text"):
        if has_webhook(c):
            txt = build_text(info, ep_dir)
            if args.dry_run:
                print("\n--- 将发送的 text ---\n" + txt)
                results.append(("text", {"errcode": 0, "dry_run": True}))
            else:
                results.append(("text", send_webhook_text(c["webhook_key"], txt)))
        elif has_app(c):
            token, tr = get_token(c)
            if not token:
                results.append(("text", tr))
            else:
                md = build_markdown(info, ep_dir)
                if args.dry_run:
                    print("\n--- 将发送的 markdown ---\n" + md)
                    results.append(("text", {"errcode": 0, "dry_run": True}))
                else:
                    results.append(("text", send_app(token, c, "markdown", {"content": md})))

    # 封面：只有 webhook 能发图；app 走 image 媒体
    if args.only in ("all", "image") and cover:
        if has_webhook(c):
            if args.dry_run:
                results.append(("image", {"errcode": 0, "dry_run": True}))
            else:
                results.append(("image", send_webhook_image(c["webhook_key"], cover)))
        elif has_app(c):
            token, _ = get_token(c)
            mid, ur = upload_media(token, cover, "image") if token else ("", {"errcode": -1})
            if mid and not args.dry_run:
                results.append(("image", send_app(token, c, "image", {"media_id": mid})))
            else:
                results.append(("image", ur))

    # 视频：只有自建应用能发文件
    if args.only in ("all", "video"):
        if not video:
            results.append(("video", {"errcode": -3, "errmsg": "没有 ≤10MB 的 mp4 可用"}))
        elif not has_app(c):
            results.append(("video", {"errcode": -4,
                                      "errmsg": "群机器人不支持发视频，需配自建应用"}))
        elif args.dry_run:
            results.append(("video", {"errcode": 0, "dry_run": True}))
        else:
            token, tr = get_token(c)
            if not token:
                results.append(("video", tr))
            else:
                mid, ur = upload_media(token, video, "video")
                if not mid:
                    results.append(("video", ur))
                else:
                    results.append(("video", send_app(token, c, "video", {
                        "media_id": mid,
                        "title": info["title"][:64],
                        "description": (info.get("tags") or "")[:120],
                    })))

    print("\n--- 结果 ---")
    bad = 0
    for kind, r in results:
        ec = r.get("errcode", 0)
        ok = ec == 0
        if not ok:
            bad += 1
        print(f"{kind:<6}{'成功' if ok else '失败'}  {json.dumps(r, ensure_ascii=False)[:200]}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
