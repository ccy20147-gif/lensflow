import { describe, expect, it } from 'vitest'
import { catalogNodeCompatible, draftNodeToCanvasNode, filterCatalog, isUnknownDefinition, layoutPositions, serializeCanvasNode } from './canvasRegistry'

const briefOut = { port_id: 'out', type_id: 'artifact', schema_id: 'brief', schema_version: '1' }
const briefIn = { port_id: 'in', type_id: 'artifact', schema_id: 'brief', schema_version: '1' }

describe('canvas registry adapters', () => {
  it('filters dynamic catalog entries by entitlement state and selected-node compatibility', () => {
    const selected = { type_id: 'source', output_ports: [briefOut] }
    const allowed = { type_id: 'allowed', category: 'Generate', input_ports: [briefIn] }
    const denied = { type_id: 'denied', category: 'Generate', available: false, input_ports: [briefIn] }
    const incompatible = { type_id: 'incompatible', category: 'Control', input_ports: [{ ...briefIn, schema_id: 'image' }] }
    expect(catalogNodeCompatible(selected, allowed)).toBe(true)
    expect(catalogNodeCompatible(selected, incompatible)).toBe(false)
    expect(filterCatalog([allowed, denied, incompatible], { search: '', category: 'all', availability: 'unavailable', compatibility: 'compatible' }, selected)).toEqual([denied])
    expect(filterCatalog([allowed, denied, incompatible], { search: '', category: 'all', availability: 'available', compatibility: 'incompatible' }, selected)).toEqual([incompatible])
  })

  it('reads legacy positions but writes the normalized layout.nodes contract', () => {
    expect(layoutPositions({ nodes: { current: { x: 1, y: 2 } }, positions: { old: { x: 3, y: 4 } } })).toEqual({ current: { x: 1, y: 2 } })
    expect(layoutPositions({ positions: { old: { x: 3, y: 4 } } })).toEqual({ old: { x: 3, y: 4 } })
  })

  it('preserves a retired node verbatim and identifies it as read-only', () => {
    const raw = { id: 'retired', type: 'retired.v1', opaque_extension: { survives: true }, data: { config: { old: 'value' } } }
    const canvasNode = { id: 'retired', data: { unknown_definition: true, raw_node: raw } }
    expect(isUnknownDefinition(canvasNode)).toBe(true)
    expect(serializeCanvasNode(canvasNode)).toBe(raw)
    const rendered = draftNodeToCanvasNode(raw, undefined, { x: 1, y: 2 }, 'fallback')
    expect(rendered.draggable).toBe(false)
    expect(rendered.deletable).toBe(false)
  })
})
