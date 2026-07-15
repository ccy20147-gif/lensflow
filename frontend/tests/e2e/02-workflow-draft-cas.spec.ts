import { test, expect } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: any) {
  await loginWithFreshAccount(page, 'workflow-cas', 'E2E')
}

test.describe('02 · Workflow Draft CAS', () => {
  test('canvas save → reload → persist → dry-run', async ({ page }) => {
    await login(page)

    await page.fill('.create-form input', `E2E-WF-${Date.now()}`)
    await page.click('.create-form button[type="submit"]')
    await page.waitForURL('**/projects/**', { timeout: 15000 })

    await page.click('text=新建工作流')
    await page.waitForURL('**/canvas?workflow_id=**', { timeout: 15000 })

    const canvas = page.getByTestId('workflow-canvas')
    await expect(canvas).toHaveAttribute('aria-busy', 'false', { timeout: 10000 })
    await Promise.all([
      page.waitForResponse((response) => response.request().method() === 'PUT'
        && /\/api\/v1\/workflows\/[^/]+\/draft$/.test(response.url())
        && response.status() === 200),
      page.getByRole('button', { name: '保存', exact: true }).click(),
    ])

    await page.reload()
    await page.waitForURL('**/canvas?workflow_id=**', { timeout: 15000 })
    await expect(canvas).toHaveAttribute('aria-busy', 'false', { timeout: 10000 })

    await page.getByTestId('workflow-dry-run').click()
    // The empty Draft is intentionally invalid. This checks that dry-run
    // reaches the compiler and exposes its structured diagnostic after reload.
    await expect(page.getByTestId('compile-result')).toContainText('工作流图不包含任何节点', { timeout: 30_000 })
  })
})
