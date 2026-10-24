-- The blog (localtc.tech/blog): news about LocalTC. Written only by the accounts in BLOG_AUTHORS (src/blog.ts),
-- read by anyone once published. Its pictures are kept here too, served from /v1/blog/images/<id>.
CREATE TABLE blog_posts (
  slug TEXT PRIMARY KEY,            -- the post's address: localtc.tech/blog#<slug>
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',    -- Markdown (a small part of it: site/blog.js draws it)
  cover TEXT,                       -- an image's address, or null
  author_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  author_name TEXT NOT NULL DEFAULT '',
  published_at TEXT,                -- null: a draft, seen only by its authors
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX blog_by_date ON blog_posts (published_at DESC);

CREATE TABLE blog_images (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,               -- image/jpeg, image/png, image/webp, image/gif
  data BLOB NOT NULL,
  author_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL
);
