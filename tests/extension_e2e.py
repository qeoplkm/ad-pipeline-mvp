"""End-to-end test of the browser extension in Chrome for Testing (needs the web UI server running).

    CHROME="/path/to/Google Chrome for Testing" .venv/bin/python tests/extension_e2e.py [run_id] [seek_seconds]

Loads extension/ unpacked, opens /static/ext-test.html, attaches the run through the same background function the
popup uses, plays across a narration and checks: narration clip playing in the offscreen document, page video ducked,
caption overlay and screen-reader live region showing the cue text, and everything restored after detach.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
CHROME = os.environ.get("CHROME") or sys.exit("set CHROME to a Chrome for Testing binary")
RUN = sys.argv[1] if len(sys.argv) > 1 else "out"
SEEK = float(sys.argv[2]) if len(sys.argv) > 2 else 152.5
BASE = "http://127.0.0.1:8765"
PORT = 9334


def targets():
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))


class Cdp:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        my = self.n
        await self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]

    async def ev(self, expr, timeout=20):
        r = await asyncio.wait_for(self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True), timeout)
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", r["exceptionDetails"]))
        return r["result"].get("value")


async def connect(pred, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for t in targets():
            if pred(t):
                return Cdp(await websockets.connect(t["webSocketDebuggerUrl"], max_size=50_000_000))
        await asyncio.sleep(0.3)
    raise RuntimeError("target not found: " + json.dumps([(t["type"], t["url"]) for t in targets()]))


async def main():
    results, ok = {}, True
    page = await connect(lambda t: t["type"] == "page" and "ext-test.html" in t["url"])
    sw = await connect(lambda t: t["type"] == "service_worker" and t["url"].endswith("background.js"))
    await page.ev("new Promise(r => { const v = document.getElementById('v'); v.readyState >= 1 ? r() : v.addEventListener('loadedmetadata', r, {once: true}); })")
    cues = json.load(urllib.request.urlopen(f"{BASE}/api/runs/{RUN}/cues"))["cues"]
    cue = next(c for c in cues if c["start"] > SEEK)
    results["attach"] = await sw.ev(
        f"""(async () => {{ const [t] = await chrome.tabs.query({{url: "{BASE}/static/ext-test.html*"}});
             return await adAttach(t.id, "{RUN}", {{audio: true, duck: true, overlay: true, announce: true, startAt: 0, duckDb: -9, volume: 1}}); }})()""")
    await page.ev(f"(async () => {{ const v = document.getElementById('v'); v.volume = 1; v.currentTime = {cue['start'] - 1.0}; await v.play(); }})()")
    await asyncio.sleep(1.0 + 1.2)   # into the cue
    results["during"] = await page.ev("""({ t: document.getElementById('v').currentTime,
        volume: document.getElementById('v').volume,
        overlay: document.querySelector('[data-ad-ext=overlay]')?.textContent,
        overlayShown: document.querySelector('[data-ad-ext=overlay]')?.style.display,
        live: document.querySelector('[data-ad-ext=live]')?.textContent })""")
    results["offscreen"] = await sw.ev("chrome.runtime.sendMessage({target: 'offscreen', action: 'state'})")
    results["expected_cue"] = cue
    await page.ev("document.getElementById('v').pause()")
    await asyncio.sleep(0.4)
    results["after_pause"] = await sw.ev("chrome.runtime.sendMessage({target: 'offscreen', action: 'state'})")
    results["detach"] = await sw.ev(f"""(async () => {{ const [t] = await chrome.tabs.query({{url: "{BASE}/static/ext-test.html*"}}); await adDetach(t.id); return true; }})()""")
    await asyncio.sleep(0.3)
    results["after_detach"] = await page.ev("({ volume: document.getElementById('v').volume, overlay: !!document.querySelector('[data-ad-ext]') })")

    # --- scenario 2: the real popup UI with a start-time offset, then the shortcut messages
    OFFSET = 10.0
    tab_id = await sw.ev(f'(async () => (await chrome.tabs.query({{url: "{BASE}/static/ext-test.html*"}}))[0].id)()')
    ext_id = next(t for t in targets() if t["type"] == "service_worker" and t["url"].endswith("/background.js"))["url"].split("/")[2]
    await sw.ev(f'chrome.tabs.create({{url: "chrome-extension://{ext_id}/popup.html?tabId={tab_id}"}}).then(() => true)')
    popup = await connect(lambda t: t["type"] == "page" and "popup.html" in t["url"])
    await popup.ev(f"new Promise(r => {{ const f = () => (document.getElementById('run') && [...document.getElementById('run').options].some(o => o.value === '{RUN}')) ? r() : setTimeout(f, 100); f(); }})")
    results["popup_runs"] = await popup.ev("[...document.getElementById('run').options].map(o => o.value)")
    if os.environ.get("E2E_SHOT"):   # save a screenshot of the popup (setup view) for the docs
        import base64
        await popup.ev(f"document.getElementById('run').value = '{RUN}'; document.getElementById('run').dispatchEvent(new Event('change')); true")
        h = await popup.ev("Math.ceil(document.body.getBoundingClientRect().bottom + 14)")
        shot = await popup.call("Page.captureScreenshot", format="png", captureBeyondViewport=True, clip={"x": 0, "y": 0, "width": 372, "height": h, "scale": 2})
        Path(os.environ["E2E_SHOT"]).write_bytes(base64.b64decode(shot["data"]))
    if os.environ.get("E2E_DEBUG"):
        print("DEBUG popup:", await popup.ev("({onclick: typeof document.getElementById('attach').onclick, run: document.getElementById('run').value, setupHidden: document.getElementById('setup').hidden, status: document.getElementById('status').textContent})"), flush=True)
        await popup.call("Runtime.enable")
    await popup.ev(f"""(async () => {{ document.getElementById('run').value = '{RUN}';
        document.getElementById('startAt').value = '{OFFSET}'; document.getElementById('attach').click();
        for (let i = 0; i < 50 && document.getElementById('attached').hidden; i++) await new Promise(r => setTimeout(r, 100));
        return document.getElementById('status').textContent; }})()""")
    results["popup_status"] = await popup.ev("document.getElementById('status').textContent + ' | attached view: ' + !document.getElementById('attached').hidden")
    await page.ev(f"(async () => {{ const v = document.getElementById('v'); v.volume = 1; v.currentTime = {cue['start'] + OFFSET + 0.3}; await v.play(); }})()")
    await asyncio.sleep(1.0)
    results["offset_during"] = await page.ev("({ overlay: document.querySelector('[data-ad-ext=overlay]')?.textContent, volume: document.getElementById('v').volume, t: document.getElementById('v').currentTime, paused: document.getElementById('v').paused, ext: !!window.__adExt })")
    if os.environ.get("E2E_DEBUG"):
        print("DEBUG popup_status:", results["popup_status"], "| offset_during:", results["offset_during"], flush=True)
    await sw.ev(f"chrome.tabs.sendMessage({tab_id}, {{type: 'ad-update', opts: {{audio: false}}, announce: 'Narration off'}})")
    await asyncio.sleep(0.2)
    results["after_toggle_off"] = {
        "offscreen": await sw.ev("chrome.runtime.sendMessage({target: 'offscreen', action: 'state'})"),
        "live": await page.ev("document.querySelector('[data-ad-ext=live]')?.textContent")}
    await sw.ev(f"chrome.tabs.sendMessage({tab_id}, {{type: 'ad-repeat'}})")
    await asyncio.sleep(0.2)
    results["after_repeat"] = await page.ev("document.querySelector('[data-ad-ext=live]')?.textContent")
    await page.ev("document.getElementById('v').pause()")
    await popup.ev("document.getElementById('detach').click()")
    await asyncio.sleep(0.4)
    results["popup_detach"] = await page.ev("({ volume: document.getElementById('v').volume, ui: !!document.querySelector('[data-ad-ext]') })")

    d, off = results["during"], results["offscreen"]
    checks = {
        "attached": bool(results["attach"] and results["attach"].get("ok")),
        "narration clip playing": off.get("ok") and not off.get("paused") and (off.get("src") or "").endswith(cue["audio"].split("/")[-1]),
        "clip position matches video": abs((off.get("time") or 0) - (d["t"] - cue["start"])) < 0.35,
        "video ducked to about -9 dB": abs(d["volume"] - 10 ** (-9 / 20)) < 0.03,
        "caption shows cue text": d["overlay"] == cue["text"] and d["overlayShown"] == "block",
        "screen reader live region": (d["live"] or "").strip() == cue["text"],
        "pause pauses narration": results["after_pause"].get("paused") is True,
        "detach restores volume and removes UI": abs(results["after_detach"]["volume"] - 1.0) < 0.01 and not results["after_detach"]["overlay"],
        "popup lists the run": RUN in results["popup_runs"],
        "popup attaches": "attached view: true" in results["popup_status"],
        "start-time offset applied": results["offset_during"]["overlay"] == cue["text"],
        "toggle off stops narration and announces it": results["after_toggle_off"]["offscreen"].get("paused") is True and (results["after_toggle_off"]["live"] or "").strip() == "Narration off",
        "repeat announces the last description": (results["after_repeat"] or "").strip() == cue["text"],
        "popup detach restores the page": abs(results["popup_detach"]["volume"] - 1.0) < 0.01 and not results["popup_detach"]["ui"],
    }
    print(json.dumps(results, indent=1, default=str)[:3000])
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
        ok = ok and bool(v)
    return ok


if __name__ == "__main__":
    prof = tempfile.mkdtemp(prefix="adext-")
    ext = str(ROOT / "extension")
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
                             f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--no-first-run",
                             "--autoplay-policy=no-user-gesture-required", "--mute-audio", f"{BASE}/static/ext-test.html?run={RUN}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                targets(); break
            except Exception:
                time.sleep(0.25)
        ok = asyncio.run(main())
    finally:
        proc.terminate()
    sys.exit(0 if ok else 1)
