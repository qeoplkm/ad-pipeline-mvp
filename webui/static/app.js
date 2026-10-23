"use strict";
const $app = document.getElementById("app");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtT = s => { s = Math.max(0, +s || 0); const m = Math.floor(s / 60); return `${m}:${(s % 60).toFixed(1).padStart(4, "0")}`; };
const api = async (url, opts) => { const r = await fetch(url, opts); if (!r.ok) throw new Error((await r.text()) || r.statusText); return r.headers.get("content-type")?.includes("json") ? r.json() : r.text(); };
let timers = [];
const clearTimers = () => { timers.forEach(clearInterval); timers = []; };

function statusChip(s) {
  const cls = s === "done" ? "st-done" : s === "failed" ? "st-failed" : "st-running";
  const label = s === "done" || s === "failed" ? s : `running · ${s === "running" ? "starting" : s}`;
  return `<span class="chip ${cls}">${esc(label)}</span>`;
}

// ------------------------------------------------------------------ home: runs + new run
async function viewHome() {
  $app.innerHTML = `<div class="grid2">
    <section class="panel"><h1>Runs</h1><div id="runs" class="muted">Loading…</div></section>
    <section class="panel"><h1>New run</h1><div id="form" class="muted">Loading…</div></section></div>`;
  renderRuns();
  timers.push(setInterval(renderRuns, 3000));
  renderForm();
}

async function renderRuns() {
  const el = document.getElementById("runs"); if (!el) return;
  const runs = await api("/api/runs");
  if (!runs.length) { el.innerHTML = `<p class="muted">No runs yet. Start one on the right.</p>`; return; }
  el.innerHTML = `<table><thead><tr><th>Run</th><th>Video</th><th>Lang</th><th>Model</th><th>Narrations</th><th>Status</th></tr></thead><tbody>
    ${runs.map(r => `<tr class="click" data-id="${esc(r.id)}" tabindex="0">
      <td><a href="#/run/${encodeURIComponent(r.id)}">${esc(r.id)}</a></td>
      <td class="small">${esc(r.video_name || "—")}</td><td>${esc(r.lang || "—")}</td>
      <td class="small">${esc(r.model || r.provider || "—")}</td><td>${r.narrations ?? "—"}</td>
      <td>${statusChip(r.status)}</td></tr>`).join("")}</tbody></table>`;
  el.querySelectorAll("tr.click").forEach(tr => {
    const go = () => location.hash = `#/run/${encodeURIComponent(tr.dataset.id)}`;
    tr.onclick = go; tr.onkeydown = e => { if (e.key === "Enter") go(); };
  });
}

