<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { apiGet, apiPost } from '@/api/client'

const route = useRoute()
const activeTab = ref<'human-tasks' | 'control-flow' | 'run-trace'>(
  (route.params.workbenchType as string) === 'control-flow' ? 'control-flow' : 'human-tasks'
)

const humanTasks = ref<any[]>([])
const loading = ref(false)
const error = ref('')

// Control flow state
const runId = ref('')
const controlState = ref<any>(null)
const runTrace = ref<any>(null)
const agentTrace = ref<any>(null)
const rerunStatus = ref('')
const requestAnswers = ref<Record<string, Record<string, any>>>({})
const workbenchOutputs = ref<Record<string, string>>({})
const artifacts = ref<any[]>([])
const businessEvidence = computed(() => ({
  selections: artifacts.value.filter((item) => item.schema_id === 'selection_record'),
  reviews: artifacts.value.filter((item) => item.schema_id === 'review_report'),
  exports: artifacts.value.filter((item) => item.schema_id === 'package_manifest'),
}))

let taskPolling: ReturnType<typeof globalThis.setInterval> | undefined
onMounted(async () => {
  await loadHumanTasks()
  await loadArtifacts()
  // A durable worker may materialize a WorkbenchTask moments after this view
  // mounts. Polling the owner-scoped read model keeps the UI in sync without
  // inventing client-side task state.
  taskPolling = globalThis.setInterval(() => { void loadHumanTasks() }, 1_000)
})
onUnmounted(() => { if (taskPolling) globalThis.clearInterval(taskPolling) })

async function loadArtifacts() {
  try { artifacts.value = await apiGet<any[]>('/artifacts/versions') } catch { artifacts.value = [] }
}

async function loadHumanTasks() {
  loading.value = true
  try {
    const data = await apiGet<any>('/runtime/human-tasks')
    humanTasks.value = data.tasks || []
  } catch (e: any) { error.value = e?.message ?? String(e) }
  finally { loading.value = false }
}

async function resolveTask(taskId: string) {
  const task = humanTasks.value.find((item) => item.task_id === taskId)
  try { await apiPost(`/runtime/human-tasks/${taskId}/resolve`, { task_version: task?.task_version, idempotency_token: globalThis.crypto.randomUUID(), payload: {} }); await loadHumanTasks() }
  catch (e: any) { error.value = e?.message ?? String(e) }
}

async function rejectTask(taskId: string) {
  const task = humanTasks.value.find((item) => item.task_id === taskId)
  try { await apiPost(`/runtime/human-tasks/${taskId}/reject`, { task_version: task?.task_version, idempotency_token: globalThis.crypto.randomUUID(), reason: 'User rejected' }); await loadHumanTasks() }
  catch (e: any) { error.value = e?.message ?? String(e) }
}

async function timeoutTask(taskId: string) {
  const task = humanTasks.value.find((item) => item.task_id === taskId)
  try { await apiPost(`/runtime/human-tasks/${taskId}/timeout`, { task_version: task?.task_version, idempotency_token: globalThis.crypto.randomUUID(), reason: 'User timeout' }); await loadHumanTasks() }
  catch (e: any) { error.value = e?.message ?? String(e) }
}

async function resolveRequestInput(task: any) {
  const answer = { ...(requestAnswers.value[task.task_id] || {}) }
  const properties = task.timeout_policy?.input_schema?.properties || {}
  for (const [key, spec] of Object.entries<any>(properties)) {
    if (spec.type === 'integer' && typeof answer[key] === 'string' && answer[key] !== '') answer[key] = Number.parseInt(answer[key], 10)
    if (spec.type === 'number' && typeof answer[key] === 'string' && answer[key] !== '') answer[key] = Number(answer[key])
  }
  try { await apiPost(`/agents/request-input/${task.task_id}/resolve`, { task_version: task.task_version, idempotency_token: globalThis.crypto.randomUUID(), answer }); await loadHumanTasks() }
  catch (e: any) { error.value = e?.message ?? String(e) }
}

