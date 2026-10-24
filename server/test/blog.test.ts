import { describe, expect, it } from "vitest";
import { API, bearer, call, data, signIn } from "./helpers";
import { env } from "cloudflare:test";
import worker from "../src/index";

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

  it("refuses what isn't a picture, and a cover from elsewhere", async () => {
    const me = await signIn(AUTHOR);
    expect((await put(me.token, new TextEncoder().encode("<svg onload=alert(1)></svg>".padEnd(64)))).status).toBe(400);
    expect((await call("POST", "/v1/blog/posts", { title: "x", cover: "https://tracker.example/pixel.gif" }, bearer(me.token))).status).toBe(400);
  });
});
