/* Every flight in the logbook on one map: the airports (bigger for more visits) and the routes between them
   (great circles, thicker for more flights). Click a route or an airport for its numbers; focus() picks one
   flight out. Needs Leaflet.

   One file, two copies: site/flightsmap.js (localtc.tech's Dashboard) and
   src/localtc/ui/static/flightsmap.js (the app's Logbook tab). tests/test_site.py keeps them identical. */

class FlightsMap {
  /** ``el``: the map's element. ``tiles``: draw an OpenStreetMap background (false: a plain dark map). */
  constructor(el, { tiles = true, onRoute = null } = {}) {
    this.onRoute = onRoute;
    this.map = L.map(el, { worldCopyJump: true, attributionControl: tiles, zoomSnap: 0.5 }).setView([30, -40], 2);
    if (tiles) {
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 12,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(this.map);
    }
    el.classList.add("flights-map", tiles ? "fm-tiles" : "fm-plain");
    this.layer = L.layerGroup().addTo(this.map);
    this.focusLayer = L.layerGroup().addTo(this.map);
    this.bounds = null;
  }

  /** Airports and routes from a list of logbook flights (each with origin/destination and their lat/lon). */
  static summarize(flights) {
    const airports = new Map(), routes = new Map();
    const visit = (icao, lat, lon) => {
      if (!icao || lat == null || lon == null) return;
      const a = airports.get(icao) || { icao, lat, lon, visits: 0 };
      a.visits += 1;
      airports.set(icao, a);
    };
    for (const f of flights) {
      visit(f.origin, f.origin_lat, f.origin_lon);
      visit(f.destination, f.destination_lat, f.destination_lon);
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

  /** Draw {airports: [{icao, lat, lon, visits}], routes: [{origin, destination, flights}]}. */
  show({ airports, routes }) {
    this.layer.clearLayers();
    this.focusLayer.clearLayers();
    const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const places = airports.filter((a) => a.lat != null && a.lon != null);
    this.where = Object.fromEntries(places.map((a) => [a.icao, a]));
    const most = Math.max(1, ...routes.map((r) => r.flights));
    for (const r of routes) {
      const a = this.where[r.origin], b = this.where[r.destination];
      if (!a || !b) continue;
      const line = L.polyline(FlightsMap.arc(a, b), { className: "fm-route", weight: 1.6 + 3 * (r.flights / most), opacity: 0.85 })
        .bindTooltip(`${esc(r.origin)} → ${esc(r.destination)}<br>${r.flights} flight${r.flights === 1 ? "" : "s"}`, { sticky: true })
        .addTo(this.layer);
      line.on("click", () => { this.focus(r.origin, r.destination); if (this.onRoute) this.onRoute(r.origin, r.destination); });
    }
    for (const a of places) {
      L.circleMarker([a.lat, a.lon], { className: "fm-airport", radius: 3.5 + Math.min(a.visits, 10) * 0.6, weight: 1.5, fillOpacity: 0.9 })
        .bindTooltip(`<b>${esc(a.icao)}</b><br>${a.visits} visit${a.visits === 1 ? "" : "s"}`)
        .addTo(this.layer);
      L.marker([a.lat, a.lon], { interactive: false, keyboard: false,
        icon: L.divIcon({ className: "", html: `<div class="fm-label">${esc(a.icao)}</div>`, iconSize: [0, 0] }) }).addTo(this.layer);
    }
    this.bounds = places.length ? L.latLngBounds(places.map((a) => [a.lat, a.lon])) : null;
    this.fit();
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
    const pts = FlightsMap.arc(a, b);
    L.polyline(pts, { className: "fm-focus", weight: 4, opacity: 1, interactive: false }).addTo(this.focusLayer);
    this.map.fitBounds(L.latLngBounds(pts).pad(0.3), { maxZoom: 8 });
  }
}

if (typeof window !== "undefined") window.FlightsMap = FlightsMap;
