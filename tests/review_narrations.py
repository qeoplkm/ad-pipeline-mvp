"""Readable dump of narrations next to their windows, for manual quality review against grids/*.jpg.

    python tests/review_narrations.py out/
"""
import json
import sys
from pathlib import Path

out = Path(sys.argv[1]).resolve()
W = {w["id"]: w for w in json.loads((out / "windows.json").read_text(encoding="utf-8"))["windows"]}
N = json.loads((out / "narrations.json").read_text(encoding="utf-8"))
print(f"provider={N['provider']} model={N['model']} voice={N.get('voice')}")
for n in N["narrations"]:
    w = W[n["window_id"]]
    cb = " / ".join(l["text"] for l in w["context_before"][-2:]) or "-"
    ca = " / ".join(l["text"] for l in w["context_after"]) or "-"
    head = (f"{n['window_id']} {w['start']:6.1f}-{w['end']:6.1f}s ({w['duration']:4.1f}s, max {w['budget']} {w['budget_unit']}) "
            f"score={n['importance_score']} status={n['status']}")
    if n["status"] == "kept":
        head += f" variant={n['variant']} stretch={n.get('stretch', 1.0):.2f} dur={n['tts_duration']}s"
    print(head)
    print(f"    before: {cb}\n    after: {ca}")
    if n["narration"]:
        print(f"    narration[{n['len']}]: {n['narration']}\n    short[{n['len_short']}]: {n['narration_short']}")
    if n.get("fit_log"):
        print(f"    fit: {n['fit_log']}")
