import { expect, test } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: any) {
  await loginWithFreshAccount(page, 'template-e2e', 'Template E2E')
}

test('benchmark template instantiates to canvas and exposes missing AtlasCloud execution state', async ({ page }) => {
  await login(page)
  await page.goto('/templates')
  // Only the deployment maintainer can seed official packages. Browser users
  // consume the resulting gallery and never receive that credential.
  await page.evaluate(async () => {
    const token = localStorage.getItem('toonflow.token')
    await fetch('/api/v1/templates/benchmarks/seed', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'X-Template-Admin-Key': 'e2e-template-maintainer' },
    })
  })
  await page.reload()
  // The gallery lists newest templates first. Consume the fresh seed rather
  // than a historical benchmark revision retained by a long-lived local DB.
  const card = page.locator('.template-row', { hasText: '广告创意候选与人工精修' }).first()
  await expect(card).toBeVisible()
  await card.click()
  await expect(page.locator('.template-detail h3')).toHaveText('广告创意候选与人工精修')
  await page.locator('.instantiate-btn').click()
  await page.waitForURL('**/projects/**/canvas?workflow_id=*')

  const workflowId = new URL(page.url()).searchParams.get('workflow_id')
  const projectId = page.url().split('/projects/')[1]?.split('/')[0] || ''
  expect(workflowId).toBeTruthy()
  await expect(page.locator('.canvas-page')).toBeVisible()
  await expect(page.locator('.registry-node')).toHaveCount(8)
  await expect(page.locator('.registry-node', { hasText: 'brief' })).toBeVisible()
  await expect(page.locator('.registry-node', { hasText: 'workbench_task' })).toBeVisible()
  // This package deliberately includes AtlasCloud-backed nodes. The E2E
  // deployment has no credential, so the browser must make that limitation
  // explicit instead of pretending the package can reach a Human Gate.
  await expect(page.locator('.palette-item', { hasText: 'Provider 未配置' }).first()).toBeVisible()
  await page.getByRole('button', { name: '发布并运行' }).click()
  await expect(page.locator('.run-status')).toContainText('已启动运行')
  // The run ID is visible, but this test intentionally does not navigate to
  // Workbench: no browser user can bypass the unavailable Provider nodes.
  expect((await page.locator('.run-status').textContent())?.match(/[0-9a-f-]{36}/i)?.[0]).toBeTruthy()
})

