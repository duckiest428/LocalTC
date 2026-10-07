/* The Flight Tracker: the flight in progress, live, as the companion app sees it. In the dashboard it's a preview (the
   map and the facts); on its own page (/tracker) it fills the screen with the radio and the intercom beside it, as
   the phone shows them: COM1, COM2 and INT (the copilot) along the bottom, each the channel you see and the one you
   talk on, and a play button on each transmission LocalTC kept the audio of.

   A WebSocket to the account's relay. While it's open the app sends the aircraft, the path flown, traffic, radio, the
   route and the ATC zones its Live Map draws (if its "map away from home" setting is on); they pass through the
   server's memory and are never stored. atcmap.js draws the route, the zones and the traffic, as in the app; api.js
   has the account calls. */

const Tracker = {
  ws: null, retry: 0, timer: null, ping: null, map: null, plane: null, trail: null, path: AtcMap.track(), tfc: new Map(),
  status: { active: false }, gotOwn: false, follow: true, wanted: false,
  tiles: null, routeLayer: null, zoneLayer: null, runwayLayer: null, route: null, routeLine: null, zones: null,
  mode: "ifr", rulesSeen: null, zonesOn: true, talkTo: "atc", drawnRef: null, lastOwn: null, lines: [],
  unread: new Set(), playing: null,

  start() {
    if (this.wanted) return;
    this.wanted = true;
    this.connect();
    document.addEventListener("visibilitychange", this.onVisibility);
  },
  stop() {
    this.wanted = false;
    document.removeEventListener("visibilitychange", this.onVisibility);
    this.close();
  },
  // Hidden tab: let go, so the app stops sending the position for nobody.
  onVisibility: () => (document.hidden ? Tracker.close() : Tracker.wanted && Tracker.connect()),

  connect() {
    if (this.ws || !this.wanted) return;
    clearTimeout(this.timer);
    this.conn("Connecting ...");
    const ws = new WebSocket(`${API.replace(/^http/, "ws")}/v1/live/ws`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.conn("Live", "on");
      this.ping = setInterval(() => ws.readyState === 1 && ws.send("ping"), 30000);
    };
    ws.onmessage = (e) => {
      if (e.data === "pong") return;
      try { const m = JSON.parse(e.data); this.handle(m.type, m.data); } catch { /* not ours */ }
    };
    ws.onclose = () => {
      clearInterval(this.ping);
      this.ws = null;
      if (!this.wanted || document.hidden) return;
      this.retry = Math.min(this.retry + 1, 6);
      this.conn("Reconnecting ...");
      this.timer = setTimeout(() => this.connect(), 1000 * 2 ** this.retry);
    };
  },
  close() {
    clearTimeout(this.timer);
    clearInterval(this.ping);
    if (this.ws) { const ws = this.ws; this.ws = null; ws.onclose = null; ws.close(); }
    this.conn("Paused");
  },
  conn(text, cls = "") {
    const c = $("#tr-conn");
    c.textContent = text;
    c.className = `tr-conn ${cls}`;
  },

  handle(type, data) {
    if (type === "hello") {
      this.statusIs(data.status || { active: false });
      this.clearMap();
      this.setRoute(data.route || null);
      this.setZones(data.zones || null);
      if (data.trail) this.setTrail(data.trail);
      if (data.own) this.own(data.own);
      if (data.traffic) this.traffic(data.traffic);
      this.lines = [];
      for (const line of data.radio || []) this.radio(line, true);
      this.renderRadio();
    } else if (type === "status") this.statusIs(data);
    else if (type === "own") this.own(data);
    else if (type === "trail") this.setTrail(data);
    else if (type === "route") this.setRoute(data);
    else if (type === "zones") this.setZones(data);
    else if (type === "traffic") this.traffic(data);
    else if (type === "radio") this.radio(data);
  },

  statusIs(s) {
    const was = this.status.active;
    this.status = s;
    const side = $("#side-live");  // the dashboard's sidebar dot
    if (side) side.hidden = !s.active;
    this.syncTalk();
    $("#tr-body").hidden = !s.active;
    if (!s.active) {
      $("#tr-status").innerHTML = `<p class="hint">No flight right now. Start one in the LocalTC app, signed in with this
        account and the companion on (Settings (the gear) → Account), and it shows up here.</p>`;
      $("#tr-nomap").hidden = true;
      if (was) this.clearMap();
      return;
    }
    const st = (x) => (x && x.station ? `${esc(x.station)} <span class="mono">${x.mhz ? Number(x.mhz).toFixed(3) : ""}</span>` : "—");
    $("#tr-status").innerHTML = `<div class="tr-head"><span class="live-dot"></span><b>${esc(s.callsign || "Flying")}</b>
      <span class="mono">${esc(s.origin || "?")} → ${esc(s.destination || "?")}</span>
      <span class="tr-phase">${esc(s.phase_label || s.phase || "")}</span>
      ${s.rules ? `<span class="tr-rules">${esc(s.rules)}</span>` : ""}</div>
      ${s.last_atc && s.last_atc.text ? `<p class="last-atc">“${esc(s.last_atc.text)}”</p>` : ""}`;
    const fact = (k, v) => `<div><dt>${k}</dt><dd>${v}</dd></div>`;
    $("#tr-facts").innerHTML = [
      fact("On", st(s.tuned)), fact("Next", st(s.next)),
      fact("Squawk", `<span class="mono">${esc(s.squawk || "—")}</span>`),
      fact("Assigned", s.altitude_ft ? `<span class="mono">${Number(s.altitude_ft).toLocaleString()} ft</span>` : "—"),
      fact("Runway", esc(s.runway || "—")),
      fact("To go", s.ete ? `<span class="mono">${esc(s.ete.nm)} nm · ${esc(s.ete.min)} min</span>` : "—"),
      ...(s.gate ? [fact("Gate", esc(s.gate))] : []),
    ].join("");
    this.ensureMap();
    this.syncRules();
    // No position a while into the flight: the app keeps it at home (its setting), say so.
    clearTimeout(this.noMapTimer);
    this.noMapTimer = setTimeout(() => { $("#tr-nomap").hidden = this.gotOwn || !this.status.active; }, 12000);
  },

  ensureMap() {
    if (this.map) { setTimeout(() => this.map.invalidateSize(), 0); return; }
    this.map = L.map("tr-map").setView([39, -98], 4);  // no world-copy jumping: atcmap.js keeps it all in one copy
    this.tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 16,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(this.map);
    // Bottom to top, as in the app: the zones, the runways, the route, the path flown, then the aircraft.
    this.zoneLayer = L.layerGroup().addTo(this.map);
    this.runwayLayer = L.layerGroup().addTo(this.map);
    this.routeLayer = L.layerGroup().addTo(this.map);
    this.trail = L.polyline([], { className: "tr-trail", weight: 2.5 }).addTo(this.map);
    const controls = L.control({ position: "topright" });
    controls.onAdd = () => {
      const bar = L.DomUtil.create("div", "tr-controls");
      bar.innerHTML = `<span class="tr-seg" role="group" aria-label="Map type" title="IFR: who controls what. VFR: a light map and the airspace classes. Opens on the flight's rules.">
          <button type="button" data-mode="ifr" aria-pressed="true">IFR</button><button type="button" data-mode="vfr" aria-pressed="false">VFR</button></span>
        <button type="button" data-act="zones" aria-pressed="true" title="Show who controls which airspace">ATC zones</button>
        <button type="button" data-act="route" title="The whole flight plan route">Route</button>
        <button type="button" data-act="follow" aria-pressed="true">Follow</button>`;
      L.DomEvent.disableClickPropagation(bar);
      L.DomEvent.on(bar, "click", (e) => {
        const b = e.target.closest("button");
        if (!b) return;
        if (b.dataset.mode) this.setMode(b.dataset.mode);
        else if (b.dataset.act === "zones") this.setZonesOn(!this.zonesOn);
        else if (b.dataset.act === "route") this.showRoute();
        else if (b.dataset.act === "follow") { this.setFollow(true); if (this.plane) this.map.panTo(this.plane.getLatLng()); }
      });
      return bar;
    };
    controls.addTo(this.map);
    this.map.on("dragstart", () => this.setFollow(false));
    // The key folds to one line (who the flight talks to) so it doesn't cover the map; a click opens it.
    const legend = L.DomUtil.create("div", "maplegend tr-legend folded", $("#tr-map"));
    legend.id = "tr-legend";
    legend.innerHTML = `<button type="button" class="lg-fold" aria-expanded="false">Map key</button>${AtcMap.LEGEND}`;
    legend.querySelector(".lg-fold").onclick = (e) => {
      const open = legend.classList.toggle("folded") === false;
      e.currentTarget.setAttribute("aria-expanded", String(open));
    };
    L.DomEvent.disableClickPropagation(legend);
    this.setRoute(this.route);  // what came before the map was first shown
    this.setZones(this.zones);
    this.setMode(this.mode);
  },
  /* The copy of the world everything is drawn in: the aircraft's, as the path flown has it. Crossing the date line
     moves it on, and the route, the zones and the runways are drawn again there. */
  useRef(lon) {
    AtcMap.setRef(lon);
    if (this.drawnRef != null && Math.abs(lon - this.drawnRef) <= 90) return;
    this.drawnRef = lon;
    this.setRoute(this.route);
    this.setZones(this.zones);
  },
  setFollow(on) {
    this.follow = on;
    $("#tr-map .tr-controls [data-act=follow]")?.setAttribute("aria-pressed", String(on));
  },
  /* IFR or VFR, as in the app: the map opens on the flight's rules and follows them when they change; the
     switch works any time. VFR is the light map (no terrain tiles here: only OpenStreetMap's are used). */
  syncRules() {
    const rules = String(this.status.rules || "IFR").toLowerCase() === "vfr" ? "vfr" : "ifr";
    if (rules !== this.rulesSeen) { this.rulesSeen = rules; this.setMode(rules); }
  },
  setMode(mode) {
    this.mode = mode === "vfr" ? "vfr" : "ifr";
    if (!this.map) return;
    $("#tr-map").dataset.mode = this.mode;
    $("#tr-map").classList.toggle("fm-tiles", this.mode === "ifr");  // IFR dark, VFR light like a chart
    document.querySelectorAll("#tr-map .tr-seg [data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === this.mode)));
    this.drawZones();
  },
  setZonesOn(on) {
    this.zonesOn = on;
    $("#tr-map .tr-controls [data-act=zones]")?.setAttribute("aria-pressed", String(on));
    this.drawZones();
  },
  // The flight plan's route: the line through its fixes, or a straight one when it was typed without them.
  setRoute(route) {
    this.route = route;
    if (!this.routeLayer) return;
    this.routeLayer.clearLayers();
    this.routeLine = route ? AtcMap.route(this.routeLayer, route) : null;
    this.straightRoute();
  },
  straightRoute() {
    if (this.routeLine || !this.route || !this.zones) return;
    const at = (icao) => (this.zones.airports || []).find((a) => a.icao === icao && a.lat != null);
    const from = at(this.route.origin), to = at(this.route.destination);
    if (from && to && from !== to) this.routeLine = AtcMap.straight(this.routeLayer, from, to);
  },
  showRoute() {
    this.setFollow(false);
    const line = this.routeLine || (this.path.pts.length > 1 ? this.trail : null);
    if (line) this.map.fitBounds(line.getBounds(), { padding: [30, 30] });
  },
  // The Live Map's ATC layer, worked out by the app for the flight (ui/zones.py): redrawn when it changes.
  setZones(z) {
    this.zones = z;
    if (!this.map) return;
    this.runwayLayer.clearLayers();
    for (const a of (z && z.airports) || []) AtcMap.runways(this.runwayLayer, a);
    this.straightRoute();
    this.drawZones();
  },
  drawZones() {
    if (!this.zoneLayer) return;
    this.zoneLayer.clearLayers();
    const legend = $("#tr-legend");
    if (legend) legend.hidden = !this.zonesOn || !this.zones;
    if (!this.zonesOn || !this.zones) return;
    AtcMap.zones(this.zoneLayer, this.zones, { vfr: this.mode === "vfr" });
    if (legend) legend.querySelector(".lg-talk").innerHTML = AtcMap.talk(this.zones);
  },
  clearMap() {
    this.gotOwn = false;
    this.drawnRef = null;
    AtcMap.setRef(null);
    this.setRoute(null);
    this.setZones(null);
    this.path = AtcMap.track();
    if (this.trail) this.trail.setLatLngs([]);
    if (this.plane) { this.plane.remove(); this.plane = null; }
    for (const m of this.tfc.values()) m.remove();
    this.tfc.clear();
  },
  // The path flown before this page opened (the app sends it): the live positions carry on from its end.
  setTrail(points) {
    if (!Array.isArray(points) || !points.length) return;
    this.ensureMap();
    // It runs up to now: it replaces what this page drew itself, ending where the aircraft is.
    this.path.set(points);
    if (this.plane) { const at = this.plane.getLatLng(); this.path.pts.push([at.lat, this.path.lon(at.lng)]); }
    this.trail.setLatLngs(this.path.pts);
    this.useRef(this.path.pts[this.path.pts.length - 1][1]);
    if (!this.plane && this.follow) this.map.fitBounds(this.trail.getBounds(), { padding: [30, 30], maxZoom: 11 });
  },
  icon(hdg, cls, size) {
    const svg = `<svg viewBox="0 0 32 32" width="${size}" height="${size}"><path d="M16 2c1.2 0 2 1.4 2 3v7l11 6v3l-11-3v6l3 2v2.5l-5-1.5-5 1.5V26l3-2v-6L3 21v-3l11-6V5c0-1.6.8-3 2-3z"/></svg>`;
    return L.divIcon({ className: `tr-plane ${cls}`, html: `<div style="transform:rotate(${Number(hdg) || 0}deg)">${svg}</div>`,
      iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
  },
  own(o) {
    if (!this.status.active || o.lat == null) return;
    this.ensureMap();
    this.gotOwn = true;
    $("#tr-nomap").hidden = true;
    this.lastOwn = o;
    if (this.path.add(o.lat, o.lon, o.ground)) this.trail.setLatLngs(this.path.pts);
    const at = [o.lat, this.path.lon(o.lon)];
    this.useRef(at[1]);
    if (!this.plane) { this.plane = L.marker(at, { icon: this.icon(o.hdg, "own", 30), zIndexOffset: 1000 }).addTo(this.map); this.map.setView(at, o.ground ? 13 : 9); }
    else { this.plane.setLatLng(at); this.plane.setIcon(this.icon(o.hdg, "own", 30)); }
    if (this.follow) this.map.panTo(at, { animate: false });
    this.plane.bindTooltip(`${Number(o.alt || 0).toLocaleString()} ft · ${o.gs ?? "—"} kt · ${String(o.hdg ?? 0).padStart(3, "0")}°`);
  },
  // The traffic around, labelled as on the app's Live Map (atcmap.js).
  traffic(list) {
    if (!this.map) return;
    AtcMap.traffic(this.map, this.tfc, list);
  },
  /* The channels, as on the phone: COM1 and COM2 (ATC, you, and the copilot when it works the radio) and INT (you and
     the copilot on the intercom, when it's on in the app). The one picked is what the log shows and where a typed line
     goes. A dot on another one: something new there. */
  syncTalk() {
    const crew = !!this.status.crew;
    const int = document.querySelector("#tr-to [data-to=crew]");
    if (int) {
      int.disabled = !crew;
      int.title = crew ? "The copilot, on the intercom" : "The copilot isn't on in LocalTC (Quick Settings → Copilot → Intercom)";
    }
    if (!crew && this.talkTo === "crew") this.setTalk("atc");
  },
  setTalk(to) {
    this.talkTo = to;
    this.unread.delete(to);
    document.querySelectorAll("#tr-to [data-to]").forEach((b) => {
      b.setAttribute("aria-selected", String(b.dataset.to === to));
      b.classList.toggle("unread", this.unread.has(b.dataset.to));
    });
    const input = $("#tr-say-text");
    if (input) {
      input.placeholder = to === "crew" ? "Say it to the copilot: \"flaps one\", \"how much fuel?\", \"brief\" ..."
        : `Type a radio call on ${to === "com2" ? "COM2" : "COM1"} ...`;
      input.setAttribute("aria-label", to === "crew" ? "Something to say to the copilot" : "A radio call to transmit");
    }
    const send = $("#tr-say-send");
    if (send) send.setAttribute("aria-label", to === "crew" ? "Say it to the copilot" : "Transmit");
    this.renderRadio();
  },
  /** The channel a line is on: the intercom, else COM2 when it says so (or ATC's frequency is COM2's), else COM1. */
  channel(line) {
    if (line.kind === "crew" || line.kind === "intercom") return "crew";
    if (line.radio === 2) return "com2";
    const o = this.lastOwn, f = Number(line.mhz);
    if (!line.radio && f && o && o.com2 && Math.abs(f - o.com2) < 0.006 && !(o.com1 && Math.abs(f - o.com1) < 0.006)) return "com2";
    return "atc";
  },
  radio(line, quiet = false) {
    const note = $("#tr-say-note");
    if (line.kind === "alert" && /^Not transmitted/.test(line.text || "")) { if (note) note.textContent = line.text; return; }
    if (!["atc", "pilot", "copilot", "crew", "intercom", "chatter"].includes(line.kind) || !line.text) return;
    line.ch = this.channel(line);
    this.lines.push(line);
    if (this.lines.length > 200) this.lines.shift();
    if (quiet) return;
    if (line.ch !== this.talkTo) {
      this.unread.add(line.ch);
      document.querySelector(`#tr-to [data-to=${line.ch}]`)?.classList.add("unread");
      return;
    }
    const list = $("#tr-radio");
    if (!list) return;
    list.insertAdjacentHTML("beforeend", this.bubble(line));
    while (list.children.length > 120) list.firstElementChild.remove();
    list.scrollTop = list.scrollHeight;
  },
  renderRadio() {
    const list = $("#tr-radio");
    if (!list) return;
    const shown = this.lines.filter((l) => l.ch === this.talkTo).slice(-120);
    list.innerHTML = shown.length ? shown.map((l) => this.bubble(l)).join("")
      : `<li class="msg-empty">${this.talkTo === "crew" ? "Nothing on the intercom yet. The copilot speaks up when there's something to say, and answers anything you say to it here."
        : "Quiet on the frequency."}</li>`;
    list.scrollTop = list.scrollHeight;
  },
  bubble(l) {
    const mine = l.kind === "pilot" || l.kind === "intercom";
    const who = { atc: l.station || "ATC", pilot: "You", copilot: "Copilot", crew: "Copilot", intercom: "You",
      chatter: l.station || "Other traffic" }[l.kind];
    const meta = [l.mhz ? Number(l.mhz).toFixed(3) : "", l.ch === "crew" ? "INT" : l.ch === "com2" ? "COM2" : "COM1"].filter(Boolean).join(" · ");
    const play = l.audio ? `<button type="button" class="msg-play" data-audio="${esc(l.audio)}" aria-label="Play">${PLAY_ICON}</button>` : "";
    return `<li class="msg ${esc(l.kind)}${mine ? " mine" : ""}${l.level === "warn" ? " warn" : ""}"><div class="msg-bubble">
      <div class="msg-who">${play}<b>${esc(who)}</b></div><div class="msg-text">${esc(l.text)}</div>
      <div class="msg-meta">${esc(meta)}</div></div></li>`;
  },
  /* A transmission as it was heard (ATC's and the copilot's voices, yours as the microphone took it): from the
     account's relay, which holds the last few while you watch. Only when you press play. */
  async play(btn) {
    const id = btn.dataset.audio;
    if (this.playing) { this.playing.audio.pause(); this.playing.btn.classList.remove("on"); }
    if (this.playing && this.playing.btn === btn) { this.playing = null; return; }
    btn.classList.add("on");
    try {
      const res = await fetch(`${API}/v1/live/clip/${encodeURIComponent(id)}`, { credentials: "include", headers: { "X-LocalTC": "1" } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "Not available");
      const audio = new Audio(URL.createObjectURL(await res.blob()));
      this.playing = { audio, btn };
      audio.onended = () => { btn.classList.remove("on"); if (this.playing && this.playing.audio === audio) this.playing = null; };
      await audio.play();
    } catch (err) {
      btn.classList.remove("on");
      btn.title = err.message;
      btn.classList.add("gone");
      this.playing = null;
    }
  },
};

const PLAY_ICON = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>';

// --- the full-screen page (/tracker): the map takes the screen, and you can talk ----------------------------------

// A line typed here: the account's relay holds it until the app picks it up (a second or two while this page
// watches). A radio call goes out on COM1 (or COM2) as if typed in the app, ATC's answer coming back in the log; a line
// for the copilot is said to it on the intercom, its answer coming back the same way.
if ($("#tr-say")) {
  $("#tr-to").onclick = (e) => { const b = e.target.closest("[data-to]"); if (b && !b.disabled) Tracker.setTalk(b.dataset.to); };
  $("#tr-radio").onclick = (e) => { const b = e.target.closest(".msg-play"); if (b) Tracker.play(b); };
  $("#tr-say").onsubmit = async (e) => {
    e.preventDefault();
    const input = $("#tr-say-text");
    const text = input.value.trim();
    if (!text) return;
    const note = $("#tr-say-note");
    const to = Tracker.talkTo;
    note.textContent = "Sending ...";
    try {
      await api("POST", "/v1/live/say", { text, to });
      input.value = "";
      note.textContent = to === "crew" ? "Said to the copilot: it answers on the intercom." : `Sent: it goes out on ${to === "com2" ? "COM2" : "COM1"} in a moment.`;
    } catch (err) { note.textContent = err.message; }
  };
}

if (document.body.dataset.page === "tracker") {
  (async () => {
    try {
      await api("GET", "/v1/me");
    } catch (err) {
      $("#tr-status").innerHTML = err.status === 401
        ? `<p class="hint">Sign in on the <a href="dashboard">dashboard</a> first, then come back here.</p>`
        : `<p class="hint">Can't reach the LocalTC server: ${esc(err.message)}</p>`;
      return;
    }
    Tracker.setTalk("atc");
    Tracker.start();
  })();
}
