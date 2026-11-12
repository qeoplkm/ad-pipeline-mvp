from pathlib import Path

from adpipe.mix import vtt_cue_text, write_vtt


def _narr(window_id, text, start, end=None, status="kept"):
    return {"window_id": window_id, "status": status, "actual_start": start,
            "actual_end": end if end is not None else start + 1.5, "text_used": text}


def test_vtt_cue_text_escapes_markup_and_drops_blank_lines():
    raw = "Tom & Jerry read a sign: <EXIT>.\n\nThe arrow points --> north."
    assert vtt_cue_text(raw) == "Tom &amp; Jerry read a sign: &lt;EXIT&gt;. The arrow points --&gt; north."
    assert vtt_cue_text("Thom's jaw drops.") == "Thom's jaw drops."
    assert vtt_cue_text("") == ""


def test_write_vtt_keeps_cues_parseable(tmp_path: Path):
    path = tmp_path / "ad.vtt"
    narrations = [
        _narr("w002", "Second plain line.", 5.0, 6.25),
        _narr("w001", "Tom & Jerry read a sign: <EXIT>.\n\nThe arrow points --> north.", 1.25, 3.5),
        _narr("w003", "not spoken", 0.5, status="dropped_too_long"),
    ]
    n = write_vtt(narrations, path, {"provider": "mock", "model": "m", "voice": "en-US-JennyNeural"})
    text = path.read_text(encoding="utf-8")
    assert n == 2
    assert text.startswith("WEBVTT\n")
    blocks = text.strip().split("\n\n")
    cue_blocks = [b for b in blocks if len(b.split("\n")) >= 2 and "-->" in b.split("\n")[1]]
    assert [b.split("\n")[0] for b in cue_blocks] == ["w001", "w002"]
    body = cue_blocks[0].split("\n", 2)[2]
    assert "-->" not in body
    assert body == "Tom &amp; Jerry read a sign: &lt;EXIT&gt;. The arrow points --&gt; north."
    assert cue_blocks[1].split("\n") == ["w002", "00:00:05.000 --> 00:00:06.250", "Second plain line."]
    assert "not spoken" not in text
