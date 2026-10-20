import { defineConfig } from "@playwright/test";
const port = process.env.PW_PORT || "3018";
if (!/^\d{4,5}$/.test(port) || Number(port) > 65535 || Number(port) < 1024) throw new Error("invalid_test_port");
export default defineConfig({ testDir: "./tests", testMatch: ["memory-boundaries.spec.ts", "memory-live.spec.ts", "memory-review.spec.ts", "agent-memory-review.spec.ts", "agent-memory-live.spec.ts", "progress.spec.ts"], workers: 1, timeout: 30000,
  use: { channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", baseURL: `http://127.0.0.1:${port}`, viewport: { width: 1440, height: 1000 }, trace: "retain-on-failure" },
  webServer: { command: `npx next start --hostname 127.0.0.1 --port ${port}`, url: `http://127.0.0.1:${port}`, reuseExistingServer: false },
});
