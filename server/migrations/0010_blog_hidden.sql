-- The site's own posts (site/blog-posts.json, written in the repository) that an author deleted or unpublished on
-- the blog page: hidden there. An author's edit of one is an ordinary post in blog_posts with the same slug, shown
-- in its place.
CREATE TABLE blog_hidden (
  slug TEXT PRIMARY KEY,
  hidden_at TEXT NOT NULL
);
