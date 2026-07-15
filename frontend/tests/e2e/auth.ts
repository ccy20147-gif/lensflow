import type { Page } from '@playwright/test'

export async function loginWithFreshAccount(
  page: Page,
  prefix: string,
  displayName: string,
): Promise<void> {
  const email = `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@toonflow.local`

  await page.goto('/login')
  await page.waitForSelector('.login-card', { timeout: 15_000 })

  const bootstrap = page.getByRole('button', { name: '初始化', exact: true })
  if (await bootstrap.isVisible({ timeout: 1_500 }).catch(() => false)) {
    await page.fill('input[type="email"]', email)
    await page.fill('input[type="text"]', displayName)
    await page.fill('input[type="password"]', 'password')
    await bootstrap.click()
  } else {
    await page.getByRole('link', { name: '注册', exact: true }).click()
    await page.fill('input[type="email"]', email)
    await page.fill('input[type="text"]', displayName)
    await page.fill('input[type="password"]', 'password')
    await page.getByRole('button', { name: '注册', exact: true }).click()
    await page.getByRole('link', { name: '登录', exact: true }).click()
  }

  await page.fill('input[type="email"]', email)
  await page.fill('input[type="password"]', 'password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.waitForURL('**/projects', { timeout: 15_000 })
}
