import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { visitTally } from "../src/flights";
import { bearer, call, data, flight, signIn } from "./helpers";

describe("the logbook", () => {
  it("uploads, lists, totals and deletes", async () => {
    const { token } = await signIn();
    const up = await call("POST", "/v1/flights", { flights: [flight("a"), flight("b", { started_at: "2026-09-18T15:00:00Z", origin: "KPAE", destination: "KBFI" })] }, bearer(token));
    expect(await data(up)).toEqual({ accepted: ["a", "b"] });
    const list = await data(await call("GET", "/v1/flights", undefined, bearer(token)));
    expect(list.flights.map((f: any) => f.id)).toEqual(["a", "b"]);
    expect(list.flights[0].landed).toBe(true);
    const stats = await data(await call("GET", "/v1/stats", undefined, bearer(token)));
    expect(stats.flights).toBe(2);
    expect(stats.airports.map((a: any) => a.icao).sort()).toEqual(["KBFI", "KPAE", "KPHX", "KSAN"]);
    expect(stats.air_hours).toBeCloseTo(1.7, 1);
    expect(stats.airports.find((a: any) => a.icao === "KSAN").lat).toBeCloseTo(32.73, 1);
    expect((await call("DELETE", "/v1/flights/a", undefined, bearer(token))).status).toBe(200);
    expect((await data(await call("GET", "/v1/flights", undefined, bearer(token)))).flights).toHaveLength(1);
  });

  it("uploading the same flight twice keeps one", async () => {
    const { token } = await signIn();
    await call("POST", "/v1/flights", { flights: [flight("same")] }, bearer(token));
    await call("POST", "/v1/flights", { flights: [flight("same", { arrival_gate: "Gate B7" })] }, bearer(token));
    const list = await data(await call("GET", "/v1/flights", undefined, bearer(token)));
    expect(list.flights).toHaveLength(1);
    expect(list.flights[0].arrival_gate).toBe("Gate B7");
  });

  it("keeps each account's flights to itself", async () => {
    const a = await signIn();
    const b = await signIn();
    await call("POST", "/v1/flights", { flights: [flight("mine")] }, bearer(a.token));
    expect((await data(await call("GET", "/v1/flights", undefined, bearer(b.token)))).flights).toEqual([]);
    expect((await call("DELETE", "/v1/flights/mine", undefined, bearer(b.token))).status).toBe(404);
  });

  it("drops fields it doesn't know and refuses broken ones", async () => {
    const { token } = await signIn();
    await call("POST", "/v1/flights", { flights: [flight("x", { lat: 33.4, track: [[1, 2]] })] }, bearer(token));
    const row = await env.DB.prepare("SELECT * FROM flights WHERE id = 'x'").first();
    expect(row).not.toHaveProperty("lat");
    expect((await call("POST", "/v1/flights", { flights: [flight("y", { air_min: -5 })] }, bearer(token))).status).toBe(400);
    expect((await call("POST", "/v1/flights", { flights: [flight("z", { started_at: "yesterday" })] }, bearer(token))).status).toBe(400);
    expect((await call("POST", "/v1/flights", { flights: Array.from({ length: 101 }, (_, i) => flight(`f${i}`)) }, bearer(token))).status).toBe(400);
  });

  it("pages through a long logbook", async () => {
    const { token } = await signIn();
    const many = Array.from({ length: 30 }, (_, i) => flight(`p${i}`, { started_at: `2026-08-${String(i + 1).padStart(2, "0")}T10:00:00Z` }));
    await call("POST", "/v1/flights", { flights: many }, bearer(token));
    const first = await data(await call("GET", "/v1/flights?limit=20", undefined, bearer(token)));
    expect(first.flights).toHaveLength(20);
    const second = await data(await call("GET", `/v1/flights?limit=20&before=${first.next}`, undefined, bearer(token)));
    expect(second.flights).toHaveLength(10);
    expect(second.next).toBeNull();
  });

  it("exports everything, and deleting the account removes everything", async () => {
    const { email, token } = await signIn();
    await call("POST", "/v1/flights", { flights: [flight("gone")] }, bearer(token));
    const exported = await data(await call("GET", "/v1/export", undefined, bearer(token)));
    expect(exported.account.email).toBe(email);
    expect(exported.flights).toHaveLength(1);
    expect((await call("DELETE", "/v1/me", { email: "someone-else@example.com" }, bearer(token))).status).toBe(400);
    expect((await call("DELETE", "/v1/me", { email }, bearer(token))).status).toBe(200);
    for (const table of ["users", "flights", "sessions", "logins"]) {
      const n = await env.DB.prepare(`SELECT COUNT(*) AS n FROM ${table} WHERE ${table === "users" ? "email" : "user_id"} = ?1`)
        .bind(table === "users" ? email : "x").first<{ n: number }>();
      expect(n!.n).toBe(0);
    }
    expect((await env.DB.prepare("SELECT COUNT(*) AS n FROM flights WHERE id = 'gone'").first<{ n: number }>())!.n).toBe(0);
    expect((await call("GET", "/v1/me", undefined, bearer(token))).status).toBe(401);
  });

  it("keeps the metrics and shows the Stats page only with Stats and metrics on, and deletes them when it's off", async () => {
    const { token } = await signIn();
    const metrics = JSON.stringify({ north_lat: 54.5, equator_crossings: 1, night_landing: true, secret: "no", max_gs_kt: 99999 });
    await call("POST", "/v1/flights", { flights: [flight("x", { metrics })] }, bearer(token));
    expect(await data(await call("GET", "/v1/stats/details", undefined, bearer(token)))).toEqual({ enabled: false });
    await call("POST", "/v1/flights", { flights: [flight("x", { metrics })], stats: true }, bearer(token));
    const on = await data(await call("GET", "/v1/stats/details", undefined, bearer(token)));
    expect(on.enabled).toBe(true);
    expect(on.flights[0].metrics).toEqual({ north_lat: 54.5, equator_crossings: 1, night_landing: true });  // checked, nothing else
    expect((await data(await call("GET", "/v1/me", undefined, bearer(token)))).stats).toBe(true);
    await call("POST", "/v1/flights", { flights: [], stats: false }, bearer(token));
    expect(await data(await call("GET", "/v1/stats/details", undefined, bearer(token)))).toEqual({ enabled: false });
    const row = await env.DB.prepare("SELECT metrics FROM flights WHERE id = 'x'").first<{ metrics: string | null }>();
    expect(row?.metrics).toBeNull();
  });

  it("counts flying in and out again as one visit", () => {
    expect(visitTally([{ origin: "KLAX", destination: "KJFK" }, { origin: "KJFK", destination: "EGLL" }, { origin: "EGLL", destination: "KJFK" }]))
      .toEqual([["KJFK", 2], ["EGLL", 1], ["KLAX", 1]]);
  });
});
