-- ChartFox sign-in (src/chartfox.ts): the tokens ChartFox gave for a sign-in the app started, waiting a few minutes
-- for the app to collect them once (by the random state it made). Deleted when collected, or when they expire.
CREATE TABLE chartfox_pending (
  state TEXT PRIMARY KEY,
  tokens TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
