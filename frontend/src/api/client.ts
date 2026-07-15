/**
 * ToonFlow API client — typed, axios-less fetch wrapper.
 *
 * Base URL is `/api/v1` so the Vite dev server can proxy to the backend.
 * The same path is served in production by FastAPI directly.
 *
 * All functions throw `ApiError` on non-2xx so callers can use a single
 * `try/catch` pattern instead of inspecting `response.ok` everywhere.
 */

const BASE_URL = '/api/v1'

export class ApiError extends Error {
  status: number
  body: unknown
  constructor(message: string, status: number, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

type Query = Record<string, string | number | boolean | undefined | null>

function buildUrl(path: string, query?: Query): string {
  const url = path.startsWith('/') ? `${BASE_URL}${path}` : `${BASE_URL}/${path}`
  if (!query) return url
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue
    search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `${url}?${qs}` : url
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text) as unknown
  } catch {
    return text
  }
}

async function request<T>(
  method: string,
  path: string,
  init?: { body?: unknown; query?: Query; headers?: Record<string, string> },
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...withAuth(init?.headers),
  }
  let body: BodyInit | undefined
  if (init?.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(init.body)
  }
  const url = buildUrl(path, init?.query)
  // Always go through `globalThis.fetch` so test mocks that stub it work.
  const response = await globalThis.fetch(url, { method, headers, body })
  const parsed = await parseBody(response)
  if (!response.ok) {
    throw new ApiError(
      `${method} ${url} failed: ${response.status}`,
      response.status,
      parsed,
    )
  }
  return parsed as T
}

export function apiGet<T>(path: string, query?: Query): Promise<T> {
  return request<T>('GET', path, { query })
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('POST', path, { body })
}

export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('PUT', path, { body })
}

export function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('PATCH', path, { body })
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>('DELETE', path)
}

// ---------------------------------------------------------------------------
// Auth — explicit token storage + login/register/logout/verify.
// ---------------------------------------------------------------------------

const TOKEN_STORAGE_KEY = 'toonflow.token'
const ACCOUNT_STORAGE_KEY = 'toonflow.account'

export interface AccountRef {
  account_id: string
  email: string
  display_name?: string
}

export function getAuthToken(): string | null {
  if (typeof globalThis.localStorage === 'undefined') return null
  return globalThis.localStorage.getItem(TOKEN_STORAGE_KEY)
}

export function setAuthToken(token: string): void {
  if (typeof globalThis.localStorage !== 'undefined') {
    globalThis.localStorage.setItem(TOKEN_STORAGE_KEY, token)
  }
}

export function clearAuthToken(): void {
  if (typeof globalThis.localStorage !== 'undefined') {
    globalThis.localStorage.removeItem(TOKEN_STORAGE_KEY)
    globalThis.localStorage.removeItem(ACCOUNT_STORAGE_KEY)
  }
}

export function getStoredAccount(): AccountRef | null {
  if (typeof globalThis.localStorage === 'undefined') return null
  const raw = globalThis.localStorage.getItem(ACCOUNT_STORAGE_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as AccountRef
  } catch {
    return null
  }
}

export function setStoredAccount(account: AccountRef): void {
  if (typeof globalThis.localStorage !== 'undefined') {
    globalThis.localStorage.setItem(ACCOUNT_STORAGE_KEY, JSON.stringify(account))
  }
}

/** Attach the bearer token to any set of headers. */
export function withAuth(extra?: Record<string, string>): Record<string, string> {
  const token = getAuthToken()
  return token
    ? { Authorization: `Bearer ${token}`, ...(extra ?? {}) }
    : { ...(extra ?? {}) }
}

// ---------------------------------------------------------------------------
// Auth endpoints
// ---------------------------------------------------------------------------

export interface BootstrapStatus {
  completed: boolean
  bootstrap_email?: string
}

export function getBootstrapStatus(): Promise<BootstrapStatus> {
  return apiGet<BootstrapStatus>('/identity/bootstrap-status')
}

export interface BootstrapRequest {
  email: string
  display_name: string
  password: string
}

export function bootstrapIdentity(body: BootstrapRequest): Promise<unknown> {
  return apiPost<unknown>('/identity/bootstrap', body)
}

