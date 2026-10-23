"""Dialogue timing.

Sources, in priority order:
  1. user-supplied SRT  -> cue times, boundaries calibrated with VAD
  2. FunASR paraformer-zh (+ fsmn-vad + ct-punc) sentence timestamps
  3. WhisperX (optional extra) as a fallback ASR

VAD backends: FunASR fsmn-vad -> silero-vad -> none.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .util import log

TAG_RE = re.compile(r"<[^>]+>|\{[^}]+\}")


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_srt(path: Path) -> list[dict]:
    import srt
    lines = []
    for sub in srt.parse(_read_text(path), ignore_errors=True):
        text = TAG_RE.sub("", sub.content).replace("\n", " ").strip()
        if not text:
            continue
        lines.append({
            "start": round(sub.start.total_seconds(), 3),
            "end": round(sub.end.total_seconds(), 3),
            "text": text,
            "source": "srt",
        })
    lines.sort(key=lambda l: l["start"])
    return lines


# ---------------------------------------------------------------- VAD
def run_vad(wav16k: Path, backend: str = "auto", lang: str = "en") -> tuple[list[tuple[float, float]] | None, str | None]:
    """Return ([(start, end), ...], backend_name) or (None, None). FunASR fsmn-vad first for zh, silero first for en."""
    order = ["silero", "funasr"] if lang == "en" else ["funasr", "silero"]
    if backend == "auto":
        for b in order:
            segs, name = run_vad(wav16k, b, lang)
            if segs is not None:
                return segs, name
        return None, None
    if backend == "funasr":
        try:
            from funasr import AutoModel
            model = AutoModel(model="fsmn-vad", disable_update=True, disable_pbar=True, log_level="ERROR",
                              max_end_silence_time=300)  # default 800 ms merges lines < 0.8 s apart
            res = model.generate(input=str(wav16k))
            segs = [(s / 1000.0, e / 1000.0) for s, e in res[0]["value"]]
            log(f"VAD (funasr fsmn-vad): {len(segs)} speech segments")
            return segs, "funasr-fsmn-vad"
        except Exception as e:
            log(f"FunASR VAD unavailable ({type(e).__name__}: {str(e)[:200]})")
    if backend == "silero":
        try:
            import soundfile as sf
            import torch
            from silero_vad import load_silero_vad, get_speech_timestamps
            model = load_silero_vad()
            y, sr = sf.read(str(wav16k), dtype="float32", always_2d=True)
            assert sr == 16000, "VAD input must be 16 kHz"
            wav = torch.from_numpy(y.mean(axis=1).copy())
            ts = get_speech_timestamps(wav, model, sampling_rate=16000, return_seconds=True,
                                       min_speech_duration_ms=150, min_silence_duration_ms=150)
            segs = [(float(t["start"]), float(t["end"])) for t in ts]
            log(f"VAD (silero): {len(segs)} speech segments")
            return segs, "silero-vad"
        except Exception as e:
            log(f"silero VAD unavailable ({type(e).__name__}: {str(e)[:200]})")
    return None, None


# ---------------------------------------------------------------- ASR
_UNIT_RE = re.compile(r"[A-Za-z0-9']+|[^\sA-Za-z0-9']")


def _is_punct(unit: str) -> bool:
    import unicodedata
    return all(unicodedata.category(ch)[0] in "PSZ" for ch in unit)


def _funasr_lines(res: dict, max_gap: float = 0.5, max_chars: int = 40) -> list[dict]:
    """Rebuild dialogue lines from FunASR's full text + token-level timestamps.

    paraformer's `sentence_info` is cut by the punctuation model, i.e. by text, so one "sentence" can span a
    12 s silence between two utterances. Here a line ends at terminal punctuation, at an inter-token gap
    > max_gap, or after max_chars characters, so line spans follow the actual speech."""
    text = (res.get("text") or "").strip()
    ts = res.get("timestamp") or []
    units = _UNIT_RE.findall(text)
    n_tok = sum(1 for u in units if not _is_punct(u))
    if not ts or n_tok != len(ts):
        return _funasr_lines_from_sentences(res, max_gap)
    lines: list[dict] = []
    cur, cur_start, cur_end, ti = "", None, None, 0

    def flush():
        nonlocal cur, cur_start, cur_end
        t = cur.strip()
        if t and cur_start is not None and any(not _is_punct(u) for u in _UNIT_RE.findall(t)):
            lines.append({"start": round(cur_start, 3), "end": round(cur_end, 3), "text": t, "source": "asr"})
        cur, cur_start, cur_end = "", None, None

    for u in units:
        if _is_punct(u):
            if cur:
                cur += u
                if u in "。！？；…!?;":
                    flush()
            continue
        s, e = ts[ti][0] / 1000.0, ts[ti][1] / 1000.0
        ti += 1
        if cur and cur_end is not None and s - cur_end > max_gap:
            flush()
        if not cur:
            cur_start = s
        cur += u
        cur_end = e
        if len(cur) >= max_chars:
            flush()
    flush()
    return lines


def _funasr_lines_from_sentences(res: dict, max_gap: float) -> list[dict]:
    """Fallback when text/timestamp alignment is off: split each sentence_info by its own token gaps,
    assigning characters to sub-lines proportionally."""
    lines = []
    for sent in res.get("sentence_info") or []:
        text = (sent.get("text") or "").strip()
        toks = sent.get("timestamp") or []
        if not text:
            continue
        if not toks:
            lines.append({"start": sent["start"] / 1000.0, "end": sent["end"] / 1000.0, "text": text, "source": "asr"})
            continue
        groups, g = [], [toks[0]]
        for a, b in zip(toks, toks[1:]):
            if b[0] / 1000.0 - a[1] / 1000.0 > max_gap:
                groups.append(g)
                g = []
            g.append(b)
        groups.append(g)
        chars = [c for c in text]
        pos = 0
        for gi, grp in enumerate(groups):
            k = len(chars) if gi == len(groups) - 1 else int(round(len(chars) * len(grp) / len(toks)))
            t = "".join(chars[pos:pos + k]).strip()
            pos += k
            if t:
                lines.append({"start": grp[0][0] / 1000.0, "end": grp[-1][1] / 1000.0, "text": t, "source": "asr"})
    return lines


def _lines_from_words(words: list[tuple[float, float, str]], max_gap: float = 0.5, max_words: int = 25) -> list[dict]:
    """Group word-level timestamps into lines: split at gaps > max_gap, after sentence-final punctuation,
    or after max_words words."""
    lines: list[dict] = []
    cur: list[str] = []
    cur_start = cur_end = None
    for s_, e_, w in words:
        w = w.strip()
        if not w:
            continue
        if cur and cur_end is not None and s_ - cur_end > max_gap:
            lines.append({"start": round(cur_start, 3), "end": round(cur_end, 3), "text": " ".join(cur), "source": "asr"})
            cur, cur_start = [], None
        if not cur:
            cur_start = s_
        cur.append(w)
        cur_end = max(e_, s_)
        if w[-1] in ".?!" or len(cur) >= max_words:
            lines.append({"start": round(cur_start, 3), "end": round(cur_end, 3), "text": " ".join(cur), "source": "asr"})
            cur, cur_start = [], None
    if cur:
        lines.append({"start": round(cur_start, 3), "end": round(cur_end, 3), "text": " ".join(cur), "source": "asr"})
    return lines


def lines_from_vad_and_words(vad: list[tuple[float, float]], words: list[tuple[float, float, str]],
                             merge_gap: float = 0.35, max_word_distance: float = 1.0) -> list[dict]:
    """VAD segments give the line boundaries (accurate), ASR words give the text (their timestamps are only
    used to assign each word to the nearest segment). Adjacent VAD segments closer than merge_gap are merged."""
    segs = merge_intervals(vad, merge_gap)
    if not segs:
        return _lines_from_words(words)
    buckets: list[list[str]] = [[] for _ in segs]
    dropped = []
    for s_, e_, w in words:
        w = w.strip()
        if not w:
            continue
        # Whisper stretches a word's START back across a preceding silence, never its end:
        # anchor each word near its end so it lands in the segment where it is actually spoken
        mid = max(s_, e_ - 0.15)
        best, best_d = None, None
        for i, (a, b) in enumerate(segs):
            d = 0.0 if a <= mid <= b else min(abs(mid - a), abs(mid - b))
            if best_d is None or d < best_d:
                best, best_d = i, d
        if best_d > max_word_distance:  # Whisper hallucination in music/silence ("Thank you.")
            dropped.append((round(s_, 2), w))
            continue
        buckets[best].append(w)
    if dropped:
        log(f"ASR: dropped {len(dropped)} words with no speech nearby (likely hallucinations): {dropped[:8]}")
    lines = []
    for (a, b), ws in zip(segs, buckets):
        lines.append({"start": round(a, 3), "end": round(b, 3), "text": " ".join(ws),
                      "source": "asr" if ws else "vad"})
    return lines


def transcribe_words(wav16k: Path, lang: str, device: str = "cpu", model_name: str | None = None
                     ) -> tuple[list[tuple[float, float, str]], str]:
    """Word (en) / character (zh) level timestamps: [(start, end, text)], used by the ASR path and by listen_check."""
    if lang == "zh":
        from funasr import AutoModel
        model = AutoModel(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc",
                          disable_update=True, disable_pbar=True, log_level="ERROR", device=device)
        res = model.generate(input=str(wav16k), batch_size_s=300, sentence_timestamp=True)[0]
        units = [u for u in _UNIT_RE.findall(res.get("text") or "") if not _is_punct(u)]
        ts = res.get("timestamp") or []
        return [(a / 1000.0, b / 1000.0, u) for (a, b), u in zip(ts, units)], "funasr-paraformer-zh"
    from faster_whisper import WhisperModel
    names = [model_name] if model_name else ["large-v3-turbo", "medium.en" if lang == "en" else "medium", "small"]
    model, used = None, None
    for name in names:
        try:
            model = WhisperModel(name, device="cpu" if device in ("cpu", "mps") else device, compute_type="int8")
            used = name
            break
        except Exception as e:
            log(f"faster-whisper model {name} unavailable: {str(e)[:150]}")
    if model is None:
        raise RuntimeError("no faster-whisper model could be loaded")
    # pass samples directly: faster-whisper's own decoder (PyAV) breaks across av versions
    import soundfile as sf
    audio, sr = sf.read(str(wav16k), dtype="float32", always_2d=True)
    assert sr == 16000, "ASR input must be 16 kHz"
    # vad_filter=False: with the filter on, Whisper merges all speech into one block, smears word times across
    # silences and drops punctuation; sentence boundaries then come from our own VAD (lines_from_vad_and_words)
    segments, info = model.transcribe(audio.mean(axis=1), language=lang, word_timestamps=True, vad_filter=False,
                                      beam_size=5, condition_on_previous_text=False)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            words.append((float(w.start), float(w.end), w.word))
    return words, f"faster-whisper-{used}"


def run_asr(wav16k: Path, backend: str = "auto", device: str = "cpu", lang: str = "en",
            vad: list[tuple[float, float]] | None = None) -> tuple[list[dict] | None, str | None]:
    if lang == "zh" and backend in ("auto", "funasr"):
        try:
            from funasr import AutoModel
            model = AutoModel(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc",
                              disable_update=True, disable_pbar=True, log_level="ERROR", device=device)
            res = model.generate(input=str(wav16k), batch_size_s=300, sentence_timestamp=True)
            lines = _funasr_lines(res[0])
            log(f"ASR (funasr paraformer-zh): {len(lines)} lines")
            return lines, "funasr-paraformer-zh"
        except Exception as e:
            log(f"FunASR ASR unavailable ({type(e).__name__}: {str(e)[:300]})")
    if backend in ("auto", "faster-whisper"):
        try:
            words, name = transcribe_words(wav16k, lang, device)
            lines = lines_from_vad_and_words(vad, words) if vad else _lines_from_words(words)
            log(f"ASR ({name}): {len(lines)} lines from {len(words)} words"
                f"{' (boundaries from VAD)' if vad else ''}")
            return lines, name
        except Exception as e:
            log(f"faster-whisper unavailable ({type(e).__name__}: {str(e)[:300]})")
    if backend in ("auto", "whisperx"):
        try:
            import whisperx
            model = whisperx.load_model("large-v3-turbo", device=device, compute_type="int8", language=lang)
            audio = whisperx.load_audio(str(wav16k))
            result = model.transcribe(audio, batch_size=8, language=lang)
            try:
                align_model, meta = whisperx.load_align_model(language_code=lang, device=device)
                result = whisperx.align(result["segments"], align_model, meta, audio, device)
            except Exception as e:  # alignment is optional
                log(f"whisperx align skipped: {e}")
            lines = [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip(), "source": "asr"}
                     for s in result["segments"] if s.get("text", "").strip()]
            log(f"ASR (whisperx): {len(lines)} lines")
            return lines, "whisperx"
        except Exception as e:
            log(f"WhisperX unavailable ({type(e).__name__}: {str(e)[:300]})")
    return None, None


# ---------------------------------------------------------------- calibration / merging
def calibrate_with_vad(lines: list[dict], vad: list[tuple[float, float]] | None, cfg: Config) -> dict:
    """Snap SRT cue boundaries to the nearest VAD speech boundary within cfg.vad_max_shift.

    A boundary is only moved when a VAD boundary is that close; otherwise the cue time is kept (e.g. when
    the VAD merged this cue with its neighbour). Uncaptioned speech is handled separately."""
    if not vad:
        return {"lines": lines, "moved": 0, "mean_abs_shift": 0.0}
    starts = [v[0] for v in vad]
    ends = [v[1] for v in vad]
    moved, shifts = 0, []
    out = []
    for ln in lines:
        s, e = ln["start"], ln["end"]
        ns = min(starts, key=lambda x: abs(x - s)) if starts else s
        ne = min(ends, key=lambda x: abs(x - e)) if ends else e
        if abs(ns - s) > cfg.vad_max_shift:
            ns = s
        if abs(ne - e) > cfg.vad_max_shift:
            ne = e
        if ne - ns < 0.2:  # degenerate, keep original
            ns, ne = s, e
        new = dict(ln)
        new["start"], new["end"] = round(ns, 3), round(ne, 3)
        if abs(ns - s) > 0.02 or abs(ne - e) > 0.02:
            moved += 1
            shifts += [abs(ns - s), abs(ne - e)]
            new["orig_start"], new["orig_end"] = s, e
        out.append(new)
    return {"lines": out, "moved": moved,
            "mean_abs_shift": (sum(shifts) / len(shifts)) if shifts else 0.0}


def uncaptioned_speech(lines: list[dict], vad: list[tuple[float, float]], cfg: Config) -> list[dict]:
    """VAD speech not covered by any caption -> extra text-less 'lines' so narration never talks over it."""
    covered = merge_intervals([(l["start"] - 0.15, l["end"] + 0.15) for l in lines], 0.0)
    extra = []
    for vs, ve in vad:
        cursor = vs
        for cs, ce in covered:
            if ce <= cursor:
                continue
            if cs >= ve:
                break
            if cs > cursor:
                extra.append((cursor, min(cs, ve)))
            cursor = max(cursor, ce)
        if cursor < ve:
            extra.append((cursor, ve))
    return [{"start": round(s, 3), "end": round(e, 3), "text": "", "source": "vad"}
            for s, e in extra if e - s >= cfg.vad_extra_min]


def merge_intervals(iv: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    iv = sorted((max(0.0, s), e) for s, e in iv if e > s)
    out: list[list[float]] = []
    for s, e in iv:
        if out and s <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def speech_segments(lines: list[dict], cfg: Config) -> list[tuple[float, float]]:
    return merge_intervals([(l["start"], l["end"]) for l in lines], cfg.speech_merge_gap)
