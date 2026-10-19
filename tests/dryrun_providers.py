"""Build a real request for each provider against a dead local endpoint (no key, no outbound traffic).

    python tests/dryrun_providers.py out_test

A ConnectionError means the SDK accepted every parameter and tried to send; a TypeError/validation error means
the request shape is wrong for the installed SDK version.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adpipe import generate  # noqa: E402

out = Path(sys.argv[1]).resolve()
W = json.loads((out / "windows.json").read_text(encoding="utf-8"))
D = json.loads((out / "dialogue.json").read_text(encoding="utf-8"))
S = json.loads((out / "scenes.json").read_text(encoding="utf-8"))
wins = W["windows"][:3]
for w in wins:
    w["grid"] = str(out / w["grid"])
batch = dict(S["batches"][0], window_ids=[w["id"] for w in wins])
parts = generate.build_batch_content(batch, wins, D["lines"], [], [])
print(f"request: {sum(1 for p in parts if p['type']=='text')} text parts, {sum(1 for p in parts if p['type']=='image')} images, "
      f"{sum(len(p['text']) for p in parts if p['type']=='text')} chars of text")
print("---- first text part ----"); print(parts[0]["text"][:600]); print("...")
print("---- window part ----"); print(parts[1]["text"])

os.environ["ANTHROPIC_API_KEY"] = "sk-ant-dryrun"; os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9"
os.environ["OPENAI_API_KEY"] = "sk-dryrun"; os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:9/v1"
os.environ["GEMINI_API_KEY"] = "dryrun"; os.environ["GOOGLE_GEMINI_BASE_URL"] = "http://127.0.0.1:9"
import anthropic, openai  # noqa: E402

def run(name, fn):
    try:
        fn()
        print(f"{name}: unexpectedly succeeded")
    except (anthropic.APIConnectionError, openai.APIConnectionError) as e:
        print(f"{name}: OK (request built, connection refused as expected: {type(e).__name__})")
    except Exception as e:
        msg = str(e)
        ok = any(k in msg.lower() for k in ("connect", "refused", "connection", "unreachable", "max retries"))
        print(f"{name}: {'OK (connection error)' if ok else 'PROBLEM'} -> {type(e).__name__}: {msg[:300]}")

run("claude", lambda: generate._call_claude(generate.DEFAULT_MODELS["claude"], parts))
run("openai", lambda: generate._call_openai(generate.DEFAULT_MODELS["openai"], parts))
run("gemini", lambda: generate._call_gemini(generate.DEFAULT_MODELS["gemini"], parts))
