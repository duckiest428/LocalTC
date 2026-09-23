import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { outbox } from "../src/email";
import { bearer, call, data, signIn } from "./helpers";

const message = { kind: "bug", message: "Tower cleared me to land on the wrong runway.", version: "0.3.0", platform: "Windows" };

describe("support messages", () => {
  it("emails the maintainer, with the account's address as Reply-To", async () => {
    (env as any).SUPPORT_EMAIL = "maintainer@example.com";
    const { email, token } = await signIn();
    const res = await call("POST", "/v1/support", { ...message, email: "someone-else@example.com" }, bearer(token));
    expect(res.status).toBe(200);
    const sent = [...outbox].reverse().find((e) => e.to === "maintainer@example.com")!;
    expect(sent.subject).toContain("bug report");
    expect(sent.replyTo).toBe(email); // never an address the sender typed
    expect(sent.text).toContain("wrong runway");
    expect(sent.text).toContain("LocalTC 0.3.0 Windows");
  });

  it("needs an account", async () => {
    const res = await call("POST", "/v1/support", message);
    expect(res.status).toBe(401);
  });

  it("needs the CSRF header from the website", async () => {
    const { res } = await signIn(undefined, "web");
    const cookie = res.headers.get("Set-Cookie")!.split(";")[0];
    expect((await call("POST", "/v1/support", message, { Cookie: cookie })).status).toBe(403);
    expect((await call("POST", "/v1/support", message, { Cookie: cookie, "X-LocalTC": "1" })).status).toBe(200);
  });

  it("needs a message", async () => {
    const { token } = await signIn();
    const res = await call("POST", "/v1/support", { ...message, message: "" }, bearer(token));
    expect(res.status).toBe(400);
    expect((await data(res)).error).toMatch(/message/i);
  });

  it("limits how often one account can write", async () => {
    const { token } = await signIn();
    const statuses = [];
    for (let i = 0; i < 12; i++) statuses.push((await call("POST", "/v1/support", message, bearer(token))).status);
    expect(statuses.slice(0, 10).every((s) => s === 200)).toBe(true);
    expect(statuses).toContain(429);
  });
});
