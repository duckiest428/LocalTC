-- The admin panel (src/admin.ts, the dashboard's Admin page, for the addresses in ADMINS only).
-- users.tags: labels an admin gives an account, shown on its Live Flights card: [{"label": "Streamer", "color": "purple"}].
-- server_flags: switches the admin panel flips without a deploy (live_sky_paused: Live Flights off for everyone).
ALTER TABLE users ADD COLUMN tags TEXT NOT NULL DEFAULT '[]';
CREATE TABLE server_flags (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