export interface RegisterRequest {
  email: string
  display_name: string
  password: string
}

export interface RegisterResponse {
  account_id: string
  email: string
  display_name: string
}

export function registerIdentity(body: RegisterRequest): Promise<RegisterResponse> {
  return apiPost<RegisterResponse>('/identity/register', body)
}

export interface LoginRequest {
  email: string
  password: string
}

export interface LoginResponse {
  account_id: string
  token: string
  expires_at: string
}

export function loginIdentity(body: LoginRequest): Promise<LoginResponse> {
  return apiPost<LoginResponse>('/identity/login', body)
}

export interface VerifyResponse {
  account_id: string
  status: string
}

export function verifyToken(): Promise<VerifyResponse> {
  return apiGet<VerifyResponse>('/identity/verify')
}

export function logoutIdentity(): Promise<unknown> {
  return apiPost<unknown>('/identity/logout')
}

/**
 * Compatibility helper kept for older call sites: bootstrap + login
 * the single owner if needed, returning a token. New callers should
 * call `bootstrapIdentity` and `loginIdentity` directly from the UI.
 */
export async function ensureAuthToken(): Promise<string> {
  const cached = getAuthToken()
  if (cached) return cached
  const status = await getBootstrapStatus().catch(() => ({ completed: false } as BootstrapStatus))
  const bootstrapEmail = 'bootstrap@toonflow.local'
  if (!status.completed) {
    await bootstrapIdentity({
      email: bootstrapEmail,
      display_name: 'Bootstrap Owner',
      password: 'bootstrap-dev-password',
    }).catch(() => {
      // Already bootstrapped in a race — fine.
    })
  }
  const login = await loginIdentity({
    email: bootstrapEmail,
    password: 'bootstrap-dev-password',
  })
  setAuthToken(login.token)
  return login.token
}

// ---------------------------------------------------------------------------
// Domain types — mirror backend Pydantic models.
// ---------------------------------------------------------------------------

export interface ProjectRecord {
  project_id: string
  owner_scope: string
  name: string
  description: string
  status: string
  default_entry: string
  created_at: string
  updated_at: string
}

export interface NodeDefinitionRecord {
  node_type_id: string
  revision_id: string
  semantic_version: string
  status: string
  category?: string
  name?: string
  description?: string
  input_ports?: unknown[]
  output_ports?: unknown[]
  config_schema?: Record<string, unknown>
}

export interface WorkflowDraftRecord {
  workflow_id: string
  draft_version: number
  graph: { nodes?: unknown[]; edges?: unknown[] } | null
  layout: Record<string, unknown> | null
  graph_hash: string
  execution_hash: string
}

export interface CreateWorkflowResponse {
  workflow_id: string
  owner_scope?: string
}

export interface CreateWorkflowRequest {
  owner_kind?: string
  owner_id?: string
}

// ---------------------------------------------------------------------------
// Project endpoints
// ---------------------------------------------------------------------------

export function listProjects(): Promise<ProjectRecord[]> {
  return apiGet<ProjectRecord[]>('/projects')
}

export interface CreateProjectRequest {
  name: string
  description?: string
}

export function createProject(body: CreateProjectRequest): Promise<ProjectRecord> {
  return apiPost<ProjectRecord>('/projects', body)
}

export function getProject(projectId: string): Promise<ProjectRecord> {
  return apiGet<ProjectRecord>(`/projects/${encodeURIComponent(projectId)}`)
}

/**
 * List workflow IDs that belong to a project. Returns the workflow_id
 * strings in whatever order the backend provides them (newest first).
 */
export function listProjectWorkflows(projectId: string): Promise<string[]> {
  return apiGet<string[]>(`/projects/${encodeURIComponent(projectId)}/workflows`)
}

/**
 * Spec-shaped helper: "list workflows in a project". Delegates to the
 * project-scoped endpoint since the canvas uses this specifically to
 * fall back when ?workflow_id is missing from the URL.
 */
export function listWorkflows(projectId: string): Promise<string[]> {
  return listProjectWorkflows(projectId)
}

export function listAllWorkflows(): Promise<{ workflows: { workflow_id: string }[] }> {
  return apiGet<{ workflows: { workflow_id: string }[] }>('/workflows/')
}

