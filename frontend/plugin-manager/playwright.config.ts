import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './src/components/plugin/hosted/e2e',
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: true,
  reporter: [['list']],
  use: {
    ...devices['Desktop Chrome'],
    headless: true,
  },
})
