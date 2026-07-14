import { test, expect } from '@playwright/test'

/**
 * Real workflow round-trip — hits the live FastAPI backend via the
 * Vite dev server proxy. Covers the full CAS draft save/load cycle.
 *
 * The test bootstraps an auth token via the same path the frontend
 * uses (`/api/v1/identity/bootstrap` → `/api/v1/identity/login`) and
 * then drives the workflow CRUD through the API.
 *
 * Workflow routes do NOT require auth.  Project routes require
 * `Authorization: Bearer <token>`.
 */

const BACKEND = 'http://127.0.0.1:8000'

async function getToken(): Promise<{
  token: string
  accountId: string
  email: string
}> {
  const api = await (
    await import('@playwright/test')
  ).request.newContext({ baseURL: BACKEND })

  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@toonflow.local`
  // Bootstrap is idempotent — call it every time; if already bootstrapped
  // with a different email we just move on.
  await api
    .post('/api/v1/identity/bootstrap', {
      data: { email, display_name: 'E2E', password: 'pw' },
    })
    .catch(() => undefined)

  const login = await api.post('/api/v1/identity/login', {
    data: { email, password: 'pw' },
  })
  expect(login.ok()).toBeTruthy()
  const body = (await login.json()) as {
    token: string
    account_id: string
  }
  expect(body.token).toBeTruthy()
  await api.dispose()
  return {
    token: body.token,
    accountId: body.account_id,
    email,
  }
}

test.describe('real workflow round-trip (backend + Vite)', () => {
  let token: string
  let projectId: string
  let workflowId: string

  test('full bootstrap → draft save → reload → draft persists', async ({
    page,
  }) => {
    // 0) Bootstrap + login
    const auth = await getToken()
    token = auth.token

    // 1) Health check — GET, not POST
    const health = await page.request.get('/api/v1/health')
    expect(health.ok()).toBeTruthy()
    const healthBody = await health.json()
    expect(healthBody).toMatchObject({ status: 'ok' })

    // 2) Create a project via API with auth
    const projectRes = await page.request.post('/api/v1/projects', {
      data: { name: 'E2E Project', description: 'created by playwright' },
      headers: { Authorization: `Bearer ${token}` },
    })
    const projectStatus = projectRes.status()
    expect(projectStatus).toBe(201)
    const project = (await projectRes.json()) as {
      project_id: string
      name: string
    }
    expect(project.project_id).toBeTruthy()
    expect(project.name).toBe('E2E Project')
    projectId = project.project_id

    // 3) Get the project back — confirms owner-scoped GET works.
    const getRes = await page.request.get(
      `/api/v1/projects/${project.project_id}`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    expect(getRes.ok()).toBeTruthy()
    const fetched = (await getRes.json()) as {
      project_id: string
      name: string
    }
    expect(fetched.project_id).toBe(project.project_id)

    // 4) Create a workflow for that project.
    //    Workflow routes do NOT require auth.
    const wfRes = await page.request.post('/api/v1/workflows/', {
      data: { owner_kind: 'user' },
    })
    expect(wfRes.status()).toBe(201)
    const wf = (await wfRes.json()) as { workflow_id: string }
    expect(wf.workflow_id).toBeTruthy()
    workflowId = wf.workflow_id

    // 5) GET draft — a draft is auto-created when the workflow is created.
    const initialDraft = await page.request.get(
      `/api/v1/workflows/${wf.workflow_id}/draft`,
    )
    expect(initialDraft.status()).toBe(200)
    const draft0 = (await initialDraft.json()) as {
      workflow_id: string
      graph_hash: string
      graph: object
    }
    expect(draft0.workflow_id).toBe(wf.workflow_id)
    expect(draft0.graph_hash).toBeTruthy()

    // 6) PUT draft with the server's current graph_hash (CAS).
    const save1 = await page.request.put(
      `/api/v1/workflows/${wf.workflow_id}/draft`,
      {
        data: {
          graph: {
            nodes: [
              {
                id: 'n1',
                type: 'default',
                position: { x: 100, y: 100 },
                data: { label: 'A', nodeType: 'agent.architect' },
              },
            ],
            edges: [],
          },
          config: {},
          layout: {},
          base_graph_hash: draft0.graph_hash,
          pinned_dependency_revisions: [],
        },
      },
    )
    expect(save1.ok()).toBeTruthy()
    const saved1 = (await save1.json()) as {
      graph_hash: string
      workflow_id: string
    }
    expect(saved1.workflow_id).toBe(wf.workflow_id)
    expect(saved1.graph_hash).toBeTruthy()
    // graph_hash must have changed
    expect(saved1.graph_hash).not.toBe(draft0.graph_hash)

    // 7) GET draft — should now return the saved version.
    const read1 = await page.request.get(
      `/api/v1/workflows/${wf.workflow_id}/draft`,
    )
    expect(read1.ok()).toBeTruthy()
    const draft1 = (await read1.json()) as {
      workflow_id: string
      graph: { nodes: { id: string }[] }
      graph_hash: string
    }
    expect(draft1.workflow_id).toBe(wf.workflow_id)
    expect(draft1.graph.nodes[0]?.id).toBe('n1')
    expect(draft1.graph_hash).toBe(saved1.graph_hash)

    // 8) CAS round-trip — save again with the new base hash.
    const save2 = await page.request.put(
      `/api/v1/workflows/${wf.workflow_id}/draft`,
      {
        data: {
          graph: {
            nodes: [
              {
                id: 'n1',
                type: 'default',
                position: { x: 100, y: 100 },
                data: { label: 'A' },
              },
              {
                id: 'n2',
                type: 'default',
                position: { x: 300, y: 200 },
                data: { label: 'B' },
              },
            ],
            edges: [
              { id: 'e1', source: 'n1', target: 'n2' },
            ],
          },
          config: {},
          layout: {},
          base_graph_hash: saved1.graph_hash,
          pinned_dependency_revisions: [],
        },
      },
    )
    expect(save2.ok()).toBeTruthy()
    const saved2 = (await save2.json()) as { graph_hash: string }

    // 9) CAS failure — saving with stale base hash must return 409.
    const conflict = await page.request.put(
      `/api/v1/workflows/${wf.workflow_id}/draft`,
      {
        data: {
          graph: { nodes: [], edges: [] },
          config: {},
          layout: {},
          base_graph_hash: saved1.graph_hash, // stale!
          pinned_dependency_revisions: [],
        },
      },
    )
    expect(conflict.status()).toBe(409)

    // 10) GET final draft — verify persistence via API
    const readFinal = await page.request.get(
      `/api/v1/workflows/${wf.workflow_id}/draft`,
    )
    expect(readFinal.ok()).toBeTruthy()
    const finalDraft = (await readFinal.json()) as {
      graph: { nodes: { id: string }[]; edges: { id: string }[] }
      graph_hash: string
    }
    expect(finalDraft.graph.nodes.map((n) => n.id)).toEqual(['n1', 'n2'])
    expect(finalDraft.graph.edges).toHaveLength(1)
    expect(finalDraft.graph_hash).toBe(saved2.graph_hash)
  })
})