export function createWorkflow(
  _projectId: string,
  body?: CreateWorkflowRequest,
): Promise<CreateWorkflowResponse> {
  return apiPost<CreateWorkflowResponse>('/workflows/', body ?? {})
}

// ---------------------------------------------------------------------------
// Workflow endpoints
// ---------------------------------------------------------------------------

export function getDraft(workflowId: string): Promise<WorkflowDraftRecord> {
  return apiGet<WorkflowDraftRecord>(`/workflows/${encodeURIComponent(workflowId)}/draft`)
}

export interface SaveDraftRequest {
  graph: { nodes?: unknown[]; edges?: unknown[] }
  config: Record<string, unknown>
  layout: Record<string, unknown>
  base_graph_hash: string
  pinned_dependency_revisions?: string[]
}

export function saveDraft(
  workflowId: string,
  draft: SaveDraftRequest['graph'],
  baseGraphHash: string,
): Promise<WorkflowDraftRecord> {
  const body: SaveDraftRequest = {
    graph: draft,
    config: {},
    layout: {},
    base_graph_hash: baseGraphHash,
  }
  return apiPut<WorkflowDraftRecord>(
    `/workflows/${encodeURIComponent(workflowId)}/draft`,
    body,
  )
}

export interface CompileReport {
  workflow_id: string
  valid: boolean
  errors: Array<{ node_id?: string; code?: string; message?: string }>
  warnings: Array<{ node_id?: string; code?: string; message?: string }>
}

export function compileWorkflow(workflowId: string): Promise<CompileReport> {
  return apiPost<CompileReport>(`/workflows/${encodeURIComponent(workflowId)}/compile`)
}

export function dryRunCompile(workflowId: string): Promise<CompileReport> {
  return apiPost<CompileReport>(`/workflows/${encodeURIComponent(workflowId)}/compile/dry-run`)
}

// ---------------------------------------------------------------------------
// Registry endpoint
// ---------------------------------------------------------------------------

export function listNodeRegistry(): Promise<NodeDefinitionRecord[]> {
  return apiGet<NodeDefinitionRecord[]>('/registry/definitions', { status: 'active' })
}

// ---------------------------------------------------------------------------
// Agent endpoints
// ---------------------------------------------------------------------------

export interface AgentDefinitionRecord {
  agent_id: string
  name: string
  description: string
  agent_kind: string
  owner_scope: string
  created_at: string
  updated_at: string
}

export interface AgentRevisionRecord {
  revision_id: string
  body: Record<string, unknown>
  base_hash?: string | null
  status: string
  created_at?: string
  promoted_at?: string | null
}

export interface CreateAgentRequest {
  name: string
  description?: string
  agent_kind?: string
  owner_scope?: string
}

export function listAgents(ownerScope?: string): Promise<AgentDefinitionRecord[]> {
  return apiGet<AgentDefinitionRecord[]>('/agents/', { owner_scope: ownerScope })
}

export function getAgent(agentId: string): Promise<AgentDefinitionRecord> {
  return apiGet<AgentDefinitionRecord>(`/agents/${encodeURIComponent(agentId)}`)
}

export function createAgent(body: CreateAgentRequest): Promise<AgentDefinitionRecord> {
  return apiPost<AgentDefinitionRecord>('/agents/', body)
}

export function updateAgent(
  agentId: string,
  body: { name?: string; description?: string },
): Promise<AgentDefinitionRecord> {
  return apiPatch<AgentDefinitionRecord>(
    `/agents/${encodeURIComponent(agentId)}`,
    body,
  )
}

