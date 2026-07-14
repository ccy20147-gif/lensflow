import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useNodeRegistryStore } from './nodeRegistry'

describe('node registry store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('loads active definitions and always clears loading', async () => {
    const response = [{ node_type_id: 'agent.architect', revision_id: 'r1', semantic_version: '1.0.0' }]
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => response }))
    const store = useNodeRegistryStore()

    await store.fetchRegistry()

    expect(fetch).toHaveBeenCalledWith('/api/v1/registry/definitions?status=active')
    expect(store.loading).toBe(false)
    expect(store.getNodeType('agent.architect')).toMatchObject({ revision_id: 'r1' })
  })

  it('clears loading when the registry request fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))
    const store = useNodeRegistryStore()

    await expect(store.fetchRegistry()).rejects.toThrow('503')
    expect(store.loading).toBe(false)
  })
})
