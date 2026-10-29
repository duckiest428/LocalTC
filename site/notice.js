/* The site-wide notice: a bar along the bottom of every page (the home page's header is see-through and fixed, so
   the top is taken). Now: LocalTC 0.4 and 0.5.0 say a new version only in Settings > Updates, never on their own, so
   their pilots are told here where to look. Closed with the x, it stays closed for that notice (a new one has a new
   key). No dependencies, no cookies: the closing is kept in this browser only. */

(() => {
  const KEY = "notice-0.5.2-update";
  try { if (localStorage.getItem(KEY)) return; } catch { /* storage blocked: shown each time */ }

  const css = `
    .site-notice { position: fixed; left: 50%; bottom: 16px; transform: translateX(-50%); z-index: 1000;
      width: min(760px, calc(100% - 32px)); box-sizing: border-box; display: flex; gap: 14px; align-items: flex-start;
      padding: 14px 16px; border-radius: var(--r3, 20px); background: var(--bg-3, #1f2329); color: var(--text-2, #b3bcc6);
      box-shadow: var(--e3, 0 8px 24px rgba(0,0,0,.4)), inset 0 0 0 1px rgba(231, 178, 74, .35);
      font: 14px/1.5 var(--sans, system-ui, sans-serif); }
    .site-notice b { color: var(--text, #e8ecf1); }
    .site-notice .sn-dot { flex: none; width: 10px; height: 10px; margin-top: 6px; border-radius: 50%;
      background: var(--amber, #e7b24a); box-shadow: 0 0 0 4px rgba(231, 178, 74, .18); }
    .site-notice p { margin: 0; flex: 1; }
    .site-notice a { color: var(--green, #5fd068); font-weight: 600; }
    .site-notice button { flex: none; border: 0; background: transparent; color: var(--text-3, #8a939d); cursor: pointer;
      font-size: 18px; line-height: 1; padding: 2px 4px; border-radius: 8px; }
    .site-notice button:hover, .site-notice button:focus-visible { color: var(--text, #e8ecf1); background: rgba(255,255,255,.06); }
    @media (max-width: 560px) { .site-notice { bottom: 10px; font-size: 13px; } }`;

  function show() {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
    const bar = document.createElement("div");
    bar.className = "site-notice";
    bar.setAttribute("role", "status");
    bar.innerHTML = `<span class="sn-dot" aria-hidden="true"></span>
      <p><b>LocalTC 0.5.2 is out.</b> On 0.5.0 or 0.4? Your copy won't tell you about new versions by itself: open
      <b>Settings</b> (the gear, top right) → <b>Updates</b> → <b>Download 0.5.2</b>, or get the
      <a href="https://github.com/duckiest428/LocalTC/releases/latest" rel="noopener">new setup</a>.</p>
      <button type="button" aria-label="Close this notice" title="Close">&#10005;</button>`;
    bar.querySelector("button").onclick = () => {
      bar.remove();
      try { localStorage.setItem(KEY, "1"); } catch { /* storage blocked: it comes back next time */ }
    };
    document.body.appendChild(bar);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", show);
  else show();
})();
