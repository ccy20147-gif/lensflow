import { test, expect } from '@playwright/test'

/**
 * E2E: 01 — Login + create project via real UI.
 */

test.describe('01 · Login + Project', () => {
  test('login → create project → detail page', async ({ page }) => {
    await page.goto('/login')
    await page.waitForSelector('.login-card', { timeout: 15000 })

    const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@toonflow.local`

    // Check if bootstrap or login mode is shown
    const bootstrapBtn = page.locator('button:has-text("初始化")')
    if (await bootstrapBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      // Bootstrap mode
      await page.fill('input[type="email"]', email)
      await page.fill('input[type="text"]', 'E2E')
      await page.fill('input[type="password"]', 'password')
      await bootstrapBtn.click()
      await page.waitForTimeout(1000)
    }

    // Now in login mode — fill credentials and log in
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

    // Should redirect to /projects
    await page.waitForURL('**/projects', { timeout: 15000 })
    await expect(page.locator('.projects-page')).toBeVisible({ timeout: 10000 })

    // Create a project
    const projectName = `e2e-proj-${Date.now()}`
    await page.fill('.create-form input', projectName)
    await page.click('.create-form button[type="submit"]')

    // Should redirect to project detail
    await page.waitForURL('**/projects/**', { timeout: 15000 })
    await expect(page.locator('.project-detail')).toBeVisible({ timeout: 10000 })
  })
})