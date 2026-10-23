"""Pipeline constants. All timing values are seconds unless noted."""
from __future__ import annotations

from dataclasses import dataclass, asdict


VOICES = {"en": "en-US-JennyNeural", "zh": "zh-CN-XiaoxiaoNeural"}


@dataclass
class Config:
    lang: str = "en"               # "en" (words budget, faster-whisper ASR) or "zh" (chars budget, FunASR)

    # --- window computation ---
    pre_pad: float = 0.20          # gap after a dialogue line ends before narration may start
    post_pad: float = 0.25         # gap before the next dialogue line starts
    opening_lead: float = 0.5      # do not start narrating before this time at the very beginning
    min_window: float = 1.5        # minimum net window duration
    long_window: float = 12.0      # windows longer than this are split at shot boundaries
    min_subwindow: float = 3.0     # sub-windows shorter than this are merged into a neighbour
    chars_per_sec: float = 4.5     # zh: Mandarin TTS comfortable rate (characters / s)
    words_per_sec: float = 2.6     # en: ~155 wpm, comfortable AD narration rate (words / s)
    chars_reserve_sec: float = 0.2 # budget = floor((duration * density - 0.2) * rate)
    density: float = 1.0           # share of each gap narration may fill (1.0 = all of it, 0.6 = leave ~40% silent)
    short_ratio: float = 0.6       # narration_short budget = floor(max_chars * 0.6)
    context_before: int = 5        # dialogue lines of context before a window
    context_after: int = 2         # dialogue lines of context after a window
    speech_merge_gap: float = 0.30 # speech segments closer than this are merged
    vad_max_shift: float = 0.60    # how far VAD may move an SRT cue boundary
    vad_extra_min: float = 0.30    # uncaptioned VAD speech shorter than this is ignored

    # --- transients (strong sound effects on the background stem) ---
    transient_guard: float = 0.4   # +/- seconds around a transient that narration must not cover
    transient_min_gap: float = 1.0 # minimum spacing between detected transients
    transient_z: float = 8.0       # robust z-score threshold on onset strength
    transient_per_min: float = 3.0 # hard cap on transients per minute (keep the strongest)

    # --- scenes / frames ---
    scene_target_sec: float = 90.0 # group consecutive shots into ~this long "scenes" for batching
    scene_max_windows: int = 8     # split a scene batch if it has more windows than this
    frames_per_window: int = 4
    grid_cell_w: int = 640
    grid_cell_h: int = 360
    grid_gutter: int = 4
    jpeg_quality: int = 85

    # --- TTS / alignment ---
    tts_voice: str = ""            # empty -> VOICES[lang]
    tts_rate: str = "+0%"
    tts_concurrency: int = 4
    trim_top_db: float = 35.0
    max_stretch: float = 1.2
    fit_margin: float = 0.05       # seconds kept free at the end of a window

    # --- mix ---
    sr: int = 44100
    duck_db: float = -9.0
    duck_ramp: float = 0.150
    narration_gain_db: float = 1.0 # narration RMS relative to measured dialogue RMS
    mp3_bitrate: str = "192k"

    def budget_for(self, duration: float) -> int:
        return max(0, int((duration * self.density - self.chars_reserve_sec) * self.unit_rate() // 1))

    def unit_rate(self) -> float:
        return self.words_per_sec if self.lang == "en" else self.chars_per_sec

    def unit_name(self) -> str:
        return "words" if self.lang == "en" else "chars"

    def voice(self) -> str:
        return self.tts_voice or VOICES.get(self.lang, VOICES["en"])

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tts_voice"] = self.voice()
        return d
