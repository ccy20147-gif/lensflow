import { expect, test } from '@playwright/test'

async function login(page: any) {
  const email = `trial-${Date.now()}@toonflow.local`
  await page.goto('/login')
  await page.waitForSelector('.login-card')
  const bootstrap = page.getByRole('button', { name: '初始化' })
  if (await bootstrap.isVisible({ timeout: 1000 }).catch(() => false)) {
    await page.fill('input[type="email"]', email)
    await page.fill('input[type="text"]', 'Trial')
    await page.fill('input[type="password"]', 'password')
    await bootstrap.click()
    await page.waitForTimeout(500)
  }
  await page.fill('input[type="email"]', email)
  if (await page.locator('input[type="text"]').isVisible({ timeout: 500 }).catch(() => false)) await page.fill('input[type="text"]', 'Trial')
  await page.fill('input[type="password"]', 'password')
  const loginButton = page.getByRole('button', { name: '登录' })
  if (await loginButton.isVisible({ timeout: 500 }).catch(() => false)) await loginButton.click()
  else await page.locator('button[type="submit"]').click()
  await page.waitForURL('**/projects')
}

test('Agent trial RequestInput survives refresh and accepts one typed answer', async ({ page }) => {
  await login(page)
  await page.goto('/agent')
  const name = `Trial Agent ${Date.now()}`
  await page.fill('.create-form input[placeholder*="名称"]', name)
  await page.locator('.create-form button[type="submit"]').click()
  const card = page.locator('.agent-card', { hasText: name })
  await card.click()
  await page.fill('.step label:nth-child(2) input', 'Return typed text')
  await page.getByRole('button', { name: '隔离试跑' }).click()
  await expect(page.locator('.trial')).toBeVisible()
  await page.getByRole('button', { name: '创建补问' }).click()
  await expect(page.locator('.trial')).toContainText('waiting')
  await page.reload()
  await page.locator('.agent-card', { hasText: name }).click()
  await expect(page.locator('.trial')).toContainText('waiting')
  await page.locator('input[aria-label="Trial answer JSON"]').fill('{"choice":"yes"}')
  await page.getByRole('button', { name: '提交回答' }).click()
  await expect(page.locator('.trial')).toContainText('accepted')
})
