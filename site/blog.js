/* The blog (blog.html): the posts, newest first, and one post at a time (blog#<slug>).

   Two kinds of post, shown together: the site's own, written in the repository (blog-posts.json), and the ones
   written here, kept by the account server (GET /v1/blog). The second kind is written on this page by the blog's
   authors: signed in to the dashboard with an author's account (the server decides who: BLOG_AUTHORS), "New post"
   opens the editor. Pictures are dropped, pasted or picked, made smaller here, and uploaded to the server.

   A post is Markdown, a small part of it: ## headings, **bold**, *italic*, `code`, [links](https://...),
   ![pictures](address), - lists, 1. lists, > quotes and --- lines. It's escaped first and built up from there,
   so nothing in a post can run. Pictures only from the server's blog store or this site. */
(function () {
  "use strict";

  const MAX_SIDE = 1800;  // a picture's longest side once made smaller
  const state = { posts: [], author: false, drafts: null, editing: null, cover: null };

  // --- Markdown, the safe little part of it --------------------------------------------------------------------

  const imageOk = (u) => /^https:\/\/api\.localtc\.tech\/v1\/blog\/images\/[a-f0-9]{32}$/.test(u)
    || /^http:\/\/localhost:8787\/v1\/blog\/images\/[a-f0-9]{32}$/.test(u) || /^\/?img\/[\w/.-]+\.(?:jpe?g|png|webp|gif)$/.test(u);
  const linkOk = (u) => /^(https?:\/\/|\/|#|mailto:)/.test(u) || /^[\w-]+(#[\w-]+)?$/.test(u);

  function inline(text) {
    let s = esc(text);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, url) => (imageOk(url.replace(/&amp;/g, "&")) ? `<img src="${url}" alt="${alt}" loading="lazy">` : ""));
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
      const u = url.replace(/&amp;/g, "&");
      if (!linkOk(u)) return label;
      const away = /^https?:/.test(u) && !/^https:\/\/(www\.)?localtc\.tech/.test(u);
      return `<a href="${url}"${away ? ' rel="noopener" target="_blank"' : ""}>${label}</a>`;
    });
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
    return s;
  }

  function markdown(src) {
    const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let para = [], list = null, quote = [], code = null;
    const flush = () => {
      if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; }
      if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; }
      if (quote.length) { out.push(`<blockquote>${quote.map(inline).join("<br>")}</blockquote>`); quote = []; }
    };
    for (const line of lines) {
      if (code !== null) {
        if (/^```/.test(line)) { out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`); code = null; } else code.push(line);
        continue;
      }
      let m;
      if (/^```/.test(line)) { flush(); code = []; }
      else if (!line.trim()) flush();
      else if ((m = /^(#{2,4})\s+(.*)$/.exec(line))) { flush(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); }
      else if (/^(-{3,}|\*{3,})\s*$/.test(line)) { flush(); out.push("<hr>"); }
      else if ((m = /^!\[([^\]]*)\]\(([^)\s]+)\)\s*$/.exec(line))) { flush(); if (imageOk(m[2])) out.push(`<figure><img src="${esc(m[2])}" alt="${esc(m[1])}" loading="lazy"></figure>`); }
      else if ((m = /^>\s?(.*)$/.exec(line))) { if (para.length || list) { const q = quote; quote = []; flush(); quote = q; } quote.push(m[1]); }
      else if ((m = /^\s*[-*]\s+(.*)$/.exec(line)) || (m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) {
        const tag = /^\s*\d/.test(line) ? "ol" : "ul";
        if (para.length || quote.length || (list && list.tag !== tag)) flush();
        list = list || { tag, items: [] };
        list.items.push(m[1]);
      } else { if (list || quote.length) flush(); para.push(line); }
    }
    if (code !== null) out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
    flush();
    return out.join("\n");
  }

  // --- the posts -------------------------------------------------------------------------------------------------

  const when = (iso) => (iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" }) : "Draft");
  const minutes = (body) => Math.max(1, Math.round(String(body || "").split(/\s+/).length / 220));
  const say = (text, error = false) => { const m = $("#blog-msg"); m.hidden = !text; m.textContent = text || ""; m.classList.toggle("error", error); };

  async function load() {
    const [own, served] = await Promise.all([
      fetch("blog-posts.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : { posts: [] })).catch(() => ({ posts: [] })),
      api("GET", "/v1/blog").catch(() => ({ posts: [] })),
    ]);
    state.posts = [...(own.posts || []).map((p) => ({ ...p, own: true })), ...(served.posts || [])]
      .filter((p) => p.published_at).sort((a, b) => b.published_at.localeCompare(a.published_at));
  }

  function card(p, big = false) {
    const cover = p.cover && imageOk(p.cover) ? `<img src="${esc(p.cover)}" alt="" loading="lazy">` : `<span class="pc-mark" aria-hidden="true"><img src="logo.svg" alt="" width="56" height="56"></span>`;
    return `<a class="post-card${big ? " big" : ""}${p.published_at ? "" : " draft"}" href="#${esc(p.slug)}" data-slug="${esc(p.slug)}">
  <div class="pc-cover">${cover}</div>
  <div class="pc-text"><span class="pc-date">${esc(when(p.published_at))}${p.published_at ? "" : " · not published"}</span>
    <h2>${esc(p.title)}</h2>${p.summary ? `<p>${esc(p.summary)}</p>` : ""}</div></a>`;
  }

  function showList() {
    $("#post").hidden = true;
    $("#editor").hidden = true;
    const list = $("#blog-list");
    list.hidden = false;
    const drafts = state.drafts;
    if (drafts) {
      list.innerHTML = `<div class="blog-sub"><h2>Drafts and posts</h2><a href="#" class="linklike" id="blog-back">&larr; The blog</a></div>
<div class="post-grid">${drafts.length ? drafts.map((p) => card(p)).join("") : `<p class="hint">Nothing written here yet.</p>`}</div>`;
      $("#blog-back").onclick = (e) => { e.preventDefault(); state.drafts = null; showList(); };
      return;
    }
    if (!state.posts.length) { list.innerHTML = `<p class="blog-empty">No posts yet. Soon.</p>`; return; }
    const [first, ...rest] = state.posts;
    list.innerHTML = `${card(first, true)}${rest.length ? `<div class="post-grid">${rest.map((p) => card(p)).join("")}</div>` : ""}`;
    document.title = "Blog | LocalTC";
  }

  async function showPost(slug) {
    let p = state.posts.find((x) => x.slug === slug) || state.all?.find((x) => x.slug === slug);
    if (p && !p.own && p.body === undefined) {
      try { p = { ...p, ...(await api("GET", `/v1/blog/posts/${encodeURIComponent(slug)}`)) }; } catch { p = null; }
    }
    if (!p) { say("That post isn't here (any more)."); location.hash = ""; return; }
    say("");
    $("#blog-list").hidden = true;
    $("#editor").hidden = true;
    const art = $("#post");
    art.hidden = false;
    const editable = state.author && !p.own;
    art.innerHTML = `<a href="#" class="linklike post-back">&larr; All posts</a>
${p.cover && imageOk(p.cover) ? `<img class="post-cover" src="${esc(p.cover)}" alt="">` : ""}
<header class="post-head"><p class="pc-date">${esc(when(p.published_at))} · ${minutes(p.body)} min read${p.author ? ` · ${esc(p.author)}` : ""}</p>
<h1>${esc(p.title)}</h1>${p.summary ? `<p class="post-lede">${esc(p.summary)}</p>` : ""}
${editable ? `<button type="button" class="btn btn-ghost btn-sm" id="post-edit">Edit this post</button>` : ""}</header>
<div class="post-body">${markdown(p.body)}</div>`;
    art.querySelector(".post-back").onclick = (e) => { e.preventDefault(); location.hash = ""; };
    if (editable) $("#post-edit").onclick = () => edit(p);
    document.title = `${p.title} | LocalTC`;
    window.scrollTo(0, art.getBoundingClientRect().top + window.scrollY - 90);
  }

  function route() {
    const slug = decodeURIComponent(location.hash.slice(1));
    if (slug && /^[a-z0-9-]+$/.test(slug)) showPost(slug); else showList();
  }

  // --- writing (the blog's authors) --------------------------------------------------------------------------------

  /** A picture made at most MAX_SIDE across (a GIF as it is, so it keeps moving), then uploaded: its address. */
  async function upload(file) {
    let blob = file;
    if (file.type !== "image/gif") {
      const bmp = await createImageBitmap(file);
      const k = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(bmp.width * k);
      canvas.height = Math.round(bmp.height * k);
      canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
      const type = file.type === "image/png" && k === 1 && file.size < 1_200_000 ? "image/png" : "image/jpeg";
      for (const q of [0.86, 0.75, 0.6]) {
        blob = await new Promise((r) => canvas.toBlob(r, type, q));
        if (blob.size <= 1_400_000) break;
      }
    }
    if (blob.size > 1_500_000) throw new Error(`${file.name} is too big, even made smaller: up to 1.5 MB.`);
    const res = await fetch(`${API}/v1/blog/images`, { method: "PUT", credentials: "include", body: blob, headers: { "Content-Type": blob.type, "X-LocalTC": "1" } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `The server said ${res.status}.`);
    return `${API}/v1/blog/images/${data.id}`;
  }

  const body = () => $("#ed-body");

  /** Put text in the post where the cursor is (or around what's selected). */
  function insert(before, after = "", placeholder = "") {
    const t = body();
    const [a, b] = [t.selectionStart, t.selectionEnd];
    const picked = t.value.slice(a, b) || placeholder;
    t.setRangeText(before + picked + after, a, b, "end");
    if (!t.value.slice(a, b).length && placeholder) t.setSelectionRange(a + before.length, a + before.length + picked.length);
    t.focus();
    preview();
  }

  /** At the start of each selected line (a list, a quote, a heading). */
  function prefix(mark) {
    const t = body();
    const start = t.value.lastIndexOf("\n", t.selectionStart - 1) + 1;
    const end = t.selectionEnd;
    const chunk = t.value.slice(start, end) || "";
    t.setRangeText(chunk.split("\n").map((l) => mark + l.replace(/^(#{2,4}\s|[-*]\s|>\s?)/, "")).join("\n"), start, end, "end");
    t.focus();
    preview();
  }

  async function addPictures(files) {
    const pics = [...files].filter((f) => /^image\/(jpeg|png|webp|gif)$/.test(f.type));
    if (!pics.length) return;
    for (const f of pics) {
      const mark = `![Uploading ${f.name} ...]()`;
      insert(`\n${mark}\n`);
      try {
        const url = await upload(f);
        body().value = body().value.replace(mark, `![${f.name.replace(/\.[^.]+$/, "").replace(/[[\]()]/g, "")}](${url})`);
      } catch (err) {
        body().value = body().value.replace(`\n${mark}\n`, "\n");
        $("#ed-state").textContent = err.message;
      }
      preview();
    }
  }

  function preview() {
    $("#ed-preview").innerHTML = markdown(body().value) || `<p class="hint">The post, as it'll look.</p>`;
  }

  function setCover(url) {
    state.cover = url || null;
    $("#ed-cover-img").innerHTML = url ? `<img src="${esc(url)}" alt="">` : "<span>No cover picture</span>";
    $("#ed-cover-clear").hidden = !url;
  }

  function edit(p = null) {
    state.editing = p;
    $("#blog-list").hidden = true;
    $("#post").hidden = true;
    $("#editor").hidden = false;
    $("#ed-h").textContent = p ? "Edit the post" : "New post";
    $("#ed-title").value = p?.title || "";
    $("#ed-summary").value = p?.summary || "";
    body().value = p?.body || "";
    setCover(p?.cover || null);
    $("#ed-publish").textContent = p?.published_at ? "Update" : "Publish";
    $("#ed-draft").textContent = p?.published_at ? "Unpublish (draft)" : "Save as draft";
    $("#ed-delete").hidden = !p;
    $("#ed-state").textContent = "";
    preview();
    window.scrollTo(0, $("#editor").getBoundingClientRect().top + window.scrollY - 90);
    $("#ed-title").focus();
  }

  async function save(published) {
    const title = $("#ed-title").value.trim();
    if (!title) { $("#ed-state").textContent = "A post needs a title."; $("#ed-title").focus(); return; }
    if (body().value.includes("![Uploading")) { $("#ed-state").textContent = "A picture is still uploading."; return; }
    $("#ed-state").textContent = "Saving ...";
    try {
      const saved = await api("POST", "/v1/blog/posts", { slug: state.editing?.slug, title, summary: $("#ed-summary").value.trim(),
        body: body().value, cover: state.cover, published });
      await Promise.all([load(), loadDrafts()]);
      say(published ? "Published." : "Saved as a draft: only the blog's authors see it.");
      if (published) location.hash = saved.slug;
      else { state.drafts = state.drafts || []; location.hash = ""; showList(); }
    } catch (err) {
      $("#ed-state").textContent = err.message;
    }
  }

  async function loadDrafts() {
    if (!state.author) return;
    const all = (await api("GET", "/v1/blog/drafts")).posts;
    if (state.drafts) state.drafts = all;
    state.all = all;
  }

  function wireEditor() {
    $("#blog-tools").hidden = false;
    $("#blog-new").onclick = () => edit();
    $("#blog-drafts").onclick = async () => { await loadDrafts(); state.drafts = state.all || []; location.hash = ""; showList(); };
    $(".ed-bar").onclick = (e) => {
      const b = e.target.closest("button[data-md]");
      if (!b) return;
      const md = b.dataset.md;
      if (md === "b") insert("**", "**", "bold text");
      else if (md === "i") insert("*", "*", "italic text");
      else if (md === "h2") prefix("## ");
      else if (md === "ul") prefix("- ");
      else if (md === "quote") prefix("> ");
      else if (md === "hr") insert("\n---\n");
      else if (md === "img") $("#ed-file").click();
      else if (md === "link") {
        const url = prompt("Link to (an address):", "https://");
        if (url && url !== "https://") insert("[", `](${url})`, "link text");
      }
    };
    body().oninput = preview;
    body().onkeydown = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = e.key.toLowerCase();
      if (k === "b") { e.preventDefault(); insert("**", "**", "bold text"); }
      else if (k === "i") { e.preventDefault(); insert("*", "*", "italic text"); }
      else if (k === "k") { e.preventDefault(); $('.ed-bar [data-md="link"]').click(); }
    };
    body().onpaste = (e) => { const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); addPictures(files); } };
    const write = $(".ed-write");
    write.ondragover = (e) => { e.preventDefault(); write.classList.add("drop"); };
    write.ondragleave = () => write.classList.remove("drop");
    write.ondrop = (e) => { e.preventDefault(); write.classList.remove("drop"); addPictures(e.dataTransfer.files); };
    $("#ed-file").onchange = (e) => { addPictures(e.target.files); e.target.value = ""; };
    $("#ed-cover-pick").onclick = () => $("#ed-cover-file").click();
    $("#ed-cover-clear").onclick = () => setCover(null);
    $("#ed-cover-file").onchange = async (e) => {
      const f = e.target.files[0];
      e.target.value = "";
      if (!f) return;
      $("#ed-state").textContent = "Uploading the cover ...";
      try { setCover(await upload(f)); $("#ed-state").textContent = ""; } catch (err) { $("#ed-state").textContent = err.message; }
    };
    $("#editor").onsubmit = (e) => { e.preventDefault(); save(true); };
    $("#ed-draft").onclick = () => save(false);
    $("#ed-cancel").onclick = () => { location.hash = state.editing ? state.editing.slug : ""; route(); };
    $("#ed-delete").onclick = async () => {
      const p = state.editing;
      if (!p || !confirm(`Delete "${p.title}" for good?`)) return;
      try {
        await api("DELETE", `/v1/blog/posts/${encodeURIComponent(p.slug)}`);
        await Promise.all([load(), loadDrafts()]);
        say("Deleted.");
        location.hash = "";
        showList();
      } catch (err) { $("#ed-state").textContent = err.message; }
    };
  }

  async function start() {
    await load();
    try {
      const me = await api("GET", "/v1/me");  // signed in to the dashboard: an author gets the editor
      state.author = !!me.blog_author;
    } catch { /* not signed in: reading only */ }
    if (state.author) { wireEditor(); await loadDrafts().catch(() => {}); }
    route();
    window.addEventListener("hashchange", route);
  }

  window.BlogMarkdown = markdown;
  start();
})();
