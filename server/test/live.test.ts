import { describe, expect, it } from "vitest";
import { cleanStatus, pushFor } from "../src/live";
import { bearer, call, data, signUp } from "./helpers";

const cruise = {
  active: true, callsign: "FFT2084", origin: "KSAN", destination: "KPHX", phase: "CRUISE",
  tuned: { station: "Los Angeles Center", mhz: 132.15 }, next: null, last_atc: { station: "Los Angeles Center", text: "Frontier 2084, roger." },
};

describe("the companion's live view", () => {
  it("shows what the desktop app sent last", async () => {
    const { token } = await signUp();
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
    const a = await signUp();
    const b = await signUp();
    await call("PUT", "/v1/live", cruise, bearer(a.token));
    expect((await data(await call("GET", "/v1/live", undefined, bearer(b.token)))).active).toBe(false);
  });

  it("registers an iPhone for notifications", async () => {
    const { token } = await signUp();
    expect((await call("POST", "/v1/push-tokens", { token: "ab".repeat(32) }, bearer(token))).status).toBe(200);
    expect((await call("POST", "/v1/push-tokens", { token: "not hex" }, bearer(token))).status).toBe(400);
  });
});
