export type BackendPathState = 'locked' | 'available' | 'current' | 'completed' | 'paused' | 'custom'
export type MeridianStatus = 'recommended' | 'current' | 'mastered' | 'free' | 'deferred' | 'exam_hot' | 'custom'

export interface KnowledgeNode { id: string; name: string; level: 'domain' | 'subtype' | 'point'; parent_id?: string | null; description?: string; exam_frequency?: number; sort_hint?: number; extra?: Record<string, unknown>; children?: KnowledgeNode[] }
export interface KnowledgeTreeResponse { domains: KnowledgeNode[] }
export interface PathState { node_id: string; status: BackendPathState; updated_at: string; meta: Record<string, unknown> }
export interface StagePlan { domain_id: string; order: number; order_locked: boolean; reason: string }
export interface NodePlan { node_id: string; stage: number; seq: number; target_mastery: number; resource_hint: string; highlight: 'normal' | 'exam_hot' | 'remedial'; calibration: boolean; reason: string; name?: string; custom?: boolean; parent_id?: string; domain_id?: string }
export interface PlanResponse { version: number; fallback: boolean; suggestion: string; stages: StagePlan[]; nodes: NodePlan[]; verify_attempts: number; verification_issues: Array<{ code: string; message: string; node_ids: string[] }> }
export interface ExpandResponse extends Omit<PlanResponse, 'suggestion'> { domain_id: string }
export interface CurrentPathResponse { plan: { version: number | null; stages: StagePlan[]; domains: Record<string, { domain_id?: string; nodes: NodePlan[] }>; adjustments: Array<{ version: number; diff: AdjustmentDiff }> }; states: PathState[]; suggestions: string[] }
export interface AdjustmentDiff { node_id: string; action: 'skip' | 'normal' | 'reinforce' | 'remedial'; skip_node_ids: string[]; updates: Array<Partial<NodePlan> & { node_id: string }>; inserted_nodes: NodePlan[]; reason: string }
export interface CompleteResponse { version: number; next_node: string | null; changes: PathState[]; diff: AdjustmentDiff }
export interface MapNode { id: string; label: string; detail: string; domainId: string; x: number; y: number; status: MeridianStatus; state: BackendPathState; free: boolean; expanded: boolean; custom?: boolean }
