import math

from adpipe.config import Config
from adpipe.windows import compute_windows, split_long_windows


def lines(*spans):
    return [{"start": s, "end": e, "text": f"line {i}"} for i, (s, e) in enumerate(spans)]


def test_gaps_padding_and_budget():
    cfg = Config(lang="en")
    speech = [(3.0, 4.0), (10.0, 12.0)]
    wins, stats = compute_windows(speech, 20.0, [], lines(*speech), cfg)
    opening = wins[0]
    assert opening["gap_type"] == "opening" and opening["start"] == cfg.opening_lead
    assert opening["end"] == 3.0 - cfg.post_pad
    mid = next(w for w in wins if w["gap_type"] == "between")
    assert mid["start"] == 4.0 + cfg.pre_pad and mid["end"] == 10.0 - cfg.post_pad
    assert mid["budget"] == math.floor((mid["duration"] - cfg.chars_reserve_sec) * cfg.words_per_sec)
    closing = next(w for w in wins if w["gap_type"] == "closing")
    assert closing["end"] == 20.0 and closing["start"] == 12.0 + cfg.pre_pad
    assert [l["text"] for l in mid["context_before"]] == ["line 0"]
    assert [l["text"] for l in mid["context_after"]] == ["line 1"]


def test_short_gap_is_dropped():
    cfg = Config(lang="en")
    speech = [(0.0, 5.0), (6.5, 10.0)]  # 1.5 s gap -> 1.05 s after padding
    wins, stats = compute_windows(speech, 10.0, [], lines(*speech), cfg)
    assert all(w["duration"] >= cfg.min_window for w in wins)
    assert stats["pieces_too_short"] >= 1


def test_transient_guard_splits_window():
    cfg = Config(lang="en")
    speech = [(0.0, 2.0), (12.0, 14.0)]
    wins, _ = compute_windows(speech, 14.0, [{"t": 7.0, "z": 50}], lines(*speech), cfg)
    assert len(wins) == 2
    assert wins[0]["end"] == 7.0 - cfg.transient_guard and wins[1]["start"] == 7.0 + cfg.transient_guard
    assert all(w["split_by_transient"] for w in wins)


def test_chinese_budget_uses_characters():
    cfg = Config(lang="zh")
    speech = [(0.0, 1.0), (6.0, 7.0)]
    wins, _ = compute_windows(speech, 7.0, [], lines(*speech), cfg)
    w = wins[0]
    assert w["budget_unit"] == "chars" and w["max_chars"] == math.floor((w["duration"] - 0.2) * 4.5)


def test_split_long_window_at_shots():
    cfg = Config(lang="en")
    speech = [(0.0, 1.0), (31.0, 32.0)]
    wins, _ = compute_windows(speech, 32.0, [], lines(*speech), cfg)
    shots = [(0.0, 8.0), (8.0, 9.0), (9.0, 20.0), (20.0, 32.0)]
    out, n = split_long_windows(wins, shots, cfg)
    assert n == 1
    ids = [w["id"] for w in out]
    assert ids == ["w001a", "w001b", "w001c"]
    assert [w["start"] for w in out] == [wins[0]["start"], 8.0, 20.0]   # 9.0 is < min_subwindow after 8.0
    assert out[-1]["end"] == wins[0]["end"]
    assert all(w["part"][1] == 3 for w in out)


def test_short_window_not_split():
    cfg = Config(lang="en")
    w = {"id": "w001", "start": 0.0, "end": 10.0, "duration": 10.0}
    out, n = split_long_windows([w], [(0.0, 5.0), (5.0, 10.0)], cfg)
    assert n == 0 and out == [w]


def test_long_window_without_shot_cuts_is_split_by_time():
    cfg = Config(lang="en")
    w = {"id": "w007", "start": 100.0, "end": 127.0, "duration": 27.0, "budget": 0, "budget_short": 0}
    out, n = split_long_windows([w], [(90.0, 140.0)], cfg)
    assert n == 1 and [x["id"] for x in out] == ["w007a", "w007b", "w007c"]
    assert [round(x["duration"], 3) for x in out] == [9.0, 9.0, 9.0]
    assert all(x["duration"] <= cfg.long_window for x in out)
    assert out[0]["split_by"] == "shots+time"


def test_long_shot_piece_is_refined_by_time():
    cfg = Config(lang="en")
    w = {"id": "w014", "start": 248.0, "end": 285.0, "duration": 37.0, "budget": 0, "budget_short": 0}
    out, _ = split_long_windows([w], [(240.0, 253.0), (253.0, 258.0), (258.0, 300.0)], cfg)
    assert all(x["duration"] <= cfg.long_window for x in out)
    assert out[0]["start"] == 248.0 and out[-1]["end"] == 285.0
    assert [x["start"] for x in out][:3] == [248.0, 253.0, 258.0]
