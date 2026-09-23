/**
 * Feedback and support: a message from the website's Support page or the app, emailed to LocalTC's
 * maintainer (the SUPPORT_EMAIL secret). Nothing is stored. The sender's address becomes the email's
 * Reply-To, so the answer goes straight back to them.
 */
import { send } from "./email";
import type { Env } from "./env";
import { HttpError, json, readJson, str } from "./http";
import { clientIp, limit } from "./rate";

const KINDS: Record<string, string> = { feedback: "Feedback", support: "Support", bug: "Bug report" };
const EMAIL = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,}$/;

export async function submit(env: Env, request: Request): Promise<Response> {
  const body = await readJson(request, 32 * 1024);
  if (typeof body.website === "string" && body.website) return json({ ok: true }); // the form's hidden trap: a bot
  const kind = KINDS[String(body.kind ?? "feedback")] ? String(body.kind ?? "feedback") : "feedback";
  const message = str(body.message, "The message", 8000).trim();
  if (message.length < 5) throw new HttpError(400, "Say a little more, so there's something to go on.");
  const from = str(body.email, "Your email address", 254).trim();
  if (!EMAIL.test(from)) throw new HttpError(400, "That email address doesn't look right; it's where the answer goes.");
  const name = typeof body.name === "string" ? body.name.trim().slice(0, 100) : "";
  const version = typeof body.version === "string" ? body.version.slice(0, 40) : "";
  const platform = typeof body.platform === "string" ? body.platform.slice(0, 60) : "";
  const source = body.source === "app" ? "the LocalTC app" : "localtc.tech";
  await limit(env, `support:${clientIp(request)}`, 5, 3600);
  await limit(env, `support-to:${from.toLowerCase()}`, 10, 86400);
  if (!env.SUPPORT_EMAIL) {
    console.log(`[support, no SUPPORT_EMAIL set] ${kind} from ${from}: ${message}`);
    return json({ ok: true, message: "Sent. Thanks!" });
  }
  const lines = [
    `${KINDS[kind]} from ${name ? `${name} <${from}>` : from}, sent from ${source}.`,
    version || platform ? `LocalTC ${version} ${platform}`.trim() : "",
    "",
    message,
    "",
    "Reply to this email to answer them.",
  ];
  await send(env, {
    to: env.SUPPORT_EMAIL,
    subject: `[LocalTC ${KINDS[kind].toLowerCase()}] ${message.split("\n")[0].slice(0, 70)}`,
    text: lines.filter((l, i) => l || i > 1).join("\n"),
    replyTo: from,
  });
  return json({ ok: true, message: "Sent. Thanks! The answer comes to your email." });
}
