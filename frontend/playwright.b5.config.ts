import { defineConfig } from "@playwright/test";
import path from "node:path";
const folder = process.env.AWB_B5_ARTIFACT_DIR;
if (process.env.AWB_B5_LIVE !== "1" || !folder || !path.isAbsolute(folder)) {
  throw new Error("explicit_b5_runner_required");
}
export default defineConfig({
  testDir: "./tests",
  testMatch: "b5-real-repair.spec.ts",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 750000,
  expect: { timeout: 15000 },
  outputDir: path.join(folder, "playwright"),
  reporter: [["list"], ["json", { outputFile: path.join(folder, "browser-results.json") }]],
  use: {
    channel: process.env.PLAYWRIGHT_CHANNEL || "chrome",
    baseURL: "http://127.0.0.1:3018",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
});
