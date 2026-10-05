/* The Flight Tracker: the flight in progress, live, as the companion app sees it. In the dashboard it's a preview
   (the map, the facts, the radio); on its own page (tracker.html) it fills the screen, and there you can type to ATC
   or to the copilot on the intercom.

   A WebSocket to the account's relay. While it's open the app sends the aircraft, the path flown, traffic, radio, the
   route and the ATC zones its Live Map draws (if its "map away from home" setting is on); they pass through the
   server's memory and are never stored. atcmap.js draws the route and the zones, as in the app; api.js has the
   account calls. */

const Tracker = {
  ws: null, retry: 0, timer: null, ping: null, map: null, plane: null, trail: null, path: AtcMap.track(), tfc: new Map(),
  status: { active: false }, gotOwn: false, follow: true, wanted: false,
  tiles: null, routeLayer: null, zoneLayer: null, runwayLayer: null, route: null, routeLine: null, zones: null,
  mode: "ifr", rulesSeen: null, zonesOn: true, talkTo: "atc",

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
      $("#tr-radio").innerHTML = "";
      for (const line of data.radio || []) this.radio(line);
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
        account and the companion on (Quick Settings → Account), and it shows up here.</p>`;
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
    this.map = L.map("tr-map", { worldCopyJump: true }).setView([39, -98], 4);
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
    if (this.plane) { const at = this.plane.getLatLng(); this.path.pts.push([at.lat, at.lng]); }
    this.trail.setLatLngs(this.path.pts);
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
    const at = [o.lat, o.lon];
    if (!this.plane) { this.plane = L.marker(at, { icon: this.icon(o.hdg, "own", 30), zIndexOffset: 1000 }).addTo(this.map); this.map.setView(at, o.ground ? 13 : 9); }
    else { this.plane.setLatLng(at); this.plane.setIcon(this.icon(o.hdg, "own", 30)); }
    if (this.path.add(o.lat, o.lon, o.ground)) this.trail.setLatLngs(this.path.pts);
    if (this.follow) this.map.panTo(at, { animate: false });
    this.plane.bindTooltip(`${Number(o.alt || 0).toLocaleString()} ft · ${o.gs ?? "—"} kt · ${String(o.hdg ?? 0).padStart(3, "0")}°`);
  },
  traffic(list) {
    if (!this.map) return;
    const seen = new Set();
    for (const t of list || []) {
      seen.add(t.id);
      const label = `${esc(t.callsign || "")} ${t.ground ? "GND" : Math.round((t.alt || 0) / 100).toString().padStart(3, "0")}`;
      const m = this.tfc.get(t.id);
      if (m) { m.setLatLng([t.lat, t.lon]); m.setIcon(this.icon(t.hdg, t.ground ? "ground" : "air", 18)); }
      else this.tfc.set(t.id, L.marker([t.lat, t.lon], { icon: this.icon(t.hdg, t.ground ? "ground" : "air", 18) })
        .bindTooltip(label).addTo(this.map));
    }
    for (const [id, m] of this.tfc) if (!seen.has(id)) { m.remove(); this.tfc.delete(id); }
  },
  /* Who a typed line is for: ATC on COM1, or the copilot on the intercom (when it's on in the app). */
  syncTalk() {
    const pick = $("#tr-to");
    if (!pick) return;
    const crew = !!this.status.crew;
    pick.hidden = !crew;
    if (!crew && this.talkTo === "crew") this.setTalk("atc");
  },
  setTalk(to) {
    this.talkTo = to;
    document.querySelectorAll("#tr-to [data-to]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.to === to)));
    const input = $("#tr-say-text");
    if (input) {
      input.placeholder = to === "crew" ? "Say it to the copilot: \"flaps one\", \"how much fuel?\", \"brief\" ..." : "Type a radio call, as in the app ...";
      input.setAttribute("aria-label", to === "crew" ? "Something to say to the copilot" : "A radio call to transmit");
    }
    const send = $("#tr-say-send");
    if (send) send.textContent = to === "crew" ? "Say" : "Transmit";
  },
  radio(line) {
    const note = $("#tr-say-note");
    if (line.kind === "alert" && /^Not transmitted/.test(line.text || "")) { if (note) note.textContent = line.text; return; }
    if (!["atc", "pilot", "copilot", "crew", "intercom"].includes(line.kind) || !line.text) return;
    const list = $("#tr-radio");
    // The intercom too: what you said to the copilot, and what it said (its callouts, its answers).
    const who = { atc: esc(line.station || "ATC"), copilot: "Copilot", pilot: "You", crew: "Copilot · intercom",
      intercom: "You · intercom" }[line.kind];
    list.insertAdjacentHTML("beforeend", `<li class="${esc(line.kind)}"><span class="who">${who}</span> ${esc(line.text)}</li>`);
    while (list.children.length > 40) list.firstElementChild.remove();
    list.scrollTop = list.scrollHeight;
  },
};

// --- the full-screen page (tracker.html): the map takes the screen, and you can talk ---------------------------

// A line typed here: the account's relay holds it until the app picks it up (a second or two while this page
// watches). A radio call goes out on COM1 as if typed in the app, ATC's answer coming back in the log; a line for the
// copilot is said to it on the intercom, its answer coming back the same way.
if ($("#tr-say")) {
  $("#tr-to").onclick = (e) => { const b = e.target.closest("[data-to]"); if (b) Tracker.setTalk(b.dataset.to); };
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
      note.textContent = to === "crew" ? "Sent to the copilot: it answers on the intercom." : "Sent to the app: it goes out on COM1 in a moment.";
    } catch (err) { note.textContent = err.message; }
  };
}

if (document.body.dataset.page === "tracker") {
  (async () => {
    try {
      await api("GET", "/v1/me");
    } catch (err) {
      $("#tr-status").innerHTML = err.status === 401
        ? `<p class="hint">Sign in on the <a href="dashboard.html">dashboard</a> first, then come back here.</p>`
        : `<p class="hint">Can't reach the LocalTC server: ${esc(err.message)}</p>`;
      return;
    }
    Tracker.setTalk("atc");
    Tracker.start();
  })();
}
