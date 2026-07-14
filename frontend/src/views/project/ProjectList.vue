<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const store = useProjectStore()
const auth = useAuthStore()

const newName = ref('')
const creating = ref(false)

onMounted(async () => {
  await store.fetchProjects()
})

async function createProject() {
  if (!newName.value.trim()) return
  creating.value = true
  try {
    const project = await store.createProject(newName.value.trim())
    newName.value = ''
    router.push(`/projects/${project.project_id}`)
  } catch (e: any) {
    store.error = e?.message ?? String(e)
  } finally {
    creating.value = false
  }
}

function goToProject(id: string) {
  router.push(`/projects/${id}`)
}

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="projects-page">
    <header class="page-header">
      <h1>项目</h1>
      <div class="header-actions">
        <span class="account-info">{{ auth.account?.email }}</span>
        <button class="btn-secondary" @click="logout">登出</button>
      </div>
    </header>

    <div v-if="store.error" class="error-banner">{{ store.error }}</div>

    <form class="create-form" @submit.prevent="createProject">
      <input v-model="newName" type="text" placeholder="新项目名称" :disabled="creating" />
      <button type="submit" :disabled="creating || !newName.trim()">
        {{ creating ? '创建中...' : '创建项目' }}
      </button>
    </form>

    <div v-if="store.loading" class="loading">加载中...</div>

    <div v-else-if="store.projects.length === 0" class="empty">
      <p>暂无项目。创建第一个项目开始使用。</p>
    </div>

    <div v-else class="project-grid">
      <div
        v-for="p in store.projects"
        :key="p.project_id"
        class="project-card"
        @click="goToProject(p.project_id)"
      >
        <h3>{{ p.name }}</h3>
        <div class="meta">
          <span class="status" :class="p.status">{{ p.status }}</span>
          <span class="date">{{ p.updated_at?.slice(0, 10) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.projects-page {
  max-width: 900px;
  margin: 0 auto;
  padding: 32px 24px;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}
.page-header h1 {
  margin: 0;
  color: var(--text-primary);
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.account-info {
  font-size: 13px;
  color: var(--text-secondary);
}
.btn-secondary {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
}
.error-banner {
  background: #fef2f2;
  color: #b91c1c;
  padding: 8px 12px;
  border-radius: 6px;
  margin-bottom: 16px;
  font-size: 14px;
}
.create-form {
  display: flex;
  gap: 8px;
  margin-bottom: 24px;
}
.create-form input {
  flex: 1;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
}
.create-form button {
  padding: 10px 20px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
.project-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 16px;
}
.project-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  cursor: pointer;
  transition: border-color 0.15s;
}
.project-card:hover {
  border-color: var(--accent);
}
.project-card h3 {
  margin: 0 0 8px;
  color: var(--text-primary);
  font-size: 16px;
}
.meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-secondary);
}
.status.active {
  color: #16a34a;
}
.status.archived {
  color: #dc2626;
}
.empty, .loading {
  text-align: center;
  padding: 40px;
  color: var(--text-secondary);
}
</style>