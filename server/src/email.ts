import type { Env } from "./env";

export interface Email {
  to: string;
  subject: string;
  text: string;
  replyTo?: string;
  from?: string; // MAIL_FROM otherwise
  headers?: Record<string, string>;
  attachments?: { filename: string; content: string }[]; // content: base64
}

/** EMAIL_MODE=test keeps what would have been sent here, for the tests to read. */
export const outbox: Email[] = [];

export async function send(env: Env, email: Email): Promise<void> {
  if (env.EMAIL_MODE === "test") {
    outbox.push(email);
    return;
  }
  if (env.EMAIL_MODE === "log" || !env.RESEND_API_KEY) {
    console.log(`[email to ${email.to}] ${email.subject}\n${email.text}`);
    return;
  }
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify(resendBody(env, email)),
  });
  if (!res.ok) console.error(`Email to ${email.to} failed: ${res.status} ${await res.text()}`);
}

function resendBody(env: Env, email: Email): Record<string, unknown> {
  return { from: email.from ?? env.MAIL_FROM ?? "LocalTC <accounts@localtc.tech>", to: [email.to], subject: email.subject,
    text: email.text, ...(email.replyTo ? { reply_to: email.replyTo } : {}), ...(email.headers ? { headers: email.headers } : {}),
    ...(email.attachments?.length ? { attachments: email.attachments } : {}) };
}

/** Many emails, a hundred to a request (Resend's batch): one request each would run into a Worker's limit. */
export async function sendMany(env: Env, emails: Email[]): Promise<void> {
  if (env.EMAIL_MODE === "test") { outbox.push(...emails); return; }
  if (env.EMAIL_MODE === "log" || !env.RESEND_API_KEY) {
    for (const e of emails) console.log(`[email to ${e.to}] ${e.subject}\n${e.text}`);
    return;
  }
  for (let i = 0; i < emails.length; i += 100) {
    const res = await fetch("https://api.resend.com/emails/batch", {
      method: "POST",
      headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify(emails.slice(i, i + 100).map((e) => resendBody(env, e))),
    });
    if (!res.ok) console.error(`A batch of ${Math.min(100, emails.length - i)} emails failed: ${res.status} ${await res.text()}`);
  }
}
