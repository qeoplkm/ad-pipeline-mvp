"""'Listening' proxy: transcribe mixed.mp3 with FunASR and check that every kept narration is intelligible
at the time ad.vtt says it plays, and that the original dialogue still comes through.

    python tests/listen_check.py out/
"""
from __future__ import annotations

import difflib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adpipe.util import count_chars, setup_caches  # noqa: E402

setup_caches()
out = Path(sys.argv[1]).resolve()
N = json.loads((out / "narrations.json").read_text(encoding="utf-8"))
D = json.loads((out / "dialogue.json").read_text(encoding="utf-8"))
kept = [n for n in N["narrations"] if n["status"] == "kept"]

wav = out / "audio" / "mixed16k.wav"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out / "mixed.mp3"), "-ac", "1", "-ar", "16000", str(wav)], check=True)
from adpipe.dialogue import transcribe_words  # noqa: E402
lang = json.loads((out / "stats.json").read_text(encoding="utf-8")).get("config", {}).get("lang", "zh")
units_ts, backend = transcribe_words(wav, lang)
units = [u for _, _, u in units_ts]
toks = [(a * 1000, b * 1000) for a, b, _ in units_ts]
print(f"transcribed mixed.mp3 with {backend}: {len(units)} units")


def heard_between(a: float, b: float) -> str:
    sep = " " if lang == "en" else ""
    return sep.join(u.strip() for u, t in zip(units, toks) if t[1] / 1000.0 > a and t[0] / 1000.0 < b)


def strip(s: str) -> str:
    if lang == "en":
        return " ".join(w.lower() for w in re_words.findall(s))
    return "".join(ch for ch in s if count_chars(ch))


import re as _re  # noqa: E402
re_words = _re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")


rows = []
for n in kept:
    heard = heard_between(n["actual_start"] - 0.3, n["actual_end"] + 0.3)
    want = strip(n["text_used"])
    heard = strip(heard) if lang == "en" else heard
    ratio = difflib.SequenceMatcher(None, want, heard).ratio() if want else 0.0
    rows.append((n["window_id"], n["actual_start"], ratio, n.get("stretch", 1.0), want, heard))
rows.sort(key=lambda r: r[1])
low = [r for r in rows if r[2] < 0.6]
print(f"narrations: {len(rows)}, intelligible (ratio>=0.6): {len(rows) - len(low)}, low: {len(low)}, "
      f"mean ratio {sum(r[2] for r in rows) / max(1, len(rows)):.2f}")
for wid, t, r, st, want, heard in rows:
    flag = "LOW " if r < 0.6 else "ok  "
    print(f"{flag}{wid} @{t:6.1f}s ratio={r:.2f} stretch={st:.2f}\n     said : {want}\n     heard: {heard}")

# dialogue untouched by the mix? deterministic check: per dialogue line, level change (mixed vs original) and
# narration-track energy inside the line. (ASR-based "still recognized" numbers below are only indicative:
# Whisper's timestamps drift on a track where two voices alternate.)
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
full, fsr = sf.read(str(out / "audio" / "full.wav"), dtype="float32", always_2d=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out / "mixed.mp3"), "-ar", str(fsr), str(out / "audio" / "_mixed.wav")], check=True)
mixed, _ = sf.read(str(out / "audio" / "_mixed.wav"), dtype="float32", always_2d=True)
adt, _ = sf.read(str(out / "ad_track.wav"), dtype="float32", always_2d=False)
(out / "audio" / "_mixed.wav").unlink(missing_ok=True)


def _db(x):
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9)) if x.size else -180.0


touched, worst = [], 0.0
dl = [l for l in D["lines"] if l.get("text")]
for l in dl:
    i0, i1 = int(l["start"] * fsr), int(l["end"] * fsr)
    delta = _db(mixed[i0:i1]) - _db(full[i0:i1])
    worst = min(worst, delta)
    if delta < -1.0 or _db(adt[i0:i1]) > -60:
        touched.append((l["start"], round(delta, 1), round(_db(adt[i0:i1]), 1)))
print(f"dialogue lines untouched by the mix (level change > -1 dB and no narration energy): {len(dl) - len(touched)}/{len(dl)}, "
      f"worst level change {worst:.1f} dB")
if touched:
    print("  touched:", touched[:10])

hit = 0
for l in dl:
    want = strip(l["text"])
    heard = heard_between(l["start"] - 0.2, l["end"] + 0.2)
    heard = strip(heard) if lang == "en" else heard
    if want and difflib.SequenceMatcher(None, want, heard).ratio() >= 0.6:
        hit += 1
print(f"dialogue lines still recognized in the mix: {hit}/{len(dl)}")
