import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import RegistryNode from './RegistryNode.vue'

const definition = {
  node_type_id: 'demo.node',
  label: 'Demo node',
  description: 'A dynamically registered node',
  semantic_version: '1.0.0',
  input_ports: [{ port_id: 'in', type_id: 'artifact', schema_id: 'brief', schema_version: '1', cardinality: 'one', required: true }],
  output_ports: [{ port_id: 'out', type_id: 'artifact', schema_id: 'brief', schema_version: '1', cardinality: 'one' }],
}

function render(data: Record<string, unknown>) {
  return mount(RegistryNode, {
    props: {
      id: 'node-1', type: 'registry', data, selected: false, dragging: false, zIndex: 0,
      isConnectable: true, connectable: true, position: { x: 0, y: 0 }, dimensions: { width: 220, height: 132 },
      resizing: false, events: {} as never, positionAbsoluteX: 0, positionAbsoluteY: 0,
    },
    global: { stubs: { Handle: true } },
  })
}

describe('RegistryNode', () => {
  it('renders schema/cardinality metadata from a registry definition', () => {
    const wrapper = render({ node_type_id: 'demo.node', definition })
    expect(wrapper.text()).toContain('Demo node')
    expect(wrapper.get('.port span').attributes('title')).toContain('brief@1')
    expect(wrapper.get('.port span').text()).toContain('*')
  })

  it('keeps an unavailable definition as an explicit placeholder', () => {
    const wrapper = render({ node_type_id: 'retired.node' })
    expect(wrapper.classes()).toContain('unknown')
    expect(wrapper.text()).toContain('remains read-only')
  })

  it('renders 100 artifact previews across fifty fixed-size dynamic cards', () => {
    const cards = Array.from({ length: 50 }, (_, index) => render({
      node_type_id: `demo.node.${index}`,
      definition,
      artifact_previews: [`artifact-${index}-a`, `artifact-${index}-b`],
    }))
    expect(cards).toHaveLength(50)
    expect(cards.flatMap((card) => card.findAll('.artifact-thumb'))).toHaveLength(100)
    expect(cards.every((card) => card.get('.registry-node').classes().includes('registry-node'))).toBe(true)
  })
})
