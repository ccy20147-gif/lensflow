import { test, expect } from '@playwright/test'

async function login(page: any) {
  await page.goto('/login')
  await page.waitForSelector('.login-card', { timeout: 15000 })
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@toonflow.local`
  const bootstrapBtn = page.locator('button:has-text("初始化")')
  if (await bootstrapBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    await page.fill('input[type="email"]', email)
    await page.fill('input[type="text"]', 'E2E')
    await page.fill('input[type="password"]', 'password')
    await bootstrapBtn.click()
    await page.waitForTimeout(1000)
  }
  await page.fill('input[type="email"]', email)
  if (await page.locator('input[type="text"]').isVisible({ timeout: 1000 }).catch(() => false)) {
    await page.fill('input[type="text"]', 'E2E')
  }
  await page.fill('input[type="password"]', 'password')
  const loginBtn = page.locator('button:has-text("登录")')
  if (await loginBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    await loginBtn.click()
  } else {
    await page.click('button[type="submit"]')
  }
  await page.waitForURL('**/projects', { timeout: 15000 })
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
})