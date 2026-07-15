import { test, expect } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

/**
 * E2E: 05 — Human Gate: create → resolve → reload → confirm state.
 *
 * Creates a caller-owned workflow, freezes a human_gate revision, and starts
 * it through the public compiled-run API. The main interactions are real
 * browser clicks: navigate to workbench, resolve the task, reload, and verify
 * that the persisted decision is still visible.
 */

async function login(page: any) {
  await loginWithFreshAccount(page, 'human-gate', 'E2E')
}

async function createCompiledGateRun(page: any) {
  return page.evaluate(async () => {
    const token = localStorage.getItem('toonflow.token')
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    const request = async (path: string, init: RequestInit = {}) => {
      const response = await fetch(path, { ...init, headers: { ...headers, ...(init.headers || {}) } })
      if (!response.ok) throw new Error(`${path} failed: ${response.status} ${await response.text()}`)
      return response.json()
    }
    const identity = await request('/api/v1/identity/verify')
    const workflow = await request('/api/v1/workflows/', {
      method: 'POST', body: JSON.stringify({ owner_kind: 'user', owner_id: identity.account_id }),
    })
    const draft = await request(`/api/v1/workflows/${workflow.workflow_id}/draft`)
    await request(`/api/v1/workflows/${workflow.workflow_id}/draft`, {
      method: 'PUT',
      body: JSON.stringify({
        graph: {
          nodes: [{ id: 'approval', type: 'human_gate', config: { policy_strength: 'domain_required', timeout_minutes: 5, on_timeout: 'fail' } }],
          edges: [],
        },
        config: {}, layout: {}, base_graph_hash: draft.graph_hash,
      }),
    })
    const revision = await request(`/api/v1/workflows/${workflow.workflow_id}/revisions`, { method: 'POST', body: '{}' })
    const run = await request('/api/v1/runtime/workflow-runs', {
      method: 'POST', body: JSON.stringify({ workflow_revision_id: revision.revision_id, input_snapshot: {} }),
    })
    const tasks = await request(`/api/v1/runtime/human-tasks?run_id=${run.run_id}`)
    if (!tasks.tasks?.[0]?.task_id) throw new Error('compiled human_gate did not materialize a task')
    return { taskId: tasks.tasks[0].task_id as string, runId: run.run_id as string }
  })
}

test.describe('05 · Human Gate lifecycle', () => {
  test('create human task → resolve → reload → confirm', async ({ page }) => {
    await login(page)

    // Create a project via browser UI
    await page.fill('.create-form input', `E2E-HG-${Date.now()}`)
    await page.click('.create-form button[type="submit"]')
    await page.waitForURL('**/projects/**', { timeout: 15000 })
    const projectId = page.url().split('/projects/')[1]?.split('/')[0] || ''

    const { taskId, runId } = await createCompiledGateRun(page)
    expect(taskId).toBeTruthy()
    expect(runId).toBeTruthy()

    // Navigate to workbench human-tasks tab
    await page.goto(`/projects/${projectId}/workbench/human-tasks`)
    await page.waitForSelector('.workbench-page', { timeout: 10000 })
    await expect(page.locator('text=Human Gate')).toBeVisible({ timeout: 5000 })

    // Verify the task card appears (it has the task_id shortened)
    await expect(page.locator(`text=${taskId.slice(0, 8)}`).first()).toBeVisible({ timeout: 5000 })

    // Click resolve (accept) on the first waiting_user task
    const acceptBtn = page.locator('.accept-btn').first()
    await expect(acceptBtn).toBeVisible({ timeout: 5000 })
    await acceptBtn.click()
    await page.waitForTimeout(1500)

    // Verify status changed to accepted
    // The refresh-btn re-fetches the task list — use it
    await page.locator('.refresh-btn').click()
    await page.waitForTimeout(1000)
    await expect(page.locator('.task-status.accepted').first()).toBeVisible({ timeout: 5000 })

    // Reload and verify accepted status persists
    await page.reload()
    await page.waitForSelector('.workbench-page', { timeout: 10000 })
    await page.locator('.refresh-btn').click()
    await page.waitForTimeout(1000)
    await expect(page.locator('.task-status.accepted').first()).toBeVisible({ timeout: 5000 })

    await expect.poll(async () => page.evaluate(async (id) => {
      const token = localStorage.getItem('toonflow.token')
      const response = await fetch(`/api/v1/runtime/human-tasks?run_id=${id}`, { headers: { Authorization: `Bearer ${token}` } })
      const data = await response.json()
      return data.tasks?.[0]?.status
    }, runId)).toBe('accepted')
  })

  test('reject human task → reload → confirm', async ({ page }) => {
    await login(page)

    await page.fill('.create-form input', `E2E-HG-${Date.now()}`)
    await page.click('.create-form button[type="submit"]')
    await page.waitForURL('**/projects/**', { timeout: 15000 })
    const projectId = page.url().split('/projects/')[1]?.split('/')[0] || ''

    const { taskId } = await createCompiledGateRun(page)

    await page.goto(`/projects/${projectId}/workbench/human-tasks`)
    await page.waitForSelector('.workbench-page', { timeout: 10000 })
    await expect(page.locator(`text=${taskId.slice(0, 8)}`).first()).toBeVisible({ timeout: 5000 })

    // Click reject
    const rejectBtn = page.locator('.reject-btn').first()
    await expect(rejectBtn).toBeVisible({ timeout: 5000 })
    await rejectBtn.click()
    await page.waitForTimeout(1500)

    // Reload and verify rejected status persists
    await page.reload()
    await page.waitForSelector('.workbench-page', { timeout: 10000 })
    await page.locator('.refresh-btn').click()
    await page.waitForTimeout(1000)
    await expect(page.locator('.task-status.rejected').first()).toBeVisible({ timeout: 5000 })
  })
})
