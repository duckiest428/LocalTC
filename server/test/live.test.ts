import { describe, expect, it } from "vitest";
import { cleanStatus, pushFor } from "../src/live";
import { bearer, call, data, signIn } from "./helpers";

const cruise = {
  active: true, callsign: "FFT2084", origin: "KSAN", destination: "KPHX", phase: "CRUISE",
  tuned: { station: "Los Angeles Center", mhz: 132.15 }, next: null, last_atc: { station: "Los Angeles Center", text: "Frontier 2084, roger." },
};

describe("the companion's live view", () => {
  it("shows what the desktop app sent last", async () => {
    const { token } = await signIn();
    expect((await data(await call("GET", "/v1/live", undefined, bearer(token)))).active).toBe(false);
    expect((await call("PUT", "/v1/live", cruise, bearer(token))).status).toBe(200);
    const now = await data(await call("GET", "/v1/live", undefined, bearer(token)));
    expect(now.phase).toBe("CRUISE");
    expect(now.tuned.station).toBe("Los Angeles Center");
  });

  it("keeps no position, even if one is sent", () => {
    const s = cleanStatus({ ...cruise, lat: 33.1, lon: -115.2, alt_msl_ft: 36000 });
    expect(s).not.toHaveProperty("lat");
    expect(s).not.toHaveProperty("alt_msl_ft");
  });

  it("notifies on a handoff and when the flight ends, not on every update", () => {
    const handoff = { ...cruise, next: { station: "Albuquerque Center", mhz: 133.65 } };
    expect(pushFor(cruise, handoff)?.title).toBe("Contact Albuquerque Center");
    expect(pushFor(handoff, { ...handoff, phase: "CRUISE" })).toBeNull();
    expect(pushFor(cruise, { active: false })?.title).toBe("Flight ended");
  });

  it("is only the account's own", async () => {
    const a = await signIn();
    const b = await signIn();
    await call("PUT", "/v1/live", cruise, bearer(a.token));
    expect((await data(await call("GET", "/v1/live", undefined, bearer(b.token)))).active).toBe(false);
  });

  it("registers an iPhone for notifications", async () => {
    const { token } = await signIn();
    expect((await call("POST", "/v1/push-tokens", { token: "ab".repeat(32) }, bearer(token))).status).toBe(200);
    expect((await call("POST", "/v1/push-tokens", { token: "not hex" }, bearer(token))).status).toBe(400);
  });
});

describe("the relay's map and radio log", () => {
  const own = { t: 12.5, lat: 33.1, lon: -115.2, alt: 36000, hdg: 88, gs: 450, vs: 0, ground: false, secret: "x" };
  const traffic = [{ id: 7, callsign: "SWA118", type: "B738", lat: 33.2, lon: -115.0, alt: 35000, hdg: 270, gs: 440, ground: false, owner: "x" }];

  it("reports how many phones watch, and passes frames to them", async () => {
    const { token } = await signIn();
    expect((await data(await call("PUT", "/v1/live", cruise, bearer(token)))).watchers).toBe(0);
    const ws = await call("GET", "/v1/live/ws", undefined, { ...bearer(token), Upgrade: "websocket" });
    expect(ws.status).toBe(101);
    const socket = ws.webSocket!;
    const got: any[] = [];
    socket.accept();
    socket.addEventListener("message", (e) => got.push(JSON.parse(e.data as string)));
    expect((await data(await call("PUT", "/v1/live/frame", { own, traffic }, bearer(token)))).watchers).toBe(1);
    await call("POST", "/v1/live/radio", { lines: [{ kind: "atc", t: 1, station: "Albuquerque Center", mhz: 133.65, text: "Frontier 2084, roger.", extra: 1 }] }, bearer(token));
    await call("POST", "/v1/live/alert", { kind: "handoff", title: "Albuquerque Center", body: "contact Phoenix Approach 119.2" }, bearer(token));
    await new Promise((r) => setTimeout(r, 50));
    expect(got.map((m) => m.type)).toEqual(["hello", "own", "traffic", "radio", "alert"]);
    expect(got[1].data).not.toHaveProperty("secret");
    expect(got[2].data[0]).not.toHaveProperty("owner");
    expect(got[3].data).not.toHaveProperty("extra");
    socket.close();
  });

  it("keeps position, traffic and radio in memory only", async () => {
    const { env, runInDurableObject } = await import("cloudflare:test");
    const { token } = await signIn();
    await call("PUT", "/v1/live", cruise, bearer(token));
    await call("PUT", "/v1/live/frame", { own, traffic }, bearer(token));
    await call("POST", "/v1/live/radio", { lines: [{ kind: "atc", text: "roger" }] }, bearer(token));
    const ids = await env.DB.prepare("SELECT user_id FROM sessions").all<{ user_id: string }>();
    for (const { user_id } of ids.results) {
      const stub = env.LIVE.get(env.LIVE.idFromName(user_id));
      await runInDurableObject(stub, async (_instance, state) => {
        const keys = [...(await state.storage.list()).keys()];
        expect(keys.every((k) => k === "status" || k === "connect")).toBe(true);
      });
    }
  });

  it("hands the phone the PC's local address and key, private addresses only", async () => {
    const a = await signIn();
    const b = await signIn();
    const key = "k".repeat(43);
    await call("PUT", "/v1/live/connect", { lan: ["http://192.168.1.20:47800", "http://8.8.8.8:47800", "http://10.0.0.5:47800"], key }, bearer(a.token));
    expect(await data(await call("GET", "/v1/live/connect", undefined, bearer(a.token)))).toEqual({ lan: ["http://192.168.1.20:47800", "http://10.0.0.5:47800"], key });
    expect((await data(await call("GET", "/v1/live/connect", undefined, bearer(b.token)))).key).toBeNull();
    expect((await call("PUT", "/v1/live/connect", { lan: [], key: "short" }, bearer(a.token))).status).toBe(400);
  });

  it("refuses alerts it doesn't know", async () => {
    const { token } = await signIn();
    expect((await call("POST", "/v1/live/alert", { kind: "spam", title: "x", body: "y" }, bearer(token))).status).toBe(400);
  });
});
