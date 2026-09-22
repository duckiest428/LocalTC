-- The LocalTC account database. Only what an account needs: who, their sign-ins, and their logbook's
-- summary lines. No positions, audio, transcripts or recordings ever reach it.

CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password TEXT NOT NULL,          -- pbkdf2-sha256$iterations$salt$hash
  verified_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,  -- sha256 of the bearer token; the token itself is never stored
  kind TEXT NOT NULL,               -- desktop, ios, web
  device TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  last_used_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX sessions_by_user ON sessions (user_id);

-- One-time links: confirming the email address, resetting the password.
CREATE TABLE tokens (
  hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  purpose TEXT NOT NULL,            -- verify, reset
  expires_at TEXT NOT NULL
);

-- The logbook, as the app's logbook.FlightRecord (minus synced_at).
CREATE TABLE flights (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL,
  callsign TEXT NOT NULL DEFAULT '',
  aircraft TEXT NOT NULL DEFAULT '',
  origin TEXT NOT NULL DEFAULT '',
  destination TEXT NOT NULL DEFAULT '',
  departure_gate TEXT NOT NULL DEFAULT '',
  arrival_gate TEXT NOT NULL DEFAULT '',
  departure_runway TEXT NOT NULL DEFAULT '',
  arrival_runway TEXT NOT NULL DEFAULT '',
  block_min REAL,
  air_min REAL,
  distance_nm REAL NOT NULL DEFAULT 0,
  max_alt_ft INTEGER NOT NULL DEFAULT 0,
  landing_vs_fpm INTEGER,
  readbacks INTEGER NOT NULL DEFAULT 0,
  readbacks_correct INTEGER NOT NULL DEFAULT 0,
  alerts INTEGER NOT NULL DEFAULT 0,
  landed INTEGER NOT NULL DEFAULT 0,
  origin_lat REAL,                  -- the airports' reference points, for the map; not the aircraft's position
  origin_lon REAL,
  destination_lat REAL,
  destination_lon REAL,
  received_at TEXT NOT NULL,
  PRIMARY KEY (user_id, id)
);
CREATE INDEX flights_by_time ON flights (user_id, started_at DESC);

-- The companion app's push notifications (APNs device tokens).
CREATE TABLE push_tokens (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  platform TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX push_tokens_by_user ON push_tokens (user_id);

-- Rate limiting: attempts per key (e.g. "login:ip:1.2.3.4") per fixed window.
CREATE TABLE attempts (
  key TEXT PRIMARY KEY,
  count INTEGER NOT NULL,
  expires_at INTEGER NOT NULL      -- unix seconds
);
