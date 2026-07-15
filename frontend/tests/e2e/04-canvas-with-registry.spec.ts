import { test, expect } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: any) {
  await loginWithFreshAccount(page, 'canvas-registry', 'E2E')
}

test.describe('04 · Canvas with Registry', () => {
  test('canvas loads node palette and persists nodes', async ({ page }) => {
    await login(page)
    await page.fill('.create-form input', `E2E-Canvas-${Date.now()}`)
    await page.click('.create-form button[type="submit"]')
    await page.waitForURL('**/projects/**', { timeout: 15000 })
    await page.click('text=新建工作流')
    await page.waitForURL('**/canvas?workflow_id=**', { timeout: 15000 })
    await expect(page.locator('.node-palette')).toBeVisible({ timeout: 10000 })
    await page.click('text=保存')
    await page.waitForTimeout(500)
    await page.reload()
    await page.waitForURL('**/canvas?workflow_id=**', { timeout: 15000 })
    await expect(page.locator('.node-palette')).toBeVisible({ timeout: 10000 })
  })

  test('canvas keyboard controls remain focusable and operate on palette nodes', async ({ page }) => {
    await login(page)
    await page.fill('.create-form input', `E2E-Keyboard-${Date.now()}`)
    await page.click('.create-form button[type="submit"]')
    await page.waitForURL('**/projects/**', { timeout: 15000 })
    await page.click('text=新建工作流')
    await page.waitForURL('**/canvas?workflow_id=**', { timeout: 15000 })
    const palette = page.locator('.palette-item').first()
    await expect(palette).toBeVisible()
    await palette.click()
    await expect(page.locator('.vue-flow__node')).toHaveCount(1)
    await page.locator('.vue-flow__node').click()
    await page.getByLabel('复制选中节点').focus()
    await page.keyboard.press('Control+C')
    await page.keyboard.press('Control+V')
    await expect(page.locator('.vue-flow__node')).toHaveCount(2)
    await page.keyboard.press('Control+Z')
    await expect(page.locator('.vue-flow__node')).toHaveCount(1)
  })

  test('retired unknown node remains readonly and preserves raw draft JSON', async ({ page }) => {
    await login(page)
    await page.fill('.create-form input', `E2E-Retired-${Date.now()}`)
    await page.click('.create-form button[type="submit"]')
    await page.waitForURL('**/projects/**', { timeout: 15000 })
    await page.click('text=新建工作流')
    await page.waitForURL('**/canvas?workflow_id=**', { timeout: 15000 })
    const workflowId = new URL(page.url()).searchParams.get('workflow_id')!
    await page.evaluate(async (id) => {
      const token = localStorage.getItem('toonflow.token')
      const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
      const draft = await (await fetch(`/api/v1/workflows/${id}/draft`, { headers })).json()
      const raw = { id: 'retired-node', type: 'retired.definition.v1', label: 'Historical node', data: { node_type_id: 'retired.definition.v1', config: { preserved: true }, opaque: { keep: 'exact' } } }
      const response = await fetch(`/api/v1/workflows/${id}/draft`, { method: 'PUT', headers, body: JSON.stringify({ graph: { nodes: [raw], edges: [] }, config: {}, layout: { nodes: { 'retired-node': { x: 40, y: 40 } } }, base_graph_hash: draft.graph_hash, pinned_dependency_revisions: [] }) })
      if (!response.ok) throw new Error(await response.text())
    }, workflowId)
    await page.reload()
    const node = page.locator('.vue-flow__node').first()
    await expect(node).toContainText('retired.definition.v1')
    await node.click()
    await expect(page.locator('.provider-warn')).toContainText('只读占位')
    await expect(page.getByRole('button', { name: '删除节点' })).toBeDisabled()
    await page.getByRole('button', { name: '保存' }).click()
    await page.reload()
    const persisted = await page.evaluate(async (id) => {
      const token = localStorage.getItem('toonflow.token')
      return (await (await fetch(`/api/v1/workflows/${id}/draft`, { headers: { Authorization: `Bearer ${token}` } })).json()).graph.nodes[0]
    }, workflowId)
    expect(persisted.label).toBe('Historical node')
    expect(persisted.data.opaque.keep).toBe('exact')
  })
})
