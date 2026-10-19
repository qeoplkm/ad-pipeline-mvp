"""Audio extraction (ffmpeg) and vocal/background separation (demucs htdemucs)."""
from __future__ import annotations

import json
from pathlib import Path

from .util import log, run_cmd


def probe_duration(video: Path) -> float:
    res = run_cmd(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                   "-of", "json", str(video)])
    return float(json.loads(res.stdout)["format"]["duration"])


def probe_video(video: Path) -> dict:
    res = run_cmd(["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
                   "-show_entries", "format=duration", "-of", "json", str(video)])
    info = json.loads(res.stdout)
    st = (info.get("streams") or [{}])[0]
    num, den = (st.get("r_frame_rate") or "25/1").split("/")
    return {
        "width": st.get("width"), "height": st.get("height"),
        "fps": float(num) / float(den or 1),
        "duration": float(info["format"]["duration"]),
    }


def extract_audio(video: Path, out_dir: Path, sr: int = 44100) -> dict:
    """Write full.wav (stereo, sr) for separation/mixing and mono16k.wav for ASR/VAD."""
    out_dir.mkdir(parents=True, exist_ok=True)
    full = out_dir / "full.wav"
    mono16k = out_dir / "mono16k.wav"
    run_cmd(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn",
             "-ac", "2", "-ar", str(sr), "-c:a", "pcm_s16le", str(full)])
    run_cmd(["ffmpeg", "-y", "-v", "error", "-i", str(full),
             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(mono16k)])
    return {"full": full, "mono16k": mono16k}


def to_mono16k(src: Path, dst: Path) -> Path:
    run_cmd(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(dst)])
    return dst


def separate_vocals(full_wav: Path, out_dir: Path, device: str = "cpu",
                    model: str = "htdemucs") -> dict | None:
    """Two-stem separation through the demucs API. Stems are written as float32 WAV at the input's scale.

    (The demucs CLI's default clip-mode 'rescale' divides the whole stem by its peak whenever any sample
    exceeds 1.0 - a single loud transient then attenuates the entire vocals track by >10 dB.)"""
    out_dir.mkdir(parents=True, exist_ok=True)
    vocals = out_dir / "vocals.wav"
    no_vocals = out_dir / "no_vocals.wav"
    try:
        import soundfile as sf
        import torch
        from demucs.api import Separator
    except Exception as e:  # pragma: no cover
        log(f"demucs not importable: {e}")
        return None
    try:
        y, sr = sf.read(str(full_wav), dtype="float32", always_2d=True)
        wav = torch.from_numpy(y.T.copy())
        log(f"demucs {model} on {device}: {y.shape[0] / sr:.1f}s stereo")
        sep = Separator(model=model, device=device, progress=False)
        _, sources = sep.separate_tensor(wav, sr)
        voc = sources["vocals"]
        rest = sum(v for k, v in sources.items() if k != "vocals")
        sf.write(str(vocals), voc.T.numpy(), sep.samplerate, subtype="FLOAT")
        sf.write(str(no_vocals), rest.T.numpy(), sep.samplerate, subtype="FLOAT")
        return {"vocals": vocals, "no_vocals": no_vocals}
    except Exception as e:
        log(f"demucs failed ({type(e).__name__}: {e}); continuing without separation")
        return None
