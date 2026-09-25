import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import worker from "../src/index";
import { API, bearer, call, data, flight, signIn } from "./helpers";

const SITE = "https://localtc.test";
const QUOTE = { kind: "clearance", station: "San Diego Clearance", mhz: 125.9, text: "Frontier 2084, cleared to Phoenix via the PADRZ3 departure, then as filed" };

function png(size = 2000): ArrayBuffer {
  const bytes = new Uint8Array(size);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  return bytes.buffer;
}

async function putImage(token: string, slug: string, body: ArrayBuffer): Promise<Response> {
  return worker.fetch(new Request(`${API}/v1/shares/${slug}/image`, {
    method: "PUT", body, headers: { "Content-Type": "image/png", ...bearer(token) },
  }), env);
}

/** The public page, as a chat app unfurling the link would ask for it: no cookie, no token. */
async function view(path: string): Promise<Response> {
  return worker.fetch(new Request(`${SITE}${path}`), env);
}

async function shared(extra: Record<string, unknown> = {}) {
  const who = await signIn();
  await call("POST", "/v1/flights", { flights: [flight("f1")] }, bearer(who.token));
  const res = await call("POST", "/v1/shares", { kind: "flight", ref: "f1", quote: QUOTE, names: { KSAN: "San Diego", KPHX: "Phoenix" }, ...extra }, bearer(who.token));
  return { ...who, res, share: await data(res.clone()) };
}

describe("shared flights", () => {
  it("makes a public page with the card's title and picture, and nothing private", async () => {
    const { res, share, token, email } = await shared();
    expect(res.status).toBe(200);
    expect(share.slug).toMatch(/^[A-Za-z0-9]{10}$/);
    expect(share.slug).not.toContain("f1");
    expect(share.url).toBe(`${SITE}/f/${share.slug}`);

    let page = await view(`/f/${share.slug}`);
    expect(page.status).toBe(200);
    let html = await page.text();
    expect(html).toContain('property="og:title" content="FFT2084: San Diego (KSAN) to Phoenix (KPHX)"');
    expect(html).toContain(`content="${SITE}/og.png"`); // no card drawn yet
    expect(html).toContain('name="robots" content="noindex"');
    expect(html).toContain("PADRZ3");
    for (const secret of [email, "Gate 17", "Gate C14", "21:26", token]) expect(html).not.toContain(secret);
    expect(page.headers.get("Content-Security-Policy")).toContain("script-src https://localtc.test");

    expect((await putImage(token, share.slug, png())).status).toBe(200);
    html = await (await view(`/f/${share.slug}`)).text();
    expect(html).toContain(`content="${SITE}/f/${share.slug}.png"`);
    const image = await view(`/f/${share.slug}.png`);
    expect(image.headers.get("Content-Type")).toBe("image/png");
    expect((await image.arrayBuffer()).byteLength).toBe(2000);

    const list = await data(await call("GET", "/v1/flights", undefined, bearer(token)));
    expect(list.flights[0].share).toBe(share.url);
  });

  it("keeps the link when the quote changes, and loses it when unshared", async () => {
    const { share, token } = await shared();
    await putImage(token, share.slug, png());
    const again = await data(await call("POST", "/v1/shares", { kind: "flight", ref: "f1", quote: null }, bearer(token)));
    expect(again.slug).toBe(share.slug);
    expect(again.card.quote).toBeNull();
    expect((await view(`/f/${share.slug}.png`)).status).toBe(404); // the old card is gone: it had the old quote

    expect((await call("DELETE", `/v1/shares/${share.slug}`, undefined, bearer(token))).status).toBe(200);
    const gone = await view(`/f/${share.slug}`);
    expect(gone.status).toBe(404);
    expect(await gone.text()).toContain("No longer shared");
    const fresh = await data(await call("POST", "/v1/shares", { kind: "flight", ref: "f1" }, bearer(token)));
    expect(fresh.slug).not.toBe(share.slug); // shared again: a new link, the old one stays dead
  });

  it("goes with its flight and with the account", async () => {
    const { share, token, email } = await shared();
    await call("DELETE", "/v1/flights/f1", undefined, bearer(token));
    expect((await view(`/f/${share.slug}`)).status).toBe(404);

    const other = await shared();
    await call("DELETE", "/v1/me", { email: other.email }, bearer(other.token));
    expect((await view(`/f/${other.share.slug}`)).status).toBe(404);
    expect(email).not.toBe(other.email);
  });

  it("refuses what isn't this pilot's to share, or isn't a card", async () => {
    const { share, token } = await shared();
    const stranger = await signIn();
    expect((await call("POST", "/v1/shares", { kind: "flight", ref: "f1" }, bearer(stranger.token))).status).toBe(404);
    expect((await putImage(stranger.token, share.slug, png())).status).toBe(404);
    expect((await call("DELETE", `/v1/shares/${share.slug}`, undefined, bearer(stranger.token))).status).toBe(404);

    expect((await putImage(token, share.slug, new TextEncoder().encode("<svg>not a png</svg>".repeat(10)).buffer as ArrayBuffer)).status).toBe(400);
    expect((await putImage(token, share.slug, png(1024 * 1024 + 1))).status).toBe(413);
    // A quote is ATC talking to this flight, not any text at all.
    const spam = { kind: "clearance", station: "x", text: "Buy cheap watches at example.com" };
    expect((await call("POST", "/v1/shares", { kind: "flight", ref: "f1", quote: spam }, bearer(token))).status).toBe(400);
    expect((await call("POST", "/v1/shares", { kind: "flight", ref: "f1", quote: { ...QUOTE, kind: "script" } }, bearer(token))).status).toBe(400);
    const odd = await data(await call("POST", "/v1/shares", { kind: "flight", ref: "f1", names: { KSAN: "<script>", KPHX: "Phoenix" } }, bearer(token)));
    expect(odd.card.origin.name).toBe("");
    expect(odd.card.destination.name).toBe("Phoenix");
    expect((await view("/f/nope")).status).toBe(404);
  });

  it("escapes what the pilot typed", async () => {
    const who = await signIn();
    await call("POST", "/v1/flights", { flights: [flight("x", { callsign: "</script><b>", aircraft: '"><img src=x>' })] }, bearer(who.token));
    const share = await data(await call("POST", "/v1/shares", { kind: "flight", ref: "x" }, bearer(who.token)));
    const html = await (await view(`/f/${share.slug}`)).text();
    expect(html).not.toContain("</script><b>");
    expect(html).not.toContain('"><img');
  });
});

