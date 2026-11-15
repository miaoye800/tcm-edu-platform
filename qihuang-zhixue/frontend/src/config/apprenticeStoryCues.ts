import type { ApprenticeScene } from './apprenticeStory'

// `speaker` 决定对话框上方那块名牌：老中医说的话写 '老中医'，旁白/心里话写 '旁白'。
export type StoryCue = { at: number; text: string; speaker?: string }

// 对话框当前该显示的一句话。
export type StoryLine = { text: string; speaker: string }

// 每段视频的台词字幕：`at` = 这句话在视频里开始的时间（秒），数值取自成片字幕。
//
// 规则：
//   1. 只有「真的说出口/写下来」的话才写在这里，题目与旁白解释不放进来。
//   2. `at` 一旦过了就切换，直到下一句；第一句之前回落到场景自带的 dialogue。
//   3. 没有列出的场景会整段回落到 dialogue，不会报错。
//   4. 场景自带的 dialogue 必须等于它这一拍的第一句 cue。切拍那一瞬间 cue 还没
//      追上时，对话框先显示 dialogue；两者不一致就会看到「一句话闪一下又换掉」，
//      也就是两句台词抢同一个节拍。`prologue beat lines agree with the cue table`
//      这条测试盯着它。
//   5. 视频开头到第一句台词之间留白时，用一句旁白（带「（）」的动作提示）占住这段
//      时间，不要提前把老中医的话抖出来——他还没开口。
//
// 台词以成片烧进画面的字幕为准：秒数取字幕卡出现的那一帧（4K 素材重新量过），
// 一句话太长、字幕分了两三张卡时，对话框按卡片边界拆行，不自己造节奏。
//
// 只有一处例外：字幕本身写错的词，框里用正确的写法，并在注释里记下来，因为
// 画面已经改不动了——`配胡` 应为 `配伍`（第三段），`相学` 应为 `向学`（序章）。
// 反过来，字幕写对而口述稿记岔的（`趁手` 不是 `称手`），按字幕。
const prologueCues: StoryCue[] = [
  { at: 0, text: '鼻间先有陈皮与艾草的微苦。你在一间陌生的中药房醒来。', speaker: '旁白' },
  { at: 4, text: '眼前的景象渐渐远去——你开始想起，自己是如何在雨后的山阶上受伤的。', speaker: '旁白' },
  { at: 14, text: '孩子快醒醒', speaker: '老中医' },
  { at: 16.5, text: '快醒醒', speaker: '老中医' },
  { at: 21, text: '我观你眼中有向学之光', speaker: '老中医' },
  { at: 24.5, text: '可愿随我习这岐黄之术', speaker: '老中医' },
]

export const apprenticeStoryCues: Record<string, StoryCue[]> = {
  // section-one 是一镜到底的序章，三个节拍共用同一张台词表。
  'wake-apothecary': prologueCues,
  'injury-memory': prologueCues,
  'meet-master': prologueCues,

  // section-two：第 6 秒他才开口问药（字幕卡：6「小友，」/ 8「先识药再辨性」/ 12「这几味药你可认得」）。
  'herb-dialogue': [
    { at: 0, text: '（老中医把几味药摊在案上，等你凑近些。）', speaker: '旁白' },
    { at: 6, text: '小友，先识药，再辨性，这几味药你可认得', speaker: '老中医' },
  ],

  // section-three：一开口就在报病例（字幕卡 0/2/5/8/9/11/13 共七张，按语义并成三行）。
  'prescription-counter': [
    { at: 0, text: '病人印堂发黑，有点畏寒', speaker: '老中医' },
    { at: 5, text: '头部发晕，数盲', speaker: '老中医' },
    { at: 9, text: '照方抓药，剂量与配伍，皆马虎不得', speaker: '老中医' },
  ],

  // section-four：第 10 秒才说到「挑一件带走」（字幕卡 10「挑一件趁手的法门带走」/ 13「自有用处」）。
  'choose-keepsake': [
    { at: 0, text: '（架上的药宝不多，各是一路走法。）', speaker: '旁白' },
    { at: 10, text: '挑一件趁手的法门带走，日后自有用处', speaker: '老中医' },
  ],

  // section-five：第 3 秒点题，第 6 秒交代功课（字幕卡 3 / 6）。
  'make-rule': [
    { at: 0, text: '（灯下摊着一张空白的功课纸。）', speaker: '旁白' },
    { at: 3, text: '学医贵在有恒', speaker: '老中医' },
    { at: 6, text: '你且定下每日功课与目标', speaker: '老中医' },
  ],

  // section-six：第 2 秒礼成，第 4 秒交代画像，第 7 秒交代它会生长
  // （字幕卡 2「拜师礼成」/ 4「此乃你此刻之画像」/ 7「随你修行，自会不断生长」）。
  // 三句必须各占一拍：前两句挤在一行时，第 4 秒才抬头看的人会以为礼还没成。
  'apprentice-complete': [
    { at: 0, text: '（灯影摇了一下，他搁下手中的笔。）', speaker: '旁白' },
    { at: 2, text: '拜师礼成', speaker: '老中医' },
    { at: 4, text: '此乃你此刻之画像', speaker: '老中医' },
    { at: 7, text: '随你修行，自会不断生长', speaker: '老中医' },
  ],
}

// 视频播完、进入作答时，用旁白语气告诉用户「现在要做什么」。
// 收徒礼成那一屏不需要这一条：它没有作答，对话框直接停在老中医最后一句上。
export const apprenticeStoryNarration: Record<string, string> = {
  'master-offer': '（四张身份牌摆在案上。挑一张最像你走过的路，也可以点「自己说说」。）',
  'prescription-counter': '（先点「记下病例」，再点「落笔写方」；两样都写好了，「落笔呈上」才会亮。）',
  'choose-keepsake': '（架上药宝不多。挑一件你愿意常常翻看的带走。）',
  'make-rule': '（定下每天练多久、先走多少天，两样都填好就能立约。）',
}

// 视频播放期间显示的那一句：取「已经说过的那一句」，并带上说话人。
export const dialogueAt = (scene: ApprenticeScene, seconds: number): StoryLine => {
  const fallback: StoryLine = { text: scene.dialogue, speaker: scene.speaker ?? '旁白' }
  const cues = apprenticeStoryCues[scene.sceneId]
  if (!cues?.length) return fallback
  let line = fallback
  for (const cue of cues) {
    if (seconds < cue.at) break
    line = { text: cue.text, speaker: cue.speaker ?? fallback.speaker }
  }
  return line
}
