export type DialogueQuestionChip = {
  id: string
  label: string
  quickReply: string
}

export type DialogueQuestionTemplate = {
  dimension: 'learning_goal' | 'knowledge_base' | 'learning_style' | 'motivation' | 'pace' | 'resource_preference'
  label: string
  questions: string[]
  quickReplies: string[]
}

export const dialogueQuestionTemplates: DialogueQuestionTemplate[] = [
  {
    dimension: 'learning_goal',
    label: '学习目标',
    questions: [
      '这轮学习你最想解决什么：考试提分、临床应用、科研研读，还是先建立兴趣？',
      '如果两周后能看到一个变化，你希望它具体是什么？',
    ],
    quickReplies: ['考试提分', '临床应用', '科研研读', '兴趣入门'],
  },
  {
    dimension: 'knowledge_base',
    label: '知识基础',
    questions: [
      '你现在大概到哪一步了：刚入门、能记药性功效，还是能做简单辨证配伍？',
      '随便举一个你熟悉的中药或方剂也行，我想听听你的基础落点。',
    ],
    quickReplies: ['刚入门', '学过药性', '能做辨证', '基础不稳'],
  },
  {
    dimension: 'learning_style',
    label: '学习风格',
    questions: [
      '平时你更吃哪种材料：图谱表格、条文教材、口诀歌诀，还是案例拆解？',
      '遇到陌生药材时，你通常先看图、读解释、背口诀，还是找医案？',
    ],
    quickReplies: ['图谱表格', '条文教材', '口诀歌诀', '案例拆解'],
  },
  {
    dimension: 'motivation',
    label: '动机强度',
    questions: [
      '这件事更像是你自己想弄明白，还是有考试、课程、实习之类的外部压力推着？',
      '什么时候你会觉得中医药学起来挺有劲？',
    ],
    quickReplies: ['自己想学', '考试推动', '实习需要', '先看看'],
  },
  {
    dimension: 'pace',
    label: '学习节奏',
    questions: [
      '接下来一两周，你每天大概能留多少分钟给这件事？',
      '你更适合每天短练，还是隔几天做一次完整复盘？',
    ],
    quickReplies: ['每天15分钟', '每天30分钟', '每天45分钟', '周末集中学'],
  },
  {
    dimension: 'resource_preference',
    label: '资源偏好',
    questions: [
      '如果给你一份学习材料，你会先打开知识图谱、短剧讲解、汤头歌诀，还是医案文选？',
      '哪种资源最容易让你愿意继续往下看？',
    ],
    quickReplies: ['知识图谱', '短剧讲解', '汤头歌诀', '医案文选'],
  },
]

export const dialogueQuestionChips: DialogueQuestionChip[] = [
  {
    id: 'exam-rush',
    label: '考前冲刺',
    quickReply: '我最近主要想提分，最好能直接抓重点和高频考点。',
  },
  {
    id: 'clinic-case',
    label: '病例推演',
    quickReply: '我更想跟着病例学，看看药性、配伍和辨证怎么落到临床。',
  },
  {
    id: 'herb-curious',
    label: '草木好奇',
    quickReply: '我喜欢从药材故事、形态和性味归经慢慢建立记忆。',
  },
  {
    id: 'deep-reading',
    label: '研读经典',
    quickReply: '我愿意读原文和教材，想把概念关系梳理清楚。',
  },
  {
    id: 'weak-points',
    label: '查漏补缺',
    quickReply: '先帮我找薄弱点，再安排一小段能完成的练习。',
  },
  {
    id: 'pace-plan',
    label: '定节奏',
    quickReply: '我想按每天30到45分钟推进，先用两周把重点章节过一遍。',
  },
]
