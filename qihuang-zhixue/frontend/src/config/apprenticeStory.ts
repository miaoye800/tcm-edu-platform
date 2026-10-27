import type { ProfileSessionStep } from '../types/profile'

export type ApprenticeSceneKind = 'performance' | 'identity' | 'quiz' | 'prescription' | 'resource' | 'rule' | 'complete'

export type ApprenticeScene = {
  sceneId: string
  kind: ApprenticeSceneKind
  title: string
  chapter: string
  speaker?: string
  dialogue: string
  requiredStepId?: string
}

// Narrative data deliberately has no media paths. See apprenticeStoryAssets.ts
// for independently replaceable video, poster, duration, and fallback layers.
//
// The prologue's three beats ride one continuous shot (section-one). Each beat's
// `dialogue` is the line that beat opens with, and it must match the first cue of
// that beat in apprenticeStoryCues.ts - the box falls back to this string for the
// instant before the first `timeupdate` lands, and a mismatch there showed two
// different lines fighting over the same beat.
//
// The same rule holds for every later scene: `dialogue` is the 0-second line of
// that shot. Where the master has not spoken yet at 0s, the line is a stage
// direction in brackets attributed to the narrator, so the box never puts words
// in his mouth before the picture does.
export const apprenticeScenes: ApprenticeScene[] = [
  { sceneId: 'wake-apothecary', kind: 'performance', title: '药香醒转', chapter: '序 · 晨雾', dialogue: '鼻间先有陈皮与艾草的微苦。你在一间陌生的中药房醒来。' },
  { sceneId: 'injury-memory', kind: 'performance', title: '回忆断片', chapter: '序 · 雨阶', dialogue: '眼前的景象渐渐远去——你开始想起，自己是如何在雨后的山阶上受伤的。' },
  { sceneId: 'meet-master', kind: 'performance', title: '再度睁眼', chapter: '序 · 灯火', speaker: '老中医', dialogue: '孩子快醒醒' },
  { sceneId: 'master-offer', kind: 'identity', title: '了解学情', chapter: '一 · 学情', speaker: '老中医', dialogue: '入门之前，我想先了解你走过的学习路：你曾学过什么，又希望从岐黄之术中得到什么？', requiredStepId: 'apprentice_prologue' },
  { sceneId: 'herb-dialogue', kind: 'quiz', title: '药圃问答', chapter: '二 · 识药', dialogue: '（老中医把几味药摊在案上，等你凑近些。）', requiredStepId: 'apprentice_act1_quiz' },
  { sceneId: 'prescription-counter', kind: 'prescription', title: '初次抓药', chapter: '三 · 开方', speaker: '老中医', dialogue: '病人印堂发黑，有点畏寒', requiredStepId: 'apprentice_act2_prescription' },
  { sceneId: 'choose-keepsake', kind: 'resource', title: '带走药宝', chapter: '四 · 传承', dialogue: '（架上的药宝不多，各是一路走法。）', requiredStepId: 'apprentice_act3_resource' },
  { sceneId: 'make-rule', kind: 'rule', title: '立下学规', chapter: '五 · 入门', dialogue: '（灯下摊着一张空白的功课纸。）', requiredStepId: 'apprentice_act4_rule' },
  { sceneId: 'apprentice-complete', kind: 'complete', title: '收徒礼成', chapter: '终 · 灯明', dialogue: '（灯影摇了一下，他搁下手中的笔。）' },
]

export const apprenticeVisual = {
  autoPerformanceMs: 5200,
  replyCommitMs: 420,
  storagePrefix: 'qihuang-apprentice-safe-point:',
} as const

// The quiz asks about a herb the screen never names: the disciple has to read the
// photograph first, then pick the effect. Keep it a plain string - it deliberately
// has no variables, so nothing can interpolate the herb name back in. It also
// doubles as the choice group's aria-label, so it must stand on its own.
export const apprenticeQuizPrompt = '你且细看这味药，它最见长的功效是哪一样？'

export const sceneForStep = (step: ProfileSessionStep | null): ApprenticeScene => {
  // Guard the null case explicitly: scenes without a bound step have an
  // undefined `requiredStepId`, which would otherwise match `step?.id` and
  // silently resolve to the prologue.
  const matched = step ? apprenticeScenes.find((scene) => scene.requiredStepId === step.id) : undefined
  return matched ?? apprenticeScenes[apprenticeScenes.length - 1]
}
