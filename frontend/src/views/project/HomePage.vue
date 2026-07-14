<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="home-page">
    <div class="hero">
      <h1 class="hero-title">ToonFlow</h1>
      <p class="hero-subtitle">开放创作平台</p>
    </div>

    <div class="action-grid">
      <div class="action-card" @click="router.push('/templates')">
        <div class="card-icon">📋</div>
        <div class="card-title">从模板开始</div>
        <div class="card-desc">选择一个模板快速上手创作</div>
      </div>

      <div class="action-card" @click="router.push('/projects')">
        <div class="card-icon">📁</div>
        <div class="card-title">我的项目</div>
        <div class="card-desc">打开已有项目或创建新项目</div>
      </div>

      <div class="action-card">
        <div class="card-icon">🤖</div>
        <div class="card-title">AI 助手</div>
        <div class="card-desc">让智能体帮你创建工作流</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-page {
  padding: 48px;
  max-width: 960px;
  margin: 0 auto;
}

.hero {
  text-align: center;
  margin-bottom: 64px;
}

.hero-title {
  font-size: 48px;
  font-weight: 800;
  background: linear-gradient(135deg, var(--color-primary), var(--color-accent));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  margin-bottom: 8px;
}

.hero-subtitle {
  color: var(--color-text-dim);
  font-size: 20px;
}

.action-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
}

.action-card {
  background: var(--color-surface-alt);
  border: 1px solid var(--color-border);
  border-radius: 16px;
  padding: 32px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.action-card:hover {
  border-color: var(--color-primary);
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(99, 102, 241, 0.15);
}

.card-icon {
  font-size: 36px;
  margin-bottom: 16px;
}

.card-title {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 8px;
}

.card-desc {
  font-size: 14px;
  color: var(--color-text-dim);
  line-height: 1.5;
}
</style>
