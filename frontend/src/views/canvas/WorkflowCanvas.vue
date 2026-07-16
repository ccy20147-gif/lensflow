<script setup lang="ts">
import { computed, ref, onBeforeUnmount, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { VueFlow, useVueFlow, type Connection, type NodeMouseEvent } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import RegistryNode from './RegistryNode.vue'
import { catalogNodeAvailable, catalogNodeCompatible, draftNodeToCanvasNode, filterCatalog, isUnknownDefinition, layoutPositions, portsCompatible, serializeCanvasNode, type CanvasCatalogNode, type CanvasPortDef } from './canvasRegistry'
import { useProjectStore } from '@/stores/project'
import { apiGet, apiPost, apiPut, getDraft as apiGetDraft, publishWorkflowRevision, startWorkflowRun } from '@/api/client'
import { discardDraft, queueDraft, takeDraft } from './offlineDraftQueue'
import { inspectorPlugin } from './inspectorPlugins'

const route = useRoute()
const store = useProjectStore()
const workflowId = (route.query.workflow_id as string) || ''
const { toObject } = useVueFlow()

type PortDef = CanvasPortDef
type CatalogNode = CanvasCatalogNode

const nodes = ref<any[]>([])
const edges = ref<any[]>([])
const catalogNodes = ref<CatalogNode[]>([])
const selectedNode = ref<any | null>(null)
const currentGraphHash = ref('')
const currentFullDraftHash = ref('')
const draftVersion = ref(0)
const saving = ref(false)
const saveError = ref('')
const compileResult = ref<any>(null)
const loading = ref(true)
const paletteSearch = ref('')
const categoryFilter = ref('all')
const availabilityFilter = ref<'all' | 'available' | 'unavailable'>('all')
const compatibilityFilter = ref<'all' | 'compatible' | 'incompatible'>('all')
const connectionError = ref('')
const runStatus = ref('')
const architectProposalId = ref('')
const architectIntent = ref('')
const architectGenerating = ref(false)
const architectDiff = ref<any>(null)
const architectError = ref('')
const architectApplying = ref(false)
const registryError = ref('')
const networkOffline = ref(false)
const selectedIds = ref<string[]>([])
const clipboard = ref<{ nodes: any[]; edges: any[] } | null>(null)
const history = ref<{ nodes: any[]; edges: any[] }[]>([])
const future = ref<{ nodes: any[]; edges: any[] }[]>([])
const restoringHistory = ref(false)
const nodeTypes = { registry: RegistryNode }
const categories = computed(() => [...new Set(catalogNodes.value.map((node) => node.category).filter(Boolean))])
const selectedDefinition = computed<CatalogNode | undefined>(() => selectedNode.value?.data?.definition)
const selectedInspectorPlugin = computed(() => inspectorPlugin(String(selectedNode.value?.data?.node_type_id || '')))

function isCatalogNodeCompatible(candidate: CatalogNode) {
  return catalogNodeCompatible(selectedDefinition.value, candidate)
}

function isCatalogNodeAvailable(node: CatalogNode) {
  return catalogNodeAvailable(node)
}

function unavailableReason(node: CatalogNode) {
  if (node.available === false) return (node as CatalogNode & { unavailable_reason?: string }).unavailable_reason || '当前账号无权使用此节点。'
  if (node.provider_required && node.execution_available === false) return 'AtlasCloud Provider 尚未配置；节点可编辑但不能执行。'
  return ''
}

const filteredCatalog = computed(() => filterCatalog(catalogNodes.value, {
  search: paletteSearch.value,
  category: categoryFilter.value,
  availability: availabilityFilter.value,
  compatibility: compatibilityFilter.value,
}, selectedDefinition.value))

onMounted(async () => {
  await loadCatalog()
  await loadDraft(workflowId)
  globalThis.addEventListener('keydown', onCanvasKeydown)
  globalThis.addEventListener('offline', onOffline)
  globalThis.addEventListener('online', onOnline)
})

onBeforeUnmount(() => {
  globalThis.removeEventListener('keydown', onCanvasKeydown)
  globalThis.removeEventListener('offline', onOffline)
  globalThis.removeEventListener('online', onOnline)
})

function onOffline() {
  networkOffline.value = true
}

function onOnline() {
  networkOffline.value = false
  // Restore editing only after the authoritative registry has been fetched.
  void loadCatalog()
  void replayOfflineDraft()
}

async function replayOfflineDraft() {
  if (!workflowId || registryError.value) return
  const queued = await takeDraft(workflowId)
  if (!queued) return
  try {
    const result = await apiPut<any>(`/workflows/${workflowId}/draft`, queued.payload)
    currentGraphHash.value = result.graph_hash || currentGraphHash.value
    draftVersion.value = result.draft_version || draftVersion.value
    await discardDraft(workflowId)
    saveError.value = ''
  } catch (error: any) {
    saveError.value = error?.status === 409
      ? '离线草稿与服务器版本冲突：本地修改已保留，请刷新后合并。'
      : '离线草稿仍待同步。'
  }
}

function cloneGraph(value: { nodes: any[]; edges: any[] }) {
  return JSON.parse(JSON.stringify(value)) as { nodes: any[]; edges: any[] }
}

function currentGraph() {
  return { nodes: nodes.value, edges: edges.value }
}

function resetHistory() {
  history.value = [cloneGraph(currentGraph())]
  future.value = []
}

function checkpoint() {
  if (restoringHistory.value) return
  const next = cloneGraph(currentGraph())
  const previous = history.value.at(-1)
  if (previous && JSON.stringify(previous) === JSON.stringify(next)) return
  history.value = [...history.value.slice(-99), next]
  future.value = []
}

function restore(snapshot: { nodes: any[]; edges: any[] }) {
  restoringHistory.value = true
  nodes.value = cloneGraph(snapshot).nodes
  edges.value = cloneGraph(snapshot).edges
  selectedNode.value = null
  selectedIds.value = []
  restoringHistory.value = false
}

function undo() {
  if (history.value.length < 2 || registryError.value) return
  const current = history.value.at(-1)!
  const previous = history.value.at(-2)!
  history.value = history.value.slice(0, -1)
  future.value = [current, ...future.value]
  restore(previous)
}

function redo() {
  const next = future.value[0]
  if (!next || registryError.value) return
  history.value = [...history.value, next]
  future.value = future.value.slice(1)
  restore(next)
}

async function loadCatalog() {
  try {
    const data = await apiGet<any>('/registry/catalog')
    catalogNodes.value = data.node_types || []
    registryError.value = ''
  } catch {
    // A draft remains inspectable when its registry snapshot cannot be read;
    // editing is intentionally disabled because compatibility cannot be judged.
    registryError.value = '节点注册表暂不可用。画布已进入只读模式；现有图数据未被修改。'
  }
}

async function retryRegistry() {
  await loadCatalog()
}

async function loadDraft(wfId: string) {
  if (!wfId) { loading.value = false; return }
  try {
    const draft: any = await store.fetchDraft(wfId)
    const g = draft?.graph || {}
    const positions = layoutPositions(draft?.layout)
    nodes.value = (g.nodes || []).map((n: any) => {
      const definition = catalogNodes.value.find((candidate) => candidate.type_id === (n.data?.node_type_id || n.type))
      return draftNodeToCanvasNode(
        n,
        definition,
        (positions[n.id] as { x: number; y: number }) || n.position || { x: Math.random() * 400, y: Math.random() * 300 },
        String(Math.random()),
      )
    })
    edges.value = (g.edges || []).map((e: any) => ({
      id: e.id || `${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle,
      targetHandle: e.targetHandle,
    }))
    currentGraphHash.value = (draft as any).graph_hash || ''
    currentFullDraftHash.value = (draft as any).full_draft_hash || ''
    draftVersion.value = (draft as any).draft_version || 0
    resetHistory()
  } catch { saveError.value = '无法加载工作流草稿。请检查网络后重试。' }
  loading.value = false
}

function addNodeFromCatalog(cat: CatalogNode) {
  if (registryError.value || !isCatalogNodeAvailable(cat)) return
  const id = `${cat.type_id}-${Date.now()}`
  nodes.value = [...nodes.value, {
    id,
    type: 'registry',
    label: cat.label || cat.type_id,
    position: { x: 100 + Math.random() * 300, y: 100 + Math.random() * 200 },
    data: {
      node_type_id: cat.type_id,
      definition_revision_id: cat.revision_id,
      definition: cat,
      // Dynamic Agent entries include this immutable revision binding.  It is
      // persisted with the node and verified again by the backend compiler.
      config: { ...(cat.config || {}) },
      provider_required: cat.provider_required,
      execution_available: cat.execution_available,
    },
  }]
  checkpoint()
}

function onConnect(conn: Connection) {
  if (registryError.value) return
  connectionError.value = ''
  const source = nodes.value.find((node) => node.id === conn.source)
  const target = nodes.value.find((node) => node.id === conn.target)
  const sourcePort = source?.data?.definition?.output_ports?.find((port: PortDef) => port.port_id === conn.sourceHandle)
  const targetPort = target?.data?.definition?.input_ports?.find((port: PortDef) => port.port_id === conn.targetHandle)
  if (!sourcePort || !targetPort) {
    connectionError.value = '请选择已注册的输入和输出端口。'
    return
  }
  if (!portsCompatible(sourcePort, targetPort)) {
    connectionError.value = `端口不兼容：${sourcePort.schema_id}@${sourcePort.schema_version || '1'} 无法连接到 ${targetPort.schema_id}@${targetPort.schema_version || '1'}。`
    return
  }
  edges.value = [...edges.value, {
    id: `${conn.source}-${conn.target}-${Date.now()}`,
    source: conn.source,
    target: conn.target,
    sourceHandle: conn.sourceHandle || undefined,
    targetHandle: conn.targetHandle || undefined,
  }]
  checkpoint()
}

function updateSelectedConfig(key: string, value: unknown) {
  if (!selectedNode.value || selectedNode.value.data?.unknown_definition || registryError.value) return
  checkpoint()
  selectedNode.value.data.config = { ...(selectedNode.value.data.config || {}), [key]: value }
  checkpoint()
}

function configFieldValue(field: Record<string, any>, key: string) {
  return selectedNode.value?.data?.config?.[key] ?? field.default ?? (field.type === 'boolean' ? false : '')
}

function updateJsonConfig(key: string, raw: string) {
  try {
    updateSelectedConfig(key, raw.trim() ? JSON.parse(raw) : undefined)
  } catch {
    saveError.value = `配置 ${key} 必须是有效 JSON。`
  }
}

function onNodeClick(event: NodeMouseEvent) {
  selectedNode.value = event.node
  selectedIds.value = [event.node.id]
}

function onSelectionChange(event: { nodes?: any[] }) {
  selectedIds.value = (event.nodes || []).map((node) => node.id)
  if (selectedIds.value.length !== 1) selectedNode.value = null
}

function onNodeDragStop() { checkpoint() }

function copySelection() {
  const picked = nodes.value.filter((node) => selectedIds.value.includes(node.id))
  if (!picked.length) return
  const ids = new Set(picked.map((node) => node.id))
  clipboard.value = cloneGraph({ nodes: picked, edges: edges.value.filter((edge) => ids.has(edge.source) && ids.has(edge.target)) })
}

function pasteSelection() {
  if (!clipboard.value || registryError.value) return
  checkpoint()
  const idMap = new Map<string, string>()
  const offset = 32
  const pasted = clipboard.value.nodes.map((node) => {
    const id = `${node.id}-copy-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    idMap.set(node.id, id)
    return { ...node, id, selected: true, position: { x: node.position.x + offset, y: node.position.y + offset } }
  })
  const pastedEdges = clipboard.value.edges.map((edge) => ({ ...edge, id: `${edge.id}-copy-${Date.now()}`, source: idMap.get(edge.source), target: idMap.get(edge.target) }))
  nodes.value = [...nodes.value.map((node) => ({ ...node, selected: false })), ...pasted]
  edges.value = [...edges.value, ...pastedEdges]
  selectedIds.value = pasted.map((node) => node.id)
  checkpoint()
}

function onCanvasKeydown(event: any) {
  const target = event.target as { matches?: (selector: string) => boolean } | null
  if (target && target.matches?.('input, textarea, select, [contenteditable="true"]')) return
  const modifier = event.metaKey || event.ctrlKey
  if (modifier && event.key.toLowerCase() === 'z') {
    event.preventDefault()
    if (event.shiftKey) redo()
    else undo()
    return
  }
  if (modifier && event.key.toLowerCase() === 'y') { event.preventDefault(); redo(); return }
  if (modifier && event.key.toLowerCase() === 'c') { event.preventDefault(); copySelection(); return }
  if (modifier && event.key.toLowerCase() === 'v') { event.preventDefault(); pasteSelection(); return }
  if ((event.key === 'Delete' || event.key === 'Backspace') && selectedIds.value.length) {
    event.preventDefault()
    deleteSelected()
  }
}

async function saveDraft() {
  if (!workflowId || registryError.value) return
  saving.value = true
  saveError.value = ''
  try {
    const graph = toObject()
    // Layout is deliberately excluded from the executable graph.  Moving a
    // node must not change a compiled plan or invalidate a running revision.
    const positions = Object.fromEntries(graph.nodes.map((node: any) => [node.id, node.position]))
    const payload = {
      graph: {
        nodes: graph.nodes.map(serializeCanvasNode),
        edges: graph.edges,
      },
      config: {},
      layout: { nodes: positions },
      base_graph_hash: currentGraphHash.value,
      // Full-draft CAS tokens protect against the layout-only race
      // (two tabs moving the same node would collide on
      // ``currentGraphHash`` and the durable backend would silently
      // accept both saves).
      expected_draft_version: draftVersion.value || undefined,
      expected_full_draft_hash: currentFullDraftHash.value || undefined,
      pinned_dependency_revisions: [],
    }
    if (typeof window !== 'undefined' && !globalThis.navigator.onLine) {
      await queueDraft({ workflowId, payload, queuedAt: Date.now() })
      saveError.value = '网络已断开：草稿已安全保存在本机，恢复网络后将以 CAS 同步。'
      return
    }
    const result = await apiPut<any>(`/workflows/${workflowId}/draft`, payload)
    currentGraphHash.value = result.graph_hash || ''
    currentFullDraftHash.value = result.full_draft_hash || ''
    draftVersion.value = result.draft_version || 0
  } catch (e: any) {
    await queueDraft({ workflowId, payload: { graph: { nodes: nodes.value.map(serializeCanvasNode), edges: edges.value }, config: {}, layout: { nodes: Object.fromEntries(nodes.value.map((node) => [node.id, node.position])) }, base_graph_hash: currentGraphHash.value, expected_draft_version: draftVersion.value || undefined, expected_full_draft_hash: currentFullDraftHash.value || undefined, pinned_dependency_revisions: [] }, queuedAt: Date.now() })
    saveError.value = e?.status === 409
      ? 'CAS 冲突：其他用户已修改此工作流，请刷新页面重试。'
      : e?.message ?? '保存失败'
  } finally {
    saving.value = false
  }
}

async function runCompile() {
  if (!workflowId) return
  try {
    const result = await apiPost<any>(`/workflows/${workflowId}/compile`)
    compileResult.value = result
  } catch (e: any) {
    compileResult.value = { status: 'failed', diagnostics: [{ message: e?.message ?? '编译失败' }] }
  }
}

async function dryRun() {
  if (!workflowId) return
  try {
    const result = await apiPost<any>(`/workflows/${workflowId}/compile/dry-run`)
    compileResult.value = result
  } catch (e: any) {
    compileResult.value = { passes: false, diagnostics: [{ message: e?.message ?? 'dry-run 失败' }] }
  }
}

async function publishAndRun() {
  if (!workflowId) return
  runStatus.value = ''
  try {
    await saveDraft()
    // Re-read the draft to obtain the owner-confirmed full_draft_hash
    // the activation requires.  draft_version is NOT a substitute:
    // two layout-only saves share a version delta of one but
    // produce different full hashes, so the backend must see the
    // exact 64-character token the owner reviewed.
    const confirmed = await apiGetDraft(workflowId)
    if (!confirmed?.full_draft_hash) {
      runStatus.value = '无法读取当前 Draft 的 full_draft_hash；请刷新后重试。'
      return
    }
    const revision = await publishWorkflowRevision(workflowId, {
      expected_full_draft_hash: confirmed.full_draft_hash,
    })
    const run = await startWorkflowRun(revision.revision_id)
    runStatus.value = `已启动运行 ${run.run_id}`
  } catch (e: any) {
    runStatus.value = e?.message ?? '启动运行失败'
  }
}

async function loadArchitectProposal() {
  architectError.value = ''
  architectDiff.value = null
  if (!architectProposalId.value.trim()) return
  try {
    architectDiff.value = await apiGet(`/architect/proposals/${architectProposalId.value.trim()}/diff`)
  } catch (e: any) {
    architectError.value = e?.message ?? '无法读取 Architect 提案'
  }
}

async function generateArchitectProposal() {
  if (!workflowId || !architectIntent.value.trim() || architectGenerating.value) return
  architectGenerating.value = true
  architectError.value = ''
  architectDiff.value = null
  try {
    const proposal: any = await apiPost('/architect/proposals', {
      workflow_id: workflowId,
      // base_draft_hash is the WorkflowDraft full hash the owner
      // reviewed (graph + layout + execution + draft_version).  A pure
      // layout move rotates this value; passing the graph hash would
      // silently let a stale proposal through.
      base_draft_hash: currentFullDraftHash.value,
      intent: architectIntent.value.trim(),
    })
    if (proposal.state === 'unknown') {
      architectError.value = 'Architect 请求正在等待 AtlasCloud 对账，请稍后刷新。'
      return
    }
    architectProposalId.value = proposal.proposal_id
    architectDiff.value = await apiGet(`/architect/proposals/${proposal.proposal_id}/diff`)
  } catch (e: any) {
    architectError.value = e?.message ?? 'Architect 提案生成失败'
  } finally {
    architectGenerating.value = false
  }
}

async function applyArchitectProposal() {
  if (!architectDiff.value || architectApplying.value) return
  architectApplying.value = true
  architectError.value = ''
  try {
    const result: any = await apiPost(`/architect/proposals/${architectDiff.value.proposal_id}/apply`, {
      // Confirmation carries the same full hash the owner reviewed at
      // generate-time.  A concurrent layout edit on another tab rotates
      // this value; the platform refuses the apply with 409.
      base_draft_hash: currentFullDraftHash.value,
      validated_plan_hash: architectDiff.value.validation?.validated_plan_hash,
      idempotency_key: `architect-apply:${architectDiff.value.proposal_id}:${architectDiff.value.validation?.validated_plan_hash}`,
    })
    architectDiff.value = result
    await loadDraft(workflowId)
  } catch (e: any) {
    architectError.value = e?.status === 409
      ? '提案已过期：草稿、权限或校验结果发生变化，请重新生成提案。'
      : e?.message ?? '应用提案失败'
  } finally {
    architectApplying.value = false
  }
}

function deleteNode(id: string) {
  const node = nodes.value.find((candidate) => candidate.id === id)
  if (registryError.value || (node && isUnknownDefinition(node))) return
  checkpoint()
  nodes.value = nodes.value.filter((node) => node.id !== id)
  edges.value = edges.value.filter((edge) => edge.source !== id && edge.target !== id)
  selectedNode.value = null
  selectedIds.value = []
  checkpoint()
}

function deleteSelected() {
  if (!selectedIds.value.length || registryError.value) return
  const mutableIds = new Set(nodes.value.filter((node) => selectedIds.value.includes(node.id) && !isUnknownDefinition(node)).map((node) => node.id))
  if (!mutableIds.size) return
  checkpoint()
  nodes.value = nodes.value.filter((node) => !mutableIds.has(node.id))
  edges.value = edges.value.filter((edge) => !mutableIds.has(edge.source) && !mutableIds.has(edge.target))
  selectedNode.value = null
  selectedIds.value = []
  checkpoint()
}
</script>

<template>
  <div class="canvas-page" data-testid="workflow-canvas" :aria-busy="loading">
    <div class="canvas-toolbar">
      <span class="canvas-title">工作流 {{ workflowId?.slice(0, 8) }}...</span>
      <span class="draft-info">v{{ draftVersion }} | hash: {{ currentGraphHash?.slice(0, 8) }}...</span>
      <div class="toolbar-actions">
        <button title="撤销 (Ctrl/Cmd+Z)" aria-label="撤销" :disabled="history.length < 2 || !!registryError" @click="undo">↶</button>
        <button title="重做 (Ctrl/Cmd+Shift+Z)" aria-label="重做" :disabled="future.length === 0 || !!registryError" @click="redo">↷</button>
        <button title="复制选中节点 (Ctrl/Cmd+C)" aria-label="复制选中节点" :disabled="selectedIds.length === 0 || !!registryError" @click="copySelection">⧉</button>
        <button title="粘贴节点 (Ctrl/Cmd+V)" aria-label="粘贴节点" :disabled="!clipboard || !!registryError" @click="pasteSelection">⌁</button>
        <button :disabled="saving || !workflowId || !!registryError" @click="saveDraft">{{ saving ? '保存中...' : '保存' }}</button>
        <button data-testid="workflow-dry-run" @click="dryRun">dry-run</button>
        <button data-testid="workflow-compile" @click="runCompile">编译</button>
        <button class="run-workflow-btn" :disabled="saving || !workflowId" @click="publishAndRun">发布并运行</button>
      </div>
    </div>

    <div v-if="saveError" class="error-banner">{{ saveError }}</div>
    <div v-if="registryError" class="degraded-banner" role="status">
      {{ registryError }}
      <button class="inline-action" @click="retryRegistry">重新连接注册表</button>
    </div>
    <div v-if="networkOffline" class="degraded-banner" role="status">网络已断开。未保存的画布修改仍保留在此页面；恢复网络后会重新加载注册表并用当前 base hash 保存。</div>
    <div v-if="connectionError" class="error-banner">{{ connectionError }}</div>
    <div v-if="runStatus" class="run-status">{{ runStatus }}</div>

    <div class="canvas-layout">
      <aside class="node-palette">
        <h4>节点目录</h4>
        <input v-model="paletteSearch" class="palette-search" placeholder="搜索节点" aria-label="搜索节点" />
        <select v-model="categoryFilter" class="palette-filter" aria-label="筛选分类">
          <option value="all">全部分类</option>
          <option v-for="category in categories" :key="category" :value="category">{{ category }}</option>
        </select>
        <select v-model="availabilityFilter" class="palette-filter" aria-label="筛选节点权限">
          <option value="all">所有权限状态</option>
          <option value="available">可添加</option>
          <option value="unavailable">无权使用</option>
        </select>
        <select v-model="compatibilityFilter" class="palette-filter" aria-label="筛选端口兼容性">
          <option value="all">所有端口状态</option>
          <option value="compatible">与所选节点兼容</option>
          <option value="incompatible">与所选节点不兼容</option>
        </select>
        <div v-if="filteredCatalog.length === 0" class="empty-palette">（无可用节点）</div>
        <button v-for="cat in filteredCatalog" :key="cat.type_id" type="button" class="palette-item" :class="{ unavailable: !isCatalogNodeAvailable(cat), incompatible: !isCatalogNodeCompatible(cat) }" :title="unavailableReason(cat) || (!isCatalogNodeCompatible(cat) ? '与当前选择节点没有可兼容端口。' : '')" :disabled="!!registryError || !isCatalogNodeAvailable(cat)" @click="addNodeFromCatalog(cat)">
          <span class="node-type"><b>{{ cat.label || cat.type_id }}</b><small>{{ cat.type_id }}</small></span>
          <span v-if="!isCatalogNodeAvailable(cat)" class="provider-tag unavailable">{{ unavailableReason(cat) }}</span>
          <span v-else-if="!isCatalogNodeCompatible(cat)" class="provider-tag unavailable">端口不兼容</span>
          <span v-if="cat.provider_required" class="provider-tag" :class="{ unavailable: cat.execution_available === false }">
            {{ cat.execution_available === false ? 'Provider 未配置' : '需 Provider' }}
          </span>
        </button>
      </aside>

      <div class="canvas-area">
        <div v-if="loading" class="loading">加载画布...</div>
        <VueFlow
          v-else
          v-model:nodes="nodes"
          v-model:edges="edges"
          class="flow-canvas"
          :node-types="nodeTypes"
          :nodes-draggable="!registryError"
          :nodes-connectable="!registryError"
          @connect="onConnect"
          @node-click="onNodeClick"
          @node-drag-stop="onNodeDragStop"
          @selection-change="onSelectionChange"
        >
          <Background />
          <Controls />
          <MiniMap />
        </VueFlow>
      </div>

      <aside class="right-panel">
        <div v-if="selectedNode" class="node-detail">
          <h4>{{ selectedNode.label }}</h4>
          <p class="node-id">id: {{ selectedNode.id }}</p>
          <p v-if="selectedNode.data?.unknown_definition" class="provider-warn">该节点定义已退役或不可用。它以只读占位保留，保存时会保留原始图 JSON。</p>
          <p v-if="selectedNode.data?.provider_required && selectedNode.data?.execution_available === false" class="provider-warn">
            此节点需要 AtlasCloud 凭证；当前环境尚未配置。
          </p>
          <p v-if="selectedInspectorPlugin" class="hint">已加载专用编辑器：{{ selectedInspectorPlugin.component }}</p>
          <div v-for="(field, key) in selectedNode.data?.definition?.config_schema?.properties || {}" :key="String(key)" class="config-field">
            <label :for="`config-${key}`">{{ field.title || key }}</label>
            <select v-if="field.enum" :id="`config-${key}`" :value="configFieldValue(field, String(key))" :disabled="!!registryError || !!selectedNode.data?.unknown_definition" @change="updateSelectedConfig(String(key), ($event.target as HTMLSelectElement).value)">
              <option v-for="choice in field.enum" :key="String(choice)" :value="choice">{{ choice }}</option>
            </select>
            <input v-else-if="field.type === 'boolean'" :id="`config-${key}`" type="checkbox" :checked="Boolean(configFieldValue(field, String(key)))" :disabled="!!registryError || !!selectedNode.data?.unknown_definition" @change="updateSelectedConfig(String(key), ($event.target as HTMLInputElement).checked)" />
            <input v-else-if="field.type === 'number' || field.type === 'integer'" :id="`config-${key}`" type="number" :value="configFieldValue(field, String(key))" :disabled="!!registryError || !!selectedNode.data?.unknown_definition" @input="updateSelectedConfig(String(key), Number(($event.target as HTMLInputElement).value))" />
            <textarea v-else-if="field.type === 'array' || field.type === 'object'" :id="`config-${key}`" :value="JSON.stringify(configFieldValue(field, String(key)), null, 2)" :disabled="!!registryError || !!selectedNode.data?.unknown_definition" @change="updateJsonConfig(String(key), ($event.target as HTMLTextAreaElement).value)" />
            <input v-else :id="`config-${key}`" :value="configFieldValue(field, String(key))" :disabled="!!registryError || !!selectedNode.data?.unknown_definition" @input="updateSelectedConfig(String(key), ($event.target as HTMLInputElement).value)" />
          </div>
          <button class="danger-btn" :disabled="!!registryError || !!selectedNode.data?.unknown_definition" @click="deleteNode(selectedNode.id)">删除节点</button>
        </div>
        <div v-else class="no-selection">选择节点</div>

        <div v-if="compileResult" class="compile-panel" data-testid="compile-result" role="status" aria-live="polite">
          <h4>{{ compileResult.status === 'compiled' || compileResult.passes === true ? '编译通过' : '编译诊断' }}</h4>
          <div v-if="compileResult.diagnostics?.length">
            <div v-for="(d, i) in compileResult.diagnostics" :key="i" class="diag" :class="d.severity">
              {{ d.message }}
            </div>
          </div>
          <div v-else>无诊断信息。</div>
        </div>
        <div class="architect-panel">
          <h4>Architect 提案</h4>
          <p>提案由受控 Agent 生成；确认前不会写入画布。</p>
          <textarea v-model="architectIntent" aria-label="Architect 创作意图" placeholder="描述想要调整的工作流" rows="3" />
          <button :disabled="architectGenerating || !architectIntent.trim()" @click="generateArchitectProposal">{{ architectGenerating ? '正在生成...' : '生成提案' }}</button>
          <details><summary>打开已有提案</summary><input v-model="architectProposalId" placeholder="WorkflowChangeProposal ID" aria-label="Architect proposal ID" /><button @click="loadArchitectProposal">查看差异</button></details>
          <p v-if="architectError" class="architect-error">{{ architectError }}</p>
          <template v-if="architectDiff">
            <p class="proposal-meta">基于 {{ architectDiff.base_draft_hash?.slice(0, 10) }} · {{ architectDiff.operations?.length || 0 }} 项操作</p>
            <ul class="proposal-ops"><li v-for="(op, i) in architectDiff.operations" :key="i"><code>{{ op.op }}</code> {{ op.node_id || op.node?.id || op.edge?.id || '' }}</li></ul>
            <div v-if="architectDiff.validation?.schema_errors?.length" class="architect-error">{{ architectDiff.validation.schema_errors.join('；') }}</div>
            <div v-if="architectDiff.validation" class="proposal-validation" aria-label="Architect confirmation checks">
              <p>预估成本：{{ architectDiff.validation.cost_estimate?.amount ?? 0 }} {{ architectDiff.validation.cost_estimate?.currency ?? 'credits' }}<span v-if="architectDiff.validation.cost_estimate?.budget_limit != null"> / 上限 {{ architectDiff.validation.cost_estimate.budget_limit }}</span></p>
              <p>权限检查：{{ architectDiff.validation.entitlement_errors?.length ? architectDiff.validation.entitlement_errors.join('；') : '当前无权限缺口' }}</p>
              <p v-if="architectDiff.validation.material_gate_errors?.length" class="architect-error">素材 Gate：{{ architectDiff.validation.material_gate_errors.join('；') }}</p>
              <p v-if="architectDiff.validation.irreversible_impacts?.length">不可逆影响：{{ architectDiff.validation.irreversible_impacts.map((item: any) => item.operation).join('、') }}</p>
            </div>
            <button class="apply-proposal" :disabled="architectApplying || architectDiff.validation?.state !== 'valid' || architectDiff.state === 'applied'" @click="applyArchitectProposal">{{ architectDiff.state === 'applied' ? '已应用' : architectApplying ? '应用中...' : '确认并原子应用' }}</button>
          </template>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.canvas-page { height: calc(100vh - 48px); display: flex; flex-direction: column; }
.canvas-toolbar {
  display: flex; align-items: center; gap: 12px; padding: 8px 16px;
  background: var(--bg-secondary); border-bottom: 1px solid var(--border);
}
.canvas-title { font-weight: 600; color: var(--text-primary); }
.draft-info { font-size: 12px; color: var(--text-secondary); font-family: monospace; }
.toolbar-actions { margin-left: auto; display: flex; gap: 6px; }
.toolbar-actions button {
  padding: 5px 12px; border: 1px solid var(--border); border-radius: 4px;
  background: var(--bg-primary); color: var(--text-primary); cursor: pointer; font-size: 13px;
}
.toolbar-actions button:hover { background: var(--bg-secondary); }
.error-banner { background: #fef2f2; color: #b91c1c; padding: 8px 16px; font-size: 13px; }
.degraded-banner { background: #fffbeb; color: #92400e; padding: 8px 16px; font-size: 13px; }
.inline-action { margin-left: 8px; border: 1px solid currentColor; background: transparent; color: inherit; cursor: pointer; padding: 2px 6px; }
.run-status { background: #eff6ff; color: #1d4ed8; padding: 8px 16px; font-size: 13px; }
.run-workflow-btn { border-color: var(--accent) !important; color: var(--accent) !important; }
.canvas-layout { display: flex; flex: 1; overflow: hidden; }
.node-palette {
  width: 200px; background: var(--bg-secondary); border-right: 1px solid var(--border);
  padding: 12px; overflow-y: auto; flex-shrink: 0;
}
.node-palette h4 { margin: 0 0 8px; color: var(--text-secondary); font-size: 13px; }
.empty-palette { font-size: 12px; color: var(--text-secondary); }
.palette-item {
  padding: 6px 8px; border-radius: 4px; cursor: pointer; margin-bottom: 4px;
  border: 1px solid var(--border); display: flex; justify-content: space-between;
  font-size: 12px;
}
.palette-search, .palette-filter { width: 100%; box-sizing: border-box; margin-bottom: 8px; padding: 6px; border: 1px solid var(--border); background: var(--bg-primary); color: var(--text-primary); font-size: 12px; }
.palette-item:hover { border-color: var(--accent); }
.palette-item.unavailable { cursor: not-allowed; opacity: .62; }
.palette-item.incompatible { border-style: dashed; }
.node-type { color: var(--text-primary); display: grid; gap: 2px; text-align: left; }
.node-type small { color: var(--text-secondary); font-family: monospace; font-size: 10px; overflow: hidden; text-overflow: ellipsis; }
.provider-tag { color: #dc2626; font-size: 10px; }
.provider-tag.unavailable { font-weight: 600; }
.canvas-area { flex: 1; min-height: 0; }
.flow-canvas { width: 100%; height: 100%; }
.loading { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--text-secondary); }
.right-panel {
  width: 260px; background: var(--bg-secondary); border-left: 1px solid var(--border);
  padding: 12px; overflow-y: auto; flex-shrink: 0;
}
.node-detail h4 { margin: 0 0 4px; color: var(--text-primary); font-size: 14px; }
.node-id { font-size: 11px; color: var(--text-secondary); font-family: monospace; }
.provider-warn { font-size: 12px; color: #dc2626; margin: 8px 0; }
.config-field { display: grid; gap: 4px; margin: 8px 0; font-size: 12px; }
.config-field input, .config-field select, .config-field textarea { min-width: 0; padding: 5px; border: 1px solid var(--border); background: var(--bg-primary); color: var(--text-primary); }
.config-field textarea { min-height: 72px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; }
.danger-btn {
  padding: 4px 10px; border: 1px solid #dc2626; color: #dc2626; border-radius: 4px;
  background: none; cursor: pointer; font-size: 12px; margin-top: 8px;
}
.no-selection { color: var(--text-secondary); font-size: 13px; }
.compile-panel { margin-top: 16px; border-top: 1px solid var(--border); padding-top: 12px; }
.compile-panel h4 { margin: 0 0 8px; font-size: 13px; color: var(--text-primary); }
.diag { font-size: 12px; padding: 4px; margin-bottom: 4px; border-radius: 3px; }
.diag.error { background: #fef2f2; color: #b91c1c; }
.diag.warning { background: #fffbeb; color: #92400e; }
.diag.info { background: #eff6ff; color: #1e40af; }
.architect-panel { margin-top: 16px; border-top: 1px solid var(--border); padding-top: 12px; }
.architect-panel h4 { margin: 0 0 6px; font-size: 13px; }
.architect-panel p { color: var(--text-secondary); font-size: 12px; }
.architect-panel input { width: 100%; box-sizing: border-box; padding: 5px; border: 1px solid var(--border); background: var(--bg-primary); color: var(--text-primary); font-size: 11px; }
.architect-panel button { margin-top: 6px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-primary); color: var(--text-primary); cursor: pointer; font-size: 12px; }
.proposal-ops { margin: 8px 0; padding-left: 18px; font-size: 11px; }
.architect-error { color: #b91c1c !important; }
.proposal-validation { border-left: 2px solid var(--border); padding-left: 8px; margin: 8px 0; }
.proposal-validation p { margin: 4px 0; }
.apply-proposal { border-color: var(--accent) !important; color: var(--accent) !important; }
</style>
