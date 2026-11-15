/** 助手消息与事件的类型。与后端 `app/assistant/contract.py` 一一对应。 */

import type { ResourcePlan } from './resource'

export type ToolState = 'running' | 'ok' | 'failed'

export interface ToolStep {
  call_id: string
  tool: string
  capability: string
  title?: string
  args?: Record<string, unknown>
  state: ToolState
  summary?: string
  error_code?: string
  remedy?: string
  task_id?: string
}

export interface PlanCardPayload {
  call_id: string
  task_id: string
  capability: string
  kind: string
  plan: ResourcePlan
  design: Record<string, unknown>
}

export interface ArtifactPayload {
  call_id: string
  task_id: string
  capability: string
  kind: string
  resource_id?: string
  open_url?: string
  verify?: Record<string, unknown>
}

export type AssistantMessageContent =
  | { type: 'text'; text: string; steps?: ToolStep[]; ui_actions?: ActionLink[] }
  | { type: 'tool'; steps: ToolStep[]; text?: string }
  | { type: 'plan'; taskId: string; capability: string; plan: ResourcePlan; design: Record<string, unknown>; confirmed?: boolean }
  | { type: 'artifact'; taskId: string; capability: string; kind: string; title?: string; resourceId?: string; openUrl?: string; verify?: Record<string, unknown> }
  | { type: 'error'; text: string; steps?: ToolStep[] }

export interface AssistantMessage {
  message_id: string
  run_id?: string
  role: 'user' | 'assistant' | 'tool'
  content: AssistantMessageContent
  created_at: string
}

/** 消息流里的一条。用户消息与助手消息同一个形状，`content` 分派渲染。 */
/** 消息里的可点动作。**不自动跳页**，点不点由用户决定。 */
export interface ActionLink {
  label: string
  url: string
}

export interface ChatItem {
  id: string
  role: 'user' | 'assistant'
  content: AssistantMessageContent
  /** 这一条触发的工具调用。放在消息上而不是 content 里：流式追加时改的是它。 */
  steps?: ToolStep[]
  /** 流式追加中，用来决定是否显示光标。 */
  streaming?: boolean
  created_at?: string
}

export interface ConversationSummary {
  conversation_id: string
  title: string
  status: string
  created_at: string
  updated_at: string
}

export interface ArtifactSummary {
  artifact_id: string
  run_id: string
  call_id: string
  capability: string
  task_id: string
  resource_id: string
  kind: string
  open_url: string
  state: 'accepted' | 'awaiting_plan' | 'done' | 'failed'
  created_at: string
}

export interface ConversationDetail extends ConversationSummary {
  messages: AssistantMessage[]
  artifacts: ArtifactSummary[]
}

export interface AssistantEvent {
  event: string
  step_index: number
  total_steps: number
  step_label: string
  payload: Record<string, unknown>
}

/** 工具行的工序进度。存在视图层，按 call_id 索引。 */
export interface StepProgress {
  index: number
  total: number
  label: string
}

export interface ChatTurnPayload {
  conversation_id?: string
  text: string
  client_turn_id?: string
  refs?: string[]
}
