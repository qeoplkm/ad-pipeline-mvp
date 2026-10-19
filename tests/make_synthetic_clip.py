"""Build a synthetic Chinese test clip (video + audio + srt) to exercise the pipeline without a real film.

    python tests/make_synthetic_clip.py tests/synthetic

Video: 75 s of coloured 'shots' with a moving shape and a shot number (hard cuts at known times).
Audio: edge-tts Mandarin dialogue at known times over a quiet pad, plus loud impulses (sound effects) in some gaps.
Also writes clip.srt (cue times deliberately offset by -0.3/+0.2 s) and truth.json.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/synthetic").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "zh"
OUT.mkdir(parents=True, exist_ok=True)
DUR, FPS, W, H, SR = 75.0, 25, 640, 360, 44100
LINES_BY_LANG = {
    "zh": [(6.0, "你怎么会在这里？"), (8.3, "我来找你谈谈上次的事。"), (11.2, "现在不是时候。"),
           (24.0, "你听我说完好吗？"), (26.6, "好吧，你说。"),
           (40.0, "这是我欠你的。"), (42.3, "我不需要。"), (52.0, "再见。")],
    "en": [(6.0, "What are you doing here?"), (8.3, "I came to talk about what happened last week."), (11.4, "This is not a good time."),
           (24.0, "Will you just hear me out?"), (26.6, "Fine. Go ahead."),
           (40.0, "This is what I owe you."), (42.3, "I don't want it."), (52.0, "Goodbye.")],
}
LINES = LINES_BY_LANG[LANG]
VOICE = {"zh": "zh-CN-YunxiNeural", "en": "en-US-GuyNeural"}[LANG]
IMPULSES = [18.0, 45.0, 46.5, 70.0]
CUTS = [5.0, 12.0, 19.0, 27.0, 36.0, 48.0, 60.0]
COLORS = [(40, 40, 160), (30, 140, 60), (150, 60, 40), (120, 120, 30), (60, 30, 120), (20, 110, 130), (90, 90, 90), (140, 40, 110)]


def make_video(path: Path):
    tmp = path.with_suffix(".raw.mp4")
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    n = int(DUR * FPS)
    for i in range(n):
        t = i / FPS
        shot = sum(1 for c in CUTS if t >= c)
        frame = np.full((H, W, 3), COLORS[shot % len(COLORS)], dtype=np.uint8)
        x = int((t * 60) % (W + 80)) - 40
        cv2.circle(frame, (x, H // 2 + int(60 * np.sin(t))), 30, (240, 240, 240), -1)
        cv2.putText(frame, f"SHOT {shot + 1}", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3, cv2.LINE_AA)
        cv2.putText(frame, f"t={t:05.1f}", (20, H - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
        vw.write(frame)
    vw.release()
    return tmp


async def synth(lines, voice=None):
    voice = voice or VOICE
    import edge_tts
    paths = []
    for i, (_, text) in enumerate(lines):
        p = OUT / f"_line{i}.mp3"
        await edge_tts.Communicate(text, voice).save(str(p))
        paths.append(p)
    return paths


def load_mono(p: Path):
    wav = p.with_suffix(".wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(p), "-ac", "1", "-ar", str(SR), str(wav)], check=True)
    y, _ = sf.read(str(wav), dtype="float32")
    wav.unlink()
    import librosa
    y, _ = librosa.effects.trim(y, top_db=35)
    return y


def make_audio(path: Path):
    n = int(DUR * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(0)
    pad = 0.02 * (np.sin(2 * np.pi * 220 * t) + 0.6 * np.sin(2 * np.pi * 330.3 * t)) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.1 * t))
    noise = rng.normal(0, 0.004, n).astype(np.float32)
    y = (pad + noise).astype(np.float32)
    mp3s = asyncio.run(synth(LINES))
    truth = []
    for (start, text), p in zip(LINES, mp3s):
        s = load_mono(p) * 0.8
        i0 = int(start * SR)
        y[i0:i0 + len(s)] += s[: n - i0]
        truth.append({"start": round(start, 3), "end": round(start + len(s) / SR, 3), "text": text})
        p.unlink()
    for ti in IMPULSES:
        i0 = int(ti * SR)
        burst = rng.normal(0, 0.5, int(0.04 * SR)).astype(np.float32) * np.hanning(int(0.04 * SR)).astype(np.float32)
        y[i0:i0 + len(burst)] += burst
    y = np.clip(y, -1, 1)
    sf.write(str(path), np.stack([y, y], axis=1), SR, subtype="PCM_16")
    return truth


def write_srt(truth, path: Path):
    def ts(x):
        h, m = int(x // 3600), int((x % 3600) // 60)
        s = x % 60
        return f"{h:02d}:{m:02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"
    rows = []
    for i, l in enumerate(truth, 1):
        rows.append(f"{i}\n{ts(max(0, l['start'] - 0.3))} --> {ts(l['end'] + 0.2)}\n{l['text']}\n")
    path.write_text("\n".join(rows), encoding="utf-8")


def main():
    raw = make_video(OUT / "clip.mp4")
    wav = OUT / "clip_audio.wav"
    truth = make_audio(wav)
    enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast"]
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-i", str(wav), *enc, "-c:a", "aac", "-b:a", "160k",
                        "-shortest", str(OUT / "clip.mp4")])
    if r.returncode != 0:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-i", str(wav), "-c:v", "mpeg4", "-q:v", "4",
                        "-c:a", "aac", "-shortest", str(OUT / "clip.mp4")], check=True)
    raw.unlink()
    wav.unlink()
    write_srt(truth, OUT / "clip.srt")
    (OUT / "truth.json").write_text(json.dumps({"lines": truth, "impulses": IMPULSES, "cuts": CUTS}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT / 'clip.mp4'}, clip.srt, truth.json")


if __name__ == "__main__":
    main()
