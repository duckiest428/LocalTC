export interface Env {
  DB: D1Database;
  LIVE: DurableObjectNamespace;
  SITE_URL: string; // where the links in emails go (verify, reset)
  ALLOWED_ORIGINS: string; // comma-separated: the dashboard's origins, allowed to call with a cookie
  COOKIE_DOMAIN: string; // ".localtc.tech", or "" for a host-only cookie
  MAIL_FROM?: string;
  EMAIL_MODE: string; // resend, log, test
  RESEND_API_KEY?: string;
  SUPPORT_EMAIL?: string; // where Support messages go (a secret: it's the maintainer's own address)
  APNS_HOST?: string;
  APNS_TOPIC?: string;
  APNS_KEY_ID?: string;
  APNS_TEAM_ID?: string;
  APNS_PRIVATE_KEY?: string; // the .p8 key's PEM
}
