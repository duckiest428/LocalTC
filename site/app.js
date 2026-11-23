/* The home page's motion, no dependencies: the header and the photos as you scroll, the hero's photo and its
   headline, the radio log typing itself, the flight leg by leg, the subscription sum, the copy
   buttons. Everything that moves stands still for prefers-reduced-motion. */

const quiet = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = window.matchMedia("(pointer: fine)").matches;
// On a touch screen the photos stay put as you scroll: moving them every frame is what makes a phone stutter.
const touch = window.matchMedia("(pointer: coarse)").matches;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// --- sections arrive ------------------------------------------------------------------------------
// The class is added here rather than in the HTML, so a page with no JavaScript simply shows
// everything at once instead of hiding it forever.
function reveals() {
  if (quiet || !("IntersectionObserver" in window)) return;
  const groups = ".section-title, .section-lede, .card, .compare, .calc, .fineprint, .step, .callout, .status-col," +
                 " .cta-center, .window, .tile, .chapter-photo, .chapter-kicker, .chapter-title, .chapter-lede," +
                 " .legs-stage, .legs-rail, .ld-box, .plan, .faq details, .versus";
  const seen = new IntersectionObserver((entries, self) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("in");
      self.unobserve(entry.target);
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });

  for (const el of document.querySelectorAll(groups)) {
    // Anything already on screen (the hero, or wherever a #link dropped you) is left alone. It
    // has nothing to arrive from, and hiding it first would only make the page flash.
    if (el.getBoundingClientRect().top < window.innerHeight) continue;
    el.classList.add("reveal");
    // Siblings in a grid come in one after another, not all at once.
    const siblings = [...(el.parentElement?.children ?? [])].filter((n) => n.matches(groups));
    el.style.setProperty("--delay", `${Math.min(siblings.indexOf(el), 5) * 70}ms`);
    // Once it has arrived it goes back to being itself, so its own hover moves aren't held up by the delay.
    el.addEventListener("transitionend", function done(e) {
      if (e.target !== el || !el.classList.contains("in")) return;
      el.removeEventListener("transitionend", done);
      setTimeout(() => { el.classList.remove("reveal", "in"); el.style.removeProperty("--delay"); }, 400);
    });
    seen.observe(el);
  }
}
// Two frames late, so a #link in the address bar has already jumped and we measure where the
// reader actually is.
requestAnimationFrame(() => requestAnimationFrame(reveals));

// --- as you scroll: the header, the progress line, the photos moving slower than the page -----------
const topbar = document.querySelector(".topbar");
const progress = document.querySelector(".progress");
const heroSlides = document.querySelector(".hero-slides");
const drifting = [...document.querySelectorAll("[data-parallax]")];
let ticking = false;

function onScroll() {
  ticking = false;
  const y = window.scrollY;
  const room = document.documentElement.scrollHeight - window.innerHeight;
  topbar?.classList.toggle("scrolled", y > 40);
  progress?.style.setProperty("--p", room > 0 ? (y / room).toFixed(4) : "0");
  if (quiet || touch) return;
  if (heroSlides && y < window.innerHeight * 1.2) heroSlides.style.transform = `translate3d(0, ${(y * 0.35).toFixed(1)}px, 0)`;
  for (const img of drifting) {
    const box = img.parentElement.getBoundingClientRect();
    if (box.bottom < -100 || box.top > window.innerHeight + 100) continue;
    const off = box.top + box.height / 2 - window.innerHeight / 2;
    img.style.setProperty("--py", `${(-off * Number(img.dataset.parallax)).toFixed(1)}px`);
  }
}
window.addEventListener("scroll", () => {
  if (!ticking) { ticking = true; requestAnimationFrame(onScroll); }
}, { passive: true });
window.addEventListener("resize", onScroll);
onScroll();

// --- the hero: the headline word by word, the window leaning toward the pointer --------------

for (const h of document.querySelectorAll("h1.split")) {
  if (quiet) break;
  const words = h.textContent.trim().split(/\s+/);
  h.textContent = "";
  words.forEach((word, i) => {
    const span = document.createElement("span");
    span.className = "w";
    span.style.setProperty("--i", i);
    span.textContent = word;
    h.append(span, " ");
  });
}

