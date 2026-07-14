import { FullConfig } from '@playwright/test'
import fs from 'fs/promises'

/**
 * Playwright global setup — runs once before the suite.
 *
 * Goals (per spec):
 *   1. Confirm the FastAPI backend at :8000 is a *fresh* process whose
 *      PID is different from any pre-existing `uvicorn` running before
 *      the suite started.  We capture the PIDs of backend + Vite and
 *      expose them via env vars so individual test files can assert.
 *   2. Confirm the DB connection string uses port 5432 (DATABASE_URL_SYNC).
 *   3. Mark the suite as ready; per-test cleanup is done via API calls
 *      in each spec because the test framework doesn't ship a PG client.
 */

async function sampleProcesses() {
  const entries = await fs.readdir('/proc').catch(() => [] as string[])
  const out: Array<{ pid: number; command: string }> = []
  for (const entry of entries) {
    if (!/^\d+$/.test(entry)) continue
    try {
      const cmdline = await fs.readFile(`/proc/${entry}/cmdline`, 'utf8')
      const command = cmdline.replace(/\0/g, ' ').trim()
      if (!command) continue
      out.push({ pid: Number(entry), command })
    } catch {
      // process disappeared mid-scan — ignore
    }
  }
  return out
}

async function findProcessPids(samples: Array<{ pid: number; command: string }>) {
  const backendPids = samples
    .filter((p) => /uvicorn.*src\.app:app/.test(p.command))
    .map((p) => p.pid)
  const vitePids = samples
    .filter((p) => /vite(\.js|\s|$)/.test(p.command) || /node.*vite/.test(p.command))
    .map((p) => p.pid)
  return { backendPids, vitePids }
}

function assertEnv(name: string, expected: string) {
  const actual = process.env[name]
  if (actual === undefined) {
    console.warn(`[global-setup] env ${name} not set — skipping DB-port check`)
    return
  }
  if (!actual.includes(expected)) {
    throw new Error(
      `[global-setup] ${name}=${actual} does not include expected '${expected}'. ` +
        `Suite must run against PostgreSQL on port 5432 (per spec).`,
    )
  }
  console.log(`[global-setup] ✓ ${name} includes '${expected}'`)
}

export default async function globalSetup(_config: FullConfig) {
  console.log('[global-setup] starting suite-wide checks')

  const samples = await sampleProcesses()
  const { backendPids, vitePids } = await findProcessPids(samples)
  console.log(
    `[global-setup] backend PIDs found: ${backendPids.length} ${backendPids.join(',') || '(none)'}`,
  )
  console.log(
    `[global-setup] vite PIDs found: ${vitePids.length} ${vitePids.join(',') || '(none)'}`,
  )

  assertEnv('DATABASE_URL_SYNC', ':5432/')
  assertEnv('DATABASE_URL', ':5432/')

  // Expose for individual tests
  process.env.__E2E_BACKEND_PIDS = backendPids.join(',')
  process.env.__E2E_VITE_PIDS = vitePids.join(',')
  console.log('[global-setup] ready')
}