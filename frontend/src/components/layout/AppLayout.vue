<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const menuItems = [
  { label: '首页', icon: 'Home', route: '/' },
  { label: '项目', icon: 'FolderKanban', route: '/projects' },
  { label: '模板', icon: 'LayoutTemplate', route: '/templates' },
  { label: '设置', icon: 'Settings', route: '/settings' },
]

const expanded = ref(true)
</script>

<template>
  <div class="app-shell">
    <!-- Sidebar -->
    <aside class="sidebar" :class="{ collapsed: !expanded }">
      <div class="sidebar-header">
        <span v-if="expanded" class="logo">ToonFlow</span>
        <button class="toggle-btn" @click="expanded = !expanded">
          <span v-if="expanded">◀</span>
          <span v-else>▶</span>
        </button>
      </div>
      <nav class="sidebar-nav">
        <a
          v-for="item in menuItems"
          :key="item.route"
          class="nav-item"
          :class="{ active: router.currentRoute.value.path === item.route }"
          @click="router.push(item.route)"
        >
          <span class="nav-icon">{{ item.icon[0] }}</span>
          <span v-if="expanded" class="nav-label">{{ item.label }}</span>
        </a>
      </nav>
    </aside>

    <!-- Main Content -->
    <main class="main-content">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
}

.sidebar {
  width: 240px;
  background: var(--color-surface-alt);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  transition: width 0.2s ease;
  flex-shrink: 0;
}

.sidebar.collapsed {
  width: 60px;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid var(--color-border);
}

.logo {
  font-weight: 700;
  font-size: 18px;
  color: var(--color-primary);
}

.toggle-btn {
  background: none;
  border: none;
  color: var(--color-text-dim);
  cursor: pointer;
  font-size: 14px;
}

.sidebar-nav {
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--color-text-dim);
  text-decoration: none;
  transition: all 0.15s ease;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.05);
  color: var(--color-text);
}

.nav-item.active {
  background: var(--color-primary);
  color: white;
}

.nav-icon {
  font-size: 18px;
  width: 24px;
  text-align: center;
}

.main-content {
  flex: 1;
  overflow: auto;
}
</style>
