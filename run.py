#!/usr/bin/env python
"""Minimal Chinese audio-description pipeline.

    python run.py --video clip.mp4 --out out/ [--srt clip.srt] [--provider gemini|claude|openai|mock]

Outputs (under --out): windows.json, grids/*.jpg, narrations.json, tts/*.wav, ad.vtt, mixed.mp3 (+ ad_track.wav,
dialogue.json, scenes.json, stats.json, timings.json, report.md).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adpipe.config import Config  # noqa: E402
from adpipe.util import PROJECT_ROOT, Timings, load_dotenv, log, read_json, setup_caches, write_json  # noqa: E402


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True, help="input mp4/mkv (3-10 min Chinese clip)")
    ap.add_argument("--out", default="out", help="output directory")
    ap.add_argument("--lang", default="en", choices=["en", "zh"], help="language of the film / narration (default en)")
    ap.add_argument("--srt", help="optional subtitle file; cue times are used and calibrated with VAD")
    ap.add_argument("--characters", default=str(PROJECT_ROOT / "characters.json"))
    ap.add_argument("--provider", default="auto", choices=["auto", "gemini", "claude", "openai", "mock"])
    ap.add_argument("--model", help="override model id (default per provider, or env AD_MODEL)")
    ap.add_argument("--voice", help="edge-tts voice; default en-US-JennyNeural (en) / zh-CN-XiaoxiaoNeural (zh); "
                                    "male: en-US-GuyNeural / zh-CN-YunxiNeural")
    ap.add_argument("--device", default="cpu", help="torch device for demucs/FunASR (cpu|mps|cuda)")
    ap.add_argument("--skip-demucs", action="store_true", help="do not separate vocals (transients/VAD use the full mix)")
    ap.add_argument("--asr", default="auto", choices=["auto", "funasr", "faster-whisper", "whisperx"])
    ap.add_argument("--vad", default="auto", choices=["auto", "funasr", "silero", "none"])
    ap.add_argument("--no-vad-extra", action="store_true", help="do not block uncaptioned VAD speech")
    ap.add_argument("--reuse", action="store_true", help="reuse existing stems/dialogue/shots/LLM responses in --out")
    ap.add_argument("--max-windows", type=int, help="only process the first N windows (cheap test runs)")
    ap.add_argument("--stop-after", choices=["audio", "dialogue", "windows", "grids", "llm", "tts"])
    ap.add_argument("--clean", action="store_true", help="delete --out first")
    return ap.parse_args()


def load_characters(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    data = read_json(p)
    chars = data.get("characters", []) if isinstance(data, dict) else data
    return [c for c in chars if isinstance(c, dict) and c.get("name")]


def main():
    args = parse_args()
    setup_caches()
    loaded = load_dotenv()
    if loaded:
        log(f".env: loaded {', '.join(loaded)}")
    from adpipe import audio, dialogue, generate, mix as mixmod, scenes, tts, windows as win

    cfg = Config(lang=args.lang)
    if args.voice:
        cfg.tts_voice = args.voice
    log(f"lang={cfg.lang} voice={cfg.voice()} budget={cfg.unit_rate()} {cfg.unit_name()}/s")
    video = Path(args.video).resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")
    out = Path(args.out).resolve()
    if args.clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    reuse = args.reuse
    T = Timings()
    stats: dict = {"video": str(video), "config": cfg.to_dict(), "args": vars(args)}

    # ------------------------------------------------------------ 1. audio
    with T.step("1_extract_audio"):
        info = audio.probe_video(video)
        duration = info["duration"]
        stats["video_info"] = info
        aud = {"full": out / "audio" / "full.wav", "mono16k": out / "audio" / "mono16k.wav"}
        if not (reuse and aud["full"].exists() and aud["mono16k"].exists()):
            aud = audio.extract_audio(video, out / "audio", cfg.sr)
    log(f"video: {info['width']}x{info['height']} @ {info['fps']:.2f} fps, {duration:.1f}s")

    # ------------------------------------------------------------ 2. demucs
    with T.step("2_demucs"):
        stems = None
        if not args.skip_demucs:
            cand = {"vocals": out / "audio" / "vocals.wav", "no_vocals": out / "audio" / "no_vocals.wav"}
            if reuse and all(p.exists() for p in cand.values()):
                stems = cand
                log("demucs: reusing existing stems")
            else:
                stems = audio.separate_vocals(aud["full"], out / "audio", device=args.device)
        T.notes["demucs"] = "ok (htdemucs, two stems)" if stems else "skipped"
        stats["demucs"] = bool(stems)
    bg_wav = stems["no_vocals"] if stems else aud["full"]
    if stems:
        vad_src = audio.to_mono16k(stems["vocals"], out / "audio" / "vocals16k.wav")
    else:
        vad_src = aud["mono16k"]
    if args.stop_after == "audio":
        return finish(T, stats, out)

    # ------------------------------------------------------------ 3. dialogue
    with T.step("3_dialogue"):
        dpath = out / "dialogue.json"
        if reuse and dpath.exists():
            dlg = read_json(dpath)
            log("dialogue: reusing dialogue.json")
        else:
            vad_segs, vad_name = (None, None) if args.vad == "none" else dialogue.run_vad(vad_src, args.vad, cfg.lang)
            cal = {"moved": 0, "mean_abs_shift": 0.0}
            asr_name = None
            if args.srt:
                lines = dialogue.parse_srt(Path(args.srt))
                cal = dialogue.calibrate_with_vad(lines, vad_segs, cfg)
                lines = cal["lines"]
                source = "srt"
            else:
                lines, asr_name = dialogue.run_asr(vad_src, args.asr, args.device, cfg.lang, vad_segs)
                if lines is None:
                    sys.exit("No ASR backend available (FunASR and WhisperX both failed). Provide --srt.")
                cal = dialogue.calibrate_with_vad(lines, vad_segs, cfg)  # VAD edges are conservative; snap to them
                lines = cal["lines"]
                source = "asr"
            extra = []
            if vad_segs and stems and not args.no_vad_extra:
                extra = dialogue.uncaptioned_speech(lines, vad_segs, cfg)
            all_lines = sorted(lines + extra, key=lambda l: l["start"])
            speech = dialogue.speech_segments(all_lines, cfg)
            dlg = {"source": source, "asr_backend": asr_name, "vad_backend": vad_name,
                   "calibration": {k: v for k, v in cal.items() if k != "lines"},
                   "lines": lines, "uncaptioned_vad_segments": extra,
                   "speech_segments": [[round(s, 3), round(e, 3)] for s, e in speech]}
            write_json(dpath, dlg)
        all_lines = sorted(dlg["lines"] + dlg.get("uncaptioned_vad_segments", []), key=lambda l: l["start"])
        speech = [tuple(x) for x in dlg["speech_segments"]]
        stats["dialogue"] = {"source": dlg["source"], "asr_backend": dlg.get("asr_backend"),
                             "vad_backend": dlg.get("vad_backend"), "lines": len(dlg["lines"]),
                             "uncaptioned_vad_segments": len(dlg.get("uncaptioned_vad_segments", [])),
                             "speech_seconds": round(sum(e - s for s, e in speech), 2),
                             "calibration": dlg.get("calibration")}
        log(f"dialogue: {len(dlg['lines'])} lines ({dlg['source']}), speech {stats['dialogue']['speech_seconds']}s "
            f"of {duration:.1f}s")
    if args.stop_after == "dialogue":
        return finish(T, stats, out)

    # ------------------------------------------------------------ 4. windows
    with T.step("4_windows"):
        transients = win.detect_transients(bg_wav, cfg)
        wins, wstats = win.compute_windows(speech, duration, transients, all_lines, cfg)
        if args.max_windows:
            wins = wins[: args.max_windows]
            log(f"--max-windows: keeping the first {len(wins)} windows")
        stats["windows"] = dict(wstats, transients=len(transients), processed=len(wins))
    if args.stop_after == "windows":
        write_windows(out, wins, transients, cfg, video, duration)
        return finish(T, stats, out)

    # ------------------------------------------------------------ 5. shots / scenes / grids
    with T.step("5_shots"):
        spath = out / "shots.json"
        if reuse and spath.exists():
            shots = [tuple(x) for x in read_json(spath)["shots"]]
        else:
            shots = scenes.detect_shots(video, duration)
            write_json(spath, {"shots": [[round(s, 3), round(e, 3)] for s, e in shots]})
        stats["shots"] = len(shots)
        wins, n_split = win.split_long_windows(wins, shots, cfg)
        stats["windows"]["long_windows_split"] = n_split
        stats["windows"]["after_split"] = len(wins)
    with T.step("6_grids"):
        batches = scenes.group_scenes(shots, wins, cfg)
        grabber = scenes.FrameGrabber(video)
        gdir = out / "grids"
        gdir.mkdir(exist_ok=True)
        for w in wins:
            frames = scenes.pick_frame_times(w, shots, cfg.frames_per_window)
            w["frames"] = frames
            w["grid"] = str(scenes.make_grid(grabber, frames, gdir / f"{w['id']}.jpg", cfg))
        grabber.close()
        write_windows(out, wins, transients, cfg, video, duration)
        write_json(out / "scenes.json", {"batches": batches})
        stats["scenes"] = {"batches": len(batches), "non_empty": sum(1 for b in batches if b["window_ids"])}
    if args.stop_after == "grids":
        return finish(T, stats, out)

    # ------------------------------------------------------------ 6. LLM
    with T.step("7_llm"):
        characters = load_characters(args.characters)
        gen = generate.generate_narrations(batches, wins, all_lines, characters, args.provider, args.model,
                                           out, reuse, cfg)
        gen["voice"] = cfg.voice()
        write_json(out / "narrations.json", gen)
        narrs = gen["narrations"]
        stats["llm"] = {"provider": gen["provider"], "model": gen["model"], "calls": len(gen["calls"]),
                        "input_tokens": sum((c["usage"] or {}).get("input_tokens") or 0 for c in gen["calls"]),
                        "output_tokens": sum((c["usage"] or {}).get("output_tokens") or 0 for c in gen["calls"]),
                        "candidates": sum(1 for n in narrs if n["narration"]),
                        "filtered_low_score": sum(1 for n in narrs if n["status"] == "filtered_low_score"),
                        "missing_in_response": sum(1 for n in narrs if n["status"] == "missing_in_response"),
                        "over_limit": sum(1 for n in narrs if n.get("over_limit")),
                        "echo_or_credit_removed": sum(1 for n in narrs if n.get("removed_sentences")),
                        "meta_phrase_narrations": sum(1 for n in narrs if n.get("meta_phrases")),
                        "score_histogram": {str(k): sum(1 for n in narrs if n["importance_score"] == k) for k in range(6)}}
    if args.stop_after == "llm":
        return finish(T, stats, out)

    # ------------------------------------------------------------ 7. TTS + fit
    with T.step("8_tts"):
        tstats = tts.fit_all(narrs, out, cfg)
        write_json(out / "narrations.json", gen)
        stats["tts"] = {k: v for k, v in tstats.items() if k != "stretch_ratios"}
    if args.stop_after == "tts":
        return finish(T, stats, out)

    # ------------------------------------------------------------ 8. mix + vtt
    with T.step("9_mix"):
        ref = stems["vocals"] if stems else aud["full"]
        d_rms = mixmod.measure_dialogue_rms(ref, speech)
        minfo = mixmod.mix(aud["full"], narrs, out, cfg, d_rms)
        n_vtt = mixmod.write_vtt(narrs, out / "ad.vtt", {"provider": gen["provider"], "model": gen["model"],
                                                           "voice": cfg.voice()})
        write_json(out / "narrations.json", gen)
        non_speech = stats["windows"]["non_speech_seconds"] or 1.0
        stats["mix"] = dict(minfo, vtt_cues=n_vtt, coverage_of_non_speech=round(minfo["narration_seconds"] / non_speech, 3))
    return finish(T, stats, out)


def write_windows(out: Path, wins: list[dict], transients: list[dict], cfg: Config, video: Path, duration: float):
    rows = []
    for w in wins:
        r = dict(w)
        if r.get("grid"):
            r["grid"] = str(Path(r["grid"]).relative_to(out))
        rows.append(r)
    write_json(out / "windows.json", {
        "video": str(video), "duration": round(duration, 3),
        "lang": cfg.lang, "budget_unit": cfg.unit_name(), "budget_rate_per_sec": cfg.unit_rate(),
        "params": {k: getattr(cfg, k) for k in ("pre_pad", "post_pad", "opening_lead", "min_window", "chars_per_sec",
                                                 "words_per_sec", "chars_reserve_sec", "transient_guard", "transient_z")},
        "transients": transients, "windows": rows})


def finish(T: Timings, stats: dict, out: Path):
    write_json(out / "timings.json", T.to_dict())
    write_json(out / "stats.json", stats)
    (out / "report.md").write_text(render_report(T, stats), encoding="utf-8")
    log(f"done. total {T.to_dict()['total']:.1f}s -> {out}")
    print(render_report(T, stats))


def render_report(T: Timings, s: dict) -> str:
    L = ["## 耗时", "", "| 步骤 | 秒 |", "|---|---:|"]
    for k, v in T.steps.items():
        L.append(f"| {k} | {v:.1f} |")
    L.append(f"| **合计** | **{T.to_dict()['total']:.1f}** |")
    L += ["", "## 统计", "", "| 指标 | 值 |", "|---|---:|"]
    vi = s.get("video_info", {})
    if vi:
        L.append(f"| 视频时长 | {vi.get('duration', 0):.1f}s ({vi.get('width')}x{vi.get('height')}) |")
    L.append(f"| demucs 人声分离 | {'是' if s.get('demucs') else '否（跳过）'} |")
    d = s.get("dialogue")
    if d:
        L.append(f"| 对白来源 | {d['source']} (asr={d.get('asr_backend')}, vad={d.get('vad_backend')}) |")
        L.append(f"| 对白句数 | {d['lines']} |")
        L.append(f"| 未字幕但 VAD 检出的语音段 | {d['uncaptioned_vad_segments']} |")
        L.append(f"| 对白总时长 | {d['speech_seconds']}s |")
        c = d.get("calibration") or {}
        if c:
            L.append(f"| VAD 校准移动的字幕条数 | {c.get('moved', 0)}（平均位移 {c.get('mean_abs_shift', 0):.2f}s） |")
    w = s.get("windows")
    if w:
        L.append(f"| 对白空隙数 | {w['gaps']}（非对白总时长 {w['non_speech_seconds']}s） |")
        L.append(f"| 强瞬态数 | {w['transients']} |")
        L.append(f"| 被瞬态切分/裁剪的窗口 | {w['split_by_transient']} |")
        L.append(f"| 过短丢弃的片段（<1.5s） | {w['pieces_too_short']} |")
        L.append(f"| **可用窗口总数** | **{w['windows']}**（本次处理 {w['processed']}，合计 {w['window_seconds']}s） |")
        if w.get("long_windows_split"):
            L.append(f"| 按镜头切分的长窗口（>12s） | {w['long_windows_split']} 个 → 切分后共 {w['after_split']} 个窗口 |")
    if "shots" in s:
        L.append(f"| 镜头数 | {s['shots']} |")
    if "scenes" in s:
        L.append(f"| 场景批次（模型调用次数） | {s['scenes']['non_empty']} |")
    g = s.get("llm")
    if g:
        L.append(f"| 模型 | {g['provider']} / {g['model']}（{s.get('config', {}).get('lang')}），输入 {g['input_tokens']} / 输出 {g['output_tokens']} tokens |")
        L.append(f"| importance_score 分布 (0-5) | {g['score_histogram']} |")
        L.append(f"| 重要性 ≤2 被过滤 | {g['filtered_low_score']} |")
        L.append(f"| 模型漏答的窗口 | {g['missing_in_response']} |")
        L.append(f"| 超出字数/词数上限的解说 | {g['over_limit']} |")
        L.append(f"| 被过滤掉复述对白/致谢句子的解说 | {g.get('echo_or_credit_removed', 0)} |")
        L.append(f"| 仍含摄影术语的解说 | {g.get('meta_phrase_narrations', 0)} |")
        L.append(f"| 进入 TTS 的解说 | {g['candidates']} |")
    t = s.get("tts")
    if t:
        L.append(f"| 直接放得下 | {t['direct']} |")
        L.append(f"| 变速后放下（≤1.2x） | {t['stretched']}（平均 {t.get('stretch_mean', '-')}x，最大 {t.get('stretch_max', '-')}x，方法 {t.get('stretch_method')}） |")
        L.append(f"| 改用 narration_short | {t['used_short']} |")
        L.append(f"| 太长丢弃 | {t['dropped']} |")
        L.append(f"| TTS 失败 | {t['tts_errors']} |")
        for dr in t.get("drop_reasons", []):
            L.append(f"| &nbsp;&nbsp;丢弃 {dr['window_id']} | {dr['reason']} |")
    m = s.get("mix")
    if m:
        L.append(f"| **最终解说条数** | **{m['kept']}**（ad.vtt {m['vtt_cues']} 条，解说总时长 {m['narration_seconds']}s，"
                 f"覆盖非对白时间 {m['coverage_of_non_speech'] * 100:.0f}%） |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
