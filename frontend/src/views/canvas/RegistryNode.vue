<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position, type NodeProps } from '@vue-flow/core'

interface Port {
  port_id: string
  label?: string
  type_id: string
  schema_id: string
  schema_version?: string
  cardinality?: string
  required?: boolean
}

interface Definition {
  node_type_id: string
  semantic_version?: string
  name?: string
  label?: string
  description?: string
  input_ports?: Port[]
  output_ports?: Port[]
}

const props = defineProps<NodeProps>()
const definition = props.data.definition as Definition | undefined
const inputPorts = definition?.input_ports ?? []
const outputPorts = definition?.output_ports ?? []
const artifactPreviews = computed(() => {
  const data = props.data as { artifact_previews?: unknown; config?: { artifact_previews?: unknown } }
  const raw = data.artifact_previews ?? data.config?.artifact_previews
  return Array.isArray(raw) ? raw.slice(0, 3) : []
})
</script>

<template>
  <article class="registry-node" :class="{ unknown: !definition }">
    <header>
      <strong>{{ definition?.label || definition?.name || data.node_type_id || 'Unknown node' }}</strong>
      <small v-if="definition?.semantic_version">v{{ definition.semantic_version }}</small>
    </header>
    <p :title="definition?.description || 'This node definition is unavailable. It remains read-only.'">{{ definition?.description || 'This node definition is unavailable. It remains read-only.' }}</p>
    <div class="ports inputs">
      <div v-for="port in inputPorts" :key="port.port_id" class="port">
        <Handle :id="port.port_id" type="target" :position="Position.Left" />
        <span :title="`${port.type_id} · ${port.schema_id}@${port.schema_version || '1'} · ${port.cardinality || 'one'}${port.required ? ' · required' : ' · optional'}`">
          {{ port.label || port.port_id }}<b v-if="port.required">*</b>
        </span>
      </div>
    </div>
    <div class="ports outputs">
      <div v-for="port in outputPorts" :key="port.port_id" class="port">
        <span :title="`${port.type_id} · ${port.schema_id}@${port.schema_version || '1'} · ${port.cardinality || 'one'}${port.required ? ' · required' : ' · optional'}`">
          {{ port.label || port.port_id }}
        </span>
        <Handle :id="port.port_id" type="source" :position="Position.Right" />
      </div>
    </div>
    <div v-if="artifactPreviews.length" class="artifact-strip" :aria-label="`${artifactPreviews.length} artifact previews`">
      <span v-for="(preview, index) in artifactPreviews" :key="index" class="artifact-thumb" :title="typeof preview === 'string' ? preview : JSON.stringify(preview)">{{ index + 1 }}</span>
    </div>
  </article>
</template>

<style scoped>
.registry-node { position: relative; width: 220px; height: 132px; overflow: hidden; border: 1px solid #94a3b8; background: #fff; color: #1e293b; contain: layout paint; }
.registry-node.unknown { border-style: dashed; background: #f8fafc; }
header { display: flex; justify-content: space-between; gap: 8px; padding: 8px 10px 4px; font-size: 12px; }
small { color: #64748b; white-space: nowrap; }
p { height: 36px; margin: 0; padding: 0 10px 7px; overflow: hidden; color: #475569; font-size: 11px; line-height: 1.35; }
.ports { border-top: 1px solid #e2e8f0; padding: 4px 0; }
.port { position: relative; min-height: 18px; padding: 2px 10px; overflow: hidden; color: #334155; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.outputs .port { text-align: right; }
b { color: #b91c1c; margin-left: 2px; }
.artifact-strip { position: absolute; right: 5px; bottom: 4px; display: flex; gap: 3px; height: 15px; }
.artifact-thumb { display: inline-grid; width: 15px; height: 15px; place-items: center; overflow: hidden; border: 1px solid #94a3b8; border-radius: 2px; background: #e2e8f0; color: #334155; font-size: 8px; }
</style>