export function deleteAgent(agentId: string): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/agents/${encodeURIComponent(agentId)}`)
}

export function listAgentRevisions(agentId: string): Promise<AgentRevisionRecord[]> {
  return apiGet<AgentRevisionRecord[]>(
    `/agents/${encodeURIComponent(agentId)}/revisions`,
  )
}

export function getAgentRevision(
  agentId: string,
  revisionId: string,
): Promise<AgentRevisionRecord> {
  return apiGet<AgentRevisionRecord>(
    `/agents/${encodeURIComponent(agentId)}/revisions/${encodeURIComponent(revisionId)}`,
  )
}

export function createAgentRevision(
  agentId: string,
  body: { body: Record<string, unknown>; base_hash?: string | null },
): Promise<AgentRevisionRecord> {
  return apiPost<AgentRevisionRecord>(
    `/agents/${encodeURIComponent(agentId)}/revisions`,
    body,
  )
}

export function promoteAgentRevision(
  agentId: string,
  revisionId: string,
): Promise<AgentRevisionRecord> {
  return apiPost<AgentRevisionRecord>(
    `/agents/${encodeURIComponent(agentId)}/revisions/${encodeURIComponent(revisionId)}/promote`,
  )
}

export function retireAgentRevision(
  agentId: string,
  revisionId: string,
): Promise<AgentRevisionRecord> {
  return apiPost<AgentRevisionRecord>(
    `/agents/${encodeURIComponent(agentId)}/revisions/${encodeURIComponent(revisionId)}/retire`,
  )
}

export function validateAgentBody(body: Record<string, unknown>): Promise<{ valid: boolean }> {
  return apiPost<{ valid: boolean }>('/agents/validate', { body })
}

export function dryRunAgent(body: Record<string, unknown>): Promise<{ valid: boolean; step_count: number }> {
  return apiPost<{ valid: boolean; step_count: number }>('/agents/dry-run', { body })
}

// ---------------------------------------------------------------------------
// Skill endpoints
// ---------------------------------------------------------------------------

export interface SkillRecord {
  skill_id: string
  name: string
  description: string
  owner_scope: string
  body: Record<string, unknown>
  content_hash: string
  status: 'draft' | 'active' | 'retired'
  created_at: string
  updated_at: string
}

export interface SkillRevisionRecord {
  revision_id: string
  skill_id: string
  revision_number: number
  body: Record<string, unknown>
  content_hash: string
  status: 'active' | 'retired'
  created_at: string
}

export interface SkillDryRunResult {
  valid: boolean
  resolved_sections: Array<{ section: string; content: unknown; tokens_estimate: number }>
  token_accounting: { total_estimated_tokens: number; max_tokens: number }
  conflicts: string[]
  security_decisions: string[]
  final_context_hash: string
}

export function listSkills(ownerScope?: string): Promise<SkillRecord[]> {
  return apiGet<SkillRecord[]>('/skills', { owner_scope: ownerScope })
}

export function getSkill(skillId: string): Promise<SkillRecord> {
  return apiGet<SkillRecord>(`/skills/${encodeURIComponent(skillId)}`)
}

export function createSkill(body: {
  name: string
  description?: string
  owner_scope?: string
  body?: Record<string, unknown>
}): Promise<SkillRecord> {
  return apiPost<SkillRecord>('/skills', body)
}

export function updateSkill(
  skillId: string,
  body: { body: Record<string, unknown>; base_hash?: string | null },
): Promise<SkillRecord> {
  return apiPatch<SkillRecord>(`/skills/${encodeURIComponent(skillId)}`, body)
}

export function deleteSkill(skillId: string): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/skills/${encodeURIComponent(skillId)}`)
}

export function validateSkillBody(body: Record<string, unknown>): Promise<{ valid: boolean }> {
  return apiPost<{ valid: boolean }>('/skills/validate', { body })
}

export function dryRunSkill(body: Record<string, unknown>): Promise<SkillDryRunResult> {
  return apiPost<SkillDryRunResult>('/skills/dry-run', { body })
}

export function submitSkillRevision(skillId: string, baseHash: string): Promise<SkillRevisionRecord> {
  return apiPost<SkillRevisionRecord>(`/skills/${encodeURIComponent(skillId)}/revisions`, { base_hash: baseHash })
}

export function listSkillRevisions(skillId: string): Promise<SkillRevisionRecord[]> {
  return apiGet<SkillRevisionRecord[]>(`/skills/${encodeURIComponent(skillId)}/revisions`)
}

export function retireSkillRevision(skillId: string, revisionId: string): Promise<SkillRevisionRecord> {
  return apiPost<SkillRevisionRecord>(`/skills/${encodeURIComponent(skillId)}/revisions/${encodeURIComponent(revisionId)}/retire`)
}

// ---------------------------------------------------------------------------
// Media Recipe endpoints
// ---------------------------------------------------------------------------

