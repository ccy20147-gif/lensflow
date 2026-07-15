import { expect, test } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: import('@playwright/test').Page) {
  await loginWithFreshAccount(page, 'architect', 'Architect')
}

test('Architect intent uses the user path and safely rejects an unconfigured provider', async ({ page }) => {
  await login(page)
  await page.fill('.create-form input', `Architect ${Date.now()}`)
  await page.locator('.create-form button[type="submit"]').click()
  await page.waitForURL('**/projects/**')
  await page.getByRole('button', { name: '新建工作流' }).click()
  await page.waitForURL('**/canvas?workflow_id=**')

  await page.getByLabel('Architect 创作意图').fill('添加一个创作 Brief 节点')
  const generated = page.waitForResponse((response) =>
    response.url().includes('/api/v1/architect/proposals') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '生成提案' }).click()
  expect((await generated).status()).toBe(403)
  await expect(page.locator('.architect-error')).toContainText('POST /api/v1/architect/proposals')
})
