import { expect, test } from '@playwright/test'

async function login(page: any) {
  await page.goto('/login')
  await page.waitForSelector('.login-card', { timeout: 15_000 })
  const email = `canvas-resource-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@toonflow.local`
  const bootstrap = page.getByRole('button', { name: '初始化' })
  if (await bootstrap.isVisible({ timeout: 1_500 }).catch(() => false)) {
    await page.locator('input[type="email"]').fill(email)
    await page.locator('input[type="text"]').fill('Canvas Resource')
    await page.locator('input[type="password"]').fill('password')
    await bootstrap.click()
  }
  await page.locator('input[type="email"]').fill(email)
  if (await page.locator('input[type="text"]').isVisible({ timeout: 800 }).catch(() => false)) await page.locator('input[type="text"]').fill('Canvas Resource')
  await page.locator('input[type="password"]').fill('password')
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL('**/projects', { timeout: 15_000 })
}

test('canvas supports reversible palette editing and resource library has a canonical revision flow', async ({ page }) => {
  await login(page)
  await page.locator('.create-form input').fill(`Canvas Resource ${Date.now()}`)
  await page.locator('.create-form button[type="submit"]').click()
  await page.waitForURL('**/projects/**')

  await page.getByText('新建工作流').click()
  await page.waitForURL('**/canvas?workflow_id=**')
  await expect(page.locator('.canvas-toolbar')).toBeVisible()
  const paletteItem = page.locator('.palette-item').first()
  await expect(paletteItem).toBeVisible({ timeout: 10_000 })
  const before = await page.locator('.vue-flow__node').count()
  await paletteItem.click()
  await expect.poll(() => page.locator('.vue-flow__node').count()).toBe(before + 1)
  await page.getByRole('button', { name: '撤销' }).click()
  await expect.poll(() => page.locator('.vue-flow__node').count()).toBe(before)
  await page.getByRole('button', { name: '重做' }).click()
  await expect.poll(() => page.locator('.vue-flow__node').count()).toBe(before + 1)

  await page.goBack()
  await page.getByText('资源库').click()
  await page.waitForURL('**/resources')
  await page.getByRole('button', { name: '创建 Artifact' }).click()
  await expect(page.locator('.artifact-card').first()).toBeVisible()
  await page.getByRole('button', { name: '提升为资源' }).first().click()
  await expect(page.getByText('Resource 身份与版本')).toBeVisible()
  const resource = page.locator('.library-section').nth(1).locator('.resource-card').first()
  await expect(resource).toBeVisible()
  await resource.getByRole('button', { name: '冻结 Draft' }).click()
  await resource.getByRole('button', { name: '查看 lineage' }).click()
  await expect(page.getByText('固定版本 lineage')).toBeVisible()
  await page.getByRole('button', { name: '从 canonical 重建视图' }).click()
  await expect(page.getByText('Canonical 重建结果')).toBeVisible()
})