test('minimal provider-free workflow lets a browser commit a Workbench Artifact and inspect its trace', async ({ page }) => {
  await login(page)
  await page.locator('.create-form input').fill(`Workbench Resource ${Date.now()}`)
  await page.locator('.create-form button[type="submit"]').click()
  await page.waitForURL('**/projects/**')
  const projectId = page.url().split('/projects/')[1]?.split('/')[0]
  expect(projectId).toBeTruthy()
  const fixture = await page.evaluate(async () => {
    const token = localStorage.getItem('toonflow.token')
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    const post = async (path: string, body: unknown = {}) => {
      const response = await fetch(`/api/v1${path}`, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`)
      return response.json()
    }
    const workflow = await post('/workflows/')
    const draftResponse = await fetch(`/api/v1/workflows/${workflow.workflow_id}/draft`, { headers })
    if (!draftResponse.ok) throw new Error(`draft: ${draftResponse.status}`)
    const draft = await draftResponse.json()
    const graph = {
      nodes: [{
        id: 'manual_workbench', type: 'workbench_task',
        config: {
          target_workbench: 'manual-review', output_schema_ref: 'workbench_result.v1', resource_type: 'workbench_result',
        },
      }],
      edges: [],
    }
    const saved = await fetch(`/api/v1/workflows/${workflow.workflow_id}/draft`, {
      method: 'PUT', headers,
      body: JSON.stringify({ graph, config: {}, layout: {}, base_graph_hash: draft.graph_hash, pinned_dependency_revisions: [] }),
    })
    if (!saved.ok) throw new Error(`save draft: ${saved.status} ${await saved.text()}`)
    const revision = await post(`/workflows/${workflow.workflow_id}/revisions`)
    const run = await post('/runtime/workflow-runs', { workflow_revision_id: revision.revision_id, input_snapshot: {} })
    return { workflowId: workflow.workflow_id as string, runId: run.run_id as string }
  })

  // Artifact creation, result selection, submission and trace inspection are
  // all user-visible browser interactions. The API above only arranged the
  // immutable, Provider-free workflow fixture.
  await page.goto(`/projects/${projectId}/resources`)
  await page.getByLabel('Artifact Schema').fill('workbench_result')
  await page.getByLabel('Artifact JSON').fill('{"title":"浏览器提交的人工结果"}')
  await page.getByRole('button', { name: '创建 Artifact' }).click()
  await expect(page.locator('.artifact-card', { hasText: 'workbench_result v1' })).toBeVisible()

  await page.goto(`/projects/${projectId}/workbench/human-tasks`)
  const task = page.locator('.task-card', { hasText: 'workbench_task' })
  await expect(task).toBeVisible({ timeout: 30_000 })
  const artifactSelect = task.getByLabel('结果 ArtifactVersion')
  await expect(artifactSelect.locator('option')).toHaveCount(2)
  await artifactSelect.selectOption({ index: 1 })
  await task.getByRole('button', { name: '提交结果' }).click()
  await expect(task.locator('.task-status.accepted')).toBeVisible()

  await page.getByRole('button', { name: '运行 Trace' }).click()
  await page.getByLabel('Run ID').fill(fixture.runId)
  await page.getByRole('button', { name: '加载 Trace' }).click()
  await expect(page.locator('.trace-list')).toContainText('manual_workbench')
  await expect(page.locator('.trace-list')).toContainText('已提交资源')
  await expect(page.locator('.trace-list')).toContainText('workbench_result')
})

test('typed replacement selector exposes only owner-compatible immutable revisions', async ({ page }) => {
  await login(page)
  const fixture = await page.evaluate(async () => {
    const token = localStorage.getItem('toonflow.token')
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    const request = async (path: string, body?: unknown, extra: Record<string, string> = {}) => {
      const response = await fetch(`/api/v1${path}`, { method: 'POST', headers: { ...headers, ...extra }, body: body === undefined ? undefined : JSON.stringify(body) })
      if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`)
      return response.json()
    }
    const artifact = await request('/artifacts/versions', { schema_id: 'toonflow.world.v1', content_json: { title: 'owner replacement' } })
    const resource = await request('/artifacts/resources', { resource_type: 'world', content_artifact_version_id: artifact.artifact_version_id })
    const revision = await request(`/artifacts/resources/${resource.resource_id}/revisions`, { base_draft_version: 1 })
    await request('/templates/benchmarks/seed', {}, { 'X-Template-Admin-Key': 'e2e-template-maintainer' })
    // Publish a minimal public-node source revision. Benchmark graphs include
    // provider policy fixtures that are deliberately rejected by instance
    // preflight, so they are not suitable as this selector fixture's source.
    const workflow = await request('/workflows/', {})
    const draft = await (await fetch(`/api/v1/workflows/${workflow.workflow_id}/draft`, { headers })).json()
    await (await fetch(`/api/v1/workflows/${workflow.workflow_id}/draft`, {
      method: 'PUT', headers,
      body: JSON.stringify({ graph: { nodes: [{ id: 'brief', type: 'brief', config: {} }], edges: [] }, config: {}, layout: {}, base_graph_hash: draft.graph_hash }),
    })).json()
    const source = await request(`/workflows/${workflow.workflow_id}/revisions`, {})
    const template = await request('/templates', {
      name: 'E2E typed replacement', workflow_revision_id: source.revision_id, visibility: 'private',
      manifest: {
        name: 'E2E typed replacement',
        dependencies: [{ dep_id: 'world', kind: 'resource', revision_id: revision.revision_id, replacement_slot: 'world_slot' }],
        replacement_slots: [{ slot_id: 'world_slot', label: 'World replacement', expected_kind: 'resource', required: true }],
      },
    }, { 'X-Template-Admin-Key': 'e2e-template-maintainer' })
    // A separate account creates a valid-looking revision. It must not be
    // returned through this owner's replacement-option endpoint.
    const otherEmail = `replacement-other-${Date.now()}@toonflow.local`
    await fetch('/api/v1/identity/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: otherEmail, display_name: 'Other', password: 'password' }) })
    const otherLogin = await (await fetch('/api/v1/identity/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: otherEmail, password: 'password' }) })).json()
    const otherHeaders = { Authorization: `Bearer ${otherLogin.token}`, 'Content-Type': 'application/json' }
    const otherArtifact = await (await fetch('/api/v1/artifacts/versions', { method: 'POST', headers: otherHeaders, body: JSON.stringify({ schema_id: 'toonflow.world.v1', content_json: { title: 'foreign' } }) })).json()
    const otherResource = await (await fetch('/api/v1/artifacts/resources', { method: 'POST', headers: otherHeaders, body: JSON.stringify({ resource_type: 'world', content_artifact_version_id: otherArtifact.artifact_version_id }) })).json()
    const otherRevision = await (await fetch(`/api/v1/artifacts/resources/${otherResource.resource_id}/revisions`, { method: 'POST', headers: otherHeaders, body: JSON.stringify({ base_draft_version: 1 }) })).json()
    return { templateId: template.template_id as string, revisionId: revision.revision_id as string, foreignRevisionId: otherRevision.revision_id as string }
  })
  await page.goto('/templates')
  await page.locator('.template-row', { hasText: 'E2E typed replacement' }).click()
  const selector = page.getByLabel('World replacement')
  await expect(selector).toBeVisible()
  await expect(selector.locator(`option[value="${fixture.revisionId}"]`)).toHaveCount(1)
  await expect(selector.locator(`option[value="${fixture.foreignRevisionId}"]`)).toHaveCount(0)
  await selector.selectOption(fixture.revisionId)
  await page.locator('.instantiate-btn').click()
  await page.waitForURL('**/projects/**/canvas?workflow_id=*')
})