const hero = document.querySelector(".hero");
const heroWindow = hero?.querySelector(".window");
if (heroWindow && !quiet && finePointer) {
  hero.addEventListener("pointermove", (e) => {
    const box = heroWindow.getBoundingClientRect();
    const x = (e.clientX - (box.left + box.width / 2)) / window.innerWidth;
    const y = (e.clientY - (box.top + box.height / 2)) / window.innerHeight;
    heroWindow.style.setProperty("--ry", `${(x * 12).toFixed(2)}deg`);
    heroWindow.style.setProperty("--rx", `${(-y * 9).toFixed(2)}deg`);
  });
  hero.addEventListener("pointerleave", () => {
    heroWindow.style.setProperty("--ry", "0deg");
    heroWindow.style.setProperty("--rx", "0deg");
  });
}

// A light that follows the pointer across the cards.
if (finePointer) {
  document.addEventListener("pointermove", (e) => {
    const card = e.target.closest?.(".tile, .step, .plan");
    if (!card) return;
    const box = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${e.clientX - box.left}px`);
    card.style.setProperty("--my", `${e.clientY - box.top}px`);
  }, { passive: true });
}

// --- the radio log -------------------------------------------------------------------------------
// A real CYVR departure, in the words LocalTC actually uses.
const EXCHANGE = [
  { who: "Air Canada 216", cls: "you",
    text: "Vancouver Delivery, Air Canada 216, ready to copy IFR to Calgary." },
  { who: "Vancouver Delivery 121.400", cls: "atc",
    text: "Air Canada 216, cleared to Calgary airport as filed, climb and maintain 5,000, expect flight level 350 one zero minutes after departure, departure frequency 126.125, squawk 4512." },
  { who: "Air Canada 216", cls: "you",
    text: "Cleared to Calgary as filed, maintain 5,000, expect 350 in ten, departure 126.125, squawk 4512, Air Canada 216." },
  { who: "Vancouver Delivery 121.400", cls: "atc", ok: true,
    text: "Air Canada 216, readback correct." },
  { who: "Vancouver Ground 121.700", cls: "atc",
    text: "Air Canada 216, runway 08R, taxi via Golf, Charlie, hold short runway 13. Information Bravo is current, altimeter 29.92." },
  { who: "Air Canada 216", cls: "you",
    text: "Runway 08R via Golf Charlie, hold short 13, Air Canada 216." },
  { who: "Vancouver Ground 121.700", cls: "atc",
    text: "Air Canada 216, cross runway 13, continue taxi runway 08R." },
  { who: "Vancouver Tower 118.700", cls: "atc",
    text: "Air Canada 216, wind 090 at 8, runway 08R, cleared for takeoff." },
  { who: "Vancouver Departure 126.125", cls: "atc",
    text: "Air Canada 216, radar contact, climb and maintain flight level 350." },
  { who: "Vancouver Departure 126.125", cls: "atc",
    text: "Air Canada 216, contact Edmonton Center 124.525." },
];

const KEEP = 4;          // lines left on screen
const CHAR_MS = 17;      // how fast a transmission types out
const GAP_MS = 1100;     // quiet between transmissions

const log = document.getElementById("log");

function line(entry) {
  const li = document.createElement("li");
  li.className = entry.cls;
  li.innerHTML = '<span class="who"></span><span class="said"></span>';
  li.querySelector(".who").textContent = entry.cls === "you" ? "You" : entry.who;
  if (entry.ok) li.querySelector(".said").classList.add("ok");
  log.append(li);
  while (log.children.length > KEEP) log.firstElementChild.remove();
  return li.querySelector(".said");
}

async function type(target, text) {
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  cursor.textContent = " ";
  target.after(cursor);
  for (let i = 1; i <= text.length; i++) {
    target.textContent = text.slice(0, i);
    await wait(CHAR_MS);
  }
  cursor.remove();
}

async function playRadio() {
  if (!log) return;
  if (quiet) {                           // no animation: just show the last few calls
    EXCHANGE.slice(-KEEP).forEach((e) => { line(e).textContent = e.text; });
    return;
  }
  for (;;) {
    for (const entry of EXCHANGE) {
      await type(line(entry), entry.text);
      await wait(GAP_MS);
    }
    await wait(1800);
    log.replaceChildren();
  }
}
playRadio();

// --- the app's tabs, in the hero's window -------------------------------------------------------------------
// A tab list as the app has it: a click or the arrow keys pick a tab, and only the picked one is in the tab order.
function appTabs() {
  const tabs = [...document.querySelectorAll(".window-tabs .wtab")];
  if (!tabs.length) return;
  const show = (tab, focus) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    }
    if (focus) tab.focus();
    // the tab row scrolls sideways on a phone: the picked tab brought into it, the page left where it is
    const row = tab.parentElement;
    if (tab.offsetLeft < row.scrollLeft) row.scrollLeft = tab.offsetLeft - 14;
    else if (tab.offsetLeft + tab.offsetWidth > row.scrollLeft + row.clientWidth) row.scrollLeft = tab.offsetLeft + tab.offsetWidth - row.clientWidth + 14;
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => show(t));
    t.addEventListener("keydown", (e) => {
      const n = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
      if (n === undefined) return;
      e.preventDefault();
      show(tabs[(n + tabs.length) % tabs.length], true);
    });
  });
}
appTabs();

// Airport Lookup, with a few airports baked in (the app looks up every airport in the sim).
const AIRPORTS = {
  CYVR: { name: "Vancouver International", metar: "CYVR 221800Z 09008KT 30SM FEW040 BKN120 08/03 A2992",
    rwys: "08L/26R, 08R/26L, 13/31", freqs: [["ATIS", "124.600"], ["Delivery", "121.400"], ["Ground", "121.700"], ["Tower", "118.700"], ["Departure", "126.125"]] },
  KSEA: { name: "Seattle-Tacoma International", metar: "KSEA 221753Z 17011KT 10SM -RA BKN025 OVC045 09/07 A2987",
    rwys: "16L/34R, 16C/34C, 16R/34L", freqs: [["ATIS", "118.000"], ["Clearance", "128.000"], ["Ground", "121.700"], ["Tower", "119.900"]] },
  KSFO: { name: "San Francisco International", metar: "KSFO 221756Z 29014KT 10SM FEW012 16/11 A3002",
    rwys: "01L/19R, 01R/19L, 10L/28R, 10R/28L", freqs: [["ATIS", "118.850"], ["Clearance", "118.200"], ["Ground", "121.800"], ["Tower", "120.500"]] },
  EGLL: { name: "London Heathrow", metar: "EGLL 221750Z 24012KT 9999 SCT032 11/06 Q1014",
    rwys: "09L/27R, 09R/27L", freqs: [["ATIS", "128.075"], ["Delivery", "121.975"], ["Ground", "121.900"], ["Tower", "118.500"]] },
  LSZH: { name: "Zurich", metar: "LSZH 221750Z 06006KT 9999 FEW045 07/01 Q1022",
    rwys: "10/28, 14/32, 16/34", freqs: [["ATIS", "128.525"], ["Delivery", "121.930"], ["Ground", "121.900"], ["Tower", "118.100"]] },
};
function lookup() {
  const q = document.getElementById("wp-q"), out = document.getElementById("wp-apt");
  if (!q || !out) return;
  const render = () => {
    const code = q.value.trim().toUpperCase();
    const a = AIRPORTS[code];
    out.replaceChildren();
    if (!a) {
      const p = document.createElement("p");
      p.className = "wp-note";
      p.textContent = code.length < 4 ? "Type an ICAO code: CYVR, KSEA, KSFO, EGLL or LSZH here. The app knows every airport in the sim."
        : `${code} isn't in this preview. Try CYVR, KSEA, KSFO, EGLL or LSZH; the app knows every airport in the sim.`;
      out.append(p);
      return;
    }
    const h = document.createElement("h4");
    h.textContent = `${code} · ${a.name}`;
    const m = document.createElement("p");
    m.className = "wp-metar";
    m.textContent = a.metar;
    const dl = document.createElement("dl");
    for (const [k, v] of [["Runways", a.rwys], ...a.freqs]) {
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = k;
      dd.textContent = v;
      dl.append(dt, dd);
    }
    out.append(h, m, dl);
  };
  q.addEventListener("input", render);
  render();
}
lookup();

