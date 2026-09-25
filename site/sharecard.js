/* The shared flight card and the Wrapped summary card: one picture, drawn here, in every place that shows it.
 *
 * One file, two copies: site/sharecard.js and src/localtc/ui/static/sharecard.js (tests/test_site.py keeps
 * them identical). The public page (localtc.tech/f/<slug>) boots it from the snapshot in the page; the
 * dashboard and the desktop app use it to preview a card before it's shared and to make its image; the
 * phone loads the public page with ?render=1 and takes the image it posts back.
 *
 * The picture is an SVG, 1200 x 630 (the size chat apps unfurl): the Earth as dots, seen from above the
 * middle of the route (an orthographic projection, so a long flight shows the planet's curve), the great
 * circle glowing across it, the route's codes and numbers, and one line from the radio. There's no server
 * drawing it: the account server hasn't the CPU on the free plan, so the pilot's own browser makes the PNG.
 *
 *   ShareCard.svg(card, land)          -> SVG markup
 *   await ShareCard.png(card, {base})  -> PNG Blob, fonts embedded
 *   await ShareCard.mount(el, card, {base, animate})
 */
(function () {
  "use strict";

  const W = 1200, H = 630;
  const RAD = Math.PI / 180, DEG = 180 / Math.PI;
  const C = {
    bg: "#0b0d10", land: "#36424f", landNear: "#56788f", grid: "rgba(255,255,255,0.045)", limb: "rgba(108,201,232,0.28)",
    text: "#e8ecf1", text2: "#b3bcc6", text3: "#8a939d", green: "#5fd068", cyan: "#6cc9e8", amber: "#e7b24a",
  };
  const SANS = "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";
  const MONO = "'JetBrains Mono', ui-monospace, Menlo, Consolas, monospace";

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (n) => Math.round(Number(n) || 0).toLocaleString("en-US");
  const duration = (min) => {
    if (min == null) return "–";
    const m = Math.round(min);
    return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
  };
  const dateText = (iso) => {
    const d = new Date(`${iso}T12:00:00Z`);
    return isNaN(d) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  };

  // --- the Earth ------------------------------------------------------------------------------------------

  /** An orthographic view: the globe seen from straight above (lat0, lon0), radius R px, centred at cx, cy. */
  function view(lat0, lon0, R, cx, cy) {
    const s0 = Math.sin(lat0 * RAD), c0 = Math.cos(lat0 * RAD);
    return {
      R, cx, cy,
      project(lat, lon) {
        const la = lat * RAD, dl = (lon - lon0) * RAD;
        const cosc = s0 * Math.sin(la) + c0 * Math.cos(la) * Math.cos(dl);
        return [cx + R * Math.cos(la) * Math.sin(dl), cy - R * (c0 * Math.sin(la) - s0 * Math.cos(la) * Math.cos(dl)), cosc >= 0];
      },
      invert(x, y) {
        const px = (x - cx) / R, py = (cy - y) / R, rho = Math.hypot(px, py);
        if (rho > 1) return null;
        if (rho < 1e-9) return [lat0, lon0];
        const c = Math.asin(rho), sc = Math.sin(c), cc = Math.cos(c);
        const lat = Math.asin(cc * s0 + (py * sc * c0) / rho) * DEG;
        const lon = lon0 + Math.atan2(px * sc, rho * c0 * cc - py * s0 * sc) * DEG;
        return [lat, ((lon + 540) % 360) - 180];
      },
    };
  }

  /** Points along the great circle from a to b ({lat, lon}). */
  function arc(a, b, steps = 72) {
    const [la1, lo1, la2, lo2] = [a.lat * RAD, a.lon * RAD, b.lat * RAD, b.lon * RAD];
    const d = 2 * Math.asin(Math.sqrt(Math.sin((la2 - la1) / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin((lo2 - lo1) / 2) ** 2));
    if (d < 1e-6) return [[a.lat, a.lon], [b.lat, b.lon]];
    const pts = [];
    for (let i = 0; i <= steps; i++) {
      const f = i / steps, A = Math.sin((1 - f) * d) / Math.sin(d), B = Math.sin(f * d) / Math.sin(d);
      const x = A * Math.cos(la1) * Math.cos(lo1) + B * Math.cos(la2) * Math.cos(lo2);
      const y = A * Math.cos(la1) * Math.sin(lo1) + B * Math.cos(la2) * Math.sin(lo2);
      const z = A * Math.sin(la1) + B * Math.sin(la2);
      pts.push([Math.atan2(z, Math.hypot(x, y)) * DEG, Math.atan2(y, x) * DEG]);
    }
    return pts;
  }

  /** The middle of a set of places on the sphere (their mean direction). */
  function centre(places) {
    let x = 0, y = 0, z = 0;
    for (const p of places) {
      x += Math.cos(p.lat * RAD) * Math.cos(p.lon * RAD);
      y += Math.cos(p.lat * RAD) * Math.sin(p.lon * RAD);
      z += Math.sin(p.lat * RAD);
    }
    return { lat: Math.atan2(z, Math.hypot(x, y)) * DEG, lon: Math.atan2(y, x) * DEG };
  }

  /** The view that fits ``lines`` (lists of [lat, lon]) into the box [x0, y0, x1, y1]. */
  function fit(lines, box, minSpanNm = 90, fill = 0.62) {
    const all = lines.flat().map(([lat, lon]) => ({ lat, lon }));
    const mid = centre(all);
    const unit = view(mid.lat, mid.lon, 1, 0, 0);
    let [minX, minY, maxX, maxY] = [Infinity, Infinity, -Infinity, -Infinity];
    for (const p of all) {
      const [x, y] = unit.project(p.lat, p.lon);
      minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
    const minSpan = minSpanNm / 3440; // radians on a unit globe: a short hop still shows some country around it
    const w = Math.max(maxX - minX, minSpan), h = Math.max(maxY - minY, minSpan * 0.6);
    const [bx0, by0, bx1, by1] = box;
    const R = Math.min((bx1 - bx0) / w, (by1 - by0) / h) * fill; // room round it: coasts, and the curve of the Earth
    const cx = (bx0 + bx1) / 2 - R * (minX + maxX) / 2, cy = (by0 + by1) / 2 - R * (minY + maxY) / 2;
    return view(mid.lat, mid.lon, R, cx, cy);
  }

  /** Is (lat, lon) on land? Even-odd over every ring, each with its box to skip it fast. */
  function onLand(land, lat, lon) {
    let inside = false;
    for (const r of land) {
      if (lon < r.box[0] || lon > r.box[2] || lat < r.box[1] || lat > r.box[3]) continue;
      const p = r.pts;
      for (let i = 0, j = p.length - 2; i < p.length; j = i, i += 2) {
        const yi = p[i + 1], yj = p[j + 1];
        if ((yi > lat) !== (yj > lat) && lon < ((p[j] - p[i]) * (lat - yi)) / (yj - yi) + p[i]) inside = !inside;
      }
    }
    return inside;
  }

  function prepareLand(raw) {
    return (raw && raw.rings ? raw.rings : []).map((pts) => {
      const box = [Infinity, Infinity, -Infinity, -Infinity];
      for (let i = 0; i < pts.length; i += 2) {
        box[0] = Math.min(box[0], pts[i]); box[2] = Math.max(box[2], pts[i]);
        box[1] = Math.min(box[1], pts[i + 1]); box[3] = Math.max(box[3], pts[i + 1]);
      }
      return { pts, box };
    });
  }

  /** The dots of land in view, brighter near the route: SVG. */
  function dots(v, land, near, spacing = 10) {
    const out = [], bright = [];
    const r = spacing * 0.2;
    for (let y = spacing / 2; y < H; y += spacing) {
      for (let x = spacing / 2 + ((y / spacing) % 2) * (spacing / 2); x < W; x += spacing) {
        const ll = v.invert(x, y);
        if (!ll || !onLand(land, ll[0], ll[1])) continue;
        const d = near(x, y);
        (d < 90 ? bright : out).push(`M${x.toFixed(1)} ${y.toFixed(1)}h0`);
      }
    }
    return `<path d="${out.join("")}" stroke="${C.land}" stroke-width="${(r * 2).toFixed(1)}" stroke-linecap="round"/>` +
      `<path d="${bright.join("")}" stroke="${C.landNear}" stroke-width="${(r * 2).toFixed(1)}" stroke-linecap="round"/>`;
  }

  function graticule(v) {
    const lines = [];
    const trace = (pts) => {
      let d = "", pen = false;
      for (const [lat, lon] of pts) {
        const [x, y, vis] = v.project(lat, lon);
        if (!vis) { pen = false; continue; }
        d += `${pen ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
        pen = true;
      }
      if (d) lines.push(d);
    };
    for (let lon = -180; lon < 180; lon += 15) trace(Array.from({ length: 81 }, (_, i) => [-80 + i * 2, lon]));
    for (let lat = -75; lat <= 75; lat += 15) trace(Array.from({ length: 181 }, (_, i) => [lat, -180 + i * 2]));
    return `<path d="${lines.join("")}" fill="none" stroke="${C.grid}" stroke-width="1"/>`;
  }

  function limb(v) {
    // The globe's edge, when a long route pulls far enough back to see it.
    if (v.R > 1400) return "";
    return `<circle cx="${v.cx.toFixed(1)}" cy="${v.cy.toFixed(1)}" r="${v.R.toFixed(1)}" fill="url(#sc-atmo)" stroke="${C.limb}" stroke-width="1.5"/>`;
  }

  /** The route as flown over the map, lifted into a bow (a flight arc, the way a route map draws one):
   *  [x, y] points on screen. ``lift``: the bow's height as a share of the distance between the ends. */
  function bowed(v, pts, lift = 0.16) {
    const xy = pts.map(([lat, lon]) => v.project(lat, lon)).filter((p) => p[2]);
    if (xy.length < 2) return xy;
    const [x0, y0] = xy[0], [x1, y1] = xy[xy.length - 1];
    const len = Math.hypot(x1 - x0, y1 - y0);
    let nx = -(y1 - y0) / (len || 1), ny = (x1 - x0) / (len || 1);
    if (ny > 0) { nx = -nx; ny = -ny; } // bow upward
    return xy.map(([x, y], i) => {
      const f = Math.sin((Math.PI * i) / (xy.length - 1)) * lift * len;
      return [x + nx * f, y + ny * f];
    });
  }

  const dOf = (xy) => xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");

  function pathOf(v, pts) {
    let d = "", pen = false;
    for (const [lat, lon] of pts) {
      const [x, y, vis] = v.project(lat, lon);
      if (!vis) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
      pen = true;
    }
    return d;
  }

  function nearFn(v, lines) {
    const pts = lines.flat().map(([lat, lon]) => v.project(lat, lon)).filter((p) => p[2]);
    return (x, y) => {
      let best = Infinity;
      for (const p of pts) best = Math.min(best, Math.hypot(p[0] - x, p[1] - y));
      return best;
    };
  }

  // --- words ------------------------------------------------------------------------------------------------

  let measurer = null;
  function textWidth(text, font) {
    if (typeof document !== "undefined" && document.createElement) {
      measurer = measurer || document.createElement("canvas").getContext("2d");
      measurer.font = font;
      return measurer.measureText(text).width;
    }
    return text.length * parseFloat(font.match(/(\d+)px/)[1]) * 0.62;
  }

  /** ``text`` in lines of at most ``width`` px, at most ``max`` of them, the last cut with an ellipsis. */
  function wrap(text, font, width, max) {
    const words = String(text).split(/\s+/);
    const lines = [];
    let line = "";
    for (const w of words) {
      const next = line ? `${line} ${w}` : w;
      if (textWidth(next, font) <= width || !line) line = next;
      else { lines.push(line); line = w; }
    }
    if (line) lines.push(line);
    if (lines.length > max) {
      lines.length = max;
      let last = lines[max - 1];
      while (last && textWidth(`${last}…`, font) > width) last = last.slice(0, -1);
      lines[max - 1] = `${last.trimEnd()}…`;
    }
    return lines;
  }

  const defs = () => `<defs>
<radialGradient id="sc-vignette" cx="70%" cy="40%" r="85%"><stop offset="0" stop-color="#131a22"/><stop offset="1" stop-color="${C.bg}"/></radialGradient>
<radialGradient id="sc-atmo" cx="50%" cy="50%" r="50%"><stop offset="0.9" stop-color="rgba(108,201,232,0)"/><stop offset="1" stop-color="rgba(108,201,232,0.10)"/></radialGradient>
<linearGradient id="sc-scrim" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="${C.bg}" stop-opacity="0.96"/><stop offset="0.42" stop-color="${C.bg}" stop-opacity="0.78"/><stop offset="0.62" stop-color="${C.bg}" stop-opacity="0"/></linearGradient>
<linearGradient id="sc-foot" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${C.bg}" stop-opacity="0"/><stop offset="0.45" stop-color="${C.bg}" stop-opacity="0.9"/></linearGradient>
<filter id="sc-glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="7"/></filter>
</defs>`;

  // The LocalTC logo's aircraft (logo.svg), centred on 0,0 in a 256 box.
  const PLANE = "M0 -70C5 -70 8 -64 8 -56L8 -22L74 8L74 18L8 2L8 28L26 40L26 48L4 42L4 54C4 60 2 64 0 66C-2 64 -4 60 -4 54L-4 42L-26 48L-26 40L-8 28L-8 2L-74 18L-74 8L-8 -22L-8 -56C-8 -64 -5 -70 0 -70Z";

  function brand(x, y, label) {
    return `<g transform="translate(${x} ${y})">
<rect width="30" height="30" rx="6.6" fill="#0a0e0c" stroke="rgba(255,255,255,0.14)"/><path transform="translate(15 15) scale(0.1172)" fill="#f5f7f6" d="${PLANE}"/>
<text x="42" y="21" font-family="${SANS}" font-weight="700" font-size="19" fill="${C.text}">LocalTC</text>
<text x="128" y="21" font-family="${MONO}" font-weight="500" font-size="13" letter-spacing="2.5" fill="${C.cyan}">${esc(label)}</text>
</g>`;
  }

  function stat(x, y, value, label, colour = C.text, size = 34) {
    return `<text x="${x}" y="${y}" font-family="${SANS}" font-weight="700" font-size="${size}" fill="${colour}" class="sc-num">${esc(value)}</text>
<text x="${x}" y="${y + 24}" font-family="${MONO}" font-size="12.5" letter-spacing="1.6" fill="${C.text3}">${esc(label)}</text>`;
  }

  function planeAt(xy, f) {
    const i = Math.max(1, Math.min(xy.length - 1, Math.round(f * (xy.length - 1))));
    const [x0, y0] = xy[i - 1], [x1, y1] = xy[i];
    const angle = Math.atan2(y1 - y0, x1 - x0) * DEG + 90;
    return `<g class="sc-plane" transform="translate(${x1.toFixed(1)} ${y1.toFixed(1)}) rotate(${angle.toFixed(1)})">
<path d="M0 -13 L3 -4 L12 2 L12 5 L3 2 L2.4 9 L5.5 12 L5.5 14 L0 12.4 L-5.5 14 L-5.5 12 L-2.4 9 L-3 2 L-12 5 L-12 2 L-3 -4 Z" fill="${C.text}" stroke="${C.bg}" stroke-width="1.2"/></g>`;
  }

  function airportDot(v, a, labelSide, colour) {
    const [x, y, vis] = v.project(a.lat, a.lon);
    if (!vis) return "";
    const dx = labelSide < 0 ? -14 : 14;
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="11" fill="${colour}" opacity="0.18"/>
<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="5" fill="${colour}" stroke="${C.bg}" stroke-width="2"/>
<text x="${(x + dx).toFixed(1)}" y="${(y + 5).toFixed(1)}" text-anchor="${labelSide < 0 ? "end" : "start"}" font-family="${MONO}" font-weight="600" font-size="15" fill="${C.text}">${esc(a.icao)}</text>`;
  }

  // --- the cards ----------------------------------------------------------------------------------------------

  function flightSvg(card, land) {
    const o = card.origin || {}, d = card.destination || {};
    const hasMap = o.lat != null && d.lat != null;
    const quote = card.quote && card.quote.text ? card.quote : null;
    const mapBox = [540, 40, 1170, quote ? 430 : 580];
    let map = "", route = "", plane = "", ends = "";
    if (hasMap) {
      const pts = arc(o, d);
      const v = fit([pts], mapBox);
      map = limb(v) + graticule(v) + dots(v, land, nearFn(v, [pts]));
      const xy = bowed(v, pts);
      const path = dOf(xy);
      const [ox, oy] = v.project(o.lat, o.lon), [dx, dy] = v.project(d.lat, d.lon);
      route = `<linearGradient id="sc-route" gradientUnits="userSpaceOnUse" x1="${ox.toFixed(1)}" y1="${oy.toFixed(1)}" x2="${dx.toFixed(1)}" y2="${dy.toFixed(1)}">
<stop offset="0" stop-color="${C.green}"/><stop offset="1" stop-color="${C.cyan}"/></linearGradient>
<path d="${path}" fill="none" stroke="url(#sc-route)" stroke-width="10" stroke-linecap="round" opacity="0.55" filter="url(#sc-glow)" class="sc-route"/>
<path d="${path}" fill="none" stroke="url(#sc-route)" stroke-width="3.5" stroke-linecap="round" class="sc-route"/>`;
      const chord = Math.hypot(dx - ox, dy - oy);
      plane = chord > 120 ? planeAt(xy, 0.62) : ""; // a hop across town: the two dots say it
      ends = airportDot(v, o, ox <= dx ? -1 : 1, C.green) + airportDot(v, d, ox <= dx ? 1 : -1, C.cyan);
    }

    // The codes, big, with the places under them.
    const codeFont = `800 76px ${SANS}`;
    const ow = textWidth(o.icao || "", codeFont);
    const arrowX = 64 + ow + 26, destX = arrowX + 64;
    const heading = `<text x="64" y="206" font-family="${SANS}" font-weight="800" font-size="76" letter-spacing="-1" fill="${C.text}">${esc(o.icao)}</text>
<path d="M${arrowX} 180 h40 m-12 -12 l12 12 l-12 12" fill="none" stroke="${C.green}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
<text x="${destX}" y="206" font-family="${SANS}" font-weight="800" font-size="76" letter-spacing="-1" fill="${C.text}">${esc(d.icao)}</text>
<text x="66" y="240" font-family="${SANS}" font-size="19" fill="${C.text2}">${esc(o.name || "")}</text>
<text x="${destX + 2}" y="240" font-family="${SANS}" font-size="19" fill="${C.text2}">${esc(d.name || "")}</text>`;
    const meta = [card.callsign, card.aircraft, dateText(card.date)].filter(Boolean).join("  ·  ");
    const fpm = card.landing_fpm < 0 ? card.landing_fpm : null;  // 0: not measured (older shares carry it)
    const landing = fpm != null ? `${fpm} fpm` : card.landed ? "---" : "No landing";
    const butter = fpm != null && fpm > -150;
    const statFont = `700 34px ${SANS}`;
    const col2 = Math.max(250, 64 + textWidth(duration(card.air_min), statFont) + 44,
      64 + textWidth(landing, statFont) + (butter ? 14 + 92 : 0) + 40);
    const stats = [
      stat(64, 348, duration(card.air_min), "AIR TIME"),
      stat(col2, 348, `${num(card.distance_nm)} nm`, "DISTANCE"),
      stat(64, 424, landing, "LANDING", butter ? C.green : C.text),
      stat(col2, 424, card.readback_pct != null ? `${card.readback_pct}%` : "–", "READBACKS"),
    ].join("");
    const badge = butter ? `<g transform="translate(${64 + textWidth(landing, statFont) + 14} 398)"><rect width="92" height="26" rx="13" fill="rgba(95,208,104,0.16)"/>
<text x="46" y="18" text-anchor="middle" font-family="${MONO}" font-weight="600" font-size="12" letter-spacing="1.5" fill="${C.green}">BUTTER</text></g>` : "";

    let q = "";
    if (quote) {
      const font = `400 20px ${MONO}`;
      const lines = wrap(`“${quote.text}”`, font, W - 64 * 2 - 24, 3);
      const top = H - 44 - lines.length * 28 - 30;
      const who = [quote.station, quote.mhz ? quote.mhz.toFixed(3) : ""].filter(Boolean).join("  ·  ").toUpperCase();
      q = `<rect x="0" y="${top - 40}" width="${W}" height="${H - top + 40}" fill="url(#sc-foot)"/>
<rect x="64" y="${top - 4}" width="4" height="${lines.length * 28 + 34}" rx="2" fill="${C.cyan}"/>
<text x="84" y="${top + 12}" font-family="${MONO}" font-weight="600" font-size="13" letter-spacing="2" fill="${C.cyan}">${esc(who)}</text>
${lines.map((l, i) => `<text x="84" y="${top + 44 + i * 28}" font-family="${MONO}" font-size="20" fill="${C.text}">${esc(l)}</text>`).join("")}`;
    }
    return svgFrame(`${map}
<rect width="${W}" height="${H}" fill="url(#sc-scrim)"/>
${route}${ends}${plane}
${brand(64, 60, "SHARED FLIGHT")}
${heading}
<text x="66" y="282" font-family="${MONO}" font-size="16" fill="${C.text3}">${esc(meta)}</text>
${stats}${badge}${q}
<text x="${W - 40}" y="${H - 22}" text-anchor="end" font-family="${MONO}" font-size="13" fill="${C.text3}">localtc.tech</text>`);
  }

  /** Every route of a period on one map, airports sized by visits, in ``box``: SVG. */
  function routesMap(card, land, box) {
    const places = (card.airports || []).filter((a) => a.lat != null && a.lon != null);
    const byIcao = Object.fromEntries(places.map((a) => [a.icao, a]));
    const lines = (card.routes || []).map((r) => [byIcao[r.origin], byIcao[r.destination], r.flights]).filter(([a, b]) => a && b);
    let map = "";
    if (places.length) {
      const arcs = lines.map(([a, b]) => arc(a, b, 48));
      const v = fit(arcs.length ? arcs : [places.map((p) => [p.lat, p.lon])], box, 600);
      const most = Math.max(1, ...lines.map((l) => l[2]));
      map = limb(v) + graticule(v) + dots(v, land, nearFn(v, arcs.length ? arcs : [places.map((p) => [p.lat, p.lon])]));
      map += arcs.map((pts, i) => `<path d="${dOf(bowed(v, pts, 0.12))}" fill="none" stroke="${C.cyan}" stroke-width="${(1.5 + 3 * lines[i][2] / most).toFixed(1)}" stroke-linecap="round" opacity="0.8" class="sc-route"/>`).join("");
      map += places.map((a) => {
        const [x, y, vis] = v.project(a.lat, a.lon);
        return vis ? `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(3 + Math.min(a.visits, 12) * 0.5).toFixed(1)}" fill="${C.green}" stroke="${C.bg}" stroke-width="1.5"/>` : "";
      }).join("");
    }
    return map;
  }

  function wrappedSvg(card, land) {
    const map = routesMap(card, land, [480, 40, 1160, 590]);
    const title = card.label || "My flying";
    const titleFont = `800 64px ${SANS}`;
    const titleLines = wrap(title, titleFont, 520, 2);
    const persona = card.persona ? `<text x="66" y="${150 + titleLines.length * 70}" font-family="${SANS}" font-weight="600" font-size="24" fill="${C.green}">${esc(card.persona)}</text>` : "";
    const y0 = 262 + titleLines.length * 70 - (card.persona ? 0 : 36);
    const big = 44;
    const stats = [
      stat(64, y0, num(card.flights), card.flights === 1 ? "FLIGHT" : "FLIGHTS", C.text, big),
      stat(214, y0, Number(card.hours) < 100 ? (Math.round(card.hours * 10) / 10).toLocaleString("en-US") : num(card.hours), "HOURS", C.text, big),
      stat(350, y0, num(card.distance_nm), "NM", C.text, big),
      stat(64, y0 + 96, num((card.airports || []).length), "AIRPORTS", C.text, big),
      stat(214, y0 + 96, card.best_landing_fpm != null ? `${card.best_landing_fpm}` : "---", "BEST FPM", card.best_landing_fpm > -150 ? C.green : C.text, big),
      stat(350, y0 + 96, card.top_airport || "–", "TOP AIRPORT", C.text, big),
    ].join("");
    return svgFrame(`${map}
<rect width="${W}" height="${H}" fill="url(#sc-scrim)"/>
${brand(64, 60, "ATC WRAPPED")}
${titleLines.map((l, i) => `<text x="64" y="${170 + i * 70}" font-family="${SANS}" font-weight="800" font-size="64" letter-spacing="-1" fill="${C.text}">${esc(l)}</text>`).join("")}
${persona}${stats}
<text x="66" y="${y0 + 170}" font-family="${MONO}" font-size="16" fill="${C.cyan}">${esc(card.distance_framing || "")}</text>
<text x="${W - 40}" y="${H - 22}" text-anchor="end" font-family="${MONO}" font-size="13" fill="${C.text3}">localtc.tech</text>`);
  }

  /** A whole Earth, faint, off to the right: turned to where the pilot flies, their airports on it. */
  function globe({ lat, lon, airports = [] }, land) {
    const v = view(lat, lon, 330, 1010, 330);
    const pins = airports.map((a) => {
      const [x, y, vis] = v.project(a.lat, a.lon);
      return vis ? `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4" fill="${C.green}" stroke="${C.bg}" stroke-width="1.5"/>` : "";
    }).join("");
    return `<g opacity="0.6">${limb(v)}${graticule(v)}${dots(v, land, () => 0, 11)}</g>${pins}`;
  }

  /** One of Wrapped's slides: a headline number and what it means, or a quote, or the map. */
  function slideSvg(card, land) {
    const accent = card.accent === "cyan" ? C.cyan : card.accent === "amber" ? C.amber : C.green;
    const map = card.map ? routesMap(card.map, land, [560, 40, 1160, 590]) : card.globe && !card.quote ? globe(card.globe, land) : "";
    const width = card.map ? 520 : W - 128;
    let body = "";
    if (card.quote) {
      const font = `400 30px ${MONO}`;
      const lines = wrap(`“${card.quote.text}”`, font, W - 128 - 24, 6);
      const who = [card.quote.station, card.quote.mhz ? Number(card.quote.mhz).toFixed(3) : ""].filter(Boolean).join("  ·  ").toUpperCase();
      const top = 200;
      body = `<rect x="64" y="${top - 30}" width="5" height="${lines.length * 42 + 50}" rx="2.5" fill="${C.cyan}"/>
<text x="90" y="${top}" font-family="${MONO}" font-weight="600" font-size="16" letter-spacing="2" fill="${C.cyan}">${esc(who)}</text>
${lines.map((l, i) => `<text x="90" y="${top + 50 + i * 42}" font-family="${MONO}" font-size="30" fill="${C.text}">${esc(l)}</text>`).join("")}
<text x="90" y="${top + 50 + lines.length * 42 + 40}" font-family="${SANS}" font-size="22" fill="${C.text2}">${esc(card.sub || "")}</text>`;
    } else {
      const valueFont = `800 ${card.small ? 84 : 120}px ${SANS}`;
      const values = wrap(String(card.value ?? ""), valueFont, width, 2);
      const size = card.small ? 84 : 120;
      const vy = 300 - (values.length - 1) * size * 0.5;
      const last = values[values.length - 1];
      const unitX = 64 + textWidth(last, valueFont) + 18;
      body = `${values.map((l, i) => `<text x="64" y="${vy + i * size}" font-family="${SANS}" font-weight="800" font-size="${size}" letter-spacing="-2" fill="${C.text}" class="sc-num">${esc(l)}</text>`).join("")}
${card.unit ? `<text x="${unitX}" y="${vy + (values.length - 1) * size}" font-family="${SANS}" font-weight="700" font-size="${Math.round(size * 0.4)}" fill="${accent}">${esc(card.unit)}</text>` : ""}
${wrap(card.label || "", `600 34px ${SANS}`, width, 2).map((l, i) => `<text x="66" y="${vy + (values.length - 1) * size + 62 + i * 42}" font-family="${SANS}" font-weight="600" font-size="34" fill="${accent}">${esc(l)}</text>`).join("")}
${wrap(card.sub || "", `400 22px ${SANS}`, width, 3).map((l, i) => `<text x="66" y="${vy + (values.length - 1) * size + 150 + i * 32}" font-family="${SANS}" font-size="22" fill="${C.text2}">${esc(l)}</text>`).join("")}`;
    }
    return svgFrame(`${map}
${map ? `<rect width="${W}" height="${H}" fill="url(#sc-scrim)"/>` : ""}
${brand(64, 60, card.kicker || "ATC WRAPPED")}
${body}
<text x="${W - 40}" y="${H - 22}" text-anchor="end" font-family="${MONO}" font-size="13" fill="${C.text3}">${esc(card.footer || "localtc.tech")}</text>`);
  }

  function svgFrame(body, style = "") {
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="sharecard">${style}${defs()}
<rect width="${W}" height="${H}" fill="url(#sc-vignette)"/>
${body}</svg>`;
  }

  // --- loading, the image, the page ---------------------------------------------------------------------

  const cache = {};
  async function loadLand(base) {
    if (!cache.land) {
      cache.land = fetch(`${base}vendor/land-110m.json`).then((r) => (r.ok ? r.json() : { rings: [] }))
        .catch(() => ({ rings: [] })).then(prepareLand);
    }
    return cache.land;
  }

  async function fontCss(base) {
    if (!cache.fonts) {
      const load = async (file, family, weights) => {
        const blob = await (await fetch(`${base}fonts/${file}`)).blob();
        const url = await new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(blob); });
        return `@font-face{font-family:"${family}";src:url(${url}) format("woff2");font-weight:${weights};}`;
      };
      cache.fonts = Promise.all([load("inter-latin-var.woff2", "Inter", "300 800"), load("jetbrains-mono-latin-var.woff2", "JetBrains Mono", "400 600")])
        .then((css) => `<style>${css.join("")}</style>`).catch(() => "");
    }
    return cache.fonts;
  }

  async function fontsReady() {
    if (typeof document === "undefined" || !document.fonts) return;
    try { await Promise.all([document.fonts.load(`800 76px Inter`), document.fonts.load(`20px "JetBrains Mono"`)]); } catch (_) { /* system fonts, then */ }
  }

  let uid = 0;
  function svg(card, land) {
    const kind = card && card.kind;
    const markup = kind === "wrapped" ? wrappedSvg(card, land) : kind === "slide" ? slideSvg(card, land) : flightSvg(card || {}, land);
    // Its own ids, so two cards on one page (a preview beside a slide) don't borrow each other's gradients.
    const id = ++uid;
    return markup.replace(/(id="|url\(#)sc-([a-z]+)/g, `$1sc-$2-${id}`);
  }

  /** The card as a PNG Blob, 1200 x 630, its fonts inside it. ``base``: where the site's files are ("" here). */
  async function png(card, { base = "" } = {}) {
    await fontsReady();
    const [land, style] = await Promise.all([loadLand(base), fontCss(base)]);
    const markup = svg(card, land).replace('class="sharecard">', `class="sharecard">${style}`);
    const img = new Image();
    img.decoding = "sync";
    const url = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml" }));
    try {
      await new Promise((ok, fail) => { img.onload = ok; img.onerror = () => fail(new Error("The card didn't draw.")); img.src = url; });
      const canvas = document.createElement("canvas");
      canvas.width = W; canvas.height = H;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, W, H);
      return await new Promise((ok, fail) => canvas.toBlob((b) => (b ? ok(b) : fail(new Error("The card didn't draw."))), "image/png"));
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  /** Draw the card into ``el``; ``animate``: the route draws itself, the aircraft flies it, the numbers count. */
  async function mount(el, card, { base = "", animate = false } = {}) {
    await fontsReady();
    const land = await loadLand(base);
    el.innerHTML = svg(card, land);
    const root = el.querySelector("svg");
    root.removeAttribute("width");
    root.removeAttribute("height");
    if (!animate || (typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches)) return root;
    root.classList.add("sc-animate");
    const routes = [...root.querySelectorAll(".sc-route")];
    for (const p of routes) {
      const len = p.getTotalLength();
      p.style.strokeDasharray = `${len} ${len}`;
      p.style.strokeDashoffset = `${len}`;
    }
    const plane = root.querySelector(".sc-plane");
    const main = routes[routes.length - 1];
    const planeFinal = plane ? plane.getAttribute("transform") : "";
    const nums = [...root.querySelectorAll(".sc-num")].map((t) => [t, t.textContent]);
    const t0 = performance.now(), dur = 1900;
    const ease = (x) => 1 - Math.pow(1 - x, 3);
    const frame = (now) => {
      const f = Math.min(1, (now - t0) / dur), e = ease(f);
      for (const p of routes) p.style.strokeDashoffset = `${p.getTotalLength() * (1 - e)}`;
      if (plane && main) {
        const len = main.getTotalLength(), at = len * Math.min(e, 0.62); // it stops where the still card has it
        const a = main.getPointAtLength(Math.max(0, at - 1)), b = main.getPointAtLength(Math.min(len, at + 1));
        plane.setAttribute("transform", f < 1 ? `translate(${b.x.toFixed(1)} ${b.y.toFixed(1)}) rotate(${(Math.atan2(b.y - a.y, b.x - a.x) * DEG + 90).toFixed(1)})` : planeFinal);
      }
      for (const [t, final] of nums) {
        const m = /^(-?)([\d,]+)(.*)$/.exec(final);
        if (m && f < 1) t.textContent = `${m[1]}${Math.round(Number(m[2].replace(/,/g, "")) * e).toLocaleString("en-US")}${m[3]}`;
        else t.textContent = final;
      }
      if (f < 1) requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
    return root;
  }

  /** The public page: the card from the snapshot in the page, a button for its image, and ?render=1 for the phone. */
  async function boot() {
    const holder = document.getElementById("share");
    const data = document.getElementById("share-data");
    if (!holder || !data) return;
    const card = JSON.parse(data.textContent || "null");
    if (!card) return;
    const base = `${holder.dataset.site || ""}/`;
    const render = new URLSearchParams(location.search).get("render") === "1";
    const frame = document.createElement("div");
    frame.className = "share-card";
    holder.appendChild(frame);
    await mount(frame, card, { base, animate: !render });
    if (render) {
      // The companion app draws its share image with this page: it listens for the PNG as a data URL.
      const blob = await png(card, { base });
      const url = await new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(blob); });
      const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.localtcCard;
      if (handler) handler.postMessage(url);
      return;
    }
    const bar = document.createElement("div");
    bar.className = "share-actions";
    bar.innerHTML = `<a class="btn btn-primary" href="${esc(base)}">Get LocalTC</a><button class="btn btn-ghost" type="button">Save image</button>`;
    bar.querySelector("button").addEventListener("click", async () => download(await png(card, { base }), card));
    holder.appendChild(bar);
  }

  function filename(card) {
    if (card.kind === "slide") return `localtc-wrapped-${String(card.name || "slide").replace(/\W+/g, "-").toLowerCase()}.png`;
    if (card.kind === "wrapped") return `localtc-wrapped-${String(card.label || "").replace(/\W+/g, "-").toLowerCase()}.png`;
    return `localtc-${(card.origin || {}).icao}-${(card.destination || {}).icao}-${card.date}.png`.toLowerCase();
  }

  function download(blob, card) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename(card);
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  // --- the share dialog (dashboard and app) ------------------------------------------------------------------

  /**
   * Share a flight: the card as the public will see it, a choice of the radio line it quotes, then the link.
   * opts: {base, moments: [...], shared: url|null, card(quote) -> snapshot for the preview,
   *        share(quote, withReplay) -> {slug, url, card}, putImage(slug, blob), unshare(slug), slug,
   *        replay: whether the flight has a replay to put on the page (the path flown and the radio)}
   */
  function dialog(opts) {
    const box = document.createElement("div");
    box.className = "share-dialog";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-label", "Share this flight");
    const moments = opts.moments || [];
    const quotes = moments.map((m, i) => `<label><input type="radio" name="sc-quote" value="${i}"${i === 0 ? " checked" : ""}>
<b>${esc(m.label)}</b><span>${esc(m.text)}</span></label>`).join("");
    box.innerHTML = `<div class="share-box">
<h2>Share this flight</h2>
<div class="share-card"></div>
${moments.length ? `<div class="share-quotes" role="radiogroup" aria-label="The line from the radio on the card">${quotes}
<label><input type="radio" name="sc-quote" value="-1"${moments.length ? "" : " checked"}><b>No quote</b><span>Just the route and the numbers</span></label></div>`
    : `<p>Upload the flight's replay to quote a line from the radio on the card.</p>`}
${opts.replay ? `<label class="share-opt"><input type="checkbox" class="sc-replay" checked><span><b>Put the replay on the page</b>
A small replay under the card: the path you flew and the radio, both sides, timed from the start of the flight.</span></label>` : ""}
<p class="sc-privacy"></p>
<div class="share-row sc-link" hidden><input type="text" readonly aria-label="The link"><button class="btn btn-ghost btn-sm sc-copy" type="button">Copy</button></div>
<div class="share-row">
<button class="btn btn-primary sc-go" type="button">Share</button>
<button class="btn btn-ghost btn-sm sc-native" type="button" hidden>Send…</button>
<button class="btn btn-ghost btn-sm sc-save" type="button">Save image</button>
<button class="btn btn-ghost btn-sm sc-stop" type="button" hidden>Stop sharing</button>
<button class="btn btn-ghost btn-sm sc-close" type="button">Close</button>
</div>
<p class="share-status" role="status" aria-live="polite"></p>
</div>`;
    document.body.appendChild(box);
    const q = (sel) => box.querySelector(sel);
    const status = (text, error = false) => { q(".share-status").textContent = text; q(".share-status").classList.toggle("error", error); };
    let slug = opts.slug || null, url = opts.shared || null;
    const chosen = () => {
      const r = box.querySelector("input[name=sc-quote]:checked");
      const i = r ? Number(r.value) : -1;
      return i >= 0 ? moments[i] : null;
    };
    const preview = () => mount(q(".share-card"), opts.card(chosen()), { base: opts.base || "" });
    const withReplay = () => !!(q(".sc-replay") && q(".sc-replay").checked);
    const privacy = () => {
      q(".sc-privacy").textContent = `Anyone with the link sees this card: the route, the date, these numbers${moments.length ? ", the line you pick" : ""}`
        + ` and the aircraft and runways${withReplay() ? ", with the path flown and the radio (gates included, if ATC said them)" : ""}.`
        + ` Never your email or the time of day${withReplay() ? "" : ", the track or the rest of the radio"}. Stop sharing whenever you like.`;
    };
    if (q(".sc-replay")) q(".sc-replay").addEventListener("change", privacy);
    const linked = () => {
      q(".sc-link").hidden = !url;
      q(".sc-link input").value = url || "";
      q(".sc-go").textContent = url ? "Update" : "Share";
      q(".sc-stop").hidden = !url;
      q(".sc-native").hidden = !url || !navigator.share;
    };
    const close = () => { box.remove(); document.removeEventListener("keydown", onKey); if (opts.onClose) opts.onClose(url); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    box.addEventListener("click", (e) => { if (e.target === box) close(); });
    box.querySelectorAll("input[name=sc-quote]").forEach((r) => r.addEventListener("change", preview));
    q(".sc-close").onclick = close;
    q(".sc-copy").onclick = async () => {
      try { await navigator.clipboard.writeText(url); status("Link copied."); } catch (_) { q(".sc-link input").select(); }
    };
    q(".sc-native").onclick = () => navigator.share({ url }).catch(() => {});
    q(".sc-save").onclick = async () => {
      try { const card = opts.card(chosen()); download(await png(card, { base: opts.base || "" }), card); } catch (err) { status(err.message, true); }
    };
    q(".sc-go").onclick = async () => {
      const button = q(".sc-go");
      button.disabled = true;
      status(url ? "Updating ..." : "Sharing ...");
      try {
        const made = await opts.share(chosen(), withReplay());
        slug = made.slug; url = made.url;
        linked();
        status("Drawing the card ...");
        await opts.putImage(slug, await png(made.card, { base: opts.base || "" }));
        status("Shared. Anyone with the link can see it.");
      } catch (err) {
        status(err.message || String(err), true);
      } finally {
        button.disabled = false;
      }
    };
    q(".sc-stop").onclick = async () => {
      if (!confirm("Stop sharing this flight? The link stops working; sharing it again makes a new one.")) return;
      try { await opts.unshare(slug); slug = null; url = null; linked(); status("Not shared any more."); } catch (err) { status(err.message, true); }
    };
    linked();
    privacy();
    preview();
    q(".sc-go").focus();
    return { close };
  }

  const ShareCard = { svg, png, mount, download, filename, loadLand, arc, dialog, W, H };
  if (typeof window !== "undefined") {
    window.ShareCard = ShareCard;
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
  }
})();
