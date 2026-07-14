import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface NodeDefinition {
  node_type_id: string
  revision_id: string
  semantic_version: string
  name: string
  category: string
  description: string
  input_ports: PortDef[]
  output_ports: PortDef[]
  config_schema: Record<string, unknown>
}

interface PortDef {
  port_id: string
  type_id: string
  schema_id: string
  cardinality: string
  label: string
}

export const useNodeRegistryStore = defineStore('nodeRegistry', () => {
  const registry = ref<NodeDefinition[]>([])
  const loading = ref(false)

  async function fetchRegistry() {
    loading.value = true
    try {
      const response = await fetch('/api/v1/registry/definitions?status=active')
      if (!response.ok) throw new Error(`Registry request failed: ${response.status}`)
      registry.value = await response.json() as NodeDefinition[]
    } finally {
      loading.value = false
    }
  }

  function getNodeType(typeId: string): NodeDefinition | undefined {
    return registry.value.find(n => n.node_type_id === typeId)
  }

  return {
    registry,
    loading,
    fetchRegistry,
    getNodeType,
  }
})
