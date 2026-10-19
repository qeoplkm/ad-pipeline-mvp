"""edge-tts synthesis, silence trimming, time-stretch fitting (pyrubberband -> ffmpeg atempo -> librosa)."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import Config
from .util import log, run_cmd


# ---------------------------------------------------------------- synthesis
async def _synth_one(text: str, voice: str, rate: str, out_mp3: Path, sem: asyncio.Semaphore, attempts: int = 4):
    import edge_tts
    last = None
    async with sem:
        for k in range(attempts):
            try:
                com = edge_tts.Communicate(text, voice, rate=rate)
                await com.save(str(out_mp3))
                if out_mp3.exists() and out_mp3.stat().st_size > 0:
                    return
                last = RuntimeError("empty audio")
            except Exception as e:
                last = e
            await asyncio.sleep(1.5 * (k + 1))
    raise RuntimeError(f"edge-tts failed for {out_mp3.name}: {last}")


async def _synth_many(items: list[tuple[str, Path]], voice: str, rate: str, concurrency: int):
    sem = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(*[_synth_one(t, voice, rate, p, sem) for t, p in items], return_exceptions=True)
    return results


def synthesize(items: list[tuple[str, Path]], cfg: Config) -> dict[Path, str | None]:
    """items: [(text, out_mp3)] -> {out_mp3: error_or_None}."""
    if not items:
        return {}
    for _, p in items:
        p.parent.mkdir(parents=True, exist_ok=True)
    results = asyncio.run(_synth_many(items, cfg.voice(), cfg.tts_rate, cfg.tts_concurrency))
    return {p: (str(r) if isinstance(r, Exception) else None) for (_, p), r in zip(items, results)}


# ---------------------------------------------------------------- audio helpers
def load_trimmed(mp3: Path, sr: int, top_db: float) -> np.ndarray:
    import librosa
    wav = mp3.with_suffix(".raw.wav")
    run_cmd(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(wav)])
    y, _ = sf.read(str(wav), dtype="float32", always_2d=False)
    wav.unlink(missing_ok=True)
    if y.size == 0:
        return y
    y, _ = librosa.effects.trim(y, top_db=top_db)
    return y


def time_stretch(y: np.ndarray, sr: int, rate: float) -> tuple[np.ndarray, str]:
    """rate > 1 shortens the audio. Pitch-preserving. Returns (audio, method)."""
    try:
        import pyrubberband as pyrb
        return pyrb.time_stretch(y, sr, rate).astype(np.float32), "pyrubberband"
    except Exception:
        pass
    try:
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.wav", Path(td) / "out.wav"
            sf.write(str(src), y, sr)
            run_cmd(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", f"atempo={rate:.4f}", str(dst)])
            y2, _ = sf.read(str(dst), dtype="float32", always_2d=False)
            return y2, "ffmpeg-atempo"
    except Exception:
        pass
    import librosa
    return librosa.effects.time_stretch(y, rate=rate).astype(np.float32), "librosa"


# ---------------------------------------------------------------- fitting
def fit_all(narrations: list[dict], out_dir: Path, cfg: Config) -> dict:
    """Synthesize candidates, trim, fit into their windows (direct -> stretch <= max_stretch -> short -> drop).
    Mutates narration records in place and writes tts/<id>.wav for kept ones."""
    tts_dir = out_dir / "tts"
    raw_dir = tts_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    sr = cfg.sr
    cands = [n for n in narrations if n.get("narration")]
    stats = {"candidates": len(cands), "direct": 0, "stretched": 0, "used_short": 0, "dropped": 0,
             "tts_errors": 0, "stretch_ratios": [], "stretch_method": None, "drop_reasons": []}

    # round 1: full narrations
    items = [(n["narration"], raw_dir / f"{n['window_id']}.full.mp3") for n in cands]
    log(f"TTS: synthesizing {len(items)} full narrations with {cfg.voice()}")
    errs = synthesize(items, cfg)
    need_short = []
    for n in cands:
        mp3 = raw_dir / f"{n['window_id']}.full.mp3"
        n["fit_log"] = []
        if errs.get(mp3):
            n["fit_log"].append(f"tts error: {errs[mp3]}")
            n["_tts_err"] = True
            continue
        if not _try_fit(n, mp3, "full", sr, cfg, tts_dir, stats):
            need_short.append(n)

    # round 2: short variants
    items = [(n["narration_short"], raw_dir / f"{n['window_id']}.short.mp3") for n in need_short if n.get("narration_short")]
    if items:
        log(f"TTS: synthesizing {len(items)} short variants")
        errs = synthesize(items, cfg)
    for n in need_short:
        if not n.get("narration_short"):
            n["fit_log"].append("no narration_short available")
            continue
        mp3 = raw_dir / f"{n['window_id']}.short.mp3"
        if errs.get(mp3):
            n["fit_log"].append(f"tts error (short): {errs[mp3]}")
            continue
        _try_fit(n, mp3, "short", sr, cfg, tts_dir, stats)

    for n in cands:
        if n.get("_tts_err"):
            n.pop("_tts_err")
            n["status"] = "tts_error"
            stats["tts_errors"] += 1
        elif n.get("tts_path") is None:
            n["status"] = "dropped_too_long"
            stats["dropped"] += 1
            stats["drop_reasons"].append({"window_id": n["window_id"], "reason": "; ".join(n["fit_log"])})
    if stats["stretch_ratios"]:
        r = stats["stretch_ratios"]
        stats["stretch_mean"] = round(float(np.mean(r)), 3)
        stats["stretch_max"] = round(float(np.max(r)), 3)
    log(f"TTS fit: direct={stats['direct']} stretched={stats['stretched']} short={stats['used_short']} "
        f"dropped={stats['dropped']} errors={stats['tts_errors']}")
    return stats


def _try_fit(n: dict, mp3: Path, variant: str, sr: int, cfg: Config, tts_dir: Path, stats: dict) -> bool:
    y = load_trimmed(mp3, sr, cfg.trim_top_db)
    dur = len(y) / sr
    avail = n["duration"] - cfg.fit_margin
    if dur <= 0.05:
        n["fit_log"].append(f"{variant}: empty audio")
        return False
    ratio = dur / avail
    if ratio <= 1.0:
        method = None
        y_out = y
        n["stretch"] = 1.0
    elif ratio <= cfg.max_stretch:
        y_out, method = time_stretch(y, sr, ratio * 1.01)
        n["stretch"] = round(ratio, 3)
        stats["stretch_ratios"].append(ratio)
        stats["stretch_method"] = method
        if len(y_out) / sr > avail:  # stretch not exact: hard-trim the tail by a few ms
            y_out = y_out[: int(avail * sr)]
    else:
        n["fit_log"].append(f"{variant}: {dur:.2f}s > {avail:.2f}s x {cfg.max_stretch} (ratio {ratio:.2f})")
        return False
    out = tts_dir / f"{n['window_id']}.wav"
    sf.write(str(out), y_out.astype(np.float32), sr, subtype="PCM_16")
    n.update({
        "variant": variant,
        "text_used": n["narration"] if variant == "full" else n["narration_short"],
        "tts_raw_duration": round(dur, 3),
        "tts_duration": round(len(y_out) / sr, 3),
        "tts_path": str(out.relative_to(tts_dir.parent)),
        "actual_start": n["start"],
        "actual_end": round(n["start"] + len(y_out) / sr, 3),
        "status": "kept",
    })
    if method:
        n["stretch_method"] = method
    n["fit_log"].append(f"{variant}: {dur:.2f}s into {avail:.2f}s -> " + ("direct" if ratio <= 1.0 else f"stretched x{ratio:.2f} ({method})"))
    if ratio <= 1.0:
        stats["direct"] += 1
    else:
        stats["stretched"] += 1
    if variant == "short":
        stats["used_short"] += 1
    return True
