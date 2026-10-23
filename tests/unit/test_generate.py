from adpipe.generate import build_batch_content, clean_narration, _normalize
from adpipe.util import count_units

CTX = ["Look Celia, we have to follow our passions;", "...and I just want to be awesome in space."]


def test_removes_onscreen_text_that_repeats_dialogue():
    text = ('Young Thom faces Celia on the canal. Magenta text reads, "Look Celia, we have to follow our passions..."')
    kept, removed = clean_narration(text, CTX)
    assert kept == "Young Thom faces Celia on the canal."
    assert len(removed) == 1


def test_whole_narration_can_be_an_echo():
    kept, removed = clean_narration('Text: "...and I just want to be awesome in space."', CTX)
    assert kept == "" and removed


def test_keeps_story_text_that_differs_from_dialogue():
    text = 'Her robotic hand rises; text reads, "Celia, I love your robot hand." Thom’s jaw drops.'
    kept, removed = clean_narration(text, CTX)
    assert kept == text and removed == []


def test_keeps_title_cards():
    kept, removed = clean_narration("Text: 40 YEARS LATER. A hand taps a glowing console.", CTX)
    assert "40 YEARS LATER" in kept and removed == []


def test_removes_funding_credit():
    kept, removed = clean_narration("A rocket rises. Text: This film is supported by the Netherlands Film Fund.", [])
    assert kept == "A rocket rises." and len(removed) == 1


def test_count_units():
    assert count_units("Thom's robot-hand rises.", "en") == 3
    assert count_units("阿宝摔倒了。", "zh") == 5


def _window(i, budget=10):
    return {"id": f"w{i:03d}", "start": 0.0, "end": 5.0, "duration": 5.0, "budget": budget, "budget_short": 6,
            "budget_unit": "words", "context_before": [], "context_after": [], "grid": "g.jpg"}


def test_low_score_is_silenced_and_missing_is_flagged():
    wins = [_window(1), _window(2), _window(3)]
    obj = {"windows": [
        {"window_id": "w001", "importance_score": 2, "narration": "Nothing new.", "narration_short": "x"},
        {"window_id": "w002", "importance_score": 4, "narration": "Po falls off the stairs.", "narration_short": ""},
    ]}
    recs = {r["window_id"]: r for r in _normalize({"id": "s01"}, wins, obj, "en")}
    assert recs["w001"]["narration"] == "" and recs["w001"]["status"] == "filtered_low_score"
    assert recs["w002"]["status"] == "candidate" and recs["w002"]["narration_short"] == "Po falls off the stairs."
    assert recs["w003"]["status"] == "missing_in_response"


def test_prompt_includes_budget_and_part_hint():
    w = _window(1)
    w.update({"id": "w001b", "part": [2, 3]})
    parts = build_batch_content({"start": 0, "end": 60}, [w], [], [], [], "en")
    text = "\n".join(p["text"] for p in parts if p["type"] == "text")
    assert "narration limit: 10 words" in text
    assert "Part 2 of 3" in text
    assert sum(1 for p in parts if p["type"] == "image") == 1
