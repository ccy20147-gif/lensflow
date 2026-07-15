<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiGet, apiPost, apiPut } from '@/api/client'

interface AgentDefinition { agent_id: string; name: string; description: string; agent_kind: string }
interface AgentRevision { revision_id: string; revision_number: number; revision_status: string; content_hash: string }
interface SopStep {
  step_id: string; instruction: string; retryAttempts: number; output_schema_ref: string
  inputBinding: string; outputBinding: string; failureStrategy: 'fail' | 'retry' | 'request_input'; checkpointMode: 'none' | 'after_step'
}
interface SkillOption { name: string; description: string; owner_scope: string; ref: { revision_id: string; resource_id?: string; resource_type?: string; grant_snapshot_id?: string } }
interface ToolOption { revision_id: string; name: string; description: string; operations: Array<{ operation_id: string; disclosure_fields: string[] }> }
interface ValidationResult { valid: boolean; message?: string; step_count?: number }

function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error) }
function parseSchema(value: string, label: string): Record<string, unknown> | undefined {
  if (!value.trim()) return undefined
  const parsed: unknown = JSON.parse(value)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${label} must be a JSON Schema object`)
  return parsed as Record<string, unknown>
}

const agents = ref<AgentDefinition[]>([])
const selectedAgent = ref<AgentDefinition | null>(null)
const selectedRevision = ref<AgentRevision | null>(null)
const revisions = ref<AgentRevision[]>([])
const name = ref('')
const desc = ref('')
const purpose = ref('')
const inputSchemaRef = ref('toonflow.text_input.v1')
const outputSchemaRef = ref('toonflow.artifact_output.v1')
const outputSchemaJson = ref('{\n  "type": "object",\n  "properties": { "text": { "type": "string" } },\n  "required": ["text"]\n}')
const providerModel = ref('atlascloud/default')
const maxAttempts = ref(1)
const maxTokens = ref(4096)
const maxCost = ref(1)
const requestInputEnabled = ref(false)
const requestInputSchemaRefs = ref('')
const requestInputMaxRequests = ref(1)
const requestInputTimeoutMinutes = ref(60)
const requestInputMaxResponseBytes = ref(16_384)
const skillOptions = ref<SkillOption[]>([])
const toolOptions = ref<ToolOption[]>([])
const selectedSkillRefs = ref<string[]>([])
const selectedToolRefs = ref<string[]>([])
const steps = ref<SopStep[]>([{
  step_id: 'draft', instruction: '', retryAttempts: 1, output_schema_ref: '',
  inputBinding: '', outputBinding: '', failureStrategy: 'fail', checkpointMode: 'after_step',
}])
const creating = ref(false)
const draftLoading = ref(false)
const error = ref('')
const revError = ref('')
const validateResult = ref<ValidationResult | null>(null)
const dryRunResult = ref<Record<string, unknown> | null>(null)
const draftVersion = ref(1)
const trialId = ref('')
const trialTask = ref<{ task_id: string; status: string; task_version: number; question?: string; input_schema?: Record<string, unknown> } | null>(null)
const trialAnswer = ref('{"choice":"yes"}')
const cloneName = ref('')
const cloneResult = ref<{ agent_id: string; credential_rebind_required_tool_revision_ids: string[] } | null>(null)
const trialTimeline = computed(() => Array.isArray(dryRunResult.value?.runtime_timeline) ? dryRunResult.value.runtime_timeline as Array<Record<string, unknown>> : [])

const revisionBody = computed(() => ({
  purpose: purpose.value.trim(),
  input_schema_ref: inputSchemaRef.value.trim() || undefined,
  output_schema_ref: outputSchemaRef.value.trim(),
  output_schema: parseSchema(outputSchemaJson.value, 'Output schema'),
  sop_steps: steps.value.map((step) => ({
    step_id: step.step_id.trim(), instruction: step.instruction.trim(),
    input_bindings: step.inputBinding.trim() ? { input: step.inputBinding.trim() } : {},
    output_bindings: step.outputBinding.trim() ? { output: step.outputBinding.trim() } : {},
    output_schema_ref: step.output_schema_ref.trim() || undefined,
    retry_policy: { max_attempts: Math.max(1, step.retryAttempts) },
    failure_policy: { strategy: step.failureStrategy },
    checkpoint_policy: { mode: step.checkpointMode },
  })),
  skill_revision_refs: selectedSkillRefs.value.map((value) => JSON.parse(value)),
  tool_revision_refs: selectedToolRefs.value,
  tool_access_plan: selectedToolRefs.value.map((revisionId) => ({
    tool_revision_id: revisionId,
    operations: (toolOptions.value.find((tool) => tool.revision_id === revisionId)?.operations || []).map((operation) => ({
      operation_id: operation.operation_id, allowed_scopes: [], disclosure_fields: operation.disclosure_fields,
    })),
  })),
  execution_policy: { provider_ref: providerModel.value.trim(), max_attempts: Math.max(1, maxAttempts.value), max_tokens: Math.max(1, maxTokens.value), max_cost: Math.max(0, maxCost.value) },
  request_input_policy: {
    enabled: requestInputEnabled.value,
    allowed_schema_refs: requestInputSchemaRefs.value.split(/[\n,]/).map((value) => value.trim()).filter(Boolean),
    max_requests_per_attempt: Math.max(1, requestInputMaxRequests.value),
    max_timeout_minutes: Math.max(1, requestInputTimeoutMinutes.value),
    max_response_bytes: Math.max(1, requestInputMaxResponseBytes.value),
  },
}))

onMounted(loadAgents)
async function loadAgents() { try { agents.value = await apiGet<AgentDefinition[]>('/agents') } catch { agents.value = [] } }
async function loadRevisions(agentId: string) { try { revisions.value = await apiGet<AgentRevision[]>(`/agents/${agentId}/revisions`) } catch { revisions.value = [] } }
async function createAgent() {
  if (!name.value.trim()) return
  creating.value = true
  try { await apiPost('/agents', { name: name.value, description: desc.value, agent_kind: 'configurable' }); name.value = ''; desc.value = ''; await loadAgents() }
  catch (e) { error.value = errorMessage(e) } finally { creating.value = false }
}
async function loadDependencyCatalog() { try { const catalog = await apiGet<{ skills: SkillOption[]; tools: ToolOption[] }>('/agents/studio/dependencies'); skillOptions.value = catalog.skills; toolOptions.value = catalog.tools } catch { skillOptions.value = []; toolOptions.value = [] } }
function hydrateDraft(body: Record<string, any>) {
  purpose.value = String(body.purpose || '')
  inputSchemaRef.value = String(body.input_schema_ref || 'toonflow.text_input.v1')
  outputSchemaRef.value = String(body.output_schema_ref || 'toonflow.artifact_output.v1')
  if (body.output_schema) outputSchemaJson.value = JSON.stringify(body.output_schema, null, 2)
  const policy = body.execution_policy || {}
  providerModel.value = String(policy.provider_ref || 'atlascloud/default')
  maxAttempts.value = Number(policy.max_attempts || 1)
  maxTokens.value = Number(policy.max_tokens || 4096)
  maxCost.value = Number(policy.max_cost || 1)
  const requestPolicy = body.request_input_policy || {}
  requestInputEnabled.value = Boolean(requestPolicy.enabled)
  requestInputSchemaRefs.value = Array.isArray(requestPolicy.allowed_schema_refs) ? requestPolicy.allowed_schema_refs.join(', ') : ''
  requestInputMaxRequests.value = Number(requestPolicy.max_requests_per_attempt || 1)
  requestInputTimeoutMinutes.value = Number(requestPolicy.max_timeout_minutes || 60)
  requestInputMaxResponseBytes.value = Number(requestPolicy.max_response_bytes || 16_384)
  selectedSkillRefs.value = Array.isArray(body.skill_revision_refs) ? body.skill_revision_refs.map((ref: unknown) => JSON.stringify(ref)) : []
  selectedToolRefs.value = Array.isArray(body.tool_revision_refs) ? body.tool_revision_refs.map(String) : []
  const rawSteps = Array.isArray(body.sop_steps) ? body.sop_steps : []
  steps.value = rawSteps.length ? rawSteps.map((step: Record<string, any>, index: number): SopStep => ({
    step_id: String(step.step_id || `step_${index + 1}`), instruction: String(step.instruction || ''),
    retryAttempts: Number(step.retry_policy?.max_attempts || 1), output_schema_ref: String(step.output_schema_ref || ''),
    inputBinding: String(step.input_bindings?.input || ''), outputBinding: String(step.output_bindings?.output || ''),
    failureStrategy: ['fail', 'retry', 'request_input'].includes(step.failure_policy?.strategy) ? step.failure_policy.strategy : 'fail',
    checkpointMode: step.checkpoint_policy?.mode === 'none' ? 'none' : 'after_step',
  })) : [{ step_id: 'draft', instruction: '', retryAttempts: 1, output_schema_ref: '', inputBinding: '', outputBinding: '', failureStrategy: 'fail', checkpointMode: 'after_step' }]
}
async function selectAgent(agent: AgentDefinition) {
  selectedAgent.value = agent; selectedRevision.value = null; draftLoading.value = true
  try {
    await Promise.all([loadRevisions(agent.agent_id), loadDependencyCatalog()])
    const draft = await apiGet<any>(`/agents/${agent.agent_id}/draft`)
    draftVersion.value = draft.draft_version; hydrateDraft(draft.body || {})
    trialId.value = localStorage.getItem(`toonflow.trial.${agent.agent_id}`) || ''
    if (trialId.value) { try { const tasks = await apiGet<any[]>(`/agents/trials/${trialId.value}/request-input`); trialTask.value = tasks[0] || null } catch { trialTask.value = null } }
  } catch { draftVersion.value = 1 } finally { draftLoading.value = false }
}
function addStep() { steps.value.push({ step_id: `step_${steps.value.length + 1}`, instruction: '', retryAttempts: 1, output_schema_ref: '', inputBinding: '', outputBinding: '', failureStrategy: 'fail', checkpointMode: 'after_step' }) }
function removeStep(index: number) { if (steps.value.length > 1) steps.value.splice(index, 1) }
function moveStep(index: number, offset: number) { const next = index + offset; if (next < 0 || next >= steps.value.length) return; const [step] = steps.value.splice(index, 1); steps.value.splice(next, 0, step) }
async function validateBody() { try { validateResult.value = await apiPost<ValidationResult>('/agents/validate', { body: revisionBody.value }) } catch (e) { validateResult.value = { valid: false, message: errorMessage(e) } } }
async function runDryRun() { if (!selectedAgent.value) return; try { const saved = await apiPut<any>(`/agents/${selectedAgent.value.agent_id}/draft`, { body: revisionBody.value, base_draft_version: draftVersion.value }); draftVersion.value = saved.draft_version; dryRunResult.value = await apiPost<Record<string, unknown>>(`/agents/${selectedAgent.value.agent_id}/draft/dry-run`, { draft_version: draftVersion.value, budget: { max_cost: maxCost.value }, fixed_input: { sample: 'studio' } }); trialId.value = String(dryRunResult.value.trial_id || ''); localStorage.setItem(`toonflow.trial.${selectedAgent.value.agent_id}`, trialId.value) } catch (e) { dryRunResult.value = { valid: false, message: errorMessage(e) } } }
async function createTrialQuestion() { if (!trialId.value) return; try { const task = await apiPost<any>(`/agents/trials/${trialId.value}/request-input`, { schema_ref: 'choice.v1', question: 'Choose', input_schema: { type: 'object', required: ['choice'], properties: { choice: { type: 'string', enum: ['yes', 'no'] } } } }); trialTask.value = task } catch (e) { revError.value = errorMessage(e) } }
async function answerTrialQuestion() { if (!trialTask.value) return; try { const answer = JSON.parse(trialAnswer.value); await apiPost<any>(`/agents/trial-request-input/${trialTask.value.task_id}/answer`, { task_version: trialTask.value.task_version, answer }); trialTask.value = await apiGet<any>(`/agents/trial-request-input/${trialTask.value.task_id}`) } catch (e) { revError.value = errorMessage(e) } }
async function cloneAgent() { if (!selectedAgent.value || !cloneName.value.trim()) return; try { cloneResult.value = await apiPost<any>(`/agents/${selectedAgent.value.agent_id}/clone`, { name: cloneName.value.trim() }); cloneName.value = ''; await loadAgents() } catch (e) { revError.value = errorMessage(e) } }
async function submitRevision(agentId: string) {
  try {
    const saved = await apiPut<any>(`/agents/${agentId}/draft`, { body: revisionBody.value, base_draft_version: draftVersion.value })
    draftVersion.value = saved.draft_version
    const revision = await apiPost<AgentRevision>(`/agents/${agentId}/draft/submit`, { base_draft_version: draftVersion.value })
    selectedRevision.value = revision; revError.value = ''; await loadRevisions(agentId)
  } catch (e) { revError.value = errorMessage(e) }
}
async function promoteRevision(agentId: string, revisionId: string) { try { await apiPost(`/agents/${agentId}/revisions/${revisionId}/promote`, {}); await loadRevisions(agentId); await loadAgents() } catch (e) { revError.value = errorMessage(e) } }
</script>

<template>
  <main class="agent-page">
    <header><h1>Agent Studio</h1><p>结构化 SOP、固定能力与类型化产物。模型调用仅经 AtlasCloud。</p></header>
    <p v-if="error" class="error-banner">{{ error }}</p>
    <form class="create-form" @submit.prevent="createAgent"><input v-model="name" placeholder="Agent 名称" required><input v-model="desc" placeholder="用途摘要"><button type="submit" :disabled="creating">{{ creating ? '创建中...' : '创建 Agent' }}</button></form>
    <div class="agent-list"><button v-for="agent in agents" :key="agent.agent_id" class="agent-card" :class="{ selected: selectedAgent?.agent_id === agent.agent_id }" @click="selectAgent(agent)"><strong>{{ agent.name }}</strong><small>{{ agent.agent_kind }}</small></button></div>
    <p v-if="selectedAgent && draftLoading" class="result">正在加载草稿...</p>
    <section v-if="selectedAgent && !draftLoading" class="editor">
      <h2>{{ selectedAgent.name }}</h2>
      <div class="sections">
        <fieldset><legend>概览与类型</legend><label>用途<input v-model="purpose" placeholder="例如：基于世界观产出小说框架"></label><label>输入 schema ref<input v-model="inputSchemaRef"></label><label>输出 schema ref<input v-model="outputSchemaRef" required></label><label>输出 JSON Schema<textarea v-model="outputSchemaJson" rows="7" spellcheck="false"></textarea></label></fieldset>
        <fieldset><legend>模型策略</legend><label>AtlasCloud 模型<input v-model="providerModel" required></label><div class="number-row"><label>最大尝试<input v-model.number="maxAttempts" type="number" min="1"></label><label>最大 token<input v-model.number="maxTokens" type="number" min="1"></label><label>最大成本<input v-model.number="maxCost" type="number" min="0" step="0.01"></label></div></fieldset>
        <fieldset><legend>RequestInput</legend><label><input v-model="requestInputEnabled" type="checkbox"> 允许补问</label><template v-if="requestInputEnabled"><label>允许 schema refs<input v-model="requestInputSchemaRefs" placeholder="例如 toonflow.choice.v1"></label><div class="number-row"><label>每次尝试最大补问<input v-model.number="requestInputMaxRequests" type="number" min="1" max="16"></label><label>最长等待分钟<input v-model.number="requestInputTimeoutMinutes" type="number" min="1"></label><label>最大回答字节<input v-model.number="requestInputMaxResponseBytes" type="number" min="1"></label></div></template></fieldset>
        <fieldset><legend>SOP 步骤</legend><div v-for="(step, index) in steps" :key="step.step_id || index" class="step"><label>Step ID<input v-model="step.step_id"></label><label class="wide">指令<input v-model="step.instruction" placeholder="明确这个步骤的目标与输出"></label><label>输入映射<input v-model="step.inputBinding" placeholder="例如 brief.text"></label><label>输出映射<input v-model="step.outputBinding" placeholder="例如 artifact.text"></label><label>步骤输出 schema ref<input v-model="step.output_schema_ref"></label><label>失败策略<select v-model="step.failureStrategy"><option value="fail">失败并停止</option><option value="retry">重试后失败</option><option value="request_input">补问后恢复</option></select></label><label>Checkpoint<select v-model="step.checkpointMode"><option value="after_step">每步后保存</option><option value="none">不保存</option></select></label><label>重试<input v-model.number="step.retryAttempts" type="number" min="1"></label><button type="button" class="icon" title="上移步骤" :disabled="index === 0" @click="moveStep(index, -1)">↑</button><button type="button" class="icon" title="下移步骤" :disabled="index === steps.length - 1" @click="moveStep(index, 1)">↓</button><button type="button" class="icon" title="删除步骤" :disabled="steps.length === 1" @click="removeStep(index)">×</button></div><button type="button" @click="addStep">添加 SOP 步骤</button></fieldset>
        <fieldset><legend>固定能力</legend><label>兼容 Skill 修订<select v-model="selectedSkillRefs" multiple aria-label="兼容 Skill 修订"><option v-for="skill in skillOptions" :key="JSON.stringify(skill.ref)" :value="JSON.stringify(skill.ref)">{{ skill.name }} · {{ skill.owner_scope }}</option></select></label><label>已批准 Tool 修订<select v-model="selectedToolRefs" multiple aria-label="已批准 Tool 修订"><option v-for="tool in toolOptions" :key="tool.revision_id" :value="tool.revision_id">{{ tool.name }} · {{ tool.operations.length }} operations</option></select></label></fieldset>
      </div>
      <div class="actions"><button @click="validateBody">静态校验</button><button @click="runDryRun">隔离试跑</button><button class="primary" @click="submitRevision(selectedAgent.agent_id)">提交不可变修订</button></div>
      <p v-if="revError" class="error-banner">{{ revError }}</p><div v-if="validateResult" class="result">校验：{{ validateResult.valid ? '通过' : validateResult.message }}</div><div v-if="dryRunResult" class="result">试跑：{{ dryRunResult.valid ? '合同有效，未提交业务 Resource' : String(dryRunResult.message || dryRunResult.status || '失败') }}<ul v-if="trialTimeline.length" class="trial-timeline"><li v-for="(entry, index) in trialTimeline" :key="index"><strong>{{ entry.phase }}</strong><span v-if="entry.status"> · {{ entry.status }}</span><span v-for="(disclosure, disclosureIndex) in (entry.tool_disclosures as any[] || [])" :key="disclosureIndex"> · Tool {{ String(disclosure.tool_revision_id || '').slice(0, 8) }} / {{ disclosure.operation_id || '' }} / {{ (disclosure.fields || []).join(', ') || 'no fields' }}</span></li></ul></div>
      <div v-if="trialId" class="result trial"><strong>Trial {{ trialId.slice(0, 8) }}</strong><button @click="createTrialQuestion">创建补问</button><template v-if="trialTask"><span>{{ trialTask.status }}</span><input v-model="trialAnswer" aria-label="Trial answer JSON"><button @click="answerTrialQuestion">提交回答</button></template></div>
      <div class="clone-row"><input v-model="cloneName" aria-label="Clone Agent name" placeholder="克隆 Agent 名称"><button @click="cloneAgent">克隆并重新绑定</button></div>
      <div v-if="cloneResult" class="result clone-result">已克隆 {{ cloneResult.agent_id.slice(0, 8) }}<span v-if="cloneResult.credential_rebind_required_tool_revision_ids.length"> · 必须重新绑定 Tool：{{ cloneResult.credential_rebind_required_tool_revision_ids.map(id => id.slice(0, 8)).join(', ') }}</span><span v-else> · 无 Tool 凭证需要重新绑定</span></div>
      <div v-if="selectedRevision" class="actions"><button class="primary" @click="promoteRevision(selectedAgent.agent_id, selectedRevision.revision_id)">发布当前修订</button></div>
      <div class="revisions"><div v-for="revision in revisions" :key="revision.revision_id"><code>r{{ revision.revision_number }} · {{ revision.revision_status }}</code><small>{{ revision.content_hash.slice(0, 12) }}</small></div></div>
    </section>
  </main>
</template>

<style scoped>
.agent-page { max-width: 1040px; margin: 0 auto; padding: 24px; color: var(--text-primary); } header p { color: var(--text-secondary); } .create-form,.actions,.number-row,.clone-row { display:flex; gap:8px; flex-wrap:wrap; } input,textarea,select,button { box-sizing:border-box; font:inherit; } input,textarea,select { width:100%; padding:8px; background:var(--bg-primary); color:var(--text-primary); border:1px solid var(--border); border-radius:4px; } input[type="checkbox"] { width:auto; margin-right:6px; } select[multiple] { min-height:88px; } button { padding:7px 12px; color:var(--text-primary); background:var(--bg-primary); border:1px solid var(--border); border-radius:4px; cursor:pointer; } button.primary,.create-form button { background:var(--accent); color:white; } .create-form input,.clone-row input { flex:1; min-width:180px; } .agent-list { display:flex; gap:8px; margin:16px 0; flex-wrap:wrap; } .agent-card { display:grid; text-align:left; min-width:150px; } .agent-card.selected { border-color:var(--accent); } small { color:var(--text-secondary); } .editor { border:1px solid var(--border); background:var(--bg-secondary); padding:16px; border-radius:6px; } .sections { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; } fieldset { min-width:0; border:1px solid var(--border); border-radius:4px; padding:12px; } label { display:block; font-size:13px; margin-bottom:8px; } .step { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:6px; align-items:end; border-top:1px solid var(--border); padding:8px 0; } .step label { margin:0; } .icon { min-width:32px; } .actions,.clone-row { margin-top:14px; } .result,.error-banner { margin-top:10px; padding:8px; border-radius:4px; background:var(--bg-primary); } .error-banner { color:#b91c1c; background:#fef2f2; } .trial-timeline { margin:8px 0 0; padding-left:20px; } .trial-timeline li { padding:2px 0; } .revisions { margin-top:14px; display:grid; gap:4px; } .revisions div { display:flex; justify-content:space-between; padding:6px; border:1px solid var(--border); } @media (max-width:760px) { .sections { grid-template-columns:1fr; } .step { grid-template-columns:1fr; } }
</style>
