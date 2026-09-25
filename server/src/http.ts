import type { Env } from "./env";

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function json(data: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers },
  });
}

export async function readJson<T = Record<string, unknown>>(request: Request, maxBytes = 256 * 1024): Promise<T> {
  const length = Number(request.headers.get("Content-Length") ?? "0");
  if (length > maxBytes) throw new HttpError(413, "Too much at once.");
  const text = await request.text();
  if (text.length > maxBytes) throw new HttpError(413, "Too much at once.");
  if (!text) return {} as T;
  try {
    const data = JSON.parse(text);
    if (data === null || typeof data !== "object" || Array.isArray(data)) throw new Error("not an object");
    return data as T;
  } catch {
    throw new HttpError(400, "The request isn't valid JSON.");
  }
}

export function str(value: unknown, field: string, max = 256): string {
  if (typeof value !== "string" || !value.trim()) throw new HttpError(400, `${field} is missing.`);
  if (value.length > max) throw new HttpError(400, `${field} is too long.`);
  return value;
}

export function allowedOrigin(env: Env, origin: string | null): string | null {
  if (!origin) return null;
  return env.ALLOWED_ORIGINS.split(",").map((o) => o.trim()).includes(origin) ? origin : null;
}

export function cors(env: Env, request: Request): Record<string, string> {
  const origin = allowedOrigin(env, request.headers.get("Origin"));
  if (!origin) return {};
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-LocalTC",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

export const COOKIE = "ltc_session";

export function sessionCookie(env: Env, token: string, maxAgeS: number): string {
  const domain = env.COOKIE_DOMAIN ? `; Domain=${env.COOKIE_DOMAIN}` : "";
  return `${COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAgeS}${domain}`;
}

export function readCookie(request: Request, name: string): string | null {
  const header = request.headers.get("Cookie") ?? "";
  for (const part of header.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return null;
}

export function now(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function isoIn(seconds: number): string {
  return new Date(Date.now() + seconds * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
}