describe("moments of an uploaded replay", () => {
  it("are kept with the replay, for the phone to pick a quote from", async () => {
    const { token } = await signIn();
    await call("POST", "/v1/flights", { flights: [flight("m1")] }, bearer(token));
    expect(await data(await call("GET", "/v1/flights/m1/moments", undefined, bearer(token)))).toEqual({ moments: [], names: {} });
    const replay = {
      v: 1, flight: { started_at: "2026-09-20T21:26:00Z", duration_s: 60 },
      airports: { KSAN: { lat: 32.73, lon: -117.19, elev: 17, name: "San Diego" } },
      track: { t: [0], lat: [32.73], lon: [-117.19], alt: [17], gs: [0], hdg: [270], vs: [0], gnd: [1] },
      radio: [
        { kind: "pilot", t: 1, text: "cleared to Phoenix via the PADRZ3 departure" },
        { kind: "atc", t: 2, station: "San Diego Tower", mhz: 118.3, text: "Frontier 2084, runway 27, cleared for takeoff" },
        { kind: "atc", t: 3, station: "San Diego Clearance", mhz: 125.9, text: QUOTE.text },
      ],
    };
    const body = await new Response(new Blob([JSON.stringify(replay)]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer();
    await worker.fetch(new Request(`${API}/v1/flights/m1/replay`, { method: "PUT", body, headers: bearer(token) }), env);
    const got = await data(await call("GET", "/v1/flights/m1/moments", undefined, bearer(token)));
    expect(got.moments.map((m: any) => m.kind)).toEqual(["clearance", "takeoff"]);
    expect(got.names).toEqual({ KSAN: "San Diego" });
  });
});
