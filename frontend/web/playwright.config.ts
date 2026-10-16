import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E 配置
 * - 用系统 Chrome（channel: "chrome"），不下载额外 Chromium
 * - 前端为 Hash 路由 + /web base，登录页 = <baseURL>/#/login
 * - 需前端(5173)+后端(8001)已在运行；不自动起服务（webServer 留空）
 * - 截图/视频产物在 e2e/.output
 */
export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e/.output",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { outputFolder: "./e2e/.report", open: "never" }]],
  use: {
    baseURL: "http://localhost:5180/web/",
    channel: "chrome",
    headless: true,
    viewport: { width: 1440, height: 900 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    actionTimeout: 15_000,
  },
  projects: [
    {
      name: "chrome",
      use: { ...devices["Desktop Chrome"], channel: "chrome" },
    },
  ],
});
