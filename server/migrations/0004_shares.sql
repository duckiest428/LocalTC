-- Shared flights and Wrapped recaps: a public page at localtc.tech/f/<slug> (or /w/<slug>) for what the pilot
-- chose to share, a snapshot of it taken at the time (never the track, gates, times of day or the account),
-- and the card image the pilot's app rendered. Unsharing, deleting the flight or the account removes it.

CREATE TABLE shares (
  slug TEXT PRIMARY KEY,            -- random, 10 characters: never the flight's id
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,               -- flight, wrapped
  ref TEXT NOT NULL,                -- the flight's id, or the Wrapped period ("month:2026-09")
  created_at TEXT NOT NULL,
  data TEXT NOT NULL,               -- the snapshot, JSON
  image BLOB,                       -- the card, PNG, once the app has rendered it
  UNIQUE (user_id, kind, ref)
);

-- The moments of an uploaded replay worth quoting (site/moments.js), kept when it's uploaded: for the phone's
-- share sheet and Wrapped's standout moment, without unpacking every replay again.
ALTER TABLE replays ADD COLUMN moments TEXT;
