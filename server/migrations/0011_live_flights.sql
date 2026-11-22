-- Live Flights (src/sky.ts): the public map of pilots who chose to share their flight. Off by default.
-- live_share: off, flight (this flight only: back to off when it ends) or always. The pilot can hide the route
-- and their name, and delay what's shown by 0, 5, 10 or 15 minutes. live_banned: an admin turned sharing off.
ALTER TABLE users ADD COLUMN live_share TEXT NOT NULL DEFAULT 'off';
ALTER TABLE users ADD COLUMN live_hide_route INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN live_hide_name INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN live_delay_min INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN live_banned INTEGER NOT NULL DEFAULT 0;
