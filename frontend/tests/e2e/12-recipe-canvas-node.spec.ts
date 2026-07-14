import { expect, test } from '@playwright/test'

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login')
  const email = `recipe-canvas-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@toonflow.local`
  const bootstrap = page.getByRole('button', { name: '初始化' })
  if (await bootstrap.isVisible({ timeout: 2_000 }).catch(() => false)) {
    await page.fill('input[type="email"]', email); await page.fill('input[type="text"]', 'Recipe Canvas'); await page.fill('input[type="password"]', 'password'); await bootstrap.click()
  }
  await page.fill('input[type="email"]', email)
  if (await page.locator('input[type="text"]').isVisible({ timeout: 1_000 }).catch(() => false)) await page.fill('input[type="text"]', 'Recipe Canvas')
  await page.fill('input[type="password"]', 'password')
  const signIn = page.getByRole('button', { name: '登录' })
  if (await signIn.isVisible({ timeout: 2_000 }).catch(() => false)) await signIn.click(); else await page.locator('button[type="submit"]').click()
  await page.waitForURL('**/projects')
}

test('published internal Recipe appears as one canvas node and starts a workflow run', async ({ page }) => {
  await login(page)
  await page.fill('.create-form input', `Recipe canvas project ${Date.now()}`)
  await page.locator('.create-form button[type="submit"]').click()
  await page.waitForURL('**/projects/**')
  const projectUrl = page.url()
  await page.getByRole('button', { name: 'Recipe', exact: true }).click()
  await expect(page.locator('.recipe-page')).toBeVisible()
  const recipeName = `Canvas recipe ${Date.now()}`
  await page.fill('.create-form input[placeholder="Recipe 名称"]', recipeName)
  await page.getByRole('button', { name: '创建 Recipe' }).click()
  await page.locator('.recipe-card', { hasText: recipeName }).click()
  // Use an internal operator so this path remains runnable in a credentialless
  // demo deployment while still using the AtlasCloud-only provider policy.
  await page.locator('.operator-row').nth(1).locator('select[aria-label="Operator 类型"]').selectOption('format_convert')
  await page.getByRole('button', { name: '提交草稿修订' }).click()
  await page.getByRole('button', { name: '发布', exact: true }).click()

  await page.goto(projectUrl)
  await page.getByRole('button', { name: '新建工作流' }).click()
  await page.waitForURL('**/canvas?workflow_id=**')
  const recipePalette = page.locator('.palette-item', { hasText: recipeName })
  await expect(recipePalette).toBeVisible()
  await expect(recipePalette).toBeEnabled()
  await recipePalette.click()
  await expect(page.locator('.vue-flow__node', { hasText: recipeName })).toBeVisible()
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await page.getByRole('button', { name: '发布并运行', exact: true }).click()
  await expect(page.locator('.run-status')).toContainText('已启动运行')
})
