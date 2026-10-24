/* Every flight in the logbook on one map: the airports (bigger for more visits) and the routes between them
   (great circles, thicker for more flights). Click a route or an airport for its numbers; focus() picks one
   flight out. Needs Leaflet.

   One file, two copies: site/flightsmap.js (localtc.tech's Dashboard) and
   src/localtc/ui/static/flightsmap.js (the app's Logbook tab). tests/test_site.py keeps them identical. */

class FlightsMap {
  /** ``el``: the map's element. ``tiles``: draw an OpenStreetMap background (false: a plain dark map). */
  constructor(el, { tiles = true, onRoute = null } = {}) {
    this.onRoute = onRoute;
    this.map = L.map(el, { attributionControl: tiles, zoomSnap: 0.5 }).setView([30, -40], 2);
    if (tiles) {
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 12,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(this.map);
    }
    el.classList.add("flights-map", tiles ? "fm-tiles" : "fm-plain");
    this.layer = L.layerGroup().addTo(this.map);
    this.focusLayer = L.layerGroup().addTo(this.map);
    this.bounds = null;
    this.labels = [];
    this.map.on("zoomend moveend resize", () => this.declutter());
  }

  /** Where to cut the world: the longitude the fewest flights' routes cross (weighed by how many flew them), as far
   * from any airport as it can be. ``places``: the airports; ``arcs``: [points, flights] for each route. */
  static cut(places, arcs = []) {
    const lons = places.map((p) => p.lon);
    const spans = arcs.map(([pts, n]) => { const l = pts.map((p) => p[1]); return [Math.min(...l), Math.max(...l), n]; });
    let best = -180, score = Infinity;
    for (let c = -180; c < 180; c += 1) {
      let crossed = 0;
      for (const [lo, hi, n] of spans) {
        for (let k = -2; k <= 2; k++) if (c + 360 * k > lo && c + 360 * k < hi) { crossed += n; break; }
      }
      const near = lons.length ? Math.min(...lons.map((l) => Math.abs(((l - c + 540) % 360) - 180))) : 180;
      const s = crossed * 1000 - near;
      if (s < score) { score = s; best = c; }
    }
    return best;
  }

