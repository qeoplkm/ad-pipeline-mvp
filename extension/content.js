// Content script: follows the page's <video>, ducks it during narration, shows/announces the text and asks the
// extension to play each narration clip at the right moment. Injected only into tabs the user attaches.
(() => {
  "use strict";
  if (window.__adExt) { window.__adExt.detach(); }

  const state = { video: null, cues: [], opts: {}, timer: null, active: null, baseVol: 1, ourVol: null, duckGain: 0.355,
                  ramp: 0.15, overlay: null, live: null, listeners: [] };

  const send = msg => chrome.runtime.sendMessage({ type: "audio", ...msg }).catch(() => {});

  function pickVideo() {
    const vids = [...document.querySelectorAll("video")].filter(v => v.offsetWidth * v.offsetHeight > 0 || v.readyState > 0);
    vids.sort((a, b) => b.offsetWidth * b.offsetHeight - a.offsetWidth * a.offsetHeight);
    return vids[0] || null;
  }

  const adTime = () => state.video.currentTime - (state.opts.startAt || 0);

  function gainAt(t) {
    // 1.0 outside narration, duckGain inside, linear ramps of state.ramp seconds on both sides
    let g = 1;
    for (const c of state.cues) {
      if (t < c.start - state.ramp) break;
      if (t > c.end + state.ramp) continue;
      let k;
      if (t < c.start) k = (c.start - t) / state.ramp;
      else if (t > c.end) k = (t - c.end) / state.ramp;
      else k = 0;
      g = Math.min(g, state.duckGain + (1 - state.duckGain) * Math.min(1, k));
    }
    return g;
  }

  function show(cue) {
    if (state.overlay) {
      state.overlay.textContent = cue ? cue.text : "";
      state.overlay.style.display = cue && state.opts.overlay ? "block" : "none";
    }
    if (cue && state.opts.announce && state.live) {
      state.live.textContent = "";
      setTimeout(() => { state.live.textContent = cue.text; }, 30);   // re-set so screen readers announce it
    }
  }

  function tick() {
    const v = state.video;
    if (!v || !v.isConnected) { const nv = pickVideo(); if (nv && nv !== v) bindVideo(nv); return; }
    const t = adTime();
    // ducking
    if (state.opts.duck) {
      const target = state.baseVol * gainAt(t);
      if (Math.abs(v.volume - target) > 0.005) { state.ourVol = target; v.volume = target; }
    }
    if (v.paused || v.seeking) return;
    const cue = state.cues.find(c => t >= c.start && t < c.end) || null;
    const id = cue ? cue.id : null;
    if (id !== state.active) {
      state.active = id;
      show(cue);
      if (cue && state.opts.audio) {
        const from = t - cue.start;
        if (from < (cue.end - cue.start) - 0.15) send({ action: "play", url: cue.audio, from, volume: state.opts.volume ?? 1, rate: v.playbackRate });
      }
    }
  }

  function on(target, ev, fn) { target.addEventListener(ev, fn); state.listeners.push([target, ev, fn]); }

  function bindVideo(v) {
    unbindVideo();
    state.video = v;
    state.baseVol = v.volume;
    on(v, "pause", () => send({ action: "pause" }));
    on(v, "play", () => { state.active = null; });            // re-trigger the current cue from the right offset
    on(v, "seeking", () => { state.active = null; send({ action: "stop" }); show(null); });
    on(v, "ratechange", () => send({ action: "rate", rate: v.playbackRate }));
    on(v, "volumechange", () => {
      // a volume change we did not cause is the user's: remember it as the new base level
      if (state.ourVol === null || Math.abs(v.volume - state.ourVol) > 0.01) {
        const g = state.opts.duck ? gainAt(adTime()) : 1;
        state.baseVol = Math.min(1, v.volume / g);
      }
    });
  }

  function unbindVideo() {
    for (const [t, ev, fn] of state.listeners) t.removeEventListener(ev, fn);
    state.listeners = [];
    if (state.video && state.opts.duck) state.video.volume = state.baseVol;
  }

  function makeUi() {
    const o = document.createElement("div");
    o.setAttribute("data-ad-ext", "overlay");
    o.setAttribute("aria-hidden", "true");   // screen readers get the live region instead
    Object.assign(o.style, { position: "fixed", left: "50%", bottom: "8%", transform: "translateX(-50%)", maxWidth: "80vw",
      padding: "8px 14px", borderRadius: "8px", background: "rgba(0,0,0,.78)", color: "#ffe27a", font: "600 20px/1.35 system-ui, sans-serif",
      zIndex: 2147483647, pointerEvents: "none", display: "none", textAlign: "center" });
    const live = document.createElement("div");
    live.setAttribute("data-ad-ext", "live");
    live.setAttribute("role", "status");
    live.setAttribute("aria-live", "assertive");
    Object.assign(live.style, { position: "fixed", width: "1px", height: "1px", overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap" });
    document.documentElement.append(o, live);
    state.overlay = o; state.live = live;
  }

  function attach(data, opts) {
    const v = pickVideo();
    if (!v) return { ok: false, error: "No video found on this page. Start the video first, then attach." };
    state.cues = data.cues;
    state.opts = Object.assign({ audio: true, duck: true, overlay: true, announce: false, startAt: 0, volume: 1 }, opts);
    state.duckGain = Math.pow(10, (opts.duckDb ?? data.duck_db ?? -9) / 20);
    state.ramp = data.duck_ramp ?? 0.15;
    bindVideo(v);
    makeUi();
    send({ action: "preload", urls: state.cues.slice(0, 40).map(c => c.audio) });
    state.timer = setInterval(tick, 50);
    return { ok: true, video: { duration: v.duration, src: (v.currentSrc || "").slice(0, 120), width: v.videoWidth, height: v.videoHeight } };
  }

  function detach() {
    clearInterval(state.timer);
    unbindVideo();
    if (state.overlay) state.overlay.remove();
    if (state.live) state.live.remove();
    send({ action: "stop" });
    chrome.runtime.onMessage.removeListener(onMsg);
    delete window.__adExt;
  }

  function onMsg(msg, sender, reply) {
    if (msg.type === "ad-attach") { reply(attach(msg.data, msg.opts || {})); return false; }
    if (msg.type === "ad-detach") { detach(); reply({ ok: true }); return false; }
    if (msg.type === "ad-update") { Object.assign(state.opts, msg.opts || {}); if (!state.opts.overlay) show(null); reply({ ok: true }); return false; }
    if (msg.type === "ad-state") {
      reply({ ok: true, t: state.video ? adTime() : null, active: state.active, volume: state.video ? state.video.volume : null,
              base: state.baseVol, overlay: state.overlay ? state.overlay.textContent : null, live: state.live ? state.live.textContent : null });
      return false;
    }
    return false;
  }
  chrome.runtime.onMessage.addListener(onMsg);
  window.__adExt = { detach, state };
})();
