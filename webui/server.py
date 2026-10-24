"""Local web UI for testing the AD pipeline.

    .venv/bin/python -m webui.server            # http://127.0.0.1:8765

Binds to 127.0.0.1 only. Runs are executed as `run.py` subprocesses into jobs/<id>/; existing output folders in the
project root (any folder with narrations.json + windows.json) are listed too, so earlier results can be opened.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"
INPUT = ROOT / "input"
STATIC = Path(__file__).resolve().parent / "static"
PY = sys.executable
JOBS.mkdir(exist_ok=True)
INPUT.mkdir(exist_ok=True)

app = FastAPI(title="ad-pipeline-mvp")
_procs: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()
ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


# ---------------------------------------------------------------- run registry
def _run_dir(run_id: str) -> Path:
    if not ID_RE.match(run_id):
        raise HTTPException(400, "bad run id")
    if run_id.startswith("job-"):
        d = JOBS / run_id
    else:
        d = ROOT / run_id
        if not d.name.startswith("out"):
            raise HTTPException(404, "unknown run")
    if not d.is_dir():
        raise HTTPException(404, "unknown run")
    return d


def _out_dir(run_id: str) -> Path:
    d = _run_dir(run_id)
    return d / "out" if run_id.startswith("job-") else d


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _status(run_id: str) -> str:
    if not run_id.startswith("job-"):
        return "done"
    meta = _read(JOBS / run_id / "job.json", {})
    with _lock:
        p = _procs.get(run_id)
    if p is not None and p.poll() is None:
        return meta.get("phase", "running")
    return meta.get("status", "unknown")


def _summary(run_id: str) -> dict:
    out = _out_dir(run_id)
    stats = _read(out / "stats.json", {}) or {}
    meta = _read(_run_dir(run_id) / "job.json", {}) if run_id.startswith("job-") else {}
    video = meta.get("video") or stats.get("video")
    mix = stats.get("mix") or {}
    llm = stats.get("llm") or {}
    timings = _read(out / "timings.json", {}) or {}
    return {
        "id": run_id,
        "kind": "job" if run_id.startswith("job-") else "folder",
        "status": _status(run_id),
        "video_name": Path(video).name if video else None,
        "lang": (stats.get("config") or {}).get("lang") or meta.get("lang"),
        "provider": llm.get("provider") or meta.get("provider"),
        "model": llm.get("model") or meta.get("model"),
        "narrations": mix.get("kept"),
        "windows": (stats.get("windows") or {}).get("after_split") or (stats.get("windows") or {}).get("windows"),
        "duration": (stats.get("video_info") or {}).get("duration"),
        "total_seconds": timings.get("total"),
        "created": meta.get("created") or (out.stat().st_mtime if out.exists() else None),
        "has_mix": (out / "mixed.mp3").exists(),
    }


@app.get("/api/runs")
def list_runs():
    ids = [d.name for d in JOBS.iterdir() if d.is_dir() and d.name.startswith("job-")]
    ids += [d.name for d in ROOT.iterdir() if d.is_dir() and d.name.startswith("out")
            and (d / "narrations.json").exists() and (d / "windows.json").exists()]
    rows = [_summary(i) for i in ids]
    rows.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return rows


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    s = _summary(run_id)
    if run_id.startswith("job-"):
        s["job"] = _read(JOBS / run_id / "job.json", {})
    return s


@app.get("/api/runs/{run_id}/log", response_class=PlainTextResponse)
def get_log(run_id: str, offset: int = 0):
    d = _run_dir(run_id)
    log = d / "run.log" if run_id.startswith("job-") else None
    if not log or not log.exists():
        return PlainTextResponse("", headers={"X-Offset": "0"})
    data = log.read_bytes()
    chunk = data[offset:].decode("utf-8", errors="replace")
    return PlainTextResponse(chunk, headers={"X-Offset": str(len(data))})


# ---------------------------------------------------------------- inputs and launching
@app.get("/api/inputs")
def list_inputs():
    rows = []
    for v in sorted(INPUT.glob("*")):
        if v.suffix.lower() not in (".mp4", ".mkv", ".mov", ".webm"):
            continue
        srts = sorted(p.name for p in INPUT.glob(v.stem + "*.srt"))
        rows.append({"name": v.name, "size_mb": round(v.stat().st_size / 1e6, 1), "srts": srts})
    return rows


@app.get("/api/providers")
def providers():
    from adpipe.util import load_dotenv
    load_dotenv(ROOT)
    have = {
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "claude": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
        "mock": True,
    }
    return {"available": have, "default_model": os.environ.get("AD_MODEL", "")}


def _safe_name(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "upload"
    return name


def _watch(run_id: str, proc: subprocess.Popen, log_path: Path):
    meta_path = JOBS / run_id / "job.json"
    while proc.poll() is None:
        try:
            tail = log_path.read_bytes()[-4000:].decode("utf-8", errors="replace")
            steps = re.findall(r"==> (\S+)", tail)
            if steps:
                meta = _read(meta_path, {})
                meta["phase"] = steps[-1]
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except Exception:
            pass
        time.sleep(1.0)
    meta = _read(meta_path, {})
    meta["status"] = "done" if proc.returncode == 0 else "failed"
    meta["returncode"] = proc.returncode
    meta["finished"] = time.time()
    meta.pop("phase", None)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    with _lock:
        _procs.pop(run_id, None)


@app.post("/api/runs")
async def create_run(
    video_name: str = Form(""),
    srt_name: str = Form(""),
    video_file: UploadFile | None = File(None),
    srt_file: UploadFile | None = File(None),
    lang: str = Form("en"),
    provider: str = Form("auto"),
    model: str = Form(""),
    voice: str = Form(""),
    characters: str = Form(""),
    max_windows: int = Form(0),
    density: float = Form(1.0),
    skip_demucs: bool = Form(False),
):
    if lang not in ("en", "zh") or provider not in ("auto", "openai", "claude", "gemini", "mock"):
        raise HTTPException(400, "bad lang/provider")
    # resolve or store the video
    if video_file is not None and video_file.filename:
        dest = INPUT / _safe_name(video_file.filename)
        with open(dest, "wb") as f:
            shutil.copyfileobj(video_file.file, f)
        video = dest
    elif video_name:
        video = INPUT / _safe_name(video_name)
    else:
        raise HTTPException(400, "choose or upload a video")
    if not video.exists():
        raise HTTPException(400, f"video not found: {video.name}")
    srt = None
    if srt_file is not None and srt_file.filename:
        srt = INPUT / _safe_name(srt_file.filename)
        with open(srt, "wb") as f:
            shutil.copyfileobj(srt_file.file, f)
    elif srt_name:
        srt = INPUT / _safe_name(srt_name)
        if not srt.exists():
            raise HTTPException(400, "subtitle not found")

    run_id = "job-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    jd = JOBS / run_id
    jd.mkdir(parents=True)
    cmd = [PY, str(ROOT / "run.py"), "--video", str(video), "--out", str(jd / "out"), "--lang", lang,
           "--provider", provider]
    if srt:
        cmd += ["--srt", str(srt)]
    if model.strip():
        cmd += ["--model", model.strip()]
    if voice.strip():
        cmd += ["--voice", voice.strip()]
    if density and 0.3 <= density < 1.0:
        cmd += ["--density", f"{density:.2f}"]
    if max_windows and max_windows > 0:
        cmd += ["--max-windows", str(int(max_windows))]
    if skip_demucs:
        cmd.append("--skip-demucs")
    if characters.strip():
        try:
            chars = json.loads(characters)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"characters is not valid JSON: {e}")
        if isinstance(chars, list):
            chars = {"characters": chars}
        (jd / "characters.json").write_text(json.dumps(chars, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd += ["--characters", str(jd / "characters.json")]
    meta = {"id": run_id, "created": time.time(), "video": str(video), "srt": str(srt) if srt else None,
            "lang": lang, "density": density, "provider": provider, "model": model or None, "voice": voice or None,
            "status": "running", "cmd": [Path(c).name if i == 0 else c for i, c in enumerate(cmd)]}
    (jd / "job.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log_path = jd / "run.log"
    logf = open(log_path, "wb")
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT, env=env)
    with _lock:
        _procs[run_id] = proc
    threading.Thread(target=_watch, args=(run_id, proc, log_path), daemon=True).start()
    return {"id": run_id}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    with _lock:
        p = _procs.get(run_id)
    if p is None or p.poll() is not None:
        raise HTTPException(400, "not running")
    p.terminate()
    return {"ok": True}


# ---------------------------------------------------------------- review: edit + re-render
_rerender: dict[str, dict] = {}


@app.post("/api/runs/{run_id}/narrations")
async def save_narrations(run_id: str, payload: dict):
    out = _out_dir(run_id)
    if _status(run_id) != "done":
        raise HTTPException(400, "run is not finished")
    if _rerender.get(run_id, {}).get("state") == "running":
        raise HTTPException(409, "a re-render is already running")
    edits = payload.get("edits") or []
    if not isinstance(edits, list):
        raise HTTPException(400, "edits must be a list")
    _rerender[run_id] = {"state": "running", "started": time.time()}

    def work():
        from adpipe.rerender import rerender
        try:
            res = rerender(out, edits)
            _rerender[run_id] = {"state": "done", "result": res, "finished": time.time()}
        except Exception as e:  # surfaced to the UI
            _rerender[run_id] = {"state": "failed", "error": f"{type(e).__name__}: {e}"}

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


@app.get("/api/runs/{run_id}/rerender")
def rerender_status(run_id: str):
    _out_dir(run_id)
    return _rerender.get(run_id, {"state": "idle"})


# ---------------------------------------------------------------- data and files
@app.get("/api/runs/{run_id}/data")
def run_data(run_id: str):
    out = _out_dir(run_id)
    return {
        "windows": (_read(out / "windows.json", {}) or {}).get("windows", []),
        "narrations": (_read(out / "narrations.json", {}) or {}).get("narrations", []),
        "dialogue": (_read(out / "dialogue.json", {}) or {}).get("lines", []),
        "stats": _read(out / "stats.json", {}) or {},
        "timings": _read(out / "timings.json", {}) or {},
        "report": (out / "report.md").read_text(encoding="utf-8") if (out / "report.md").exists() else "",
    }


@app.get("/api/runs/{run_id}/cues")
def run_cues(run_id: str):
    """Timed narration cues for the browser extension: when each clip plays, its text and its audio file."""
    out = _out_dir(run_id)
    gen = _read(out / "narrations.json", {}) or {}
    stats = _read(out / "stats.json", {}) or {}
    cues = []
    for n in gen.get("narrations", []):
        if n.get("status") != "kept" or not n.get("tts_path"):
            continue
        cues.append({"id": n["window_id"], "start": n["actual_start"], "end": n["actual_end"],
                     "text": n["text_used"], "audio": f"/files/{run_id}/{n['tts_path']}"})
    cues.sort(key=lambda c: c["start"])
    cfg = stats.get("config") or {}
    return {"run": run_id, "lang": cfg.get("lang", "en"), "duck_db": cfg.get("duck_db", -9.0),
            "duck_ramp": cfg.get("duck_ramp", 0.15), "duration": (stats.get("video_info") or {}).get("duration"),
            "video_name": Path(stats.get("video") or "").name, "cues": cues}


@app.get("/files/{run_id}/{path:path}")
def run_file(run_id: str, path: str):
    out = _out_dir(run_id).resolve()
    target = (out / path).resolve()
    if out not in target.parents or not target.is_file():
        raise HTTPException(404, "not found")
    if target.suffix in (".json",) and target.name.endswith("request.json"):
        raise HTTPException(403, "not served")
    return FileResponse(target, headers={"Cache-Control": "no-store"})


@app.get("/video/{run_id}")
def run_video(run_id: str):
    out = _out_dir(run_id)
    stats = _read(out / "stats.json", {}) or {}
    meta = _read(_run_dir(run_id) / "job.json", {}) if run_id.startswith("job-") else {}
    v = meta.get("video") or stats.get("video")
    if not v:
        raise HTTPException(404, "no video recorded")
    p = Path(v).resolve()
    if ROOT.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "video not available")
    return FileResponse(p)


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/static/{name}")
def static(name: str):
    p = (STATIC / name).resolve()
    if STATIC.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


def main():
    import uvicorn
    port = int(os.environ.get("AD_UI_PORT", "8765"))
    print(f"AD pipeline UI: http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
