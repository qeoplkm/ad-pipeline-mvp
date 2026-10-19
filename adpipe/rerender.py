"""Re-render an existing run after human edits: re-synthesize TTS, re-fit, re-mix and rewrite ad.vtt.

No model call. Used by the web UI's review table.
"""
from __future__ import annotations

import dataclasses
import time
from pathlib import Path

from . import mix as mixmod
from . import tts
from .config import Config
from .util import count_units, log, read_json, write_json

FIT_KEYS = ("variant", "text_used", "tts_raw_duration", "tts_duration", "tts_path", "actual_start", "actual_end",
            "stretch", "stretch_method", "fit_log")


def _config(stats: dict) -> Config:
    raw = stats.get("config") or {}
    names = {f.name for f in dataclasses.fields(Config)}
    return Config(**{k: v for k, v in raw.items() if k in names})


def rerender(out_dir: Path, edits: list[dict]) -> dict:
    out_dir = Path(out_dir)
    gen = read_json(out_dir / "narrations.json")
    stats = read_json(out_dir / "stats.json")
    dlg = read_json(out_dir / "dialogue.json")
    full = out_dir / "audio" / "full.wav"
    if not full.exists():
        raise RuntimeError("this run has no audio/full.wav (snapshot folder); re-render needs the original run folder")
    cfg = _config(stats)
    by_id = {e["window_id"]: e for e in edits if e.get("window_id")}
    narrs = gen["narrations"]
    changed = 0
    for n in narrs:
        e = by_id.get(n["window_id"])
        if e is not None:
            new_text = (e.get("narration") or "").strip()
            new_short = (e.get("narration_short") or "").strip()
            disabled = bool(e.get("disabled"))
            if (new_text != (n.get("text_used") or n.get("narration") or "") or new_short != (n.get("narration_short") or "")
                    or disabled != bool(n.get("disabled"))):
                changed += 1
                n.setdefault("model_narration", n.get("narration", ""))
                n["narration"], n["narration_short"], n["disabled"] = new_text, new_short, disabled
                n["edited"] = True
                n["len"] = count_units(new_text, cfg.lang)
                n["len_short"] = count_units(new_short, cfg.lang)
                n["over_limit"] = bool(new_text) and n["len"] > n.get("budget", 10 ** 6)
        for k in FIT_KEYS:
            n.pop(k, None)
        if n.get("disabled"):
            n["status"] = "disabled"
        elif n.get("narration"):
            n["status"] = "candidate"
        elif n.get("status") not in ("missing_in_response",):
            n["status"] = "filtered_low_score"
    # fit only candidates: hide disabled ones from the fitter
    hidden = {}
    for n in narrs:
        if n["status"] != "candidate" and n.get("narration"):
            hidden[n["window_id"]] = n["narration"]
            n["narration"] = ""
    t0 = time.perf_counter()
    tstats = tts.fit_all(narrs, out_dir, cfg)
    for n in narrs:
        if n["window_id"] in hidden:
            n["narration"] = hidden[n["window_id"]]
    speech = [tuple(x) for x in dlg["speech_segments"]]
    ref = out_dir / "audio" / "vocals.wav"
    d_rms = mixmod.measure_dialogue_rms(ref if ref.exists() else full, speech)
    minfo = mixmod.mix(full, narrs, out_dir, cfg, d_rms)
    n_vtt = mixmod.write_vtt(narrs, out_dir / "ad.vtt", {"provider": gen.get("provider"), "model": gen.get("model"),
                                                         "voice": cfg.voice()})
    gen.setdefault("reviews", []).append({"at": time.time(), "changed": changed})
    write_json(out_dir / "narrations.json", gen)
    stats["mix"] = dict(stats.get("mix") or {}, **minfo, vtt_cues=n_vtt)
    stats["review"] = {"edited_total": sum(1 for n in narrs if n.get("edited")), "last_changed": changed,
                       "last_at": time.time(), "rerender_seconds": round(time.perf_counter() - t0, 1)}
    write_json(out_dir / "stats.json", stats)
    log(f"rerender: {changed} changed, {minfo['kept']} narrations, {tstats['dropped']} dropped")
    return {"changed": changed, "kept": minfo["kept"], "dropped": tstats["dropped"],
            "drop_reasons": tstats["drop_reasons"], "seconds": stats["review"]["rerender_seconds"]}
