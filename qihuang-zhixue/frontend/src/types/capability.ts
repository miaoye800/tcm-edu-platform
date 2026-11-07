/** 能力契约的对外形状。与后端 `app/capabilities/spec.py` 的 `public()` 一一对应。 */

export interface CapabilityArtifact {
  kind: string
  open_url_template: string
  resolve_on: string
  structure: string
  theme: string
  evidence_tiers: string[]
}

export interface CapabilityErrorSpec {
  code: string
  http_status: number
  message: string
}

export interface CapabilitySummary {
  name: string
  version: string
  kind: 'job' | 'query' | 'command'
  title: string
  one_line: string
  use_when: string[]
  avoid_when: string[]
  audiences: string[]
  input_schema: Record<string, unknown>
  output_shape: Record<string, string>
  pipeline: Array<{ id: string; label: string }>
  events: string[]
  idempotency: boolean
  errors: CapabilityErrorSpec[]
  side_effects: string[]
  requires: string[]
  cost: string
  latency: string
  examples: string[]
  artifact?: CapabilityArtifact
  deprecation?: string
}

export interface CapabilityIndex {
  schema_version: number
  count: number
  capabilities: CapabilitySummary[]
}

/** `mindmap.generate` 的入参。字段名与后端 input_model 一致，不做二次改名。 */
export interface MindmapCallParams {
  query: string
  node_id?: string
  depth?: number
  branches?: number
  style?: 'exam' | 'understand' | 'compare'
  focus?: string
  /** `auto` = 由后端按问法给建议（证候题给鱼骨、分类题给向下分支）。 */
  structure?: string
  theme?: string
  client_invocation_id?: string
}

export interface MindmapAccepted {
  ok: boolean
  capability: string
  capability_version: string
  invocation_id: string
  status: string
  replayed: boolean
  task_id: string
  stream_url: string
  artifact: {
    kind: string
    resource_id: string | null
    open_url: string | null
    structure: string
    theme_id: string
    resolve_on: string
  } | null
}
