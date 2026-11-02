-- The departures due from an airport, as OpenSky last answered (src/departures.ts): one answer for every pilot there.
CREATE TABLE departures_cache (
  icao TEXT NOT NULL,
  block INTEGER NOT NULL,
  body TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  PRIMARY KEY (icao, block)
);
