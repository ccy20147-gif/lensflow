import { expect, test } from '@playwright/test'

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login'); const email = `architect-${Date.now()}@toonflow.local`
  const bootstrap = page.getByRole('button', { name: '初始化' })
  if (await bootstrap.isVisible({ timeout: 1500 }).catch(() => false)) { await page.fill('input[type="email"]', email); await page.fill('input[type="text"]', 'Architect'); await page.fill('input[type="password"]', 'password'); await bootstrap.click() }
  await page.fill('input[type="email"]', email); if (await page.locator('input[type="text"]').isVisible({ timeout: 500 }).catch(() => false)) await page.fill('input[type="text"]', 'Architect'); await page.fill('input[type="password"]', 'password')
  const signIn = page.getByRole('button', { name: '登录' }); if (await signIn.isVisible({ timeout: 1500 }).catch(() => false)) await signIn.click(); else await page.locator('button[type="submit"]').click(); await page.waitForURL('**/projects')
}

test('Architect fixture proposal shows decision review, applies once, and stale confirmation is rejected', async ({ page }) => {
  await login(page); await page.fill('.create-form input', `Architect ${Date.now()}`); await page.locator('.create-form button[type="submit"]').click(); await page.waitForURL('**/projects/**'); await page.getByRole('button', { name: '新建工作流' }).click(); await page.waitForURL('**/canvas?workflow_id=**')
  const workflowId = new URL(page.url()).searchParams.get('workflow_id')!
  const proposal = await page.evaluate(async (id) => { const token = localStorage.getItem('toonflow.token'); const r = await fetch('/api/v1/architect/test-fixtures/proposals', { method: 'POST', headers: {'Content-Type':'application/json', Authorization:`Bearer ${token}`}, body: JSON.stringify({workflow_id:id}) }); return r.json() }, workflowId) as { proposal_id: string }
  const review = await page.evaluate(async (id) => { const token = localStorage.getItem('toonflow.token'); const r = await fetch(`/api/v1/architect/proposals/${id}`, { headers:{Authorization:`Bearer ${token}`} }); return r.json() }, proposal.proposal_id) as { base_draft_hash: string; validation: { validated_plan_hash: string } }
  await page.getByLabel('Architect proposal ID').fill(proposal.proposal_id); await page.getByRole('button', { name: '查看差异' }).click()
  await expect(page.locator('.proposal-validation')).toContainText('预估成本'); await expect(page.locator('.proposal-validation')).toContainText('权限检查'); await expect(page.locator('.proposal-ops')).toContainText('add_node')
  await page.getByRole('button', { name: '确认并原子应用' }).click(); await expect(page.getByRole('button', { name: '已应用' })).toBeVisible()
  const duplicate = await page.evaluate(async ({ id, plan }) => { const token = localStorage.getItem('toonflow.token'); const r = await fetch(`/api/v1/architect/proposals/${id}/apply`, { method:'POST', headers:{'Content-Type':'application/json', Authorization:`Bearer ${token}`}, body:JSON.stringify({base_draft_hash: plan.base_draft_hash, validated_plan_hash: plan.validation.validated_plan_hash, idempotency_key:`architect-apply:${id}:${plan.validation.validated_plan_hash}`}) }); return {status:r.status, body:await r.json()} }, { id: proposal.proposal_id, plan: review })
  expect(duplicate.status).toBe(200); expect(duplicate.body.state).toBe('applied')

  const stale = await page.evaluate(async (id) => {
    const token = localStorage.getItem('toonflow.token')!
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    const fixture = await fetch('/api/v1/architect/test-fixtures/proposals', { method: 'POST', headers, body: JSON.stringify({ workflow_id: id }) }).then((response) => response.json())
    const proposal = await fetch(`/api/v1/architect/proposals/${fixture.proposal_id}`, { headers }).then((response) => response.json())
    const draft = await fetch(`/api/v1/workflows/${id}/draft`, { headers }).then((response) => response.json())
    const graph = { ...draft.graph, nodes: [...(draft.graph.nodes || []), { id: 'stale-user-edit', type: 'brief' }] }
    const mutation = await fetch(`/api/v1/workflows/${id}/draft`, { method: 'PUT', headers, body: JSON.stringify({ graph, config: draft.config || {}, layout: draft.layout || {}, base_graph_hash: draft.graph_hash, pinned_dependency_revisions: [] }) })
    const before = await fetch(`/api/v1/workflows/${id}/draft`, { headers }).then((response) => response.json())
    const apply = await fetch(`/api/v1/architect/proposals/${fixture.proposal_id}/apply`, { method: 'POST', headers, body: JSON.stringify({ base_draft_hash: proposal.base_draft_hash, validated_plan_hash: proposal.validation.validated_plan_hash, idempotency_key: `stale:${fixture.proposal_id}` }) })
    const after = await fetch(`/api/v1/workflows/${id}/draft`, { headers }).then((response) => response.json())
    return { mutation: mutation.status, apply: apply.status, before: before.graph_hash, after: after.graph_hash }
  }, workflowId)
  expect(stale.mutation).toBe(200)
  expect(stale.apply).toBe(409)
  expect(stale.after).toBe(stale.before)
})
