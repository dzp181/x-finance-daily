#!/usr/bin/env bash
# 视频号草稿箱统一入口。清掉 WorkBuddy 注入的 PYTHONPATH，否则第三方库的
# mkdir(exist_ok=True) 会被 sandbox shim 变成 EEXIST 异常。
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv-sau/bin/python ]; then
  echo "缺 .venv-sau，先跑：bash scripts/setup_sau.sh" >&2
  exit 1
fi

exec env -u PYTHONPATH \
  PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers" \
  .venv-sau/bin/python scripts/push_draft.py "$@"
