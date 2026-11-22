import { describe, expect, it } from "vitest";
import { BEAT_MIN_MS, IP_BEATS_PER_MIN, type Meta, STALE_MS, Sky, cleanSkyFlight } from "../src/sky";
import { env, runInDurableObject } from "cloudflare:test";
import { bearer, call, data, signIn } from "./helpers";

const cruise = {
  callsign: "ACA8272", type: "A321", from: "CYVR", to: "KSEA", from_ll: [49.1939, -123.1844], to_ll: [47.449, -122.309], route: "PAE3 ARRIE", rules: "IFR", phase: "cruise",
  lat: 48.12345678, lon: -122.98765432, alt: 13000, gs: 310, hdg: 152, ground: false, station: "Seattle Center", mhz: 132.6,
};
const meta: Meta = { name: "Duck", hideRoute: false, hideName: false, delayMin: 0 };

async function sharing(mode = "always", extra: Record<string, unknown> = {}) {
  const pilot = await signIn();
  const res = await call("PATCH", "/v1/me", { live_share: mode, display_name: "Duck", ...extra }, bearer(pilot.token));
  expect(res.status).toBe(200);
  return pilot;
}

const map = async () => (await data(await call("GET", "/v1/sky/flights"))).flights as Record<string, unknown>[];

describe("Live Flights' heartbeat", () => {
  it("is off by default: nothing is taken", async () => {
    const { token } = await signIn();
    const me = await data(await call("GET", "/v1/me", undefined, bearer(token)));
    expect(me.live_share).toBe("off");
    const res = await call("PUT", "/v1/sky/flight", cruise, bearer(token));
    expect(res.status).toBe(403);
    expect((await map()).some((f) => f.callsign === "ACA8272" && f.name === "Duck")).toBe(false);
  });

  it("needs the account", async () => {
    expect((await call("PUT", "/v1/sky/flight", cruise)).status).toBe(401);
  });

  it("shows a shared flight on the map, with only the shared fields", async () => {
    const { token } = await sharing();
    const res = await data(await call("PUT", "/v1/sky/flight", { ...cruise, email: "x@y.z", voice: "...", secret: 1 }, bearer(token)));
    expect(res.ok).toBe(true);
    const f = (await map()).find((x) => x.id === res.flight_id)!;
    expect(f.name).toBe("Duck");
    expect(f.lat).toBe(48.1235);
    expect(f.route).toBe("PAE3 ARRIE");
    expect(Object.keys(f).sort()).toEqual(["alt", "callsign", "delay_min", "from", "from_ll", "ground", "gs", "hdg", "id", "lat", "lon",
      "mhz", "name", "phase", "route", "rules", "station", "to", "to_ll", "type"]);
    expect(JSON.stringify(await map())).not.toMatch(/@example\.com|voice|secret/);
  });

  it("hides the route and the name when the pilot asks", async () => {
    const { token } = await sharing("always", { live_hide_route: true, live_hide_name: true });
    const { flight_id } = await data(await call("PUT", "/v1/sky/flight", cruise, bearer(token)));
    const f = (await map()).find((x) => x.id === flight_id)!;
    expect(f.name).toBe("LocalTC pilot");
    expect(f).not.toHaveProperty("route");
  });

  it("comes off the map when stopped, and 'this flight only' ends with the flight", async () => {
    const { token } = await sharing("flight");
    const { flight_id } = await data(await call("PUT", "/v1/sky/flight", cruise, bearer(token)));
    expect((await map()).some((f) => f.id === flight_id)).toBe(true);
    const stop = await data(await call("DELETE", "/v1/sky/flight", { ended: true }, bearer(token)));
    expect(stop.mode).toBe("off");
    expect((await map()).some((f) => f.id === flight_id)).toBe(false);
    expect((await data(await call("GET", "/v1/me", undefined, bearer(token)))).live_share).toBe("off");
  });

  it("comes off the map at once when sharing is turned off on the dashboard", async () => {
    const { token } = await sharing();
    const { flight_id } = await data(await call("PUT", "/v1/sky/flight", cruise, bearer(token)));
    await call("PATCH", "/v1/me", { live_share: "off" }, bearer(token));
    expect((await map()).some((f) => f.id === flight_id)).toBe(false);
    expect((await call("PUT", "/v1/sky/flight", cruise, bearer(token))).status).toBe(403);
  });

  it("checks what it's sent", async () => {
    const { token } = await sharing();
    expect((await call("PUT", "/v1/sky/flight", { ...cruise, lat: 91 }, bearer(token))).status).toBe(400);
    expect((await call("PUT", "/v1/sky/flight", { ...cruise, callsign: "<b>" }, bearer(token))).status).toBe(400);
    expect((await call("PATCH", "/v1/me", { live_share: "everyone" }, bearer(token))).status).toBe(400);
    expect((await call("PATCH", "/v1/me", { live_delay_min: 7 }, bearer(token))).status).toBe(400);
  });

  it("lets an admin take a flight down and turn an account's sharing off", async () => {
    const pilot = await sharing();
    const { flight_id } = await data(await call("PUT", "/v1/sky/flight", cruise, bearer(pilot.token)));
    const other = await signIn();
    expect((await call("DELETE", `/v1/sky/flights/${flight_id}`, undefined, bearer(other.token))).status).toBe(403);
    const admin = await signIn("admin@example.com");
    expect((await call("POST", "/v1/sky/ban", { flight_id }, bearer(admin.token))).status).toBe(200);
    expect((await map()).some((f) => f.id === flight_id)).toBe(false);
    expect((await call("PUT", "/v1/sky/flight", cruise, bearer(pilot.token))).status).toBe(403);
    expect((await call("PATCH", "/v1/me", { live_share: "always" }, bearer(pilot.token))).status).toBe(403);
    expect((await data(await call("GET", "/v1/me", undefined, bearer(pilot.token)))).live_blocked).toBe(true);
  });
});

