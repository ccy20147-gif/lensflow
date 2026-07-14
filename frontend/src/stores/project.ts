import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  createProject as apiCreateProject,
  createWorkflow as apiCreateWorkflow,
  ensureAuthToken,
  getDraft as apiGetDraft,
  listProjects as apiListProjects,
  saveDraft as apiSaveDraft,
  type ProjectRecord,
  type WorkflowDraftRecord,
} from '@/api/client'

/**
 * Project & workflow domain types — re-exported here so callers can
 * import them from the store without coupling to the raw API client.
 */
export interface Project extends ProjectRecord {
  // Mirror backend `ProjectRecord` shape exactly; we keep the `Project`
  // alias for legacy call sites in components.
  id: string
  updated_at: string
}

export type WorkflowDraft = WorkflowDraftRecord

export const useProjectStore = defineStore('project', () => {
  const currentProject = ref<Project | null>(null)
  const projects = ref<Project[]>([])
  const workflows = ref<{ workflow_id: string; owner_scope: string }[]>([])
  const currentDraft = ref<WorkflowDraft | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  function toProject(record: ProjectRecord): Project {
    return { ...record, id: record.project_id, updated_at: record.updated_at }
  }

  /**
   * Fetch projects owned by the given owner_scope. The owner scope is
   * auto-derived from auth (the bearer token's account) when omitted;
   * callers rarely need to pass it explicitly.
   */
  async function fetchProjects(ownerScope?: string): Promise<Project[]> {
    loading.value = true
    error.value = null
    try {
      await ensureAuthToken()
      void ownerScope // Owner scope is already encoded in the bearer token.
      const records = await apiListProjects()
      const list = records.map(toProject)
      projects.value = list
      return list
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  async function setCurrentProject(id: string): Promise<Project | null> {
    loading.value = true
    error.value = null
    try {
      await ensureAuthToken()
      const { getProject } = await import('@/api/client')
      const record = await getProject(id)
      const project = toProject(record)
      currentProject.value = project
      return project
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      return null
    } finally {
      loading.value = false
    }
  }

  async function createProject(name: string, description?: string): Promise<Project> {
    loading.value = true
    error.value = null
    try {
      await ensureAuthToken()
      const record = await apiCreateProject({ name, description })
      const project = toProject(record)
      projects.value = [project, ...projects.value]
      return project
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  async function createWorkflow(projectId: string): Promise<string> {
    await ensureAuthToken()
    const result = await apiCreateWorkflow(projectId, {})
    return result.workflow_id
  }

  /**
   * Fetch the workflow draft for a given workflow_id. We use workflow_id
   * (not project_id) here — they are distinct entities on the backend.
   */
  async function fetchDraft(workflowId: string): Promise<WorkflowDraft | null> {
    loading.value = true
    error.value = null
    try {
      await ensureAuthToken()
      const draft = await apiGetDraft(workflowId)
      currentDraft.value = draft
      return draft
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      return null
    } finally {
      loading.value = false
    }
  }

  /**
   * Save a workflow draft using compare-and-swap on `baseHash`. The
   * backend returns 409 if the hash doesn't match, which we surface
   * as a thrown ApiError so the caller can decide how to recover.
   */
  async function saveDraft(
    workflowId: string,
    draft: { nodes?: unknown[]; edges?: unknown[] },
    baseHash: string,
  ): Promise<WorkflowDraft> {
    loading.value = true
    error.value = null
    try {
      const saved = await apiSaveDraft(workflowId, draft, baseHash)
      currentDraft.value = saved
      return saved
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  return {
    currentProject,
    projects,
    workflows,
    currentDraft,
    loading,
    error,
    fetchProjects,
    setCurrentProject,
    createProject,
    createWorkflow,
    fetchDraft,
    saveDraft,
  }
})