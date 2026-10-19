"""Shot detection (PySceneDetect AdaptiveDetector), scene batching, keyframe picking and 2x2 grids."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .config import Config
from .util import fmt_short, log, overlaps


def detect_shots(video: Path, duration: float) -> list[tuple[float, float]]:
    from scenedetect import AdaptiveDetector, detect
    scene_list = detect(str(video), AdaptiveDetector(), show_progress=False)
    shots = [(float(s.get_seconds()), float(e.get_seconds())) for s, e in scene_list]
    shots = [(s, e) for s, e in shots if e > s]
    if not shots:
        shots = [(0.0, duration)]
    # make sure the list covers [0, duration]
    if shots[0][0] > 0.0:
        shots.insert(0, (0.0, shots[0][0]))
    if shots[-1][1] < duration - 0.05:
        shots.append((shots[-1][1], duration))
    log(f"shots: {len(shots)} (AdaptiveDetector)")
    return shots


def group_scenes(shots: list[tuple[float, float]], windows: list[dict], cfg: Config) -> list[dict]:
    """Group consecutive shots into ~scene_target_sec scenes (cut only at shot boundaries), then
    assign windows by midpoint and split any scene with too many windows into batches."""
    scenes: list[dict] = []
    cur_start = shots[0][0]
    for i, (s, e) in enumerate(shots):
        last = i == len(shots) - 1
        if e - cur_start >= cfg.scene_target_sec or last:
            scenes.append({"start": round(cur_start, 3), "end": round(e, 3)})
            cur_start = e
    for sc in scenes:
        sc["window_ids"] = [w["id"] for w in windows if sc["start"] <= (w["start"] + w["end"]) / 2 < sc["end"]]
    # closing windows that end exactly at duration can fall out of the last scene: catch them
    assigned = {wid for sc in scenes for wid in sc["window_ids"]}
    for w in windows:
        if w["id"] not in assigned:
            scenes[-1]["window_ids"].append(w["id"])

    batches: list[dict] = []
    for i, sc in enumerate(scenes, 1):
        wids = sc["window_ids"]
        chunks = [wids[j:j + cfg.scene_max_windows] for j in range(0, len(wids), cfg.scene_max_windows)] or [[]]
        for k, chunk in enumerate(chunks):
            bid = f"s{i:02d}" + (chr(ord('a') + k) if len(chunks) > 1 else "")
            batches.append({"id": bid, "scene_index": i, "start": sc["start"], "end": sc["end"],
                            "window_ids": chunk})
    for w in windows:
        for b in batches:
            if w["id"] in b["window_ids"]:
                w["scene_id"] = b["id"]
    log(f"scenes: {len(scenes)} (~{cfg.scene_target_sec:.0f}s), {len(batches)} LLM batches, "
        f"{sum(1 for b in batches if b['window_ids'])} non-empty")
    return batches


def pick_frame_times(window: dict, shots: list[tuple[float, float]], n: int = 4) -> list[dict]:
    """n frames per window: one per sub-shot center (longest sub-shots first), remaining frames spread
    proportionally to sub-shot duration."""
    ws, we = window["start"], window["end"]
    subs = []
    for i, (s, e) in enumerate(shots):
        ov = overlaps(ws, we, s, e)
        if ov > 0.05:
            subs.append({"shot": i, "s": max(s, ws), "e": min(e, we), "dur": ov})
    if not subs:
        subs = [{"shot": -1, "s": ws, "e": we, "dur": we - ws}]
    if len(subs) > n:
        subs = sorted(sorted(subs, key=lambda x: -x["dur"])[:n], key=lambda x: x["s"])
    total = sum(x["dur"] for x in subs)
    alloc = [max(1, int(round(n * x["dur"] / total))) for x in subs]
    while sum(alloc) > n:
        i = max(range(len(alloc)), key=lambda j: (alloc[j], subs[j]["dur"]))
        alloc[i] -= 1
    while sum(alloc) < n:
        i = max(range(len(alloc)), key=lambda j: subs[j]["dur"] / alloc[j])
        alloc[i] += 1
    frames = []
    for x, k in zip(subs, alloc):
        for j in range(k):
            t = x["s"] + (x["e"] - x["s"]) * (j + 0.5) / k
            frames.append({"t": round(t, 3), "shot": x["shot"]})
    return sorted(frames, key=lambda f: f["t"])[:n]


class FrameGrabber:
    def __init__(self, video: Path):
        self.cap = cv2.VideoCapture(str(video))
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open video {video}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0

    def grab(self, t: float):
        self.cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, frame = self.cap.read()
        if not ok or frame is None:
            # retry slightly earlier (end of file)
            self.cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t - 0.5) * 1000.0)
            ok, frame = self.cap.read()
        return frame if ok else None

    def close(self):
        self.cap.release()


def _fit(frame, w: int, h: int):
    canvas = np.zeros((h, w, 3), dtype=np.uint8)
    if frame is None:
        return canvas
    fh, fw = frame.shape[:2]
    scale = min(w / fw, h / fh)
    nw, nh = max(1, int(fw * scale)), max(1, int(fh * scale))
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
    x0, y0 = (w - nw) // 2, (h - nh) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = resized
    return canvas


def _label(img, text: str):
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2
    (tw, th), base = cv2.getTextSize(text, font, scale, thick)
    cv2.rectangle(img, (0, 0), (tw + 16, th + base + 12), (0, 0, 0), -1)
    cv2.putText(img, text, (8, th + 6), font, scale, (255, 255, 255), thick, cv2.LINE_AA)
    return img


def make_grid(grabber: FrameGrabber, frames: list[dict], out_path: Path, cfg: Config) -> Path:
    cw, ch, g = cfg.grid_cell_w, cfg.grid_cell_h, cfg.grid_gutter
    cells = []
    for i, f in enumerate(frames[:4], 1):
        cell = _fit(grabber.grab(f["t"]), cw, ch)
        cells.append(_label(cell, f"{i}  {fmt_short(f['t'])}"))
    while len(cells) < 4:
        cells.append(np.zeros((ch, cw, 3), dtype=np.uint8))
    grid = np.full((2 * ch + g, 2 * cw + g, 3), 40, dtype=np.uint8)
    grid[0:ch, 0:cw] = cells[0]
    grid[0:ch, cw + g:] = cells[1]
    grid[ch + g:, 0:cw] = cells[2]
    grid[ch + g:, cw + g:] = cells[3]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), grid, [int(cv2.IMWRITE_JPEG_QUALITY), cfg.jpeg_quality])
    return out_path
