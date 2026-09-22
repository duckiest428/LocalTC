import type { Env } from "./env";
import { HttpError } from "./http";

/** At most ``max`` attempts per ``key`` in each ``windowS``-second window, or 429. */
export async function limit(env: Env, key: string, max: number, windowS: number): Promise<void> {
  const nowS = Math.floor(Date.now() / 1000);
  const window = Math.floor(nowS / windowS) * windowS;
  const row = await env.DB.prepare(
    `INSERT INTO attempts (key, count, expires_at) VALUES (?1, 1, ?2)
     ON CONFLICT(key) DO UPDATE SET count = count + 1 RETURNING count`,
  ).bind(`${key}:${window}`, window + windowS).first<{ count: number }>();
  if ((row?.count ?? 0) > max) throw new HttpError(429, "Too many attempts. Wait a few minutes and try again.");
}

export function clientIp(request: Request): string {
  return request.headers.get("CF-Connecting-IP") ?? "local";
}
