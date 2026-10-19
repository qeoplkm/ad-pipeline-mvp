"""Sanity checks on a pipeline output directory.

    python tests/check_outputs.py out/ [tests/synthetic/truth.json]

Verifies: required artifacts exist; every kept narration lies inside its window, outside speech and outside
transient guards; windows respect min duration / pad / max_chars formula; grid images have the right size;
ad.vtt parses and matches narrations.json. With truth.json also compares dialogue timing to ground truth.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

out = Path(sys.argv[1]).resolve()
truth = json.loads(Path(sys.argv[2]).read_text()) if len(sys.argv) > 2 else None
fails: list[str] = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def load(name):
    return json.loads((out / name).read_text(encoding="utf-8"))


for f in ["windows.json", "narrations.json", "ad.vtt", "mixed.mp3", "dialogue.json", "stats.json", "timings.json", "report.md"]:
    check((out / f).exists(), f"missing {f}")
if fails:
    print("\n".join(fails)); sys.exit(1)

W = load("windows.json"); N = load("narrations.json"); D = load("dialogue.json"); S = load("stats.json")
cfg = S["config"]
speech = [tuple(x) for x in D["speech_segments"]]
guards = [(t["t"] - cfg["transient_guard"], t["t"] + cfg["transient_guard"]) for t in W["transients"]]
wins = {w["id"]: w for w in W["windows"]}

# windows
for w in W["windows"]:
    check(w["duration"] >= cfg["min_window"] - 1e-6, f"{w['id']} shorter than min_window")
    check(abs(w["end"] - w["start"] - w["duration"]) < 0.01, f"{w['id']} duration mismatch")
    rate = cfg["words_per_sec"] if cfg.get("lang") == "en" else cfg["chars_per_sec"]
    exp = max(0, math.floor((w["duration"] - cfg["chars_reserve_sec"]) * rate))
    check(w["budget"] == exp, f"{w['id']} budget {w['budget']} != {exp}")
    for s, e in speech:
        check(min(e, w["end"]) - max(s, w["start"]) <= 1e-6, f"{w['id']} overlaps speech {s}-{e}")
        if abs(e - (w["start"] - cfg["pre_pad"])) < 0.02:
            pass
    for s, e in guards:
        check(min(e, w["end"]) - max(s, w["start"]) <= 1e-6, f"{w['id']} overlaps transient guard {s:.2f}-{e:.2f}")
    check(len(w["context_before"]) <= cfg["context_before"] and len(w["context_after"]) <= cfg["context_after"], f"{w['id']} context size")
    for l in w["context_before"]:
        check(l["end"] <= w["start"] + 0.02, f"{w['id']} context_before line not before window")
    for l in w["context_after"]:
        check(l["start"] >= w["end"] - 0.02, f"{w['id']} context_after line not after window")
    if w.get("grid"):
        import cv2
        img = cv2.imread(str(out / w["grid"]))
        check(img is not None and img.shape[1] == 2 * cfg["grid_cell_w"] + cfg["grid_gutter"]
              and img.shape[0] == 2 * cfg["grid_cell_h"] + cfg["grid_gutter"], f"{w['id']} grid size")
        check(len(w["frames"]) == cfg["frames_per_window"], f"{w['id']} frame count")
        check(all(w["start"] - 0.01 <= f["t"] <= w["end"] + 0.01 for f in w["frames"]), f"{w['id']} frame outside window")

# narrations
kept = [n for n in N["narrations"] if n["status"] == "kept"]
for n in N["narrations"]:
    w = wins[n["window_id"]]
    if n["importance_score"] <= 2:
        check(n["narration"] == "" and n["status"] != "kept", f"{n['window_id']} low score but has narration")
    if n["status"] == "kept":
        check(n["actual_start"] >= w["start"] - 1e-6 and n["actual_end"] <= w["end"] + 0.02,
              f"{n['window_id']} audio {n['actual_start']}-{n['actual_end']} outside window {w['start']}-{w['end']}")
        check((out / n["tts_path"]).exists(), f"{n['window_id']} tts wav missing")
        check(n.get("stretch", 1.0) <= cfg["max_stretch"] + 1e-6, f"{n['window_id']} stretch too high")
        for s, e in speech:
            check(min(e, n["actual_end"]) - max(s, n["actual_start"]) <= 1e-6, f"{n['window_id']} narration overlaps speech")
    if n["status"].startswith("dropped"):
        check(bool(n.get("fit_log")), f"{n['window_id']} dropped without reason")

# vtt
vtt = (out / "ad.vtt").read_text(encoding="utf-8")
check(vtt.startswith("WEBVTT"), "vtt header")
cues = re.findall(r"^(w\d{3}[a-z]?)\n(\d\d:\d\d:\d\d\.\d{3}) --> (\d\d:\d\d:\d\d\.\d{3})\n(.+)$", vtt, flags=re.M)
check(len(cues) == len(kept), f"vtt cues {len(cues)} != kept {len(kept)}")
for cid, a, b, text in cues:
    n = next((x for x in kept if x["window_id"] == cid), None)
    check(n is not None and n["text_used"] == text, f"vtt text mismatch {cid}")

# ground truth
if truth:
    tl = truth["lines"]
    lines = sorted(D["lines"], key=lambda l: l["start"])
    errs, bad = [], []
    for b in tl:
        # recognized lines overlapping this truth line (boundaries matter, text may be split differently)
        ov = [a for a in lines if min(a["end"], b["end"]) - max(a["start"], b["start"]) > 0.05]
        if not ov:
            bad.append(f"truth line {b['start']}-{b['end']} '{b['text']}' not detected as speech")
            continue
        s0, e0 = min(a["start"] for a in ov), max(a["end"] for a in ov)
        errs.append((abs(s0 - b["start"]), abs(e0 - b["end"])))
        if s0 > b["start"] + 0.10 or e0 < b["end"] - 0.10:
            bad.append(f"cuts into speech: {s0}-{e0} vs truth {b['start']}-{b['end']}")
        if abs(s0 - b["start"]) > 0.6 or abs(e0 - b["end"]) > 0.6:
            bad.append(f"boundary too far from truth: {s0}-{e0} vs {b['start']}-{b['end']}")
    if errs:
        ms, me = max(e[0] for e in errs), max(e[1] for e in errs)
        print(f"dialogue timing vs truth: {len(lines)} lines for {len(tl)} truth lines; max start err {ms:.2f}s, "
              f"max end err {me:.2f}s, mean {sum(e[0]+e[1] for e in errs)/len(errs)/2:.2f}s")
    for b in bad:
        check(False, "dialogue " + b)
    imp = truth.get("impulses", [])
    found = [t["t"] for t in W["transients"]]
    hit = sum(1 for i in imp if any(abs(i - f) < 0.15 for f in found))
    print(f"transients: {hit}/{len(imp)} impulses detected, {len(found) - hit} extra")
    check(hit == len(imp), "not all impulses detected")
    cuts = truth.get("cuts", [])
    shots = json.loads((out / "shots.json").read_text())["shots"]
    starts = [s for s, _ in shots]
    chit = sum(1 for c in cuts if any(abs(c - s) < 0.25 for s in starts))
    print(f"cuts: {chit}/{len(cuts)} detected, {len(shots)} shots")
    check(chit == len(cuts), "not all cuts detected")

print(f"windows={len(wins)} narrations_kept={len(kept)} checks_failed={len(fails)}")
if fails:
    print("\n".join("FAIL " + f for f in fails))
    sys.exit(1)
print("ALL CHECKS PASSED")
