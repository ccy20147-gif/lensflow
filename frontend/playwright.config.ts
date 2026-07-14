import { defineConfig, devices } from '@playwright/test'

declare const process: { env: { CI?: string } }

const isCI = Boolean(process.env.CI)
const e2eBackendPort = (process.env as Record<string, string | undefined>).E2E_BACKEND_PORT ?? '18000'
const e2eFrontendPort = (process.env as Record<string, string | undefined>).E2E_FRONTEND_PORT ?? '15173'

export default defineConfig({
  testDir: './tests/e2e',
  testIgnore: /_deprecated\/.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  timeout: 90_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://localhost:${e2eFrontendPort}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: `cd ../backend && UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade heads && UV_CACHE_DIR=/tmp/uv-cache uv run uvicorn src.app:app --port ${e2eBackendPort} --host 127.0.0.1`,
      url: `http://127.0.0.1:${e2eBackendPort}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: 'pipe',
      stderr: 'pipe',
      env: {
        DATABASE_URL: 'postgresql+asyncpg://toonflow:toonflow@localhost:5432/toonflow',
        DATABASE_URL_SYNC: 'postgresql+psycopg2://toonflow:toonflow@localhost:5432/toonflow',
        UV_CACHE_DIR: '/tmp/uv-cache',
        // E2E must prove the deployment-safe missing-credential path.
        ATLAS_CLOUD_API_KEY: '',
        ATLASCLOUD_API_KEY: '',
        EMBEDDED_BUSINESS_WORKER_ENABLED: 'true',
        DEBUG: 'true',
      },
    },
    {
      command: `pnpm exec vite --port ${e2eFrontendPort} --strictPort`,
      url: `http://localhost:${e2eFrontendPort}`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: 'pipe',
      stderr: 'pipe',
      env: {
        VITE_API_PROXY_TARGET: `http://127.0.0.1:${e2eBackendPort}`,
      },
    },
  ],
})
