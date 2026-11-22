/* Live Flights (live.html): the public map of LocalTC pilots who chose to share their flight.

   GET /v1/sky/flights every 15 s (paused while the tab is hidden), cached at the edge by the account server, so
   it holds only what the pilots shared (server/src/sky.ts): no account, no email. Between two answers each aircraft
   moves on along its heading at its speed, and snaps to where the next answer puts it. Everything from the pilots
   goes on the page as text (textContent), never as HTML.

   Every time the page opens, a dialog says who's on the map before the map can be used. It isn't remembered. */
(function () {
  "use strict";

  const POLL_MS = 15_000;
  const COAST_MAX_S = 30; // an aircraft moves on by itself at most this long past its last position
  const COLOURS = { air: AtcMap.COLOURS.air, ground: AtcMap.COLOURS.taxi, picked: AtcMap.COLOURS.own };
  const PHASE = { parked: "Parked", taxi: "Taxiing", takeoff: "Taking off", climb: "Climbing", cruise: "Cruising",
    descent: "Descending", approach: "On approach", landing: "Landing", landed: "Landed" };
  const el = (id) => document.getElementById(id);

  const state = { flights: new Map(), markers: new Map(), picked: null, routeLine: null, at: 0, timer: null, admin: false };

  // --- who's on the map: asked every time, before the map is used ------------------------------------------------

  const intro = el("lf-intro");
  if (intro && typeof intro.showModal === "function") {
    // Escape doesn't skip it, "Got it" (or "How sharing works") does. A browser may close a dialog on Escape anyway
    // before the page has been clicked: then it opens again.
    intro.addEventListener("cancel", (e) => e.preventDefault());
    el("lf-intro-how").addEventListener("click", () => intro.close("how"));
    intro.addEventListener("close", () => {
      if (intro.returnValue !== "ok" && intro.returnValue !== "how") { intro.returnValue = ""; intro.showModal(); el("lf-intro-ok").focus(); return; }
      if (intro.returnValue === "ok") el("lf-map").focus({ preventScroll: true });
    });
    intro.showModal();
    el("lf-intro-ok").focus();
  }

  // --- the map ---------------------------------------------------------------------------------------------------

  const map = L.map("lf-map", { zoomControl: false, worldCopyJump: true, minZoom: 2, maxZoom: 13, keyboard: true })
    .setView([35, -30], 3);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 13,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(map);
  el("lf-map").tabIndex = 0;
  el("lf-zin").onclick = () => map.zoomIn();
  el("lf-zout").onclick = () => map.zoomOut();
  el("lf-fit").onclick = fitAll;
  map.on("click", () => pick(null));

  function fitAll() {
    const pts = [...state.flights.values()].map((f) => [f.lat, f.lon]);
    if (pts.length === 1) map.setView(pts[0], 7);
    else if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 8 });
  }

  function icon(f) {
    const colour = f.id === state.picked ? COLOURS.picked : f.ground ? COLOURS.ground : COLOURS.air;
    const kind = AtcMap.kindOf(f.type);
    const size = kind === "heavy" ? 30 : kind === "light" || kind === "heli" ? 22 : 26;
    return L.divIcon({ className: "lf-plane", iconSize: [size, size], iconAnchor: [size / 2, size / 2],
      html: `<div style="transform:rotate(${Number(f.hdg) || 0}deg)">${AtcMap.PLANE(colour, size, kind)}</div>` });
  }

  const label = (f) => `${f.callsign}${f.type ? `, ${f.type}` : ""}${f.from && f.to ? `, ${f.from} to ${f.to}` : ""}`;

  function draw() {
    for (const [id, m] of state.markers) {
      if (!state.flights.has(id)) { m.remove(); state.markers.delete(id); }
    }
    for (const f of state.flights.values()) {
      let m = state.markers.get(f.id);
      if (!m) {
        m = L.marker([f.lat, f.lon], { icon: icon(f), title: label(f), alt: label(f), keyboard: true, riseOnHover: true })
          .on("click", (e) => { L.DomEvent.stopPropagation(e); pick(f.id); }).addTo(map);
        m.on("keypress", (e) => { if (e.originalEvent.key === "Enter" || e.originalEvent.key === " ") pick(f.id); });
        state.markers.set(f.id, m);
      } else {
        m.setLatLng([f.lat, f.lon]);
        m.setIcon(icon(f));
      }
    }
    counts();
    el("lf-empty").hidden = state.flights.size > 0;
    if (state.picked && !state.flights.has(state.picked)) pick(null);
    else if (state.picked) card(state.flights.get(state.picked));
  }

  function counts() {
    const all = [...state.flights.values()];
    const n = (id, v) => { el(id).textContent = String(v); };
    n("n-live", all.length);
    n("n-ifr", all.filter((f) => f.rules !== "VFR").length);
    n("n-vfr", all.filter((f) => f.rules === "VFR").length);
    n("n-air", all.filter((f) => !f.ground).length);
    n("n-ground", all.filter((f) => f.ground).length);
    n("n-heli", all.filter((f) => AtcMap.kindOf(f.type) === "heli").length);
  }

  // --- moving on between two answers ------------------------------------------------------------------------------

  function coast() {
    const now = Date.now();
    for (const f of state.flights.values()) {
      const m = state.markers.get(f.id);
      if (!m || f.ground || !f.gs) continue;
      const dt = Math.min((now - f.seen) / 1000, COAST_MAX_S);
      const nm = (f.gs * dt) / 3600, h = (f.hdg * Math.PI) / 180;
      const lat = f.lat + (nm / 60) * Math.cos(h);
      const lon = f.lon + (nm / 60) * Math.sin(h) / Math.max(0.2, Math.cos((f.lat * Math.PI) / 180));
      m.setLatLng([lat, lon]);
    }
  }
  setInterval(coast, 1000);

  // --- the card -------------------------------------------------------------------------------------------------

  function chip(text, cls = "") {
    const s = document.createElement("span");
    s.className = `lf-chip ${cls}`;
    s.textContent = text;
    return s;
  }

  function card(f) {
    const box = el("lf-card");
    if (!f) { box.hidden = true; return; }
    el("lf-c-callsign").textContent = f.callsign;
    const chips = el("lf-c-chips");
    chips.replaceChildren(...[f.type && chip(f.type), chip(f.rules || "IFR", f.rules === "VFR" ? "vfr" : "ifr"),
      f.phase && chip(PHASE[f.phase] || f.phase, "phase"),
      // An admin's tags for the pilot ("Streamer", "Contributor"), in the colour given (a class from a fixed list).
      ...(f.tags || []).map((t) => chip(t.label, `tag tag-${/^[a-z]+$/.test(t.color) ? t.color : "gray"}`))].filter(Boolean));
    airlineLogo(f.airline);
    const radio = f.station ? `${f.station}${f.mhz ? ` ${Number(f.mhz).toFixed(3).replace(/0$/, "")}` : ""}` : "";
    el("lf-c-pilot").textContent = [f.name, radio].filter(Boolean).join(" · ");
    el("lf-c-od").textContent = f.from || f.to ? `${f.from || "?"}  ✈  ${f.to || "?"}` : "";
    el("lf-c-gs").textContent = f.gs != null ? `${f.gs} kt` : "–";
    el("lf-c-alt").textContent = f.alt != null ? `${Math.round(f.alt).toLocaleString("en-US")} ft` : "–";
    el("lf-c-hdg").textContent = f.hdg != null ? `${String(f.hdg).padStart(3, "0")}°` : "–";
    const route = el("lf-c-route");
    route.textContent = f.route ? `Route: ${f.route}` : "Route not shared.";
    route.classList.toggle("muted", !f.route);
    const delay = el("lf-c-delay");
    delay.hidden = !f.delay_min;
    delay.textContent = f.delay_min ? `Shown ${f.delay_min} minutes behind, as the pilot chose.` : "";
    el("lf-admin").hidden = !state.admin;
    box.hidden = false;
  }

  function pick(id) {
    const before = state.picked;
    state.picked = id;
    if (state.routeLine) { state.routeLine.remove(); state.routeLine = null; }
    for (const fid of [before, id]) {
      const f = fid && state.flights.get(fid), m = fid && state.markers.get(fid);
      if (f && m) m.setIcon(icon(f));
    }
    const f = id && state.flights.get(id);
    card(f || null);
    if (!f) return;
    drawRoute(f);
    el("lf-card").focus({ preventScroll: true });
  }

  // --- the route: as the app's Live Map draws it --------------------------------------------------------------------

  const ROUTE = { color: "#e978d6", weight: 2.5, opacity: 0.9, interactive: false };

  /** The filed route through its fixes, each leg along the curve of the earth, every fix a dot with its name. Without
   * the fixes (an older app, or none filed), a dashed line from departure to arrival along the great circle. */
  async function drawRoute(f) {
    const layer = L.layerGroup().addTo(map);
    state.routeLine = layer;
    let fixes = null;
    if (f.has_route) {
      try {
        const res = await fetch(`${API}/v1/sky/flights/${encodeURIComponent(f.id)}/route`, { credentials: "omit" });
        if (res.ok) fixes = (await res.json()).fixes;
      } catch { /* the dashed line instead */ }
    }
    if (state.routeLine !== layer) return; // another flight picked meanwhile
    if (Array.isArray(fixes) && fixes.length >= 2) {
      const pts = [];
      for (let i = 1; i < fixes.length; i++) {
        const a = fixes[i - 1], b = fixes[i];
        const leg = greatCircle([a.lat, a.lon], [b.lat, b.lon], Math.max(2, Math.min(64, Math.round(dist(a, b) / 40))));
        // longitudes run on from the leg before, so a route across the date line stays in one piece
        const shift = pts.length ? 360 * Math.round((pts[pts.length - 1][1] - leg[0][1]) / 360) : 0;
        pts.push(...leg.slice(pts.length ? 1 : 0).map(([la, lo]) => [la, lo + shift]));
      }
      L.polyline(pts, ROUTE).addTo(layer);
      let k = 0;
      fixes.forEach((x, i) => {
        if (x.kind === "apt") return;
        const at = nearest(pts, x, k);
        k = at.i;
        L.circleMarker(at.p, { radius: 3, color: "#e9d38a", weight: 1, fillOpacity: 0.85, interactive: false }).addTo(layer);
        if (fixes.length <= 60 || i % 2 === 0) {
          L.marker(at.p, { icon: L.divIcon({ className: "lf-fix", html: "", iconSize: [0, 0] }), interactive: false, keyboard: false })
            .addTo(layer).getElement().textContent = x.ident; // a fix's name, as text
        }
      });
    } else if (f.from_ll && f.to_ll) {
      L.polyline(greatCircle(f.from_ll, f.to_ll), { ...ROUTE, dashArray: "6 6" }).addTo(layer);
    }
  }

  const dist = (a, b) => {  // nautical miles
    const r = Math.PI / 180, dLat = (b.lat - a.lat) * r, dLon = (b.lon - a.lon) * r;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) ** 2;
    return 3440 * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
  };

  /** The point of the drawn line at a fix (its longitude as the line has it, past the date line or not). */
  function nearest(pts, x, from) {
    let best = from, bd = Infinity;
    for (let i = from; i < pts.length; i++) {
      const d = Math.abs(pts[i][0] - x.lat) + Math.abs(((pts[i][1] - x.lon + 540) % 360) - 180);
      if (d < bd) { bd = d; best = i; }
      if (d < 1e-6) break;
    }
    return { i: best, p: pts[best] };
  }

  // --- the airline's logo -------------------------------------------------------------------------------------------

  const logos = new Map(); // IATA code -> a data URL with the white round it cleared, or "" (none)

  function airlineLogo(iata) {
    const box = el("lf-c-logo");
    const show = (src) => { if (el("lf-c-logo").dataset.code !== iata) return; box.hidden = !src; if (src) box.src = src; };
    box.dataset.code = iata || "";
    box.hidden = true;
    if (!iata || !/^[A-Z0-9]{2}$/.test(iata)) return;
    if (logos.has(iata)) { show(logos.get(iata)); return; }
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => { const src = clearWhite(img); logos.set(iata, src); show(src); };
    img.onerror = () => { logos.set(iata, ""); show(""); };
    img.src = `${API}/v1/logo/${iata}`;
    box.alt = `${iata} logo`;
  }

  /** A logo's white background made see-through (the white joined to its edges), as the app does. */
  function clearWhite(img) {
    try {
      const c = document.createElement("canvas"), w = (c.width = img.naturalWidth), h = (c.height = img.naturalHeight);
      const g = c.getContext("2d");
      g.drawImage(img, 0, 0);
      const d = g.getImageData(0, 0, w, h), px = d.data, seen = new Uint8Array(w * h), stack = [];
      const white = (i) => px[i * 4 + 3] > 0 && px[i * 4] > 235 && px[i * 4 + 1] > 235 && px[i * 4 + 2] > 235;
      for (let x = 0; x < w; x++) stack.push(x, (h - 1) * w + x);
      for (let y = 0; y < h; y++) stack.push(y * w, y * w + w - 1);
      while (stack.length) {
        const i = stack.pop();
        if (seen[i]) continue;
        seen[i] = 1;
        if (px[i * 4 + 3] > 0) { if (!white(i)) continue; px[i * 4 + 3] = 0; }
        const x = i % w, y = (i - x) / w;
        if (x > 0) stack.push(i - 1);
        if (x < w - 1) stack.push(i + 1);
        if (y > 0) stack.push(i - w);
        if (y < h - 1) stack.push(i + w);
      }
      g.putImageData(d, 0, 0);
      return c.toDataURL("image/png");
    } catch {
      return img.src;
    }
  }

  el("lf-close").onclick = () => { const id = state.picked; pick(null); state.markers.get(id)?.getElement()?.focus(); };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.picked && !intro.open) pick(null); });

  /** Points along the shortest way round the globe, longitudes kept continuous so the line crosses the date line. */
  function greatCircle([lat1, lon1], [lat2, lon2], n = 64) {
    const r = Math.PI / 180, p1 = lat1 * r, l1 = lon1 * r, p2 = lat2 * r, l2 = lon2 * r;
    const d = 2 * Math.asin(Math.sqrt(Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin((l2 - l1) / 2) ** 2));
    if (!d) return [[lat1, lon1], [lat2, lon2]];
    const out = [];
    let prev = null;
    for (let i = 0; i <= n; i++) {
      const f = i / n, a = Math.sin((1 - f) * d) / Math.sin(d), b = Math.sin(f * d) / Math.sin(d);
      const x = a * Math.cos(p1) * Math.cos(l1) + b * Math.cos(p2) * Math.cos(l2);
      const y = a * Math.cos(p1) * Math.sin(l1) + b * Math.cos(p2) * Math.sin(l2);
      const z = a * Math.sin(p1) + b * Math.sin(p2);
      let lon = Math.atan2(y, x) / r;
      if (prev !== null) while (lon - prev > 180) lon -= 360;
      if (prev !== null) while (prev - lon > 180) lon += 360;
      prev = lon;
      out.push([Math.atan2(z, Math.hypot(x, y)) / r, lon]);
    }
    return out;
  }

  // --- the data -------------------------------------------------------------------------------------------------

  async function load() {
    try {
      const res = await fetch(`${API}/v1/sky/flights`, { credentials: "omit" });
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      const seen = Date.now();
      state.flights = new Map((data.flights || []).filter((f) => Number.isFinite(f.lat) && Number.isFinite(f.lon))
        .map((f) => [f.id, { ...f, seen }]));
      state.at = Date.parse(data.generated_at) || seen;
      const first = !state.loaded;
      state.loaded = true;
      draw();
      if (first && state.flights.size) fitAll();
      el("lf-updated").textContent = data.paused ? "Live Flights is paused right now." : `Updated ${new Date(state.at).toLocaleTimeString()}`;
    } catch {
      el("lf-updated").textContent = "Can't reach Live Flights right now. Trying again ...";
    }
  }

  function schedule() {
    clearInterval(state.timer);
    state.timer = document.hidden ? null : setInterval(load, POLL_MS);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); schedule(); });
  load();
  schedule();

  // --- for Live Flights' admins (?admin): take a flight down, or turn an account's sharing off ---------------------

  if (new URLSearchParams(location.search).has("admin")) {
    api("GET", "/v1/me").then((me) => { state.admin = !!me.sky_admin; }).catch(() => {});
    el("lf-takedown").onclick = () => {
      const id = state.picked;
      if (id && confirm("Take this flight off the map?")) api("DELETE", `/v1/sky/flights/${id}`).then(load).catch((e) => alert(e.message));
    };
    el("lf-ban").onclick = () => {
      const id = state.picked;
      if (id && confirm("Turn this account's sharing off? Its flight leaves the map and it can't share again until you turn it back on."))
        api("POST", "/v1/sky/ban", { flight_id: id }).then(load).catch((e) => alert(e.message));
    };
  }
})();
