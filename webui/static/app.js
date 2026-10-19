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
async function viewRun(id) {
  $app.innerHTML = `<div class="crumbs"><a href="#/">← Runs</a></div><h1>${esc(id)}</h1><div id="runbody" class="muted">Loading…</div>`;
  const run = await api(`/api/runs/${encodeURIComponent(id)}`);
  if (run.status !== "done") return viewRunProgress(id, run);
  return viewResult(id, run);
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

async function viewResult(id, run) {
  const body = document.getElementById("runbody");
  const d = await api(`/api/runs/${encodeURIComponent(id)}/data`);
  body.innerHTML = renderStats(run, d) + `<div class="panel"><p class="muted">Player and review table arrive in the next milestones.</p>
    <p><a href="/files/${encodeURIComponent(id)}/mixed.mp3">mixed.mp3</a> · <a href="/files/${encodeURIComponent(id)}/ad.vtt">ad.vtt</a></p></div>`;
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
  const m = h.match(/^#\/run\/(.+)$/);
  (m ? viewRun(decodeURIComponent(m[1])) : viewHome()).catch(e => { $app.innerHTML = `<div class="panel err">${esc(e.message)}</div>`; });
}
window.addEventListener("hashchange", route);
route();
