# ad-pipeline-mvp

**Automatic audio description (AD) for film clips, as a validation prototype.**

Audio description is the narration that tells blind and low-vision viewers what is happening on screen, spoken in the gaps between lines of dialogue. This project runs the whole loop on a 3–10 minute clip:

dialogue gaps → key-frame grids → multimodal LLM writes the description → text-to-speech → time alignment → ducked mix

It produces a listening file (`mixed.mp3`) for testing with blind users and a WebVTT track (`ad.vtt`) for a side-loaded audio track.

**This is a prototype, not a product.** It exists to answer one question before anything is built: *is unreviewed, machine-written description good enough to be useful?*

English is the default (`--lang en`), aimed at US viewers: faster-whisper for dialogue, word budgets (2.6 words/s), a prompt following the DCMP Description Key and ACB practice, and the `en-US-JennyNeural` voice. `--lang zh` switches to Mandarin (FunASR, 4.5 characters/s, Chinese prompt, `zh-CN-XiaoxiaoNeural`). Everything else is shared.

## Quick start

Requires Python 3.12 and `ffmpeg` on the PATH. Runs on CPU (tested on an Apple M4 Pro).

```bash
git clone https://github.com/qeoplkm/ad-pipeline-mvp.git
cd ad-pipeline-mvp
python3.12 -m venv .venv
.venv/bin/pip install uv
.venv/bin/uv pip install -e .

cp .env.example .env      # then put ONE model API key in .env (never commit it; .env is git-ignored)

.venv/bin/python run.py --video clip.mp4 --out out/ [--srt clip.srt]             # English (default)
.venv/bin/python run.py --lang zh --video clip.mp4 --out out/ [--srt clip.srt]   # Mandarin
```

API keys are read only from the environment, or from a local `.env` file that `run.py` loads into the environment. Real environment variables take precedence.

