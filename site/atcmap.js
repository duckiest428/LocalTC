/* The ATC layer of a live map, shared by the app's Live Map and the website's Flight Tracker (the same file in
   both, like flightsmap.js): the flight plan's route and its fixes, the flight's runways, and the zones the
   engine hands over at (ui/zones.py): the centres, the departure and approach areas, the stretch of final,
   the taxi route ground gave, the gate, and each airport's controllers. On the VFR map, each airport's
   airspace class instead, labelled like a sectional. Leaflet draws it; the styles are in atcmap.css. */

const AtcMap = (() => {
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const mhz = (v) => Number(v).toFixed(3);
  const ROUTE = { color: "#e978d6", weight: 2, opacity: 0.85 };

  /* The date line. Leaflet draws a line from 179°E to 179°W the long way, across the whole world: a flight from New
     York to Tokyo drew its route, the centres on it and their names across every continent. So nothing here is drawn
     at -180..180 as given: lines run on in one piece (Tokyo becomes 220°W after Anchorage), and everything else is
     drawn in the copy of the world nearest the aircraft (``setRef``: its longitude, as the path flown has it). */
  let ref = null;
  const wrapNear = (lon, to) => lon + 360 * Math.round((to - lon) / 360);
  const near = (lon) => (ref == null ? lon : wrapNear(lon, ref));
  /** Points joined up: each next one within 180° of the one before, the first near the aircraft. */
  function chain(points) {
    const out = [];
    for (const p of points) out.push([p[0], out.length ? wrapNear(p[1], out[out.length - 1][1]) : near(p[1])]);
    return out;
  }
  /** An outline in one piece, then moved as a whole to the copy of the world nearest the aircraft. */
  function ring(points) {
    const joined = [];
    for (const p of points) joined.push([p[0], joined.length ? wrapNear(p[1], joined[joined.length - 1][1]) : p[1]]);
    if (!joined.length) return joined;
    const mid = joined.reduce((a, p) => a + p[1], 0) / joined.length, shift = near(mid) - mid;
    return joined.map((p) => [p[0], p[1] + shift]);
  }
  const at = (lat, lon) => [lat, near(lon)];

  /** The planned route: a line through its fixes, each one dotted and named. Returns the line (or null). */
  function route(layer, plan) {
    const fixes = (plan && plan.fixes) || [];
    const pts = chain(fixes.map((f) => [f.lat, f.lon]));
    fixes.forEach((f, i) => {
      if (f.kind === "apt") return;
      L.circleMarker(pts[i], { radius: 3, color: "#e9d38a", weight: 1, fillOpacity: 0.8 }).addTo(layer);
      L.marker(pts[i], { icon: L.divIcon({ className: "", html: `<div class="fix-label" style="margin:6px 0 0 6px">${esc(f.ident)}</div>`, iconSize: [0, 0] }),
        interactive: false, keyboard: false }).addTo(layer);
    });
    return fixes.length > 1 ? L.polyline(pts, ROUTE).addTo(layer) : null;
  }

  /** A plan typed without fixes: a straight dashed line from one airport to the other. */
  function straight(layer, from, to) {
    return L.polyline(chain([[from.lat, from.lon], [to.lat, to.lon]]), { ...ROUTE, dashArray: "6 6" }).addTo(layer);
  }

  /** An airport's runways as lines, and its code. ``a.runways``: {name, lat, lon, heading_true, length_m}. */
  function runways(layer, a) {
    const shift = near(a.lon) - a.lon;
    for (const r of a.runways || []) {
      if (r.lat == null || r.lon == null || !r.length_m) continue;
      const half = r.length_m / 2, h = (r.heading_true * Math.PI) / 180;
      const dLat = (half * Math.cos(h)) / 111320, dLon = (half * Math.sin(h)) / (111320 * Math.cos((r.lat * Math.PI) / 180));
      L.polyline([[r.lat - dLat, r.lon - dLon + shift], [r.lat + dLat, r.lon + dLon + shift]], { color: "#d9dde2", weight: 4, opacity: 0.9 })
        .addTo(layer).bindTooltip(`${esc(a.icao)} ${esc(r.name)}`);
    }
    L.marker(at(a.lat, a.lon), { icon: L.divIcon({ className: "", html: `<div class="tfc-label" style="color:#e9d38a;font-weight:700">${esc(a.icao)}</div>`, iconSize: [0, 0] }),
      interactive: false, keyboard: false }).addTo(layer);
  }

  /** The ATC zones (ui/zones.py's answer) into ``layer``, IFR or VFR. */
  function zones(layer, z, { vfr = false } = {}) {
    const label = (p, text, cls) => L.marker(at(p[0], p[1]), { icon: L.divIcon({ className: "", html: `<div class="zone-label ${cls}">${esc(text)}</div>`, iconSize: [0, 0] }), interactive: false, keyboard: false }).addTo(layer);
    if (vfr) classes(layer, z);
    for (const c of vfr ? [] : z.centers || []) {
      const on = c.active || c.working;
      L.polygon((c.rings || []).map(ring), { color: "#d9dde2", weight: on ? 2.2 : 1, opacity: c.route ? 0.85 : 0.35, dashArray: c.route ? null : "4 6",
        fill: on, fillColor: "#d9dde2", fillOpacity: 0.04, interactive: false }).addTo(layer);
      if (c.label) label(c.label, c.name, `center ${c.active ? "here" : c.route ? "" : "dim"}`);
    }
    for (const a of vfr ? [] : z.terminals || []) {
      L.polygon((a.rings || []).map(ring), { color: "#2fb67c", weight: a.working ? 2.2 : 1.4, opacity: 0.9, fillColor: "#2fb67c",
        fillOpacity: a.working ? 0.22 : 0.12 }).addTo(layer)
        .bindTooltip(`${esc(a.name)} — ${a.role === "departure" ? "departure works you until you leave this area" : "approach takes you in here"}`);
      if (a.label) label(a.label, a.name, "terminal");
    }
    if (z.final && !vfr) {
      L.polygon(ring(z.final.ring), { color: "#e7b24a", weight: 1.5, dashArray: "5 5", fillColor: "#e7b24a", fillOpacity: 0.1 }).addTo(layer)
        .bindTooltip(`Joining final for ${esc(z.final.approach || "runway " + z.final.runway)}: approach clears the approach here and sends you to tower`);
    }
    if (z.taxi && z.taxi.points && z.taxi.points.length > 1) {  // the route ground gave: to the runway, or in to the gate
      const via = z.taxi.taxiways && z.taxi.taxiways.length ? ` via ${z.taxi.taxiways.join(", ")}` : "";
      const pts = chain(z.taxi.points);
      L.polyline(pts, { color: "#111", weight: 7, opacity: 0.5, interactive: false }).addTo(layer);
      L.polyline(pts, { color: "#f2c94c", weight: 3.5, opacity: 0.95, dashArray: "8 6" }).addTo(layer)
        .bindTooltip(`Taxi to ${esc(z.taxi.to)}${esc(via)}`, { sticky: true });
      L.circleMarker(pts[pts.length - 1], { radius: 4, color: "#f2c94c", weight: 2, fillColor: "#111", fillOpacity: 1, interactive: false }).addTo(layer);
    }
    if (z.gate && z.gate.lat != null) {
      L.circleMarker(at(z.gate.lat, z.gate.lon), { radius: 6, color: "#fff", weight: 2, fillColor: "#8a6cf0", fillOpacity: 1 }).addTo(layer)
        .bindTooltip(`${esc(z.gate.name)} at ${esc(z.gate.icao)}: where ground sent you`, { permanent: true, direction: "right", className: "atc-gate" });
    }
    const KIND = { clearance: ["D", "b-clearance"], ground: ["G", "b-ground"], tower: ["T", "b-tower"], departure: ["A", "b-terminal"], approach: ["A", "b-terminal"] };
    for (const ap of z.airports || []) {
      if (ap.tower_nm && !vfr) L.circle(at(ap.lat, ap.lon), { radius: ap.tower_nm * 1852, color: "#e2574c", weight: 1.3, dashArray: "4 5", fillColor: "#e2574c", fillOpacity: 0.05, interactive: false }).addTo(layer);
      const stations = ap.stations || [];
      const seen = new Set();
      const badges = stations.filter((s) => KIND[s.controller] && !seen.has(KIND[s.controller][0]) && seen.add(KIND[s.controller][0]))
        .map((s) => {
          const all = stations.filter((o) => KIND[o.controller]?.[0] === KIND[s.controller][0]);
          const state = all.some((o) => o.tuned) ? "tuned" : all.some((o) => o.next) ? "next" : "";
          return `<span class="atc-badge ${KIND[s.controller][1]} ${state}">${KIND[s.controller][0]}</span>`;
        }).join("");
      const tip = stations.map((s) => `${esc(s.station)} ${mhz(s.mhz)}${s.tuned ? " ◀ tuned" : s.next ? " ◀ next" : ""}`).join("<br>");
      L.marker(at(ap.lat, ap.lon), { icon: L.divIcon({ className: "", html: `<div class="atc-badges">${badges}</div>`, iconSize: [0, 0] }) })
        .addTo(layer).bindTooltip(`<b>${esc(ap.icao)}</b> ${esc(ap.name || "")}<br>${tip}`);
    }
  }

  /* The VFR layer: each airport's airspace class, with ceiling over floor on every ring like a sectional. */
  function classes(layer, z) {
    const STYLE = { B: ["b", "#2b6fd8", null, 2.4], C: ["c", "#b23a9e", null, 2.2], D: ["d", "#2b6fd8", "7 5", 1.8],
      CTR: ["d", "#2b6fd8", "7 5", 1.8], ATZ: ["atz", "#b23a9e", "4 4", 1.6] };
    const hundreds = (ft) => (ft ? String(Math.round(ft / 100)) : "SFC");
    const along = (a, nm) => [a.lat - (nm / 60) * Math.SQRT1_2, near(a.lon) + ((nm / 60) * Math.SQRT1_2) / Math.cos((a.lat * Math.PI) / 180)];
    for (const a of z.classes || []) {
      const st = STYLE[a.class];
      (a.rings || []).forEach((r, i) => {
        if (!st) return;
        L.circle(at(a.lat, a.lon), { radius: r.nm * 1852, color: st[1], weight: st[3], dashArray: st[2], fill: i === 0,
          fillColor: st[1], fillOpacity: 0.07, interactive: false }).addTo(layer);
        const inner = i ? a.rings[i - 1].nm : 0;  // the label sits in its band, southeast of the field
        L.marker(along(a, i ? (inner + r.nm) / 2 : r.nm * 0.62), { icon: L.divIcon({ className: "",
          html: `<div class="vfr-alt ${st[0]}"><span>${hundreds(r.ceiling)}</span><i>${hundreds(r.floor)}</i></div>`, iconSize: [0, 0] }),
          interactive: false, keyboard: false }).addTo(layer);
      });
      const color = a.towered ? "#1d5bbf" : "#8e2c7c";
      L.circleMarker(at(a.lat, a.lon), { radius: 5, color, weight: 2, fillColor: "#fff", fillOpacity: 1 }).addTo(layer)
        .bindTooltip(`<b>${esc(a.icao)}</b> ${esc(a.name || "")}<br>${a.label ? `${esc(a.label)}${a.class === "B" || a.class === "C" ? ` (Class ${a.class})` : ""}` : "No tower: uncontrolled"}`);
      L.marker(at(a.lat, a.lon), { icon: L.divIcon({ className: "", html: `<div class="vfr-apt ${a.towered ? "towered" : "other"}">${esc(a.icao)}</div>`, iconSize: [0, 0] }),
        interactive: false, keyboard: false }).addTo(layer);
    }
  }

  /** Who the flight talks to now and next, for the legend. */
  function talk(z) {
    const out = [];
    if (z.tuned) out.push(`<span class="now">▶ ${esc(z.tuned.station)} ${mhz(z.tuned.mhz)}</span>`);
    if (z.next) out.push(`<span class="then">next: ${esc(z.next.station)} ${mhz(z.next.mhz)}</span>`);
    if (!z.tuned && z.center) out.push(`in ${esc(z.center)} airspace`);
    return out.join("<br>");
  }

  /** The legend's contents: what each colour means, IFR and VFR (the map's data-mode shows one). */
  const LEGEND = `
    <div class="lg-talk"></div>
    <div class="lg-ifr">
      <div class="lg-row"><span class="sw sw-center"></span>Centre: enroute, handed over at its boundary</div>
      <div class="lg-row"><span class="sw sw-terminal"></span>Departure / Approach area</div>
      <div class="lg-row"><span class="sw sw-final"></span>Joining final: cleared, then to Tower</div>
      <div class="lg-row"><span class="sw sw-taxi"></span>Taxi route ground gave</div>
      <div class="lg-row"><span class="sw sw-tower"></span>Tower's control zone</div>
      <div class="lg-row"><span class="sw sw-route"></span>Flight plan route</div>
      <div class="lg-row"><span class="sw sw-hold"></span>Hold ATC gave, and route amendments</div>
    </div>
    <div class="lg-row lg-wx"><span class="sw sw-sigmet"></span>SIGMET / AIRMET (red: thunderstorms)</div>
    <div class="lg-row lg-wx"><span class="sw sw-tfr"></span>TFR, restricted or danger area</div>
    <div class="lg-row lg-wx"><span class="sw sw-notam"></span>NOTAMs (a closed runway red)</div>
    <div class="lg-vfr">
      <div class="lg-row"><span class="sw sw-b"></span>Class B: needs "cleared into the Class Bravo"</div>
      <div class="lg-row"><span class="sw sw-c"></span>Class C: talk to approach before entering</div>
      <div class="lg-row"><span class="sw sw-d"></span>Class D / control zone: call the tower first</div>
      <div class="lg-row"><span class="sw sw-atz"></span>Traffic zone (ATZ)</div>
      <div class="lg-row lg-muted">Labels: ceiling / floor in hundreds of feet MSL (SFC = surface)</div>
    </div>
    <div class="lg-row lg-badges"><span><span class="atc-badge b-clearance">D</span>Clearance</span>
      <span><span class="atc-badge b-ground">G</span>Ground</span> <span><span class="atc-badge b-tower">T</span>Tower</span>
      <span><span class="atc-badge b-terminal">A</span>Dep/App</span></div>
    <div class="lg-src lg-ifr">Airspace: VATSpy &amp; SimAware TRACON projects, CC BY-SA 4.0</div>
    <div class="lg-src lg-vfr">Airspace classes are simplified (typical sizes), not for real navigation</div>`;

  /* The path flown, kept as the app keeps it (ui/companion.py): a point every ~200 m in the air and every ~10 m on
     the ground, so the taxi from the gate keeps its turns; one always on lifting off or touching down. Past `keep`
     points it's simplified (Douglas-Peucker): straight legs lose their points, the turns and the taxi keep theirs.
     Dropping every other point instead lost the taxi a few hours into a flight. */
  function track(keep = 3000) {
    return {
      pts: [], ground: false,
      add(lat, lon, ground) {
        ground = !!ground;
        const last = this.pts[this.pts.length - 1];
        if (last) lon = wrapNear(lon, last[1]);  // on across the date line, never back across the world
        if (last && ground === this.ground && Math.abs(last[0] - lat) + Math.abs(last[1] - lon) < (ground ? 0.0001 : 0.002)) return false;
        this.ground = ground;
        this.pts.push([lat, lon]);
        if (this.pts.length > keep) this.pts = simplify(this.pts, keep);
        return true;
      },
      set(points) {
        const joined = [];
        for (const p of points) joined.push([p[0], joined.length ? wrapNear(p[1], joined[joined.length - 1][1]) : p[1]]);
        this.pts = simplify(joined, keep);
      },
      /** Where the aircraft is drawn: its longitude in the same copy of the world as the end of the path. */
      lon(lon) { const last = this.pts[this.pts.length - 1]; return last ? wrapNear(lon, last[1]) : lon; },
    };
  }

  /* Traffic, as the app's Live Map shows it: each aircraft pointing its way, airborne cyan and on the ground grey,
     labelled with its callsign, its altitude in hundreds of feet (or GND) and its type. */
  const PLANE = (color, size) => `<svg viewBox="0 0 32 32" width="${size}" height="${size}"><path fill="${color}" stroke="#000" stroke-width="1" d="M16 2c1.2 0 2 1.4 2 3v7l11 6v3l-11-3v6l3 2v2.5l-5-1.5-5 1.5V26l3-2v-6L3 21v-3l11-6V5c0-1.6.8-3 2-3z"/></svg>`;
  function trafficIcon(t) {
    const color = t.ground ? "#8d959e" : "#62c7e6";
    const alt = t.ground ? "GND" : `${Math.round((t.alt || 0) / 100).toString().padStart(3, "0")}`;
    return L.divIcon({ className: "", html: `<div style="transform:rotate(${Number(t.hdg) || 0}deg);width:20px;height:20px">${PLANE(color, 20)}</div>
      <div class="tfc-label" style="margin-left:18px;margin-top:-18px">${esc(t.callsign || "")}<br>${alt} ${esc(t.type || "")}</div>`,
      iconSize: [20, 20], iconAnchor: [10, 10] });
  }
  /** The traffic ``list`` on ``map``, its markers kept in ``markers`` (a Map by id) from one update to the next. */
  function traffic(map, markers, list) {
    const seen = new Set();
    for (const t of list || []) {
      if (t.lat == null || t.lon == null) continue;
      seen.add(t.id);
      const pos = at(t.lat, t.lon), m = markers.get(t.id);
      if (m) { m.setLatLng(pos); m.setIcon(trafficIcon(t)); }
      else markers.set(t.id, L.marker(pos, { icon: trafficIcon(t), interactive: false }).addTo(map));
    }
    for (const [id, m] of markers) if (!seen.has(id)) { m.remove(); markers.delete(id); }
  }
  function simplify(path, keep) {
    let out = path;
    for (let tol = 0.00003; out.length > keep * 0.75 && out.length > 2; tol *= 2) out = douglasPeucker(out, tol);
    return out;
  }
  function douglasPeucker(path, tolerance) {
    const keep = path.map(() => false);
    keep[0] = keep[path.length - 1] = true;
    const stack = [[0, path.length - 1]];
    while (stack.length) {
      const [a, b] = stack.pop();
      if (b <= a + 1) continue;
      const k = Math.cos((path[a][0] * Math.PI) / 180);
      const ax = path[a][1] * k, ay = path[a][0], dx = path[b][1] * k - ax, dy = path[b][0] - ay, len2 = dx * dx + dy * dy;
      let worst = -1, at = a;
      for (let i = a + 1; i < b; i++) {
        const px = path[i][1] * k - ax, py = path[i][0] - ay;
        const u = len2 === 0 ? 0 : Math.max(0, Math.min(1, (px * dx + py * dy) / len2));
        const d = Math.hypot(px - u * dx, py - u * dy);
        if (d > worst) { worst = d; at = i; }
      }
      if (worst > tolerance) { keep[at] = true; stack.push([a, at], [at, b]); }
    }
    return path.filter((_, i) => keep[i]);
  }

  /* Real-world hazards around the flight (localtc.ops: GET ops/around): SIGMETs and AIRMETs by hazard, TFRs and
     restricted, prohibited and danger areas, each with its levels and its source in the tooltip. */
  const HAZARD = { TS: "#e2574c", CONVECTIVE: "#e2574c", TC: "#e2574c", TURB: "#e7a33e", ICE: "#5aa9e6", VA: "#b07ae0",
    MTW: "#e7a33e", IFR: "#9aa3ad", "MTN OBSCN": "#9aa3ad" };
  const AREA = { tfr: ["#ff4d6d", "TFR"], restricted: ["#d65db1", "Restricted"], prohibited: ["#ff2e2e", "Prohibited"],
    danger: ["#ff9f1c", "Danger area"] };
  const utc = (t) => new Date(t * 1000).toISOString().replace("T", " ").slice(5, 16) + "Z";
  /** When an area is in force: "active until ...", or "from ... (not yet)" when it starts later. */
  const when = (start, end) => {
    const now = Date.now() / 1000;
    if (start && start > now) return `<br><b>Not yet active</b>: from ${utc(start)}${end ? ` to ${utc(end)}` : ""}`;
    return end ? `<br>Active until ${utc(end)}` : "";
  };
  const levels = (lo, hi) => `${lo ? `${Math.round(lo / 100) * 100} ft` : "SFC"} – ${hi ? (hi >= 18000 ? `FL${Math.round(hi / 100)}` : `${hi} ft`) : "unl"}`;
  function hazards(layer, h) {
    for (const s of (h && h.sigmets) || []) {
      const c = HAZARD[String(s.hazard || "").toUpperCase()] || "#e9d38a";
      L.polygon(ring(s.polygon || []), { color: c, weight: 1.6, dashArray: s.kind === "airmet" ? "3 5" : null, fillColor: c,
        fillOpacity: s.kind === "airmet" ? 0.06 : 0.14 }).addTo(layer)
        .bindTooltip(`<b>${esc(String(s.kind || "").toUpperCase())} ${esc(s.hazard)}</b> ${esc(s.fir || "")}<br>${levels(s.base_ft, s.top_ft)}`
          + `<br><span class="muted">${s.valid_to ? `until ${new Date(s.valid_to * 1000).toISOString().slice(11, 16)}Z · ` : ""}aviationweather.gov</span>`, { sticky: true });
    }
    for (const a of (h && h.areas) || []) {
      const [c, what] = AREA[a.kind] || ["#d65db1", a.kind];
      L.polygon(ring(a.polygon || []), { color: c, weight: 1.8, dashArray: a.kind === "tfr" ? "6 4" : null, fillColor: c, fillOpacity: 0.1 })
        .addTo(layer).bindTooltip(`<b>${esc(what)}</b> ${esc(a.name || "")}<br>${levels(a.floor_ft, a.ceiling_ft)}${when(a.start, a.end)}<br><span class="muted">${esc(a.source || "")}</span>`, { sticky: true });
    }
  }

  /** The EFB's hazards list (the app's, relayed: {kind, hazard, name, floor_ft, ceiling_ft, polygon}) as ``hazards``
      takes it. */
  function hazardsFromEfb(list) {
    const sigmets = [], areas = [];
    for (const h of list || []) {
      if (h.kind === "sigmet" || h.kind === "airmet") sigmets.push({ ...h, base_ft: h.floor_ft, top_ft: h.ceiling_ft, fir: h.name });
      else areas.push({ ...h, source: h.kind === "tfr" ? "FAA TFR" : "openAIP" });
    }
    return { sigmets, areas };
  }

  /* What ATC is working the flight to (the engine's AtcOperations): the hold's racetrack with its fix, place and EFC,
     and each route amendment (a shortcut, vectors around an area, a deviation) as a line. */
  function operations(layer, ops) {
    if (!ops) return;
    const h = ops.hold;
    if (h && h.racetrack && ["cleared", "entering", "holding", "leaving"].includes(h.state)) {
      L.polyline(chain(h.racetrack), { color: "#4fd1c5", weight: 2.5, opacity: 0.95 }).addTo(layer)
        .bindTooltip(`<b>Hold at ${esc(h.fix)}</b>: inbound ${String(h.inbound).padStart(3, "0")}°, ${esc(h.turn)} turns, ${Number(h.altitude_ft).toLocaleString()} ft`
          + `<br>${h.reason === "arrival traffic" ? `number ${h.position}, ` : ""}${esc(h.reason)}${h.efc ? `, EFC ${esc(h.efc)}Z` : ""} · entry: ${esc(h.entry)}`, { sticky: true });
      L.marker(at(h.lat, h.lon), { icon: L.divIcon({ className: "", html: `<div class="zone-label hold">HOLD ${esc(h.fix)}${h.efc ? ` · EFC ${esc(h.efc)}` : ""}</div>`, iconSize: [0, 0] }),
        interactive: false, keyboard: false }).addTo(layer);
    }
    for (const a of ops.routing || []) {
      if (a.state !== "active" || !a.line || a.line.length < 2) continue;
      const style = { shortcut: ["#e978d6", "6 6"], reroute: ["#ff9f1c", "8 5"], deviation: ["#e2574c", "2 6"] }[a.kind] || ["#e9d38a", "4 4"];
      L.polyline(chain(a.line), { color: style[0], weight: 2.4, dashArray: style[1] }).addTo(layer)
        .bindTooltip(`${a.kind === "shortcut" ? "Direct" : a.kind === "reroute" ? "Vectors around" : "Deviation"} ${esc(a.fix || "")}: ${esc(a.reason || "")}`, { sticky: true });
    }
  }

  /* The flight's airports' NOTAMs (the EFB's briefings): a badge on each airport with how many are in force and how
     many ATC works to, the list in its tooltip (ATC's first), and each runway a NOTAM closes drawn red over it.
     ``airports``: the zones' airports ({icao, lat, lon, runways}). */
  function notams(layer, briefings, airports) {
    for (const a of airports || []) {
      const items = ((briefings || {})[a.icao]?.notams?.items || []).filter((n) => n.in_force !== false);
      if (!items.length || a.lat == null) continue;
      const atc = items.filter((n) => (n.works_to || []).length);
      const closed = new Set();
      for (const n of atc) for (const [kind, subject] of n.works_to || []) if (kind === "runway_closed") closed.add(String(subject).replace(/\s/g, ""));
      for (const r of a.runways || []) {
        const name = String(r.name || "").replace(/\s/g, "");
        if (r.lat == null || !r.length_m || ![...closed].some((c) => c === name || name.split("/").includes(c) || c.split("/").some((e) => name.split("/").includes(e)))) continue;
        const half = r.length_m / 2, h = (r.heading_true * Math.PI) / 180, k = Math.cos((r.lat * Math.PI) / 180);
        const dLat = (half * Math.cos(h)) / 111320, dLon = (half * Math.sin(h)) / (111320 * k), shift = near(r.lon) - r.lon;
        L.polyline([[r.lat - dLat, r.lon - dLon + shift], [r.lat + dLat, r.lon + dLon + shift]], { color: "#ff3b30", weight: 6, opacity: 0.9, dashArray: "10 6" })
          .addTo(layer).bindTooltip(`${esc(a.icao)} runway ${esc(r.name)} closed (NOTAM)`);
      }
      const list = [...atc, ...items.filter((n) => !(n.works_to || []).length)].slice(0, 12)
        .map((n) => `<div class="notam-tip${(n.works_to || []).length ? " atc" : ""}"><b>${esc(n.id || "")}</b> ${esc(String(n.text || "").slice(0, 160))}</div>`).join("");
      L.marker(at(a.lat, a.lon), { icon: L.divIcon({ className: "", iconSize: [0, 0],
        html: `<div class="notam-badge${atc.length ? " atc" : ""}">NOTAM ${items.length}${atc.length ? ` · ${atc.length} ATC` : ""}</div>` }) })
        .addTo(layer).bindTooltip(`<b>${esc(a.icao)} NOTAMs</b> (FAA)${list}${items.length > 12 ? `<div class="muted">and ${items.length - 12} more in the EFB</div>` : ""}`, { className: "notam-tooltip" });
    }
  }

  return { esc, route, straight, runways, zones, talk, track, traffic, hazards, hazardsFromEfb, operations, notams, PLANE, LEGEND,
    setRef(lon) { ref = lon; }, get ref() { return ref; } };
})();
