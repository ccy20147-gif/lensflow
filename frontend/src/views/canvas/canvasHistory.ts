export interface CanvasSnapshot<N = unknown, E = unknown> {
  nodes: N[]
  edges: E[]
}

function copy<N, E>(snapshot: CanvasSnapshot<N, E>): CanvasSnapshot<N, E> {
  return structuredClone(snapshot)
}

/** Small, deterministic undo/redo history for graph editing actions. */
export function createCanvasHistory<N = unknown, E = unknown>() {
  const undoStack: CanvasSnapshot<N, E>[] = []
  const redoStack: CanvasSnapshot<N, E>[] = []

  return {
    checkpoint(snapshot: CanvasSnapshot<N, E>) {
      undoStack.push(copy(snapshot))
      redoStack.length = 0
    },
    undo(current: CanvasSnapshot<N, E>) {
      const previous = undoStack.pop()
      if (!previous) return undefined
      redoStack.push(copy(current))
      return previous
    },
    redo(current: CanvasSnapshot<N, E>) {
      const next = redoStack.pop()
      if (!next) return undefined
      undoStack.push(copy(current))
      return next
    },
  }
}
