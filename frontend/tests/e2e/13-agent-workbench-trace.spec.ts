import { expect, test } from '@playwright/test'

async function login(page: any) {
  const email = `agent-trace-${Date.now()}@toonflow.local`
  await page.goto('/login')
  const bootstrap = page.getByRole('button', { name: '初始化' })
  if (await bootstrap.isVisible({ timeout: 1_000 }).catch(() => false)) {
    await page.fill('input[type="email"]', email); await page.fill('input[type="text"]', 'Agent Trace'); await page.fill('input[type="password"]', 'password'); await bootstrap.click()
    await page.getByRole('button', { name: '登录' }).waitFor()
    await page.fill('input[type="email"]', email); await page.fill('input[type="password"]', 'password'); await page.getByRole('button', { name: '登录' }).click()
    await page.waitForURL('**/projects')
    return
  }
  await page.getByRole('button', { name: '登录' }).waitFor()
  await page.getByRole('link', { name: '注册' }).click()
  await page.fill('input[type="email"]', email); await page.fill('input[type="text"]', 'Agent Trace'); await page.fill('input[type="password"]', 'password')
  await page.getByRole('button', { name: '注册' }).click()
  await page.getByRole('link', { name: '登录' }).waitFor()
  await page.getByRole('link', { name: '登录' }).click()
  await page.fill('input[type="email"]', email); await page.fill('input[type="password"]', 'password'); await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL('**/projects')
}

test('Workbench renders owner Agent trace and RequestInput recovery after refresh', async ({ page }) => {
  await login(page)
  await page.fill('.create-form input', `Trace Project ${Date.now()}`); await page.locator('.create-form button[type="submit"]').click(); await page.waitForURL('**/projects/**')
  const projectId = page.url().split('/projects/')[1]?.split('/')[0] || ''
  const seeded = await page.evaluate(async () => {
    const token = localStorage.getItem('toonflow.token'); const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    const request = async (path: string, init: RequestInit = {}) => { const r = await fetch(path, { ...init, headers: { ...headers, ...(init.headers || {}) } }); if (!r.ok) throw new Error(`${path}: ${r.status}`); return r.json() }
    const workflow = await request('/api/v1/workflows/', { method: 'POST', body: '{}' }); const draft = await request(`/api/v1/workflows/${workflow.workflow_id}/draft`)
    await request(`/api/v1/workflows/${workflow.workflow_id}/draft`, { method: 'PUT', body: JSON.stringify({ graph: { nodes: [{ id: 'agent', type: 'agent_invoke' }], edges: [] }, config: {}, layout: {}, base_graph_hash: draft.graph_hash }) })
    const revision = await request(`/api/v1/workflows/${workflow.workflow_id}/revisions`, { method: 'POST', body: '{}' }); const run = await request('/api/v1/runtime/workflow-runs', { method: 'POST', body: JSON.stringify({ workflow_revision_id: revision.revision_id, input_snapshot: {} }) }); const snapshot = await request(`/api/v1/runtime/workflow-runs/${run.run_id}`)
    const node = snapshot.nodes[0]; const agent = await request('/api/v1/agents', { method: 'POST', body: JSON.stringify({ name: `Trace Agent ${Date.now()}` }) }); const agentRevision = await request(`/api/v1/agents/${agent.agent_id}/revisions`, { method: 'POST', body: JSON.stringify({ body: { sop_steps: [{ step_id: 'ask', instruction: 'Ask' }], execution_policy: { provider_ref: 'atlascloud/test' } } }) }); await request(`/api/v1/agents/${agent.agent_id}/revisions/${agentRevision.revision_id}/promote`, { method: 'POST', body: '{}' })
    await request(`/api/v1/agents/${agent.agent_id}/revisions/${agentRevision.revision_id}/request-input`, { method: 'POST', body: JSON.stringify({ run_id: run.run_id, node_run_id: node.node_run_id, attempt_id: node.attempts[0].attempt_id, schema_ref: 'text@1', question: 'Provide text', timeout_minutes: 5, idempotency_token: crypto.randomUUID(), input_schema: { type: 'object', properties: { text: { type: 'string' } } } }) })
    return { runId: run.run_id, revisionId: agentRevision.revision_id }
  })
  await page.goto(`/projects/${projectId}/workbench/human-tasks`); await page.getByRole('button', { name: '运行 Trace' }).click(); await page.getByLabel('Run ID').fill(seeded.runId); await page.getByRole('button', { name: '加载 Trace' }).click()
  const detail = page.locator('.agent-trace-list'); await expect(detail).toContainText(seeded.revisionId.slice(0, 8)); await expect(detail).toContainText('RequestInput: waiting')
  await page.reload(); await page.getByRole('button', { name: '运行 Trace' }).click(); await page.getByLabel('Run ID').fill(seeded.runId); await page.getByRole('button', { name: '加载 Trace' }).click(); await expect(page.locator('.agent-trace-list')).toContainText('RequestInput: waiting')
})
