/* The dashboard's Admin page (dashboard.html #admin): only shown to the addresses in the server's ADMINS, and every call
   checked there again (server/src/admin.ts). An overview, Live Flights (pause it, take a flight down, turn an account's
   sharing off), and the accounts: search, tags ("Streamer", "Contributor", shown on their Live Flights card), sign out
   everywhere, delete. What the server sends goes on the page as text (textContent), never as HTML. */
const Admin = (() => {
  "use strict";
  const COLORS = ["green", "blue", "purple", "amber", "red", "pink", "teal", "gray"];
  const el = (tag, props = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else if (k.startsWith("on")) e[k] = v;
      else e.setAttribute(k, v);
    }
    e.append(...kids.filter((k) => k !== null && k !== undefined && k !== false));
    return e;
  };
  const day = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" }) : "–");
  const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "–");
  const ago = (ms) => { const s = Math.round((Date.now() - ms) / 1000); return s < 90 ? `${s} s ago` : `${Math.round(s / 60)} min ago`; };
  const st = { q: "", offset: 0, open: null, sort: "joined", dir: "desc" };
  // The columns the accounts sort by: their name in the header, and which way a first click sorts (A to Z, or most first).
  const SORT = { email: ["Email", "asc"], name: ["Name", "asc"], tags: ["Tags", "desc"], flights: ["Flights", "desc"],
    live: ["Live Flights", "desc"], discord: ["Discord", "desc"], joined: ["Joined", "desc"], last_seen: ["Last seen", "desc"] };
  const ORDER = { email: ["A to Z", "Z to A"], name: ["A to Z", "Z to A"], tags: ["fewest first", "most first"],
    flights: ["fewest first", "most first"], live: ["off first", "sharing first"], discord: ["off first", "on first"],
    joined: ["oldest first", "newest first"], last_seen: ["longest ago first", "most recent first"] };

  function say(text, error = false) {
    const m = $("#adm-msg");
    m.textContent = text || "";
    m.classList.toggle("error", error);
  }

  async function overview() {
    const o = await api("GET", "/v1/admin/overview");
    const tiles = [["Accounts", o.users], ["Confirmed", o.verified], ["New this week", o.new_week], ["Active today", o.active_day],
      ["Flights synced", o.flights], ["Replays", o.replays], ["Shared pages", o.shares], ["Blog followers", o.followers],
      ["Sharing on", o.sharing], ["On Live Flights now", o.live]];
    $("#adm-tiles").replaceChildren(...tiles.map(([label, n]) =>
      el("div", { class: "adm-tile" }, el("b", { text: Number(n ?? 0).toLocaleString() }), el("span", { text: label }))));
  }

  async function live() {
    const v = await api("GET", "/v1/admin/live");
    const toggle = $("#adm-pause");
    toggle.textContent = v.paused ? "Turn Live Flights back on" : "Pause Live Flights for everyone";
    toggle.className = v.paused ? "btn btn-primary" : "btn btn-danger";
    toggle.disabled = v.locked;
    $("#adm-live-state").textContent = v.locked ? "Paused in wrangler.toml (LIVE_SKY = \"off\"): change it there."
      : v.paused ? "Paused: no flight is taken, and the map is empty." : `On: ${v.flights.length} flight${v.flights.length === 1 ? "" : "s"} on the map.`;
    toggle.onclick = async () => {
      if (!v.paused && !confirm("Pause Live Flights for everyone? Every flight leaves the map, and none is taken until you turn it back on.")) return;
      try { await api("PUT", "/v1/admin/live", { paused: !v.paused }); await Promise.all([live(), overview()]); } catch (e) { say(e.message, true); }
    };
    const rows = v.flights.map((f) => el("tr", {},
      el("td", { class: "mono", text: f.callsign }), el("td", { text: [f.from, f.to].filter(Boolean).join(" → ") || "–" }),
      el("td", {}, el("a", { href: "#admin", text: f.email || "(deleted)", onclick: (e) => { e.preventDefault(); openUser(f.user_id); } })),
      el("td", { text: ago(f.started) }), el("td", { text: ago(f.last) }),
      el("td", { class: "adm-acts" },
        el("button", { class: "btn btn-ghost btn-sm", type: "button", text: "Take off the map", onclick: () => takeDown(f) }),
        el("button", { class: "btn btn-danger btn-sm", type: "button", text: "Turn off sharing", onclick: () => setBanned(f.user_id, true) }))));
    $("#adm-live-rows").replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: "6", class: "hint", text: "No one is sharing right now." }))]));
  }

  async function takeDown(f) {
    if (!confirm(`Take ${f.callsign} off the map? It comes back with its next update unless its sharing is turned off.`)) return;
    try { await api("DELETE", `/v1/sky/flights/${f.id}`); await live(); } catch (e) { say(e.message, true); }
  }

  async function users() {
    const v = await api("GET", `/v1/admin/users?q=${encodeURIComponent(st.q)}&offset=${st.offset}&sort=${st.sort}&dir=${st.dir}`);
    const rows = v.users.map((u) => el("tr", { class: "adm-row", tabindex: "0", onclick: () => openUser(u.id),
      onkeydown: (e) => { if (e.key === "Enter") openUser(u.id); } },
      el("td", {}, el("span", { text: u.email }), u.verified ? null : el("span", { class: "adm-flag", text: "unconfirmed" })),
      el("td", { text: u.display_name || "–" }),
      el("td", {}, ...u.tags.map((t) => el("span", { class: `lf-chip tag tag-${COLORS.includes(t.color) ? t.color : "gray"}`, text: t.label }))),
      el("td", { class: "num", text: String(u.flights) }),
      el("td", { text: u.live_banned ? "turned off" : u.live_share }),
      el("td", { class: u.discord ? "adm-on" : "", text: u.discord === null ? "–" : u.discord ? "On" : "Off",
        title: u.discord === null ? "Not known: the app hasn't synced since it started saying" : "" }),
      el("td", { class: "adm-date", text: day(u.created_at), title: when(u.created_at) }),
      el("td", { class: "adm-date", text: day(u.last_seen), title: when(u.last_seen) })));
    $("#adm-user-rows").replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: "8", class: "hint", text: "No account matches." }))]));
    $("#adm-users-count").textContent = `${v.total.toLocaleString()} account${v.total === 1 ? "" : "s"}${st.q ? ` matching "${st.q}"` : ""}`;
    $("#adm-prev").disabled = st.offset === 0;
    $("#adm-next").disabled = st.offset + v.page >= v.total;
    const last = Math.min(v.total, st.offset + v.page);
    $("#adm-page").textContent = v.total ? `${st.offset + 1} to ${last} of ${v.total.toLocaleString()}, by ${SORT[st.sort][0].toLowerCase()}, ${ORDER[st.sort][st.dir === "asc" ? 0 : 1]}` : "";
    heads();
  }

  /** The header: each column a button that sorts by it (again: the other way), the sorted one marked for screen readers. */
  function heads() {
    for (const th of document.querySelectorAll("#adm-user-head th[data-sort]")) {
      const key = th.dataset.sort, on = key === st.sort;
      th.setAttribute("aria-sort", on ? (st.dir === "asc" ? "ascending" : "descending") : "none");
      let b = th.querySelector("button");
      if (!b) {
        b = el("button", { type: "button", class: "adm-sort", onclick: () => {
          if (st.sort === key) st.dir = st.dir === "asc" ? "desc" : "asc"; else { st.sort = key; st.dir = SORT[key][1]; }
          st.offset = 0;
          users().catch((e) => say(e.message, true));
        } });
        th.replaceChildren(b);
      }
      b.textContent = SORT[key][0];
      b.append(el("span", { class: "adm-arrow", "aria-hidden": "true", text: on ? (st.dir === "asc" ? " ▲" : " ▼") : "" }));
      b.title = `Sort by ${SORT[key][0].toLowerCase()}`;
    }
  }

  async function openUser(id) {
    st.open = id;
    const box = $("#adm-user");
    box.hidden = false;
    box.replaceChildren(el("p", { class: "hint", text: "Loading ..." }));
    let u;
    try { u = await api("GET", `/v1/admin/users/${encodeURIComponent(id)}`); } catch (e) { box.replaceChildren(el("p", { class: "error", text: e.message })); return; }
    const tags = [...u.tags];
    const tagList = el("div", { class: "adm-tags" });
    const drawTags = () => tagList.replaceChildren(...(tags.length ? tags.map((t, i) => el("span", { class: `lf-chip tag tag-${t.color}` },
      t.label, el("button", { type: "button", class: "adm-x", "aria-label": `Remove the tag ${t.label}`, text: "×", onclick: () => { tags.splice(i, 1); saveTags(); } })))
      : [el("span", { class: "hint", text: "No tags." })]));
    const label = el("input", { type: "text", maxlength: "20", placeholder: "Streamer", "aria-label": "Tag" });
    const color = el("select", { "aria-label": "Colour" }, ...COLORS.map((c) => el("option", { value: c, text: c })));
    color.value = "purple";
    const saveTags = async () => {
      try { const v = await api("PATCH", `/v1/admin/users/${id}`, { tags }); tags.splice(0, tags.length, ...v.tags); drawTags(); users(); say("Tags saved. They show on the account's Live Flights card."); }
      catch (e) { say(e.message, true); openUser(id); }
    };
    const add = el("form", { class: "adm-add", onsubmit: (e) => {
      e.preventDefault();
      const text = label.value.trim();
      if (!text) return;
      if (tags.length >= 4) { say("At most 4 tags.", true); return; }
      tags.push({ label: text, color: color.value });
      label.value = "";
      saveTags();
    } }, label, color, el("button", { class: "btn btn-primary btn-sm", type: "submit", text: "Add tag" }));
    drawTags();
    box.replaceChildren(
      el("div", { class: "adm-user-head" }, el("h3", { text: u.email }),
        el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Close", onclick: () => { box.hidden = true; st.open = null; } })),
      el("dl", { class: "adm-facts" },
        el("dt", { text: "Display name" }), el("dd", { text: u.display_name || "–" }),
        el("dt", { text: "Joined" }), el("dd", { text: `${when(u.created_at)}${u.verified ? "" : " (not confirmed)"}` }),
        el("dt", { text: "Last seen" }), el("dd", { text: when(u.last_seen) }),
        el("dt", { text: "Flights synced" }), el("dd", { text: String(u.flights) }),
        el("dt", { text: "Live Flights" }), el("dd", { text: u.live_banned ? "Turned off by an admin" : `Sharing: ${u.live_share}` }),
        el("dt", { text: "Discord" }), el("dd", { text: u.discord === null ? "Not known yet" : u.discord ? "Rich Presence on" : "Rich Presence off" })),
      el("h4", { text: "Tags" }), tagList, add,
      el("h4", { text: "Devices" }),
      el("ul", { class: "adm-list" }, ...(u.sessions.length ? u.sessions.map((s) => el("li", { text: `${s.device || s.kind} (${s.kind}), last used ${when(s.last_used_at)}` }))
        : [el("li", { class: "hint", text: "Signed in nowhere." })])),
      el("h4", { text: "Latest flights" }),
      el("ul", { class: "adm-list" }, ...(u.recent.length ? u.recent.map((f) => el("li", { text: `${when(f.started_at)} · ${f.callsign || "?"} · ${f.origin || "?"} → ${f.destination || "?"}${f.aircraft ? ` · ${f.aircraft}` : ""}` }))
        : [el("li", { class: "hint", text: "None synced." })])),
      el("div", { class: "adm-acts adm-danger" },
        el("button", { type: "button", class: u.live_banned ? "btn btn-ghost btn-sm" : "btn btn-danger btn-sm",
          text: u.live_banned ? "Allow Live Flights sharing again" : "Turn off Live Flights sharing", onclick: () => setBanned(id, !u.live_banned) }),
        el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Sign out everywhere", onclick: async () => {
          if (!confirm(`Sign ${u.email} out on every device?`)) return;
          try { const r = await api("POST", `/v1/admin/users/${id}/signout`); say(`Signed out of ${r.signed_out} device${r.signed_out === 1 ? "" : "s"}.`); openUser(id); }
          catch (e) { say(e.message, true); }
        } }),
        el("button", { type: "button", class: "btn btn-danger btn-sm", text: "Delete account", onclick: async () => {
          const typed = prompt(`Delete ${u.email} and everything in it (logbook, replays, shared pages), for good?\n\nType the address to confirm:`);
          if (!typed) return;
          try { await api("DELETE", `/v1/admin/users/${id}`, { email: typed }); box.hidden = true; say(`Deleted ${u.email}.`); refresh(); }
          catch (e) { say(e.message, true); }
        } })));
    box.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  async function setBanned(id, banned) {
    if (banned && !confirm("Turn off this account's Live Flights sharing? Its flight leaves the map, and it can't share again until you allow it.")) return;
    try { await api("PATCH", `/v1/admin/users/${id}`, { live_banned: banned }); say(banned ? "Sharing turned off." : "Sharing allowed again."); refresh(); if (st.open === id) openUser(id); }
    catch (e) { say(e.message, true); }
  }

  async function refresh() {
    try { await Promise.all([overview(), live(), users()]); } catch (e) { say(e.message, true); }
  }

  let wired = false;
  function open() {
    if (!wired) {
      wired = true;
      let typing;
      $("#adm-search").oninput = (e) => { clearTimeout(typing); typing = setTimeout(() => { st.q = e.target.value.trim(); st.offset = 0; users().catch((x) => say(x.message, true)); }, 250); };
      $("#adm-prev").onclick = () => { st.offset = Math.max(0, st.offset - 50); users(); };
      $("#adm-next").onclick = () => { st.offset += 50; users(); };
      $("#adm-refresh").onclick = refresh;
    }
    say("");
    refresh();
  }

  return { open };
})();