export interface MediaRecipeRecord {
  recipe_id: string
  name: string
  description: string
  owner_scope: string
  recipe_type: string
  created_at: string
  updated_at: string
}

export interface MediaRecipeRevisionRecord {
  revision_id: string
  recipe_id?: string | null
  revision_number: number
  content_hash: string
  base_hash?: string | null
  revision_status: 'draft' | 'active' | 'retired'
  operator_graph: Record<string, unknown>
  public_input_schema_refs: string[]
  public_output_schema_refs: string[]
  parameter_schema: Record<string, unknown>
  capability_requirements: string[]
  created_at?: string
}

export interface MediaRecipeDiff {
  left_revision_id: string
  right_revision_id: string
  changed_fields: string[]
  changes: Record<string, { from: unknown; to: unknown }>
}

export interface RecipeDryRunResult {
  valid: boolean
  step_count: number
  plan_hash: string
  control_outcomes: Array<{ operator_id: string; control: string; outcome: string }>
}

export function listRecipes(ownerScope?: string): Promise<MediaRecipeRecord[]> {
  return apiGet<MediaRecipeRecord[]>('/recipes', { owner_scope: ownerScope })
}

export function getRecipe(recipeId: string): Promise<MediaRecipeRecord> {
  return apiGet<MediaRecipeRecord>(`/recipes/${encodeURIComponent(recipeId)}`)
}

export function createRecipe(body: {
  name: string
  description?: string
  owner_scope?: string
  recipe_type?: string
}): Promise<MediaRecipeRecord> {
  return apiPost<MediaRecipeRecord>('/recipes', body)
}

export function updateRecipe(
  recipeId: string,
  body: { name?: string; description?: string; recipe_type?: string },
): Promise<MediaRecipeRecord> {
  return apiPatch<MediaRecipeRecord>(`/recipes/${encodeURIComponent(recipeId)}`, body)
}

export function deleteRecipe(recipeId: string): Promise<{ status: string }> {
  return apiDelete<{ status: string }>(`/recipes/${encodeURIComponent(recipeId)}`)
}

export function listRecipeRevisions(recipeId: string): Promise<MediaRecipeRevisionRecord[]> {
  return apiGet<MediaRecipeRevisionRecord[]>(
    `/recipes/${encodeURIComponent(recipeId)}/revisions`,
  )
}

export function createRecipeRevision(
  recipeId: string,
  body: { body: Record<string, unknown>; base_hash?: string | null },
): Promise<MediaRecipeRevisionRecord> {
  return apiPost<MediaRecipeRevisionRecord>(
    `/recipes/${encodeURIComponent(recipeId)}/revisions`,
    body,
  )
}

export function promoteRecipeRevision(
  recipeId: string,
  revisionId: string,
): Promise<MediaRecipeRevisionRecord> {
  return apiPost<MediaRecipeRevisionRecord>(
    `/recipes/${encodeURIComponent(recipeId)}/revisions/${encodeURIComponent(revisionId)}/promote`,
    {},
  )
}

export function retireRecipeRevision(
  recipeId: string,
  revisionId: string,
): Promise<MediaRecipeRevisionRecord> {
  return apiPost<MediaRecipeRevisionRecord>(
    `/recipes/${encodeURIComponent(recipeId)}/revisions/${encodeURIComponent(revisionId)}/retire`,
    {},
  )
}

export function diffRecipeRevisions(
  recipeId: string,
  revisionId: string,
  otherRevisionId: string,
): Promise<MediaRecipeDiff> {
  return apiGet<MediaRecipeDiff>(
    `/recipes/${encodeURIComponent(recipeId)}/revisions/${encodeURIComponent(revisionId)}/diff/${encodeURIComponent(otherRevisionId)}`,
  )
}

export function validateRecipeBody(body: Record<string, unknown>): Promise<{ valid: boolean }> {
  return apiPost<{ valid: boolean }>('/recipes/validate', { body })
}

export function dryRunRecipe(
  body: Record<string, unknown>,
): Promise<RecipeDryRunResult> {
  return apiPost<RecipeDryRunResult>('/recipes/dry-run', { body })
}

