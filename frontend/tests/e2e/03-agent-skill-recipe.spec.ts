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
    await page.waitForSelector('.editor', { timeout: 5000 })
    await page.fill('.step label:nth-child(2) input', 'Echo the supplied typed input')
    await page.click('text=静态校验')
    await expect(page.locator('.result').first()).toContainText('通过', { timeout: 5000 })

    await page.click('text=隔离试跑')
    await expect(page.locator('.result').last()).toContainText('合同有效', { timeout: 5000 })
  })

  test('Skill: create → validate → card created', async ({ page }) => {
    await login(page)
    await page.goto('/skills')
    await page.waitForSelector('.skill-page', { timeout: 10000 })

    const skillName = `E2E-Skill-${Date.now()}`
    await page.fill('input[placeholder="一条不可执行的写作/审核指令"]', 'Apply the supplied style guide.')
    await page.fill('.create input[placeholder*="名称"]', skillName)
    await page.getByRole('button', { name: '保存 Skill 草稿', exact: true }).first().click()
    await page.waitForTimeout(2000)

    // Assert the skill card for this name exists (creation succeeded)
    await expect(page.locator('.list article', { hasText: skillName })).toBeVisible({ timeout: 5000 })

    await page.click('text=预览装配与安全校验')
    await expect(page.locator('.result')).toContainText('通过', { timeout: 5000 })
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
