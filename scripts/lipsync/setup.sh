#!/usr/bin/env bash
# 数字人渲染环境（Wav2Lip）。在项目根目录执行：bash scripts/lipsync/setup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "==> 建 Python 3.10 环境（Wav2Lip 对版本敏感，不能和主环境混用）"
uv venv --python 3.10 .venv-lipsync
uv pip install --python .venv-lipsync/bin/python \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  torch numpy opencv-contrib-python-headless

echo "==> 拉 Wav2Lip 源码（只用来拿模型结构，人脸检测已换成 YuNet）"
mkdir -p scripts/lipsync/third_party
if [ ! -d scripts/lipsync/third_party/Wav2Lip ]; then
  git clone --depth 1 https://github.com/Rudrabha/Wav2Lip.git scripts/lipsync/third_party/Wav2Lip
fi

echo "==> 下模型"
mkdir -p assets/models
cd assets/models
[ -f wav2lip_gan.pth ] || curl -L -C - -o wav2lip_gan.pth \
  "https://huggingface.co/numz/wav2lip_studio/resolve/main/Wav2lip/wav2lip_gan.pth"
[ -f yunet.onnx ] || curl -L -o yunet.onnx \
  "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"

echo "==> 完成。示例："
echo "  .venv-lipsync/bin/python scripts/lipsync/render.py \\"
echo "    --image assets/avatar/host.png --audio vo.wav --out out.mp4 --fps 25"
