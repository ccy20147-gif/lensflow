import { test, expect } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: any) {
  await loginWithFreshAccount(page, 'agents', 'Agents')
}

async function connect(page: any, source: any, target: any) {
  const from = await source.locator('.vue-flow__handle.source').boundingBox()
  const to = await target.locator('.vue-flow__handle.target').boundingBox()
  if (!from || !to) throw new Error('Agent node ports were not rendered')
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2)
  await page.mouse.down()
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 12 })
  await page.mouse.up()
}

test('published Agent revision appears in canvas and three fixed nodes compile as a DAG', async ({ page }) => {
  await login(page)
  const projectName = `Agent graph ${Date.now()}`
  await page.fill('.create-form input', projectName)
  await page.click('.create-form button[type="submit"]')
  await page.waitForURL('**/projects/**', { timeout: 15000 })
  const projectId = page.url().split('/projects/')[1]?.split('/')[0] || ''

  await page.goto(`/projects/${projectId}/agent-studio`)
  const agentName = `Story Agent ${Date.now()}`
  await page.fill('.create-form input[placeholder*="名称"]', agentName)
  await page.click('.create-form button[type="submit"]')
  await page.locator('.agent-card', { hasText: agentName }).click()
  await page.getByRole('textbox', { name: '输入 schema ref', exact: true }).fill('toonflow.story.v1')
  await page.getByRole('textbox', { name: '输出 schema ref', exact: true }).fill('toonflow.story.v1')
  await page.fill('.step label:nth-child(2) input', 'Return a typed story artifact')
  await page.click('text=提交不可变修订')
  await expect(page.locator('button:has-text("发布当前修订")')).toBeVisible({ timeout: 5000 })
  await page.click('button:has-text("发布当前修订")')

  await page.goto(`/projects/${projectId}`)
  await page.click('button:has-text("新建工作流")')
  await page.waitForURL('**/canvas?workflow_id=*', { timeout: 15000 })
  await expect(page.getByTestId('workflow-canvas')).toHaveAttribute('aria-busy', 'false', { timeout: 10000 })
  await expect(page.locator('.palette-item', { hasText: agentName })).toBeVisible({ timeout: 10000 })
  const agentPalette = page.locator('.palette-item', { hasText: agentName })
  await agentPalette.click(); await agentPalette.click(); await agentPalette.click()
  const agentNodes = page.locator('.vue-flow__node .registry-node', { hasText: agentName })
  await expect(agentNodes).toHaveCount(3)
  const first = agentNodes.nth(0); const second = agentNodes.nth(1); const third = agentNodes.nth(2)
  await connect(page, first, second); await connect(page, second, third)
  await Promise.all([
    page.waitForResponse((response) => response.request().method() === 'PUT'
      && /\/api\/v1\/workflows\/[^/]+\/draft$/.test(response.url())
      && response.status() === 200),
    page.getByRole('button', { name: '保存', exact: true }).click(),
  ])
  await page.getByTestId('workflow-compile').click()
  // Compilation reads the persisted registry snapshot before responding; the
  // E2E database contains the full catalog, so this is not a UI animation wait.
  await expect(page.getByTestId('compile-result')).toContainText('编译通过', { timeout: 30_000 })
  await page.click('button:has-text("发布并运行")')
  // With no AtlasCloud credentials, dispatch is visibly blocked at execution;
  // publishing itself must succeed and preserve the fixed Agent revisions.
  await expect(page.locator('.run-status')).toContainText('已启动运行', { timeout: 10000 })
})
