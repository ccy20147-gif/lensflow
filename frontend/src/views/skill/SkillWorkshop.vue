<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  createSkill,
  dryRunSkill,
  listSkillRevisions,
  listSkills,
  retireSkillRevision,
  submitSkillRevision,
  updateSkill,
  validateSkillBody,
  type SkillDryRunResult,
  type SkillRecord,
  type SkillRevisionRecord,
} from '@/api/client'

function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error) }
function splitLines(value: string): string[] { return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean) }
function bodyLines(value: unknown): string { return Array.isArray(value) ? value.map(String).join('\n') : '' }

const skills = ref<SkillRecord[]>([])
const selectedSkill = ref<SkillRecord | null>(null)
const revisions = ref<SkillRevisionRecord[]>([])
const name = ref('')
const description = ref('')
const purpose = ref('')
const instructions = ref<string[]>([''])
const examples = ref<string[]>([])
const knowledgeRefs = ref('')
const roles = ref('')
const priority = ref(100)
const maxTokens = ref(4096)
const required = ref(true)
const conflictTags = ref('')
const language = ref('und')
const assemblyTier = ref('explicit')
const saving = ref(false)
const error = ref('')
const status = ref('')
const validation = ref<{ valid: boolean; message?: string } | null>(null)
const preview = ref<SkillDryRunResult | null>(null)

const draftBody = computed<Record<string, unknown>>(() => ({
  purpose: purpose.value.trim(),
  instructions: instructions.value.map((value) => value.trim()).filter(Boolean),
  examples: examples.value.map((value) => value.trim()).filter(Boolean),
  knowledge_refs: splitLines(knowledgeRefs.value).map((artifact_version_id) => ({ artifact_version_id })),
  applicable_agent_roles: splitLines(roles.value),
  priority: Number(priority.value),
  conflict_tags: splitLines(conflictTags.value),
  max_assembly_tokens: Number(maxTokens.value),
  assembly_policy: { required: required.value, max_tokens: Number(maxTokens.value) },
  language: language.value.trim() || 'und',
  assembly_tier: assemblyTier.value,
}))

onMounted(loadSkills)
async function loadSkills() {
  try { skills.value = await listSkills() } catch (caught) { error.value = errorMessage(caught) }
}
function applyBody(value: Record<string, unknown>) {
  purpose.value = String(value.purpose ?? '')
  instructions.value = Array.isArray(value.instructions) && value.instructions.length ? value.instructions.map(String) : ['']
  examples.value = Array.isArray(value.examples) ? value.examples.map(String) : []
  knowledgeRefs.value = Array.isArray(value.knowledge_refs) ? value.knowledge_refs.map((ref) => typeof ref === 'object' && ref ? String((ref as Record<string, unknown>).artifact_version_id ?? '') : '').filter(Boolean).join('\n') : ''
  roles.value = bodyLines(value.applicable_agent_roles)
  priority.value = Number(value.priority ?? 100)
  maxTokens.value = Number(value.max_assembly_tokens ?? (value.assembly_policy as Record<string, unknown> | undefined)?.max_tokens ?? 4096)
  required.value = Boolean((value.assembly_policy as Record<string, unknown> | undefined)?.required ?? true)
  conflictTags.value = bodyLines(value.conflict_tags)
  language.value = String(value.language ?? 'und')
  assemblyTier.value = String(value.assembly_tier ?? 'explicit')
  preview.value = null; validation.value = null
}
async function selectSkill(skill: SkillRecord) {
  selectedSkill.value = skill; error.value = ''; status.value = ''
  applyBody(skill.body || {})
  try { revisions.value = await listSkillRevisions(skill.skill_id) } catch (caught) { error.value = errorMessage(caught); revisions.value = [] }
}
function add(list: string[]) { list.push('') }
function remove(list: string[], index: number) { list.splice(index, 1) }
async function create() {
  if (!name.value.trim()) return
  saving.value = true; error.value = ''; status.value = ''
  try {
    const created = await createSkill({ name: name.value.trim(), description: description.value.trim(), body: draftBody.value })
    name.value = ''; description.value = ''; await loadSkills(); await selectSkill(created)
    status.value = 'Skill 草稿已创建。保存后可发布不可变修订。'
  } catch (caught) { error.value = errorMessage(caught) } finally { saving.value = false }
}
async function saveDraft() {
  if (!selectedSkill.value) return
  saving.value = true; error.value = ''; status.value = ''
  try {
    const saved = await updateSkill(selectedSkill.value.skill_id, { body: draftBody.value, base_hash: selectedSkill.value.content_hash })
    selectedSkill.value = saved; await loadSkills(); status.value = '草稿已通过 CAS 保存。'
  } catch (caught) { error.value = errorMessage(caught) } finally { saving.value = false }
}
async function validate() {
  try { validation.value = await validateSkillBody(draftBody.value); preview.value = null } catch (caught) { validation.value = { valid: false, message: errorMessage(caught) } }
}
async function previewAssembly() {
  try { preview.value = await dryRunSkill(draftBody.value); validation.value = { valid: true } } catch (caught) { preview.value = null; validation.value = { valid: false, message: errorMessage(caught) } }
}
async function publishRevision() {
  if (!selectedSkill.value) return
  saving.value = true; error.value = ''; status.value = ''
  try {
    const saved = await updateSkill(selectedSkill.value.skill_id, { body: draftBody.value, base_hash: selectedSkill.value.content_hash })
    selectedSkill.value = saved
    await submitSkillRevision(selectedSkill.value.skill_id, selectedSkill.value.content_hash)
    await loadSkills()
    await selectSkill(selectedSkill.value)
    status.value = '不可变修订已发布。后续草稿修改不会影响该修订或已装配 Agent。'
  } catch (caught) { error.value = errorMessage(caught) } finally { saving.value = false }
}
async function retireRevision(revision: SkillRevisionRecord) {
  if (!selectedSkill.value) return
  error.value = ''; status.value = ''
  try {
    await retireSkillRevision(selectedSkill.value.skill_id, revision.revision_id)
    await selectSkill(selectedSkill.value)
    status.value = '修订已退役，历史审计记录仍保留。'
  } catch (caught) { error.value = errorMessage(caught) }
}
</script>

