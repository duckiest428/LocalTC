import { env } from "cloudflare:test";
import { outbox } from "../src/email";
import worker from "../src/index";

export const API = "https://api.localtc.test";

// Each call from its own address, so the per-address rate limits don't trip across tests. The per-email
// limit is what the guessing test exercises.
function randomIp(): string {
  return `198.51.${Math.floor(Math.random() * 255)}.${Math.floor(Math.random() * 255)}`;
}

export async function call(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<Response> {
  const init: RequestInit = { method, headers: { "Content-Type": "application/json", "CF-Connecting-IP": randomIp(), ...headers } };
  if (body !== undefined) init.body = JSON.stringify(body);
  return worker.fetch(new Request(`${API}${path}`, init), env);
}

export async function data<T = any>(res: Response): Promise<T> {
  return (await res.json()) as T;
}

/** The link token from the last email sent to ``to`` (``verify`` or ``reset``). */
export function linkToken(to: string, purpose: string): string {
  const email = [...outbox].reverse().find((e) => e.to === to && e.text.includes(`?${purpose}=`));
  if (!email) throw new Error(`no ${purpose} email to ${to}`);
  return new URL(email.text.match(/https?:\/\/\S+/)![0]).searchParams.get(purpose)!;
}

export async function signUp(email = `pilot-${crypto.randomUUID()}@example.com`, password = "correct horse battery"): Promise<{ email: string; password: string; token: string }> {
  await call("POST", "/v1/auth/register", { email, password });
  await call("POST", "/v1/auth/verify", { token: linkToken(email, "verify") });
  const res = await call("POST", "/v1/auth/login", { email, password, kind: "desktop", device: "Test PC" });
  return { email, password, token: (await data(res)).token };
}

export function bearer(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

export function flight(id: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id, started_at: "2026-09-20T21:26:00Z", ended_at: "2026-09-20T22:46:38Z", callsign: "FFT2084", aircraft: "A320neo",
    origin: "KSAN", destination: "KPHX", departure_gate: "Gate 17", arrival_gate: "Gate C14", departure_runway: "27",
    arrival_runway: "26", block_min: 65.0, air_min: 51.8, distance_nm: 321.1, max_alt_ft: 36246, landing_vs_fpm: -235,
    readbacks: 23, readbacks_correct: 22, alerts: 2, landed: true, ...extra,
  };
}
