/* The header's menu on a narrow screen: the links folded behind a button (the styles hide them below 1080 px).
   Built here, so every page's header stays plain links; without JavaScript they show as a wrapped row instead. */
(function () {
  "use strict";
  const bar = document.querySelector("header.topbar");
  const nav = bar && bar.querySelector(".nav");
  if (!nav) return;
  nav.id = nav.id || "site-nav";
  document.documentElement.classList.add("has-nav-js");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "nav-toggle";
  button.setAttribute("aria-controls", nav.id);
  button.setAttribute("aria-expanded", "false");
  button.setAttribute("aria-label", "Menu");
  button.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" '
    + 'stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>';
  bar.appendChild(button);
  // On a phone the Dashboard button and the Discord icon go into the menu too (the styles hide them in the header).
  for (const src of [bar.querySelector(":scope > a.btn"), bar.querySelector(":scope > .nav-discord")]) {
    if (!src) continue;
    const a = document.createElement("a");
    a.href = src.getAttribute("href");
    a.className = "nav-more";
    a.textContent = src.classList.contains("nav-discord") ? "Discord" : src.textContent.trim();
    if (src.rel) a.rel = src.rel;
    nav.appendChild(a);
  }

  const set = (open) => {
    bar.classList.toggle("nav-open", open);
    button.setAttribute("aria-expanded", String(open));
    button.setAttribute("aria-label", open ? "Close the menu" : "Menu");
  };
  button.addEventListener("click", () => {
    const open = !bar.classList.contains("nav-open");
    set(open);
    if (open) nav.querySelector("a")?.focus();
  });
  nav.addEventListener("click", (e) => { if (e.target.closest("a")) set(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && bar.classList.contains("nav-open")) { set(false); button.focus(); }
  });
  document.addEventListener("click", (e) => { if (!bar.contains(e.target)) set(false); });
  window.matchMedia("(min-width: 1081px)").addEventListener("change", (m) => { if (m.matches) set(false); });
})();
