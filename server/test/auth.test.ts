import { describe, expect, it } from "vitest";
import { outbox } from "../src/email";
import { bearer, call, data, linkToken, signUp } from "./helpers";

describe("accounts", () => {
  it("must confirm the email before signing in", async () => {
    const email = "new@example.com";
    expect((await call("POST", "/v1/auth/register", { email, password: "correct horse battery" })).status).toBe(202);
    const early = await call("POST", "/v1/auth/login", { email, password: "correct horse battery", kind: "desktop" });
    expect(early.status).toBe(403);
    await call("POST", "/v1/auth/verify", { token: linkToken(email, "verify") });
    const res = await call("POST", "/v1/auth/login", { email, password: "correct horse battery", kind: "desktop" });
    expect(res.status).toBe(200);
    expect((await data(res)).token).toMatch(/^[\w-]{40,}$/);
  });

  it("answers the same whether or not an address has an account", async () => {
    const { email } = await signUp();
    const again = await call("POST", "/v1/auth/register", { email, password: "another password!" });
    const fresh = await call("POST", "/v1/auth/register", { email: "nobody-yet@example.com", password: "another password!" });
    expect(again.status).toBe(fresh.status);
    expect(await data(again)).toEqual(await data(fresh));
  });

  it("limits sign-ups from one address", async () => {
    const ip = { "CF-Connecting-IP": "192.0.2.77" };
    const statuses = [];
    for (let i = 0; i < 6; i++) statuses.push((await call("POST", "/v1/auth/register", { email: `many${i}@example.com`, password: "correct horse battery" }, ip)).status);
    expect(statuses).toEqual([202, 202, 202, 202, 202, 429]);
  });

  it("refuses short passwords and bad addresses", async () => {
    expect((await call("POST", "/v1/auth/register", { email: "a@example.com", password: "short" })).status).toBe(400);
    expect((await call("POST", "/v1/auth/register", { email: "not an email", password: "long enough password" })).status).toBe(400);
  });

  it("says the same for a wrong password and an unknown email", async () => {
    const { email } = await signUp();
    const wrong = await call("POST", "/v1/auth/login", { email, password: "wrong password here" });
    const unknown = await call("POST", "/v1/auth/login", { email: "ghost@example.com", password: "wrong password here" });
    expect(wrong.status).toBe(401);
    expect(await data(wrong)).toEqual(await data(unknown));
  });

  it("stops guessing after ten tries", async () => {
    const { email } = await signUp();
    const statuses = [];
    for (let i = 0; i < 12; i++) statuses.push((await call("POST", "/v1/auth/login", { email, password: `guess number ${i}` })).status);
    expect(statuses.slice(0, 9).every((s) => s === 401)).toBe(true);
    expect(statuses.at(-1)).toBe(429);
  });

  it("stores no passwords or tokens in the clear", async () => {
    const { env } = await import("cloudflare:test");
    const { email, password, token } = await signUp();
    const user = await env.DB.prepare("SELECT password FROM users WHERE email = ?1").bind(email).first<{ password: string }>();
    expect(user!.password).toMatch(/^pbkdf2-sha256\$\d+\$/);
    expect(user!.password).not.toContain(password);
    const sessions = await env.DB.prepare("SELECT token_hash FROM sessions").all<{ token_hash: string }>();
    expect(sessions.results.some((s) => s.token_hash === token)).toBe(false);
  });

  it("resets a password and signs out everywhere", async () => {
    const { email, token } = await signUp();
    await call("POST", "/v1/auth/reset/request", { email });
    const res = await call("POST", "/v1/auth/reset", { token: linkToken(email, "reset"), password: "a brand new password" });
    expect(res.status).toBe(200);
    expect((await call("GET", "/v1/me", undefined, bearer(token))).status).toBe(401);
    expect((await call("POST", "/v1/auth/login", { email, password: "a brand new password", kind: "desktop" })).status).toBe(200);
    // A reset link works once.
    expect((await call("POST", "/v1/auth/reset", { token: linkToken(email, "reset"), password: "yet another password" })).status).toBe(400);
  });

  it("a reset request for an unknown address sends nothing", async () => {
    const before = outbox.length;
    expect((await call("POST", "/v1/auth/reset/request", { email: "ghost2@example.com" })).status).toBe(202);
    expect(outbox.length).toBe(before);
  });

  it("lists and revokes devices", async () => {
    const { email, password, token } = await signUp();
    const other = await data(await call("POST", "/v1/auth/login", { email, password, kind: "ios", device: "iPhone" }));
    const me = await data(await call("GET", "/v1/me", undefined, bearer(token)));
    expect(me.sessions).toHaveLength(2);
    const phone = me.sessions.find((s: any) => s.device === "iPhone");
    expect((await call("DELETE", `/v1/sessions/${phone.id}`, undefined, bearer(token))).status).toBe(200);
    expect((await call("GET", "/v1/me", undefined, bearer(other.token))).status).toBe(401);
  });

  it("logs out", async () => {
    const { token } = await signUp();
    expect((await call("POST", "/v1/auth/logout", undefined, bearer(token))).status).toBe(200);
    expect((await call("GET", "/v1/me", undefined, bearer(token))).status).toBe(401);
  });
});

describe("the website's cookie", () => {
  it("is HttpOnly, and the token never appears in the page", async () => {
    const { email, password } = await signUp();
    const res = await call("POST", "/v1/auth/login", { email, password, kind: "web" });
    const cookie = res.headers.get("Set-Cookie")!;
    expect(cookie).toMatch(/ltc_session=[\w-]+; Path=\/; HttpOnly; Secure; SameSite=Lax/);
    expect((await data(res)).token).toBeUndefined();
  });

  it("needs the X-LocalTC header to change anything (CSRF)", async () => {
    const { email, password } = await signUp();
    const cookie = (await call("POST", "/v1/auth/login", { email, password, kind: "web" })).headers.get("Set-Cookie")!.split(";")[0];
    expect((await call("GET", "/v1/me", undefined, { Cookie: cookie })).status).toBe(200);
    expect((await call("POST", "/v1/auth/logout", undefined, { Cookie: cookie })).status).toBe(403);
    expect((await call("POST", "/v1/auth/logout", undefined, { Cookie: cookie, "X-LocalTC": "1" })).status).toBe(200);
  });

  it("CORS lets only the dashboard's origin in", async () => {
    const ok = await call("OPTIONS", "/v1/me", undefined, { Origin: "https://localtc.test" });
    expect(ok.headers.get("Access-Control-Allow-Origin")).toBe("https://localtc.test");
    expect(ok.headers.get("Access-Control-Allow-Credentials")).toBe("true");
    const evil = await call("OPTIONS", "/v1/me", undefined, { Origin: "https://evil.example" });
    expect(evil.headers.get("Access-Control-Allow-Origin")).toBeNull();
  });
});
