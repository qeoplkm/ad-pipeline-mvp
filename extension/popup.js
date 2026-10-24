"use strict";
const $ = id => document.getElementById(id);
const statusEl = $("status");
const say = (text, kind = "") => { statusEl.textContent = text; statusEl.className = `status ${kind}`; };

async function activeTab() {
  // ?tabId=N lets tests open the popup as a page and target another tab
  const q = new URLSearchParams(location.search).get("tabId");
  if (q) return { id: +q };
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function loadRuns(server) {
  const sel = $("run");
  try {
    const r = await fetch(`${server}/api/runs`);
    const runs = (await r.json()).filter(x => x.status === "done" && x.has_mix);
    if (!runs.length) { sel.innerHTML = `<option value="">No finished runs</option>`; return; }
    sel.innerHTML = runs.map(x => `<option value="${x.id}">${x.id} · ${x.video_name || "?"} · ${x.narrations ?? "?"} narrations</option>`).join("");
    const saved = (await chrome.storage.local.get("run")).run;
    if (saved && runs.some(x => x.id === saved)) sel.value = saved;
    const info = () => { const x = runs.find(r => r.id === sel.value); $("run-info").textContent = x ? `${x.lang || ""} · ${x.model || x.provider || ""} · ${x.duration ? Math.round(x.duration) + " s" : ""}` : ""; };
    sel.onchange = info; info();
  } catch (e) {
    sel.innerHTML = `<option value="">Server not reachable</option>`;
    say(`Cannot reach ${server}. Start the local server first.`, "err");
  }
}

function readOpts() {
  return { audio: $("o-audio").checked, duck: $("o-duck").checked, overlay: $("o-overlay").checked, announce: $("o-announce").checked,
           startAt: +$("startAt").value || 0, duckDb: +$("duckDb").value, volume: +$("volume").value };
}

async function showAttached(tab, info) {
  $("setup").hidden = !!info; $("attached").hidden = !info;
  if (!info) return;
  $("att-run").textContent = info.run;
  $("att-cues").textContent = `(${info.cues} cues)`;
  for (const k of ["audio", "duck", "overlay", "announce"]) {
    const el = $(`u-${k}`); el.checked = !!info.opts[k];
    el.onchange = async () => {
      info.opts[k] = el.checked;
      await chrome.tabs.sendMessage(tab.id, { type: "ad-update", opts: { [k]: el.checked } });
      await chrome.storage.session.set({ [`tab-${tab.id}`]: info });
    };
  }
}

(async () => {
  const st = await chrome.storage.local.get({ server: "http://127.0.0.1:8765", opts: null });
  $("server").value = st.server;
  if (st.opts) {
    for (const k of ["audio", "duck", "overlay", "announce"]) $(`o-${k}`).checked = !!st.opts[k];
    $("duckDb").value = st.opts.duckDb ?? -9; $("volume").value = st.opts.volume ?? 1;
  }
  const upd = () => { $("duckDb-v").textContent = `${$("duckDb").value} dB`; $("volume-v").textContent = `${Math.round($("volume").value * 100)}%`; };
  $("duckDb").oninput = upd; $("volume").oninput = upd; upd();
  $("server").onchange = async () => { await chrome.storage.local.set({ server: $("server").value.replace(/\/$/, "") }); loadRuns($("server").value); };

  const tab = await activeTab();
  const info = (await chrome.storage.session.get(`tab-${tab.id}`))[`tab-${tab.id}`];
  await showAttached(tab, info);
  if (!info) loadRuns(st.server);

  $("attach").onclick = async () => {
    const run = $("run").value; if (!run) return;
    const opts = readOpts();
    await chrome.storage.local.set({ run, opts });
    $("attach").disabled = true; say("Attaching…");
    const res = await chrome.runtime.sendMessage({ type: "attach", tabId: tab.id, run, opts });
    $("attach").disabled = false;
    if (!res || !res.ok) { say(res ? res.error : "Attach failed", "err"); return; }
    say("Attached. Play the video.", "ok");
    showAttached(tab, (await chrome.storage.session.get(`tab-${tab.id}`))[`tab-${tab.id}`]);
  };
  $("detach").onclick = async () => {
    await chrome.runtime.sendMessage({ type: "detach", tabId: tab.id });
    say("Detached.", "ok"); showAttached(tab, null); loadRuns($("server").value);
  };
})();