async function submitWorkbenchTask(task: any) {
  const raw = workbenchOutputs.value[task.task_id] || ''
  if (!raw) { error.value = '请选择此工作台生成的结果版本。'; return }
  try {
    await apiPost(`/business-nodes/workbench-tasks/${task.task_id}/submit`, {
      task_version: task.task_version,
      idempotency_token: globalThis.crypto.randomUUID(),
      output_artifact_version_ids: [raw],
    })
    await loadHumanTasks()
  } catch (e: any) { error.value = e?.message ?? String(e) }
}

async function loadControlState() {
  if (!runId.value) return
  try { controlState.value = await apiGet<any>(`/control-flow/runs/${runId.value}/state`) }
  catch (e: any) { controlState.value = { error: e?.message } }
}

async function loadRunTrace() {
  if (!runId.value.trim()) return
  try {
    const id = runId.value.trim()
    runTrace.value = await apiGet<any>(`/runtime/workflow-runs/${id}`)
    agentTrace.value = await apiGet<any>(`/runtime/workflow-runs/${id}/agent-trace`)
    rerunStatus.value = ''
  }
  catch (e: any) { runTrace.value = null; error.value = e?.message ?? String(e) }
}

async function cancelRun() {
  if (!runTrace.value?.run_id) return
  try {
    const result = await apiPost<any>(`/runtime/workflow-runs/${runTrace.value.run_id}/cancel`, {})
    rerunStatus.value = `运行已取消：${result.status}`
    await loadRunTrace()
  } catch (e: any) { error.value = e?.message ?? String(e) }
}

async function rerunNode(nodeInstanceId: string) {
  if (!runTrace.value?.run_id) return
  try {
    const result = await apiPost<any>(`/runtime/workflow-runs/${runTrace.value.run_id}/closure/execute`, { selected_node_ids: [nodeInstanceId], mode: 'downstream' })
    rerunStatus.value = `已创建固定输入的重跑 ${result.run_id}；下游在新运行中以新产物版本重新计算。`
  } catch (e: any) { error.value = e?.message ?? String(e) }
}

function traceStepIds(steps: Array<{ step_id?: string }> | undefined): string {
  const names: string[] = []
  for (const step of steps || []) names.push(step.step_id || 'unnamed')
  return names.join(', ')
}
</script>

