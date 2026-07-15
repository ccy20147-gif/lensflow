<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getTemplate,
  getTemplateReplacementOptions,
  instantiateTemplate,
  listTemplates,
  resolveTemplateDependencies,
  type TemplateDependencyResolution,
  type TemplateDetail,
  type TemplateSummary,
} from '@/api/client'

const route = useRoute()
const router = useRouter()
const templates = ref<TemplateSummary[]>([])
const selected = ref<TemplateDetail | null>(null)
const resolution = ref<TemplateDependencyResolution | null>(null)
const replacements = ref<Record<string, string>>({})
const replacementOptions = ref<Record<string, Array<{ revision_id: string; label: string }>>>({})
const projectName = ref('')
const parametersText = ref('{}')
const loading = ref(true)
const instantiating = ref(false)
const error = ref('')

const projectId = computed(() => String(route.params.projectId || ''))

async function loadTemplates() {
  loading.value = true
  error.value = ''
  try { templates.value = await listTemplates() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { loading.value = false }
}

async function selectTemplate(templateId: string) {
  error.value = ''
  resolution.value = null
  replacements.value = {}
  replacementOptions.value = {}
  try {
    selected.value = await getTemplate(templateId)
    for (const slot of selected.value.manifest?.replacement_slots || []) replacements.value[slot.slot_id] = ''
    const options = await getTemplateReplacementOptions(templateId)
    replacementOptions.value = Object.fromEntries(options.slots.map((slot) => [slot.slot_id, slot.candidates]))
    await checkDependencies()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
}

async function checkDependencies() {
  if (!selected.value) return
  try { resolution.value = await resolveTemplateDependencies(selected.value.template_id, replacements.value) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
}

async function instantiate() {
  if (!selected.value) return
  instantiating.value = true
  error.value = ''
  try {
    const parameters = JSON.parse(parametersText.value) as Record<string, unknown>
    const result = await instantiateTemplate(selected.value.template_id, {
      project_name: projectName.value,
      parameters,
      replacements: replacements.value,
    })
    await router.push(`/projects/${result.project_id}/canvas?workflow_id=${result.workflow_id}`)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { instantiating.value = false }
}

onMounted(loadTemplates)
</script>

<template>
  <div class="template-page">
    <header>
      <div>
        <h2>工作流模板</h2>
        <p class="hint">从固定修订创建可编辑的独立工作流。</p>
      </div>
      <div class="header-actions">
        <button class="icon-button" title="刷新模板" aria-label="刷新模板" @click="loadTemplates">↻</button>
      </div>
    </header>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div class="template-layout">
      <section class="template-list" aria-label="模板列表">
        <p v-if="loading" class="hint">加载中...</p>
        <p v-else-if="templates.length === 0" class="hint">暂无可用模板。</p>
        <button v-for="template in templates" :key="template.template_id" class="template-row" :class="{ selected: selected?.template_id === template.template_id }" @click="selectTemplate(template.template_id)">
          <strong>{{ template.name }}</strong>
          <span>{{ template.description || '固定工作流修订' }}</span>
        </button>
      </section>
      <section v-if="selected" class="template-detail">
        <h3>{{ selected.name }}</h3>
        <p>{{ selected.description || '此模板使用固定工作流修订。' }}</p>
        <dl>
          <div><dt>固定修订</dt><dd>{{ selected.workflow_revision_id.slice(0, 8) }}...</dd></div>
          <div><dt>依赖</dt><dd>{{ selected.manifest?.dependencies?.length || 0 }}</dd></div>
        </dl>
        <div v-for="slot in selected.manifest?.replacement_slots || []" :key="slot.slot_id" class="field">
          <label :for="`replacement-${slot.slot_id}`">{{ slot.label }}<span v-if="slot.required"> *</span></label>
          <select :id="`replacement-${slot.slot_id}`" v-model="replacements[slot.slot_id]" @change="checkDependencies">
            <option value="">{{ slot.description || '选择固定修订' }}</option>
            <option v-for="option in replacementOptions[slot.slot_id] || []" :key="option.revision_id" :value="option.revision_id">{{ option.label }} · {{ option.revision_id.slice(0, 8) }}</option>
          </select>
        </div>
        <p v-if="resolution && !resolution.resolved" class="warning">需先解决：{{ [...resolution.missing, ...resolution.unresolved_slots].join('、') }}</p>
        <textarea v-model="parametersText" rows="4" aria-label="模板参数" placeholder="{}" />
        <input v-model="projectName" aria-label="新项目名称" placeholder="新项目名称（可选）" />
        <button class="instantiate-btn" :disabled="instantiating || resolution?.resolved === false" @click="instantiate">
          {{ instantiating ? '创建中...' : '创建项目和工作流' }}
        </button>
      </section>
      <section v-else class="template-detail empty-detail">选择一个模板查看依赖与参数。</section>
    </div>
    <p v-if="projectId" class="context">当前项目上下文：{{ projectId.slice(0, 8) }}...</p>
  </div>
</template>

<style scoped>
.template-page { padding: 20px 0; }
header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 16px; }
.header-actions { display: flex; gap: 8px; }
.seed-benchmarks-btn { padding: 7px 10px; border: 1px solid var(--accent); border-radius: 4px; background: var(--bg-primary); color: var(--accent); cursor: pointer; font-size: 12px; }
h2, h3 { color: var(--text-primary); margin: 0; }
.hint { color: var(--text-secondary); font-size: 14px; }
.template-layout { display: grid; grid-template-columns: minmax(190px, 0.8fr) minmax(280px, 1.2fr); border: 1px solid var(--border); border-radius: 6px; min-height: 300px; }
.template-list { border-right: 1px solid var(--border); padding: 8px; display: flex; flex-direction: column; gap: 4px; }
.template-row { text-align: left; border: 1px solid transparent; border-radius: 4px; background: transparent; color: var(--text-primary); padding: 9px; cursor: pointer; }
.template-row:hover, .template-row.selected { border-color: var(--accent); background: var(--bg-secondary); }
.template-row strong, .template-row span { display: block; }
.template-row span { color: var(--text-secondary); font-size: 12px; margin-top: 3px; }
.template-detail { padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.template-detail p { margin: 0; color: var(--text-secondary); font-size: 13px; }
dl { display: flex; gap: 24px; margin: 2px 0; } dt { color: var(--text-secondary); font-size: 12px; } dd { margin: 2px 0 0; font-family: monospace; font-size: 12px; }
.field { display: grid; gap: 4px; } label { font-size: 13px; color: var(--text-primary); }
input, textarea, select { box-sizing: border-box; width: 100%; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-primary); color: var(--text-primary); padding: 8px; font: inherit; }
textarea { font-family: monospace; font-size: 12px; }
.instantiate-btn { align-self: flex-start; padding: 8px 14px; border: 0; border-radius: 4px; background: var(--accent); color: white; cursor: pointer; }
.instantiate-btn:disabled { opacity: 0.55; cursor: not-allowed; }
.warning, .error-banner { color: #b91c1c !important; background: #fef2f2; padding: 8px; border-radius: 4px; }
.empty-detail { color: var(--text-secondary); justify-content: center; }
.icon-button { border: 1px solid var(--border); border-radius: 4px; background: var(--bg-primary); color: var(--text-primary); width: 32px; height: 32px; cursor: pointer; }
.context { margin-top: 10px; font-size: 12px; color: var(--text-secondary); }
@media (max-width: 700px) { .template-layout { grid-template-columns: 1fr; } .template-list { border-right: 0; border-bottom: 1px solid var(--border); } }
</style>
