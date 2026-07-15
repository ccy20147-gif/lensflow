import { test, expect } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: any) {
  await loginWithFreshAccount(page, 'agent-skill-recipe', 'E2E')
}

test.describe('03 · Agent / Skill / Recipe', () => {
  test('Agent: create → validate → dry-run', async ({ page }) => {
    await login(page)

    await page.fill('.create-form input', `E2E-AGT-${Date.now()}`)
    await page.click('.create-form button[type="submit"]')
    await page.waitForURL('**/projects/**', { timeout: 15000 })

    const projectId = page.url().split('/projects/')[1]?.split('/')[0] || ''
    await page.goto(`/projects/${projectId}/agent-studio`)
    await page.waitForSelector('.agent-page', { timeout: 10000 })

    const agentName = `E2E-Agent-${Date.now()}`
    await page.fill('.create-form input[placeholder*="名称"]', agentName)
    await page.click('.create-form button[type="submit"]')
    await page.waitForTimeout(2000)

    const agentCard = page.locator('.agent-card', { hasText: agentName }).first()
    await agentCard.click()
    await expect(page.getByRole('heading', { name: agentName, exact: true })).toBeVisible({ timeout: 10000 })
    await page.fill('.step label:nth-child(2) input', 'Echo the supplied typed input')
    await page.click('text=静态校验')
    await expect(page.locator('.result').first()).toContainText('通过', { timeout: 5000 })

    await page.click('text=隔离试跑')
    await expect(page.locator('.result', { hasText: '合同有效' })).toBeVisible({ timeout: 5000 })
  })

  test('Skill: draft → preview → publish immutable revision → retire with history', async ({ page }) => {
    await login(page)
    await page.goto('/skills')
    await page.waitForSelector('.skill-page', { timeout: 10000 })

    const skillName = `E2E-Skill-${Date.now()}`
    await page.fill('input[placeholder="一条不可执行的写作/审核指令"]', 'Apply the supplied style guide.')
    await page.fill('.create input[placeholder*="名称"]', skillName)
    await page.getByRole('button', { name: '创建 Skill 草稿', exact: true }).click()
    await page.waitForTimeout(2000)

    // Creating a Skill selects its mutable draft.
    await expect(page.locator('.skill-list .skill-card', { hasText: skillName })).toBeVisible({ timeout: 5000 })

    await page.getByRole('button', { name: '预览装配', exact: true }).click()
    await expect(page.locator('.result')).toContainText('通过', { timeout: 5000 })
    await expect(page.getByLabel('装配预览')).toContainText('tokens')

    await page.getByRole('button', { name: '发布不可变修订', exact: true }).click()
    await expect(page.getByLabel('不可变修订历史')).toContainText('r1', { timeout: 5000 })

    await page.getByLabel('不可变修订历史').getByRole('button', { name: '退役', exact: true }).click()
    await expect(page.getByLabel('不可变修订历史')).toContainText('retired', { timeout: 5000 })
  })

  test('Recipe: create → validate → reload', async ({ page }) => {
    await login(page)
    await page.goto('/recipe')
    await page.waitForSelector('.recipe-page', { timeout: 10000 })

    const recipeName = `E2E-Recipe-${Date.now()}`
    await page.fill('.create-form input[placeholder*="名称"]', recipeName)
    await page.click('.create-form button[type="submit"]')
    await page.waitForTimeout(2000)

    // Assert the recipe card exists
    await expect(page.locator('.recipe-card', { hasText: recipeName })).toBeVisible({ timeout: 5000 })

    // Validate the structured operator form instead of the removed JSON editor.
    await page.getByRole('button', { name: 'validate', exact: true }).click()
    await expect(page.locator('.result')).toContainText('通过', { timeout: 5000 })

    // Reload and verify persistence
    await page.reload()
    await page.waitForSelector('.recipe-page', { timeout: 10000 })
    await expect(page.locator('.recipe-card', { hasText: recipeName })).toBeVisible({ timeout: 5000 })
  })
})
