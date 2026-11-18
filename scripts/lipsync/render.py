"""用 Wav2Lip 驱动一张静态人像开口说话。

设计上和官方 inference.py 的差别：
1. 人脸检测换成 OpenCV YuNet（官方依赖 face_alignment + s3fd，在 M1 上装不动），
   静态图只检测一次，bbox 全程固定，不会出现逐帧抖动。
2. 音频特征走自己实现的 mel.py，不依赖 librosa。
3. 贴回时在 bbox 边缘做羽化，避免官方那种硬接缝。

用法：
    python render.py --image host.png --audio vo.wav --out out.mp4 --fps 25
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "third_party" / "Wav2Lip"))

from models.wav2lip import Wav2Lip  # noqa: E402

sys.path.insert(0, str(ROOT))
import mel as M  # noqa: E402

FFMPEG = "/Users/dzp/.workbuddy/binaries/python/versions/3.13.12/lib/python3.13/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v9.0"
IMG_SIZE = 96
MEL_STEP = 16


def pick_device(prefer: str = "auto") -> str:
    if prefer != "auto":
        return prefer
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_model(ckpt: Path, device: str) -> Wav2Lip:
    model = Wav2Lip()
    state = torch.load(ckpt, map_location="cpu", weights_only=False)
    state = state.get("state_dict", state)
    # 官方 checkpoint 的 key 带 module. 前缀
    state = {k.replace("module.", "", 1): v for k, v in state.items()}
    model.load_state_dict(state)
    model.eval()
    return model.to(device)


def detect_face(img_bgr: np.ndarray, yunet: Path) -> tuple[int, int, int, int]:
    """用 5 点关键点推一个人脸框，而不是直接用检测框。

    YuNet 的检测框会把头发、额头、脖子一起圈进去，直接送进 96×96 会把它们
    全部重建成糊的一团。这里以「眼睛到嘴」的距离 d 为尺度重建：
    顶部 eye_y - 0.78d（略高于眉毛），底部 mouth_y + 0.62d（含下巴）。
    """
    h, w = img_bgr.shape[:2]
    det = cv2.FaceDetectorYN.create(str(yunet), "", (w, h), 0.6, 0.3, 5000)
    det.setInputSize((w, h))
    _, faces = det.detect(img_bgr)
    if faces is None or len(faces) == 0:
        raise SystemExit("YuNet 没检测到人脸，换一张正脸清晰的图")

    best = max(faces, key=lambda f: f[2] * f[3])
    pts = best[4:14].reshape(5, 2)
    eye_y = float(pts[0:2, 1].mean())
    mouth_y = float(pts[3:5, 1].mean())
    cx = float((pts[0, 0] + pts[1, 0]) / 2)
    d = max(mouth_y - eye_y, 1.0)

    top = eye_y - 0.78 * d
    bottom = mouth_y + 0.62 * d
    side = bottom - top                      # 正方形，避免 resize 到 96 时变形
    left = cx - side / 2
    right = cx + side / 2

    return (
        max(0, int(round(left))),
        max(0, int(round(top))),
        min(w, int(round(right))),
        min(h, int(round(bottom))),
    )


def blend_mask(
    h: int,
    w: int,
    cy: float = 0.755,
    rx: float = 0.36,
    ry: float = 0.245,
    feather: float = 0.38,
) -> np.ndarray:
    """融合权重：只替换嘴部椭圆区域。

    Wav2Lip 输出只有 96×96，放大人脸尺度后会明显发糊。如果把整个下半脸都
    替换掉，就会出现「半张脸清晰、半张脸糊」的水泥缝。这里只保留嘴周一块
    椭圆（上到鼻尖下、下到下巴），外面全部用原图，糊的面积小到几乎看不出来。

    圆心坐标按人脸框内部比例给：嘴大概在框高 0.74 处（眼睛到嘴的距离为 d，
    框高 1.4d，顶部在 eye_y-0.78d）。
    """
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    dist = np.sqrt(((yy - cy) / ry) ** 2 + ((xx - 0.5) / rx) ** 2)
    return np.clip((1 - dist) / max(feather, 1e-6), 0, 1)[..., None].astype(np.float32)


def match_color(src: np.ndarray, ref: np.ndarray, weight: np.ndarray | None = None) -> np.ndarray:
    """LAB 空间对齐均值/方差，压掉 GAN 输出的偏色。

    权重只在会被替换的区域内统计——如果用整张脸的均值去校准嘴周一小块，
    会把它拉向全脸平均色，反而出现明显色差。
    """
    s = cv2.cvtColor(src, cv2.COLOR_BGR2LAB).astype(np.float32)
    r = cv2.cvtColor(ref, cv2.COLOR_BGR2LAB).astype(np.float32)
    w = None if weight is None else weight[..., 0].astype(np.float32)
    if w is not None and w.sum() < 1e-3:
        w = None

    for c in range(3):
        sc, rc = s[..., c], r[..., c]
        if w is None:
            s_mu, s_sd = sc.mean(), sc.std()
            r_mu, r_sd = rc.mean(), rc.std()
        else:
            tot = w.sum()
            s_mu = float((sc * w).sum() / tot)
            r_mu = float((rc * w).sum() / tot)
            s_sd = float(np.sqrt(((sc - s_mu) ** 2 * w).sum() / tot))
            r_sd = float(np.sqrt(((rc - r_mu) ** 2 * w).sum() / tot))
        s[..., c] = (sc - s_mu) / (s_sd + 1e-5) * r_sd + r_mu

    return cv2.cvtColor(np.clip(s, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def unsharp(img: np.ndarray, amount: float = 0.35, radius: float = 1.2) -> np.ndarray:
    """Wav2Lip 输出只有 96×96，放大后偏软，补一点锐度。

    radius 要小、amount 要克制：半径一大就会把双三次插值的振铃放大成颗粒感，
    整张脸看起来像加了噪点滤镜。
    """
    blur = cv2.GaussianBlur(img, (0, 0), radius)
    return cv2.addWeighted(img, 1 + amount, blur, -amount, 0)


def make_superres(kind: str):
    """加载 OpenCV dnn_superres 模型。

    Wav2Lip 只输出 96×96，直接放大到人脸尺寸（6 倍以上）会糊成一团。先超分
    到 384×384 再缩到目标尺寸，等效放大倍率降到 1.6 倍，嘴周明显清楚。
    """
    if kind == "none":
        return None
    path = ROOT.parent.parent / "assets" / "models" / f"{kind.upper()}_x4.pb"
    if not path.exists():
        print(f"! 找不到超分模型 {path}，跳过超分")
        return None
    sr = cv2.dnn_superres.DnnSuperResImpl_create()
    sr.readModel(str(path))
    sr.setModel(kind.lower(), 4)
    return sr


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--audio", required=True, help="16k 单声道 wav")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=25)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--pad-top", type=int, default=0)
    ap.add_argument("--pad-bottom", type=int, default=10)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "mps"])
    ap.add_argument("--sr", default="none", choices=["none", "espcn", "edsr"],
                    help="嘴部超分开关；OpenCV 5 的 dnn_superres 只吃 ONNX，TF 的 .pb 目前会报错")
    args = ap.parse_args()

    device = pick_device(args.device)
    print(f"device={device}")

    # 1) 底图
    img = cv2.imread(args.image)
    if img is None:
        raise SystemExit(f"读不到图：{args.image}")
    img = cv2.resize(img, (args.width, args.height), interpolation=cv2.INTER_CUBIC)

    yunet = ROOT.parent.parent / "assets" / "models" / "yunet.onnx"
    x1, y1, x2, y2 = detect_face(img, yunet)
    y2 = min(img.shape[0], y2 + args.pad_bottom)
    y1 = max(0, y1 - args.pad_top)
    print(f"face bbox=({x1},{y1},{x2},{y2})  {x2-x1}x{y2-y1}")

    # 2) 音频
    wav = M.load_wav(args.audio)
    duration = len(wav) / M.SAMPLE_RATE
    n_frames = int(duration * args.fps)
    mels = M.mel_chunks(M.melspectrogram(wav), args.fps, n_frames, MEL_STEP)
    print(f"audio {duration:.2f}s -> {n_frames} frames @ {args.fps}fps, mel {mels.shape}")

    # 3) 静态人像，逐帧输入完全一致
    face = img[y1:y2, x1:x2]
    face96 = cv2.resize(face, (IMG_SIZE, IMG_SIZE))
    masked = face96.copy()
    masked[IMG_SIZE // 2 :] = 0                      # 下半脸置零，与训练一致
    stacked = np.concatenate([masked, face96], axis=2).astype(np.float32) / 255.0
    face_batch = torch.from_numpy(stacked.transpose(2, 0, 1)).unsqueeze(0)

    model = load_model(ROOT.parent.parent / "assets" / "models" / "wav2lip_gan.pth", device)
    sr = make_superres(args.sr)
    print(f"superres={args.sr}")

    box_h, box_w = y2 - y1, x2 - x1
    alpha = blend_mask(box_h, box_w)
    canvas = img.copy()
    ref_patch = img[y1:y2, x1:x2].copy()

    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{args.width}x{args.height}", "-r", str(args.fps),
        "-i", "-",
        "-i", args.audio,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(args.out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    t0 = time.time()
    try:
        for start in range(0, n_frames, args.batch):
            end = min(start + args.batch, n_frames)
            m = torch.from_numpy(mels[start:end].astype(np.float32)).unsqueeze(1).to(device)
            fb = face_batch.repeat(end - start, 1, 1, 1).to(device)
            with torch.no_grad():
                pred = model(m, fb)
            pred = pred.cpu().numpy().transpose(0, 2, 3, 1) * 255.0

            for i in range(end - start):
                small = pred[i].astype(np.uint8)
                if sr is not None:
                    big = sr.upsample(small)
                else:
                    big = small
                p = cv2.resize(big, (box_w, box_h), interpolation=cv2.INTER_CUBIC)
                p = match_color(p, ref_patch, alpha)
                p = unsharp(p)
                region = canvas[y1:y2, x1:x2].astype(np.float32)
                blended = p.astype(np.float32) * alpha + region * (1 - alpha)
                canvas[y1:y2, x1:x2] = np.clip(blended, 0, 255).astype(np.uint8)
                proc.stdin.write(canvas.tobytes())

            done = end
            if done % (args.batch * 5) == 0 or done == n_frames:
                el = time.time() - t0
                fps_now = done / max(el, 1e-6)
                eta = (n_frames - done) / max(fps_now, 1e-6)
                print(f"  {done}/{n_frames}  {fps_now:.1f} fps  eta {eta:.0f}s", flush=True)
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait()

    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg 退出码 {proc.returncode}")

    size_mb = Path(args.out).stat().st_size / 1e6
    print(f"done -> {args.out}  {size_mb:.1f}MB  用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