export interface RecipeTrialResult {
  run_id: string
  node_run_attempt_id: string
  provider_attempt_id?: string
  status: string
  record_id?: string
  artifact_version_ids?: string[]
  outbox_event_id?: string
  operator_attempt_ids?: string[]
  lab_trial: true
}

export function executeRecipeTrial(
  recipeId: string,
  revisionId: string,
  body: { inputs: Record<string, unknown>; idempotency_key: string },
): Promise<RecipeTrialResult> {
  return apiPost<RecipeTrialResult>(
    `/recipes/${encodeURIComponent(recipeId)}/revisions/${encodeURIComponent(revisionId)}/trial`,
    body,
  )
}

// ---------------------------------------------------------------------------
// Control Flow endpoints
// ---------------------------------------------------------------------------

export interface ConditionRecord {
  condition_id: string
  run_id: string
  node_instance_id: string
  operator: string
  threshold: unknown
  value_path?: string | null
  resolved?: boolean | null
}

export interface ConditionCreateRequest {
  run_id: string
  node_instance_id: string
  operator: string
  threshold?: unknown
  value_path?: string | null
  config?: Record<string, unknown>
}

export function createCondition(body: ConditionCreateRequest): Promise<ConditionRecord> {
  return apiPost<ConditionRecord>('/control-flow/conditions', body)
}

export function getCondition(conditionId: string): Promise<ConditionRecord> {
  return apiGet<ConditionRecord>(
    `/control-flow/conditions/${encodeURIComponent(conditionId)}`,
  )
}

export function listConditionsForRun(runId: string): Promise<ConditionRecord[]> {
  return apiGet<ConditionRecord[]>(
    `/control-flow/runs/${encodeURIComponent(runId)}/conditions`,
  )
}

export function evaluateCondition(
  conditionId: string,
  body: { resolved_value: unknown },
): Promise<{ status: string }> {
  return apiPost<{ status: string }>(
    `/control-flow/conditions/${encodeURIComponent(conditionId)}/evaluate`,
    body,
  )
}

export interface JoinRecord {
  join_id: string
  run_id: string
  node_instance_id: string
  strategy: string
  source_node_ids: string[]
  status: string
}

export function createJoin(body: {
  run_id: string
  node_instance_id: string
  strategy: string
  source_node_ids: string[]
  config?: Record<string, unknown>
}): Promise<JoinRecord> {
  return apiPost<JoinRecord>('/control-flow/joins', body)
}

export function getJoin(joinId: string): Promise<JoinRecord> {
  return apiGet<JoinRecord>(`/control-flow/joins/${encodeURIComponent(joinId)}`)
}

export function listJoinsForRun(runId: string): Promise<JoinRecord[]> {
  return apiGet<JoinRecord[]>(`/control-flow/runs/${encodeURIComponent(runId)}/joins`)
}

export function resolveJoin(joinId: string): Promise<{ status: string }> {
  return apiPost<{ status: string }>(
    `/control-flow/joins/${encodeURIComponent(joinId)}/resolve`,
  )
}

export interface MapItemRecord {
  map_item_id: string
  run_id: string
  node_instance_id: string
  item_key: string
  status: string
}

export function createMapItem(body: {
  run_id: string
  node_instance_id: string
  item_key: string
  item_value?: Record<string, unknown>
}): Promise<MapItemRecord> {
  return apiPost<MapItemRecord>('/control-flow/map-items', body)
}

export function getMapItem(mapItemId: string): Promise<MapItemRecord> {
  return apiGet<MapItemRecord>(
    `/control-flow/map-items/${encodeURIComponent(mapItemId)}`,
  )
}

export function listMapItemsForRun(runId: string): Promise<MapItemRecord[]> {
  return apiGet<MapItemRecord[]>(
    `/control-flow/runs/${encodeURIComponent(runId)}/map-items`,
  )
}

export function startMapItem(mapItemId: string): Promise<MapItemRecord> {
  return apiPost<MapItemRecord>(
    `/control-flow/map-items/${encodeURIComponent(mapItemId)}/start`,
  )
}

export function completeMapItem(
  mapItemId: string,
  body: { result?: Record<string, unknown> },
): Promise<MapItemRecord> {
  return apiPost<MapItemRecord>(
    `/control-flow/map-items/${encodeURIComponent(mapItemId)}/complete`,
    body,
  )
}

