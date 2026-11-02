import { env } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { openskyHttp } from "../src/departures";
import { call, data } from "./helpers";

const realSend = openskyHttp.send;
let asked: string[] = [];
let answer: () => Response;

beforeEach(async () => {
  (env as any).OPENSKY_CLIENT_ID = "client-1";
  (env as any).OPENSKY_CLIENT_SECRET = "s3cret";
  asked = [];
  answer = () => new Response(JSON.stringify([
    { callsign: "IBE0263 ", firstSeen: 1000000, estArrivalAirport: "MDSD", icao24: "34520B" },
    { callsign: "ECKCP   ", firstSeen: 1000000 }, // a registration: no schedule
    { callsign: "RYR55Z  " }, // no time
  ]));
  openskyHttp.send = async (req: Request) => {
    asked.push(req.url);
    if (req.url.includes("/token")) return new Response(JSON.stringify({ access_token: "at-1" }));
    expect(req.headers.get("Authorization")).toBe("Bearer at-1");
    return answer();
  };
  await env.DB.prepare("DELETE FROM departures_cache").run();
});

afterEach(() => {
  openskyHttp.send = realSend;
});

describe("the departures due", () => {
  it("gives yesterday's airline flights, moved on a day, with no account", async () => {
    const got = await data(await call("GET", "/v1/departures/LEMD"));
    expect(got.departures).toEqual([{ callsign: "IBE0263", off_t: 1000000 + 86400 - 900, destination: "MDSD", hex: "34520b" }]);
    const begin = Number(new URL(asked[1]).searchParams.get("begin"));
    expect(begin).toBeLessThanOrEqual(Date.now() / 1000 - 86400);
    expect(new URL(asked[1]).searchParams.get("airport")).toBe("LEMD");
  });

  it("asks OpenSky once for every pilot at the airport", async () => {
    await call("GET", "/v1/departures/LEMD");
    const again = await data(await call("GET", "/v1/departures/LEMD"));
    expect(asked.filter((u) => u.includes("/flights/")).length).toBe(1);
    expect(again.departures.length).toBe(1);
  });

  it("has none, and doesn't fail, with no secrets or when OpenSky fails", async () => {
    answer = () => new Response("busy", { status: 500 });
    expect((await data(await call("GET", "/v1/departures/KJFK"))).departures).toEqual([]);
    (env as any).OPENSKY_CLIENT_ID = undefined;
    expect((await data(await call("GET", "/v1/departures/EGLL"))).departures).toEqual([]);
  });

  it("says none where OpenSky has no flights", async () => {
    answer = () => new Response("", { status: 404 });
    expect((await data(await call("GET", "/v1/departures/LSZH"))).departures).toEqual([]);
  });
});
