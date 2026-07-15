import { expect, test } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: import('@playwright/test').Page) {
  await loginWithFreshAccount(page, 'recipe-canvas', 'Recipe Canvas')
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
