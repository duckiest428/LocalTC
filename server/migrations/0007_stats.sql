-- Stats and metrics (the app's [account] stats, off by default): the dashboard's Stats page is on, and each
-- flight's line carries a few more numbers from the flight (src/flights.ts METRICS). Turned off, the flag goes
-- back to 0 and every flight's metrics are deleted.
ALTER TABLE users ADD COLUMN stats INTEGER NOT NULL DEFAULT 0;
ALTER TABLE flights ADD COLUMN metrics TEXT;
