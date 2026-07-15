<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  apiGet,
  createRecipe,
  createRecipeRevision,
  diffRecipeRevisions,
  dryRunRecipe,
  executeRecipeTrial,
  listRecipeRevisions,
  listRecipes,
  promoteRecipeRevision,
  retireRecipeRevision,
  type MediaRecipeDiff,
  type MediaRecipeRecord,
  type MediaRecipeRevisionRecord,
  type RecipeDryRunResult,
  validateRecipeBody,
} from '@/api/client'

type OperatorKind = 'input' | 'image_loader' | 'video_loader' | 'audio_loader' | 'resize' | 'crop' | 'format_convert' | 'color_convert' | 'frame_extract' | 'score' | 'branch' | 'merge' | 'atlas_image' | 'atlas_video' | 'atlas_llm'
interface OperatorRow { id: string; type: OperatorKind; modelId: string; inputs: string; outputs: string; requiredControls: string; supportedControls: string; unsupportedPolicy: 'block' | 'degrade'; parameters: string }

// This is intentionally a finite, client-visible mirror of the V1 server
// operator registry. It is not executable code and a server compile remains
// the authoritative policy check before every save or run.
const operatorCatalog: Array<{ value: OperatorKind; label: string; provider?: boolean }> = [
  { value: 'input', label: '输入' }, { value: 'image_loader', label: '图像载入' }, { value: 'video_loader', label: '视频载入' }, { value: 'audio_loader', label: '音频载入' },
  { value: 'resize', label: '缩放' }, { value: 'crop', label: '裁切' }, { value: 'format_convert', label: '格式转换' }, { value: 'color_convert', label: '色彩转换' }, { value: 'frame_extract', label: '抽帧' },
  { value: 'score', label: '评分' }, { value: 'branch', label: '有限分支' }, { value: 'merge', label: '合并' },
  { value: 'atlas_image', label: 'AtlasCloud 图像', provider: true }, { value: 'atlas_video', label: 'AtlasCloud 视频', provider: true }, { value: 'atlas_llm', label: 'AtlasCloud 文本', provider: true },
]

