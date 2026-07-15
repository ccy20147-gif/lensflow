import { expect, test } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: import('@playwright/test').Page) {
  await loginWithFreshAccount(page, 'recipe-revision', 'Recipe Revision')
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

  // The Lab must exercise the production-shaped, revision-pinned trial
  // endpoint rather than treating compile-only dry-run as an execution. This
  // credentialless E2E environment intentionally proves the safe provider
  // rejection path; a configured deployment continues through AtlasCloud.
  await expect(page.getByRole('button', { name: '执行受控试跑', exact: true })).toBeVisible()
  const trialResponse = page.waitForResponse((response) =>
    response.url().includes('/api/v1/recipes/') && response.url().includes('/trial') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '执行受控试跑', exact: true }).click()
  expect((await trialResponse).status()).toBe(403)
  await expect(page.locator('.error-banner')).toContainText('POST /api/v1/recipes/')

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
