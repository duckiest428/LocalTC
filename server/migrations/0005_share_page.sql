-- The shared flight's page grows: who flew it, if the pilot gives a name, and, if they choose, a small replay
-- of it (the path flown and the radio, on a clock from the start of the flight: never the time of day).

-- A name the pilot chooses to show on what they share ("Flown by ..."); empty shows nothing.
ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT '';

-- A replay cut down for a public page (site/cardmodel.js miniReplay): a few hundred points of the track, the
-- radio calls and the flight's details. Worked out when the replay is uploaded.
ALTER TABLE replays ADD COLUMN mini TEXT;

-- What the page shows beyond the card: the flight's details and, if the pilot chose it, the mini replay.
ALTER TABLE shares ADD COLUMN extra TEXT;
