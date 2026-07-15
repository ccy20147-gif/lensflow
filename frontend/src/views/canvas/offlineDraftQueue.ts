export interface QueuedDraft { workflowId: string; payload: Record<string, unknown>; queuedAt: number }

const DB = 'toonflow-canvas'
const STORE = 'offline-drafts'

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB, 1)
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: 'workflowId' })
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

export async function queueDraft(item: QueuedDraft): Promise<void> {
  const db = await database()
  await new Promise<void>((resolve, reject) => {
    const request = db.transaction(STORE, 'readwrite').objectStore(STORE).put(item)
    request.onsuccess = () => resolve(); request.onerror = () => reject(request.error)
  })
  db.close()
}

export async function takeDraft(workflowId: string): Promise<QueuedDraft | undefined> {
  const db = await database()
  const result = await new Promise<QueuedDraft | undefined>((resolve, reject) => {
    const request = db.transaction(STORE).objectStore(STORE).get(workflowId)
    request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error)
  })
  db.close(); return result
}

export async function discardDraft(workflowId: string): Promise<void> {
  const db = await database()
  await new Promise<void>((resolve, reject) => {
    const request = db.transaction(STORE, 'readwrite').objectStore(STORE).delete(workflowId)
    request.onsuccess = () => resolve(); request.onerror = () => reject(request.error)
  })
  db.close()
}
