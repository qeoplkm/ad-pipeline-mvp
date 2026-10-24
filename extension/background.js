// Service worker: attaches runs to tabs and relays narration audio to the offscreen player.
"use strict";

const DEFAULT_SERVER = "http://127.0.0.1:8765";

async function settings() {
  const s = await chrome.storage.local.get({ server: DEFAULT_SERVER });
  return s;
}

async function ensureOffscreen() {
  const have = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (have.length) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["AUDIO_PLAYBACK"],
    justification: "Play audio description clips in sync with the page video",
  });
}

async function attach(tabId, run, opts) {
  const { server } = await settings();
  const r = await fetch(`${server}/api/runs/${encodeURIComponent(run)}/cues`);
  if (!r.ok) throw new Error(`server: ${r.status} ${await r.text()}`);
  const data = await r.json();
  data.cues.forEach(c => { c.audio = server + c.audio; });
  await ensureOffscreen();
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  const res = await chrome.tabs.sendMessage(tabId, { type: "ad-attach", data, opts });
  if (!res || !res.ok) throw new Error((res && res.error) || "no response from the page");
  await chrome.storage.session.set({ [`tab-${tabId}`]: { run, opts, cues: data.cues.length, video: res.video } });
  return res;
}

async function detach(tabId) {
  try { await chrome.tabs.sendMessage(tabId, { type: "ad-detach" }); } catch (e) { /* tab gone */ }
  await relay({ action: "stop" });
  await chrome.storage.session.remove(`tab-${tabId}`);
}

async function relay(msg) {
  try { await ensureOffscreen(); return await chrome.runtime.sendMessage({ target: "offscreen", ...msg }); }
  catch (e) { return { ok: false, error: String(e) }; }
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.target === "offscreen") return false;              // meant for the offscreen document
  if (msg.type === "audio") { relay(msg).then(reply); return true; }   // from a content script
  if (msg.type === "attach") { attach(msg.tabId, msg.run, msg.opts).then(r => reply({ ok: true, ...r }), e => reply({ ok: false, error: e.message })); return true; }
  if (msg.type === "detach") { detach(msg.tabId).then(() => reply({ ok: true })); return true; }
  return false;
});

chrome.commands.onCommand.addListener(async (command, tab) => {
  const tabId = tab ? tab.id : (await chrome.tabs.query({ active: true, currentWindow: true }))[0]?.id;
  if (!tabId) return;
  const key = `tab-${tabId}`;
  const info = (await chrome.storage.session.get(key))[key];
  if (!info) return;                                   // not attached to this tab
  if (command === "toggle-narration") {
    info.opts.audio = !info.opts.audio;
    await chrome.tabs.sendMessage(tabId, { type: "ad-update", opts: { audio: info.opts.audio }, announce: info.opts.audio ? "Narration on" : "Narration off" });
    if (!info.opts.audio) await relay({ action: "stop" });
    await chrome.storage.session.set({ [key]: info });
  } else if (command === "repeat-last") {
    await chrome.tabs.sendMessage(tabId, { type: "ad-repeat" });
  }
});

// the page switched to another video (e.g. YouTube navigation without reload): forget the attachment
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg.type === "detached-by-page" && sender.tab) { chrome.storage.session.remove(`tab-${sender.tab.id}`); relay({ action: "stop" }); }
  return false;
});

chrome.tabs.onRemoved.addListener(tabId => chrome.storage.session.remove(`tab-${tabId}`));
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === "loading") { chrome.storage.session.remove(`tab-${tabId}`); relay({ action: "stop" }); }
});

// exposed for automated tests
self.adAttach = attach;
self.adDetach = detach;