<template>
  <main class="skill-page">
    <header><h1>Skill Workshop</h1><p>Skill 是不可执行的版本化指令与知识；不包含工具、网络、URL 或凭证。</p></header>
    <p v-if="error" class="error">{{ error }}</p><p v-if="status" class="status">{{ status }}</p>
    <form class="create" @submit.prevent="create"><input v-model="name" placeholder="Skill 名称" required><input v-model="description" placeholder="描述"><button :disabled="saving">{{ saving ? '保存中...' : '创建 Skill 草稿' }}</button></form>
    <section class="skill-list" aria-label="Skill 列表"><button v-for="skill in skills" :key="skill.skill_id" type="button" class="skill-card" :class="{ selected: selectedSkill?.skill_id === skill.skill_id }" @click="selectSkill(skill)"><strong>{{ skill.name }}</strong><span>{{ skill.status }}</span><code>{{ skill.content_hash.slice(0, 12) }}</code></button><p v-if="!skills.length">尚无 Skill 草稿。</p></section>
    <section class="workshop">
      <div class="editor-heading"><div><h2>{{ selectedSkill?.name || '新 Skill 草稿' }}</h2><small>{{ selectedSkill ? `草稿 ${selectedSkill.content_hash.slice(0, 12)} · 发布会冻结当前内容。` : '先填写非执行性内容，再创建首个草稿。' }}</small></div><span>非执行性内容编辑器</span></div>
      <section class="editor"><fieldset><legend>概览与适用范围</legend><label>用途<input v-model="purpose" placeholder="例如：长篇小说世界观一致性检查"></label><label>适用 Agent 角色（逗号分隔）<input v-model="roles" placeholder="writer, reviewer"></label><label>语言<input v-model="language" placeholder="und"></label><label>装配层级<select v-model="assemblyTier" aria-label="装配层级"><option value="platform">平台</option><option value="managed">受管</option><option value="step">步骤</option><option value="explicit">显式</option></select></label></fieldset><fieldset><legend>指令</legend><div v-for="(_item,index) in instructions" :key="index" class="line"><input v-model="instructions[index]" placeholder="一条不可执行的写作/审核指令"><button type="button" :disabled="instructions.length === 1" title="删除指令" @click="remove(instructions,index)">×</button></div><button type="button" @click="add(instructions)">添加指令</button></fieldset><fieldset><legend>示例</legend><div v-for="(_item,index) in examples" :key="index" class="line"><textarea v-model="examples[index]" rows="2" placeholder="示例（不包含私密数据）"></textarea><button type="button" title="删除示例" @click="remove(examples,index)">×</button></div><button type="button" @click="add(examples)">添加示例</button></fieldset><fieldset><legend>只读知识</legend><label>ArtifactVersion IDs（每行一个，同 owner 的固定版本）<textarea v-model="knowledgeRefs" rows="3" placeholder="ArtifactVersion UUID"></textarea></label></fieldset><fieldset><legend>预算与冲突</legend><div class="grid"><label>优先级<input v-model.number="priority" type="number" min="0"></label><label>最大装配 token<input v-model.number="maxTokens" type="number" min="1"></label></div><label>冲突标签（逗号分隔；!格式表示相反规则）<input v-model="conflictTags"></label><label class="check"><input v-model="required" type="checkbox"> 必需 Skill：超预算/撤权时阻断编译</label></fieldset></section>
      <div class="actions"><button type="button" @click="validate">安全校验</button><button type="button" @click="previewAssembly">预览装配</button><button type="button" :disabled="saving || !selectedSkill" @click="saveDraft">保存草稿</button><button type="button" class="primary" :disabled="saving || !selectedSkill" @click="publishRevision">发布不可变修订</button></div>
      <p v-if="validation" :class="validation.valid ? 'result' : 'error'">{{ validation.valid ? '校验通过：符合非执行性 Skill 边界。' : validation.message }}</p>
      <section v-if="preview" class="preview" aria-label="装配预览"><h3>静态装配预览</h3><p>{{ preview.token_accounting.total_estimated_tokens }} / {{ preview.token_accounting.max_tokens }} tokens · {{ preview.resolved_sections.length }} 个上下文段</p><p>安全决策：{{ preview.security_decisions.join('、') }}</p><p v-if="preview.conflicts.length">冲突：{{ preview.conflicts.join('；') }}</p><p v-else>当前单 Skill 无冲突；多 Skill 的角色、schema 与预算冲突在 Agent 装配时由服务端阻断。</p></section>
      <section v-if="selectedSkill" class="versions" aria-label="不可变修订历史"><h3>不可变修订历史</h3><p v-if="!revisions.length">尚未发布修订。</p><article v-for="revision in revisions" :key="revision.revision_id" class="revision"><div><strong>r{{ revision.revision_number }}</strong><span>{{ revision.status }}</span><code>{{ revision.content_hash.slice(0, 12) }}</code><small>{{ revision.created_at }}</small></div><button v-if="revision.status === 'active'" type="button" @click="retireRevision(revision)">退役</button></article></section>
    </section>
  </main>
