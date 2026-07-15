import { expect, test } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: any) {
  await loginWithFreshAccount(page, 'canvas-recovery', 'Canvas Recovery')
}

test('registry outage makes a persisted canvas read-only and retry restores editing', async ({ page }) => {
  await login(page)
  await page.locator('.create-form input').fill(`Canvas Recovery ${Date.now()}`)
  await page.locator('.create-form button[type="submit"]').click()
  await page.waitForURL('**/projects/**')
  await page.getByText('新建工作流').click()
  await page.waitForURL('**/canvas?workflow_id=**')
  await expect(page.locator('.node-palette')).toBeVisible()

  await page.route('**/api/v1/registry/catalog', async (route) => {
    await route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"unavailable"}' })
  })
  await page.reload()
  await expect(page.locator('.degraded-banner')).toContainText('只读模式')
  await expect(page.getByRole('button', { name: '保存' })).toBeDisabled()

  await page.unroute('**/api/v1/registry/catalog')
  await page.getByRole('button', { name: '重新连接注册表' }).click()
  await expect(page.locator('.degraded-banner')).toHaveCount(0)
  await expect(page.locator('.palette-item').first()).toBeEnabled()
})

test('fifty registry-driven cards keep stable geometry and persist through normalized layout', async ({ page }) => {
  await login(page)
  await page.locator('.create-form input').fill(`Canvas Capacity ${Date.now()}`)
  await page.locator('.create-form button[type="submit"]').click()
  await page.waitForURL('**/projects/**')
  await page.getByText('新建工作流').click()
  await page.waitForURL('**/canvas?workflow_id=**')
  const paletteItem = page.locator('.palette-item').first()
  await expect(paletteItem).toBeEnabled({ timeout: 10_000 })

  for (let index = 0; index < 50; index += 1) await paletteItem.click()
  await expect(page.locator('.registry-node')).toHaveCount(50)
  const geometry = await page.locator('.registry-node').evaluateAll((cards) => cards.map((card) => {
    const rect = card.getBoundingClientRect()
    return { width: rect.width, height: rect.height }
  }))
  // Chromium sub-pixel transforms vary by less than a pixel; cards must not
  // resize as their content/status changes.
  expect(geometry.every((card) => Math.abs(card.width - 220) < 0.2 && Math.abs(card.height - 132) < 0.2)).toBe(true)

  await page.getByRole('button', { name: '保存' }).click()
  await page.reload()
  await expect(page.locator('.registry-node')).toHaveCount(50)
})
