import type { ProfileSessionStep } from '../types/profile'

export type ApprenticeSceneKind = 'performance' | 'identity' | 'quiz' | 'prescription' | 'resource' | 'rule' | 'complete'

export type ApprenticeScene = {
  sceneId: string
  kind: ApprenticeSceneKind
  title: string
  chapter: string
  speaker?: string
  dialogue: string
  canSkip: boolean
  requiredStepId?: string
}

// Narrative data deliberately has no media paths. See apprenticeStoryAssets.ts
// for independently replaceable video, poster, duration, and fallback layers.
export const apprenticeScenes: ApprenticeScene[] = [
  { sceneId: 'wake-apothecary', kind: 'performance', title: '药香醒转', chapter: '序 · 晨雾', dialogue: '鼻间先有陈皮与艾草的微苦。你在一间陌生的中药房醒来。', canSkip: true },
  { sceneId: 'injury-memory', kind: 'performance', title: '回忆断片', chapter: '序 · 雨阶', dialogue: '湿滑的青石阶在眼前倾斜，疼痛与雨声同时坠下。', canSkip: true },
  { sceneId: 'meet-master', kind: 'performance', title: '再度睁眼', chapter: '序 · 灯火', speaker: '老中医', dialogue: '醒了？山中湿滑，你伤得不轻。是我把你带回药房，煎药守了半夜。', canSkip: true },
  { sceneId: 'master-offer', kind: 'identity', title: '叩问初心', chapter: '一 · 收徒', speaker: '老中医', dialogue: '既然醒来，便说说你为何靠近岐黄。先择一张旧时身份，让我知你从何处来。', canSkip: false, requiredStepId: 'apprentice_prologue' },
  { sceneId: 'herb-dialogue', kind: 'quiz', title: '药圃问答', chapter: '二 · 识药', speaker: '老中医', dialogue: '辨药不为逞强，只为临证时少一分迟疑。你看见什么，便如实作答。', canSkip: false, requiredStepId: 'apprentice_act1_quiz' },
  { sceneId: 'prescription-counter', kind: 'prescription', title: '初次抓药', chapter: '三 · 开方', speaker: '老中医', dialogue: '柜上有秤、有纸。依病例写下你的抓药方，方中每一味都会按现有知识库留下可追溯的记录。', canSkip: false, requiredStepId: 'apprentice_act2_prescription' },
  { sceneId: 'choose-keepsake', kind: 'resource', title: '带走药宝', chapter: '四 · 传承', speaker: '老中医', dialogue: '学医不必只循一条路。选一件你愿意常常翻看的药宝。', canSkip: false, requiredStepId: 'apprentice_act3_resource' },
  { sceneId: 'make-rule', kind: 'rule', title: '立下学规', chapter: '五 · 入门', speaker: '老中医', dialogue: '师徒之约，贵在可行。把你愿意守住的节奏写下来。', canSkip: false, requiredStepId: 'apprentice_act4_rule' },
  { sceneId: 'apprentice-complete', kind: 'complete', title: '收徒礼成', chapter: '终 · 灯明', speaker: '老中医', dialogue: '今日所答皆已留作证据。档案卷会随日后所学慢慢显影。', canSkip: false },
]

export const apprenticeVisual = {
  sceneTransitionMs: 240,
  autoPerformanceMs: 5200,
  storagePrefix: 'qihuang-apprentice-safe-point:',
} as const

export const sceneForStep = (step: ProfileSessionStep | null): ApprenticeScene => {
  const matched = apprenticeScenes.find((scene) => scene.requiredStepId === step?.id)
  return matched ?? apprenticeScenes[apprenticeScenes.length - 1]
}
