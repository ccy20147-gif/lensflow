import { expect, test } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: any) {
  await loginWithFreshAccount(page, 'request-input', 'Request Input')
}

test('RequestInput: create → typed submit → reload persists', async ({ page }) => {
  await login(page)
  await page.fill('.create-form input', `RequestInput-${Date.now()}`)
  await page.click('.create-form button[type="submit"]')
  await page.waitForURL('**/projects/**')
  const projectId = page.url().split('/projects/')[1]?.split('/')[0] || ''
  const seeded = await page.evaluate(async () => {
    const token = localStorage.getItem('toonflow.token')
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    const request = async (path: string, init: RequestInit = {}) => {
      const response = await fetch(path, { ...init, headers: { ...headers, ...(init.headers || {}) } })
      if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`)
      return response.json()
    }
    const identity = await request('/api/v1/identity/verify')
    const workflow = await request('/api/v1/workflows/', { method: 'POST', body: JSON.stringify({ owner_kind: 'user', owner_id: identity.account_id }) })
    const draft = await request(`/api/v1/workflows/${workflow.workflow_id}/draft`)
    await request(`/api/v1/workflows/${workflow.workflow_id}/draft`, { method: 'PUT', body: JSON.stringify({ graph: { nodes: [{ id: 'agent', type: 'agent_invoke' }], edges: [] }, config: {}, layout: {}, base_graph_hash: draft.graph_hash }) })
    const workflowRevision = await request(`/api/v1/workflows/${workflow.workflow_id}/revisions`, { method: 'POST', body: '{}' })
    const run = await request('/api/v1/runtime/workflow-runs', { method: 'POST', body: JSON.stringify({ workflow_revision_id: workflowRevision.revision_id, input_snapshot: {} }) })
    const snapshot = await request(`/api/v1/runtime/workflow-runs/${run.run_id}`)
    const node = snapshot.nodes.find((item: any) => item.node_instance_id === 'agent')
    const agent = await request('/api/v1/agents', { method: 'POST', body: JSON.stringify({ name: `Input agent ${Date.now()}`, description: '', agent_kind: 'configurable' }) })
    const revision = await request(`/api/v1/agents/${agent.agent_id}/revisions`, { method: 'POST', body: JSON.stringify({ body: { sop_steps: [{ step_id: 'ask', instruction: 'Ask for text' }], execution_policy: { provider_ref: 'atlascloud/test' } } }) })
    await request(`/api/v1/agents/${agent.agent_id}/revisions/${revision.revision_id}/promote`, { method: 'POST', body: '{}' })
    const task = await request(`/api/v1/agents/${agent.agent_id}/revisions/${revision.revision_id}/request-input`, { method: 'POST', body: JSON.stringify({ run_id: run.run_id, node_run_id: node.node_run_id, attempt_id: node.attempts[0].attempt_id, schema_ref: 'text@1', question: 'Provide text', timeout_minutes: 5, idempotency_token: crypto.randomUUID(), input_schema: { type: 'object', required: ['text'], properties: { text: { type: 'string' } } } }) })
    return { taskId: task.task_id }
  })
  await page.goto(`/projects/${projectId}/workbench/human-tasks`)
  await page.waitForSelector('.workbench-page')
  const card = page.locator('.task-card', { hasText: seeded.taskId.slice(0, 8) })
  await expect(card).toBeVisible()
  await card.locator('input[type="text"]').fill('typed answer')
  await card.locator('.accept-btn').click()
  await page.locator('.refresh-btn').click()
  await expect(card.locator('.task-status.accepted')).toBeVisible()
  await page.reload()
  await page.locator('.refresh-btn').click()
  await expect(page.locator('.task-card', { hasText: seeded.taskId.slice(0, 8) }).locator('.task-status.accepted')).toBeVisible()
})
