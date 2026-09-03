import type { ResourceType } from './resource'

export interface ImmersiveNode { id: string; label: string; detail?: string; stage?: number }
export interface ImmersiveSession { session_id: string; greeting: string; chips: string[]; node: { id?: string; node_id?: string; name?: string; label?: string; stage?: number } }
export interface ImmersiveRecommendation { type: ResourceType; reason: string; params: Record<string, unknown> }
export interface ImmersiveChatEvent { event: 'token' | 'recommendation' | 'done' | 'error'; text?: string; recommendation?: ImmersiveRecommendation[]; message?: string }