<template>
  <div class="workbench-page">
    <h1>工作台</h1>
    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="tabs">
      <button :class="{ active: activeTab === 'human-tasks' }" @click="activeTab = 'human-tasks'">Human Gate</button>
      <button :class="{ active: activeTab === 'control-flow' }" @click="activeTab = 'control-flow'">控制流</button>
      <button :class="{ active: activeTab === 'run-trace' }" @click="activeTab = 'run-trace'">运行 Trace</button>
    </div>

    <!-- Human Gate -->
    <div v-if="activeTab === 'human-tasks'" class="tab-content">
      <button class="refresh-btn" @click="loadHumanTasks" :disabled="loading">刷新</button>

      <div v-if="loading">加载中...</div>
      <div v-else-if="humanTasks.length === 0" class="empty">无 Human Gate 任务。</div>
      <div v-else class="task-list">
        <div v-for="t in humanTasks" :key="t.task_id" class="task-card">
          <div class="task-header">
            <span class="task-id">{{ t.task_id?.slice(0, 8) }}...</span>
            <span class="task-status" :class="t.status">{{ t.status }}</span>
          </div>
          <div class="task-meta">
            <span>run: {{ t.run_id?.slice(0, 8) }}...</span>
            <span>kind: {{ t.task_kind }}</span>
          </div>
          <div v-if="t.task_kind === 'workbench_task'" class="workbench-task-detail">
            <span>工作台：{{ t.timeout_policy?.target_workbench }}</span>
            <span>输出：{{ t.schema_ref }}</span>
            <span>固定输入：{{ t.input_snapshot_refs?.length || 0 }}</span>
          </div>
          <div v-if="t.status === 'waiting_user' || t.status === 'pending' || t.status === 'waiting'" class="task-actions">
            <template v-if="t.task_kind === 'request_input'">
              <div v-for="(spec, key) in (t.timeout_policy?.input_schema?.properties || {})" :key="key" class="request-field">
                <label>{{ key }}</label>
                <select v-if="spec.enum" v-model="(requestAnswers[t.task_id] ||= {})[key]">
                  <option v-for="value in spec.enum" :key="String(value)" :value="value">{{ value }}</option>
                </select>
                <input v-else :type="spec.type === 'number' || spec.type === 'integer' ? 'number' : spec.type === 'boolean' ? 'checkbox' : 'text'" v-model="(requestAnswers[t.task_id] ||= {})[key]" />
              </div>
              <button class="accept-btn" @click="resolveRequestInput(t)">提交</button>
            </template>
            <template v-else-if="t.task_kind === 'workbench_task'">
              <select v-model="workbenchOutputs[t.task_id]" aria-label="结果 ArtifactVersion">
                <option value="">选择固定结果版本</option>
                <option v-for="artifact in artifacts.filter((item) => `${item.schema_id}.v${item.schema_version}` === t.schema_ref)" :key="artifact.artifact_version_id" :value="artifact.artifact_version_id">{{ artifact.schema_id }} · {{ artifact.artifact_version_id.slice(0, 8) }}</option>
              </select>
              <button class="accept-btn" @click="submitWorkbenchTask(t)">提交结果</button>
            </template>
            <template v-else>
            <button class="accept-btn" @click="resolveTask(t.task_id)">✓ 通过</button>
            <button class="reject-btn" @click="rejectTask(t.task_id)">✗ 拒绝</button>
            <button class="timeout-btn" @click="timeoutTask(t.task_id)">⏱ 超时</button>
            </template>
          </div>
        </div>
      </div>
      <section class="business-evidence" aria-label="业务节点状态">
        <h3>选择、审查与导出</h3>
        <div class="evidence-grid">
          <div class="evidence-card"><strong>Selection</strong><span>{{ businessEvidence.selections.length }} 条固定选择记录</span></div>
          <div class="evidence-card"><strong>Review</strong><span>{{ businessEvidence.reviews.length }} 份结构化报告</span></div>
          <div class="evidence-card"><strong>Export</strong><span>{{ businessEvidence.exports.length }} 个固定交付包</span></div>
        </div>
      </section>
    </div>

    <!-- Control Flow -->
    <div v-if="activeTab === 'control-flow'" class="tab-content">
      <div class="control-input">
        <input v-model="runId" placeholder="Run ID" />
        <button @click="loadControlState">查询</button>
      </div>
      <div v-if="controlState" class="control-output">
        <pre>{{ JSON.stringify(controlState, null, 2) }}</pre>
      </div>
      <p class="control-note">Map、OrderedMap/Fold 与固定版本子工作流的运行状态会显示在此快照中。</p>
    </div>

    <div v-if="activeTab === 'run-trace'" class="tab-content">
      <div class="control-input"><input v-model="runId" placeholder="Run ID" aria-label="Run ID" /><button @click="loadRunTrace">加载 Trace</button></div>
      <button v-if="runTrace && !['completed', 'failed', 'cancelled'].includes(runTrace.status)" class="reject-btn" @click="cancelRun">取消运行</button>
      <p v-if="rerunStatus" class="run-status">{{ rerunStatus }}</p>
      <div v-if="runTrace" class="trace-list">
        <div v-for="node in runTrace.nodes" :key="node.node_run_id" class="trace-card">
          <header><strong>{{ node.node_instance_id }}</strong><span>{{ node.node_type_id }}</span><em :class="node.status">{{ node.status }}</em></header>
          <div v-for="attempt in node.attempts" :key="attempt.attempt_id" class="attempt-row">
            <div><code>epoch {{ attempt.execution_epoch }}</code><span>{{ attempt.status }}</span><span v-if="attempt.fixed_input?.agent_revision_id">revision {{ attempt.fixed_input.agent_revision_id.slice(0, 8) }}</span><span v-if="attempt.actual_cost !== null">cost {{ attempt.actual_cost }}</span></div>
            <div v-if="attempt.fixed_input?.upstream_artifact_refs?.length" class="trace-refs">输入: <code v-for="ref in attempt.fixed_input.upstream_artifact_refs" :key="ref.source_node_id">{{ ref.source_node_id }} → {{ ref.artifact_version_ids.join(', ') }}</code></div>
            <div v-if="attempt.fixed_input?.committed_resource_refs?.length" class="trace-refs">已提交资源: <code v-for="ref in attempt.fixed_input.committed_resource_refs" :key="ref.revision_id">{{ ref.resource_type }} · {{ ref.revision_id.slice(0, 8) }}</code></div>
            <div v-if="attempt.output_artifact_version_ids?.length" class="trace-refs">输出: <code v-for="id in attempt.output_artifact_version_ids" :key="id">{{ id.slice(0,8) }}</code></div>
            <div v-if="attempt.fixed_input?.fallback_for_node_ids?.length" class="fallback">Fallback for: {{ attempt.fixed_input.fallback_for_node_ids.join(', ') }}</div>
          </div>
          <button :disabled="node.status === 'running' || node.status === 'waiting_user'" @click="rerunNode(node.node_instance_id)">从固定输入重跑下游</button>
        </div>
      </div>
      <section v-if="agentTrace?.agents?.length" class="agent-trace-list" aria-label="Agent 运行详情">
        <h3>Agent 运行详情</h3>
        <article v-for="agent in agentTrace.agents" :key="agent.node_instance_id" class="agent-trace-card">
          <header><strong>{{ agent.node_instance_id }}</strong><span>{{ agent.node_status }}</span></header>
          <div v-for="attempt in agent.attempts" :key="attempt.attempt_id" class="agent-attempt">
            <div>revision <code>{{ attempt.agent_revision_id?.slice(0, 8) || 'unfixed' }}</code> · epoch {{ attempt.execution_epoch }} · {{ attempt.status }}</div>
            <div v-if="attempt.input_artifact_refs?.length" class="trace-refs">输入版本: <code v-for="ref in attempt.input_artifact_refs" :key="ref.source_node_id">{{ ref.source_node_id }} → {{ ref.artifact_version_ids.join(', ') }}</code></div>
            <div v-if="attempt.resource_refs?.length" class="trace-refs">资源版本: <code v-for="ref in attempt.resource_refs" :key="ref.revision_id">{{ ref.resource_type }} · {{ ref.revision_id.slice(0, 8) }}</code></div>
            <div v-if="attempt.output_artifact_version_ids?.length" class="trace-refs">输出版本: <code v-for="id in attempt.output_artifact_version_ids" :key="id">{{ id.slice(0, 8) }}</code></div>
            <div v-if="attempt.actual_cost !== null">{{ attempt.provider_id || 'provider' }} / {{ attempt.model_id || 'unknown' }} · cost {{ attempt.actual_cost }} · usage {{ JSON.stringify(attempt.usage || {}) }}</div>
            <div v-if="attempt.request_input" class="request-recovery">RequestInput: {{ attempt.request_input.status }} · {{ attempt.request_input.schema_ref }} · {{ attempt.request_input_answered ? '已恢复回答' : '等待回答' }}</div>
            <div v-for="trace in attempt.sop_trace" :key="trace.artifact_version_id" class="sop-trace">SOP {{ trace.phase }}<span v-if="trace.failure_owner"> · failure: {{ trace.failure_owner }}</span><span v-if="trace.sop_steps?.length"> · {{ traceStepIds(trace.sop_steps) }}</span></div>
          </div>
        </article>
      </section>
      <p v-else class="control-note">输入运行 ID 查看每个节点的固定 revision、输入/输出版本、attempt、成本和 fallback 归属。</p>
    </div>
  </div>
