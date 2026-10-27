import { describe, expect, it } from "vitest";
import { API, bearer, call, data, signIn } from "./helpers";
import { env } from "cloudflare:test";
import worker from "../src/index";
import { outbox } from "../src/email";

const AUTHOR = "duckiesty@gmail.com";
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, ...new Array(80).fill(0)]);

async function put(token: string, body: Uint8Array): Promise<Response> {
  return worker.fetch(new Request(`${API}/v1/blog/images`, { method: "PUT", body, headers: { ...bearer(token), "Content-Type": "image/png" } }), env);
}

describe("the blog", () => {
  it("lets only its author write, and anyone read what's published", async () => {
    const me = await signIn(AUTHOR);
    const other = await signIn();
    expect((await data(await call("GET", "/v1/me", undefined, bearer(me.token)))).blog_author).toBe(true);
    expect((await data(await call("GET", "/v1/me", undefined, bearer(other.token)))).blog_author).toBe(false);
    expect((await call("POST", "/v1/blog/posts", { title: "Hi" }, bearer(other.token))).status).toBe(403);
    expect((await put(other.token, PNG)).status).toBe(403);
    expect((await call("GET", "/v1/blog/drafts", undefined, bearer(other.token))).status).toBe(403);

    const draft = await data(await call("POST", "/v1/blog/posts", { title: "LocalTC 0.5: the copilot's hands", summary: "What's new.", body: "## New\nHello." }, bearer(me.token)));
    expect(draft.slug).toBe("localtc-0-5-the-copilot-s-hands");
    expect((await data(await call("GET", "/v1/blog"))).posts).toEqual([]);  // a draft: not public
    expect((await call("GET", `/v1/blog/posts/${draft.slug}`)).status).toBe(404);

    const img = await data(await put(me.token, PNG));
    expect(img.url).toMatch(/\/v1\/blog\/images\/[a-f0-9]{32}$/);
    const served = await call("GET", `/v1/blog/images/${img.id}`);
    expect(served.headers.get("Content-Type")).toBe("image/png");
    expect(new Uint8Array(await served.arrayBuffer())).toEqual(PNG);  // the picture itself, every byte

    await call("POST", "/v1/blog/posts", { slug: draft.slug, title: "LocalTC 0.5", body: `![A320](${img.url})`, cover: img.url, published: true }, bearer(me.token));
    const posts = (await data(await call("GET", "/v1/blog"))).posts;
    expect(posts.map((p: any) => [p.slug, p.title, p.cover])).toEqual([[draft.slug, "LocalTC 0.5", img.url]]);
    expect((await data(await call("GET", `/v1/blog/posts/${draft.slug}`))).body).toContain(img.url);

    const second = await data(await call("POST", "/v1/blog/posts", { title: "LocalTC 0.5: the copilot's hands", published: true }, bearer(me.token)));
    expect(second.slug).toBe(`${draft.slug}-2`);  // the same title as the first: its own address
    expect((await call("DELETE", `/v1/blog/posts/${second.slug}`, undefined, bearer(other.token))).status).toBe(403);
    expect((await call("DELETE", `/v1/blog/posts/${second.slug}`, undefined, bearer(me.token))).status).toBe(200);
  });

  it("lets the authors change and delete the site's own posts, without emailing anyone", async () => {
    const me = await signIn(AUTHOR);
    const fan = await signIn();
    await call("PUT", "/v1/blog/follow", {}, bearer(fan.token));
    outbox.length = 0;
    // A change to a post written in the repository: saved here under its slug, shown in its place, no email.
    const changed = await data(await call("POST", "/v1/blog/posts", { slug: "localtc-0-5-0-is-out", title: "LocalTC 0.5.0 is out!",
      body: "Edited.", cover: "img/hero-runway.jpg", published: true, quiet: true, published_at: "2026-10-27T12:00:00Z" }, bearer(me.token)));
    expect(changed.slug).toBe("localtc-0-5-0-is-out");
    expect(changed.published_at).toBe("2026-10-27T12:00:00Z");  // its own date, not the edit's
    let blog = await data(await call("GET", "/v1/blog"));
    expect(blog.posts.map((p: any) => p.slug)).toContain("localtc-0-5-0-is-out");
    expect(blog.hidden).not.toContain("localtc-0-5-0-is-out");
    expect(outbox.length).toBe(0);
    // Deleted: gone from here and hidden, so the repository's copy doesn't come back; the same for one never changed.
    expect((await call("DELETE", "/v1/blog/posts/localtc-0-5-0-is-out", undefined, bearer(me.token))).status).toBe(200);
    expect((await call("DELETE", "/v1/blog/posts/welcome-to-the-blog", undefined, bearer(me.token))).status).toBe(200);
    expect((await call("DELETE", "/v1/blog/posts/welcome-to-the-blog", undefined, bearer(fan.token))).status).toBe(403);
    blog = await data(await call("GET", "/v1/blog"));
    expect(blog.posts.map((p: any) => p.slug)).not.toContain("localtc-0-5-0-is-out");
    expect(blog.hidden).toEqual(expect.arrayContaining(["localtc-0-5-0-is-out", "welcome-to-the-blog"]));
    // Written again: shown again.
    await call("POST", "/v1/blog/posts", { slug: "welcome-to-the-blog", title: "Welcome back", published: true, quiet: true }, bearer(me.token));
    expect((await data(await call("GET", "/v1/blog"))).hidden).not.toContain("welcome-to-the-blog");
  });

  it("refuses what isn't a picture, and a cover from elsewhere", async () => {
    const me = await signIn(AUTHOR);
    expect((await put(me.token, new TextEncoder().encode("<svg onload=alert(1)></svg>".padEnd(64)))).status).toBe(400);
    expect((await call("POST", "/v1/blog/posts", { title: "x", cover: "https://tracker.example/pixel.gif" }, bearer(me.token))).status).toBe(400);
  });

  it("counts readers and reactions once per browser, for anyone", async () => {
    const me1 = { "CF-Connecting-IP": "203.0.113.7", "User-Agent": "Reader" };
    const r1 = await data(await call("POST", "/v1/blog/posts/welcome-to-the-blog/read", {}, me1));
    expect(r1).toEqual({ reactions: {}, mine: [] });
    await call("POST", "/v1/blog/posts/welcome-to-the-blog/read", {}, me1);  // again: still one reader
    await call("POST", "/v1/blog/posts/welcome-to-the-blog/read", {}, { "CF-Connecting-IP": "203.0.113.8" });
    let r = await data(await call("POST", "/v1/blog/posts/welcome-to-the-blog/react", { kind: "love" }, me1));
    await call("POST", "/v1/blog/posts/welcome-to-the-blog/react", { kind: "love" }, me1);
    expect(r).toEqual({ reactions: { love: 1 }, mine: ["love"] });
    expect((await call("POST", "/v1/blog/posts/welcome-to-the-blog/react", { kind: "angry" }, me1)).status).toBe(400);
    const stored = await env.DB.prepare("SELECT viewer FROM blog_reads").all<{ viewer: string }>();
    expect(stored.results.every((x) => /^[a-f0-9]{64}$/.test(x.viewer) && !x.viewer.includes("203"))).toBe(true);
    const me = await signIn(AUTHOR);
    const drafts = await data(await call("GET", "/v1/blog/drafts", undefined, bearer(me.token)));
    expect(drafts.reads["welcome-to-the-blog"]).toBe(2);
    expect(drafts.reactions["welcome-to-the-blog"]).toEqual({ love: 1 });
    r = await data(await call("POST", "/v1/blog/posts/welcome-to-the-blog/react", { kind: "love", on: false }, me1));
    expect(r).toEqual({ reactions: {}, mine: [] });
  });

  it("emails a new post to its followers once, and unfollows from the email's link", async () => {
    const reader = await signIn();
    expect((await call("GET", "/v1/blog/follow")).status).toBe(401);
    expect((await data(await call("PUT", "/v1/blog/follow", undefined, bearer(reader.token)))).following).toBe(true);
    const me = await signIn(AUTHOR);
    const before = outbox.length;
    await call("POST", "/v1/blog/posts", { title: "Followers hear first", summary: "News.", published: true }, bearer(me.token));
    const mail = outbox.slice(before).find((e) => e.to === reader.email)!;
    expect(mail.subject).toBe("New on the LocalTC blog: Followers hear first");
    expect(mail.from).toContain("marketing@localtc.tech");
    await call("POST", "/v1/blog/posts", { slug: "followers-hear-first", title: "Followers hear first", body: "edited", published: true }, bearer(me.token));
    expect(outbox.slice(before).filter((e) => e.to === reader.email)).toHaveLength(1);  // an edit isn't news
    const link = mail.text.match(/\/v1\/blog\/unfollow\/[a-f0-9]{48}/)![0];
    expect((await call("GET", link)).status).toBe(200);
    expect((await data(await call("GET", "/v1/blog/follow", undefined, bearer(reader.token)))).following).toBe(false);
  });
});
