/**
 * 渲染层的「presence」：把后端的 6 个路线状态压成 7 个视觉态。
 *
 * 为什么不让后端直接给：状态回答的是「这一关排在哪」（locked / current / completed…），
 * 而画面要回答的是「这个穴位现在长什么样」。两者不是一一对应——
 * 一个穴位挂 2–3 小节时，"学了一半"既不是 completed 也不是 locked，只能叫 `split`。
 *
 * 一穴挂多节时的 `split`（计划 §10.5 第 7 态）不是装饰：中药学 75% 的穴位槽位被占用，
 * 用户点到「麻黄」很可能看到「解表药·辛温」学完、「应用」没学，用单一金色会撒谎。
 */
import type { BackendPathState } from '../../types/path'
import type { MeridianAtlas, MeridianSection } from '../../services/meridianApi'

export type Presence = 'planned' | 'active' | 'current' | 'lit' | 'deferred' | 'split' | 'dormant'

export interface AcupointPresence {
  id: string
  presence: Presence
  completed: number
  total: number
  ratio: number
  section_ids: string[]
}

export interface SectionProgress {
  section: MeridianSection
  presence: Presence
  completed: number
  total: number
  ratio: number
}

const toPresence = (status: BackendPathState | undefined): Presence => {
  if (status === 'current') return 'current'
  if (status === 'completed') return 'lit'
  if (status === 'paused') return 'deferred'
  if (status === 'available' || status === 'custom') return 'active'
  return 'planned'
}

const aggregate = (
  parts: Presence[],
  completed: number,
  total: number,
): Presence => {
  if (!parts.length || total === 0) return 'dormant'
  if (parts.includes('current')) return 'current'
  if (parts.includes('deferred') && completed * 2 < total) return 'deferred'
  if (parts.includes('lit') || completed > 0) {
    if (completed >= total) return 'lit'
    return 'split'
  }
  if (parts.includes('active')) return 'active'
  return 'planned'
}

/**
 * 按穴位归组小节。
 *
 * ``sections`` 可传一份**筛选过的**子集（比如"只看中药学"）：没被覆盖的穴位在
 * `buildPresence` 里会落到 `dormant`，也就是淡下去——这就是学科筛选的实现方式，
 * 不需要在渲染层再加一套"变暗"通道。
 */
export const sectionsByAcupoint = (
  atlas: MeridianAtlas,
  sections: MeridianSection[] = atlas.layer?.sections ?? [],
): Map<string, MeridianSection[]> => {
  const map = new Map<string, MeridianSection[]>()
  for (const section of sections) {
    const list = map.get(section.acupoint_id)
    if (list) list.push(section)
    else map.set(section.acupoint_id, [section])
  }
  return map
}

export const buildPresence = (
  atlas: MeridianAtlas,
  states: Map<string, BackendPathState>,
  sections: MeridianSection[] = atlas.layer?.sections ?? [],
): Map<string, AcupointPresence> => {
  const grouped = sectionsByAcupoint(atlas, sections)
  const out = new Map<string, AcupointPresence>()

  for (const acupoint of atlas.acupoints) {
    const sections = grouped.get(acupoint.id) ?? []
    const marks: Presence[] = []
    const sectionIds: string[] = []
    let completed = 0
    let total = 0

    for (const section of sections) {
      sectionIds.push(section.id)
      for (const tick of section.points) {
        total += 1
        const presence = toPresence(states.get(tick.id))
        if (presence === 'lit') completed += 1
        marks.push(presence)
      }
    }

    out.set(acupoint.id, {
      id: acupoint.id,
      presence: sections.length ? aggregate(marks, completed, total) : 'dormant',
      completed,
      total,
      ratio: total ? completed / total : 0,
      section_ids: sectionIds,
    })
  }
  return out
}

export const buildSectionProgress = (
  atlas: MeridianAtlas,
  states: Map<string, BackendPathState>,
): Map<string, SectionProgress> => {
  const out = new Map<string, SectionProgress>()
  for (const section of atlas.layer?.sections ?? []) {
    const marks: Presence[] = []
    let completed = 0
    for (const tick of section.points) {
      const presence = toPresence(states.get(tick.id))
      if (presence === 'lit') completed += 1
      marks.push(presence)
    }
    const total = section.points.length
    out.set(section.id, {
      section,
      presence: aggregate(marks, completed, total),
      completed,
      total,
      ratio: total ? completed / total : 0,
    })
  }
  return out
}

export const presenceLabel: Record<Presence, string> = {
  planned: '待学',
  active: '已排入路线',
  current: '正在学',
  lit: '已点亮',
  deferred: '已暂缓',
  split: '部分点亮',
  dormant: '未挂章节',
}

export const presenceOfNode = (status: BackendPathState | undefined): Presence => toPresence(status)
