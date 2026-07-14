import { expect, test } from '@playwright/test'

async function login(page: any) {
  await page.goto('/login')
  const email = `template-e2e-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@toonflow.local`
  const bootstrap = page.locator('button:has-text("初始化")')
  if (await bootstrap.isVisible({ timeout: 2_000 }).catch(() => false)) {
    await page.fill('input[type="email"]', email)
    await page.fill('input[type="text"]', 'Template E2E')
    await page.fill('input[type="password"]', 'password')
    await bootstrap.click()
  }
  await page.fill('input[type="email"]', email)
  if (await page.locator('input[type="text"]').isVisible({ timeout: 1_000 }).catch(() => false)) await page.fill('input[type="text"]', 'Template E2E')
  await page.fill('input[type="password"]', 'password')
  const signIn = page.locator('button:has-text("登录")')
  if (await signIn.isVisible({ timeout: 2_000 }).catch(() => false)) await signIn.click()
  else await page.locator('button[type="submit"]').click()
  await page.waitForURL('**/projects')
}

test('benchmark template -> canvas -> Workbench ResourceCommit is a real browser path', async ({ page }) => {
  await login(page)
  await page.goto('/templates')
  await page.getByRole('button', { name: '创建官方基准模板' }).click()
  const card = page.locator('.template-row', { hasText: '广告创意候选与人工精修' }).first()
  await expect(card).toBeVisible()
  await card.click()
  await expect(page.locator('.template-detail h3')).toHaveText('广告创意候选与人工精修')
  await page.locator('.instantiate-btn').click()
  await page.waitForURL('**/projects/**/canvas?workflow_id=*')

  const workflowId = new URL(page.url()).searchParams.get('workflow_id')
  const projectId = page.url().split('/projects/')[1]?.split('/')[0] || ''
  expect(workflowId).toBeTruthy()
  await expect(page.locator('.canvas-page')).toBeVisible()
  await expect(page.locator('.registry-node')).toHaveCount(8)
  await expect(page.locator('.registry-node', { hasText: 'brief' })).toBeVisible()
  await expect(page.locator('.registry-node', { hasText: 'workbench_task' })).toBeVisible()
  await page.getByRole('button', { name: '发布并运行' }).click()
  await expect(page.locator('.run-status')).toContainText('已启动运行')
  const runId = (await page.locator('.run-status').textContent())?.match(/[0-9a-f-]{36}/i)?.[0]
  expect(runId).toBeTruthy()

  await page.goto(`/projects/${projectId}/resources`)
  await page.getByLabel('Artifact Schema').fill('workbench_result')
  await page.getByLabel('Artifact JSON').fill('{"title":"浏览器提交的精修结果"}')
  await page.getByRole('button', { name: '创建 Artifact 版本' }).click()
  await expect(page.locator('.resource-card', { hasText: 'workbench_result v1' })).toBeVisible()

  await page.goto(`/projects/${projectId}/workbench/human-tasks`)
  const task = page.locator('.task-card', { hasText: 'workbench_task' })
  // The durable worker is independent from navigation. Force a user-visible
  // read-model refresh once, then let the Workbench polling show progress.
  await page.getByRole('button', { name: '刷新' }).click()
  await expect(task).toBeVisible({ timeout: 30_000 })
  const artifactSelect = task.getByLabel('结果 ArtifactVersion')
  await expect(artifactSelect.locator('option')).toHaveCount(2)
  await artifactSelect.selectOption({ index: 1 })
  await task.getByRole('button', { name: '提交结果' }).click()
  await expect(task.locator('.task-status.accepted')).toBeVisible()

  await page.getByRole('button', { name: '运行 Trace' }).click()
  await page.getByLabel('Run ID').fill(runId!)
  await page.getByRole('button', { name: '加载 Trace' }).click()
  await expect(page.locator('.trace-list')).toContainText('已提交资源')
  await expect(page.locator('.trace-list')).toContainText('creative_board')
})
