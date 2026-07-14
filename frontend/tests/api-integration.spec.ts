import { describe, expect, it, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useProjectStore } from '@/stores/project'
import {
  listProjects,
  createProject,
  listWorkflows,
  createWorkflow,
  getDraft,
  saveDraft,
  listNodeRegistry,
  diffRecipeRevisions,
  promoteRecipeRevision,
} from '@/api/client'

/**
 * API integration tests — verify the frontend hits the correct backend
 * endpoints, uses workflow_id (not project_id) for workflow operations,
 * and that the project store wires fetch results into Pinia state.
 *
 * Tests mock globalThis.fetch so no real HTTP or DB is required.
 */

function mockJsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

beforeEach(() => {
  setActivePinia(createPinia())
  if (typeof globalThis.localStorage !== 'undefined') {
    globalThis.localStorage.clear()
  }
})

describe('api client — project endpoints', () => {
  it('listProjects hits GET /api/v1/projects', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await listProjects()

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/projects')
    expect(init.method).toBe('GET')
  })

  it('createProject hits POST /api/v1/projects with the name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        project_id: 'p1',
        owner_scope: 'user:dev',
        name: 'My Project',
        description: '',
        status: 'active',
        default_entry: 'main',
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const created = await createProject({ name: 'My Project' })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/projects')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ name: 'My Project' })
    expect(created.project_id).toBe('p1')
  })

  it('createProject forwards description when provided', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({
      project_id: 'p2',
      owner_scope: 'user:dev',
      name: 'Desc',
      description: 'hello',
      status: 'active',
      default_entry: 'main',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    }))
    vi.stubGlobal('fetch', fetchMock)

    await createProject({ name: 'Desc', description: 'hello' })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ name: 'Desc', description: 'hello' })
  })
})

describe('api client — workflow endpoints', () => {
  it('listWorkflows hits GET /api/v1/projects/:id/workflows', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse(['wf-a', 'wf-b']))
    vi.stubGlobal('fetch', fetchMock)

    const ids = await listWorkflows('proj-123')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/projects/proj-123/workflows')
    expect(init.method).toBe('GET')
    expect(ids).toEqual(['wf-a', 'wf-b'])
  })

  it('createWorkflow hits POST /api/v1/workflows/ and returns workflow_id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({ workflow_id: 'wf-new', owner_scope: 'user:dev' }, 201),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { workflow_id } = await createWorkflow('proj-xyz')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/workflows/')
    expect(init.method).toBe('POST')
    expect(workflow_id).toBe('wf-new')
  })

  it('getDraft hits GET /api/v1/workflows/:workflow_id/draft (NOT project_id)', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        workflow_id: 'wf-1',
        draft_version: 1,
        graph: { nodes: [], edges: [] },
        layout: {},
        graph_hash: 'h1',
        execution_hash: 'e1',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const draft = await getDraft('wf-1')

    const [url] = fetchMock.mock.calls[0] as [string]
    // Crucially, the URL must contain the workflow_id, not a project_id.
    expect(url).toBe('/api/v1/workflows/wf-1/draft')
    expect(url).not.toContain('/projects/')
    expect(draft.workflow_id).toBe('wf-1')
  })

  it('saveDraft hits PUT /api/v1/workflows/:workflow_id/draft with base_graph_hash', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        workflow_id: 'wf-1',
        draft_version: 2,
        graph: { nodes: [{ id: 'n' }], edges: [] },
        layout: {},
        graph_hash: 'h2',
        execution_hash: 'e2',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const saved = await saveDraft(
      'wf-1',
      { nodes: [{ id: 'n' }], edges: [] },
      'h1',
    )

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/workflows/wf-1/draft')
    expect(init.method).toBe('PUT')
    const body = JSON.parse(init.body as string)
    expect(body.base_graph_hash).toBe('h1')
    expect(body.graph.nodes).toEqual([{ id: 'n' }])
    expect(saved.graph_hash).toBe('h2')
  })
})

describe('api client — registry', () => {
  it('listNodeRegistry hits /api/v1/registry/definitions?status=active', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await listNodeRegistry()

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe('/api/v1/registry/definitions?status=active')
  })
})

describe('api client — Media Recipe revision lifecycle', () => {
  it('promotes an immutable Recipe revision through its owner-scoped endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({
      revision_id: 'r1', revision_number: 1, content_hash: 'hash', revision_status: 'active', operator_graph: {},
      public_input_schema_refs: [], public_output_schema_refs: [], parameter_schema: {}, capability_requirements: [],
    }))
    vi.stubGlobal('fetch', fetchMock)
    await promoteRecipeRevision('recipe-1', 'revision-1')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/recipes/recipe-1/revisions/revision-1/promote')
    expect(init.method).toBe('POST')
  })

  it('loads an explicit revision A/B diff rather than comparing mutable editor state', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({
      left_revision_id: 'a', right_revision_id: 'b', changed_fields: ['operator_graph'], changes: {},
    }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await diffRecipeRevisions('recipe-1', 'revision-a', 'revision-b')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/recipes/recipe-1/revisions/revision-a/diff/revision-b')
    expect(init.method).toBe('GET')
    expect(result.changed_fields).toEqual(['operator_graph'])
  })
})

describe('project store — wiring', () => {
  it('fetchProjects populates the projects list from GET /projects', async () => {
    // ensureAuthToken can make many calls: bootstrap-status, login, verify.
    // Provide enough mock responses so the chain is never exhausted.
    const defaults = [
      // ensureAuthToken path
      mockJsonResponse({ completed: true }),                                                // getBootstrapStatus (skips bootstrapIdentity)
      mockJsonResponse({ token: 'mock-tok', account: { id: 'u1' }, account_id: 'u1' }),     // loginIdentity
      // actual fetchProjects
      mockJsonResponse([{ project_id: 'p1', name: 'Test', status: 'active', created_at: '2026-01-01', updated_at: '2026-01-01', owner_scope: 'user:u1' }]),
    ]
    const fallback = mockJsonResponse({ ok: false }, 500) // catch-all for any extra calls
    const fetchMock = vi.fn()
    defaults.forEach(r => fetchMock.mockResolvedValueOnce(r))
    fetchMock.mockResolvedValue(fallback)
    vi.stubGlobal('fetch', fetchMock)

    const store = useProjectStore()
    const projects = await store.fetchProjects()

    expect(projects).toHaveLength(1)
    expect(projects[0]?.project_id).toBe('p1')
    expect(store.projects[0]?.project_id).toBe('p1')

    // The listProjects call must hit /api/v1/projects.
    const projectCall = fetchMock.mock.calls.find(([url]) => url === '/api/v1/projects')
    expect(projectCall).toBeDefined()
  })

  it('saveDraft calls PUT /workflows/:workflow_id/draft with the workflow_id', async () => {
    // Pre-populate auth token so ensureAuthToken doesn't intercept.
    localStorage.setItem('toonflow.token', 'tok-pre')
    const fetchMock = vi
      .fn()
      // saveDraft → PUT
      .mockResolvedValueOnce(
        mockJsonResponse({
          workflow_id: 'wf-99',
          draft_version: 1,
          graph: { nodes: [], edges: [] },
          layout: {},
          graph_hash: 'new-hash',
          execution_hash: 'exec',
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    const store = useProjectStore()
    await store.saveDraft('wf-99', { nodes: [{ id: 'x' }], edges: [] }, '')

    const saveCall = fetchMock.mock.calls.find(
      ([url, init]) => url === '/api/v1/workflows/wf-99/draft' && (init as RequestInit).method === 'PUT',
    )
    expect(saveCall).toBeDefined()
  })
})