async function renderForm() {
  const el = document.getElementById("form");
  const [inputs, prov] = await Promise.all([api("/api/inputs"), api("/api/providers")]);
  const provOpts = ["auto", "openai", "claude", "gemini", "mock"].map(p => {
    const ok = p === "auto" || prov.available[p];
    return `<option value="${p}" ${ok ? "" : "disabled"}>${p}${ok || p === "auto" ? "" : " (no key)"}</option>`;
  }).join("");
  el.innerHTML = `<form id="runform">
    <label for="video_name">Video from input/</label>
    <select id="video_name" name="video_name"><option value="">— upload instead —</option>
      ${inputs.map(v => `<option value="${esc(v.name)}" data-srts='${esc(JSON.stringify(v.srts))}'>${esc(v.name)} (${v.size_mb} MB)</option>`).join("")}</select>
    <label for="video_file">…or upload a video</label><input id="video_file" name="video_file" type="file" accept="video/*">
    <label for="srt_name">Subtitles (optional; without them the dialogue is transcribed)</label>
    <select id="srt_name" name="srt_name"><option value="">none</option></select>
    <label for="srt_file">…or upload an .srt</label><input id="srt_file" name="srt_file" type="file" accept=".srt">
    <div class="row">
      <div><label for="lang">Language</label><select id="lang" name="lang"><option value="en">English</option><option value="zh">Mandarin</option></select></div>
      <div><label for="provider">Model provider</label><select id="provider" name="provider">${provOpts}</select></div>
    </div>
    <div class="row">
      <div><label for="model">Model (blank = default)</label><input id="model" name="model" type="text" placeholder="${esc(prov.default_model || "provider default")}"></div>
      <div><label for="voice">Voice (blank = default)</label><input id="voice" name="voice" type="text" placeholder="en-US-JennyNeural"></div>
    </div>
    <label for="characters">Cast list (optional JSON: [{"name": "...", "description": "..."}])</label>
    <textarea id="characters" name="characters" placeholder='[{"name": "Thom", "description": "young man, dark hair, leather jacket"}]'></textarea>
    <div class="row">
      <div><label for="max_windows">Only first N windows (0 = all)</label><input id="max_windows" name="max_windows" type="number" min="0" value="0"></div>
      <div><label class="check"><input type="checkbox" name="skip_demucs" value="true"> Skip vocal separation (faster)</label></div>
    </div>
    <div class="actions"><button class="primary" type="submit">Start run</button><span id="formmsg" class="muted small"></span></div>
    <div id="formerr" class="err"></div></form>`;
  const vsel = document.getElementById("video_name"), ssel = document.getElementById("srt_name");
  vsel.onchange = () => {
    const srts = JSON.parse(vsel.selectedOptions[0]?.dataset.srts || "[]");
    ssel.innerHTML = `<option value="">none</option>` + srts.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    if (srts.length) ssel.value = srts[0];
  };
  document.getElementById("runform").onsubmit = async e => {
    e.preventDefault();
    const btn = e.target.querySelector("button[type=submit]"); btn.disabled = true;
    document.getElementById("formerr").textContent = "";
    document.getElementById("formmsg").textContent = "Uploading and starting…";
    try {
      const fd = new FormData(e.target);
      if (!fd.get("skip_demucs")) fd.set("skip_demucs", "false");
      const r = await api("/api/runs", { method: "POST", body: fd });
      location.hash = `#/run/${encodeURIComponent(r.id)}`;
    } catch (err) {
      document.getElementById("formerr").textContent = err.message; btn.disabled = false;
      document.getElementById("formmsg").textContent = "";
    }
  };
}

// ------------------------------------------------------------------ run page
async function viewRun(id, startAt) {
  $app.innerHTML = `<div class="crumbs"><a href="#/">← Runs</a></div><h1>${esc(id)}</h1><div id="runbody" class="muted">Loading…</div>`;
  const run = await api(`/api/runs/${encodeURIComponent(id)}`);
  if (run.status !== "done") return viewRunProgress(id, run);
  return viewResult(id, run, startAt);
}

