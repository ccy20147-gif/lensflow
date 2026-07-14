<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiGet, apiPost, apiPut } from '@/api/client'

interface AgentDefinition { agent_id: string; name: string; description: string; agent_kind: string }
interface AgentRevision { revision_id: string; revision_number: number; revision_status: string; content_hash: string }
interface SopStep { step_id: string; instruction: string; retryAttempts: number; output_schema_ref: string }
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
const skillRefs = ref('')
const toolRefs = ref('')
const steps = ref<SopStep[]>([{ step_id: 'draft', instruction: '', retryAttempts: 1, output_schema_ref: '' }])
const creating = ref(false)
const error = ref('')
const revError = ref('')
const validateResult = ref<ValidationResult | null>(null)
const dryRunResult = ref<Record<string, unknown> | null>(null)
const draftVersion = ref(1)
const trialId = ref('')
const trialTask = ref<{ task_id: string; status: string; task_version: number; question?: string; input_schema?: Record<string, unknown> } | null>(null)
const trialAnswer = ref('{"choice":"yes"}')

const revisionBody = computed(() => ({
  purpose: purpose.value.trim(),
  input_schema_ref: inputSchemaRef.value.trim() || undefined,
  output_schema_ref: outputSchemaRef.value.trim(),
  output_schema: parseSchema(outputSchemaJson.value, 'Output schema'),
  sop_steps: steps.value.map((step) => ({
    step_id: step.step_id.trim(), instruction: step.instruction.trim(),
    output_schema_ref: step.output_schema_ref.trim() || undefined,
    retry_policy: { max_attempts: Math.max(1, step.retryAttempts) },
  })),
  skill_revision_refs: skillRefs.value.split(/[,\n]/).map((v) => v.trim()).filter(Boolean),
  tool_revision_refs: toolRefs.value.split(/[,\n]/).map((v) => v.trim()).filter(Boolean),
  execution_policy: { provider_ref: providerModel.value.trim(), max_attempts: Math.max(1, maxAttempts.value), max_tokens: Math.max(1, maxTokens.value), max_cost: Math.max(0, maxCost.value) },
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
async function selectAgent(agent: AgentDefinition) { selectedAgent.value = agent; selectedRevision.value = null; await loadRevisions(agent.agent_id); try { const draft = await apiGet<any>(`/agents/${agent.agent_id}/draft`); draftVersion.value = draft.draft_version } catch { draftVersion.value = 1 }; trialId.value = localStorage.getItem(`toonflow.trial.${agent.agent_id}`) || ''; if (trialId.value) { try { const tasks = await apiGet<any[]>(`/agents/trials/${trialId.value}/request-input`); trialTask.value = tasks[0] || null } catch { trialTask.value = null } } }
function addStep() { steps.value.push({ step_id: `step_${steps.value.length + 1}`, instruction: '', retryAttempts: 1, output_schema_ref: '' }) }
function removeStep(index: number) { if (steps.value.length > 1) steps.value.splice(index, 1) }
async function validateBody() { try { validateResult.value = await apiPost<ValidationResult>('/agents/validate', { body: revisionBody.value }) } catch (e) { validateResult.value = { valid: false, message: errorMessage(e) } } }
async function runDryRun() { if (!selectedAgent.value) return; try { const saved = await apiPut<any>(`/agents/${selectedAgent.value.agent_id}/draft`, { body: revisionBody.value, base_draft_version: draftVersion.value }); draftVersion.value = saved.draft_version; dryRunResult.value = await apiPost<Record<string, unknown>>(`/agents/${selectedAgent.value.agent_id}/draft/dry-run`, { draft_version: draftVersion.value, budget: { max_cost: maxCost.value }, fixed_input: { sample: 'studio' }, simulated_output: { text: 'trial' }, usage: { tokens: 1 } }); trialId.value = String(dryRunResult.value.trial_id || ''); localStorage.setItem(`toonflow.trial.${selectedAgent.value.agent_id}`, trialId.value) } catch (e) { dryRunResult.value = { valid: false, message: errorMessage(e) } } }
async function createTrialQuestion() { if (!trialId.value) return; try { const task = await apiPost<any>(`/agents/trials/${trialId.value}/request-input`, { schema_ref: 'choice.v1', question: 'Choose', input_schema: { type: 'object', required: ['choice'], properties: { choice: { type: 'string', enum: ['yes', 'no'] } } } }); trialTask.value = task } catch (e) { revError.value = errorMessage(e) } }
async function answerTrialQuestion() { if (!trialTask.value) return; try { const answer = JSON.parse(trialAnswer.value); await apiPost<any>(`/agents/trial-request-input/${trialTask.value.task_id}/answer`, { task_version: trialTask.value.task_version, answer }); trialTask.value = await apiGet<any>(`/agents/trial-request-input/${trialTask.value.task_id}`) } catch (e) { revError.value = errorMessage(e) } }
async function createRevision(agentId: string) { try { const rev = await apiPost<AgentRevision>(`/agents/${agentId}/revisions`, { body: revisionBody.value, base_hash: null }); selectedRevision.value = rev; revError.value = ''; await loadRevisions(agentId) } catch (e) { revError.value = errorMessage(e) } }
async function promoteRevision(agentId: string, revisionId: string) { try { await apiPost(`/agents/${agentId}/revisions/${revisionId}/promote`, {}); await loadRevisions(agentId); await loadAgents() } catch (e) { revError.value = errorMessage(e) } }
</script>

<template>
  <main class="agent-page">
    <header><h1>Agent Studio</h1><p>结构化 SOP、固定能力与类型化产物。模型调用仅经 AtlasCloud。</p></header>
    <p v-if="error" class="error-banner">{{ error }}</p>
    <form class="create-form" @submit.prevent="createAgent"><input v-model="name" placeholder="Agent 名称" required><input v-model="desc" placeholder="用途摘要"><button type="submit" :disabled="creating">{{ creating ? '创建中...' : '创建 Agent' }}</button></form>
    <div class="agent-list"><button v-for="agent in agents" :key="agent.agent_id" class="agent-card" :class="{ selected: selectedAgent?.agent_id === agent.agent_id }" @click="selectAgent(agent)"><strong>{{ agent.name }}</strong><small>{{ agent.agent_kind }}</small></button></div>
    <section v-if="selectedAgent" class="editor">
      <h2>{{ selectedAgent.name }}</h2>
      <div class="sections">
        <fieldset><legend>概览与类型</legend><label>用途<input v-model="purpose" placeholder="例如：基于世界观产出小说框架"></label><label>输入 schema ref<input v-model="inputSchemaRef"></label><label>输出 schema ref<input v-model="outputSchemaRef" required></label><label>输出 JSON Schema<textarea v-model="outputSchemaJson" rows="7" spellcheck="false"></textarea></label></fieldset>
        <fieldset><legend>模型策略</legend><label>AtlasCloud 模型<input v-model="providerModel" required></label><div class="number-row"><label>最大尝试<input v-model.number="maxAttempts" type="number" min="1"></label><label>最大 token<input v-model.number="maxTokens" type="number" min="1"></label><label>最大成本<input v-model.number="maxCost" type="number" min="0" step="0.01"></label></div></fieldset>
        <fieldset><legend>SOP 步骤</legend><div v-for="(step, index) in steps" :key="index" class="step"><label>Step ID<input v-model="step.step_id"></label><label class="wide">指令<input v-model="step.instruction" placeholder="明确这个步骤的目标与输出"></label><label>重试<input v-model.number="step.retryAttempts" type="number" min="1"></label><label>步骤输出 schema ref<input v-model="step.output_schema_ref"></label><button type="button" class="icon" title="删除步骤" :disabled="steps.length === 1" @click="removeStep(index)">×</button></div><button type="button" @click="addStep">添加 SOP 步骤</button></fieldset>
        <fieldset><legend>固定能力</legend><label>SkillRevision IDs（逗号或换行分隔）<textarea v-model="skillRefs" rows="2" placeholder="只允许已授权、固定版本的 Skill"></textarea></label><label>ToolRevision IDs（逗号或换行分隔）<textarea v-model="toolRefs" rows="2" placeholder="只允许批准工具；凭证不会显示"></textarea></label></fieldset>
      </div>
      <div class="actions"><button @click="validateBody">静态校验</button><button @click="runDryRun">隔离试跑</button><button class="primary" @click="createRevision(selectedAgent.agent_id)">提交不可变修订</button></div>
      <p v-if="revError" class="error-banner">{{ revError }}</p><div v-if="validateResult" class="result">校验：{{ validateResult.valid ? '通过' : validateResult.message }}</div><div v-if="dryRunResult" class="result">试跑：{{ dryRunResult.valid ? '合同有效，未提交业务 Resource' : String(dryRunResult.message || '失败') }}</div>
      <div v-if="trialId" class="result trial"><strong>Trial {{ trialId.slice(0, 8) }}</strong><button @click="createTrialQuestion">创建补问</button><template v-if="trialTask"><span>{{ trialTask.status }}</span><input v-model="trialAnswer" aria-label="Trial answer JSON"><button @click="answerTrialQuestion">提交回答</button></template></div>
      <div v-if="selectedRevision" class="actions"><button class="primary" @click="promoteRevision(selectedAgent.agent_id, selectedRevision.revision_id)">发布当前修订</button></div>
      <div class="revisions"><div v-for="revision in revisions" :key="revision.revision_id"><code>r{{ revision.revision_number }} · {{ revision.revision_status }}</code><small>{{ revision.content_hash.slice(0, 12) }}</small></div></div>
    </section>
  </main>
</template>

<style scoped>
.agent-page { max-width: 1040px; margin: 0 auto; padding: 24px; color: var(--text-primary); } header p { color: var(--text-secondary); } .create-form,.actions,.number-row { display:flex; gap:8px; flex-wrap:wrap; } input,textarea,button { box-sizing:border-box; font:inherit; } input,textarea { width:100%; padding:8px; background:var(--bg-primary); color:var(--text-primary); border:1px solid var(--border); border-radius:4px; } button { padding:7px 12px; color:var(--text-primary); background:var(--bg-primary); border:1px solid var(--border); border-radius:4px; cursor:pointer; } button.primary,.create-form button { background:var(--accent); color:white; } .create-form input { flex:1; min-width:180px; } .agent-list { display:flex; gap:8px; margin:16px 0; flex-wrap:wrap; } .agent-card { display:grid; text-align:left; min-width:150px; } .agent-card.selected { border-color:var(--accent); } small { color:var(--text-secondary); } .editor { border:1px solid var(--border); background:var(--bg-secondary); padding:16px; border-radius:6px; } .sections { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; } fieldset { min-width:0; border:1px solid var(--border); border-radius:4px; padding:12px; } label { display:block; font-size:13px; margin-bottom:8px; } .step { display:grid; grid-template-columns:1fr 2fr .75fr 1fr auto; gap:6px; align-items:end; border-top:1px solid var(--border); padding:8px 0; } .step label { margin:0; } .icon { min-width:32px; } .actions { margin-top:14px; } .result,.error-banner { margin-top:10px; padding:8px; border-radius:4px; background:var(--bg-primary); } .error-banner { color:#b91c1c; background:#fef2f2; } .revisions { margin-top:14px; display:grid; gap:4px; } .revisions div { display:flex; justify-content:space-between; padding:6px; border:1px solid var(--border); } @media (max-width:760px) { .sections { grid-template-columns:1fr; } .step { grid-template-columns:1fr; } }
</style>
