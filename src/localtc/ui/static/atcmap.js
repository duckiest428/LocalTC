/* The ATC layer of a live map, shared by the app's Live Map and the website's Flight Tracker (the same file in
   both, like flightsmap.js): the flight plan's route and its fixes, the flight's runways, and the zones the
   engine hands over at (ui/zones.py): the centres, the departure and approach areas, the stretch of final,
   the taxi route ground gave, the gate, and each airport's controllers. On the VFR map, each airport's
   airspace class instead, labelled like a sectional. Leaflet draws it; the styles are in atcmap.css. */

const AtcMap = (() => {
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const mhz = (v) => Number(v).toFixed(3);
  const ROUTE = { color: "#e978d6", weight: 2, opacity: 0.85 };

  /** The planned route: a line through its fixes, each one dotted and named. Returns the line (or null). */
  function route(layer, plan) {
    const fixes = (plan && plan.fixes) || [];
    for (const f of fixes) {
      if (f.kind === "apt") continue;
      L.circleMarker([f.lat, f.lon], { radius: 3, color: "#e9d38a", weight: 1, fillOpacity: 0.8 }).addTo(layer);
      L.marker([f.lat, f.lon], { icon: L.divIcon({ className: "", html: `<div class="fix-label" style="margin:6px 0 0 6px">${esc(f.ident)}</div>`, iconSize: [0, 0] }),
        interactive: false, keyboard: false }).addTo(layer);
    }
    return fixes.length > 1 ? L.polyline(fixes.map((f) => [f.lat, f.lon]), ROUTE).addTo(layer) : null;
  }

  /** A plan typed without fixes: a straight dashed line from one airport to the other. */
  function straight(layer, from, to) {
    return L.polyline([[from.lat, from.lon], [to.lat, to.lon]], { ...ROUTE, dashArray: "6 6" }).addTo(layer);
  }

  /** An airport's runways as lines, and its code. ``a.runways``: {name, lat, lon, heading_true, length_m}. */
  function runways(layer, a) {
    for (const r of a.runways || []) {
      if (r.lat == null || r.lon == null || !r.length_m) continue;
      const half = r.length_m / 2, h = (r.heading_true * Math.PI) / 180;
      const dLat = (half * Math.cos(h)) / 111320, dLon = (half * Math.sin(h)) / (111320 * Math.cos((r.lat * Math.PI) / 180));
      L.polyline([[r.lat - dLat, r.lon - dLon], [r.lat + dLat, r.lon + dLon]], { color: "#d9dde2", weight: 4, opacity: 0.9 })
        .addTo(layer).bindTooltip(`${esc(a.icao)} ${esc(r.name)}`);
    }
    L.marker([a.lat, a.lon], { icon: L.divIcon({ className: "", html: `<div class="tfc-label" style="color:#e9d38a;font-weight:700">${esc(a.icao)}</div>`, iconSize: [0, 0] }),
      interactive: false, keyboard: false }).addTo(layer);
  }

  /** The ATC zones (ui/zones.py's answer) into ``layer``, IFR or VFR. */
  function zones(layer, z, { vfr = false } = {}) {
    const label = (at, text, cls) => L.marker(at, { icon: L.divIcon({ className: "", html: `<div class="zone-label ${cls}">${esc(text)}</div>`, iconSize: [0, 0] }), interactive: false, keyboard: false }).addTo(layer);
    if (vfr) classes(layer, z);
    for (const c of vfr ? [] : z.centers || []) {
      const on = c.active || c.working;
      L.polygon(c.rings, { color: "#d9dde2", weight: on ? 2.2 : 1, opacity: c.route ? 0.85 : 0.35, dashArray: c.route ? null : "4 6",
        fill: on, fillColor: "#d9dde2", fillOpacity: 0.04, interactive: false }).addTo(layer);
      if (c.label) label(c.label, c.name, `center ${c.active ? "here" : c.route ? "" : "dim"}`);
    }
    for (const a of vfr ? [] : z.terminals || []) {
      L.polygon(a.rings, { color: "#2fb67c", weight: a.working ? 2.2 : 1.4, opacity: 0.9, fillColor: "#2fb67c",
        fillOpacity: a.working ? 0.22 : 0.12 }).addTo(layer)
        .bindTooltip(`${esc(a.name)} — ${a.role === "departure" ? "departure works you until you leave this area" : "approach takes you in here"}`);
      if (a.label) label(a.label, a.name, "terminal");
    }
    if (z.final && !vfr) {
      L.polygon(z.final.ring, { color: "#e7b24a", weight: 1.5, dashArray: "5 5", fillColor: "#e7b24a", fillOpacity: 0.1 }).addTo(layer)
        .bindTooltip(`Joining final for ${esc(z.final.runway)}: approach clears the approach here and sends you to tower`);
    }
    if (z.taxi && z.taxi.points && z.taxi.points.length > 1) {  // the route ground gave: to the runway, or in to the gate
      const via = z.taxi.taxiways && z.taxi.taxiways.length ? ` via ${z.taxi.taxiways.join(", ")}` : "";
      L.polyline(z.taxi.points, { color: "#111", weight: 7, opacity: 0.5, interactive: false }).addTo(layer);
      L.polyline(z.taxi.points, { color: "#f2c94c", weight: 3.5, opacity: 0.95, dashArray: "8 6" }).addTo(layer)
        .bindTooltip(`Taxi to ${esc(z.taxi.to)}${esc(via)}`, { sticky: true });
      L.circleMarker(z.taxi.points[z.taxi.points.length - 1], { radius: 4, color: "#f2c94c", weight: 2, fillColor: "#111", fillOpacity: 1, interactive: false }).addTo(layer);
    }
    if (z.gate && z.gate.lat != null) {
      L.circleMarker([z.gate.lat, z.gate.lon], { radius: 6, color: "#fff", weight: 2, fillColor: "#8a6cf0", fillOpacity: 1 }).addTo(layer)
        .bindTooltip(`${esc(z.gate.name)} at ${esc(z.gate.icao)}: where ground sent you`, { permanent: true, direction: "right", className: "atc-gate" });
    }
    const KIND = { clearance: ["D", "b-clearance"], ground: ["G", "b-ground"], tower: ["T", "b-tower"], departure: ["A", "b-terminal"], approach: ["A", "b-terminal"] };
    for (const ap of z.airports || []) {
      if (ap.tower_nm && !vfr) L.circle([ap.lat, ap.lon], { radius: ap.tower_nm * 1852, color: "#e2574c", weight: 1.3, dashArray: "4 5", fillColor: "#e2574c", fillOpacity: 0.05, interactive: false }).addTo(layer);
      const stations = ap.stations || [];
      const seen = new Set();
      const badges = stations.filter((s) => KIND[s.controller] && !seen.has(KIND[s.controller][0]) && seen.add(KIND[s.controller][0]))
        .map((s) => {
          const all = stations.filter((o) => KIND[o.controller]?.[0] === KIND[s.controller][0]);
          const state = all.some((o) => o.tuned) ? "tuned" : all.some((o) => o.next) ? "next" : "";
          return `<span class="atc-badge ${KIND[s.controller][1]} ${state}">${KIND[s.controller][0]}</span>`;
        }).join("");
      const tip = stations.map((s) => `${esc(s.station)} ${mhz(s.mhz)}${s.tuned ? " ◀ tuned" : s.next ? " ◀ next" : ""}`).join("<br>");
      L.marker([ap.lat, ap.lon], { icon: L.divIcon({ className: "", html: `<div class="atc-badges">${badges}</div>`, iconSize: [0, 0] }) })
        .addTo(layer).bindTooltip(`<b>${esc(ap.icao)}</b> ${esc(ap.name || "")}<br>${tip}`);
    }
  }

  /* The VFR layer: each airport's airspace class, with ceiling over floor on every ring like a sectional. */
  function classes(layer, z) {
    const STYLE = { B: ["b", "#2b6fd8", null, 2.4], C: ["c", "#b23a9e", null, 2.2], D: ["d", "#2b6fd8", "7 5", 1.8],
      CTR: ["d", "#2b6fd8", "7 5", 1.8], ATZ: ["atz", "#b23a9e", "4 4", 1.6] };
    const hundreds = (ft) => (ft ? String(Math.round(ft / 100)) : "SFC");
    const along = (a, nm) => [a.lat - (nm / 60) * Math.SQRT1_2, a.lon + ((nm / 60) * Math.SQRT1_2) / Math.cos((a.lat * Math.PI) / 180)];
    for (const a of z.classes || []) {
      const st = STYLE[a.class];
      (a.rings || []).forEach((r, i) => {
        if (!st) return;
        L.circle([a.lat, a.lon], { radius: r.nm * 1852, color: st[1], weight: st[3], dashArray: st[2], fill: i === 0,
          fillColor: st[1], fillOpacity: 0.07, interactive: false }).addTo(layer);
        const inner = i ? a.rings[i - 1].nm : 0;  // the label sits in its band, southeast of the field
        L.marker(along(a, i ? (inner + r.nm) / 2 : r.nm * 0.62), { icon: L.divIcon({ className: "",
          html: `<div class="vfr-alt ${st[0]}"><span>${hundreds(r.ceiling)}</span><i>${hundreds(r.floor)}</i></div>`, iconSize: [0, 0] }),
          interactive: false, keyboard: false }).addTo(layer);
      });
      const color = a.towered ? "#1d5bbf" : "#8e2c7c";
      L.circleMarker([a.lat, a.lon], { radius: 5, color, weight: 2, fillColor: "#fff", fillOpacity: 1 }).addTo(layer)
        .bindTooltip(`<b>${esc(a.icao)}</b> ${esc(a.name || "")}<br>${a.label ? `${esc(a.label)}${a.class === "B" || a.class === "C" ? ` (Class ${a.class})` : ""}` : "No tower: uncontrolled"}`);
      L.marker([a.lat, a.lon], { icon: L.divIcon({ className: "", html: `<div class="vfr-apt ${a.towered ? "towered" : "other"}">${esc(a.icao)}</div>`, iconSize: [0, 0] }),
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
    </div>
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
        if (last && ground === this.ground && Math.abs(last[0] - lat) + Math.abs(last[1] - lon) < (ground ? 0.0001 : 0.002)) return false;
        this.ground = ground;
        this.pts.push([lat, lon]);
        if (this.pts.length > keep) this.pts = simplify(this.pts, keep);
        return true;
      },
      set(points) { this.pts = simplify(points.map((p) => [p[0], p[1]]), keep); },
    };
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

  return { esc, route, straight, runways, zones, talk, track, LEGEND };
})();
