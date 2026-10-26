export interface Env {
  DB: D1Database;
  LIVE: DurableObjectNamespace;
  SITE_URL: string; // where the links in emails go (verify, reset)
  ALLOWED_ORIGINS: string; // comma-separated: the dashboard's origins, allowed to call with a cookie
  COOKIE_DOMAIN: string; // ".localtc.tech", or "" for a host-only cookie
  MAIL_FROM?: string;
  EMAIL_MODE: string; // resend, log, test
  RESEND_API_KEY?: string;
  SUPPORT_EMAIL?: string; // where Support messages go (support@localtc.tech, as the pages say)
  APNS_HOST?: string;
  APNS_TOPIC?: string;
  APNS_KEY_ID?: string;
  APNS_TEAM_ID?: string;
  APNS_PRIVATE_KEY?: string; // the .p8 key's PEM
  CHARTFOX_CLIENT_ID?: string; // ChartFox's OAuth client (src/chartfox.ts); without both, ChartFox sign-in is off
  CHARTFOX_CLIENT_SECRET?: string;
  MARKETING_FROM?: string; // the blog's emails to its followers: "LocalTC <marketing@localtc.tech>"
  BLOG_AUTHORS?: string; // comma-separated addresses that may write the blog (src/blog.ts); its default otherwise
}