</template>

<style scoped>
.skill-page{max-width:1100px;margin:auto;padding:24px;color:var(--text-primary)}header p,small{color:var(--text-secondary)}.create,.actions,.grid,.line,.skill-list{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.create{margin:18px 0}.create input{flex:1;min-width:180px}.skill-list{align-items:stretch;margin-bottom:18px}.skill-card{display:grid;min-width:160px;text-align:left;gap:3px}.skill-card.selected{border-color:var(--accent);box-shadow:inset 3px 0 var(--accent)}.workshop{border:1px solid var(--border);border-radius:6px;padding:16px;background:var(--bg-secondary)}.editor-heading{display:flex;justify-content:space-between;gap:12px}.editor-heading h2,.preview h3,.versions h3{margin:0}.editor{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}fieldset{min-width:0;border:1px solid var(--border);border-radius:5px;padding:12px}label{display:block;font-size:13px;margin-bottom:8px}.line{margin-bottom:7px}.line input,.line textarea{flex:1}.check{display:flex;gap:8px;align-items:center}.check input{width:auto}.actions{margin-top:12px}.primary,.create button{background:var(--accent);color:#fff}.result,.error,.status,.preview{margin-top:12px;padding:9px;border-radius:4px;background:var(--bg-primary)}.error{color:#b91c1c;background:#fef2f2}.status{color:#166534;background:#f0fdf4}.preview p{margin:6px 0}.versions{border-top:1px solid var(--border);margin-top:16px;padding-top:14px}.revision{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)}.revision div{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.revision span{color:var(--text-secondary)}input,textarea,select,button{box-sizing:border-box;font:inherit}input,textarea,select{width:100%;padding:8px;color:var(--text-primary);background:var(--bg-primary);border:1px solid var(--border);border-radius:4px}button{padding:7px 12px;border:1px solid var(--border);border-radius:4px;background:var(--bg-primary);color:var(--text-primary);cursor:pointer}button:disabled{opacity:.55;cursor:not-allowed}@media(max-width:680px){.editor{grid-template-columns:1fr}.create{align-items:stretch}.editor-heading,.revision{flex-direction:column}}
</style>
