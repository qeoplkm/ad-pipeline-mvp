"""Small helpers: timing, json io, timestamps, subprocess, cache dirs."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def setup_caches(root: Path = PROJECT_ROOT) -> None:
    """Keep every model download inside the project directory unless the user already chose a location."""
    cache = root / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    defaults = {
        "TORCH_HOME": cache / "torch",
        "MODELSCOPE_CACHE": cache / "modelscope",
        "HF_HOME": cache / "huggingface",
        "XDG_CACHE_HOME": cache / "xdg",
        "TOKENIZERS_PARALLELISM": "false",
    }
    for k, v in defaults.items():
        os.environ.setdefault(k, str(v))


def load_dotenv(root: Path = PROJECT_ROOT) -> list[str]:
    """Load KEY=VALUE lines from <project>/.env into os.environ (existing variables win). Returns loaded names."""
    env = root / ".env"
    loaded = []
    if not env.exists():
        return loaded
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v
            loaded.append(k)
    return loaded


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Timings:
    """Collects wall-clock durations per pipeline step."""

    def __init__(self) -> None:
        self.steps: dict[str, float] = {}
        self.notes: dict[str, str] = {}

    @contextmanager
    def step(self, name: str):
        log(f"==> {name}")
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            self.steps[name] = self.steps.get(name, 0.0) + dt
            log(f"<== {name} took {dt:.1f}s")

    def to_dict(self) -> dict:
        return {"steps": self.steps, "notes": self.notes, "total": sum(self.steps.values())}


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def read_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _split(seconds: float, per_second: int) -> tuple[int, int, int]:
    """Round to the display precision first, then split, so 59.97 s becomes 01:00.0, not 00:60.0."""
    units = round(max(0.0, float(seconds)) * per_second)
    whole, frac = divmod(units, per_second)
    return whole // 60, whole % 60, frac


def fmt_ts(seconds: float, ms: bool = True) -> str:
    """HH:MM:SS.mmm (WebVTT compatible)."""
    if ms:
        mins, s, frac = _split(seconds, 1000)
        return f"{mins // 60:02d}:{mins % 60:02d}:{s:02d}.{frac:03d}"
    mins, s, frac = _split(seconds, 10)
    return f"{mins // 60:02d}:{mins % 60:02d}:{s:02d}.{frac}"


def fmt_short(seconds: float) -> str:
    """M:SS.s - compact form for prompts and frame labels."""
    mins, s, frac = _split(seconds, 10)
    return f"{mins:02d}:{s:02d}.{frac}"


def run_cmd(cmd: list[str], quiet: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        sys.stderr.write(res.stderr[-4000:])
        raise RuntimeError(f"command failed ({res.returncode}): {' '.join(cmd[:6])} ...")
    return res


def count_chars(text: str) -> int:
    """Count speakable characters: everything except whitespace and punctuation."""
    import unicodedata
    n = 0
    for ch in text:
        if ch.isspace():
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("S"):
            continue
        n += 1
    return n


WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def count_units(text: str, lang: str) -> int:
    """Budget units: words for English, speakable characters for Chinese."""
    return count_words(text) if lang == "en" else count_chars(text)


def overlaps(a0: float, a1: float, b0: float, b1: float) -> float:
    """Length of overlap between [a0,a1] and [b0,b1]."""
    return max(0.0, min(a1, b1) - max(a0, b0))
