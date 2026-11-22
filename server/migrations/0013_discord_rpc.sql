-- Whether the app's Discord Rich Presence is on (the app's [ui] discord), as it says with each logbook sync, for the
-- admin panel. NULL: not known yet (an older app, or no sync since).
ALTER TABLE users ADD COLUMN discord_rpc INTEGER;