function viewRunProgress(id, run) {
  const body = document.getElementById("runbody");
  body.innerHTML = `<div class="panel">
      <div class="actions" style="margin-top:0"><span id="st">${statusChip(run.status)}</span>
      <button id="cancel" ${run.status === "failed" ? "disabled" : ""}>Cancel</button>
      <span class="muted small">${esc(run.video_name || "")} · ${esc(run.lang || "")} · ${esc(run.provider || "")}</span></div>
      <h2 style="margin-top:16px">Log</h2><pre class="log" id="log"></pre></div>`;
  let offset = 0;
  const logEl = document.getElementById("log");
  const tick = async () => {
    try {
      const r = await fetch(`/api/runs/${encodeURIComponent(id)}/log?offset=${offset}`);
      const txt = await r.text(); offset = +(r.headers.get("X-Offset") || offset);
      if (txt) { const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 30;
        logEl.textContent += txt.replace(/\r/g, "\n"); if (atBottom) logEl.scrollTop = logEl.scrollHeight; }
      const s = await api(`/api/runs/${encodeURIComponent(id)}`);
      document.getElementById("st").innerHTML = statusChip(s.status);
      if (s.status === "done") { clearTimers(); setTimeout(() => viewRun(id), 800); }
      if (s.status === "failed") { document.getElementById("cancel").disabled = true; }
    } catch (e) { /* keep polling */ }
  };
  tick(); timers.push(setInterval(tick, 1500));
  document.getElementById("cancel").onclick = async () => { await api(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" }); };
}

async function viewResult(id, run, startAt) {
  const body = document.getElementById("runbody");
  const d = await api(`/api/runs/${encodeURIComponent(id)}/data`);
  const bust = Date.now();
  const f = p => `/files/${encodeURIComponent(id)}/${p}?v=${bust}`;
  body.innerHTML = renderStats(run, d) + `
  <section class="panel player">
    <div class="stage" id="stage">
      <video id="vid" src="/video/${encodeURIComponent(id)}" preload="auto" playsinline muted>
        <track kind="captions" srclang="en" label="Audio description" src="${f("ad.vtt")}" default>
      </video>
      <div class="blind" id="blind" hidden><span>Picture hidden. Listen as a blind viewer would.</span></div>
    </div>
    <audio id="aud" src="${f("mixed.mp3")}" preload="auto"></audio>
    <div class="controls">
      <button id="play" class="primary">▶ Play</button>
      <span class="time" id="time">0:00.0</span>
      <div class="seg" role="radiogroup" aria-label="Audio">
        <button data-mode="ad" class="on" role="radio" aria-checked="true">AD mix</button>
        <button data-mode="orig" role="radio" aria-checked="false">Original</button>
      </div>
      <label class="check inline"><input type="checkbox" id="hidepic"> Hide picture</label>
      <label class="check inline"><input type="checkbox" id="showcc" checked> Show AD text</label>
    </div>
    <div class="timeline" id="tl" title="Click to seek">
      <div class="lane"><span class="lane-k">Dialogue</span><div class="lane-v" id="lane-d"></div></div>
      <div class="lane"><span class="lane-k">Windows</span><div class="lane-v" id="lane-w"></div></div>
      <div class="lane"><span class="lane-k">Narration</span><div class="lane-v" id="lane-n"></div></div>
      <div class="cursor" id="cursor"></div>
    </div>
    <div class="now" id="now"><span class="muted">The current narration appears here.</span></div>
    <p class="small muted">Space = play/pause · ←/→ = 5 s · click the timeline to seek ·
      <a href="${f("mixed.mp3")}" download>mixed.mp3</a> · <a href="${f("ad.vtt")}" download>ad.vtt</a> · <a href="${f("ad_track.wav")}" download>ad_track.wav</a></p>
  </section>
  <div id="review"></div>`;
  setupPlayer(d, run);
  if (startAt) { const v = document.getElementById("vid"); v.addEventListener("loadedmetadata", () => { v.currentTime = startAt; }, { once: true }); }
  if (typeof renderReview === "function") renderReview(id, d);
}

function setupPlayer(d, run) {
  const vid = document.getElementById("vid"), aud = document.getElementById("aud");
  const play = document.getElementById("play"), timeEl = document.getElementById("time");
  const dur = run.duration || (d.stats.video_info || {}).duration || 1;
  let mode = "ad";
  const kept = d.narrations.filter(n => n.status === "kept");
  const pct = t => `${(100 * t / dur).toFixed(3)}%`;
  const bar = (a, b, cls, title) => a >= dur ? "" : `<i class="${cls}" style="left:${pct(a)};width:${pct(Math.max(Math.min(b, dur) - a, dur / 2000))}" title="${esc(title)}"></i>`;
  document.getElementById("lane-d").innerHTML = d.dialogue.map(l => bar(l.start, l.end, "b-d", `${fmtT(l.start)} ${l.text}`)).join("");
  document.getElementById("lane-w").innerHTML = d.windows.map(w => bar(w.start, w.end, "b-w", `${w.id} ${fmtT(w.start)}–${fmtT(w.end)}`)).join("");
  document.getElementById("lane-n").innerHTML = kept.map(n => bar(n.actual_start, n.actual_end, "b-n", `${n.window_id}: ${n.text_used}`)).join("");

  const sync = force => {
    if (mode !== "ad") return;
    if (force || Math.abs(aud.currentTime - vid.currentTime) > 0.25) aud.currentTime = vid.currentTime;
  };
  const setMode = m => {
    mode = m;
    document.querySelectorAll(".seg button").forEach(b => { const on = b.dataset.mode === m; b.classList.toggle("on", on); b.setAttribute("aria-checked", on); });
    vid.muted = m === "ad";
    if (m === "ad") { sync(true); if (!vid.paused) aud.play(); } else aud.pause();
  };
  document.querySelectorAll(".seg button").forEach(b => b.onclick = () => setMode(b.dataset.mode));
  const toggle = () => vid.paused ? vid.play() : vid.pause();
  play.onclick = toggle;
  vid.addEventListener("click", toggle);
  vid.addEventListener("play", () => { play.textContent = "❚❚ Pause"; if (mode === "ad") { sync(true); aud.play(); } });
  vid.addEventListener("pause", () => { play.textContent = "▶ Play"; aud.pause(); });
  vid.addEventListener("seeked", () => sync(true));
  vid.addEventListener("waiting", () => aud.pause());
  vid.addEventListener("playing", () => { if (mode === "ad") { sync(true); aud.play(); } });
  vid.addEventListener("ended", () => aud.pause());
  const now = document.getElementById("now"), cursor = document.getElementById("cursor");
  let lastId;
  const onTime = () => {
    const t = vid.currentTime; timeEl.textContent = `${fmtT(t)} / ${fmtT(dur)}`;
    cursor.style.left = `calc(88px + (100% - 96px) * ${t / dur})`; sync(false);
    const n = kept.find(n => t >= n.actual_start && t <= n.actual_end);
    const id = n ? n.window_id : null;
    if (id !== lastId) {
      lastId = id;
      now.innerHTML = n ? `<span class="chip">${esc(n.window_id)}</span> ${esc(n.text_used)}` : `<span class="muted">—</span>`;
      document.querySelectorAll("tr.nrow").forEach(tr => tr.classList.toggle("playing", tr.dataset.id === id));
    }
  };
  vid.addEventListener("timeupdate", onTime);
  vid.addEventListener("seeked", onTime);
  document.getElementById("tl").onclick = e => {
    const r = e.currentTarget.querySelector(".lane-v").getBoundingClientRect();
    vid.currentTime = Math.min(dur, Math.max(0, (e.clientX - r.left) / r.width * dur));
  };
  document.getElementById("hidepic").onchange = e => {
    document.getElementById("blind").hidden = !e.target.checked;
    vid.style.visibility = e.target.checked ? "hidden" : "visible";
  };
  const track = () => vid.textTracks[0];
  document.getElementById("showcc").onchange = e => { if (track()) track().mode = e.target.checked ? "showing" : "hidden"; };
  vid.addEventListener("loadedmetadata", () => { if (track()) track().mode = "showing"; });
  window.seekTo = t => { vid.currentTime = t; document.getElementById("stage").scrollIntoView({ block: "nearest", behavior: "smooth" }); };
  document.onkeydown = e => {
    if (!document.getElementById("vid") || ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(e.target.tagName)) return;
    if (e.code === "Space") { e.preventDefault(); toggle(); }
    if (e.code === "ArrowRight") vid.currentTime = Math.min(dur, vid.currentTime + 5);
    if (e.code === "ArrowLeft") vid.currentTime = Math.max(0, vid.currentTime - 5);
  };
}

function renderStats(run, d) {
  const s = d.stats || {}, w = s.windows || {}, llm = s.llm || {}, mix = s.mix || {}, t = d.timings || {};
  const items = [
    ["Duration", run.duration ? fmtT(run.duration) : "—"],
    ["Windows", w.after_split ?? w.windows ?? "—"],
    ["Narrations", mix.kept ?? "—"],
    ["Narration time", mix.narration_seconds ? `${mix.narration_seconds.toFixed(0)} s` : "—"],
    ["Model", llm.model || "—"],
    ["Tokens in / out", llm.input_tokens != null ? `${(llm.input_tokens / 1000).toFixed(1)}k / ${(llm.output_tokens / 1000).toFixed(1)}k` : "—"],
    ["Run time", t.total ? `${t.total.toFixed(0)} s` : "—"],
  ];
  return `<div class="stats">${items.map(([k, v]) => `<div class="stat"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`).join("")}</div>`;
}

// ------------------------------------------------------------------ router
function route() {
  clearTimers();
  const h = location.hash || "#/";
  const m = h.match(/^#\/run\/([^?]+)(?:\?t=([\d.]+))?$/);
  (m ? viewRun(decodeURIComponent(m[1]), m[2] ? +m[2] : 0) : viewHome()).catch(e => { $app.innerHTML = `<div class="panel err">${esc(e.message)}</div>`; });
}
window.addEventListener("hashchange", route);
route();

// ------------------------------------------------------------------ review table (milestone 3)
const countUnits = (text, lang) => lang === "zh"
  ? [...(text || "")].filter(c => /[\p{L}\p{N}]/u.test(c)).length
  : ((text || "").match(/[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g) || []).length;

function renderReview(id, d) {
  const el = document.getElementById("review"); if (!el) return;
  const lang = (d.stats.config || {}).lang || "en";
  const unit = lang === "zh" ? "chars" : "words";
  const wins = Object.fromEntries(d.windows.map(w => [w.id, w]));
  const rows = d.narrations;
  const original = {};
  rows.forEach(n => original[n.window_id] = { narration: n.text_used || n.narration || "", narration_short: n.narration_short || "", disabled: !!n.disabled });
  const review = d.stats.review;
  el.innerHTML = `<section class="panel">
    <div class="rev-head">
      <h2>Review narration</h2>
      <div class="seg" role="radiogroup" aria-label="Filter">
        <button data-f="all" class="on">All (${rows.length})</button>
        <button data-f="kept">Played (${rows.filter(n => n.status === "kept").length})</button>
        <button data-f="silent">Silent (${rows.filter(n => n.status !== "kept").length})</button>
        <button data-f="changed">Changed</button>
      </div>
      <span class="grow"></span>
      <span id="revmsg" class="muted small">${review ? `Reviewed: ${review.edited_total} edited so far.` : "Edit text, then save to re-synthesize and re-mix (no model call)."}</span>
      <button id="revsave" class="primary" disabled>Save &amp; re-render</button>
    </div>
    <div class="tablewrap"><table class="rev">
      <thead><tr><th>Window</th><th>Frames</th><th>Score</th><th>Narration (${unit} / budget)</th><th></th></tr></thead>
      <tbody>${rows.map(n => {
        const w = wins[n.window_id] || {};
        const text = original[n.window_id].narration;
        const before = (w.context_before || []).slice(-1).map(l => l.text).join(" ");
        const after = (w.context_after || []).slice(0, 1).map(l => l.text).join(" ");
        return `<tr class="nrow st-${esc(n.status)}" data-id="${esc(n.window_id)}">
          <td class="nowrap"><button class="link seek" data-t="${w.start}" title="Play from here">${esc(n.window_id)}</button>
            <div class="small muted">${fmtT(w.start)}–${fmtT(w.end)}</div><div class="small muted">${(w.duration || 0).toFixed(1)} s</div>
            <div class="small">${statusLabel(n)}</div></td>
          <td><a href="/files/${encodeURIComponent(id)}/${esc(w.grid || "")}" target="_blank" rel="noopener"><img class="thumb" loading="lazy" alt="Key frames for ${esc(n.window_id)}" src="/files/${encodeURIComponent(id)}/${esc(w.grid || "")}"></a></td>
          <td class="score">${n.importance_score ?? "—"}</td>
          <td class="edit">
            ${before ? `<div class="ctx small muted">◀ ${esc(before)}</div>` : ""}
            <textarea class="t-main" rows="2" aria-label="Narration for ${esc(n.window_id)}">${esc(text)}</textarea>
            <div class="meta small"><span class="cnt"></span>
              <details><summary>short version</summary><textarea class="t-short" rows="1" aria-label="Short narration for ${esc(n.window_id)}">${esc(original[n.window_id].narration_short)}</textarea></details>
              ${n.edited && n.model_narration ? `<details><summary>model's original</summary><div class="muted">${esc(n.model_narration)}</div></details>` : ""}
              ${(n.fit_log || []).length && n.status !== "kept" ? `<span class="warn">${esc((n.fit_log || []).join("; "))}</span>` : ""}
            </div>
            ${after ? `<div class="ctx small muted">▶ ${esc(after)}</div>` : ""}
          </td>
          <td class="nowrap"><label class="check inline small"><input type="checkbox" class="t-off" ${n.disabled ? "checked" : ""}> mute</label></td>
        </tr>`; }).join("")}</tbody></table></div></section>`;

  const save = document.getElementById("revsave"), msg = document.getElementById("revmsg");
  const state = tr => ({ narration: tr.querySelector(".t-main").value.trim(), narration_short: tr.querySelector(".t-short").value.trim(),
    disabled: tr.querySelector(".t-off").checked });
  const isChanged = tr => { const o = original[tr.dataset.id], s = state(tr);
    return s.narration !== o.narration.trim() || s.narration_short !== o.narration_short.trim() || s.disabled !== o.disabled; };
  const refresh = tr => {
    const w = wins[tr.dataset.id] || {}, s = state(tr);
    const c = countUnits(s.narration, lang), b = w.budget ?? 0;
    const cnt = tr.querySelector(".cnt");
    cnt.textContent = `${c} / ${b} ${unit}`; cnt.classList.toggle("over", c > b);
    tr.classList.toggle("changed", isChanged(tr));
    const n = document.querySelectorAll("tr.nrow.changed").length;
    save.disabled = n === 0; save.textContent = n ? `Save & re-render (${n})` : "Save & re-render";
  };
  el.querySelectorAll("tr.nrow").forEach(tr => {
    refresh(tr);
    tr.querySelectorAll("textarea, input").forEach(i => i.addEventListener("input", () => refresh(tr)));
  });
  el.querySelectorAll(".seek").forEach(b => b.onclick = () => {
    const v = document.getElementById("vid"); window.seekTo(Math.max(0, +b.dataset.t - 1)); v.play();
  });
  el.querySelectorAll(".rev-head .seg button").forEach(b => b.onclick = () => {
    el.querySelectorAll(".rev-head .seg button").forEach(x => x.classList.toggle("on", x === b));
    const f = b.dataset.f;
    el.querySelectorAll("tr.nrow").forEach(tr => {
      const kept = tr.classList.contains("st-kept");
      tr.hidden = f === "kept" ? !kept : f === "silent" ? kept : f === "changed" ? !tr.classList.contains("changed") : false;
    });
  });
  save.onclick = async () => {
    const edits = [...el.querySelectorAll("tr.nrow.changed")].map(tr => ({ window_id: tr.dataset.id, ...state(tr) }));
    save.disabled = true; msg.textContent = `Re-rendering ${edits.length} change(s)…`;
    try {
      await api(`/api/runs/${encodeURIComponent(id)}/narrations`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ edits }) });
      const poll = async () => {
        const s = await api(`/api/runs/${encodeURIComponent(id)}/rerender`);
        if (s.state === "running") return setTimeout(poll, 1000);
        if (s.state === "failed") { msg.innerHTML = `<span class="err">${esc(s.error)}</span>`; save.disabled = false; return; }
        const y = window.scrollY;
        await viewRun(id); window.scrollTo(0, y);
        const m = document.getElementById("revmsg");
        if (m) m.textContent = `Re-rendered in ${s.result.seconds} s: ${s.result.kept} narrations play, ${s.result.dropped} dropped (too long).`;
      };
      poll();
    } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; save.disabled = false; }
  };
}

function statusLabel(n) {
  const map = { kept: "plays", filtered_low_score: "silent (low score)", dropped_too_long: "dropped: too long", disabled: "muted by reviewer",
    missing_in_response: "model skipped", tts_error: "TTS error" };
  let s = map[n.status] || n.status;
  if (n.status === "kept" && n.variant === "short") s += " · short";
  if (n.status === "kept" && n.stretch > 1.0) s += ` · ${n.stretch.toFixed(2)}x`;
  if (n.edited) s += " · edited";
  return `<span class="chip">${esc(s)}</span>`;
}
