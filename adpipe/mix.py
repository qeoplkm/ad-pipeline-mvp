"""Ducked mix (original -9 dB under narration with 150 ms ramps) + narration-only track + WebVTT."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from .config import Config
from .util import fmt_ts, log, run_cmd


def _db(db: float) -> float:
    return float(10 ** (db / 20.0))


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)) + 1e-12)) if x.size else 0.0


def measure_dialogue_rms(ref_wav: Path, speech: list[tuple[float, float]]) -> float:
    y, sr = sf.read(str(ref_wav), dtype="float32", always_2d=True)
    mono = y.mean(axis=1)
    chunks = [mono[int(s * sr): int(e * sr)] for s, e in speech if e > s]
    chunks = [c for c in chunks if c.size]
    if not chunks:
        return _rms(mono) or 0.05
    return _rms(np.concatenate(chunks))


def duck_envelope(n: int, sr: int, regions: list[tuple[float, float]], duck_db: float, ramp: float) -> np.ndarray:
    env = np.ones(n, dtype=np.float32)
    g = _db(duck_db)
    r = max(1, int(ramp * sr))
    for s, e in regions:
        si, ei = max(0, int(s * sr)), min(n, int(e * sr))
        if ei <= si:
            continue
        a0 = max(0, si - r)
        if si > a0:
            env[a0:si] = np.minimum(env[a0:si], np.linspace(1.0, g, si - a0, dtype=np.float32))
        env[si:ei] = np.minimum(env[si:ei], g)
        b1 = min(n, ei + r)
        if b1 > ei:
            env[ei:b1] = np.minimum(env[ei:b1], np.linspace(g, 1.0, b1 - ei, dtype=np.float32))
    return env


def _soft_clip(x: np.ndarray, knee: float = 0.95) -> np.ndarray:
    a = np.abs(x)
    over = a > knee
    x = x.copy()
    x[over] = np.sign(x[over]) * (knee + (a[over] - knee) * 0.25)
    return np.clip(x, -1.0, 1.0)


def mix(full_wav: Path, narrations: list[dict], out_dir: Path, cfg: Config, dialogue_rms: float) -> dict:
    y, sr = sf.read(str(full_wav), dtype="float32", always_2d=True)
    n = y.shape[0]
    if dialogue_rms < _db(-45.0):  # separation or VAD went wrong; fall back to a sane fixed level
        log(f"mix: dialogue reference too quiet ({20 * np.log10(dialogue_rms + 1e-9):.1f} dB), using -20 dBFS")
        dialogue_rms = _db(-20.0)
    kept = [m for m in narrations if m.get("status") == "kept" and m.get("tts_path")]
    narr = np.zeros(n, dtype=np.float32)
    regions = []
    target = dialogue_rms * _db(cfg.narration_gain_db)
    gains = []
    for m in kept:
        a, asr = sf.read(str(out_dir / m["tts_path"]), dtype="float32", always_2d=False)
        if asr != sr:
            raise RuntimeError(f"sample rate mismatch for {m['tts_path']}: {asr} != {sr}")
        r = _rms(a)
        gain = float(np.clip(target / r, 0.1, 10.0)) if r > 0 else 1.0
        gains.append(gain)
        si = int(m["actual_start"] * sr)
        seg = (a * gain)[: max(0, n - si)]
        narr[si: si + len(seg)] += seg
        regions.append((m["actual_start"], m["actual_start"] + len(seg) / sr))
    env = duck_envelope(n, sr, regions, cfg.duck_db, cfg.duck_ramp)
    mixed = y * env[:, None] + narr[:, None]
    mixed = _soft_clip(mixed)

    mixed_wav = out_dir / "mixed.wav"
    sf.write(str(mixed_wav), mixed, sr, subtype="PCM_16")
    mixed_mp3 = out_dir / "mixed.mp3"
    run_cmd(["ffmpeg", "-y", "-v", "error", "-i", str(mixed_wav), "-c:a", "libmp3lame", "-b:a", cfg.mp3_bitrate, str(mixed_mp3)])
    mixed_wav.unlink(missing_ok=True)
    ad_track = out_dir / "ad_track.wav"
    sf.write(str(ad_track), _soft_clip(narr), sr, subtype="PCM_16")
    log(f"mix: {len(kept)} narrations, duck {cfg.duck_db} dB, narration gain mean "
        f"{(float(np.mean(gains)) if gains else 1.0):.2f}x -> {mixed_mp3.name}, {ad_track.name}")
    return {"kept": len(kept), "mixed_mp3": str(mixed_mp3), "ad_track": str(ad_track),
            "narration_seconds": round(sum(e - s for s, e in regions), 2),
            "dialogue_rms_db": round(20 * np.log10(dialogue_rms + 1e-9), 1)}


def write_vtt(narrations: list[dict], path: Path, meta: dict) -> int:
    kept = sorted((m for m in narrations if m.get("status") == "kept"), key=lambda m: m["actual_start"])
    lines = ["WEBVTT", "", f"NOTE ad-pipeline-mvp | provider={meta.get('provider')} model={meta.get('model')} "
             f"voice={meta.get('voice')}", ""]
    for m in kept:
        lines += [m["window_id"], f"{fmt_ts(m['actual_start'])} --> {fmt_ts(m['actual_end'])}", m["text_used"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(kept)
