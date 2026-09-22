import type { Env } from "./env";

export interface Email {
  to: string;
  subject: string;
  text: string;
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
    body: JSON.stringify({ from: env.MAIL_FROM ?? "LocalTC <accounts@localtc.tech>", to: [email.to], subject: email.subject, text: email.text }),
  });
  if (!res.ok) console.error(`Email to ${email.to} failed: ${res.status} ${await res.text()}`);
}
