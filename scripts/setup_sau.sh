#!/usr/bin/env bash
# 一次性安装 social-auto-upload 的运行环境（视频号草稿），可重复执行。
# 依赖：uv（自带 python3.12）、网络可到 GitHub / 微软 CDN。
set -euo pipefail
cd "$(dirname "$0")/.."

REPO="https://github.com/dreammis/social-auto-upload.git"
PIN="3847fff"
VENDOR="vendor/social-auto-upload"

if [ ! -d "$VENDOR/.git" ]; then
  echo "→ 克隆 $REPO"
  mkdir -p vendor
  git clone "$REPO" "$VENDOR"
  git -C "$VENDOR" checkout "$PIN"
else
  echo "→ 已有 $VENDOR（$(git -C "$VENDOR" rev-parse --short HEAD)），跳过克隆"
fi

[ -f "$VENDOR/conf.py" ] || { cp "$VENDOR/conf.example.py" "$VENDOR/conf.py"; echo "→ 补 conf.py"; }

if [ ! -x .venv-sau/bin/python ]; then
  echo "→ 建 .venv-sau（python 3.12）"
  uv venv --python 3.12 .venv-sau
fi

echo "→ 装依赖"
uv pip install --python .venv-sau/bin/python \
  loguru "patchright==1.58.2" playwright qrcode requests segno tinycss2 \
  opencv-python-headless numpy pillow

echo "→ 装 chromium（放项目内 .browsers，避开 ~/Library 的沙箱限制）"
mkdir -p .browsers
PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers" env -u PYTHONPATH \
  .venv-sau/bin/patchright install chromium

echo
echo "√ 完成。下一步扫码登录（只需一次）："
echo "    bash scripts/push_draft.sh --login"
echo "  之后每期存草稿："
echo "    bash scripts/push_draft.sh <期目录名>"
