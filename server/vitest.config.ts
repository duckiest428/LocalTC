import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

export default defineConfig(async () => {
  const migrations = await readD1Migrations("./migrations");
  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: {
          bindings: {
            TEST_MIGRATIONS: migrations,
            EMAIL_MODE: "test",
            SITE_URL: "https://localtc.test",
            ALLOWED_ORIGINS: "https://localtc.test",
            COOKIE_DOMAIN: "",
            APNS_TOPIC: "",
            PBKDF2_ITERATIONS: "1000", // fast tests; production uses 100000
          },
        },
      }),
    ],
    test: { setupFiles: ["./test/apply-migrations.ts"] },
  };
});
