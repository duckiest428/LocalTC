import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { outbox } from "../src/email";
import { bearer, call, data, lastEmail, signIn } from "./helpers";

describe("signing in by email", () => {
  it("creates the account on the first sign-in", async () => {
    const email = "new@example.com";
    expect((await call("POST", "/v1/auth/start", { email })).status).toBe(202);
    const mail = outbox.at(-1)!;
    expect(mail.subject).toMatch(/^\d{6} is your LocalTC sign-in code$/);
    expect(mail.text).toContain("creates your LocalTC account");
    const res = await call("POST", "/v1/auth/finish", { email, code: lastEmail(email).code, kind: "desktop" });
    expect(res.status).toBe(200);
    expect((await data(res)).token).toMatch(/^[\w-]{40,}$/);
    const user = await env.DB.prepare("SELECT verified_at FROM users WHERE email = ?1").bind(email).first<{ verified_at: string }>();
    expect(user!.verified_at).not.toBeNull();
  });

  it("answers the same whether or not an address has an account", async () => {
    const { email } = await signIn();
    const known = await call("POST", "/v1/auth/start", { email });
    const unknown = await call("POST", "/v1/auth/start", { email: "nobody-yet@example.com" });
    expect(known.status).toBe(unknown.status);
    expect(await data(known)).toEqual(await data(unknown));
  });

  it("the link signs in the website", async () => {
    const email = "linker@example.com";
    await call("POST", "/v1/auth/start", { email });
    const res = await call("POST", "/v1/auth/finish", { token: lastEmail(email).token, kind: "web" });
    expect(res.status).toBe(200);
    expect(res.headers.get("Set-Cookie")).toMatch(/ltc_session=[\w-]+; Path=\/; HttpOnly; Secure; SameSite=Lax/);
    expect((await data(res)).token).toBeUndefined();
  });

  it("a code or link works once", async () => {
    const email = "once@example.com";
    await call("POST", "/v1/auth/start", { email });
    const { code, token } = lastEmail(email);
    expect((await call("POST", "/v1/auth/finish", { email, code })).status).toBe(200);
    expect((await call("POST", "/v1/auth/finish", { email, code })).status).toBe(401);
    expect((await call("POST", "/v1/auth/finish", { token })).status).toBe(400);
  });

  it("only the newest email works", async () => {
    const email = "twice@example.com";
    await call("POST", "/v1/auth/start", { email });
    const first = lastEmail(email).code;
    await call("POST", "/v1/auth/start", { email });
    const second = lastEmail(email).code;
    if (first !== second) expect((await call("POST", "/v1/auth/finish", { email, code: first })).status).toBe(401);
    expect((await call("POST", "/v1/auth/finish", { email, code: second })).status).toBe(200);
  });

  it("five wrong codes void the sign-in, even with the right one next", async () => {
    const email = "guesser@example.com";
    await call("POST", "/v1/auth/start", { email });
    const { code } = lastEmail(email);
    const wrong = code === "000000" ? "111111" : "000000";
    for (let i = 0; i < 5; i++) expect((await call("POST", "/v1/auth/finish", { email, code: wrong })).status).toBe(401);
    expect((await call("POST", "/v1/auth/finish", { email, code })).status).toBe(401);
  });

  it("an expired code doesn't work", async () => {
    const email = "late@example.com";
    await call("POST", "/v1/auth/start", { email });
    await env.DB.prepare("UPDATE logins SET expires_at = '2000-01-01T00:00:00Z'").run();
    expect((await call("POST", "/v1/auth/finish", { email, code: lastEmail(email).code })).status).toBe(401);
  });

  it("limits how many emails one address gets", async () => {
    const email = "flood@example.com";
    const statuses = [];
    for (let i = 0; i < 6; i++) statuses.push((await call("POST", "/v1/auth/start", { email })).status);
    expect(statuses).toEqual([202, 202, 202, 202, 202, 429]);
  });

  it("refuses what isn't an email address", async () => {
    expect((await call("POST", "/v1/auth/start", { email: "not an email" })).status).toBe(400);
  });

  it("stores no codes or tokens in the clear", async () => {
    const email = "hashed@example.com";
    await call("POST", "/v1/auth/start", { email });
    const { code, token } = lastEmail(email);
    const row = await env.DB.prepare("SELECT code_hash, link_hash FROM logins").first<{ code_hash: string; link_hash: string }>();
    expect(JSON.stringify(row)).not.toContain(code);
    expect(JSON.stringify(row)).not.toContain(token);
    const session = await signIn();
    const sessions = await env.DB.prepare("SELECT token_hash FROM sessions").all<{ token_hash: string }>();
    expect(sessions.results.some((s) => s.token_hash === session.token)).toBe(false);
  });

  it("lists and revokes devices", async () => {
    const { email, token } = await signIn();
    await call("POST", "/v1/auth/start", { email });
    const phone = await data(await call("POST", "/v1/auth/finish", { email, code: lastEmail(email).code, kind: "ios", device: "iPhone" }));
    const me = await data(await call("GET", "/v1/me", undefined, bearer(token)));
    expect(me.sessions).toHaveLength(2);
    const id = me.sessions.find((s: any) => s.device === "iPhone").id;
    expect((await call("DELETE", `/v1/sessions/${id}`, undefined, bearer(token))).status).toBe(200);
    expect((await call("GET", "/v1/me", undefined, bearer(phone.token))).status).toBe(401);
  });

  it("logs out", async () => {
    const { token } = await signIn();
    expect((await call("POST", "/v1/auth/logout", undefined, bearer(token))).status).toBe(200);
    expect((await call("GET", "/v1/me", undefined, bearer(token))).status).toBe(401);
  });
});

describe("the website's cookie", () => {
  it("needs the X-LocalTC header to change anything (CSRF)", async () => {
    const { res } = await signIn(undefined, "web");
    const cookie = res.headers.get("Set-Cookie")!.split(";")[0];
    expect((await call("GET", "/v1/me", undefined, { Cookie: cookie })).status).toBe(200);
    expect((await call("POST", "/v1/auth/logout", undefined, { Cookie: cookie })).status).toBe(403);
    expect((await call("POST", "/v1/auth/logout", undefined, { Cookie: cookie, "X-LocalTC": "1" })).status).toBe(200);
  });

  it("CORS lets only the dashboard's origin in", async () => {
    const ok = await call("OPTIONS", "/v1/me", undefined, { Origin: "https://localtc.test" });
    expect(ok.headers.get("Access-Control-Allow-Origin")).toBe("https://localtc.test");
    const evil = await call("OPTIONS", "/v1/me", undefined, { Origin: "https://evil.example" });
    expect(evil.headers.get("Access-Control-Allow-Origin")).toBeNull();
  });
});