export function failMapItem(
  mapItemId: string,
  body: { error?: string },
): Promise<MapItemRecord> {
  return apiPost<MapItemRecord>(
    `/control-flow/map-items/${encodeURIComponent(mapItemId)}/fail`,
    body,
  )
}

export function skipMapItem(mapItemId: string): Promise<MapItemRecord> {
  return apiPost<MapItemRecord>(
    `/control-flow/map-items/${encodeURIComponent(mapItemId)}/skip`,
  )
}

// ---------------------------------------------------------------------------
// Template endpoints — fixed-revision package discovery and instantiation.
// ---------------------------------------------------------------------------

export interface TemplateSummary {
  template_id: string
  name: string
  description?: string
  category?: string
}

export interface TemplateDetail extends TemplateSummary {
  workflow_revision_id: string
  manifest: {
    name: string
    description?: string
    dependencies: Array<{ dep_id: string; name?: string; replacement_slot?: string | null }>
    replacement_slots: Array<{ slot_id: string; label: string; description?: string; required: boolean }>
  }
  default_mapping: Record<string, unknown>
}

export interface TemplateDependencyResolution {
  resolved: boolean
  missing: string[]
  unresolved_slots: string[]
  available: boolean
}

export interface TemplateInstance {
  instance_id: string
  template_id: string
  template_revision_id: string
  project_id: string
  workflow_id: string
  dependency_resolution: Record<string, string>
  replacement_mapping: Record<string, string>
  attribution_manifest: Record<string, unknown>
  created_at: string
}

export interface TemplateReplacementOptions { slots: Array<{ slot_id: string; expected_kind: string; candidates: Array<{ revision_id: string; label: string }> }> }

export function listTemplates(): Promise<TemplateSummary[]> {
  return apiGet<TemplateSummary[]>('/templates')
}

export function seedBenchmarkTemplates(): Promise<{ template_ids: string[] }> {
  return apiPost<{ template_ids: string[] }>('/templates/benchmarks/seed', {})
}

export function getTemplate(templateId: string): Promise<TemplateDetail> {
  return apiGet<TemplateDetail>(`/templates/${encodeURIComponent(templateId)}`)
}

export function getTemplateReplacementOptions(templateId: string): Promise<TemplateReplacementOptions> {
  return apiGet<TemplateReplacementOptions>(`/templates/${encodeURIComponent(templateId)}/replacement-options`)
}

export function resolveTemplateDependencies(
  templateId: string,
  replacements: Record<string, string>,
): Promise<TemplateDependencyResolution> {
  return apiPost<TemplateDependencyResolution>(
    `/templates/${encodeURIComponent(templateId)}/resolve-dependencies`,
    replacements,
  )
}

export function instantiateTemplate(
  templateId: string,
  body: { project_name?: string; project_description?: string; parameters?: Record<string, unknown>; replacements?: Record<string, string> },
): Promise<TemplateInstance> {
  return apiPost<TemplateInstance>(`/templates/${encodeURIComponent(templateId)}/instantiate`, body)
}

export function publishWorkflowRevision(workflowId: string): Promise<{ revision_id: string; compiled_plan_id: string }> {
  return apiPost<{ revision_id: string; compiled_plan_id: string }>(`/workflows/${encodeURIComponent(workflowId)}/revisions`, {})
}

export function startWorkflowRun(workflowRevisionId: string): Promise<{ run_id: string; status: string }> {
  return apiPost<{ run_id: string; status: string }>('/runtime/workflow-runs', { workflow_revision_id: workflowRevisionId, input_snapshot: {} })
}

// ---------------------------------------------------------------------------
// Runtime status surface for workflow execution views.
// ---------------------------------------------------------------------------

export interface WorkflowRunSummary {
  run_id: string
  workflow_id: string
  status: string
  created_at?: string
}

export function listRuns(): Promise<{ runs: WorkflowRunSummary[] }> {
  return apiGet<{ runs: WorkflowRunSummary[] }>('/runtime/runs')
}

export function getRuntimeHealth(): Promise<{ status: string; components?: Record<string, string> }> {
  return apiGet<{ status: string; components?: Record<string, string> }>('/runtime/health')
}
