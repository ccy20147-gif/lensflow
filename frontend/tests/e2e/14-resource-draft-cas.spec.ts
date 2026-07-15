import { expect, test } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: any) {
  await loginWithFreshAccount(page, 'resource-cas', 'Resource')
}

test('Resource Draft CAS, freeze and immutable revision diff are visible in browser', async ({ page }) => {
  await login(page)
  await page.fill('.create-form input', `Resource ${Date.now()}`); await page.locator('.create-form button[type="submit"]').click(); await page.waitForURL('**/projects/**')
  const projectId = page.url().split('/projects/')[1]?.split('/')[0] || ''
  const seeded = await page.evaluate(async () => {
    const token = localStorage.getItem('toonflow.token'); const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    const request = async (path: string, init: RequestInit = {}) => { const r = await fetch(path, { ...init, headers: { ...headers, ...(init.headers || {}) } }); if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`); return r.json() }
    const a = await request('/api/v1/artifacts/versions', { method: 'POST', body: JSON.stringify({ schema_id: 'shot_plan', schema_version: 1, content_json: { title: 'A', shot: 1 } }) })
    const b = await request('/api/v1/artifacts/versions', { method: 'POST', body: JSON.stringify({ schema_id: 'shot_plan', schema_version: 1, content_json: { title: 'B', shot: 2 } }) })
    const resource = await request('/api/v1/artifacts/resources', { method: 'POST', body: JSON.stringify({ resource_type: 'shot_plan', content_artifact_version_id: a.artifact_version_id }) })
    const first = await request(`/api/v1/artifacts/resources/${resource.resource_id}/draft`, { method: 'PUT', body: JSON.stringify({ content_artifact_version_id: b.artifact_version_id, base_draft_version: resource.draft_version }) })
    await request(`/api/v1/artifacts/resources/${resource.resource_id}/revisions`, { method: 'POST', body: JSON.stringify({ base_draft_version: first.draft_version }) })
    const second = await request(`/api/v1/artifacts/resources/${resource.resource_id}/draft`, { method: 'PUT', body: JSON.stringify({ content_artifact_version_id: a.artifact_version_id, base_draft_version: first.draft_version }) })
    await request(`/api/v1/artifacts/resources/${resource.resource_id}/revisions`, { method: 'POST', body: JSON.stringify({ base_draft_version: second.draft_version }) })
    const stale = await fetch(`/api/v1/artifacts/resources/${resource.resource_id}/draft`, { method: 'PUT', headers, body: JSON.stringify({ content_artifact_version_id: b.artifact_version_id, base_draft_version: first.draft_version }) })
    return { resourceId: resource.resource_id, staleStatus: stale.status }
  })
  expect(seeded.staleStatus).toBe(409)
  await page.goto(`/projects/${projectId}/resources`); await page.getByRole('button', { name: '编辑 Draft' }).click()
  await expect(page.getByLabel('Resource Draft 编辑器')).toBeVisible(); await expect(page.getByRole('button', { name: '比较 Revision' }).first()).toBeVisible(); await page.getByRole('button', { name: '比较 Revision' }).first().click(); await expect(page.getByLabel('Resource Revision 差异')).toContainText('changed_keys')
})
