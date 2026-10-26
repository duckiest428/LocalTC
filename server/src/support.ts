/**
 * Feedback and support: a message from the Dashboard's Support section or the app, emailed to LocalTC's
 * maintainer (the SUPPORT_EMAIL secret). Nothing is stored. Only signed-in accounts can write; the account's
 * address becomes the email's Reply-To, so the answer goes straight back to them.
 *
 * Anything can go with it, attached: a session export, logs, screenshots ({attachments: [{name, data}]}, data in
 * base64), up to 5 files and 25 MB together, which is what an email carries. They're in the email only.
 */
import type { Auth } from "./auth";
import { send } from "./email";
import type { Env } from "./env";
import { HttpError, json, readJson, str } from "./http";
import { clientIp, limit } from "./rate";

const KINDS: Record<string, string> = { feedback: "Feedback", support: "Support", bug: "Bug report" };
const MAX_FILES = 5;
export const MAX_ATTACHED = 25 * 1024 * 1024; // bytes, all the files together
const BASE64 = /^[A-Za-z0-9+/]*={0,2}$/;

/** The files sent with a message, checked: names made safe, sizes counted. */
function attachments(raw: unknown): { filename: string; content: string }[] {
  if (raw === undefined || raw === null) return [];
  if (!Array.isArray(raw) || raw.length > MAX_FILES) throw new HttpError(400, `Attach up to ${MAX_FILES} files.`);
  let total = 0;
  return raw.map((f) => {
    const name = typeof f?.name === "string" ? f.name : "";
    const data = typeof f?.data === "string" ? f.data.replace(/\s+/g, "") : "";
    if (!data || !BASE64.test(data)) throw new HttpError(400, `${name || "A file"} didn't come through whole.`);
    total += Math.floor((data.length * 3) / 4);
    if (total > MAX_ATTACHED) throw new HttpError(413, "The files are too big together: up to 25 MB. Zip them, or share a link in the message.");
    const filename = name.replace(/[^\w .()-]+/g, "_").replace(/^\.+/, "").slice(0, 100) || "attachment";
    return { filename, content: data };
  });
}

export async function submit(env: Env, request: Request, auth: Auth): Promise<Response> {
  const body = await readJson(request, Math.ceil(MAX_ATTACHED * 1.37) + 64 * 1024);
  const files = attachments(body.attachments);
  const kind = KINDS[String(body.kind ?? "feedback")] ? String(body.kind ?? "feedback") : "feedback";
  const message = str(body.message, "The message", 8000).trim();
  if (message.length < 5) throw new HttpError(400, "Say a little more, so there's something to go on.");
  const from = auth.user.email;
  const version = typeof body.version === "string" ? body.version.slice(0, 40) : "";
  const platform = typeof body.platform === "string" ? body.platform.slice(0, 60) : "";
  const source = body.source === "app" ? "the LocalTC app" : "localtc.tech";
  await limit(env, `support:${clientIp(request)}`, 5, 3600);
  await limit(env, `support-from:${auth.user.id}`, 10, 86400);
  if (!env.SUPPORT_EMAIL) {
    console.log(`[support, no SUPPORT_EMAIL set] ${kind} from ${from}: ${message}${files.length ? ` (${files.map((f) => f.filename).join(", ")})` : ""}`);
    return json({ ok: true, message: "Sent. Thanks!" });
  }
  const lines = [
    `${KINDS[kind]} from ${from}, sent from ${source}.`,
    version || platform ? `LocalTC ${version} ${platform}`.trim() : "",
    "",
    message,
    "",
    ...(files.length ? [`Attached: ${files.map((f) => f.filename).join(", ")}`, ""] : []),
    "Reply to this email to answer them.",
  ];
  await send(env, {
    to: env.SUPPORT_EMAIL,
    subject: `[LocalTC ${KINDS[kind].toLowerCase()}] ${message.split("\n")[0].slice(0, 70)}`,
    text: lines.filter((l, i) => l || i > 1).join("\n"),
    replyTo: from,
    attachments: files,
  });
  return json({ ok: true, message: "Sent. Thanks! The answer comes to your email." });
}
