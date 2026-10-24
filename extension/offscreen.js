// Offscreen document: plays narration clips. Lives at the extension origin, so page CSP never blocks it.
"use strict";
const audio = new Audio();
audio.preload = "auto";
let current = null;
const cache = new Map();   // url -> preloaded Audio

function preload(urls) {
  for (const u of urls) {
    if (cache.has(u)) continue;
    const a = new Audio(); a.preload = "auto"; a.src = u; cache.set(u, a);
  }
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.target !== "offscreen") return false;
  try {
    if (msg.action === "preload") { preload(msg.urls || []); reply({ ok: true }); }
    else if (msg.action === "play") {
      const a = cache.get(msg.url) || audio;
      if (a === audio && audio.src !== msg.url) audio.src = msg.url;
      if (current && current !== a) current.pause();
      current = a;
      a.volume = Math.max(0, Math.min(1, msg.volume ?? 1));
      a.playbackRate = msg.rate || 1;
      const go = () => { a.currentTime = Math.max(0, msg.from || 0); a.play().catch(() => {}); };
      a.readyState >= 1 ? go() : a.addEventListener("loadedmetadata", go, { once: true });
      reply({ ok: true });
    } else if (msg.action === "pause" || msg.action === "stop") {
      if (current) current.pause();
      if (msg.action === "stop") current = null;
      reply({ ok: true });
    } else if (msg.action === "rate") {
      if (current) current.playbackRate = msg.rate || 1; reply({ ok: true });
    } else if (msg.action === "volume") {
      if (current) current.volume = msg.volume; reply({ ok: true });
    } else if (msg.action === "state") {
      reply({ ok: true, src: current ? current.src : null, paused: current ? current.paused : true,
              time: current ? current.currentTime : 0, volume: current ? current.volume : null });
    } else reply({ ok: false, error: "unknown action" });
  } catch (e) { reply({ ok: false, error: String(e) }); }
  return false;
});
