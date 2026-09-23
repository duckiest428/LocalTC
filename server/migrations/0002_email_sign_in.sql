-- Sign-in by email only: no passwords. Each sign-in is a 6-digit code and a link, sent to the address,
-- valid for 15 minutes and usable once. Using either proves the address, so there's no separate
-- confirmation step, and no password to reset or leak.

ALTER TABLE users DROP COLUMN password;

DROP TABLE tokens;

CREATE TABLE logins (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash TEXT NOT NULL,          -- sha256 of id:code; the code itself is only in the email
  link_hash TEXT NOT NULL UNIQUE,   -- sha256 of the link's token
  attempts INTEGER NOT NULL DEFAULT 0,  -- wrong codes; five and the sign-in is void
  expires_at TEXT NOT NULL
);
CREATE INDEX logins_by_user ON logins (user_id);