// --- the flight, leg by leg ---------------------------------------------------------------------
// Each leg's bar fills (CSS), and when it's full the next leg comes up. Off screen, or with the pointer on the photo,
// the bar holds.
function legs() {
  const section = document.getElementById("legs");
  if (!section) return;
  const buttons = [...section.querySelectorAll(".legs-rail button")];
  const photos = [...section.querySelectorAll(".leg-photo img")];
  const phase = document.getElementById("leg-phase");
  const station = document.getElementById("leg-station");
  const freq = document.getElementById("leg-freq");
  const said = document.getElementById("leg-said");
  let typing = 0;

  async function typeOut(text) {
    const me = ++typing;
    if (quiet) { said.textContent = text; return; }
    for (let i = 1; i <= text.length; i++) {
      if (me !== typing) return;
      said.textContent = text.slice(0, i);
      await wait(15);
    }
  }

  function show(n) {
    buttons.forEach((b, i) => {
      b.setAttribute("aria-selected", String(i === n));
      b.tabIndex = i === n ? 0 : -1;
      b.classList.toggle("done", i < n);
      const bar = b.querySelector(".leg-bar");
      bar.style.animation = "none";
      void bar.offsetWidth;              // restart the fill
      bar.style.animation = "";
    });
    photos.forEach((p, i) => p.classList.toggle("on", i === n));
    // the next leg's photo, ready before it's needed
    const next = photos[(n + 1) % photos.length];
    if (next.loading === "lazy") next.loading = "eager";
    const b = buttons[n];
    const rail = b.closest(".legs-rail");
    if (rail.scrollWidth > rail.clientWidth) {   // a phone: the rail scrolls, and the leg on now stays in it
      rail.scrollBy({ left: b.getBoundingClientRect().left - rail.getBoundingClientRect().left - 8, behavior: quiet ? "auto" : "smooth" });
    }
    phase.textContent = b.dataset.phase;
    station.textContent = b.dataset.station;
    freq.textContent = b.dataset.freq;
    typeOut(b.dataset.said);
  }

  buttons.forEach((b, i) => {
    b.addEventListener("click", () => show(i));
    b.addEventListener("keydown", (e) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (!step) return;
      e.preventDefault();
      const n = (i + step + buttons.length) % buttons.length;
      show(n);
      buttons[n].focus();
    });
    b.querySelector(".leg-bar").addEventListener("animationend", () => {
      if (!quiet) show((i + 1) % buttons.length);
    });
  });
  buttons.forEach((b, i) => { b.tabIndex = i === 0 ? 0 : -1; });

  let visible = false, held = false;
  const sync = () => section.classList.toggle("paused", !visible || held);
  sync();
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; sync(); }, { threshold: 0.35 }).observe(section);
  const stage = section.querySelector(".legs-stage");
  stage.addEventListener("pointerenter", () => { held = true; sync(); });
  stage.addEventListener("pointerleave", () => { held = false; sync(); });
}
legs();

