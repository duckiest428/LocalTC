-- The blog's readers: reactions on a post and who has read it, each reader known only by a salted hash of their IP
-- address and browser (nothing is stored on their device; the salt below never leaves the database), and the
-- accounts that follow the blog by email.
CREATE TABLE blog_reactions (
  slug TEXT NOT NULL,
  kind TEXT NOT NULL,               -- like, love, takeoff, clap
  viewer TEXT NOT NULL,             -- sha256 of the salt, IP address and user agent
  created_at TEXT NOT NULL,
  PRIMARY KEY (slug, kind, viewer)
);

CREATE TABLE blog_reads (
  slug TEXT NOT NULL,
  viewer TEXT NOT NULL,
  read_at TEXT NOT NULL,
  PRIMARY KEY (slug, viewer)
);

CREATE TABLE blog_followers (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  token TEXT NOT NULL UNIQUE,       -- the unfollow link in each email: works without signing in
  created_at TEXT NOT NULL
);

CREATE TABLE blog_salt (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  salt TEXT NOT NULL                -- random, made the first time a reader is counted
);

-- When the followers were emailed about a post: once, the first time it's published.
ALTER TABLE blog_posts ADD COLUMN notified_at TEXT;
