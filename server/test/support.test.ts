import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { outbox } from "../src/email";
import { call, data } from "./helpers";

const message = { kind: "bug", email: "pilot@example.com", name: "Pilot", message: "Tower cleared me to land on the wrong runway.", version: "0.3.0", platform: "Windows" };

describe("support messages", () => {
  it("emails the maintainer, with the sender as Reply-To", async () => {
    (env as any).SUPPORT_EMAIL = "maintainer@example.com";
    const res = await call("POST", "/v1/support", message);
    expect(res.status).toBe(200);
    const sent = [...outbox].reverse().find((e) => e.to === "maintainer@example.com")!;
    expect(sent.subject).toContain("bug report");
    expect(sent.replyTo).toBe("pilot@example.com");
    expect(sent.text).toContain("wrong runway");
    expect(sent.text).toContain("LocalTC 0.3.0 Windows");
  });

  it("needs a message and an address to answer", async () => {
    expect((await call("POST", "/v1/support", { ...message, message: "" })).status).toBe(400);
    expect((await call("POST", "/v1/support", { ...message, email: "nope" })).status).toBe(400);
    expect((await data(await call("POST", "/v1/support", { ...message, email: "" }))).error).toMatch(/email/i);
  });

  it("quietly drops what a bot fills in", async () => {
    const before = outbox.length;
    expect((await call("POST", "/v1/support", { ...message, website: "http://spam.example" })).status).toBe(200);
    expect(outbox.length).toBe(before);
  });

  it("limits how often one address can write", async () => {
    const headers = { "CF-Connecting-IP": "203.0.113.9" };
    const statuses = [];
    for (let i = 0; i < 7; i++) statuses.push((await call("POST", "/v1/support", message, headers)).status);
    expect(statuses.slice(0, 5)).toEqual([200, 200, 200, 200, 200]);
    expect(statuses).toContain(429);
  });
});
