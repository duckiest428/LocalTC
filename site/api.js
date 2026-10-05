/* What the signed-in pages share (the dashboard, the full-screen Flight Tracker): the account server's address and
   a call to it with the sign-in cookie. The sign-in is an HttpOnly cookie on api.localtc.tech, which these pages can't
   read; nothing is kept in this browser's storage. */

const API = ["localhost", "127.0.0.1"].includes(location.hostname) ? "http://localhost:8787" : "https://api.localtc.tech";
const $ = (s) => document.querySelector(s);

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(method, path, body) {
  const res = await fetch(API + path, {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-LocalTC": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `The server said ${res.status}.`);
    err.status = res.status;
    throw err;
  }
  return data;
}
