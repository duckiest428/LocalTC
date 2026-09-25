/* The shared flight's page, under the card: the flight's details, and (if the pilot put it on the page) a
 * small replay that plays by itself: the path drawn across the map as the aircraft flies it, the altitude
 * profile, and the radio scrolling alongside. Nothing to press; it loops, and it holds still for anyone who
 * has asked their system for less motion.
 *
 * Only on the public page (localtc.tech/f/<slug>), from the snapshot the account server put in it
 * (#share-extra: cardmodel.js flightDetails and miniReplay). Time runs from the start of the flight.
 *
 * Playback isn't linear: a three-hour flight is mostly cruise and quiet, and the talking is at either end. The
 * clock runs through each part in proportion half to its length, half to how much was said in it, so the
 * calls on the ground get time to be read and the cruise still passes.
 */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const MAP_W = 720, MAP_H = 440, PROF_H = 64;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clock = (s) => {
    s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return `${h ? `${h}:${String(m).padStart(2, "0")}` : m}:${String(sec).padStart(2, "0")}`;
  };
  const fl = (ft) => (ft >= 18000 ? `FL${Math.round(ft / 100)}` : `${Math.round(ft / 10) * 10} ft`);

  function readJson(id) {
    const el = document.getElementById(id);
    try { return el ? JSON.parse(el.textContent || "null") : null; } catch (_) { return null; }
  }

  // --- the details -------------------------------------------------------------------------------------------

  function details(d, card) {
    if (!d) return "";
    const item = (label, value, sub = "") => value ? `<div class="sd-item"><dt>${esc(label)}</dt><dd>${esc(value)}${sub ? `<span>${esc(sub)}</span>` : ""}</dd></div>` : "";
    const wx = (w) => {  // the wind, and under it the altimeter and the ATIS letter
      const rest = [w.altimeter, w.atis && `ATIS ${w.atis}`].filter(Boolean).join(" · ");
      return w.wind ? [`Wind ${w.wind}`, rest] : [rest, ""];
    };
    const o = (card.origin || {}).icao || "", dst = (card.destination || {}).icao || "";
    const runways = d.departure_runway || d.arrival_runway
      ? `${d.departure_runway ? `${o} ${d.departure_runway}` : o} → ${d.arrival_runway ? `${dst} ${d.arrival_runway}` : dst}` : "";
    const livery = d.livery && d.livery !== d.aircraft ? d.livery : "";
    const items = [
      item("Aircraft", d.aircraft || livery, d.aircraft ? livery : ""),
      item("Runways", runways),
      item("Cruise", d.max_alt_ft > 1000 ? fl(d.max_alt_ft) : ""),
      item(`Weather at ${o}`, ...wx(d.weather?.departure || {})),
      item(`Weather at ${dst}`, ...wx(d.weather?.arrival || {})),
    ].join("");
    const route = (d.route || []).length
      ? `<div class="sd-route"><h3>Route filed</h3><p><b>${esc(o)}</b> ${d.route.map((x) => `<span>${esc(x)}</span>`).join(" ")} <b>${esc(dst)}</b></p></div>` : "";
    if (!items && !route) return "";
    return `<div class="share-details"><h2>The flight</h2><dl>${items}</dl>${route}</div>`;
  }

  // --- the map -----------------------------------------------------------------------------------------------

  /** A flat map fitted round the track and the route: longitude squeezed by the cosine of the middle latitude. */
  function frame(points) {
    let minLat = 90, maxLat = -90, minLon = 180, maxLon = -180;
    for (const [lat, lon] of points) {
      minLat = Math.min(minLat, lat); maxLat = Math.max(maxLat, lat);
      minLon = Math.min(minLon, lon); maxLon = Math.max(maxLon, lon);
    }
    const k = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);
    const w = Math.max((maxLon - minLon) * k, 0.25), h = Math.max(maxLat - minLat, 0.18);
    const pad = 48;
    const scale = Math.min((MAP_W - 2 * pad) / w, (MAP_H - 2 * pad) / h);
    const cx = (minLon + maxLon) / 2, cy = (minLat + maxLat) / 2;
    const xy = (lat, lon) => [MAP_W / 2 + (lon - cx) * k * scale, MAP_H / 2 - (lat - cy) * scale];
    const span = { lat: MAP_H / 2 / scale + 1, lon: MAP_W / 2 / (k * scale) + 1 };
    return { xy, box: [cx - span.lon, cy - span.lat, cx + span.lon, cy + span.lat] };
  }

  function landPath(rings, f) {
    const [x0, y0, x1, y1] = f.box;
    const out = [];
    for (const r of rings) {
      let a = 180, b = 90, c = -180, d = -90;
      for (let i = 0; i < r.length; i += 2) { a = Math.min(a, r[i]); c = Math.max(c, r[i]); b = Math.min(b, r[i + 1]); d = Math.max(d, r[i + 1]); }
      if (c < x0 || a > x1 || d < y0 || b > y1 || c - a > 300) continue;
      let p = "";
      for (let i = 0; i < r.length; i += 2) {
        const [x, y] = f.xy(r[i + 1], r[i]);
        p += `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
      }
      out.push(`${p}Z`);
    }
    return out.join("");
  }

  // --- the clock -----------------------------------------------------------------------------------------------

  /** Flight time for a share of the playback: half by time, half by the calls made. */
  function warp(duration, radio) {
    const times = radio.map((l) => l.t).sort((a, b) => a - b);
    const said = (t) => {
      if (!times.length) return t / duration;
      let lo = 0, hi = times.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (times[mid] <= t) lo = mid + 1; else hi = mid; }
      return lo / times.length;
    };
    const N = 600, us = [], ts = [];
    for (let i = 0; i <= N; i++) {
      const t = (duration * i) / N;
      ts.push(t);
      us.push(0.5 * (t / duration) + 0.5 * said(t));
    }
    return (u) => {  // playback share, 0..1 -> seconds into the flight
      let lo = 0, hi = N;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (us[mid] < u) lo = mid + 1; else hi = mid; }
      if (lo === 0) return 0;
      const k = (u - us[lo - 1]) / Math.max(1e-9, us[lo] - us[lo - 1]);
      return ts[lo - 1] + k * (ts[lo] - ts[lo - 1]);
    };
  }

  // --- the replay ----------------------------------------------------------------------------------------------

  async function replay(host, r, card, base) {
    const track = r.track || [];
    if (track.length < 2) return;
    const duration = Math.max(r.duration_s || 0, track[track.length - 1][0], 1);
    const radio = (r.radio || []).filter((l) => l.t <= duration + 60);
    const origin = card.origin || {}, destination = card.destination || {};
    const f = frame([...track.map((p) => [p[1], p[2]]), ...(r.route || []).map((x) => [x.lat, x.lon]),
      ...[origin, destination].filter((a) => typeof a.lat === "number").map((a) => [a.lat, a.lon])]);
    const pts = track.map((p) => f.xy(p[1], p[2]));
    const maxAlt = Math.max(1000, ...track.map((p) => p[3]));
    const seconds = Math.min(90, Math.max(40, 20 + radio.length * 0.45));
    const flightTime = warp(duration, radio);
    const toU = (() => {  // seconds into the flight -> share of the playback, for the profile's x
      const N = 400, tbl = [];
      for (let i = 0; i <= N; i++) tbl.push(i / N);
      const tt = tbl.map(flightTime);
      return (t) => {
        let lo = 0, hi = N;
        while (lo < hi) { const mid = (lo + hi) >> 1; if (tt[mid] < t) lo = mid + 1; else hi = mid; }
        return tbl[lo];
      };
    })();

    const who = (l) => l.who === "atc" ? (l.station || "ATC") : l.who === "copilot" ? "Copilot" : (card.callsign || "Pilot");
    const airport = (a, cls) => {
      if (typeof a.lat !== "number") return "";
      const [x, y] = f.xy(a.lat, a.lon);
      return `<g class="${cls}"><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="5"/><text x="${(x + 9).toFixed(1)}" y="${(y + 4).toFixed(1)}">${esc(a.icao)}</text></g>`;
    };
    const route = (r.route || []).map((x) => f.xy(x.lat, x.lon));
    const profile = track.map((p) => `${(toU(p[0]) * MAP_W).toFixed(1)},${(PROF_H - 4 - (p[3] / maxAlt) * (PROF_H - 12)).toFixed(1)}`);

    host.insertAdjacentHTML("afterbegin", `<div class="mini-replay">
<div class="mr-head"><span class="mr-live" aria-hidden="true"></span><b>Replay</b><span class="mr-clock">0:00</span><span class="mr-phase"></span>
<span class="mr-read"><span class="mr-alt"></span><span class="mr-gs"></span></span></div>
<div class="mr-body">
<div class="mr-left">
<svg class="mr-map" viewBox="0 0 ${MAP_W} ${MAP_H}" role="img" aria-label="The path flown, ${esc(origin.icao)} to ${esc(destination.icao)}">
<path class="mr-land" d=""/>
${route.length > 1 ? `<polyline class="mr-plan" points="${route.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}"/>` : ""}
<polyline class="mr-ghost" points="${pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}"/>
<polyline class="mr-flown" points=""/>
${airport(origin, "mr-apt")}${airport(destination, "mr-apt mr-dest")}
<g class="mr-plane"><path d="M0 -9C.8 -9 1.3 -8 1.3 -7L1.3 -2.8L9.5 1L9.5 2.3L1.3 .3L1.3 3.6L3.4 5.1L3.4 6.1L.5 5.4L.5 7C.5 7.7 .2 8.2 0 8.4C-.2 8.2 -.5 7.7 -.5 7L-.5 5.4L-3.4 6.1L-3.4 5.1L-1.3 3.6L-1.3 .3L-9.5 2.3L-9.5 1L-1.3 -2.8L-1.3 -7C-1.3 -8 -.8 -9 0 -9Z"/></g>
</svg>
<svg class="mr-profile" viewBox="0 0 ${MAP_W} ${PROF_H}" preserveAspectRatio="none" aria-hidden="true">
<polyline class="mr-alt-line" points="${profile.join(" ")}"/>
<line class="mr-head-line" x1="0" x2="0" y1="0" y2="${PROF_H}"/>
</svg>
</div>
<ol class="mr-radio" aria-label="The radio"></ol>
</div>
</div>`);
    const q = (sel) => host.querySelector(sel);
    const flown = q(".mr-flown"), plane = q(".mr-plane"), list = q(".mr-radio"), headLine = q(".mr-head-line");
    const clockEl = q(".mr-clock"), phaseEl = q(".mr-phase"), altEl = q(".mr-alt"), gsEl = q(".mr-gs");

    // The coasts, once the land has loaded (the same file the card's dots come from).
    fetch(`${base}vendor/land-110m.json`).then((res) => res.json()).then((land) => {
      q(".mr-land").setAttribute("d", landPath(land.rings || [], f));
    }).catch(() => {});

    const items = radio.map((l) => {
      const li = document.createElement("li");
      li.className = `mr-line mr-${l.who}`;
      li.innerHTML = `<span class="mr-who">${esc(who(l))}<i>${clock(l.t)}</i></span><span class="mr-text">${esc(l.text)}</span>`;
      return li;
    });

    let shown = 0;
    const at = (t) => {  // index of the last track point at or before t
      let lo = 0, hi = track.length - 1;
      while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (track[mid][0] <= t) lo = mid; else hi = mid - 1; }
      return lo;
    };
    const draw = (t) => {
      const i = at(t), a = track[i], b = track[Math.min(i + 1, track.length - 1)];
      const k = b[0] > a[0] ? Math.min(1, (t - a[0]) / (b[0] - a[0])) : 0;
      const p = [pts[i][0] + (pts[Math.min(i + 1, pts.length - 1)][0] - pts[i][0]) * k, pts[i][1] + (pts[Math.min(i + 1, pts.length - 1)][1] - pts[i][1]) * k];
      flown.setAttribute("points", pts.slice(0, i + 1).map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ") + ` ${p[0].toFixed(1)},${p[1].toFixed(1)}`);
      // Heading: towards a point a little ahead on the path, so a turn reads as one.
      const ahead = pts[Math.min(pts.length - 1, i + 2)], behind = pts[Math.max(0, i - 1)];
      const deg = ahead[0] === behind[0] && ahead[1] === behind[1] ? 0 : Math.atan2(ahead[0] - behind[0], behind[1] - ahead[1]) * 180 / Math.PI;
      plane.setAttribute("transform", `translate(${p[0].toFixed(1)} ${p[1].toFixed(1)}) rotate(${deg.toFixed(0)})`);
      const alt = a[3] + (b[3] - a[3]) * k;
      altEl.textContent = fl(alt);
      gsEl.textContent = `${Math.round((a[4] || 0) + ((b[4] || 0) - (a[4] || 0)) * k)} kt`;
      clockEl.textContent = clock(t);
      const phase = (r.phases || []).filter((x) => x.t <= t).pop();
      phaseEl.textContent = phase ? phase.text : "";
      headLine.setAttribute("x1", (toU(t) * MAP_W).toFixed(1));
      headLine.setAttribute("x2", (toU(t) * MAP_W).toFixed(1));
      let added = false;
      while (shown < items.length && radio[shown].t <= t) {
        list.appendChild(items[shown]);
        shown++;
        added = true;
      }
      if (added) list.scrollTo({ top: list.scrollHeight, behavior: "smooth" });
    };
    const reset = () => { list.textContent = ""; shown = 0; };

    if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
      host.classList.add("mr-still");
      draw(duration);
      list.scrollTop = 0;
      return;
    }

    // Plays while it's on screen; loops, with a pause at the end.
    let start = null, visible = false, raf = 0, hold = 0;
    const HOLD = 4000;
    const tick = (now) => {
      raf = 0;
      if (!visible) return;
      if (start === null) start = now;
      const u = (now - start) / (seconds * 1000);
      if (u >= 1) {
        if (!hold) { draw(duration); hold = now; }
        if (now - hold > HOLD) { hold = 0; start = now; reset(); }
      } else {
        draw(flightTime(u));
      }
      raf = requestAnimationFrame(tick);
    };
    let pausedAt = null;
    new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      if (visible && !raf) {
        if (pausedAt !== null && start !== null) start += performance.now() - pausedAt;
        pausedAt = null;
        raf = requestAnimationFrame(tick);
      } else if (!visible) {
        pausedAt = performance.now();
      }
    }, { threshold: 0.25 }).observe(host.querySelector(".mini-replay"));
  }

  function boot() {
    const host = document.getElementById("share-more");
    const extra = readJson("share-extra");
    const card = readJson("share-data");
    if (!host || !extra || !card) return;
    const base = `${(document.getElementById("share") || {}).dataset?.site || ""}/`;
    host.insertAdjacentHTML("beforeend", details(extra.details, card));
    if (extra.replay) replay(host, extra.replay, card, base);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
