/* LocalTC in the pilot's language. Every word of the window is written in English (app.js, efb.js, the page); this
   puts each piece of text and each tooltip into the language chosen as it's drawn, from i18n/<code>.json (English
   text -> its translation, made with tools/i18n.py). A text with numbers or names in it is a pattern there ("Step {0}
   of {1}"). What isn't the window's own words stays as it is: the radio log, callsigns, the map's aircraft, an
   aircraft's checklist (anything inside translate="no"). ATC and the radio are aviation English in every language. */
"use strict";

const I18n = (() => {
  const ATTRS = ["title", "placeholder", "aria-label"];
  const SKIP = "script,style,code,pre,[translate=no],.leaflet-pane,.leaflet-control-attribution";
  let lang = "en";
  let dict = null;  // English -> translation
  let patterns = [];  // [regex, translation, anchor]: texts with values in them
  const texts = new WeakMap();  // text node -> {src: its English, out: what's shown}
  const attrs = new WeakMap();  // element -> {attr: {src, out}}
  let observer = null;
  let busy = false;

  const norm = (s) => s.replace(/\s+/g, " ").trim();

  function compile(entries) {
    patterns = [];
    for (const [src, out] of Object.entries(entries)) {
      if (!src.includes("{0}")) continue;
      const parts = src.split(/\{(\d)\}/);
      let re = "^";
      let anchor = "";
      parts.forEach((part, i) => {
        if (i % 2) re += "(.*?)";
        else {
          // (A space beside a value may not be there: "... with Piper. {1}" drawn without its {1}.)
          re += part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/^ /, " ?").replace(/ $/, " ?");
          if (part.trim().length > anchor.length) anchor = part.trim();
        }
      });
      // Open at both ends ("{0} flight{1}"): only where the values are short beside its words.
      const open = parts[0] === "" && parts[parts.length - 1] === "";
      patterns.push([new RegExp(re + "$"), out, anchor, open ? parts.filter((_, i) => i % 2 === 0).join("").length : -1]);
    }
    patterns.sort((a, b) => b[2].length - a[2].length);  // the most particular first
  }

  /** ``english`` in the language chosen, or null when there's nothing to change. */
  function lookup(english) {
    if (!dict) return null;
    const key = norm(english);
    if (!key || !/[A-Za-z]/.test(key)) return null;
    if (Object.prototype.hasOwnProperty.call(dict, key)) return dict[key];
    for (const [re, out, anchor, literal] of patterns) {
      if (anchor && !key.includes(anchor)) continue;
      const m = re.exec(key);
      // Values are short beside the words around them: "{0} flight{1}" isn't "During a flight it always stays loaded ...".
      if (m && literal >= 0 && key.length - literal > Math.max(40, 3 * literal)) continue;
      if (m) return out.replace(/\{(\d)\}/g, (_, n) => { const v = m[Number(n) + 1] ?? ""; return lookup(v) ?? v; });
    }
    return null;
  }

  /** ``english`` translated (itself when there's no translation): for the messages that aren't in the page. */
  function t(english) {
    if (english == null) return english;
    const s = String(english);
    if (!dict) return s;
    // A message of several paragraphs: each on its own.
    if (s.includes("\n")) return s.split("\n").map((line) => (line.trim() ? t(line) : line)).join("\n");
    return lookup(s) ?? s;
  }

  function skipped(el) {
    return !el || (el.closest && el.closest(SKIP) !== null);
  }

  function doText(node) {
    const value = node.nodeValue;
    if (!value || !value.trim()) return;
    const was = texts.get(node);
    if (was && value === was.out) return;  // ours already
    if (skipped(node.parentElement) || node.parentElement.tagName === "TEXTAREA") return;  // (what's typed is the pilot's)
    const out = lookup(value);
    if (out == null) { texts.delete(node); return; }
    // (A piece that starts with punctuation in its language follows on from the word before: no space between.)
    const lead = /^[,.;:!?)，。；：！？）、]/.test(out) ? "" : value.match(/^\s*/)[0], trail = value.match(/\s*$/)[0];
    const shown = lead + out + trail;
    texts.set(node, { src: value, out: shown });
    if (shown !== value) node.nodeValue = shown;
  }

  function doAttrs(el) {
    if (skipped(el)) return;
    let mine = attrs.get(el);
    for (const name of ATTRS) {
      const value = el.getAttribute(name);
      if (!value) continue;
      const was = mine && mine[name];
      if (was && value === was.out) continue;
      const out = lookup(value);
      if (out == null) continue;
      mine = mine || {};
      mine[name] = { src: value, out };
      attrs.set(el, mine);
      if (out !== value) el.setAttribute(name, out);
    }
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) { doText(root); return; }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE) {
      if (skipped(root)) return;
      doAttrs(root);
    }
    const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode: (n) => (n.nodeType === Node.ELEMENT_NODE && n.matches(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    for (let n = it.nextNode(); n; n = it.nextNode()) {
      if (n.nodeType === Node.TEXT_NODE) doText(n); else doAttrs(n);
    }
  }

  /** Back to the English the app wrote (the language changed). */
  function restore(root) {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = it.currentNode; n; n = it.nextNode()) {
      if (n.nodeType === Node.TEXT_NODE) {
        const was = texts.get(n);
        if (was && n.nodeValue === was.out) n.nodeValue = was.src;
        texts.delete(n);
      } else if (n.nodeType === Node.ELEMENT_NODE) {
        const mine = attrs.get(n);
        if (mine) for (const [name, was] of Object.entries(mine)) if (n.getAttribute(name) === was.out) n.setAttribute(name, was.src);
        attrs.delete(n);
      }
    }
  }

  function watch() {
    if (observer || !document.body) return;
    observer = new MutationObserver((records) => {
      if (busy || !dict) return;
      busy = true;
      try {
        for (const r of records) {
          if (r.type === "characterData") doText(r.target);
          else if (r.type === "attributes") doAttrs(r.target);
          else r.addedNodes.forEach(walk);
        }
      } finally { busy = false; }
    });
    observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }

  function load(code) {
    if (!code || code === "en") return null;
    // Read before the page is drawn (a file next to the page: no wait worth showing English first for).
    try {
      const xhr = new XMLHttpRequest();
      xhr.open("GET", `/static/i18n/${encodeURIComponent(code)}.json`, false);
      xhr.send();
      return xhr.status === 200 ? JSON.parse(xhr.responseText) : null;
    } catch { return null; }
  }

  /** The window in ``code`` from now on ("en": as written). */
  function set(code) {
    code = code || "en";
    if (code === lang && (dict || code === "en")) return;
    const next = load(code);
    if (dict && document.body) { busy = true; restore(document.body); busy = false; }
    lang = next ? code : "en";
    dict = next;
    compile(dict || {});
    document.documentElement.lang = lang;
    try { localStorage.setItem("lang", lang); } catch { /* not kept: the app's setting still says */ }
    if (dict && document.body) { busy = true; try { walk(document.body); } finally { busy = false; } }
    watch();
  }

  // The browser's own boxes (Delete this flight?) in the language too.
  const nativeConfirm = window.confirm.bind(window), nativePrompt = window.prompt.bind(window), nativeAlert = window.alert.bind(window);
  window.confirm = (m) => nativeConfirm(t(m));
  window.prompt = (m, d) => nativePrompt(t(m), d);
  window.alert = (m) => nativeAlert(t(m));

  let first = "en";
  try { first = localStorage.getItem("lang") || "en"; } catch { /* English until the app says */ }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => set(first));
  else set(first);

  return { set, t, get lang() { return lang; } };
})();
