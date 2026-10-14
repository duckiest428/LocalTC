import { env } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { chartfoxHttp } from "../src/chartfox";
import { call, data } from "./helpers";

const STATE = "a".repeat(40);
const realSend = chartfoxHttp.send;
let sent: { url: string; body: string }[] = [];

beforeEach(() => {
  (env as any).CHARTFOX_CLIENT_ID = "client-1";
  (env as any).CHARTFOX_CLIENT_SECRET = "s3cret";
  sent = [];
  chartfoxHttp.send = async (req: Request) => {
    sent.push({ url: req.url, body: await req.text() });
    return new Response(JSON.stringify({ access_token: "at-1", refresh_token: "rt-1", expires_in: 3600, token_type: "Bearer" }),
      { headers: { "Content-Type": "application/json" } });
  };
});

afterEach(() => {
  chartfoxHttp.send = realSend;
});

describe("ChartFox sign-in", () => {
  it("sends the browser to ChartFox with LocalTC's client id, never its secret", async () => {
    const res = await call("GET", `/v1/chartfox/authorize?state=${STATE}`);
    expect(res.status).toBe(302);
    const to = new URL(res.headers.get("Location")!);
    expect(to.origin + to.pathname).toBe("https://api.chartfox.org/oauth/authorize");
    expect(to.searchParams.get("client_id")).toBe("client-1");
    expect(to.searchParams.get("state")).toBe(STATE);
    expect(to.searchParams.get("scope")).toContain("charts:index");
    expect(res.headers.get("Location")).not.toContain("s3cret");
  });

  it("exchanges the code with the secret and holds the tokens for the app to collect once", async () => {
    const back = await call("GET", `/v1/chartfox/callback?state=${STATE}&code=xyz`);
    expect(back.status).toBe(200);
    expect(await back.text()).toContain("ChartFox connected");
    expect(sent[0].url).toBe("https://api.chartfox.org/oauth/token");
    expect(sent[0].body).toContain("client_secret=s3cret");
    expect(sent[0].body).toContain("code=xyz");
    const got = await call("POST", "/v1/chartfox/token", { state: STATE });
    expect(got.status).toBe(200);
    expect((await data(got)).access_token).toBe("at-1");
    expect((await call("POST", "/v1/chartfox/token", { state: STATE })).status).toBe(404); // once only
  });

  it("waits for a sign-in not finished yet", async () => {
    expect((await call("POST", "/v1/chartfox/token", { state: "b".repeat(40) })).status).toBe(404);
  });

  it("refreshes through the server", async () => {
    const res = await call("POST", "/v1/chartfox/refresh", { refresh_token: "rt-1" });
    expect(res.status).toBe(200);
    expect(sent[0].body).toContain("grant_type=refresh_token");
    expect(sent[0].body).toContain("client_secret=s3cret");
  });

  it("is off without the client set up", async () => {
    (env as any).CHARTFOX_CLIENT_ID = "";
    expect((await call("GET", `/v1/chartfox/authorize?state=${STATE}`)).status).toBe(503);
  });

  it("refuses a state LocalTC didn't make", async () => {
    expect((await call("GET", "/v1/chartfox/authorize?state=short")).status).toBe(400);
  });
});
