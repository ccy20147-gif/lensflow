<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiGet, apiPost, apiPut } from '@/api/client'

interface Artifact { artifact_version_id: string; schema_id: string; schema_version: number; content_hash?: string; content_uri?: string }
interface Resource { resource_id: string; resource_type: string; revision_count: number; active_revision_id?: string; draft: { draft_version: number; content_artifact_version_id: string } }

const artifacts = ref<Artifact[]>([])
const resources = ref<Resource[]>([])
const schemaId = ref('toonflow.world.v1')
const contentText = ref('{"title":"已确认工作台结果"}')
const resourceType = ref('world')
const provenance = ref<any>(null)
const selected = ref<Resource | null>(null)
const draftArtifactId = ref('')
const revisions = ref<any[]>([])
const revisionDiff = ref<any>(null)
const error = ref('')
const busy = ref(false)
const resourceTypes = ['world', 'character', 'shot_plan', 'shot_spec', 'creative_work', 'agent', 'recipe', 'generic']

async function load() {
  try {
    const [artifactRows, resourceRows] = await Promise.all([apiGet<Artifact[]>('/artifacts/versions'), apiGet<Resource[]>('/artifacts/resources')])
    artifacts.value = artifactRows
    resources.value = resourceRows
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
}

async function createArtifact() {
  error.value = ''
  busy.value = true
  try {
    await apiPost('/artifacts/versions', { schema_id: schemaId.value, schema_version: 1, content_json: JSON.parse(contentText.value) })
    await load()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { busy.value = false }
}

async function promote(artifact: Artifact) {
  error.value = ''
  busy.value = true
  try {
    await apiPost('/artifacts/resources', { resource_type: resourceType.value, content_artifact_version_id: artifact.artifact_version_id })
    await load()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { busy.value = false }
}

async function inspect(resource: Resource) {
  error.value = ''
  try { provenance.value = await apiGet(`/artifacts/resources/${resource.resource_id}/provenance`) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
}

async function editDraft(resource: Resource) {
  selected.value = resource; draftArtifactId.value = resource.draft.content_artifact_version_id; revisionDiff.value = null
  try { revisions.value = await apiGet<any[]>(`/artifacts/resources/${resource.resource_id}/revisions`) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
}

async function saveDraft() {
  if (!selected.value || !draftArtifactId.value) return
  busy.value = true
  try { await apiPut(`/artifacts/resources/${selected.value.resource_id}/draft`, { content_artifact_version_id: draftArtifactId.value, base_draft_version: selected.value.draft.draft_version }); await load() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { busy.value = false }
}

async function diffRevisions(left: string, right: string) {
  if (!selected.value) return
  try { revisionDiff.value = await apiGet(`/artifacts/resources/${selected.value.resource_id}/revisions/${left}/diff/${right}`) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
}

async function freeze(resource: Resource) {
  error.value = ''
  busy.value = true
  try {
    await apiPost(`/artifacts/resources/${resource.resource_id}/revisions`, { base_draft_version: resource.draft.draft_version })
    await load()
    await inspect(resources.value.find((row) => row.resource_id === resource.resource_id) || resource)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { busy.value = false }
}

async function rebuild() {
  error.value = ''
  try {
    const result: any = await apiPost('/artifacts/resources/rebuild-projection')
    // Rendering the returned canonical projection makes the recovery action
    // visible without creating a second persisted "latest" store.
    provenance.value = { rebuilt: true, ...result }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
}

onMounted(load)
</script>

<template>
  <div class="resource-page">
    <header class="page-header"><div><h1>资源库</h1><p class="hint">Artifact 是不可变内容；Resource Draft 可编辑，运行只使用固定 Revision。</p></div><button class="secondary" @click="rebuild">从 canonical 重建视图</button></header>
    <form class="artifact-create" @submit.prevent="createArtifact">
      <label>Schema <input v-model="schemaId" aria-label="Artifact Schema" /></label>
      <label>JSON <textarea v-model="contentText" aria-label="Artifact JSON" rows="2" /></label>
      <button type="submit" :disabled="busy">创建 Artifact</button>
    </form>
    <p v-if="error" class="error">{{ error }}</p>

    <section class="library-section"><h2>Artifact 版本</h2><p class="hint">提升后形成 Resource Draft；原 Artifact 永不改写。</p>
      <div v-if="artifacts.length === 0" class="empty">暂无 Artifact。</div>
      <article v-for="artifact in artifacts" :key="artifact.artifact_version_id" class="resource-card artifact-card">
        <div><b>{{ artifact.schema_id }} v{{ artifact.schema_version }}</b><code>{{ artifact.artifact_version_id }}</code></div>
        <div class="promote-controls"><select v-model="resourceType" aria-label="资源类型"><option v-for="type in resourceTypes" :key="type" :value="type">{{ type }}</option></select><button :disabled="busy" @click="promote(artifact)">提升为资源</button></div>
      </article>
    </section>

    <section class="library-section"><h2>Resource 身份与版本</h2>
      <div v-if="resources.length === 0" class="empty">尚未提升资源。</div>
      <article v-for="resource in resources" :key="resource.resource_id" class="resource-card">
        <div><b>{{ resource.resource_type }}</b><code>{{ resource.resource_id }}</code><small>Draft v{{ resource.draft.draft_version }} · {{ resource.revision_count }} 个冻结版本</small></div>
        <div class="resource-actions"><button @click="inspect(resource)">查看 lineage</button><button @click="editDraft(resource)">编辑 Draft</button><button :disabled="busy" @click="freeze(resource)">冻结 Draft</button></div>
      </article>
    </section>
    <section v-if="selected" class="provenance" aria-label="Resource Draft 编辑器"><h2>Draft v{{ selected.draft.draft_version }}</h2><select v-model="draftArtifactId" aria-label="Draft ArtifactVersion"><option v-for="artifact in artifacts" :key="artifact.artifact_version_id" :value="artifact.artifact_version_id">{{ artifact.schema_id }} · {{ artifact.artifact_version_id.slice(0, 8) }}</option></select><button :disabled="busy" @click="saveDraft">保存 Draft (CAS)</button><div v-if="revisions.length > 1"><button v-for="revision in revisions.slice(1)" :key="revision.revision_id" @click="diffRevisions(revisions[0].revision_id, revision.revision_id)">比较 Revision</button></div><pre v-if="revisionDiff" aria-label="Resource Revision 差异">{{ JSON.stringify(revisionDiff, null, 2) }}</pre></section>
    <section v-if="provenance" class="provenance" aria-live="polite"><h2>{{ provenance.rebuilt ? 'Canonical 重建结果' : '固定版本 lineage' }}</h2><pre>{{ JSON.stringify(provenance, null, 2) }}</pre></section>
  </div>
</template>

<style scoped>
.resource-page { max-width: 980px; margin: 0 auto; padding: 24px; }.page-header { display:flex; justify-content:space-between; gap:16px; align-items:start; }h1,h2 { color: var(--text-primary); }h2 { font-size:16px; margin:0 0 5px; }.hint,.empty,small { color: var(--text-secondary); font-size:13px; }.library-section { margin-top:28px; }.artifact-create { display:grid; grid-template-columns:1fr 2fr auto; gap:8px; align-items:end; margin:14px 0; }.artifact-create label { display:grid; gap:4px; font-size:12px; color:var(--text-secondary); }.artifact-create input,.artifact-create textarea,select { box-sizing:border-box; width:100%; padding:6px; border:1px solid var(--border); background:var(--bg-primary); color:var(--text-primary); }.resource-card { padding:10px 12px; border:1px solid var(--border); border-radius:6px; margin:6px 0; display:flex; gap:12px; justify-content:space-between; align-items:center; }.resource-card div:first-child { display:grid; gap:3px; }.resource-card code { font-size:11px; color:var(--text-secondary); }.promote-controls,.resource-actions { display:flex; gap:6px; align-items:center; }.promote-controls select { width:120px; }button { padding:7px 10px; border:0; background:var(--accent); color:white; cursor:pointer; }button.secondary,.resource-actions button:first-child { background:var(--bg-primary); color:var(--text-primary); border:1px solid var(--border); }button:disabled { opacity:.55; cursor:not-allowed; }.error { color:#b91c1c; font-size:12px; }.provenance { margin-top:28px; border-top:1px solid var(--border); padding-top:14px; }.provenance pre { overflow:auto; max-height:340px; padding:12px; background:var(--bg-secondary); color:var(--text-primary); font-size:11px; }@media (max-width:700px) { .page-header,.resource-card { flex-direction:column; }.artifact-create { grid-template-columns:1fr; }.promote-controls,.resource-actions { width:100%; } }
</style>
