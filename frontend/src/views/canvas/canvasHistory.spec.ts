import { describe, expect, it, beforeEach } from 'vitest'
import { createCanvasHistory, type CanvasSnapshot } from './canvasHistory'

/**
 * History module tests — covers undo/redo semantics, snapshot
 * isolation (mutating the current snapshot must not corrupt history),
 * and the redo invalidation rule after a new edit.
 */
describe('canvas history', () => {
  let history: ReturnType<typeof createCanvasHistory<TestNode, TestEdge>>

  interface TestNode {
    id: string
    label?: string
  }
  interface TestEdge {
    id: string
  }

  beforeEach(() => {
    history = createCanvasHistory<TestNode, TestEdge>()
  })

  it('restores graph state and invalidates redo after a new edit', () => {
    const initial: CanvasSnapshot<TestNode, TestEdge> = {
      nodes: [{ id: 'a' }],
      edges: [],
    }
    const edited: CanvasSnapshot<TestNode, TestEdge> = {
      nodes: [{ id: 'a' }, { id: 'b' }],
      edges: [],
    }
    history.checkpoint(initial)

    // undo returns the last checkpointed state (initial)
    expect(history.undo(edited)).toEqual(initial)
    // redo returns the state we passed to undo (edited)
    expect(history.redo(initial)).toEqual(edited)

    history.checkpoint(edited)
    // a new checkpoint after undo invalidates redo
    expect(history.redo(edited)).toBeUndefined()
  })

  it('returns undefined when undoing with empty stack', () => {
    expect(history.undo({ nodes: [], edges: [] })).toBeUndefined()
  })

  it('returns undefined when redoing with empty stack', () => {
    expect(history.redo({ nodes: [], edges: [] })).toBeUndefined()
  })

  it('isolates snapshots via deep copy', () => {
    const nodes: TestNode[] = [{ id: 'a' }]
    const edges: TestEdge[] = []
    history.checkpoint({ nodes, edges })

    const liveSnapshot: CanvasSnapshot<TestNode, TestEdge> = {
      nodes: [{ id: 'b' }],
      edges: [],
    }
    const restored = history.undo(liveSnapshot)

    // Mutating the live snapshot after restoring must not change history.
    liveSnapshot.nodes.push({ id: 'c' })
    expect(restored?.nodes).toEqual([{ id: 'a' }])
  })

  it('supports multiple undo / redo round trips', () => {
    const s1: CanvasSnapshot<TestNode, TestEdge> = { nodes: [{ id: 'a' }], edges: [] }
    const s2: CanvasSnapshot<TestNode, TestEdge> = { nodes: [{ id: 'a' }, { id: 'b' }], edges: [] }
    const s3: CanvasSnapshot<TestNode, TestEdge> = { nodes: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], edges: [] }

    history.checkpoint(s1)  // undo=[s1]
    history.checkpoint(s2)  // undo=[s1,s2]

    // undo returns the most recent checkpoint (s2), pushes current (s3) to redo
    expect(history.undo(s3)).toEqual(s2)
    // undo again: returns s1, pushes s2 to redo
    expect(history.undo(s2)).toEqual(s1)
    // redo: returns s2, pushes s1 to undo
    expect(history.redo(s1)).toEqual(s2)
    // redo again: returns s3, pushes s2 to undo
    expect(history.redo(s2)).toEqual(s3)
  })

  it('a new checkpoint after undo clears the redo stack', () => {
    const s1: CanvasSnapshot<TestNode, TestEdge> = { nodes: [{ id: 'a' }], edges: [] }
    const s2: CanvasSnapshot<TestNode, TestEdge> = { nodes: [{ id: 'a' }, { id: 'b' }], edges: [] }
    const s3: CanvasSnapshot<TestNode, TestEdge> = { nodes: [{ id: 'x' }], edges: [] }

    history.checkpoint(s1)
    // undo pushes the current state to redo
    expect(history.undo(s2)).toEqual(s1)   // redo=[s2]
    // A new checkpoint invalidates redo.
    history.checkpoint(s3)
    expect(history.redo(s3)).toBeUndefined()
  })
})
