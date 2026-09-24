import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import worker from "../src/index";
import { API, bearer, call, data, flight, signIn } from "./helpers";

function replay(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    v: 1,
    flight: { id: "", callsign: "DAL2543", aircraft: "A220-300", origin: "KDEN", destination: "KSEA", started_at: "2026-09-23T22:18:20Z",
      zulu0: 80302, duration_s: 600 },
    airports: { KDEN: { lat: 39.86, lon: -104.67, elev: 5434 }, KSEA: { lat: 47.45, lon: -122.31, elev: 433 } },
    route: [{ ident: "MUGBE", lat: 39.93, lon: -104.9 }],
    track: { t: [0, 5, 10], lat: [39.86, 39.87, 39.88], lon: [-104.67, -104.68, -104.69], alt: [5430, 5430, 6000],
      gs: [0, 20, 150], hdg: [250, 250, 251], vs: [0, 0, 2000], gnd: [1, 1, 0] },
    radio: [
      { kind: "pilot", t: 1, text: "Denver Clearance, Delta 2543, IFR to Seattle", ok: true },
      { kind: "atc", t: 3, station: "Denver Clearance", mhz: 118.75, text: "Delta 2543, cleared to Seattle ..." },
    ],
    marks: [{ t: 9, kind: "takeoff", text: "Takeoff" }],
    ...extra,
  };
}

async function gz(value: unknown): Promise<ArrayBuffer> {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return new Response(new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer();
}

async function put(token: string, id: string, body: ArrayBuffer): Promise<Response> {
  return worker.fetch(new Request(`${API}/v1/flights/${id}/replay`, {
    method: "PUT", body, headers: { "Content-Type": "application/gzip", ...bearer(token) },
  }), env);
}

describe("replays", () => {
  it("uploads, plays back, shows in the logbook and deletes", async () => {
    const { token } = await signIn();
    await call("POST", "/v1/flights", { flights: [flight("r1"), flight("r2", { started_at: "2026-09-19T10:00:00Z" })] }, bearer(token));
    expect((await put(token, "r1", await gz(replay()))).status).toBe(200);
    const got = await data(await call("GET", "/v1/flights/r1/replay", undefined, bearer(token)));
    expect(got.flight.id).toBe("r1");
    expect(got.track.alt).toEqual([5430, 5430, 6000]);
    expect(got.radio[0].ok).toBe(true);
    const list = await data(await call("GET", "/v1/flights", undefined, bearer(token)));
    expect(Object.fromEntries(list.flights.map((f: any) => [f.id, f.has_replay]))).toEqual({ r1: true, r2: false });
    expect((await call("DELETE", "/v1/flights/r1/replay", undefined, bearer(token))).status).toBe(200);
    expect((await call("GET", "/v1/flights/r1/replay", undefined, bearer(token))).status).toBe(404);
  });

  it("needs the flight in the account, and keeps each account's replays to itself", async () => {
    const a = await signIn();
    const b = await signIn();
    expect((await put(a.token, "nope", await gz(replay()))).status).toBe(404);
    await call("POST", "/v1/flights", { flights: [flight("mine")] }, bearer(a.token));
    await put(a.token, "mine", await gz(replay()));
    expect((await call("GET", "/v1/flights/mine/replay", undefined, bearer(b.token))).status).toBe(404);
    expect((await call("DELETE", "/v1/flights/mine/replay", undefined, bearer(b.token))).status).toBe(404);
  });

  it("drops what it doesn't know and refuses what's broken", async () => {
    const { token } = await signIn();
    await call("POST", "/v1/flights", { flights: [flight("x")] }, bearer(token));
    const extra = replay({ audio: "base64...", llm: [{ prompt: "p" }] });
    (extra.radio as any[])[0].audio_ref = "audio/0001.wav";
    expect((await put(token, "x", await gz(extra))).status).toBe(200);
    const got = await data(await call("GET", "/v1/flights/x/replay", undefined, bearer(token)));
    expect(got).not.toHaveProperty("audio");
    expect(got).not.toHaveProperty("llm");
    expect(got.radio[0]).not.toHaveProperty("audio_ref");

    const short = replay();
    (short.track as any).lat = [39.86];
    expect((await put(token, "x", await gz(short))).status).toBe(400);
    expect((await put(token, "x", await gz(replay({ v: 2 })))).status).toBe(400);
    expect((await put(token, "x", await gz(replay({ radio: [{ kind: "shell", t: 0, text: "" }] })))).status).toBe(400);
    expect((await put(token, "x", await gz("not json"))).status).toBe(400);
    expect((await put(token, "x", new TextEncoder().encode("not gzip").buffer as ArrayBuffer)).status).toBe(400);
    expect((await put(token, "x", await gz("x".repeat(9 * 1024 * 1024)))).status).toBe(413);
  });

  it("goes with its flight, and with the account", async () => {
    const { token, email } = await signIn();
    await call("POST", "/v1/flights", { flights: [flight("f1"), flight("f2")] }, bearer(token));
    await put(token, "f1", await gz(replay()));
    await put(token, "f2", await gz(replay()));
    const exported = await data(await call("GET", "/v1/export", undefined, bearer(token)));
    expect(exported.replays.map((r: any) => r.flight.id)).toEqual(["f1", "f2"]);
    await call("DELETE", "/v1/flights/f1", undefined, bearer(token));
    const user = await env.DB.prepare("SELECT id FROM users WHERE email = ?1").bind(email).first<{ id: string }>();
    const count = async () => (await env.DB.prepare("SELECT COUNT(*) AS n FROM replays WHERE user_id = ?1").bind(user!.id).first<{ n: number }>())!.n;
    expect(await count()).toBe(1);
    await call("DELETE", "/v1/me", { email }, bearer(token));
    expect(await count()).toBe(0);
  });
});
