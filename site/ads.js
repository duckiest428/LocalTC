/* The dashboard's ads: Google AdSense (its script is in each page's <head>). One ad, on every other visit to the
   dashboard, in one place at the foot of the page that's showing (never the full-screen Flight Tracker, tracker.html,
   which doesn't load this). Optional: Account → Ads turns them off. A one-time notice tells people they're new.
   Kept in this browser's storage: whether they're on, the visit count, and that the notice was seen. */

const Ads = (() => {
  const CLIENT = "ca-pub-4833147286335766";
  // The ad unit's slot id, from AdSense (Ads → By ad unit → Display ads → its data-ad-slot). Empty: no ad shown.
  const SLOT = "";
  const KEY_ON = "localtc.ads", KEY_VISITS = "localtc.ads.visits", KEY_SEEN = "localtc.ads.noticeSeen";

  const read = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, value); } catch { /* private mode: not kept */ } };

  const enabled = () => read(KEY_ON) !== "off";

  // This visit counted once (a reload in the same tab isn't a new visit); ads on every second one.
  let visits = Number(read(KEY_VISITS) || 0);
  try {
    if (!sessionStorage.getItem(KEY_VISITS)) {
      visits += 1;
      write(KEY_VISITS, String(visits));
      sessionStorage.setItem(KEY_VISITS, "1");
    }
  } catch { /* storage blocked: every visit counts as the first */ }
  const thisVisit = visits % 2 === 0;

  const filled = new Set();  // the pages with their ad already in place

  /** The ad at the foot of ``page`` (a .dash-page's id), when this visit shows ads. One per page, once. */
  function place(page) {
    if (!SLOT || !enabled() || !thisVisit || filled.has(page)) return;
    const section = document.getElementById(page);
    if (!section) return;
    filled.add(page);
    const box = document.createElement("aside");
    box.className = "dash-ad";
    box.setAttribute("aria-label", "Advertisement");
    box.innerHTML = `<p class="dash-ad-label">Advertisement · <a href="#account">turn off</a></p>`
      + `<ins class="adsbygoogle" style="display:block" data-ad-client="${CLIENT}" data-ad-slot="${SLOT}"`
      + ` data-ad-format="auto" data-full-width-responsive="true"></ins>`;
    section.appendChild(box);
    try { (window.adsbygoogle = window.adsbygoogle || []).push({}); } catch { /* blocked: nothing shown */ }
  }

  function set(on) {
    write(KEY_ON, on ? "on" : "off");
    const box = document.getElementById("ads-on");
    if (box) box.checked = on;
    if (!on) { document.querySelectorAll(".dash-ad").forEach((el) => el.remove()); filled.clear(); }
  }

  /** The one-time notice, the first time the dashboard is open after ads came. */
  function notice() {
    const dialog = document.getElementById("ads-notice");
    if (!dialog || read(KEY_SEEN) || typeof dialog.showModal !== "function") return;
    write(KEY_SEEN, "1");
    dialog.addEventListener("close", () => { if (dialog.returnValue === "off") set(false); }, { once: true });
    dialog.showModal();
  }

  const box = document.getElementById("ads-on");
  if (box) {
    box.checked = enabled();
    box.addEventListener("change", () => set(box.checked));
  }

  // The page showing now, and each one opened after (dashboard.js shows one .dash-page at a time).
  const visible = () => [...document.querySelectorAll(".dash-page")].find((el) => !el.hidden);
  const watch = new MutationObserver(() => {
    if (document.getElementById("dash")?.hidden) return;  // signed out: no dashboard yet
    const page = visible();
    if (page) { place(page.id); notice(); }
  });
  watch.observe(document.body, { subtree: true, attributes: true, attributeFilter: ["hidden"] });

  return { place, set, enabled };
})();
