export interface InspectorPlugin { nodeTypeId: string; component: string }

// Dedicated editors register here. The Canvas always retains the generic
// JSON-Schema renderer when no trusted plugin is installed.
const plugins = new Map<string, InspectorPlugin>()

export function registerInspectorPlugin(plugin: InspectorPlugin): void { plugins.set(plugin.nodeTypeId, plugin) }
export function inspectorPlugin(nodeTypeId: string): InspectorPlugin | undefined { return plugins.get(nodeTypeId) }
export function hasInspectorPlugin(nodeTypeId: string): boolean { return plugins.has(nodeTypeId) }