| Provider | Key | Default model |
|---|---|---|
| `--provider gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `gemini-2.5-pro` |
| `--provider claude` | `ANTHROPIC_API_KEY` | `claude-opus-5-5` |
| `--provider openai` | `OPENAI_API_KEY` | `gpt-5` |

Useful options:

- `--provider auto` (default) picks the first provider with a key. Override the model with `--model` or `AD_MODEL`.
- `--provider mock` skips the LLM and uses canned text, so you can test the audio side with no key.
- `--srt` uses subtitle timing, with cue edges snapped to detected speech. Without it the pipeline transcribes: faster-whisper `large-v3-turbo` for English, FunASR paraformer-zh for Mandarin, WhisperX as a fallback (`pip install -e .[whisperx]`).
- `--reuse` reuses stems, dialogue, shots, LLM responses and TTS already in `--out`. LLM responses are reused only if the request is byte-identical.
- `--max-windows N` processes only the first N windows, for cheap API tests. `--stop-after windows|grids|llm|tts` stops early for debugging.
- `characters.json` is a hand-filled cast list (name + visible description). Leave it empty and the model uses consistent visible identifiers ("the woman in the red dress").
- Model weights (demucs, FunASR, Whisper) download into the project's `.cache/`.

## Outputs

| File | Contents |
|---|---|
| `windows.json` | Narration windows: start, end, duration, budget (`max_words` / `max_chars`), the 5 dialogue lines before and 2 after, scene, frame times, detected sound-effect transients |
| `grids/*.jpg` | One 2x2 key-frame grid per window, each cell stamped with its index and timestamp |
| `narrations.json` | Per window: `importance_score`, `narration`, `narration_short`, plus the fitting result (stretch ratio, short version used, or dropped and why) |
| `tts/*.wav` | Synthesized, trimmed, aligned narration clips |
| `ad.vtt` | WebVTT timeline; cue id = window id, times = when the narration actually plays |
| `mixed.mp3` | Listening file: original audio ducked −9 dB under narration with 150 ms ramps |
| `ad_track.wav` | Narration-only track of the same length (the side-loaded-track form) |
| `dialogue.json`, `shots.json`, `scenes.json` | Intermediate results |
| `stats.json`, `timings.json`, `report.md` | Statistics and per-step timings |

## How it works

1. **Audio.** ffmpeg extracts the soundtrack. demucs `htdemucs` splits it into vocals and background, called through the API and saved as float32. The CLI's default clip handling rescales a whole stem whenever one sample clips, which once dropped the vocals by 15 dB.
2. **Dialogue timing.** With subtitles, each cue edge snaps to the nearest voice-activity boundary within 0.6 s. Without subtitles, English uses faster-whisper for the words and the VAD speech segments for line boundaries. Whisper's own word times stretch across silences, and it hallucinates "Thank you." over music, so words more than 1 s from detected speech are dropped. Mandarin uses FunASR, rebuilding sentences from token timestamps. Speech the VAD finds on the vocal stem without a caption is also protected.
3. **Windows.** Gaps between dialogue lines get 0.2 s of padding after a line and 0.25 s before the next. A window must be at least 1.5 s. Budget = floor((duration − 0.2) × rate). Strong onsets on the background stem (robust z-score ≥ 8, at most 3 per minute) get a ±0.4 s guard that narration may not cover. Windows longer than 12 s are split at shot boundaries (pieces ≥ 3 s), so each description plays over the shots it describes.
4. **Visuals.** PySceneDetect `AdaptiveDetector` finds the shots. Each window gets 4 frames spread across its shots, tiled into a 2x2 grid with OpenCV.
5. **Generation.** Shots are grouped into ~90 s scenes. Each scene of up to 8 windows is **one** model call, containing every grid, the scene's timed dialogue, each window's time, budget and surrounding lines, and the descriptions already played. The model returns JSON `{importance_score, narration, narration_short}` under a schema, and windows scoring 2 or less stay silent. A post-filter removes sentences that read out on-screen text matching nearby dialogue, and production credits.
6. **TTS and fitting.** edge-tts synthesizes each line, `librosa.effects.trim` removes silence, and the clip is fitted into its window: as is, or time-stretched up to 1.2x, or replaced by the short version, or dropped with a logged reason.
7. **Mix.** The original audio is ducked −9 dB under each narration with 150 ms linear ramps. Narration is loudness-matched to the dialogue, summed, soft-limited, and exported as `mixed.mp3`, `ad_track.wav` and `ad.vtt`.

## Self-tests

```bash
.venv/bin/python tests/make_synthetic_clip.py tests/synthetic_en en   # 75 s clip: TTS dialogue, impulse sound effects, hard cuts
.venv/bin/python run.py --video tests/synthetic_en/clip.mp4 --out out_test_en --provider mock --clean
.venv/bin/python tests/check_outputs.py out_test_en tests/synthetic_en/truth.json
.venv/bin/python tests/listen_check.py out_test_en                    # re-transcribes mixed.mp3
```

`check_outputs.py` verifies that every narration sits inside its window, never overlaps dialogue or a transient guard, that budgets follow the formula, that grids and `ad.vtt` are consistent, and, given ground truth, that dialogue timing, impulses and cuts are detected. On the English synthetic clip: dialogue edges within 0.13 s of truth, 4/4 impulses, 7/7 cuts, all checks pass. The Mandarin synthetic clip passes on both the subtitle and the ASR path.

`listen_check.py` is the "listening" proxy. It transcribes `mixed.mp3`, checks each narration is intelligible at the time `ad.vtt` says, and checks per dialogue line that the mix left its level untouched.

## Results: *Tears of Steel*, first 8 minutes (English)

Clip: Blender Foundation's open movie *Tears of Steel* (CC-BY 3.0), 0:00–8:00 at 1280x534, with the official English subtitles (76 cues). Model `gpt-6-luna` (OpenAI), voice `en-US-JennyNeural`. The cast list holds only the three characters verifiable from frames and dialogue: Thom (young and old), Celia and Barley.

```bash
.venv/bin/python run.py --video tears_of_steel_0-8min.mp4 --srt tears_of_steel_0-8min.en.srt --out out --provider openai --clean
```

Full run, CPU, models cached:

| Step | Seconds |
|---|---:|
| demucs separation | 104 |
| dialogue (subtitles + VAD) | 1 |
| windows, shots, grids | 10 |
| LLM (6 calls) | 120 |
| TTS + mix | 14 |
| **Total** | **250** |

Without subtitles, faster-whisper transcribes the 480 s in 107 s with clean punctuation. It drops 16 hallucinated words over music and yields 29 windows and 26 narrations, all checks passing. ASR speech is tighter than subtitle timing: 97% of it falls inside subtitle speech.

### First version (v1) and the three most obvious problems

v1 produced 33 windows and 30 narrations. All fit without stretching, all 30 were intelligible on re-transcription, and all 76 dialogue lines were untouched by the mix (worst level change −0.3 dB). Checked against the grids, the descriptions were factually accurate, characters were named consistently, and title cards ("40 YEARS LATER", "TEARS OF STEEL") were read.

The problems, found by checking each narration against its grid and re-transcribing the mix:

1. **In-scene text that repeats dialogue was read aloud.** The film projects lines as magenta text inside the scene, and the model read them ("Magenta text reads, 'Look Celia, we have to follow our passions...'"), which repeats the dialogue.
2. **Long windows became one long paragraph played from the window start.** Windows of 30–37 s spanning 3–4 shots became 50+ word paragraphs read for 15–19 s, so description and picture drifted apart by up to ten seconds.
3. **Low-value text and camera jargon slipped in.** The funding credit was read verbatim, and phrases like "Playback jumps…" and "The image fades to…" are meta language the DCMP guidelines avoid.

### Optimized version (v2)

Three changes: long windows split at shot boundaries; a filter that drops quoted or "Text:" sentences matching nearby dialogue (≥ 0.6 similarity) while keeping story text such as "Celia, I love your robot hand"; and a tighter prompt (no credits, a list of banned meta phrases, no need to fill the budget).

| Metric | v1 | v2 |
|---|---:|---:|
| Windows (after splitting) | 33 | 44 (5 long windows split) |
| Narrations played | 30 | 42 |
| Longest single narration | 19.6 s | 9.9 s |
| Narrations with camera jargon | 7 | 0 |
| Narrations repeating dialogue | 3 | 0 |
| Credits read aloud | 1 | 0 |
| Over the word budget (still fit) | 4 | 5 |
| Intelligible on re-transcription | 30 / 30 | 42 / 42 |
| Dialogue lines untouched by the mix | 76 / 76 | 76 / 76 |

Example: window w011 (153–191 s) was one 51-word, 19 s paragraph. In v2 it is four cues, each on its own shot: a figure descends on a rope; Thom rubs his shoulder and crosses the room; Thom and a dark-haired woman pause in a warmly lit room; Thom steps back out to face a towering machine. In this run the model followed the new rules on its own, so the dialogue-echo filter is a safety net. Unit tests cover v1's three real cases.

Cost: about 65k input and 13k output tokens per 8-minute run in v2.

**Still open:** one 27 s window has no shot cut long enough to split on and needs a duration-based fallback. "Don't fill the budget" barely changed the total word count, so whether narration feels crowded needs real listeners.

### What this says about the core question

Factual accuracy, timing and never talking over dialogue can be guaranteed automatically. Style and editorial choices cannot yet: what to skip, which on-screen text matters, how dense to be. The next step is listening sessions with blind and low-vision viewers, comparing this output against professional AD.

## Earlier experiment: Mandarin

An earlier run used a 5-minute Mandarin-dubbed clip of a commercial animated film (not redistributed here) on the ASR path. Findings that carry over to any language:

- **Who is who depends on the cast list.** Without one, the model invents labels such as "the small one in brown" for a named mentor, and listeners cannot map them. With a cast list, 16 of 20 narrations named characters correctly. Back views and partial shots were still missed, and names drifted.
- **Crowd noise eats the windows.** Cheering and shouting count as speech, so only 22 windows (98 s) survived in 295 s. Key events landed in 2–3 s gaps with budgets of 7–12 characters, where descriptions became telegraphic and sometimes wrong. This needs a product decision on whether narration may play over crowd noise.

## Known engineering limits

- Narration may start 0.5 s into the clip (`Config.opening_lead`); whether to describe opening title cards early needs listening tests.
- VAD edges are conservative by 0.2–0.3 s, so short gaps of 2–3 s fall below the 1.5 s minimum.
- Without the `rubberband` CLI, time-stretching falls back to ffmpeg `atempo`, which sounds strained near 1.2x. Re-synthesizing at a faster TTS rate would be better.
- Measuring dialogue intelligibility with Whisper on the mixed track is unreliable: its timestamps drift when narration and dialogue alternate. `listen_check.py` uses per-line level comparison instead.

## License

MIT for the code. Test clips are not included. *Tears of Steel* is © Blender Foundation, CC-BY 3.0 (mango.blender.org).