</template>

<style scoped>
.workbench-page { max-width: 900px; margin: 0 auto; padding: 24px; }
h1 { color: var(--text-primary); }
.error-banner { background: #fef2f2; color: #b91c1c; padding: 8px; border-radius: 4px; margin-bottom: 12px; font-size: 13px; }
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
.tabs button { padding: 8px 16px; border: none; background: none; color: var(--text-secondary); cursor: pointer; font-size: 14px; }
.tabs button.active { color: var(--accent); border-bottom: 2px solid var(--accent); }
.tab-content { padding: 8px 0; }
.refresh-btn { padding: 6px 14px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-primary); color: var(--text-primary); cursor: pointer; margin-bottom: 12px; }
.empty { color: var(--text-secondary); padding: 20px; text-align: center; }
.task-list { display: flex; flex-direction: column; gap: 8px; }
.task-card { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 6px; padding: 12px; }
.task-header { display: flex; justify-content: space-between; margin-bottom: 4px; }
.task-id { font-family: monospace; font-size: 12px; color: var(--text-primary); }
.task-status { font-size: 12px; padding: 2px 6px; border-radius: 3px; }
.task-status.waiting_user { background: #fffbeb; color: #92400e; }
.task-status.pending { background: #fffbeb; color: #92400e; }
.task-status.waiting { background: #fffbeb; color: #92400e; }
.task-status.accepted { background: #f0fdf4; color: #16a34a; }
.task-status.rejected { background: #fef2f2; color: #dc2626; }
.task-status.expired { background: #f3f4f6; color: #6b7280; }
.task-meta { font-size: 11px; color: var(--text-secondary); display: flex; gap: 12px; margin-bottom: 8px; }
.workbench-task-detail { display: flex; gap: 12px; margin: 6px 0; font-size: 12px; color: var(--text-primary); }
.task-actions { display: flex; gap: 6px; }
.task-actions button { padding: 4px 10px; border: 1px solid var(--border); border-radius: 4px; font-size: 12px; cursor: pointer; }
.business-evidence { margin-top: 18px; border-top: 1px solid var(--border); padding-top: 14px; }
.business-evidence h3 { margin: 0 0 8px; font-size: 14px; color: var(--text-primary); }
.evidence-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.evidence-card { border: 1px solid var(--border); background: var(--bg-secondary); padding: 10px; border-radius: 4px; display: grid; gap: 4px; }
.evidence-card span { font-size: 12px; color: var(--text-secondary); }
.accept-btn { color: #16a34a; }
.reject-btn { color: #dc2626; }
.timeout-btn { color: #6b7280; }
.control-input { display: flex; gap: 8px; margin-bottom: 12px; }
.control-input input { flex: 1; padding: 8px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-primary); color: var(--text-primary); }
.control-input button { padding: 8px 16px; background: var(--accent); color: white; border: none; border-radius: 4px; cursor: pointer; }
.control-output { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 6px; padding: 12px; margin-bottom: 12px; overflow-x: auto; }
.control-output pre { margin: 0; font-size: 12px; color: var(--text-primary); white-space: pre-wrap; }
.control-note { font-size: 12px; color: var(--text-secondary); }
.run-status { padding: 8px; background: #eff6ff; color: #1d4ed8; font-size: 12px; }
.trace-list { display: grid; gap: 8px; }
.trace-card { border: 1px solid var(--border); background: var(--bg-secondary); padding: 10px; border-radius: 5px; }
.trace-card header { display: flex; gap: 8px; align-items: center; font-size: 13px; }
.trace-card header span { color: var(--text-secondary); font-size: 12px; }.trace-card header em { margin-left: auto; font-style: normal; font-size: 12px; }
.attempt-row { border-top: 1px solid var(--border); margin-top: 8px; padding-top: 8px; display: grid; gap: 5px; font-size: 12px; }.attempt-row > div:first-child { display: flex; gap: 8px; flex-wrap: wrap; }.trace-refs { display: flex; gap: 5px; flex-wrap: wrap; color: var(--text-secondary); }.fallback { color: #92400e; }.trace-card button { margin-top: 10px; padding: 5px 9px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-primary); color: var(--text-primary); cursor: pointer; font-size: 12px; }
.agent-trace-list { margin-top: 16px; display: grid; gap: 8px; }.agent-trace-list h3 { margin: 0; font-size: 14px; }.agent-trace-card { border: 1px solid var(--border); border-radius: 5px; background: var(--bg-secondary); padding: 10px; }.agent-trace-card header { display: flex; justify-content: space-between; font-size: 13px; }.agent-attempt { border-top: 1px solid var(--border); margin-top: 8px; padding-top: 8px; display: grid; gap: 5px; font-size: 12px; }.request-recovery { color: #92400e; }.sop-trace { color: var(--text-secondary); }
</style>
