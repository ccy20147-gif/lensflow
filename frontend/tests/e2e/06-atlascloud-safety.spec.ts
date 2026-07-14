import { expect, test } from '@playwright/test'

async function login(page: any) {
  await page.goto('/login')
  await page.waitForSelector('.login-card')
  const email = `atlas-e2e-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@toonflow.local`
  const bootstrap = page.locator('button:has-text("初始化")')
  if (await bootstrap.isVisible({ timeout: 2_000 }).catch(() => false)) {
    await page.fill('input[type="email"]', email)
    await page.fill('input[type="text"]', 'Atlas E2E')
    await page.fill('input[type="password"]', 'password')
    await bootstrap.click()
  }
  await page.fill('input[type="email"]', email)
  if (await page.locator('input[type="text"]').isVisible({ timeout: 1_000 }).catch(() => false)) {
    await page.fill('input[type="text"]', 'Atlas E2E')
  }
  await page.fill('input[type="password"]', 'password')
  const signIn = page.locator('button:has-text("登录")')
  if (await signIn.isVisible({ timeout: 2_000 }).catch(() => false)) await signIn.click()
  else await page.locator('button[type="submit"]').click()
  await page.waitForURL('**/projects')
}

test('AtlasCloud without a credential is visibly and safely rejected', async ({ page }) => {
  await login(page)
  await page.goto('/recipe')
  await expect(page.locator('.provider-note')).toContainText('AtlasCloud')

  // The request originates in the rendered browser page. It invokes the
  // real boundary but must fail before an attempt/outbox or Atlas request.
  const result = await page.evaluate(async () => {
    const token = localStorage.getItem('toonflow.token')
    const response = await fetch('/api/v1/recipes/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        node_run_attempt_id: '00000000-0000-0000-0000-000000000001',
        idempotency_key: `e2e-atlas-${Date.now()}`,
        inputs: { prompt: 'test' },
        body: {
          recipe_type: 'image_pipeline',
          operator_graph: {
            source: { type: 'input', outputs: ['prompt'] },
            generate: { type: 'atlas_image', model_id: 'atlas-test-model', inputs: ['source.prompt'], outputs: ['image'] },
          },
        },
      }),
    })
    return { status: response.status, body: await response.json() }
  })

  expect(result.status).toBe(403)
  expect(result.body).toMatchObject({
    detail: { error: { code: 'POLICY_BLOCKED', message: 'AtlasCloud 凭证未配置' } },
  })
})