  /** The airport labels that fit, the busiest first: one that would sit on another is left out until zoomed in. */
  declutter() {
    const placed = [];
    for (const { marker, visits } of [...this.labels].sort((x, y) => y.visits - x.visits)) {
      const el = marker.getElement();
      if (!el) continue;
      const p = this.map.latLngToContainerPoint(marker.getLatLng());
      const box = [p.x + 6, p.y - 8, p.x + 6 + 8 * (el.textContent || "").length, p.y + 8];
      const clear = !placed.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]);
      el.style.visibility = clear ? "" : "hidden";
      if (clear) placed.push(box);
    }
  }

  /** Airports and routes from a list of logbook flights (each with origin/destination and their lat/lon). */
  static summarize(flights) {
    const airports = new Map(), routes = new Map();
    const visit = (icao, lat, lon, counts) => {
      if (!icao || lat == null || lon == null) return;
      const a = airports.get(icao) || { icao, lat, lon, visits: 0 };
      if (counts) a.visits += 1;
      airports.set(icao, a);
    };
    // Landing somewhere is a visit, and so is leaving from somewhere the flight before didn't land; flying out
    // again after landing is the same visit.
    let at = "";
    for (const f of [...flights].sort((x, y) => String(x.started_at || "").localeCompare(String(y.started_at || "")))) {
      visit(f.origin, f.origin_lat, f.origin_lon, f.origin !== at);
      visit(f.destination, f.destination_lat, f.destination_lon, true);
      at = f.destination || "";
      if (f.origin && f.destination && f.origin !== f.destination) {
        const key = `${f.origin}>${f.destination}`;
        const r = routes.get(key) || { origin: f.origin, destination: f.destination, flights: 0 };
        r.flights += 1;
        routes.set(key, r);
      }
    }
    return { airports: [...airports.values()], routes: [...routes.values()] };
  }

  /** Points along the great circle from a to b, with longitudes unwrapped so the line never jumps the map. */
  static arc(a, b, steps = 48) {
    const rad = Math.PI / 180, deg = 180 / Math.PI;
    const [la1, lo1, la2, lo2] = [a.lat * rad, a.lon * rad, b.lat * rad, b.lon * rad];
    const d = 2 * Math.asin(Math.sqrt(Math.sin((la2 - la1) / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin((lo2 - lo1) / 2) ** 2));
    if (d < 1e-6) return [[a.lat, a.lon], [b.lat, b.lon]];
    const pts = [];
    let prev = null;
    for (let i = 0; i <= steps; i++) {
      const f = i / steps, A = Math.sin((1 - f) * d) / Math.sin(d), B = Math.sin(f * d) / Math.sin(d);
      const x = A * Math.cos(la1) * Math.cos(lo1) + B * Math.cos(la2) * Math.cos(lo2);
      const y = A * Math.cos(la1) * Math.sin(lo1) + B * Math.cos(la2) * Math.sin(lo2);
      const z = A * Math.sin(la1) + B * Math.sin(la2);
      let lon = Math.atan2(y, x) * deg;
      if (prev !== null) while (lon - prev > 180) lon -= 360;
      if (prev !== null) while (lon - prev < -180) lon += 360;
      prev = lon;
      pts.push([Math.atan2(z, Math.sqrt(x * x + y * y)) * deg, lon]);
    }
    return pts;
  }

  /** The great circle from a to b, moved by whole turns so it starts at a where a is drawn. It may end at a copy
   * of b a turn away (the short way crosses the cut): one unbroken line, b's copy drawn at its end. */
  static placed(a, b) {
    const pts = FlightsMap.arc(a, b);
    const shift = a.lon - pts[0][1];
    return pts.map(([lat, lon]) => [lat, lon + shift]);
  }

  /** Draw {airports: [{icao, lat, lon, visits}], routes: [{origin, destination, flights}]}. */
  show({ airports, routes }) {
    this.layer.clearLayers();
    this.focusLayer.clearLayers();
    const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const known = airports.filter((a) => a.lat != null && a.lon != null);
    const at = Object.fromEntries(known.map((a) => [a.icao, a]));
    const arcs = routes.filter((r) => at[r.origin] && at[r.destination])
      .map((r) => [FlightsMap.arc(at[r.origin], at[r.destination], 24), r.flights]);
    const cut = FlightsMap.cut(known, arcs);
    // Every airport within the same 360 degrees, starting at the cut: a route to Tokyo from New York and Tokyo's
    // dot on the same side of the date line.
    const places = airports.filter((a) => a.lat != null && a.lon != null)
      .map((a) => ({ ...a, lon: cut + ((((a.lon - cut) % 360) + 360) % 360) }));
    this.where = Object.fromEntries(places.map((a) => [a.icao, a]));
    this.labels = [];
    const most = Math.max(1, ...routes.map((r) => r.flights));
    const ghosts = [];  // airports a route reaches round the back of the map: drawn again where it ends
    for (const r of routes) {
      const a = this.where[r.origin], b = this.where[r.destination];
      if (!a || !b) continue;
      const pts = FlightsMap.placed(a, b), end = pts[pts.length - 1];
      if (Math.abs(end[1] - b.lon) > 180) ghosts.push({ ...b, lon: b.lon + 360 * Math.round((end[1] - b.lon) / 360) });
      const line = L.polyline(pts, { className: "fm-route", weight: 1.6 + 3 * (r.flights / most), opacity: 0.85 })
        .bindTooltip(`${esc(r.origin)} → ${esc(r.destination)}<br>${r.flights} flight${r.flights === 1 ? "" : "s"}`, { sticky: true })
        .addTo(this.layer);
      line.on("click", () => { this.focus(r.origin, r.destination); if (this.onRoute) this.onRoute(r.origin, r.destination); });
    }
    const drawn = new Set();
    for (const a of [...places, ...ghosts]) {
      const key = `${a.icao}@${Math.round(a.lon)}`;
      if (drawn.has(key)) continue;
      drawn.add(key);
      L.circleMarker([a.lat, a.lon], { className: "fm-airport", radius: 3 + Math.min(Math.sqrt(a.visits), 4) * 0.8, weight: 1.5, fillOpacity: 0.9 })
        .bindTooltip(`<b>${esc(a.icao)}</b><br>${a.visits} visit${a.visits === 1 ? "" : "s"}`)
        .addTo(this.layer);
      const label = L.marker([a.lat, a.lon], { interactive: false, keyboard: false,
        icon: L.divIcon({ className: "", html: `<div class="fm-label">${esc(a.icao)}</div>`, iconSize: [0, 0] }) }).addTo(this.layer);
      this.labels.push({ marker: label, visits: a.visits });
    }
    this.bounds = places.length ? L.latLngBounds([...places, ...ghosts].map((a) => [a.lat, a.lon])) : null;
    this.fit();
    this.declutter();
    return places.length;
  }

  fit() {
    this.map.invalidateSize();
    if (this.bounds) this.map.fitBounds(this.bounds.pad(0.25), { maxZoom: 7, animate: false });
  }

  /** One route picked out (a row clicked in the table, or the route itself); null clears it. */
  focus(origin, destination) {
    this.focusLayer.clearLayers();
    const a = this.where?.[origin], b = this.where?.[destination];
    if (!a || !b) return;
    const pts = FlightsMap.placed(a, b);
    L.polyline(pts, { className: "fm-focus", weight: 4, opacity: 1, interactive: false }).addTo(this.focusLayer);
    this.map.fitBounds(L.latLngBounds(pts).pad(0.3), { maxZoom: 8 });
  }
}

if (typeof window !== "undefined") window.FlightsMap = FlightsMap;
