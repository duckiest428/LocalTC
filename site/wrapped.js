/* ATC Wrapped: a week, a month or a year of flying, told as slides (the dashboard and the desktop app).
 *
 * One file, two copies: site/wrapped.js and src/localtc/ui/static/wrapped.js (tests/test_site.py keeps them
 * identical). The account server works the recap out (GET /v1/wrapped); this picks the period in the
 * pilot's own time zone, turns each slide into a card (sharecard.js draws it, the same picture the "Save
 * image" button saves), and plays them like a story: tap or the arrow keys to move, a few seconds each.
 * A week is one card. Only the summary can become a public link.
 *
 * Only the last finished week, month or year can be seen (the server says the same). The one still going
 * is locked, with a countdown to when it ends and its recap unlocks.
 *
 *   new WrappedView(el, {base, load(range) -> recap, share(range) -> {slug, url, card}, putImage(slug, blob)})
 */
(function () {
  "use strict";

  const SLIDE_MS = 6500;
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (n) => Math.round(Number(n) || 0).toLocaleString("en-US");
  const hours = (h) => (Number(h) < 100 ? (Math.round(Number(h) * 10) / 10).toLocaleString("en-US") : num(h));
  const iso = (d) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
  const day = (s) => { const d = new Date(`${String(s).slice(0, 10)}T12:00:00Z`); return isNaN(d) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }); };
  const duration = (min) => { const m = Math.round(min || 0); return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`; };

  /** The period containing ``at`` (local time), moved by ``step`` periods: {period, from, to, tz, label}. */
  function periodOf(period, at, step = 0) {
    const d = new Date(at);
    let start, end, label;
    if (period === "week") {
      start = new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7) + 7 * step);
      end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 7);
      label = `Week of ${start.getDate()} ${MONTHS[start.getMonth()].slice(0, 3)} ${start.getFullYear()}`;
    } else if (period === "month") {
      start = new Date(d.getFullYear(), d.getMonth() + step, 1);
      end = new Date(start.getFullYear(), start.getMonth() + 1, 1);
      label = `${MONTHS[start.getMonth()]} ${start.getFullYear()}`;
    } else {
      start = new Date(d.getFullYear() + step, 0, 1);
      end = new Date(start.getFullYear() + 1, 0, 1);
      label = `${start.getFullYear()}`;
    }
    const current = Date.now() >= start.getTime() && Date.now() < end.getTime();
    return { period, from: iso(start), to: iso(end), tz: -start.getTimezoneOffset(), label, current, start };
  }

  /** "12d 04:13:22" */
  function countdown(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const clock = [h, m, sec].map((n) => String(n).padStart(2, "0")).join(":");
    return d ? `${d}d ${clock}` : clock;
  }

  /** A slide of the recap as a card for sharecard.js: {kind: "slide", kicker, value, unit, label, sub}. */
  function slideCard(s, recap) {
    // Slides without a map of their own get the globe, turned to where the pilot flew.
    const places = (recap.airports || []).filter((a) => a.lat != null && a.lon != null);
    const mid = places.length ? { lat: places.reduce((t, a) => t + a.lat, 0) / places.length, lon: places.reduce((t, a) => t + a.lon, 0) / places.length } : { lat: 30, lon: -40 };
    const base = { kind: "slide", name: s.kind, footer: `${recap.label}  ·  localtc.tech`, globe: { ...mid, airports: places.slice(0, 40) } };
    const route = (f) => (f ? `${f.origin} → ${f.destination}` : "");
    switch (s.kind) {
      case "intro": return { ...base, value: recap.label, small: true, label: `${s.flights} flights. Let's look back.`, kicker: "ATC WRAPPED" };
      case "totals": return { ...base, value: hours(s.hours), unit: "hours", label: "in the air", sub: `${s.flights} flights, ${s.landings} landings.${s.hours_framing ? ` ${s.hours_framing}.` : ""}` };
      case "distance": return { ...base, value: num(s.distance_nm), unit: "nm", label: "flown", sub: s.framing, accent: "cyan" };
      case "map": return { ...base, value: `${s.airports.length}`, unit: s.airports.length === 1 ? "airport" : "airports", label: `${s.routes.length} ${s.routes.length === 1 ? "route" : "routes"} between them`, map: s };
      case "busiest_month": { const [y, m] = s.month.split("-"); return { ...base, value: MONTHS[Number(m) - 1], small: true, label: "was your busiest month", sub: `${s.flights} flights in ${MONTHS[Number(m) - 1]} ${y}.` }; }
      case "top_airport": return { ...base, value: s.icao, label: s.name ? `${s.name}: your most visited airport` : "your most visited airport", sub: `${s.visits} visits.` };
      case "top_route": return { ...base, value: `${s.origin} → ${s.destination}`, small: true, label: "your most flown route", sub: `${s.flights} times.`, accent: "cyan" };
      case "new_airports": return { ...base, value: num(s.count), unit: s.count === 1 ? "new airport" : "new airports", label: "you'd never flown to before", sub: s.icaos.join("  ") };
      case "aircraft": return { ...base, value: s.aircraft, small: true, label: "your ride", sub: `${s.flights} flights in it${s.types > 1 ? `, of ${s.types} types flown` : ""}.` };
      case "longest": return { ...base, value: duration(s.air_min), label: "your longest flight", sub: `${route(s)}, ${num(s.distance_nm)} nm, ${day(s.date)}.`, accent: "cyan" };
      case "best_landing": return { ...base, value: `${s.fpm}`, unit: "fpm", label: "your softest landing", sub: `${route(s.flight)}, ${day(s.flight.date)}. ${s.greasers} of ${s.landings} landings softer than 150 fpm.` };
      case "streak": return { ...base, value: num(s.days), unit: "days", label: "flying in a row", sub: `${day(s.from)} to ${day(s.to)}.`, accent: "amber" };
      case "radio": {
        const trend = s.previous == null ? `${num(s.readbacks)} readbacks.` : s.accuracy >= s.previous ? `Up from ${Math.round(s.previous)}% the time before.` : `Down from ${Math.round(s.previous)}% the time before.`;
        return { ...base, value: `${Math.round(s.accuracy)}%`, label: "of your readbacks right", sub: trend, accent: "cyan" };
      }
      case "moment": return { ...base, kicker: `STANDOUT MOMENT: ${String(s.label || "").toUpperCase()}`, quote: s, sub: s.flight ? `${route(s.flight)}, ${day(s.flight.date)}` : "" };
      case "first_last": return { ...base, value: route(s.first), small: true, label: "is where it started", sub: `And it ended with ${route(s.last)}, ${day(s.last.date)}.` };
      case "persona": return { ...base, kicker: "YOUR PILOT TYPE", value: s.name, small: true, label: s.blurb, accent: "amber" };
      default: return null;
    }
  }

  class WrappedView {
    constructor(el, opts) {
      this.el = el;
      this.opts = opts;
      this.base = opts.base || "";
      this.period = "month";
      this.step = -1;  // the last finished one: the only one to look back on
      this.timer = null;
      this.countdown = null;
      el.classList.add("wrapped");
      el.innerHTML = `<div class="wr-bar">
  <div class="wr-tabs" role="tablist" aria-label="Period">
    <button type="button" role="tab" data-period="week">Week</button><button type="button" role="tab" data-period="month">Month</button><button type="button" role="tab" data-period="year">Year</button>
  </div>
  <div class="wr-nav"><button type="button" class="wr-prev" aria-label="The period before">‹</button><b class="wr-label"></b><button type="button" class="wr-next" aria-label="The period after">›</button></div>
</div>
<div class="wr-stage"></div>`;
      el.querySelector(".wr-tabs").onclick = (e) => { const b = e.target.closest("button[data-period]"); if (b) { this.period = b.dataset.period; this.step = -1; this.load(); } };
      el.querySelector(".wr-prev").onclick = () => { this.step = -1; this.load(); };
      el.querySelector(".wr-next").onclick = () => { this.step = 0; this.load(); };
      this.onKey = (e) => {
        if (!this.slides || !el.isConnected || el.closest("[hidden]") || /INPUT|TEXTAREA/.test(document.activeElement?.tagName || "")) return;
        if (e.key === "ArrowRight" || e.key === " ") { e.preventDefault(); this.show(this.at + 1); }
        else if (e.key === "ArrowLeft") { e.preventDefault(); this.show(this.at - 1); }
      };
      document.addEventListener("keydown", this.onKey);
    }

    /** Open a period: "month", -1 is last month (the one to see); 0 is this month (locked until it ends). */
    open(period = this.period, step = -1) {
      this.period = period;
      this.step = step === 0 ? 0 : -1;
      return this.load();
    }

    async load() {
      this.stop();
      const range = periodOf(this.period, Date.now(), this.step);
      this.range = range;
      this.el.querySelectorAll(".wr-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.period === this.period)));
      this.el.querySelector(".wr-label").textContent = range.label;
      this.el.querySelector(".wr-prev").disabled = this.step === -1;
      this.el.querySelector(".wr-next").disabled = this.step === 0;
      const stage = this.el.querySelector(".wr-stage");
      if (range.current) return this.locked(range);
      stage.innerHTML = `<p class="wr-hint">Looking back ...</p>`;
      let recap;
      try {
        recap = await this.opts.load(range);
      } catch (err) {
        stage.innerHTML = `<p class="wr-hint error">${esc(err.message)}</p>`;
        return;
      }
      if (range !== this.range) return; // another period was picked meanwhile
      this.recap = recap;
      if (!recap.flights) return this.empty(recap);
      this.slides = recap.slides.map((s) => (s.kind === "summary" ? { ...recap.card, name: "summary" } : slideCard(s, recap))).filter(Boolean);
      stage.innerHTML = `<div class="wr-progress">${this.slides.length > 1 ? this.slides.map(() => "<i><b></b></i>").join("") : ""}</div>
<div class="wr-slide share-card" tabindex="0" aria-live="polite"></div>
<div class="wr-actions">
  <button type="button" class="btn btn-ghost btn-sm wr-save">Save image</button>
  <button type="button" class="btn btn-primary btn-sm wr-share" hidden>Share my ${esc(this.period === "week" ? "week" : this.period)}</button>
  <span class="wr-link" hidden><input type="text" readonly aria-label="The link"><button type="button" class="btn btn-ghost btn-sm wr-copy">Copy</button></span>
  <span class="wr-status" role="status"></span>
</div>`;
      const slide = stage.querySelector(".wr-slide");
      slide.onclick = (e) => { const r = slide.getBoundingClientRect(); this.show(this.at + (e.clientX - r.left < r.width / 3 ? -1 : 1)); };
      slide.onmouseenter = () => this.stop();
      slide.onmouseleave = () => this.tick();
      stage.querySelector(".wr-save").onclick = async () => {
        const card = this.slides[this.at];
        window.ShareCard.download(await window.ShareCard.png(card, { base: this.base }), card);
      };
      stage.querySelector(".wr-share").onclick = () => this.share();
      stage.querySelector(".wr-copy").onclick = async () => {
        const input = stage.querySelector(".wr-link input");
        try { await navigator.clipboard.writeText(input.value); this.status("Link copied."); } catch (_) { input.select(); }
      };
      this.show(0);
    }

    /** The period still going: locked, counting down to when it ends. */
    locked(range) {
      const stage = this.el.querySelector(".wr-stage");
      const last = periodOf(this.period, Date.now(), -1);
      stage.innerHTML = `<div class="wr-empty wr-locked"><div class="wr-lock" aria-hidden="true">&#128274;</div>
<h3>${esc(range.label)} is still going</h3>
<p>Its Wrapped unlocks when it ends, in</p><p class="wr-countdown" role="timer" aria-live="off"></p>
<button type="button" class="btn btn-ghost btn-sm wr-back">See ${esc(last.label)}</button></div>`;
      stage.querySelector(".wr-back").onclick = () => { this.step = -1; this.load(); };
      const end = new Date(range.to).getTime();
      const tick = () => {
        const left = end - Date.now();
        if (left <= 0) { this.step = -1; this.load(); return; }  // it's over: that's the one to see now
        const out = stage.querySelector(".wr-countdown");
        if (out) out.textContent = countdown(left);
      };
      tick();
      this.countdown = setInterval(tick, 1000);
    }

    empty(recap) {
      const stage = this.el.querySelector(".wr-stage");
      const next = periodOf(this.period, Date.now(), 0);
      const last = recap.last_flight;
      const when = last && (last.days_ago === 0 ? "today" : last.days_ago === 1 ? "yesterday" : `${last.days_ago} days ago`);
      stage.innerHTML = `<div class="wr-empty"><h3>No flights in ${esc(this.range.label.replace(/^Week of/, "the week of"))}.</h3>
${last ? `<p>Your last flight was ${esc(when)}: ${esc(last.origin)} → ${esc(last.destination)}.</p>` : ""}
<p>Your ${esc(next.label.replace(/^Week of/, "week of"))} Wrapped unlocks in <b class="wr-countdown"></b>.</p></div>`;
      const end = new Date(next.to).getTime();
      const tick = () => { const out = stage.querySelector(".wr-countdown"); if (out) out.textContent = countdown(end - Date.now()); };
      tick();
      this.countdown = setInterval(tick, 1000);
    }

    async show(i) {
      if (!this.slides) return;
      this.at = Math.max(0, Math.min(this.slides.length - 1, i));
      const stage = this.el.querySelector(".wr-stage");
      stage.querySelectorAll(".wr-progress i").forEach((bar, j) => {
        bar.classList.toggle("done", j < this.at);
        bar.classList.toggle("now", j === this.at);
      });
      const card = this.slides[this.at];
      await window.ShareCard.mount(stage.querySelector(".wr-slide"), card, { base: this.base, animate: true });
      const summary = card.kind === "wrapped";
      stage.querySelector(".wr-share").hidden = !summary || !this.opts.share;
      this.tick();
    }

    tick() {
      this.stop();
      if (!this.slides || this.at >= this.slides.length - 1) return;
      const bar = this.el.querySelector(".wr-progress i.now b");
      if (bar) { bar.style.transition = "none"; bar.style.width = "0"; void bar.offsetWidth; bar.style.transition = `width ${SLIDE_MS}ms linear`; bar.style.width = "100%"; }
      this.timer = setTimeout(() => this.show(this.at + 1), SLIDE_MS);
    }

    stop() {
      clearTimeout(this.timer);
      this.timer = null;
      clearInterval(this.countdown);
      this.countdown = null;
      const bar = this.el.querySelector(".wr-progress i.now b");
      if (bar) { bar.style.transition = "none"; bar.style.width = "0"; }
    }

    status(text, error = false) {
      const s = this.el.querySelector(".wr-status");
      if (s) { s.textContent = text; s.classList.toggle("error", error); }
    }

    async share() {
      const button = this.el.querySelector(".wr-share");
      button.disabled = true;
      this.status("Sharing ...");
      try {
        const made = await this.opts.share(this.range);
        this.status("Drawing the card ...");
        await this.opts.putImage(made.slug, await window.ShareCard.png(made.card, { base: this.base }));
        const link = this.el.querySelector(".wr-link");
        link.hidden = false;
        link.querySelector("input").value = made.url;
        this.status("Shared: the summary card only, never a flight in detail.");
      } catch (err) {
        this.status(err.message || String(err), true);
      } finally {
        button.disabled = false;
      }
    }

    destroy() {
      this.stop();
      document.removeEventListener("keydown", this.onKey);
    }
  }

  WrappedView.periodOf = periodOf;
  WrappedView.slideCard = slideCard;
  window.WrappedView = WrappedView;
})();