function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error) }
function lines(value: string): string[] { return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean) }
function parseObject(value: string, label: string): Record<string, unknown> {
  if (!value.trim()) return {}
  const parsed: unknown = JSON.parse(value)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${label} 必须是 JSON 对象`)
  return parsed as Record<string, unknown>
}
function pretty(value: unknown): string { return JSON.stringify(value ?? {}, null, 2) }
function defaultOperators(): OperatorRow[] {
  return [
    { id: 'source', type: 'input', modelId: '', inputs: '', outputs: 'prompt', requiredControls: '', supportedControls: '', unsupportedPolicy: 'block', parameters: '{}' },
    { id: 'generate', type: 'atlas_image', modelId: '', inputs: 'source.prompt', outputs: 'media', requiredControls: '', supportedControls: '', unsupportedPolicy: 'block', parameters: '{}' },
  ]
}

const recipes = ref<MediaRecipeRecord[]>([])
const selectedRecipe = ref<MediaRecipeRecord | null>(null)
const revisions = ref<MediaRecipeRevisionRecord[]>([])
const selectedRevision = ref<MediaRecipeRevisionRecord | null>(null)
const name = ref('')
const desc = ref('')
const recipeType = ref('image_pipeline')
const creating = ref(false)
const saving = ref(false)
const error = ref('')
const status = ref('')
const inputRefs = ref('toonflow.prompt.v1')
const outputRefs = ref('toonflow.media_output.v1')
const capabilityRequirements = ref('atlascloud.image_generation')
const parameterSchema = ref('{\n  "type": "object",\n  "properties": {\n    "seed": { "type": "integer", "default": 0 }\n  }\n}')
const operators = ref<OperatorRow[]>(defaultOperators())
const validation = ref<{ valid: boolean; message?: string } | null>(null)
const dryRun = ref<RecipeDryRunResult | null>(null)
const diff = ref<MediaRecipeDiff | null>(null)
const trialInputs = ref('{\n  "prompt": "Recipe Lab trial"\n}')
const trialRunning = ref(false)
const trialRun = ref<{ status: string; run_id: string; nodes: Array<Record<string, unknown>> } | null>(null)

const revisionBody = computed<Record<string, unknown>>(() => ({
  recipe_type: recipeType.value,
  public_input_schema_refs: lines(inputRefs.value),
  public_output_schema_refs: lines(outputRefs.value),
  capability_requirements: lines(capabilityRequirements.value),
  parameter_schema: parseObject(parameterSchema.value, '参数 schema'),
  operator_graph: Object.fromEntries(operators.value.map((operator) => [operator.id.trim(), {
    type: operator.type,
    model_id: operator.modelId.trim() || undefined,
    inputs: lines(operator.inputs),
    outputs: lines(operator.outputs),
    parameters: parseObject(operator.parameters, `${operator.id || '算子'} 参数`),
    required_controls: lines(operator.requiredControls),
    supported_controls: lines(operator.supportedControls),
    unsupported_policy: operator.unsupportedPolicy,
  }])),
}))

onMounted(loadRecipes)
async function loadRecipes() {
  try { recipes.value = await listRecipes() } catch (caught) { error.value = errorMessage(caught) }
}
async function createDefinition() {
  if (!name.value.trim()) return
  creating.value = true; error.value = ''
  try {
    const created = await createRecipe({ name: name.value.trim(), description: desc.value.trim(), recipe_type: recipeType.value })
    name.value = ''; desc.value = ''
    await loadRecipes()
    await selectRecipe(created)
    status.value = 'Recipe 已创建；填写公开合同后提交首个不可变修订。'
  } catch (caught) { error.value = errorMessage(caught) } finally { creating.value = false }
}
async function selectRecipe(recipe: MediaRecipeRecord) {
  selectedRecipe.value = recipe; selectedRevision.value = null; diff.value = null; error.value = ''; dryRun.value = null
  try { revisions.value = await listRecipeRevisions(recipe.recipe_id) } catch (caught) { error.value = errorMessage(caught) }
  const latest = revisions.value[0]
  if (latest) loadRevision(latest)
}
function loadRevision(revision: MediaRecipeRevisionRecord) {
  selectedRevision.value = revision; diff.value = null
  const graph = revision.operator_graph || {}
  recipeType.value = (graph && (revision as unknown as { recipe_type?: string }).recipe_type) || selectedRecipe.value?.recipe_type || recipeType.value
  inputRefs.value = (revision.public_input_schema_refs || []).join('\n')
  outputRefs.value = (revision.public_output_schema_refs || []).join('\n')
  capabilityRequirements.value = (revision.capability_requirements || []).join('\n')
  parameterSchema.value = pretty(revision.parameter_schema)
  const rows: OperatorRow[] = Object.entries(graph).map(([id, raw]) => {
    const node = (raw || {}) as Record<string, unknown>
    return {
      id, type: String(node.type || 'input') as OperatorKind, modelId: String(node.model_id || ''), inputs: Array.isArray(node.inputs) ? node.inputs.join(', ') : '', outputs: Array.isArray(node.outputs) ? node.outputs.join(', ') : '',
      requiredControls: Array.isArray(node.required_controls) ? node.required_controls.join(', ') : '', supportedControls: Array.isArray(node.supported_controls) ? node.supported_controls.join(', ') : '',
      unsupportedPolicy: (node.unsupported_policy === 'degrade' ? 'degrade' : 'block') as OperatorRow['unsupportedPolicy'], parameters: pretty(node.parameters),
    }
  })
  operators.value = rows.length ? rows : defaultOperators()
}
function addOperator() { operators.value.push({ id: `step_${operators.value.length + 1}`, type: 'format_convert', modelId: '', inputs: '', outputs: '', requiredControls: '', supportedControls: '', unsupportedPolicy: 'block', parameters: '{}' }) }
function removeOperator(index: number) { if (operators.value.length > 1) operators.value.splice(index, 1) }
async function validate() {
  try { validation.value = await validateRecipeBody(revisionBody.value); dryRun.value = null } catch (caught) { validation.value = { valid: false, message: errorMessage(caught) } }
}
async function runDryRun() {
  try { dryRun.value = await dryRunRecipe(revisionBody.value); validation.value = { valid: true } } catch (caught) { validation.value = { valid: false, message: errorMessage(caught) }; dryRun.value = null }
}
async function runProviderTrial() {
  if (!selectedRecipe.value || !selectedRevision.value || selectedRevision.value.revision_status !== 'active') {
    error.value = '请先发布此 Recipe 修订，再执行 AtlasCloud 试跑。'
    return
  }
  trialRunning.value = true; error.value = ''; trialRun.value = null
  try {
    const inputs = parseObject(trialInputs.value, '试跑输入')
    const result = await executeRecipeTrial(selectedRecipe.value.recipe_id, selectedRevision.value.revision_id, {
      inputs,
      idempotency_key: `recipe-lab:${selectedRevision.value.revision_id}:${globalThis.crypto.randomUUID()}`,
    })
    trialRun.value = await apiGet<{ status: string; run_id: string; nodes: Array<Record<string, unknown>> }>(`/workflow-runs/${result.run_id}`)
  } catch (caught) { error.value = errorMessage(caught) } finally { trialRunning.value = false }
}
async function saveRevision() {
  if (!selectedRecipe.value) return
  saving.value = true; error.value = ''; status.value = ''
  try {
    const saved = await createRecipeRevision(selectedRecipe.value.recipe_id, { body: revisionBody.value, base_hash: revisions.value[0]?.content_hash || null })
    await selectRecipe(selectedRecipe.value)
    const current = revisions.value.find((revision) => revision.revision_id === saved.revision_id)
    if (current) loadRevision(current)
    status.value = '草稿修订已保存。发布前可继续试跑或查看差异。'
  } catch (caught) { error.value = errorMessage(caught) } finally { saving.value = false }
}
async function promote() {
  if (!selectedRecipe.value || !selectedRevision.value) return
  try { await promoteRecipeRevision(selectedRecipe.value.recipe_id, selectedRevision.value.revision_id); await selectRecipe(selectedRecipe.value); status.value = '修订已发布为固定主画布节点版本。' } catch (caught) { error.value = errorMessage(caught) }
}
async function retire() {
  if (!selectedRecipe.value || !selectedRevision.value) return
  try { await retireRecipeRevision(selectedRecipe.value.recipe_id, selectedRevision.value.revision_id); await selectRecipe(selectedRecipe.value); status.value = '修订已退役，仅保留只读审计。' } catch (caught) { error.value = errorMessage(caught) }
}
async function showDiff(revision: MediaRecipeRevisionRecord) {
  if (!selectedRecipe.value || !selectedRevision.value || revision.revision_id === selectedRevision.value.revision_id) return
  try { diff.value = await diffRecipeRevisions(selectedRecipe.value.recipe_id, selectedRevision.value.revision_id, revision.revision_id) } catch (caught) { error.value = errorMessage(caught) }
}
</script>

<template>
  <main class="recipe-page">
    <header class="page-header"><div><h1>Media Recipe Lab</h1><p>有限媒体算子图在此编辑；主画布仅固定引用已发布的单个 Recipe 修订。</p></div></header>
    <p v-if="error" class="error-banner">{{ error }}</p><p v-if="status" class="status">{{ status }}</p>

    <form class="create-form" @submit.prevent="createDefinition">
      <input v-model="name" placeholder="Recipe 名称" required><input v-model="desc" placeholder="描述">
      <select v-model="recipeType" aria-label="Recipe 类型"><option value="image_pipeline">图像流程</option><option value="video_pipeline">视频流程</option></select>
      <button type="submit" :disabled="creating">{{ creating ? '创建中...' : '创建 Recipe' }}</button>
    </form>

    <section class="recipe-list" aria-label="Recipe 列表"><button v-for="recipe in recipes" :key="recipe.recipe_id" type="button" class="recipe-card" :class="{ selected: selectedRecipe?.recipe_id === recipe.recipe_id }" @click="selectRecipe(recipe)"><strong>{{ recipe.name }}</strong><small>{{ recipe.recipe_type }}</small></button><p v-if="!recipes.length" class="empty">尚无 Recipe 定义。</p></section>

    <section v-if="selectedRecipe" class="lab-editor">
      <div class="editor-heading"><div><h2>{{ selectedRecipe.name }}</h2><small>{{ selectedRecipe.description || '未填写描述' }}</small></div><span>当前公开合同与算子图</span></div>
      <div class="contract-grid">
        <label>公开输入 schema refs<textarea v-model="inputRefs" rows="2" placeholder="toonflow.prompt.v1"></textarea></label>
        <label>公开输出 schema refs<textarea v-model="outputRefs" rows="2" placeholder="toonflow.media_output.v1"></textarea></label>
        <label>AtlasCloud 能力要求<textarea v-model="capabilityRequirements" rows="2" placeholder="atlascloud.image_generation"></textarea></label>
        <label>参数 schema<textarea v-model="parameterSchema" rows="6" spellcheck="false"></textarea></label>
      </div>
      <section class="operator-section"><div class="section-title"><h3>有限算子图</h3><button type="button" @click="addOperator">添加算子</button></div><p class="hint">只可选择注册的媒体、转换、评分、有限分支及 AtlasCloud 算子。保存和运行均由服务端再校验。</p>
        <article v-for="(operator, index) in operators" :key="`${operator.id}-${index}`" class="operator-row">
          <label>步骤 ID<input v-model="operator.id" placeholder="步骤 ID"></label>
          <label>算子<select v-model="operator.type" aria-label="Operator 类型"><option v-for="option in operatorCatalog" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
          <label>Atlas 模型<input v-model="operator.modelId" placeholder="仅 AtlasCloud 算子"></label>
          <label>前驱输出<input v-model="operator.inputs" placeholder="source.prompt, other.media"></label>
          <label>本算子输出<input v-model="operator.outputs" placeholder="media"></label>
          <label>请求控制<input v-model="operator.requiredControls" placeholder="pose, camera"></label>
          <label>已支持控制<input v-model="operator.supportedControls" placeholder="pose"></label>
          <label>不支持策略<select v-model="operator.unsupportedPolicy"><option value="block">阻断</option><option value="degrade">明确降级</option></select></label>
          <label class="operator-params">算子参数<textarea v-model="operator.parameters" rows="3" spellcheck="false"></textarea></label>
          <button type="button" class="icon" title="删除算子" :disabled="operators.length === 1" @click="removeOperator(index)">×</button>
        </article>
      </section>
      <div class="actions"><button type="button" @click="validate">validate</button><button type="button" @click="runDryRun">试跑编译</button><button type="button" class="primary" :disabled="saving" @click="saveRevision">{{ saving ? '保存中...' : '提交草稿修订' }}</button></div>
      <div v-if="validation" class="result">静态校验：{{ validation.valid ? '通过' : validation.message }}</div>
      <div v-if="dryRun" class="trace"><strong>试跑 trace</strong><span>{{ dryRun.step_count }} 个算子 · {{ dryRun.plan_hash.slice(0, 12) }}</span><ul><li v-for="outcome in dryRun.control_outcomes" :key="`${outcome.operator_id}-${outcome.control}`"><code>{{ outcome.operator_id }}</code> · {{ outcome.control }}: <b :class="`outcome-${outcome.outcome}`">{{ outcome.outcome }}</b></li><li v-if="!dryRun.control_outcomes.length">无请求控制项，未发生静默控制降级。</li></ul></div>

      <section class="versions"><h3>不可变修订</h3><div v-for="revision in revisions" :key="revision.revision_id" class="revision"><button type="button" class="revision-select" :class="{ selected: selectedRevision?.revision_id === revision.revision_id }" @click="loadRevision(revision)"><strong>r{{ revision.revision_number }}</strong><span>{{ revision.revision_status }}</span><code>{{ revision.content_hash.slice(0, 12) }}</code></button><div class="revision-actions"><button type="button" @click="showDiff(revision)" :disabled="selectedRevision?.revision_id === revision.revision_id">与当前比较</button><button v-if="selectedRevision?.revision_id === revision.revision_id && revision.revision_status === 'draft'" type="button" class="primary" @click="promote">发布</button><button v-if="selectedRevision?.revision_id === revision.revision_id && revision.revision_status === 'active'" type="button" @click="retire">退役</button></div></div>
        <pre v-if="diff" class="diff" aria-label="修订差异">{{ JSON.stringify(diff, null, 2) }}</pre>
      </section>
      <section v-if="selectedRevision?.revision_status === 'active'" class="provider-trial" aria-label="AtlasCloud Recipe 试跑">
        <h3>AtlasCloud 受控试跑</h3>
        <p>使用当前已发布修订创建隔离运行，输入、provider dispatch、算子状态、产物和成本均持久化。</p>
        <label>固定输入 JSON<textarea v-model="trialInputs" rows="4" spellcheck="false"></textarea></label>
        <button type="button" class="primary" :disabled="trialRunning" @click="runProviderTrial">{{ trialRunning ? '正在提交 AtlasCloud...' : '执行受控试跑' }}</button>
        <div v-if="trialRun" class="trace provider-trace"><strong>运行 {{ trialRun.status }}</strong><ul><li v-for="node in trialRun.nodes" :key="String(node.node_run_id)"><code>{{ node.node_instance_id }}</code> · {{ node.status }}<ul><li v-for="attempt in (node.attempts as Array<Record<string, unknown>>)" :key="String(attempt.attempt_id)">epoch {{ attempt.execution_epoch }} · {{ attempt.status }} · {{ attempt.provider_id || 'internal' }} · 成本 {{ attempt.actual_cost ?? '-' }} · 产物 {{ (attempt.output_artifact_version_ids as string[] || []).length }}</li></ul></li></ul></div>
      </section>
    </section>
    <p class="provider-note">真实生成只经 AtlasCloud 服务端适配器；未配置凭证时在网络副作用前安全拒绝。</p>
  </main>
</template>

<style scoped>
.recipe-page { max-width: 1180px; margin: 0 auto; padding: 24px; color: var(--text-primary); }.page-header h1,.page-header p { margin:0; }.page-header p,.hint,small,.empty { color:var(--text-secondary); }.page-header p { margin-top:6px; }.create-form,.actions { display:flex; gap:8px; flex-wrap:wrap; margin:20px 0; }.create-form input { flex:1; min-width:180px; }.create-form select { width:150px; }.recipe-list { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:18px; }.recipe-card,.revision-select { display:grid; text-align:left; gap:3px; min-width:150px; }.recipe-card.selected,.revision-select.selected { border-color:var(--accent); box-shadow:inset 3px 0 var(--accent); }.lab-editor { border:1px solid var(--border); background:var(--bg-secondary); padding:16px; border-radius:6px; }.editor-heading { display:flex; justify-content:space-between; gap:16px; align-items:start; }.editor-heading h2,.operator-section h3,.versions h3,.provider-trial h3 { margin:0; }.editor-heading > span { color:var(--text-secondary); font-size:12px; }.contract-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin:16px 0; }.contract-grid label,.operator-row label,.provider-trial label { display:grid; gap:4px; font-size:12px; color:var(--text-secondary); }.operator-section { border-top:1px solid var(--border); padding-top:14px; }.section-title { display:flex; justify-content:space-between; align-items:center; }.hint { font-size:12px; }.operator-row { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; align-items:end; border-top:1px solid var(--border); padding:12px 0; }.operator-params { grid-column:span 3; }.icon { min-width:34px; }.actions { margin-bottom:0; }.primary,.create-form button { background:var(--accent); color:#fff; }.result,.trace,.status,.error-banner { margin-top:12px; padding:9px; border-radius:4px; background:var(--bg-primary); }.error-banner { color:#b91c1c; background:#fef2f2; }.status { color:#166534; background:#f0fdf4; }.trace { display:grid; gap:6px; }.trace ul { margin:0; padding-left:18px; }.outcome-applied { color:#166534; }.outcome-degraded,.outcome-transformed,.outcome-ignored_with_warning { color:#a16207; }.outcome-blocked { color:#b91c1c; }.versions,.provider-trial { border-top:1px solid var(--border); margin-top:16px; padding-top:14px; }.provider-trial { display:grid; gap:8px; }.provider-trial p { margin:0; color:var(--text-secondary); font-size:12px; }.revision { display:flex; justify-content:space-between; gap:10px; padding:8px 0; border-bottom:1px solid var(--border); }.revision-select { border:0; padding:0; background:transparent; min-width:0; }.revision-actions { display:flex; align-items:center; gap:6px; }.diff { overflow:auto; max-height:380px; padding:10px; background:var(--bg-primary); font-size:11px; }.provider-note { font-size:12px; color:#b45309; margin-top:16px; } input,textarea,select,button { box-sizing:border-box; font:inherit; } input,textarea,select { width:100%; padding:7px; border:1px solid var(--border); border-radius:4px; background:var(--bg-primary); color:var(--text-primary); } button { padding:7px 10px; border:1px solid var(--border); border-radius:4px; background:var(--bg-primary); color:var(--text-primary); cursor:pointer; } button:disabled { opacity:.55; cursor:not-allowed; } @media (max-width:760px) { .contract-grid,.operator-row { grid-template-columns:1fr; }.operator-params { grid-column:auto; }.editor-heading,.revision { flex-direction:column; }.create-form select { width:100%; } }
</style>
