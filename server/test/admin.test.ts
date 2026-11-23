import { describe, expect, it } from "vitest";
import { bearer, call, data, signIn } from "./helpers";

const cruise = {
  callsign: "FFT38", type: "A21N", airline: "F9", from: "MDPC", to: "TJSJ", rules: "IFR", phase: "climb",
  lat: 18.6, lon: -68.2, alt: 10972, gs: 341, hdg: 132, ground: false,
  fixes: [{ ident: "MDPC", lat: 18.567, lon: -68.363, kind: "apt" }, { ident: "MELLA", lat: 18.7, lon: -67.9 },
    { ident: "TJSJ", lat: 18.439, lon: -66.002, kind: "apt" }],
};

describe("the admin panel", () => {
  it("is only for the admins", async () => {
    const pilot = await signIn();
    for (const [m, p] of [["GET", "/v1/admin/overview"], ["GET", "/v1/admin/users"], ["GET", "/v1/admin/live"]] as const) {
      expect((await call(m, p, undefined, bearer(pilot.token))).status).toBe(403);
    }
    expect((await data(await call("GET", "/v1/me", undefined, bearer(pilot.token)))).admin).toBe(false);
    const admin = await signIn("admin@example.com");
    expect((await data(await call("GET", "/v1/me", undefined, bearer(admin.token)))).admin).toBe(true);
    const o = await data(await call("GET", "/v1/admin/overview", undefined, bearer(admin.token)));
    expect(o.users).toBeGreaterThan(0);
  });

  it("finds an account, tags it, and shows the tags on its Live Flights card", async () => {
    const pilot = await signIn();
    await call("PATCH", "/v1/me", { live_share: "always", display_name: "Duckiest" }, bearer(pilot.token));
    const { flight_id, need_fixes } = await data(await call("PUT", "/v1/sky/flight", cruise, bearer(pilot.token)));
    expect(need_fixes).toBe(false);
    const admin = await signIn("admin@example.com");
    const found = await data(await call("GET", `/v1/admin/users?q=${encodeURIComponent(pilot.email)}`, undefined, bearer(admin.token)));
    expect(found.users).toHaveLength(1);
    const id = found.users[0].id;
    const bad = await call("PATCH", `/v1/admin/users/${id}`, { tags: [{ label: "<b>x</b>", color: "red" }] }, bearer(admin.token));
    expect(bad.status).toBe(400);
    const ok = await data(await call("PATCH", `/v1/admin/users/${id}`, { tags: [{ label: "Streamer", color: "purple" }] }, bearer(admin.token)));
    expect(ok.tags).toEqual([{ label: "Streamer", color: "purple" }]);
    const shown = (await data(await call("GET", "/v1/sky/flights"))).flights.find((f: any) => f.id === flight_id);
    expect(shown.tags).toEqual([{ label: "Streamer", color: "purple" }]);
    expect(shown.airline).toBe("F9");
    expect(shown.has_route).toBe(true);
    expect(shown).not.toHaveProperty("fixes");
    const route = await data(await call("GET", `/v1/sky/flights/${flight_id}/route`));
    expect(route.fixes.map((f: any) => f.ident)).toEqual(["MDPC", "MELLA", "TJSJ"]);
    expect(route.trail).toEqual([[18.6, -68.2]]);
  });

  it("pauses Live Flights for everyone, and turns it back on", async () => {
    const pilot = await signIn();
    await call("PATCH", "/v1/me", { live_share: "always" }, bearer(pilot.token));
    await call("PUT", "/v1/sky/flight", cruise, bearer(pilot.token));
    const admin = await signIn("admin@example.com");
    const paused = await data(await call("PUT", "/v1/admin/live", { paused: true }, bearer(admin.token)));
    expect(paused.paused).toBe(true);
    expect(paused.flights).toHaveLength(0);
    expect((await call("PUT", "/v1/sky/flight", { ...cruise, lat: 18.7 }, bearer(pilot.token))).status).toBe(503);
    expect((await data(await call("GET", "/v1/me", undefined, bearer(pilot.token)))).live_paused).toBe(true);
    await call("PUT", "/v1/admin/live", { paused: false }, bearer(admin.token));
    expect((await data(await call("GET", "/v1/me", undefined, bearer(pilot.token)))).live_paused).toBe(false);
  });

  it("signs an account out everywhere, and deletes it only with its address typed", async () => {
    const pilot = await signIn();
    const admin = await signIn("admin@example.com");
    const id = (await data(await call("GET", `/v1/admin/users?q=${encodeURIComponent(pilot.email)}`, undefined, bearer(admin.token)))).users[0].id;
    await call("POST", `/v1/admin/users/${id}/signout`, undefined, bearer(admin.token));
    expect((await call("GET", "/v1/me", undefined, bearer(pilot.token))).status).toBe(401);
    expect((await call("DELETE", `/v1/admin/users/${id}`, { email: "wrong@example.com" }, bearer(admin.token))).status).toBe(400);
    expect((await call("DELETE", `/v1/admin/users/${id}`, { email: pilot.email }, bearer(admin.token))).status).toBe(200);
    expect((await call("GET", `/v1/admin/users/${id}`, undefined, bearer(admin.token))).status).toBe(404);
  });

  it("shows whether the app's Discord Rich Presence is on, as its sync says, and sorts by any column", async () => {
    const on = await signIn(`aaa-${crypto.randomUUID()}@example.com`);
    const off = await signIn(`zzz-${crypto.randomUUID()}@example.com`);
    await call("POST", "/v1/flights", { flights: [], stats: false, discord: true }, bearer(on.token));
    await call("POST", "/v1/flights", { flights: [], stats: false, discord: false }, bearer(off.token));
    const admin = await signIn("admin@example.com");
    const find = async (sort: string, dir: string) =>
      (await data(await call("GET", `/v1/admin/users?q=example.com&sort=${sort}&dir=${dir}`, undefined, bearer(admin.token)))).users;
    const byEmail = await find("email", "asc");
    expect(byEmail.find((u: any) => u.email === on.email).discord).toBe(true);
    expect(byEmail.find((u: any) => u.email === off.email).discord).toBe(false);
    const emails = byEmail.map((u: any) => u.email);
    expect(emails).toEqual([...emails].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase())));
    expect((await find("email", "desc"))[0].email >= (await find("email", "asc"))[0].email).toBe(true);
    expect((await find("discord", "desc"))[0].discord).toBe(true);
    const r = await data(await call("GET", "/v1/admin/users?sort=nonsense; DROP TABLE users", undefined, bearer(admin.token)));
    expect(r.sort).toBe("joined");
  });
});
