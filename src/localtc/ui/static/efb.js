/* The EFB tab (ui/efb_api.py): the briefing, the plan with its fuel and weights, the airport and its gates, the
   charts (ChartFox), the checklists, the scratchpad, Airport Ops, the documents, the achievements, and the data
   services' keys. The same as the companion's EFB; what changes in flight arrives as the "efb" event. */

const Efb = {
  section: "briefing", state: {}, briefIcao: "", airportIcao: "", chartIcao: "", gateMap: null, chartfoxPoll: null,
  SECTIONS: [["briefing", "Briefing"], ["plan", "Plan & fuel"], ["airport", "Airport & gates"], ["charts", "Charts"],
    ["checklists", "Checklists"], ["scratchpad", "Scratchpad"], ["ops", "Airport Ops"], ["docs", "Documents"],
    ["achievements", "Achievements"], ["data", "Data"]],

  init() {
    $("#efb-nav").innerHTML = this.SECTIONS.map(([id, label]) =>
      `<button class="btn small" data-efb="${id}" aria-pressed="${id === this.section}">${label}</button>`).join("");
    $$("#efb-nav [data-efb]").forEach((b) => (b.onclick = () => this.show(b.dataset.efb)));
  },
  show(section = this.section) {
    if (!$("#efb-nav").children.length) this.init();
    this.section = section;
    $$("#efb-nav [data-efb]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.efb === section)));
    const body = $("#efb-body");
    body.innerHTML = `<div class="muted pad">Loading ...</div>`;
    const render = this[`render_${section}`];
    Promise.resolve(render.call(this, body)).catch((e) => { body.innerHTML = `<div class="pad error">${esc(e.message || e)}</div>`; });
  },
  /** The "efb" event: the live parts, redrawn where they're showing. */
  update(state) {
    this.state = state || {};
    MapView.operations(this.state.atc);
    const n = this.state.achievements?.new || [];
    if (n.length && n[n.length - 1].id !== this.lastNew) { this.lastNew = n[n.length - 1].id; toast(`Achievement: ${n[n.length - 1].title}`); }
    if (!$("#page-efb").classList.contains("active")) return;
    if (["checklists", "scratchpad", "ops"].includes(this.section)) this.show(this.section);
  },
  airports() {
    const p = S.state?.plan || {};
    return [...new Set([p.origin, p.destination, p.alternate, S.flight?.origin, S.flight?.destination].filter(Boolean))];
  },
  picker(current, onPick) {
    const list = this.airports();
    return `<div class="row efbpick">${list.map((a) => `<button class="btn small" data-pick="${esc(a)}" aria-pressed="${a === current}">${esc(a)}</button>`).join("")}
      <input type="text" class="efbicao" maxlength="4" placeholder="ICAO" value="${list.includes(current) ? "" : esc(current || "")}"><button class="btn small" data-go>Show</button></div>`;
  },
  bindPicker(root, onPick) {
    $$("[data-pick]", root).forEach((b) => (b.onclick = () => onPick(b.dataset.pick)));
    const input = $(".efbicao", root);
    const go = () => input.value.trim() && onPick(input.value.trim().toUpperCase());
    $("[data-go]", root).onclick = go;
    input.onkeydown = (e) => { if (e.key === "Enter") go(); };
  },
  age(meta) {
    if (!meta) return "";
    if (!meta.available) return `<span class="muted">unavailable${meta.why ? `: ${esc(meta.why)}` : ""}</span>`;
    const mins = meta.got ? Math.round((Date.now() / 1000 - meta.got) / 60) : null;
    return `<span class="muted">${esc(meta.source || "")}${mins != null ? `, ${mins} min old` : ""}${meta.stale ? " (stale)" : ""}</span>`;
  },

  /* --- briefing --- */
  async render_briefing(body) {
    const icao = this.briefIcao || this.airports()[0] || "";
    if (!icao) { body.innerHTML = `${this.picker("", null)}<div class="muted pad">Load a flight plan, or type an airport.</div>`; this.bindPicker(body, (a) => { this.briefIcao = a; this.show(); }); return; }
    const b = await api(`efb/briefing?icao=${encodeURIComponent(icao)}`);
    const notams = b.notams?.items || [];
    body.innerHTML = `${this.picker(icao)}
      <div class="efbgrid">
        <div class="panel efbcard"><h3>METAR</h3><pre class="raw">${esc(b.metar || "Not known yet (fetched once the flight starts)")}</pre></div>
        <div class="panel efbcard"><h3>TAF ${this.age(b.taf)}</h3><pre class="raw">${esc(b.taf?.raw || "None")}</pre></div>
        ${b.atis ? `<div class="panel efbcard"><h3>ATIS ${esc(b.atis.letter)} <span class="muted">${esc(b.atis.source)}</span></h3>
          <div>Runway ${esc(b.atis.runway)} · ${esc(b.atis.approach)} approach</div>
          ${(b.atis.notices || []).map((n) => `<div class="small">• ${esc(n)}</div>`).join("")}</div>` : ""}
        <div class="panel efbcard wide"><h3>NOTAMs (${notams.length}) ${this.age(b.notams)}</h3>
          ${notams.length ? notams.map((n) => `<div class="notam ${n.works_to?.length ? "atc" : ""} ${n.in_force ? "" : "later"}">
            <b>${esc(n.id)}</b> ${n.works_to?.length ? `<span class="tag">ATC works to it</span>` : ""}${n.in_force ? "" : ` <span class="tag">not yet</span>`}
            <div class="small">${esc(n.text)}</div></div>`).join("") : `<div class="muted">${esc(b.notams?.why || "None in force")}</div>`}</div>
      </div>`;
    this.bindPicker(body, (a) => { this.briefIcao = a; this.show(); });
  },

  /* --- plan, fuel and weights, alternates --- */
  async render_plan(body) {
    const p = S.state?.plan;
    const form = (k, label, v, w = "") => `<label class="efbfield ${w}"><span>${label}</span><input data-plan="${k}" value="${esc(v ?? "")}"></label>`;
    body.innerHTML = `<div class="panel efbcard"><h3>Flight plan</h3>
        <div class="efbform">${form("callsign", "Callsign", p?.callsign)}${form("origin", "From", p?.origin)}${form("destination", "To", p?.destination)}
        ${form("alternate", "Alternate", p?.alternate)}${form("cruise", "Cruise", p?.cruise_ft ? (p.cruise_ft >= 18000 ? `FL${p.cruise_ft / 100}` : p.cruise_ft) : "")}
        ${form("aircraft", "Type", p?.aircraft)}${form("route", "Route", p?.route, "wide")}</div>
        <div class="row"><button class="btn small primary" id="efb-save">Save plan</button>
        <button class="btn small" id="efb-simbrief">Import SimBrief…</button>
        <button class="btn small" id="efb-sbout" ${p ? "" : "disabled"}>Open in SimBrief</button>
        <span class="muted small">${p ? esc(`${p.source === "simbrief" ? "From SimBrief" : "Typed"} · ${p.fixes?.length || 0} fixes`) : "No plan yet"}</span></div></div>
      <div class="panel efbcard"><h3>Fuel &amp; weights</h3>
        <div class="row"><label class="efbfield"><span>Passengers</span><input id="efb-pax" type="number" min="0"></label>
        <label class="efbfield"><span>Cargo kg</span><input id="efb-cargo" type="number" min="0" value="0"></label>
        <label class="efbfield"><span>Extra fuel kg</span><input id="efb-extra" type="number" min="0" value="0"></label>
        <button class="btn small" id="efb-calc">Work out</button></div><div id="efb-perf" class="muted">…</div></div>
      <div class="panel efbcard"><h3>Alternates near ${esc(p?.destination || "the destination")}</h3><div id="efb-alts" class="muted">…</div></div>`;
    $("#efb-save").onclick = async () => {
      const v = Object.fromEntries($$("[data-plan]", body).map((i) => [i.dataset.plan, i.value.trim()]));
      try { const r = await api("flight/plan", v); S.state.plan = r.plan; toast(`Plan saved: ${r.summary}`); this.show(); } catch (e) { fail(e); }
    };
    $("#efb-simbrief").onclick = () => $("#btn-newflight")?.click();
    $("#efb-sbout").onclick = async () => { try { const r = await api("efb/simbrief_link"); api("open", { what: "url", url: r.url }); } catch (e) { fail(e); } };
    const perf = async () => {
      const q = new URLSearchParams({ pax: $("#efb-pax").value, cargo_kg: $("#efb-cargo").value, extra_kg: $("#efb-extra").value });
      try {
        const r = await api(`efb/perf?${q}`);
        if (!r.known) { $("#efb-perf").textContent = r.why; return; }
        if (!$("#efb-pax").value) $("#efb-pax").value = r.weights.payload.pax;
        const kg = (x) => `${x.kg.toLocaleString()} kg <span class="muted">(${x.lb.toLocaleString()} lb)</span>`;
        const lim = (x, name) => `<tr class="${x.ok ? "" : "bad"}"><td>${name}</td><td>${kg(x)}</td><td class="muted">max ${x.limit_kg.toLocaleString()}</td></tr>`;
        const f = r.fuel, w = r.weights;
        $("#efb-perf").innerHTML = `<div class="small muted">${esc(r.name)} · ${r.distance_nm} nm · ${esc(r.note)}</div>
          <table class="efbtable"><tr><td>Trip</td><td>${kg(f.trip)}</td><td class="muted">${f.trip_min ? `${f.trip_min} min` : ""}</td></tr>
          <tr><td>Contingency</td><td>${kg(f.contingency)}</td><td></td></tr><tr><td>Alternate</td><td>${kg(f.alternate)}</td><td></td></tr>
          <tr><td>Final reserve</td><td>${kg(f.final_reserve)}</td><td></td></tr><tr><td>Taxi</td><td>${kg(f.taxi)}</td><td></td></tr>
          <tr><td>Extra</td><td>${kg(f.extra)}</td><td></td></tr><tr class="total"><td>Block</td><td>${kg(f.block)}</td><td></td></tr></table>
          <table class="efbtable"><tr><td>Payload</td><td>${kg(w.payload)}</td><td class="muted">${w.payload.pax}/${w.payload.seats} seats</td></tr>
          ${lim(w.zfw, "Zero fuel")}${lim(w.tow, "Takeoff")}${lim(w.lw, "Landing")}${lim(w.fuel, "Fuel")}</table>
          ${w.ok ? "" : `<div class="error">Over a limit: take payload or fuel off.</div>`}`;
      } catch (e) { $("#efb-perf").textContent = e.message; }
    };
    $("#efb-calc").onclick = perf;
    perf();
    if (p?.destination) api(`efb/alternates?icao=${encodeURIComponent(p.destination)}`).then((r) => {
      $("#efb-alts").innerHTML = r.alternates.length ? `<table class="efbtable">${r.alternates.map((a) =>
        `<tr><td><b>${esc(a.icao)}</b> ${esc(a.name)}</td><td>${a.nm} nm</td><td class="muted">${(a.longest_ft || 0).toLocaleString()} ft</td></tr>`).join("")}</table>
        <div class="muted small">Runways of ${r.min_runway_ft.toLocaleString()} ft or more, among the airports LocalTC knows.</div>`
        : `<span class="muted">None known yet with a long enough runway.</span>`;
    }).catch((e) => ($("#efb-alts").textContent = e.message));
  },

  /* --- the airport, its runways and gates --- */
  async render_airport(body) {
    const icao = this.airportIcao || S.flight?.destination || this.airports()[0] || "";
    if (!icao) { body.innerHTML = `${this.picker("")}<div class="muted pad">Pick an airport.</div>`; this.bindPicker(body, (a) => { this.airportIcao = a; this.show(); }); return; }
    const a = await api(`efb/airport?icao=${encodeURIComponent(icao)}`);
    const procs = Object.entries(a.procedures || {}).map(([k, v]) => `${k.toUpperCase()} ${esc(v)}`).join(" · ");
    body.innerHTML = `${this.picker(icao)}
      <div class="efbgrid"><div class="panel efbcard"><h3>${esc(a.icao)} ${esc(a.name || "")}</h3>
        <div class="muted small">Elevation ${a.elev_ft} ft${procs ? ` · ${procs}` : ""}</div>
        <table class="efbtable">${(a.runways || []).map((r) => `<tr><td><b>${esc(r.name)}</b></td><td>${r.length_ft.toLocaleString()} × ${r.width_ft} ft</td>
          <td>${String(r.heading_mag).padStart(3, "0")}°</td><td class="small">${esc((r.ils || []).join(", "))}</td>
          <td class="small muted">${esc(Object.entries(r.approaches || {}).map(([e, l]) => `${e}: ${l.join(", ")}`).join("; "))}</td></tr>`).join("")}</table></div>
        <div class="panel efbcard"><h3>Frequencies</h3>${(a.all_frequencies || []).map((f) => `<div class="small">${esc(f.label)} <b>${mhz(f.mhz)}</b> <span class="muted">${esc(f.name || "")}</span></div>`).join("")}</div>
        <div class="panel efbcard wide"><h3>Gates</h3>${a.gates ? `<div class="row small"><span class="gk free">free</span><span class="gk taken">occupied</span><span class="gk mine">yours</span><span class="gk small">too small</span>
          <span class="muted">Click a gate to ask Airport Ops for it (the arrival only; ATC sends you there).</span></div><div id="efb-gatemap" class="gatemap"></div>`
          : `<div class="muted">Start a flight to see the gates.</div>`}</div></div>`;
    this.bindPicker(body, (x) => { this.airportIcao = x; this.show(); });
    if (a.gates) this.drawGates(a.gates);
  },
  drawGates(g) {
    if (this.gateMap) { this.gateMap.remove(); this.gateMap = null; }
    const stands = g.stands || [];
    if (!stands.length) { $("#efb-gatemap").innerHTML = `<div class="muted">No stands in the sim's data.</div>`; return; }
    const map = L.map("efb-gatemap", { zoomControl: true, attributionControl: false });
    this.gateMap = map;
    if (S.state.map_tiles !== false) L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
    const pts = [];
    for (const s of stands) {
      const mine = s.index === g.assigned;
      const color = mine ? "#8a6cf0" : s.occupied || s.taken ? "#e2574c" : !s.fits ? "#7d858e" : "#2fb67c";
      pts.push([s.lat, s.lon]);
      L.circleMarker([s.lat, s.lon], { radius: mine ? 7 : 5, color: "#000", weight: 1, fillColor: color, fillOpacity: 0.95 }).addTo(map)
        .bindTooltip(`<b>${esc(s.name)}</b>${s.airline ? ` · ${esc(s.airline)}` : ""}${s.international ? " · international" : ""}<br>${mine ? "yours" : s.occupied ? "occupied" : s.fits ? "free" : "too small for your aircraft"}`)
        .on("click", () => this.requestGate(s));
    }
    map.fitBounds(pts, { padding: [20, 20] });
  },
  async requestGate(s) {
    if (!confirm(`Ask Airport Ops for ${s.name}?`)) return;
    try { const r = await api("efb/gate", { gate: s.label }); toast(r.why, !r.ok); this.show("airport"); } catch (e) { fail(e); }
  },

  /* --- charts (ChartFox) --- */
  async render_charts(body) {
    const icao = this.chartIcao || this.airports()[0] || "";
    const credit = (c) => `<div class="small muted chartcredit"><a href="${esc(c.url)}" data-ext>${esc(c.text)}</a> · <a href="${esc(c.privacy)}" data-ext>ChartFox privacy</a> · ${esc(c.disclaimer)}</div>`;
    if (!icao) { body.innerHTML = `${this.picker("")}<div class="muted pad">Pick an airport.</div>`; this.bindPicker(body, (a) => { this.chartIcao = a; this.show(); }); return; }
    const r = await api(`efb/charts?icao=${encodeURIComponent(icao)}`);
    if (!r.connected) {
      body.innerHTML = `<div class="panel efbcard"><h3>Charts from ChartFox</h3>
        <p>Sign in with your free ChartFox account to see the charts for every airport here, the right one first for each phase of the flight, with your position on the georeferenced ones.</p>
        <button class="btn primary" id="cf-connect">Connect ChartFox</button> <span class="muted small" id="cf-wait"></span>${credit(r.credit)}</div>`;
      $("#cf-connect").onclick = () => this.connectChartFox();
      this.bindExt(body);
      return;
    }
    const groups = Object.entries(r.groups || {}).filter(([, l]) => l.length);
    body.innerHTML = `${this.picker(icao)}<div class="chartsplit"><div class="panel chartlist">${groups.map(([kind, list]) =>
      `<div class="chartgroup"><h4>${esc(kind)}</h4>${list.map((c) => `<div class="chartrow ${r.first.includes(c.id) ? "first" : ""}" data-chart="${esc(c.id)}">${esc(c.name)}${c.has_georef ? ` <span class="tag">GEO</span>` : ""}</div>`).join("")}</div>`).join("")
      || `<div class="muted pad">No charts for ${esc(icao)}.</div>`}
      <div class="row"><button class="btn small" id="cf-off">Disconnect ChartFox</button></div></div>
      <div class="panel chartview" id="chartview"><div class="muted pad">Pick a chart.</div></div></div>${credit(r.credit)}`;
    this.bindPicker(body, (a) => { this.chartIcao = a; this.show(); });
    this.bindExt(body);
    $$("[data-chart]", body).forEach((el) => (el.onclick = () => this.viewChart(el.dataset.chart)));
    $("#cf-off").onclick = async () => { await api("efb/chartfox/disconnect", {}); this.show(); };
    if (r.first[0]) this.viewChart(r.first[0]);
  },
  async viewChart(id) {
    const view = $("#chartview");
    view.innerHTML = `<div class="muted pad">Loading ...</div>`;
    const height = Math.max(400, view.clientHeight || 700);
    try {
      const c = await api(`efb/chart?id=${encodeURIComponent(id)}&height=${height}`);
      const isPdf = /\.pdf(\?|$)/i.test(c.view_url);
      view.innerHTML = `<div class="charthead"><b>${esc(c.name)}</b> <span class="muted">${esc(c.code || "")}</span></div>
        <div class="chartimg" style="height:${height}px">${isPdf ? `<iframe src="${esc(c.view_url)}" title="${esc(c.name)}"></iframe>`
          : `<img src="${esc(c.view_url)}" alt="${esc(c.name)}" style="height:${height}px">`}
        ${c.own ? `<div class="chartown" style="left:${c.own[0]}px;top:${c.own[1]}px" title="You are here">✈</div>` : ""}</div>`;
    } catch (e) { view.innerHTML = `<div class="pad error">${esc(e.message)}</div>`; }
  },
  async connectChartFox() {
    try {
      const r = await api("efb/chartfox/connect", {});
      api("open", { what: "url", url: r.url });
      $("#cf-wait").textContent = "Sign in with ChartFox in your browser ...";
      clearInterval(this.chartfoxPoll);
      let tries = 0;
      this.chartfoxPoll = setInterval(async () => {
        if (++tries > 150) { clearInterval(this.chartfoxPoll); $("#cf-wait").textContent = "Timed out: try again."; return; }
        try { const c = await api("efb/chartfox/collect", {}); if (c.connected) { clearInterval(this.chartfoxPoll); toast("ChartFox connected"); this.show("charts"); } } catch (e) { clearInterval(this.chartfoxPoll); fail(e); }
      }, 2000);
    } catch (e) { fail(e); }
  },
  bindExt(root) {
    $$("[data-ext]", root).forEach((a) => (a.onclick = (e) => { e.preventDefault(); api("open", { what: "url", url: a.href }); }));
  },

  /* --- checklists --- */
  async render_checklists(body) {
    const r = await api("efb/checklists");
    const cur = r.current;
    const ICON = { ok: "✔", wrong: "✖", read: "•", skipped: "↷", "": "" };
    body.innerHTML = `${!r.copilot ? `<div class="pad muted">${esc(r.why)}</div>` : ""}
      ${cur && cur.lines ? `<div class="panel efbcard"><h3>${esc(cur.name)} checklist <span class="tag">${esc(cur.status)}</span></h3>
        <div class="muted small">${esc(cur.source || "")} · read ${esc(r.mode || "")}</div>
        ${cur.lines.map((l, i) => `<div class="clline ${l.result} ${i === cur.index && !["completed", "stopped"].includes(cur.status) ? "now" : ""}">
          <span class="clmark">${ICON[l.result] ?? ""}</span><span class="clch">${esc(l.challenge)}</span><span class="clre">${esc(l.response)}</span>
          ${l.why ? `<span class="muted small">${esc(l.why)}</span>` : ""}</div>`).join("")}
        <div class="row"><button class="btn small primary" data-cl="check">Checked</button><button class="btn small" data-cl="skip">Skip</button>
        <button class="btn small" data-cl="pause">Hold</button><button class="btn small" data-cl="resume">Resume</button><button class="btn small" data-cl="stop">Stop</button></div></div>` : ""}
      <div class="panel efbcard"><h3>${r.book ? esc(r.book.name) : "Checklists"}</h3>
        ${r.book ? `<div class="muted small">${esc(r.book.source)}</div>${r.book.checklists.map((c) =>
          `<div class="clrow"><button class="btn small" data-run="${esc(c.id)}">Run</button> <b>${esc(c.name)}</b> <span class="muted small">${c.lines.length} items · ${esc(c.phase.replace(/_/g, " "))}</span></div>`).join("")}`
        : `<div class="muted">${r.copilot ? "No checklists for this aircraft: the copilot doesn't make any up." : "Start a flight with the copilot on."}</div>`}</div>`;
    $$("[data-cl]", body).forEach((b) => (b.onclick = () => api("efb/checklist", { action: b.dataset.cl }).catch(fail)));
    $$("[data-run]", body).forEach((b) => (b.onclick = () => api("efb/checklist", { action: "run", id: b.dataset.run }).catch(fail)));
  },

  /* --- scratchpad --- */
  async render_scratchpad(body) {
    const r = await api("efb/scratchpad");
    const KINDS = ["note", "clearance", "atis", "frequency", "squawk", "weather", "time"];
    const notes = r.notes.slice().sort((a, b) => (b.pinned - a.pinned) || (b.t - a.t));
    body.innerHTML = `<div class="panel efbcard"><h3>Scratchpad</h3>
        <textarea id="sp-text" rows="3" placeholder="Write a note: a clearance, the ATIS, a frequency ... (also on the phone)"></textarea>
        <div class="row"><select id="sp-kind"><option value="">auto</option>${KINDS.map((k) => `<option>${k}</option>`).join("")}</select>
        <label class="small"><input type="checkbox" id="sp-pin"> pin</label><button class="btn small primary" id="sp-add">Add</button></div></div>
      ${notes.map((n) => `<div class="panel spnote ${n.pinned ? "pinned" : ""}"><div class="sphead"><span class="tag">${esc(n.kind)}</span>
        <span class="muted small">${new Date(n.t * 1000).toLocaleTimeString()} · ${esc((n.phase || "").replace(/_/g, " ").toLowerCase())} · ${esc(n.source)}</span>
        <button class="btn small" data-pin="${n.id}">${n.pinned ? "Unpin" : "Pin"}</button><button class="btn small" data-del="${n.id}">Delete</button></div>
        <div class="sptext">${esc(n.text)}</div>
        ${Object.keys(n.picked || {}).length ? `<div class="sppicked">${Object.entries(n.picked).map(([k, v]) =>
          `<span class="tag ${n.checked?.[k] || ""}" title="${esc(n.checked?.[k] === "differs" ? "Not what ATC gave" : n.checked?.[k] === "same" ? "As ATC gave it" : "Your note: ATC hasn't given this")}">${esc(k)} ${esc(v)}</span>`).join("")}
          <span class="muted small">your note, not the sim's</span></div>` : ""}</div>`).join("")}`;
    $("#sp-add").onclick = async () => {
      const text = $("#sp-text").value.trim();
      if (!text) return;
      try { await api("efb/scratchpad", { op: "add", text, kind: $("#sp-kind").value, pinned: $("#sp-pin").checked }); this.show(); } catch (e) { fail(e); }
    };
    $$("[data-pin]", body).forEach((b) => (b.onclick = () => api("efb/scratchpad", { op: "edit", id: b.dataset.pin, pinned: b.textContent === "Pin" }).then(() => this.show()).catch(fail)));
    $$("[data-del]", body).forEach((b) => (b.onclick = () => api("efb/scratchpad", { op: "delete", id: b.dataset.del }).then(() => this.show()).catch(fail)));
  },

  /* --- Airport Ops, and ATC's conditions and hold --- */
  render_ops(body) {
    const o = this.state.ops, atc = this.state.atc;
    const svc = o ? Object.entries(o.services || {}) : [];
    const conds = (atc?.conditions || []).filter((c) => c.state === "active");
    const hold = atc?.hold && ["cleared", "entering", "holding", "leaving"].includes(atc.hold.state) ? atc.hold : null;
    body.innerHTML = `<div class="efbgrid">
      <div class="panel efbcard"><h3>Airport Ops ${o?.icao ? `· ${esc(o.icao)}` : ""}</h3>${o ? `
        ${svc.map(([k, v]) => `<div class="opsrow"><span>${esc(k)}</span><span class="tag ${v.state.replace(/ /g, "-")}">${esc(v.state)}</span><span class="muted small">${esc(v.source || "")}</span></div>`).join("")}
        ${o.gsx ? "" : `<div class="muted small">GSX isn't running: boarding, catering and cleaning aren't tracked (the fuel is, from the sim).</div>`}
        ${o.deice?.required ? `<div class="opsrow deice">De-icing: number ${o.deice.position}, about ${o.deice.wait_min} min (${esc(o.deice.why)}) <span class="tag">${esc(o.deice.state)}</span></div>` : ""}
        ${(o.closures || []).map((c) => `<div class="small">⚠ ${esc(c)}</div>`).join("")}` : `<div class="muted">Start a flight.</div>`}</div>
      <div class="panel efbcard"><h3>Messages</h3>${(o?.messages || []).slice().reverse().map((m) =>
        `<div class="opsmsg"><span class="muted small">${new Date(m.t * 1000).toLocaleTimeString()}</span> ${esc(m.text)}</div>`).join("") || `<div class="muted">None yet.</div>`}
        <div class="row"><select id="ops-kind"><option>maintenance</option><option>dispatch</option><option>other</option></select>
        <input id="ops-note" type="text" maxlength="500" placeholder="A note to Ops"><button class="btn small" id="ops-send">Send</button></div></div>
      <div class="panel efbcard"><h3>ATC</h3>
        ${hold ? `<div class="opsrow"><b>Holding at ${esc(hold.fix)}</b> · ${hold.reason === "arrival traffic" ? `number ${hold.position} · ` : ""}${esc(hold.reason)}${hold.efc ? ` · EFC ${esc(hold.efc)}Z` : ""} · ${esc(hold.state)}${hold.laps ? ` · ${hold.laps} laps` : ""}</div>` : ""}
        ${conds.map((c) => `<div class="opsrow"><span class="tag">${esc(c.kind.replace(/_/g, " "))}</span> ${esc(c.traffic || "")} ${c.runway ? `runway ${esc(c.runway)}` : ""}</div>`).join("")}
        ${(atc?.routing || []).filter((a) => a.state === "active").map((a) => `<div class="opsrow"><span class="tag">${esc(a.kind)}</span> ${esc(a.fix || "")} ${esc(a.reason || "")}</div>`).join("")}
        ${!hold && !conds.length ? `<div class="muted">No conditions, holds or reroutes.</div>` : ""}
        ${atc?.minimum_altitude_ft ? `<div class="small muted">Minimum altitude here: ${Number(atc.minimum_altitude_ft).toLocaleString()} ft</div>` : ""}</div></div>`;
    const send = $("#ops-send");
    if (send) send.onclick = async () => {
      const text = $("#ops-note").value.trim();
      if (!text) return;
      try { await api("efb/ops_note", { text, kind: $("#ops-kind").value }); $("#ops-note").value = ""; } catch (e) { fail(e); }
    };
  },

  /* --- documents --- */
  async render_docs(body) {
    const r = await api("efb/docs");
    body.innerHTML = `<div class="panel efbcard"><h3>Documents</h3>
      <div class="muted small">PDFs, pictures and text files in ${esc(r.folder || "the EFB's documents folder")}.</div>
      ${r.docs.map((d) => `<div class="docrow"><a href="/api/efb/doc?name=${encodeURIComponent(d.name)}" target="_blank">${esc(d.name)}</a>
        <span class="muted small">${(d.size / 1024).toFixed(0)} KB</span></div>`).join("") || `<div class="muted">None yet.</div>`}
      <div class="row"><button class="btn small" id="docs-open" ${r.folder ? "" : "disabled"}>Open the folder</button></div></div>`;
    $("#docs-open").onclick = () => api("efb/docs/open", {}).catch(fail);
  },

  /* --- achievements --- */
  async render_achievements(body) {
    const r = await api("efb/achievements");
    const best = r.bests?.smoothest_landing;
    body.innerHTML = `<div class="panel efbcard"><h3>Achievements · ${r.earned}/${r.total}</h3>
      ${best ? `<div class="small">Smoothest landing: <b>${best.fpm} fpm</b> at ${esc(best.airport)}</div>` : ""}
      <div class="achgrid">${r.achievements.map((a) => `<div class="ach ${a.earned ? "got" : ""}"><b>${esc(a.title)}</b>
        <div class="small">${esc(a.description)}</div>
        ${a.earned ? `<div class="small muted">${esc(a.detail || "")} · ${new Date(a.at * 1000).toLocaleDateString()}</div>`
          : a.progress ? `<div class="bar"><i style="width:${(100 * a.progress[0]) / a.progress[1]}%"></i></div><div class="small muted">${a.progress[0]}/${a.progress[1]}</div>` : ""}</div>`).join("")}</div>
      <div class="muted small">A flight that breaks a rule (an incursion, a takeoff or landing without a clearance, a low altitude alert) earns no flight-quality achievements.</div></div>`;
  },

  /* --- the data services' keys --- */
  async render_data(body) {
    const r = await api("data/keys");
    body.innerHTML = `<div class="panel efbcard"><h3>Real-world data</h3>
      <p class="small muted">Each is a free account of your own. The keys are kept in Windows' credential store, never in a file; leave a box empty to keep the key you have.</p>
      ${r.services.map((s) => `<div class="keyrow"><b>${esc(s.name)}</b> ${s.has_key ? `<span class="tag ok">set</span>` : `<span class="tag">not set</span>`}
        <div class="small muted">${esc(s.what)} · <a href="${esc(s.signup)}" data-ext>get one</a></div>
        ${s.fields.map((f) => `<input type="password" data-key="${esc(f)}" placeholder="${esc(f.replace(/_/g, " "))}" autocomplete="off">`).join("")}</div>`).join("")}
      <div class="row"><button class="btn small primary" id="keys-save">Save keys</button></div></div>`;
    this.bindExt(body);
    $("#keys-save").onclick = async () => {
      const keys = Object.fromEntries($$("[data-key]", body).filter((i) => i.value.trim()).map((i) => [i.dataset.key, i.value.trim()]));
      try { await api("data/keys", { keys }); toast("Keys saved"); this.show(); } catch (e) { fail(e); }
    };
  },
};