// --- what the subscription costs -----------------------------------------------------------------
const rate = document.getElementById("rate");
const years = document.getElementById("years");
const total = document.getElementById("sub-total");
let shown = 0, tween = 0;

function countTo(value) {
  cancelAnimationFrame(tween);
  if (quiet) { shown = value; total.textContent = `$${value.toLocaleString("en-US")}`; return; }
  const from = shown, start = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - start) / 600);
    shown = Math.round(from + (value - from) * (1 - (1 - k) ** 3));
    total.textContent = `$${shown.toLocaleString("en-US")}`;
    if (k < 1) tween = requestAnimationFrame(step);
  };
  tween = requestAnimationFrame(step);
}

function sum() {
  const monthly = Number(rate.value);
  const y = Number(years.value);
  document.getElementById("rate-out").textContent = `$${monthly}`;
  document.getElementById("years-out").textContent = `${y} year${y === 1 ? "" : "s"}`;
  countTo(monthly * 12 * y);
}
if (rate && years && total) {
  rate.addEventListener("input", sum);
  years.addEventListener("input", sum);
  // It counts up the first time you see it.
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(([e], self) => {
      if (!e.isIntersecting) return;
      self.disconnect();
      sum();
    }, { threshold: 0.4 }).observe(total);
  } else {
    sum();
  }
}

// --- copy the install commands -------------------------------------------------------------------
document.querySelectorAll(".copy").forEach((button) => {
  button.addEventListener("click", async () => {
    const text = button.previousElementSibling.textContent;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Press Ctrl-C";
    }
    setTimeout(() => { button.textContent = "Copy"; }, 1600);
  });
});
