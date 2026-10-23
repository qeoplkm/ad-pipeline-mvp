from pathlib import Path

from adpipe.config import Config
from adpipe.dialogue import (_funasr_lines, calibrate_with_vad, lines_from_vad_and_words, merge_intervals,
                             parse_srt, uncaptioned_speech)


def test_merge_intervals():
    assert merge_intervals([(0, 1), (1.2, 2), (5, 6)], 0.3) == [(0, 2), (5, 6)]


def test_calibration_snaps_only_within_limit():
    cfg = Config()
    lines = [{"start": 5.7, "end": 7.3, "text": "a"}, {"start": 20.0, "end": 22.0, "text": "b"}]
    vad = [(6.0, 7.1), (30.0, 31.0)]
    out = calibrate_with_vad(lines, vad, cfg)["lines"]
    assert (out[0]["start"], out[0]["end"]) == (6.0, 7.1)
    assert (out[1]["start"], out[1]["end"]) == (20.0, 22.0)   # no VAD edge within 0.6 s


def test_uncaptioned_speech_is_protected():
    cfg = Config()
    lines = [{"start": 1.0, "end": 2.0, "text": "a"}]
    extra = uncaptioned_speech(lines, [(1.0, 2.0), (5.0, 6.0)], cfg)
    assert [(e["start"], e["end"]) for e in extra] == [(5.0, 6.0)]


def test_vad_boundaries_and_hallucination_drop():
    vad = [(6.0, 7.2), (8.3, 10.5)]
    words = [(5.6, 6.1, "What"), (6.1, 7.0, "now?"), (7.0, 8.4, "I"), (8.4, 10.4, "came."), (57.4, 58.8, "Thank")]
    lines = lines_from_vad_and_words(vad, words)
    assert [(l["start"], l["end"], l["text"]) for l in lines] == [(6.0, 7.2, "What now?"), (8.3, 10.5, "I came.")]


def test_funasr_lines_split_on_long_silence():
    res = {"text": "现在不是时候你听我说", "timestamp": [[11180, 11360], [11360, 11500], [11500, 11700], [11700, 11900],
                                             [11900, 12100], [12100, 12200], [24000, 24200], [24200, 24400],
                                             [24400, 24600], [24600, 24800]]}
    lines = _funasr_lines(res)
    assert [l["text"] for l in lines] == ["现在不是时候", "你听我说"]
    assert lines[1]["start"] == 24.0


def test_parse_srt(tmp_path: Path):
    p = tmp_path / "a.srt"
    p.write_text("1\n00:00:01,000 --> 00:00:02,500\n<i>Hello</i>\nthere\n\n2\n00:00:03,000 --> 00:00:04,000\n\n", encoding="utf-8")
    lines = parse_srt(p)
    assert lines == [{"start": 1.0, "end": 2.5, "text": "Hello there", "source": "srt"}]
