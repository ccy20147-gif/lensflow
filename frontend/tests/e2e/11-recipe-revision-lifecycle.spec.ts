import { expect, test } from '@playwright/test'

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.waitForSelector('.login-card')
  const email = `recipe-revision-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@toonflow.local`
  const bootstrap = page.getByRole('button', { name: '初始化' })
  if (await bootstrap.isVisible({ timeout: 2_000 }).catch(() => false)) {
    await page.fill('input[type="email"]', email)
    await page.fill('input[type="text"]', 'Recipe Revision')
    await page.fill('input[type="password"]', 'password')
    await bootstrap.click()
  }
  await page.fill('input[type="email"]', email)
  if (await page.locator('input[type="text"]').isVisible({ timeout: 1_000 }).catch(() => false)) {
    await page.fill('input[type="text"]', 'Recipe Revision')
  }
  await page.fill('input[type="password"]', 'password')
  const signIn = page.getByRole('button', { name: '登录' })
  if (await signIn.isVisible({ timeout: 2_000 }).catch(() => false)) await signIn.click()
  else await page.locator('button[type="submit"]').click()
  await page.waitForURL('**/projects')
}

test('Recipe Lab persists a CAS revision, promotes it, and displays an A/B diff', async ({ page }) => {
  await login(page)
  await page.goto('/recipe')
  await expect(page.locator('.recipe-page')).toBeVisible()

  const recipeName = `Recipe lifecycle ${Date.now()}`
  await page.fill('.create-form input[placeholder="Recipe 名称"]', recipeName)
  await page.getByRole('button', { name: '创建 Recipe' }).click()
  const card = page.locator('.recipe-card', { hasText: recipeName })
  await expect(card).toBeVisible()
  await card.click()
  await expect(page.locator('.lab-editor')).toBeVisible()

  await page.getByRole('button', { name: 'validate', exact: true }).click()
  await expect(page.locator('.result')).toContainText('通过')
  await page.getByRole('button', { name: '提交草稿修订' }).click()
  await expect(page.locator('.versions')).toContainText('r1')
  await expect(page.locator('.versions')).toContainText('draft')
  await page.getByRole('button', { name: '发布', exact: true }).click()
  await expect(page.locator('.versions')).toContainText('active')

  // Change the public parameter contract, creating revision B with r1's
  // content hash as the CAS base hash. The Lab must not mutate r1.
  await page.locator('.contract-grid textarea').nth(3).fill('{"type":"object","properties":{"seed":{"type":"integer","default":42}}}')
  await page.getByRole('button', { name: '提交草稿修订' }).click()
  await expect(page.locator('.versions')).toContainText('r2')
  await expect(page.locator('.versions')).toContainText('draft')

  await page.locator('.revision-select', { hasText: 'r1' }).click()
  await page.locator('.revision', { hasText: 'r2' }).getByRole('button', { name: '与当前比较', exact: true }).click()
  await expect(page.locator('.diff')).toContainText('parameter_schema')
})
