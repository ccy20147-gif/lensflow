/** Pure registry/canvas adapters. Keeping them outside the Vue component makes
 * compatibility, legacy-layout and retired-node behavior independently testable. */
export interface CanvasPortDef {
  port_id: string
  label?: string
  type_id: string
  schema_id: string
  schema_version?: string
  cardinality?: string
  required?: boolean
}

export interface CanvasCatalogNode {
  type_id: string
  revision_id?: string
  semantic_version?: string
  label?: string
  description?: string
  category?: string
  available?: boolean
  unavailable_reason?: string
  input_ports?: CanvasPortDef[]
  output_ports?: CanvasPortDef[]
  config_schema?: Record<string, unknown>
  provider_required?: boolean
  execution_available?: boolean
  config?: Record<string, unknown>
}

export function portsCompatible(left: CanvasPortDef, right: CanvasPortDef): boolean {
  return left.type_id === right.type_id
    && left.schema_id === right.schema_id
    && (left.schema_version || '1') === (right.schema_version || '1')
}

export function catalogNodeCompatible(selected: CanvasCatalogNode | undefined, candidate: CanvasCatalogNode): boolean {
  if (!selected) return true
  return (selected.output_ports || []).some((output) => (candidate.input_ports || []).some((input) => portsCompatible(output, input)))
    || (candidate.output_ports || []).some((output) => (selected.input_ports || []).some((input) => portsCompatible(output, input)))
}

export function catalogNodeAvailable(node: CanvasCatalogNode): boolean {
  return node.available !== false
}

export function filterCatalog(
  entries: CanvasCatalogNode[],
  filters: { search: string; category: string; availability: 'all' | 'available' | 'unavailable'; compatibility: 'all' | 'compatible' | 'incompatible' },
  selected?: CanvasCatalogNode,
): CanvasCatalogNode[] {
  const search = filters.search.trim().toLocaleLowerCase()
  return entries.filter((node) => {
    const matchesSearch = !search || `${node.label || ''} ${node.type_id} ${node.description || ''}`.toLocaleLowerCase().includes(search)
    const available = catalogNodeAvailable(node)
    const compatible = catalogNodeCompatible(selected, node)
    return matchesSearch
      && (filters.category === 'all' || node.category === filters.category)
      && (filters.availability === 'all' || (filters.availability === 'available' && available) || (filters.availability === 'unavailable' && !available))
      && (filters.compatibility === 'all' || (filters.compatibility === 'compatible' && compatible) || (filters.compatibility === 'incompatible' && !compatible))
  })
}

/** `nodes` is the normalized API contract. `positions` is only read for old drafts. */
export function layoutPositions(layout: Record<string, any> | null | undefined): Record<string, unknown> {
  return layout?.nodes || layout?.positions || {}
}

export function isUnknownDefinition(node: { data?: { unknown_definition?: boolean } }): boolean {
  return node.data?.unknown_definition === true
}

export interface CanvasDraftNode {
  id?: string
  type?: string
  label?: string
  position?: { x: number; y: number }
  config?: Record<string, unknown>
  data?: Record<string, unknown>
  [key: string]: unknown
}

/** Map a persisted node into a UI node without granting retired definitions edit rights. */
export function draftNodeToCanvasNode(raw: CanvasDraftNode, definition: CanvasCatalogNode | undefined, position: { x: number; y: number }, fallbackId: string) {
  const persistedData = raw.data || {}
  const nodeTypeId = typeof persistedData.node_type_id === 'string' ? persistedData.node_type_id : raw.type
  const config = persistedData.config ?? raw.config ?? {}
  return {
    id: raw.id || fallbackId,
    type: 'registry',
    label: raw.label || raw.type || 'Node',
    position,
    data: { ...persistedData, node_type_id: nodeTypeId, config, definition, raw_node: raw, unknown_definition: !definition },
    draggable: Boolean(definition),
    selectable: true,
    deletable: Boolean(definition),
  }
}

export function serializeCanvasNode(node: any): any {
  if (isUnknownDefinition(node)) return node.data.raw_node
  return {
    id: node.id,
    type: node.data.node_type_id,
    label: node.label,
    data: {
      node_type_id: node.data.node_type_id,
      definition_revision_id: node.data.definition_revision_id,
      config: node.data.config || {},
    },
  }
}
