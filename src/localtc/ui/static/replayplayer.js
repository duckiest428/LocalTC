/* A flight to rewatch: the aircraft moving along its track on a map, a timeline to scrub, and the radio
   transcript in step with it. Plays a replay (docs/replay-format.md, v1): the app builds them from its
   recordings, the website gets the ones the pilot uploaded. Needs Leaflet, and replayplayer.css.

   One file, two copies: site/replayplayer.js (localtc.tech's Dashboard) and
   src/localtc/ui/static/replayplayer.js (the app's Logbook). tests/test_site.py keeps them identical. */

class ReplayPlayer {
  static SPEEDS = [1, 4, 16, 64];
  static QUIET_S = 45;  // skipping quiet stretches: a gap longer than this ...
  static LEAD_S = 8;  // ... jumps to this long before the next call

  /** ``el``: an empty element to fill. ``tiles``: an OpenStreetMap background (false: a plain dark map). */
  constructor(el, replay, { tiles = true } = {}) {
    if (!replay || replay.v !== 1) throw new Error("This replay is from a newer LocalTC: update to play it.");
    this.el = el;
    this.r = replay;
    this.track = replay.track;
    this.duration = replay.flight.duration_s || this.track.t[this.track.t.length - 1] || 0;
    this.t = 0;
    this.speed = 4;
    this.playing = false;
    this.skipQuiet = true;
    this.follow = false;
    this.userScrolled = 0;
    this.shownIdx = -1;
    this.line = -1;
    this.frame = null;
    this.phases = (replay.marks || []).filter((m) => m.kind === "phase");
    this.calls = replay.radio.filter((l) => l.kind === "atc" || l.kind === "pilot" || l.kind === "copilot");
    this.events = [...replay.radio.map((l) => l.t), ...(replay.marks || []).map((m) => m.t)].sort((a, b) => a - b);
    this.render(tiles);
    this.seek(0);
    this.onKey = (e) => this.key(e);
    document.addEventListener("keydown", this.onKey);
  }

  destroy() {
    this.pause();
    document.removeEventListener("keydown", this.onKey);
    if (this.map) this.map.remove();
    this.el.innerHTML = "";
  }

  // --- building the page ---------------------------------------------------------------------------------------

