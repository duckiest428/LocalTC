-- Flight replays: a flight's track (thinned to every few seconds) and its radio transcript, as the app's
-- replay format v1 (docs/replay-format.md), gzipped. Only for flights the pilot chose to upload; never audio.
-- Deleting the flight or the account deletes its replay.

CREATE TABLE replays (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  flight_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  size INTEGER NOT NULL,            -- bytes, gzipped
  data BLOB NOT NULL,
  PRIMARY KEY (user_id, flight_id)
);
