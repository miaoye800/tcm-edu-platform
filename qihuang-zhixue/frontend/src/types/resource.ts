export type ResourceType = 'doc' | 'graph' | 'mindmap' | 'quiz' | 'story'
export type ResourceStatus = 'generating' | 'ready' | 'failed'

export interface ResourceSource { [key: string]: string | undefined; source?: string; title?: string; quote?: string; ref?: string; '出处'?: string; '原文摘录'?: string; '定位'?: string }
export interface ResourceItem { id: string; node_id: string; type: ResourceType; title: string; content?: Record<string, unknown>; sources?: ResourceSource[]; notes?: string; status: ResourceStatus; created_at: string; favorited?: boolean; meta?: Record<string, unknown> }
export interface ResourceListResponse { items: ResourceItem[]; total: number; offset: number; limit: number }
export type DocBlockType = 'heading' | 'paragraph' | 'bullet' | 'table' | 'callout' | 'quote'
export interface ResourceDocBlock { type: DocBlockType | string; text: string }
/** Back end emits body citations as zero-based indexes, such as [0] for sources[0]. */
export interface ResourceTaskPayload { step_id?: string; resource_id?: string; message?: string; index?: number; block?: ResourceDocBlock }
export interface ResourceTaskEvent { event: 'step_start' | 'step_done' | 'block' | 'token' | 'done' | 'error'; step_index: number; total_steps: number; step_label: string; payload: ResourceTaskPayload }
export interface ResourceFilters { type?: ResourceType; nodeId?: string; favorited?: boolean; q?: string }

export const displayCitation = (zeroBased: number) => zeroBased + 1
/** Table blocks arrive as Markdown text; malformed or non-table text intentionally falls back to a paragraph. */
export const parseMarkdownTable = (text: string): string[][] => text.split(/\r?\n/).map(line => line.trim()).filter(line => line.startsWith('|')).filter((line, index) => index !== 1 || !/^\|?\s*:?-{3,}/.test(line.split('|').join('').trim())).map(line => line.replace(/^\||\|$/g, '').split('|').map(cell => cell.trim()))
