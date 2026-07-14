<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const router = useRouter()
const store = useProjectStore()

const projectId = route.params.projectId as string
const newWorkflowLoading = ref(false)

onMounted(async () => {
  await store.fetchProjects()
  store.setCurrentProject(projectId)
})

async function createWorkflow() {
  newWorkflowLoading.value = true
  try {
    const wfId = await store.createWorkflow(projectId)
    router.push(`/projects/${projectId}/canvas?workflow_id=${wfId}`)
  } catch (e: any) {
    store.error = e?.message ?? String(e)
  } finally {
    newWorkflowLoading.value = false
  }
}

function goToCanvas(wfId: string) {
  router.push(`/projects/${projectId}/canvas?workflow_id=${wfId}`)
}

function goTo(tab: string) {
  router.push(`/projects/${projectId}/${tab}`)
}
</script>

<template>
  <div class="project-detail">
    <header class="page-header">
      <button class="back-btn" @click="router.push('/projects')">← 返回</button>
      <h1>{{ store.currentProject?.name || '项目详情' }}</h1>
    </header>

    <div v-if="store.error" class="error-banner">{{ store.error }}</div>

    <div class="actions">
      <button class="btn-primary" :disabled="newWorkflowLoading" @click="createWorkflow">
        {{ newWorkflowLoading ? '创建中...' : '新建工作流' }}
      </button>
    </div>

    <div class="tabs">
      <button class="tab" @click="goTo('canvas')">画布</button>
      <button class="tab" @click="goTo('workbench/human-tasks')">工作台</button>
      <button class="tab" @click="goTo('agent-studio')">Agent</button>
      <button class="tab" @click="goTo('recipe-lab')">Recipe</button>
      <button class="tab" @click="goTo('resources')">资源库</button>
      <button class="tab" @click="goTo('templates')">模板</button>
    </div>

    <div class="workflow-list">
      <h3>工作流</h3>
      <div v-if="store.loading" class="loading">加载中...</div>
      <div v-else class="workflow-grid">
        <div
          v-for="wf in store.workflows"
          :key="wf.workflow_id"
          class="workflow-card"
          @click="goToCanvas(wf.workflow_id)"
        >
          <span class="wf-id">{{ wf.workflow_id?.slice(0, 8) }}...</span>
          <span class="open-hint">打开 →</span>
        </div>
        <div v-if="store.workflows.length === 0" class="empty">
          尚无工作流。点击"新建工作流"开始。
        </div>
      </div>
    </div>

    <router-view />
  </div>
</template>

<style scoped>
.project-detail {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px;
}
.page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.page-header h1 {
  margin: 0;
  color: var(--text-primary);
}
.back-btn {
  padding: 4px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
}
.error-banner {
  background: #fef2f2; color: #b91c1c; padding: 8px 12px; border-radius: 6px; margin-bottom: 16px;
}
.actions {
  margin-bottom: 16px;
}
.btn-primary {
  padding: 8px 20px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 8px;
}
.tab {
  padding: 6px 14px;
  border: none;
  background: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 14px;
  border-radius: 4px;
}
.tab:hover {
  background: var(--bg-secondary);
  color: var(--text-primary);
}
.workflow-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}
.workflow-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.workflow-card:hover {
  border-color: var(--accent);
}
.wf-id {
  font-family: monospace;
  font-size: 13px;
  color: var(--text-primary);
}
.open-hint {
  font-size: 12px;
  color: var(--text-secondary);
}
.empty, .loading {
  text-align: center;
  padding: 20px;
  color: var(--text-secondary);
}
</style>
