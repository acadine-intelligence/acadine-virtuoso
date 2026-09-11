import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './tests/browser', workers: 1, retries: 0, timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:8976', viewport: { width: 1200, height: 900 } },
  outputDir: './test-results',
  webServer: {
    command: '../../.venv/bin/python tests/serve_fixture.py --port 8976',
    url: 'http://127.0.0.1:8976/test/evidence', reuseExistingServer: false, timeout: 30000,
  },
})