  static esc(v) {
    return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  static clock(s) {
    s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }

  render(tiles) {
    const esc = ReplayPlayer.esc, f = this.r.flight;
    const day = f.started_at ? new Date(f.started_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";
    this.el.classList.add("replay");
    this.el.innerHTML = `
      <div class="rp-head">
        <div class="rp-title"><b class="rp-mono">${esc(f.callsign || "Flight")}</b>
          <span class="rp-mono">${esc(f.origin || "?")} → ${esc(f.destination || "?")}</span>
          <span class="rp-sub">${esc([f.aircraft, day, ReplayPlayer.clock(this.duration)].filter(Boolean).join(" · "))}</span></div>
      </div>
      <div class="rp-body">
        <div class="rp-mapwrap">
          <div class="rp-map" aria-label="Map of the flight"></div>
          <div class="rp-readout rp-mono" aria-live="off"></div>
        </div>
        <div class="rp-side">
          <div class="rp-list-h">Radio <span class="rp-sub">${this.calls.length} calls · click one to go there</span></div>
          <ol class="rp-list" tabindex="0" aria-label="Radio transcript">${this.r.radio.map((l, i) => this.lineHtml(l, i)).join("")}</ol>
        </div>
      </div>
      <div class="rp-bar">
        <div class="rp-controls">
          <button type="button" class="rp-btn rp-prev" title="Previous call (J)" aria-label="Previous call">&#9198;</button>
          <button type="button" class="rp-btn rp-play" title="Play (space)" aria-label="Play">&#9654;</button>
          <button type="button" class="rp-btn rp-next" title="Next call (K)" aria-label="Next call">&#9197;</button>
          <span class="rp-time rp-mono"></span>
          <span class="rp-grow"></span>
          <label class="rp-check" title="Jump over stretches with nothing said"><input type="checkbox" class="rp-quiet" ${this.skipQuiet ? "checked" : ""}> Skip quiet</label>
          <span class="rp-speeds" role="group" aria-label="Speed">${ReplayPlayer.SPEEDS.map((s) =>
            `<button type="button" class="rp-speed${s === this.speed ? " on" : ""}" data-speed="${s}">${s}×</button>`).join("")}</span>
        </div>
        <div class="rp-scrub">
          <div class="rp-ticks">${this.ticksHtml()}</div>
          <input type="range" class="rp-range" min="0" max="${this.duration}" step="0.1" value="0" aria-label="Time in the flight">
        </div>
      </div>`;
    const q = (s) => this.el.querySelector(s);
    this.ui = { readout: q(".rp-readout"), list: q(".rp-list"), play: q(".rp-play"), time: q(".rp-time"), range: q(".rp-range") };
    this.items = [...this.ui.list.children];
    q(".rp-play").onclick = () => (this.playing ? this.pause() : this.play());
    q(".rp-prev").onclick = () => this.jump(-1);
    q(".rp-next").onclick = () => this.jump(1);
    q(".rp-quiet").onchange = (e) => { this.skipQuiet = e.target.checked; };
    q(".rp-speeds").onclick = (e) => {
      const b = e.target.closest("[data-speed]");
      if (!b) return;
      this.speed = Number(b.dataset.speed);
      this.el.querySelectorAll(".rp-speed").forEach((x) => x.classList.toggle("on", x === b));
    };
    this.ui.range.oninput = () => this.seek(Number(this.ui.range.value));
    this.ui.list.onclick = (e) => {
      const li = e.target.closest("li[data-i]");
      if (li) this.seek(this.r.radio[Number(li.dataset.i)].t, { scroll: false });
    };
    this.ui.list.addEventListener("wheel", () => { this.userScrolled = performance.now(); }, { passive: true });
    this.ui.list.addEventListener("touchmove", () => { this.userScrolled = performance.now(); }, { passive: true });
    this.drawMap(q(".rp-map"), tiles);
  }

  lineHtml(l, i) {
    const esc = ReplayPlayer.esc;
    const at = `<span class="rp-at rp-mono">+${ReplayPlayer.clock(l.t)}</span>`;
    const mhz = l.mhz ? ` <span class="rp-sub rp-mono">${Number(l.mhz).toFixed(3)}</span>` : "";
    if (l.kind === "phase") return `<li data-i="${i}" class="rp-l phase">${at}<span>${esc(l.text)}</span></li>`;
    if (l.kind === "tuned") return `<li data-i="${i}" class="rp-l tuned">${at}<span>Tuned ${mhz} ${esc(l.text)}</span></li>`;
    if (l.kind === "alert") return `<li data-i="${i}" class="rp-l alert">${at}<span>${esc(l.text)}</span></li>`;
    const who = l.kind === "atc" ? esc(l.station) : l.kind === "atis" ? esc(l.station) : l.kind === "copilot" ? "Copilot" : "You";
    const rb = l.ok === true ? '<span class="rp-rb ok" title="Readback correct">✓</span>'
      : l.ok === false ? `<span class="rp-rb bad" title="${esc(l.readback || "Readback not right")}">✗ ${esc(l.readback || "")}</span>` : "";
    const text = l.kind === "atis" ? `<details><summary>ATIS</summary>${esc(l.text)}</details>` : esc(l.text);
    return `<li data-i="${i}" class="rp-l ${l.kind}">${at}<div><div class="rp-who">${who}${mhz}${rb}</div><div class="rp-text${l.unclear ? " unclear" : ""}">${text}</div></div></li>`;
  }

  ticksHtml() {
    const at = (t) => `${((t / (this.duration || 1)) * 100).toFixed(3)}%`;
    const esc = ReplayPlayer.esc;
    const calls = this.calls.map((l) => `<i class="rp-tick ${l.kind === "atc" ? "atc" : "pilot"}" style="left:${at(l.t)}"></i>`);
    const marks = (this.r.marks || []).filter((m) => m.kind !== "phase" || /Takeoff|Final|Landed|Airborne|Taxiing/.test(m.text))
      .map((m) => `<i class="rp-mark ${m.kind}" style="left:${at(m.t)}" title="+${ReplayPlayer.clock(m.t)} ${esc(m.text)}"></i>`);
    return calls.join("") + marks.join("");
  }

  drawMap(el, tiles) {
    this.map = L.map(el, { worldCopyJump: true, attributionControl: tiles, zoomSnap: 0.5 });
    if (tiles) {
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 16,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(this.map);
    }
    el.classList.add(tiles ? "fm-tiles" : "fm-plain");
    const pts = this.track.t.map((_, i) => [this.track.lat[i], this.track.lon[i]]);
    this.pts = pts;
    if (this.r.route?.length) {
      L.polyline(this.r.route.map((f) => [f.lat, f.lon]), { className: "rp-route", weight: 1.5, dashArray: "4 6", interactive: false }).addTo(this.map);
    }
    L.polyline(pts, { className: "rp-track", weight: 2, interactive: false }).addTo(this.map);
    this.flown = L.polyline([], { className: "rp-flown", weight: 3, interactive: false }).addTo(this.map);
    for (const [icao, a] of Object.entries(this.r.airports || {})) {
      L.circleMarker([a.lat, a.lon], { radius: 5, className: "rp-apt" }).bindTooltip(icao, { permanent: true, direction: "right", className: "rp-apt-label" }).addTo(this.map);
    }
    this.plane = L.marker(pts[0], { icon: this.icon(this.track.hdg[0]), zIndexOffset: 1000, keyboard: false }).addTo(this.map);
    this.bounds = L.latLngBounds(pts);
    this.map.on("dragstart", () => this.setFollow(false));
    const ctl = L.control({ position: "topright" });
    ctl.onAdd = () => {
      const box = L.DomUtil.create("div", "rp-mapbtns");
      box.innerHTML = '<button type="button" class="rp-follow">Follow</button><button type="button" class="rp-fit">Whole flight</button>';
      L.DomEvent.disableClickPropagation(box);
      box.querySelector(".rp-follow").onclick = () => this.setFollow(!this.follow);
      box.querySelector(".rp-fit").onclick = () => { this.setFollow(false); this.fit(); };
      return box;
    };
    ctl.addTo(this.map);
    this.fit();
  }

  /** Leaflet measures the map when it's shown: call after the player becomes visible. */
  fit() {
    this.map.invalidateSize();
    if (this.bounds.isValid()) this.map.fitBounds(this.bounds, { padding: [24, 24], maxZoom: 13 });
  }

  setFollow(on) {
    this.follow = on;
    this.el.querySelector(".rp-follow")?.classList.toggle("on", on);
    if (on) {
      const s = this.sample(this.t);
      this.map.setView([s.lat, s.lon], Math.max(this.map.getZoom(), s.gnd ? 14 : 9));
    }
  }

  icon(hdg) {
    const svg = '<svg viewBox="0 0 32 32" width="30" height="30"><path d="M16 2c1.2 0 2 1.4 2 3v7l11 6v3l-11-3v6l3 2v2.5l-5-1.5-5 1.5V26l3-2v-6L3 21v-3l11-6V5c0-1.6.8-3 2-3z"/></svg>';
    return L.divIcon({ className: "rp-plane", html: `<div style="transform:rotate(${Number(hdg) || 0}deg)">${svg}</div>`, iconSize: [30, 30], iconAnchor: [15, 15] });
  }

  // --- time ------------------------------------------------------------------------------------------------------

  /** The last index in a sorted array at or before ``t`` (-1 before the first). */
  static before(arr, t, get = (x) => x) {
    let lo = 0, hi = arr.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (get(arr[mid]) <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  /** Where the aircraft was at ``t``, in between the track's points. */
  sample(t) {
    const k = this.track, i = Math.max(0, ReplayPlayer.before(k.t, t)), j = Math.min(i + 1, k.t.length - 1);
    const span = k.t[j] - k.t[i], f = span > 0 ? Math.min(1, Math.max(0, (t - k.t[i]) / span)) : 0;
    const lerp = (col) => k[col][i] + (k[col][j] - k[col][i]) * f;
    const turn = ((k.hdg[j] - k.hdg[i] + 540) % 360) - 180;
    return { i, lat: lerp("lat"), lon: lerp("lon"), alt: lerp("alt"), gs: lerp("gs"), vs: lerp("vs"),
      hdg: (k.hdg[i] + turn * f + 360) % 360, gnd: k.gnd[f < 0.5 ? i : j] };
  }

  seek(t, { scroll = true } = {}) {
    this.t = Math.min(this.duration, Math.max(0, t));
    this.show(scroll);
  }

  play() {
    if (this.t >= this.duration) this.t = 0;
    this.playing = true;
    this.ui.play.innerHTML = "&#10074;&#10074;";
    this.ui.play.setAttribute("aria-label", "Pause");
    this.ui.play.title = "Pause (space)";
    let last = performance.now();
    const tick = (now) => {
      if (!this.playing) return;
      let t = this.t + ((now - last) / 1000) * this.speed;
      last = now;
      if (this.skipQuiet) {
        const k = ReplayPlayer.before(this.events, this.t) + 1;
        const nextAt = k < this.events.length ? this.events[k] : this.duration;
        if (nextAt - this.t > ReplayPlayer.QUIET_S) t = Math.max(t, nextAt - ReplayPlayer.LEAD_S);
      }
      this.seek(t);
      if (this.t >= this.duration) return this.pause();
      this.frame = requestAnimationFrame(tick);
    };
    this.frame = requestAnimationFrame(tick);
  }

  pause() {
    this.playing = false;
    cancelAnimationFrame(this.frame);
    if (!this.ui) return;
    this.ui.play.innerHTML = "&#9654;";
    this.ui.play.setAttribute("aria-label", "Play");
    this.ui.play.title = "Play (space)";
  }

  /** To the next (1) or previous (-1) call on the radio. */
  jump(dir) {
    const calls = this.calls;
    let k = ReplayPlayer.before(calls, this.t - (dir < 0 ? 1 : 0), (l) => l.t);
    k = dir > 0 ? k + 1 : k;
    if (k >= 0 && k < calls.length) this.seek(calls[k].t);
  }

  key(e) {
    if (!this.el.isConnected || this.el.offsetParent === null || e.target.closest?.("input:not([type=range]), textarea, select")) return;
    const k = e.key;
    if (k === " ") { e.preventDefault(); this.playing ? this.pause() : this.play(); }
    else if (k === "ArrowRight" && e.target.type !== "range") this.seek(this.t + 10);
    else if (k === "ArrowLeft" && e.target.type !== "range") this.seek(this.t - 10);
    else if (k === "j" || k === "J") this.jump(-1);
    else if (k === "k" || k === "K") this.jump(1);
  }

  // --- drawing the moment ----------------------------------------------------------------------------------------

  show(scroll) {
    const s = this.sample(this.t), f = this.r.flight;
    const at = [s.lat, s.lon];
    this.plane.setLatLng(at);
    this.plane.setIcon(this.icon(s.hdg));
    if (s.i !== this.shownIdx) {
      this.flown.setLatLngs(this.pts.slice(0, s.i + 1).concat([at]));
      this.shownIdx = s.i;
    }
    if (this.follow) this.map.panTo(at, { animate: false });
    const p = ReplayPlayer.before(this.phases, this.t, (m) => m.t);
    const phase = p >= 0 ? this.phases[p].text : "";
    const zulu = f.zulu0 == null ? "" : (() => { const z = (f.zulu0 + this.t) % 86400; return `${String(Math.floor(z / 3600)).padStart(2, "0")}${String(Math.floor((z % 3600) / 60)).padStart(2, "0")}Z`; })();
    const vs = Math.round(s.vs / 50) * 50;
    this.ui.readout.innerHTML = [
      ["ALT", `${(Math.round(s.alt / 10) * 10).toLocaleString()}`], ["GS", `${Math.round(s.gs)}`],
      ["VS", `${vs > 0 ? "+" : ""}${vs.toLocaleString()}`], ["HDG", String(Math.round(s.hdg) % 360).padStart(3, "0")],
    ].map(([k, v]) => `<span><i>${k}</i>${v}</span>`).join("") + (phase ? `<span class="rp-phase">${ReplayPlayer.esc(phase)}</span>` : "");
    this.ui.time.textContent = `+${ReplayPlayer.clock(this.t)} / ${ReplayPlayer.clock(this.duration)}${zulu ? `  ·  ${zulu}` : ""}`;
    if (document.activeElement !== this.ui.range) this.ui.range.value = this.t;
    this.highlight(scroll);
  }

  highlight(scroll) {
    const line = ReplayPlayer.before(this.r.radio, this.t, (l) => l.t);
    if (line === this.line) return;
    const from = Math.min(line, this.line), to = Math.max(line, this.line);
    for (let i = Math.max(0, from); i <= to && i < this.items.length; i++) {
      this.items[i].classList.toggle("past", i < line);
      this.items[i].classList.toggle("now", i === line);
    }
    if (this.line === -1 || line === -1) this.items.forEach((li, i) => { li.classList.toggle("past", i < line); li.classList.toggle("now", i === line); });
    this.line = line;
    // Keep the current line in view, unless the pilot is reading further up or down the list.
    if (scroll && line >= 0 && performance.now() - this.userScrolled > 4000) {
      const list = this.ui.list, li = this.items[line];
      const top = li.offsetTop;  // the list is positioned: offsets are within it
      if (top < list.scrollTop || top + li.offsetHeight > list.scrollTop + list.clientHeight) {
        list.scrollTop = Math.max(0, top - list.clientHeight / 3);
      }
    }
  }
}

if (typeof window !== "undefined") window.ReplayPlayer = ReplayPlayer;
