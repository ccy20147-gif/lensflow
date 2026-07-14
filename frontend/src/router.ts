import { createRouter, createWebHistory } from 'vue-router'
import { getAuthToken } from '@/api/client'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/auth/LoginView.vue'),
    },
    {
      path: '/',
      name: 'home',
      redirect: '/projects',
    },
    {
      path: '/projects',
      name: 'projects',
      component: () => import('@/views/project/ProjectList.vue'),
    },
    {
      path: '/projects/:projectId',
      name: 'project-detail',
      component: () => import('@/views/project/ProjectDetail.vue'),
      children: [
        {
          path: 'canvas',
          name: 'canvas',
          component: () => import('@/views/canvas/WorkflowCanvas.vue'),
        },
        {
          path: 'workbench/:workbenchType',
          name: 'workbench',
          component: () => import('@/views/workbench/WorkbenchView.vue'),
        },
        {
          path: 'agent-studio',
          name: 'agent-studio',
          component: () => import('@/views/agent/AgentStudio.vue'),
        },
        {
          path: 'recipe-lab',
          name: 'recipe-lab',
          component: () => import('@/views/recipe/RecipeLab.vue'),
        },
        {
          path: 'resources',
          name: 'resource-library',
          component: () => import('@/views/project/ResourceLibrary.vue'),
        },
        {
          path: 'templates',
          name: 'project-templates',
          component: () => import('@/views/project/TemplateGallery.vue'),
        },
      ],
    },
    {
      path: '/agent',
      name: 'agent-global',
      component: () => import('@/views/agent/AgentStudio.vue'),
    },
    {
      path: '/recipe',
      name: 'recipe-global',
      component: () => import('@/views/recipe/RecipeLab.vue'),
    },
    {
      path: '/skills',
      name: 'skills',
      component: () => import('@/views/skill/SkillWorkshop.vue'),
    },
    {
      path: '/templates',
      name: 'templates',
      component: () => import('@/views/project/TemplateGallery.vue'),
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/settings/SettingsPage.vue'),
    },
  ],
})

// Redirect to /login if no token (except for /login itself)
router.beforeEach((to, _from, next) => {
  if (to.path === '/login') {
    return next()
  }
  if (!getAuthToken()) {
    return next('/login')
  }
  next()
})

export default router
