#!/usr/bin/env python3
"""把 create_media 返回的 presigned_url 用掉：把本地文件 PUT 上去。

ima 的上传是「create_media 拿凭证 → PUT COS → add_knowledge 入库」三步，
但 create_media / add_knowledge 只能走 MCP 工具，脚本这边只负责中间那次 PUT。

用法（给 agent 用，最省事）：
    python3 scripts/ima_put.py --job job.json
    python3 scripts/ima_put.py --url '<presigned>' --header 'Content-Type:text/markdown' \
        --header 'x-cos-acl:private' --file outbox/x/y.md

job.json 就是 create_media 的返回值原样存下来：
    {"upload_url": {"presigned_url": "...", "auth_headers": {...}}, "media_id": "..."}
成功后打印 media_id，失败返回非 0（含 HTTP 状态码与响应体）。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def put(url: str, headers: dict[str, str], data: bytes) -> int:
    req = urllib.request.Request(url, data=data, method="PUT")
    # Content-Length 必须与 create_media 时传入的 file_size 一致，签名里包含它
    h = {k: str(v) for k, v in headers.items()}
    h["Content-Length"] = str(len(data))
    for k, v in h.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # noqa: BLE001
        print(f"上传失败: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="执行 ima presigned PUT 上传")
    ap.add_argument("--job", help="create_media 返回值的 JSON 文件")
    ap.add_argument("--url", help="presigned_url")
    ap.add_argument("--header", action="append", default=[], help="K:V，可重复")
    ap.add_argument("--file", required=True, help="要上传的本地文件")
    args = ap.parse_args()

    if args.job:
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))
        u = job.get("upload_url") or {}
        url = u.get("presigned_url") or job.get("presigned_url")
        headers = u.get("auth_headers") or job.get("auth_headers") or {}
        media_id = job.get("media_id")
    else:
        url, headers, media_id = args.url, {}, None
    for kv in args.header:
        k, _, v = kv.partition(":")
        headers[k.strip()] = v.strip()

    if not url:
        print("缺 presigned_url", file=sys.stderr)
        return 2

    data = Path(args.file).read_bytes()
    status = put(url, headers, data)
    if 200 <= status < 300:
        print(json.dumps({"ok": True, "status": status, "media_id": media_id,
                          "bytes": len(data)}, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
