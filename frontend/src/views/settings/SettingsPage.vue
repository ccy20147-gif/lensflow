<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'
import { useRouter } from 'vue-router'
const auth = useAuthStore()
const router = useRouter()
function logout() { auth.logout(); router.push('/login') }
</script>

<template>
  <div class="settings-page">
    <h1>设置</h1>
    <div class="card">
      <h3>账户</h3>
      <p>邮箱：{{ auth.account?.email || '未登录' }}</p>
      <button @click="logout">登出</button>
    </div>
    <div class="card">
      <h3>Demo 信息</h3>
      <p>ToonFlow Demo v0.1.0</p>
      <p>PostgreSQL 持久化已启用</p>
      <p>AtlasCloud 凭证由部署环境管理；未配置时实际媒体生成会被安全拒绝</p>
    </div>
  </div>
</template>

<style scoped>
.settings-page { max-width: 600px; margin: 0 auto; padding: 24px; }
h1 { color: var(--text-primary); }
.card { background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
.card h3 { margin: 0 0 8px; color: var(--text-primary); }
.card p { margin: 4px 0; color: var(--text-secondary); font-size: 14px; }
button { padding: 6px 14px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-primary); color: var(--text-primary); cursor: pointer; }
</style>
