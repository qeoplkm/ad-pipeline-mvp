"""Narration window computation: dialogue gaps minus transient guards, with character budgets and dialogue context."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .config import Config
from .util import log


def detect_transients(bg_wav: Path, cfg: Config) -> list[dict]:
    """Strong onsets on the background (no-vocals) stem. Returns [{t, z}] sorted by time."""
    import librosa
    from scipy.signal import find_peaks

    y, sr = librosa.load(str(bg_wav), sr=22050, mono=True)
    if len(y) < sr:
        return []
    hop = 512
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    med = float(np.median(oenv))
    mad = float(np.median(np.abs(oenv - med))) + 1e-6
    z = (oenv - med) / (1.4826 * mad)
    distance = max(1, int(cfg.transient_min_gap * sr / hop))
    peaks, _ = find_peaks(z, height=cfg.transient_z, distance=distance)
    times = librosa.frames_to_time(peaks, sr=sr, hop_length=hop)
    cands = sorted(zip(times.tolist(), z[peaks].tolist()), key=lambda x: -x[1])
    cap = int(math.ceil(len(y) / sr / 60.0 * cfg.transient_per_min))
    keep = sorted(cands[:cap], key=lambda x: x[0])
    log(f"transients: {len(peaks)} candidates above z={cfg.transient_z}, kept {len(keep)} (cap {cap})")
    return [{"t": round(t, 3), "z": round(zz, 1)} for t, zz in keep]


def _subtract(interval: tuple[float, float], blocks: list[tuple[float, float]]) -> list[tuple[float, float, bool]]:
    """Subtract sorted blocks from interval. Returns [(s, e, was_cut)]."""
    s, e = interval
    pieces = []
    cur = s
    cut = False
    for bs, be in blocks:
        if be <= cur or bs >= e:
            continue
        if bs > cur:
            pieces.append((cur, bs, True))
        cur = max(cur, be)
        cut = True
    if cur < e:
        pieces.append((cur, e, cut))
    return pieces


def compute_windows(speech: list[tuple[float, float]], duration: float, transients: list[dict],
                    lines: list[dict], cfg: Config) -> tuple[list[dict], dict]:
    guard = cfg.transient_guard
    blocks = sorted((tr["t"] - guard, tr["t"] + guard) for tr in transients)
    # subtitles may extend past a trimmed clip's end: never build windows beyond the video
    speech = [(s, min(e, duration)) for s, e in speech if s < duration]

    # dialogue gaps, including the opening gap and the gap after the last line
    gaps = []
    cursor = 0.0
    has_prev = False
    for s, e in speech:
        if s > cursor:
            gaps.append((cursor, s, has_prev, True))
        cursor = max(cursor, e)
        has_prev = True
    if duration > cursor:
        gaps.append((cursor, duration, has_prev, False))

    text_lines = [l for l in lines if l.get("text")]
    windows = []
    n_short = 0
    for g0, g1, prev, nxt in gaps:
        ws = g0 + cfg.pre_pad if prev else max(g0, cfg.opening_lead)
        we = g1 - cfg.post_pad if nxt else g1
        if prev and nxt:
            gap_type = "between"
        elif not prev and nxt:
            gap_type = "opening"
        elif prev and not nxt:
            gap_type = "closing"
        else:
            gap_type = "whole"
        for ps, pe, was_cut in _subtract((ws, we), blocks):
            dur = pe - ps
            if dur < cfg.min_window:
                n_short += 1
                continue
            budget = max(0, int(math.floor((dur - cfg.chars_reserve_sec) * cfg.unit_rate())))
            before = [l for l in text_lines if l["end"] <= ps + 0.01][-cfg.context_before:]
            after = [l for l in text_lines if l["start"] >= pe - 0.01][:cfg.context_after]
            windows.append({
                "id": "",
                "start": round(ps, 3),
                "end": round(pe, 3),
                "duration": round(dur, 3),
                "budget": budget,
                "budget_short": max(0, int(math.floor(budget * cfg.short_ratio))),
                "budget_unit": cfg.unit_name(),
                ("max_words" if cfg.lang == "en" else "max_chars"): budget,
                "gap_type": gap_type,
                "gap": [round(g0, 3), round(g1, 3)],
                "split_by_transient": was_cut,
                "context_before": [{"start": l["start"], "end": l["end"], "text": l["text"]} for l in before],
                "context_after": [{"start": l["start"], "end": l["end"], "text": l["text"]} for l in after],
            })
    for i, w in enumerate(windows, 1):
        w["id"] = f"w{i:03d}"
    stats = {"gaps": len(gaps), "windows": len(windows), "pieces_too_short": n_short,
             "split_by_transient": sum(1 for w in windows if w["split_by_transient"]),
             "non_speech_seconds": round(sum(g1 - g0 for g0, g1, _, _ in gaps), 2),
             "window_seconds": round(sum(w["duration"] for w in windows), 2)}
    log(f"windows: {len(windows)} usable (>= {cfg.min_window}s), {n_short} pieces too short, "
        f"{stats['split_by_transient']} touched by transient guards")
    return windows, stats


def split_long_windows(windows: list[dict], shots: list[tuple[float, float]], cfg: Config) -> tuple[list[dict], int]:
    """Split windows longer than cfg.long_window at shot boundaries so each narration plays over the shots it
    describes. Sub-windows keep the parent's context and get ids like w011a, w011b."""
    out, n_split = [], 0
    for w in windows:
        if w["duration"] <= cfg.long_window:
            out.append(w)
            continue
        ws, we = w["start"], w["end"]
        cuts = [s for s, _ in shots if ws + cfg.min_subwindow <= s <= we - cfg.min_subwindow]
        bounds = [ws]
        for c in cuts:
            if c - bounds[-1] >= cfg.min_subwindow:
                bounds.append(c)
        if we - bounds[-1] < cfg.min_subwindow and len(bounds) > 1:
            bounds.pop()
        bounds.append(we)
        if len(bounds) <= 2:
            out.append(w)
            continue
        n_split += 1
        parts = list(zip(bounds, bounds[1:]))
        for k, (a, b) in enumerate(parts):
            sub = dict(w)
            dur = b - a
            budget = max(0, int(math.floor((dur - cfg.chars_reserve_sec) * cfg.unit_rate())))
            key = "max_words" if cfg.lang == "en" else "max_chars"
            sub.update({"id": f"{w['id']}{chr(ord('a') + k)}", "parent": w["id"], "part": [k + 1, len(parts)],
                        "start": round(a, 3), "end": round(b, 3), "duration": round(dur, 3),
                        "budget": budget, "budget_short": max(0, int(math.floor(budget * cfg.short_ratio))),
                        key: budget})
            out.append(sub)
    log(f"long windows: split {n_split} windows > {cfg.long_window}s at shot boundaries -> {len(out)} windows")
    return out, n_split
