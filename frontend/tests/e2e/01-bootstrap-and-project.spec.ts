import { test, expect } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

/**
 * E2E: 01 — Login + create project via real UI.
 */

test.describe('01 · Login + Project', () => {
  test('login → create project → detail page', async ({ page }) => {
    await loginWithFreshAccount(page, 'bootstrap-project', 'E2E')
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
