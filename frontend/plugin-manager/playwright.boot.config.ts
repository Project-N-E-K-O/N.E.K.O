import { defineConfig, devices } from '@playwright/test'

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH

export default defineConfig({
  testDir: './src/e2e',
  testMatch: '**/*.e2e.ts',
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: false,
  reporter: [['list']],
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --strictPort',
    url: 'http://127.0.0.1:4173/ui/',
    reuseExistingServer: false,
    timeout: 30_000,
  },
  use: {
    ...devices['Desktop Chrome'],
    headless: true,
    launchOptions: executablePath ? { executablePath } : {},
  },
})
