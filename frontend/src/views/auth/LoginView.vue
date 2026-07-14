<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const mode = ref<'bootstrap' | 'login' | 'register'>('bootstrap')
const email = ref('')
const displayName = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

onMounted(async () => {
  const completed = await auth.refreshBootstrapStatus()
  if (completed) {
    mode.value = 'login'
  }
})

async function doBootstrap() {
  loading.value = true
  error.value = ''
  try {
    await auth.bootstrap({ email: email.value, display_name: displayName.value, password: password.value })
    mode.value = 'login'
  } catch (e: any) {
    error.value = e?.message ?? String(e)
  } finally {
    loading.value = false
  }
}

async function doLogin() {
  loading.value = true
  error.value = ''
  try {
    await auth.login({ email: email.value, password: password.value })
    router.push('/projects')
  } catch (e: any) {
    error.value = e?.message ?? String(e)
  } finally {
    loading.value = false
  }
}

async function doRegister() {
  loading.value = true
  error.value = ''
  try {
    await auth.register({ email: email.value, display_name: displayName.value, password: password.value })
    router.push('/projects')
  } catch (e: any) {
    error.value = e?.message ?? String(e)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <h1>ToonFlow</h1>
      <p class="subtitle">Open Creation Platform</p>

      <div v-if="error" class="error-banner">{{ error }}</div>

      <!-- Bootstrap -->
      <template v-if="mode === 'bootstrap'">
        <h2>初始化管理员</h2>
        <p class="hint">首次运行需要创建管理员账户。</p>
        <form @submit.prevent="doBootstrap">
          <label>邮箱</label>
          <input v-model="email" type="email" placeholder="admin@toonflow.local" required />
          <label>显示名称</label>
          <input v-model="displayName" type="text" placeholder="Admin" required />
          <label>密码</label>
          <input v-model="password" type="password" placeholder="至少 6 位" required minlength="6" />
          <button type="submit" :disabled="loading">{{ loading ? '初始化中...' : '初始化' }}</button>
        </form>
      </template>

      <!-- Login -->
      <template v-if="mode === 'login'">
        <h2>登录</h2>
        <form @submit.prevent="doLogin">
          <label>邮箱</label>
          <input v-model="email" type="email" placeholder="admin@toonflow.local" required />
          <label>密码</label>
          <input v-model="password" type="password" required />
          <button type="submit" :disabled="loading">{{ loading ? '登录中...' : '登录' }}</button>
        </form>
        <p class="switch-link">
          还没有账户？<a href="#" @click.prevent="mode = 'register'">注册</a>
        </p>
      </template>

      <!-- Register -->
      <template v-if="mode === 'register'">
        <h2>注册</h2>
        <form @submit.prevent="doRegister">
          <label>邮箱</label>
          <input v-model="email" type="email" placeholder="user@example.com" required />
          <label>显示名称</label>
          <input v-model="displayName" type="text" placeholder="User" required />
          <label>密码</label>
          <input v-model="password" type="password" placeholder="至少 6 位" required minlength="6" />
          <button type="submit" :disabled="loading">{{ loading ? '注册中...' : '注册' }}</button>
        </form>
        <p class="switch-link">
          已有账户？<a href="#" @click.prevent="mode = 'login'">登录</a>
        </p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: var(--bg-primary);
}
.login-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 40px;
  width: 400px;
  max-width: 90vw;
}
.login-card h1 {
  margin: 0 0 4px;
  font-size: 28px;
  color: var(--text-primary);
}
.subtitle {
  color: var(--text-secondary);
  margin: 0 0 24px;
}
.error-banner {
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 16px;
  font-size: 14px;
}
form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
label {
  font-size: 13px;
  color: var(--text-secondary);
}
input {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 14px;
}
button {
  padding: 10px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  margin-top: 8px;
}
button:disabled {
  opacity: 0.6;
  cursor: default;
}
.switch-link {
  text-align: center;
  margin-top: 16px;
  font-size: 13px;
  color: var(--text-secondary);
}
.switch-link a {
  color: var(--accent);
}
</style>