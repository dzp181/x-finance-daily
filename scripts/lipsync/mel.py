"""Wav2Lip 的音频预处理：复现 librosa 的 STFT + Slaney mel filterbank。

不直接依赖 librosa，因为它的 numba/llvmlite 依赖在 Python 3.10 + numpy 2.x 上
很容易装崩。这里用 numpy 重写，参数与 Wav2Lip hparams 完全一致：

    sample_rate 16000 / n_fft 800 / hop 200 / win 800 / n_mels 80
    fmin 55 / fmax 7600 / max_abs_value 4

对齐要点：librosa.stft 默认 center=True、pad_mode='constant'，并且 mel 的
Slaney 面积归一化会让低频权重大幅提高，这两处任一处不一致，口型就会整体偏移。
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000
N_FFT = 800
HOP = 200
WIN = 800
N_MELS = 80
FMIN = 55.0
FMAX = 7600.0
MAX_ABS = 4.0


def _hz_to_mel(freqs: np.ndarray) -> np.ndarray:
    """librosa 默认的 Slaney 刻度（htk=False）。"""
    f_min, f_sp = 0.0, 200.0 / 3
    mels = (freqs - f_min) / f_sp
    min_log_hz = 1000.0
    min_log_mel = (min_log_hz - f_min) / f_sp
    logstep = np.log(6.4) / 27.0
    log_t = freqs >= min_log_hz
    mels[log_t] = min_log_mel + np.log(freqs[log_t] / min_log_hz) / logstep
    return mels


def _mel_to_hz(mels: np.ndarray) -> np.ndarray:
    f_min, f_sp = 0.0, 200.0 / 3
    freqs = f_min + f_sp * mels
    min_log_hz = 1000.0
    min_log_mel = (min_log_hz - f_min) / f_sp
    logstep = np.log(6.4) / 27.0
    log_t = mels >= min_log_mel
    freqs[log_t] = min_log_hz * np.exp(logstep * (mels[log_t] - min_log_mel))
    return freqs


def _mel_frequencies(n_mels: int, fmin: float, fmax: float) -> np.ndarray:
    min_mel = _hz_to_mel(np.array([fmin]))[0]
    max_mel = _hz_to_mel(np.array([fmax]))[0]
    return _mel_to_hz(np.linspace(min_mel, max_mel, n_mels))


def _mel_filterbank() -> np.ndarray:
    """(N_MELS, 1 + N_FFT//2)，norm='slaney'。"""
    fftfreqs = np.linspace(0, SAMPLE_RATE / 2, 1 + N_FFT // 2)
    mel_f = _mel_frequencies(N_MELS + 2, FMIN, FMAX)
    fdiff = np.diff(mel_f)
    ramps = np.subtract.outer(mel_f, fftfreqs)

    weights = np.zeros((N_MELS, 1 + N_FFT // 2), dtype=np.float64)
    for i in range(N_MELS):
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]
        weights[i] = np.maximum(0, np.minimum(lower, upper))

    enorm = 2.0 / (mel_f[2 : N_MELS + 2] - mel_f[:N_MELS])
    weights *= enorm[:, np.newaxis]
    return weights


_MEL_BASIS: np.ndarray | None = None


def _basis() -> np.ndarray:
    global _MEL_BASIS
    if _MEL_BASIS is None:
        _MEL_BASIS = _mel_filterbank()
    return _MEL_BASIS


def _hann() -> np.ndarray:
    """scipy.signal.get_window('hann', 800, fftbins=True)，即周期型 Hann。"""
    n = np.arange(WIN)
    return 0.5 - 0.5 * np.cos(2 * np.pi * n / WIN)


def _stft(y: np.ndarray) -> np.ndarray:
    pad = N_FFT // 2
    y = np.pad(y, (pad, pad), mode="constant")
    n_frames = 1 + (len(y) - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n_frames)[:, None]
    frames = y[idx] * _hann()[None, :]
    return np.fft.rfft(frames, n=N_FFT, axis=1).T  # (401, T)


def load_wav(path: str) -> np.ndarray:
    """读成 16k 单声道 float32，[-1, 1]。"""
    import wave

    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        n = wf.getnchannels()
        width = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    if width != 2:
        raise ValueError(f"只支持 16bit wav，当前 {width*8}bit")

    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if n > 1:
        data = data.reshape(-1, n).mean(axis=1)

    if sr != SAMPLE_RATE:
        # 线性插值重采样，语音特征足够
        t_new = np.arange(int(len(data) * SAMPLE_RATE / sr)) * (sr / SAMPLE_RATE)
        data = np.interp(t_new, np.arange(len(data)), data).astype(np.float32)

    return data


def melspectrogram(wav: np.ndarray) -> np.ndarray:
    """返回 (T, 80)，值域约 [-4, 4]，与训练时一致。"""
    D = _stft(np.asarray(wav, dtype=np.float64))
    S = _basis() @ np.abs(D)               # (80, T)

    min_level = np.exp(-100 / 20 * np.log(10))
    S = 20 * np.log10(np.maximum(min_level, S))
    S = np.clip(
        (2 * MAX_ABS) * ((S - S.min()) / (S.max() - S.min())) - MAX_ABS,
        -MAX_ABS,
        MAX_ABS,
    )
    return S.T.astype(np.float32)


def mel_chunks(mel: np.ndarray, fps: float, n_frames: int, mel_step: int = 16) -> np.ndarray:
    """按视频帧率取 mel 窗口，返回 (n_frames, 80, 16)。

    Wav2Lip 训练时每个视频帧对应 16 个 mel 帧，即 0.2s。mel 的帧率是
    16000/200 = 80Hz，所以第 i 帧对应 mel 起点 int(i * 80 / fps)。
    末尾不足一窗时对齐到音频尾部，避免最后半秒口型静止。
    """
    mult = 80.0 / fps
    total = mel.shape[0]
    out = np.empty((n_frames, mel.shape[1], mel_step), dtype=np.float32)
    for i in range(n_frames):
        start = int(i * mult)
        if start + mel_step > total:
            start = max(0, total - mel_step)
        out[i] = mel[start : start + mel_step].T
    return out
