import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  timeout: 60000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8766",
    viewport: { width: 1505, height: 1045 },
  },
  webServer: {
    command:
      "python3 ../preview.py --port 8766 --output-dir ../runs/workbench-tests",
    url: "http://127.0.0.1:8766",
    reuseExistingServer: false,
  },
  reporter: "list",
});
