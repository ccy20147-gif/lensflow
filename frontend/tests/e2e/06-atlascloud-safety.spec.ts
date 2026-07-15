import { expect, test } from '@playwright/test'
import { loginWithFreshAccount } from './auth'

async function login(page: any) {
  await loginWithFreshAccount(page, 'atlas-e2e', 'Atlas E2E')
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