describe("cleaning a heartbeat", () => {
  it("clamps numbers and drops what doesn't fit", () => {
    const f = cleanSkyFlight({ ...cruise, alt: 99999, gs: -5, hdg: 725, mhz: 99.1, phase: "hacking", route: "DCT <x>",
      station: "<script>", type: "TOOLONG", extra: 1 });
    expect(f.alt).toBe(60000);
    expect(f.gs).toBe(0);
    expect(f.hdg).toBe(5);
    expect(f).not.toHaveProperty("mhz");
    expect(f).not.toHaveProperty("phase");
    expect(f).not.toHaveProperty("route");
    expect(f).not.toHaveProperty("station");
    expect(f.type).toBe("TOOL");
    expect(f).not.toHaveProperty("extra");
  });
});

describe("the map's memory", () => {
  const flight = cleanSkyFlight(cruise);

  it("forgets a flight 90 seconds after its last heartbeat", () => {
    const sky = new Sky();
    sky.beat("u1", "ip", meta, flight, 0);
    expect(sky.snapshot(STALE_MS).flights).toHaveLength(1);
    expect(sky.snapshot(STALE_MS + 1).flights).toHaveLength(0);
  });

  it("keeps one flight per account", () => {
    const sky = new Sky();
    const a = sky.beat("u1", "ip", meta, flight, 0);
    expect(sky.beat("u1", "ip", meta, flight, 10_000)).toBe(a);
    const b = sky.beat("u1", "ip", meta, { ...flight, callsign: "ACA8273" }, 20_000);
    expect(b).not.toBe(a);
    expect(sky.snapshot(20_000).flights.map((f) => f.id)).toEqual([b]);
  });

  it("shows a delayed flight where it was, and not before the delay has passed", () => {
    const sky = new Sky();
    const d = { ...meta, delayMin: 5 };
    for (let t = 0; t <= 6 * 60_000; t += 10_000) sky.beat("u1", "ip", d, { ...flight, lat: 40 + t / 60_000 }, t);
    const f = sky.snapshot(6 * 60_000).flights[0];
    expect(f.lat).toBe(41);
    expect(f.delay_min).toBe(5);
    const fresh = new Sky();
    fresh.beat("u2", "ip", d, flight, 0);
    expect(fresh.snapshot(60_000).flights).toHaveLength(0);
  });

  it("limits how often an account and an address may send", () => {
    const sky = new Sky();
    sky.beat("u1", "ip", meta, flight, 0);
    expect(() => sky.beat("u1", "ip", meta, flight, BEAT_MIN_MS - 1)).toThrow(/Too many/);
    for (let i = 0; i < IP_BEATS_PER_MIN - 1; i++) sky.beat(`v${i}`, "shared", meta, flight, 1000);
    sky.beat("w", "shared", meta, flight, 1000);
    expect(() => sky.beat("x", "shared", meta, flight, 1000)).toThrow(/address/);
  });
});

describe("the map surviving the room's restart", () => {
  const flight = cleanSkyFlight(cruise);

  it("saves what changed and what left, and starts again from it", () => {
    const sky = new Sky();
    const id = sky.beat("u1", "ip", meta, flight, 0);
    const first = sky.changes();
    expect(first.put.map((h) => h.id)).toEqual([id]);
    expect(sky.changes()).toEqual({ put: [], del: [] });
    const restarted = new Sky();
    restarted.restore(first.put, 30_000);
    expect(restarted.snapshot(30_000).flights.map((f) => f.id)).toEqual([id]);
    restarted.stop("u1");
    expect(restarted.changes().del).toEqual([id]);
  });

  it("keeps nothing in its storage once the flight is off the map", async () => {
    const { token } = await sharing();
    const { flight_id } = await data(await call("PUT", "/v1/sky/flight", cruise, bearer(token)));
    const room = env.SKY.get(env.SKY.idFromName("public"));
    const stored = () => runInDurableObject(room, async (_o, state) => (await state.storage.list({ prefix: "f:" })).has(`f:${flight_id}`));
    expect(await stored()).toBe(true);
    await call("DELETE", "/v1/sky/flight", {}, bearer(token));
    expect(await stored()).toBe(false);
  });
});
