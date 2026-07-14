import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  createAgent as apiCreateAgent,
  createAgentRevision as apiCreateAgentRevision,
  deleteAgent as apiDeleteAgent,
  dryRunAgent as apiDryRunAgent,
  getAgent as apiGetAgent,
  getAgentRevision as apiGetAgentRevision,
  listAgentRevisions as apiListAgentRevisions,
  listAgents as apiListAgents,
  promoteAgentRevision as apiPromoteAgentRevision,
  retireAgentRevision as apiRetireAgentRevision,
  updateAgent as apiUpdateAgent,
  validateAgentBody as apiValidateAgentBody,
  type AgentDefinitionRecord,
  type AgentRevisionRecord,
} from '@/api/client'

export type Agent = AgentDefinitionRecord
export type AgentRevision = AgentRevisionRecord

export const useAgentStore = defineStore('agent', () => {
  const agents = ref<Agent[]>([])
  const revisions = ref<AgentRevision[]>([])
  const currentAgent = ref<Agent | null>(null)
  const currentRevision = ref<AgentRevision | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchAgents(): Promise<Agent[]> {
    loading.value = true
    error.value = null
    try {
      agents.value = await apiListAgents()
      return agents.value
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  async function loadAgent(agentId: string): Promise<Agent | null> {
    loading.value = true
    error.value = null
    try {
      const agent = await apiGetAgent(agentId)
      currentAgent.value = agent
      return agent
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      return null
    } finally {
      loading.value = false
    }
  }

  async function createAgent(body: Parameters<typeof apiCreateAgent>[0]): Promise<Agent> {
    loading.value = true
    error.value = null
    try {
      const agent = await apiCreateAgent(body)
      agents.value = [agent, ...agents.value]
      return agent
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  async function updateAgent(
    agentId: string,
    body: Parameters<typeof apiUpdateAgent>[1],
  ): Promise<Agent> {
    const agent = await apiUpdateAgent(agentId, body)
    agents.value = agents.value.map((a) => (a.agent_id === agent.agent_id ? agent : a))
    if (currentAgent.value?.agent_id === agent.agent_id) currentAgent.value = agent
    return agent
  }

  async function deleteAgent(agentId: string): Promise<void> {
    await apiDeleteAgent(agentId)
    agents.value = agents.value.filter((a) => a.agent_id !== agentId)
  }

  async function fetchRevisions(agentId: string): Promise<AgentRevision[]> {
    const rows = await apiListAgentRevisions(agentId)
    revisions.value = rows
    return rows
  }

  async function loadRevision(
    agentId: string,
    revisionId: string,
  ): Promise<AgentRevision | null> {
    try {
      const row = await apiGetAgentRevision(agentId, revisionId)
      currentRevision.value = row
      return row
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      return null
    }
  }

  async function createRevision(
    agentId: string,
    body: { body: Record<string, unknown>; base_hash?: string | null },
  ): Promise<AgentRevision> {
    const row = await apiCreateAgentRevision(agentId, body)
    revisions.value = [row, ...revisions.value]
    currentRevision.value = row
    return row
  }

  async function promoteRevision(
    agentId: string,
    revisionId: string,
  ): Promise<AgentRevision> {
    const row = await apiPromoteAgentRevision(agentId, revisionId)
    revisions.value = revisions.value.map((r) =>
      r.revision_id === row.revision_id ? row : r,
    )
    currentRevision.value = row
    return row
  }

  async function retireRevision(
    agentId: string,
    revisionId: string,
  ): Promise<AgentRevision> {
    const row = await apiRetireAgentRevision(agentId, revisionId)
    revisions.value = revisions.value.map((r) =>
      r.revision_id === row.revision_id ? row : r,
    )
    currentRevision.value = row
    return row
  }

  async function validateBody(body: Record<string, unknown>): Promise<void> {
    await apiValidateAgentBody(body)
  }

  async function dryRun(
    body: Record<string, unknown>,
  ): Promise<{ valid: boolean; step_count: number }> {
    return apiDryRunAgent(body)
  }

  return {
    agents,
    revisions,
    currentAgent,
    currentRevision,
    loading,
    error,
    fetchAgents,
    loadAgent,
    createAgent,
    updateAgent,
    deleteAgent,
    fetchRevisions,
    loadRevision,
    createRevision,
    promoteRevision,
    retireRevision,
    validateBody,
    dryRun,
  }
})
