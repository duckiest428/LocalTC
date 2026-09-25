import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import worker from "../src/index";
import { bearer, call, data, flight, signIn } from "./helpers";

const SEPT = "period=month&from=2026-09-01T00:00:00Z&to=2026-10-01T00:00:00Z&label=September%202026";
const YEAR = "period=year&from=2026-01-01T00:00:00Z&to=2027-01-01T00:00:00Z&label=2026";

function day(d: number, extra: Record<string, unknown> = {}): Record<string, unknown> {
  const date = `2026-09-${String(d).padStart(2, "0")}T14:00:00Z`;
  return flight(`sep${d}`, { started_at: date, ended_at: date, ...extra });
}

async function pilot(flights: Record<string, unknown>[]) {
  const who = await signIn();
  if (flights.length) await call("POST", "/v1/flights", { flights }, bearer(who.token));
  return who;
}

const kinds = (r: any) => r.slides.map((s: any) => s.kind);

describe("ATC Wrapped", () => {
  it("tells a month in slides, leaving out what says nothing", async () => {
    const { token } = await pilot([
      day(3), day(4, { landing_vs_fpm: -95 }), day(5, { origin: "KPHX", destination: "KSAN", air_min: 70, distance_nm: 300 }),
      day(20, { aircraft: "B737-800", readbacks: 10, readbacks_correct: 5 }),
    ]);
    const r = await data(await call("GET", `/v1/wrapped?${SEPT}`, undefined, bearer(token)));
    expect(r.tier).toBe("month");
    expect(r.flights).toBe(4);
    expect(kinds(r)).toEqual(["intro", "totals", "distance", "map", "top_airport", "top_route", "aircraft", "longest", "best_landing", "radio", "summary"]);
    const slide = (k: string) => r.slides.find((s: any) => s.kind === k);
    expect(slide("best_landing").fpm).toBe(-95);
    expect(slide("best_landing").greasers).toBe(1);
    expect(slide("top_route")).toMatchObject({ origin: "KSAN", destination: "KPHX", flights: 3 });
    expect(slide("longest").origin).toBe("KPHX");
    expect(slide("aircraft")).toMatchObject({ aircraft: "A320neo", flights: 3 });
    expect(slide("distance").framing).toMatch(/marathons/);
    expect(slide("radio").accuracy).toBeCloseTo((100 * 71) / 79, 0);
    expect(kinds(r)).not.toContain("persona"); // a year's
  });

  it("compares with the period before", async () => {
    const { token } = await pilot([day(3), day(4), flight("aug", { started_at: "2026-08-15T10:00:00Z", readbacks: 10, readbacks_correct: 10 })]);
    const r = await data(await call("GET", `/v1/wrapped?${SEPT}`, undefined, bearer(token)));
    expect(r.previous).toMatchObject({ flights: 1, readback_accuracy: 100 });
    expect(r.slides.find((s: any) => s.kind === "radio").previous).toBe(100);
  });

  it("shows a thin month as a week's card, and an empty one as nothing, with the last flight", async () => {
    const one = await pilot([day(3)]);
    const r = await data(await call("GET", `/v1/wrapped?${SEPT}`, undefined, bearer(one.token)));
    expect(r.tier).toBe("week");
    expect(kinds(r)).toEqual(["summary"]);

    const none = await pilot([flight("old", { started_at: "2026-07-01T10:00:00Z" })]);
    const empty = await data(await call("GET", `/v1/wrapped?${SEPT}`, undefined, bearer(none.token)));
    expect(empty.flights).toBe(0);
    expect(empty.slides).toEqual([]);
    expect(empty.last_flight.id).toBe("old");
    expect(empty.last_flight.days_ago).toBeGreaterThan(60);
    const share = await call("POST", "/v1/shares", { kind: "wrapped", period: "month", from: "2026-09-01T00:00:00Z", to: "2026-10-01T00:00:00Z" }, bearer(none.token));
    expect(share.status).toBe(400);
  });

  it("gives a year its superlatives: streak, busiest month, new airports, the standout moment, a pilot type", async () => {
    const { token } = await pilot([
      flight("jan", { started_at: "2026-01-10T10:00:00Z", origin: "KSEA", destination: "KSEA" }),
      day(3), day(4), day(5), day(6, { origin: "KLAX", destination: "KSFO", origin_lat: 33.9, origin_lon: -118.4, destination_lat: 37.6, destination_lon: -122.4 }),
    ]);
    const radio = [{ kind: "atc", t: 3, station: "San Diego Clearance", mhz: 125.9, text: "Frontier 2084, cleared to Phoenix via the PADRZ3 departure" }];
    const replay = { v: 1, flight: { started_at: "2026-09-04T14:00:00Z", duration_s: 60 }, airports: {},
      track: { t: [0], lat: [32.73], lon: [-117.19], alt: [17], gs: [0], hdg: [270], vs: [0], gnd: [1] }, radio };
    const body = await new Response(new Blob([JSON.stringify(replay)]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer();
    const put = await worker.fetch(new Request("https://api.localtc.test/v1/flights/sep4/replay", { method: "PUT", body, headers: bearer(token) }), env);
    expect(put.status).toBe(200);

    const r = await data(await call("GET", `/v1/wrapped?${YEAR}&tz=-420`, undefined, bearer(token)));
    expect(r.tier).toBe("year");
    const slide = (k: string) => r.slides.find((s: any) => s.kind === k);
    expect(slide("busiest_month")).toMatchObject({ month: "2026-09", flights: 4 });
    expect(slide("streak")).toMatchObject({ days: 4, from: "2026-09-03", to: "2026-09-06" });
    expect(slide("moment")).toMatchObject({ moment: "clearance", label: "Clearance", station: "San Diego Clearance" });
    expect(slide("moment").flight.id).toBe("sep4");
    expect(slide("persona").name).toBeTruthy();
    expect(slide("first_last").first.id).toBe("jan");
    expect(kinds(r).at(-1)).toBe("summary");
  });

  it("shares only the summary, as a public card", async () => {
    const { token, email } = await pilot([day(3), day(4)]);
    const share = await data(await call("POST", "/v1/shares", { kind: "wrapped", period: "month", from: "2026-09-01T00:00:00Z",
      to: "2026-10-01T00:00:00Z", label: "September 2026" }, bearer(token)));
    expect(share.url).toMatch(/\/w\/[A-Za-z0-9]{10}$/);
    expect(share.card).toMatchObject({ kind: "wrapped", flights: 2, label: "September 2026" });
    const html = await (await worker.fetch(new Request(share.url), env)).text();
    expect(html).toContain("September 2026: ATC Wrapped");
    expect(html).not.toContain(email);
    expect(html).not.toContain("sep3"); // no flight in detail
  });

  it("refuses a period that isn't one", async () => {
    const { token } = await pilot([]);
    for (const q of ["period=decade&from=2026-01-01T00:00:00Z&to=2027-01-01T00:00:00Z", "period=year&from=2020-01-01T00:00:00Z&to=2027-01-01T00:00:00Z",
      "period=week&from=2026-09-08T00:00:00Z&to=2026-09-01T00:00:00Z", "period=week&from=yesterday&to=today"]) {
      expect((await call("GET", `/v1/wrapped?${q}`, undefined, bearer(token))).status).toBe(400);
    }
  });
});
