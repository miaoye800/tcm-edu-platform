import type { DimensionKey, IdentityDimensionCard, IdentityCardKind } from '../../types/profile'

type DimensionDescription = {
  label: string
  dimension: string
  labels: Record<string, string>
  levels?: Record<string, string>
  fallback: string
}

export const identityCardDescriptions: Record<DimensionKey, DimensionDescription> = {
  learning_goal: {
    label: '学习目标',
    dimension: '学习目标说明这轮学习最想解决什么问题。',
    labels: {
      应试提分: '应试提分 = 目标明确，优先围绕考试范围、错题和高频考点学习。',
      临床实用: '临床实用 = 希望把药性、证候和处方变化用于真实病例理解。',
      兴趣科普: '兴趣科普 = 先由好奇心进入，适合故事、图像和生活化解释。',
      学术深造: '学术深造 = 重视原理、出处和体系，适合深入研读与概念辨析。',
      其他: '其他 = 用户表达了更个人化的目标，暂不强行归入固定类别。',
    },
    fallback: '这张牌表示当前学习目标中的一个候选标签。',
  },
  motivation: {
    label: '动机',
    dimension: '动机说明推动学习的主要力量来自哪里。',
    labels: {
      求道者: '动机·求道者 = 内驱：出于兴趣与自我要求学习。',
      赶考人: '动机·赶考人 = 外驱：被考试、任务或现实压力推动。',
      双修者: '动机·双修者 = 内外混合：既有兴趣，也有明确外部目标。',
      随缘客: '动机·随缘客 = 被动：暂时没有强目标，需要更轻量的引导。',
      内驱: '动机·内驱 = 出于兴趣、成就感或自我要求学习。',
      外驱: '动机·外驱 = 主要被考试、任务、实习或他人期待推动。',
      内外混合: '动机·内外混合 = 兴趣和外部目标同时存在。',
      被动: '动机·被动 = 还在试探阶段，需要降低启动门槛。',
    },
    fallback: '这张牌表示当前学习动机的一个候选标签。',
  },
  resource_preference: {
    label: '资源偏好',
    dimension: '资源偏好说明更适合用哪种学习材料打开知识。',
    labels: {
      观脉者: '资源·观脉者 = 偏好知识图谱，适合先看结构关系。',
      入戏郎: '资源·入戏郎 = 偏好拟人短剧，适合用情境记住药性。',
      诵诗生: '资源·诵诗生 = 偏好汤头歌诀，适合用节奏和口诀记忆。',
      读案客: '资源·读案客 = 偏好医案文选，适合在病例里理解辨证。',
      知识图谱: '资源·知识图谱 = 先看结构、分类和关联。',
      拟人化短剧: '资源·拟人化短剧 = 用故事和角色帮助记忆。',
      汤头歌诀: '资源·汤头歌诀 = 用口诀、韵律和复诵巩固。',
      医案文选: '资源·医案文选 = 从真实案例与处方变化切入。',
    },
    fallback: '这张牌表示当前资源偏好中的一种材料形态。',
  },
  learning_style: {
    label: '学习风格',
    dimension: '学习风格说明信息以什么形式出现时更容易吸收。',
    labels: {
      观象者: '风格·观象者 = 视觉型：图表、颜色和关系图更容易进入记忆。',
      研文客: '风格·研文客 = 文字型：条文、定义和原文脉络更稳定。',
      记韵家: '风格·记韵家 = 记忆/口诀型：韵律、复诵和口头线索更有效。',
      视觉型: '风格·视觉型 = 通过图像、结构图和空间关系学习。',
      文字型: '风格·文字型 = 通过文本、笔记和概念边界学习。',
      '记忆(口诀)型': '风格·记忆(口诀)型 = 通过口诀、歌诀和反复背诵学习。',
    },
    fallback: '这张牌表示一种可能更适合你的学习呈现方式。',
  },
  knowledge_base: {
    label: '知识基础',
    dimension: '知识基础说明当前中医药相关知识的起点。',
    labels: {},
    levels: {
      药童: '知识基础·药童 = 仍在入门，需要先建立核心概念。',
      学徒: '知识基础·学徒 = 有基本接触，适合从主干知识补齐。',
      坐堂医: '知识基础·坐堂医 = 已有一般基础，可以进入辨析和应用。',
      杏林手: '知识基础·杏林手 = 基础较好，适合更系统地串联证候与药性。',
      大医: '知识基础·大医 = 熟练度高，适合高阶病例、边界与迁移。',
      待取证: '知识基础·待取证 = 还缺少足够证据，暂不下判断。',
    },
    fallback: '这张牌表示知识基础等级与当前分数。',
  },
  pace: {
    label: '学习节奏',
    dimension: '学习节奏说明每天投入和目标周期是否匹配。',
    labels: {},
    levels: {
      文火慢炖: '学习节奏·文火慢炖 = 时间较少或周期较松，适合低压持续。',
      温火常煎: '学习节奏·温火常煎 = 节奏中等，适合稳定推进和阶段复盘。',
      武火急煎: '学习节奏·武火急煎 = 时间投入高或周期紧，适合冲刺和高频反馈。',
      待取证: '学习节奏·待取证 = 还缺少每日时长或目标周期证据。',
    },
    fallback: '这张牌表示学习节奏等级与当前分数。',
  },
  weak_points: {
    label: '薄弱点',
    dimension: '薄弱点记录用户亲口提到的卡点、易错点和不确定点。',
    labels: {
      药性功效: '薄弱点·药性功效 = 对药物性味、功效或主治容易混淆。',
      配伍剂量: '薄弱点·配伍剂量 = 对配伍关系、剂量变化或禁忌不够稳。',
      辨证思路: '薄弱点·辨证思路 = 在证候判断、病机推理上容易卡住。',
      背诵记忆: '薄弱点·背诵记忆 = 歌诀、归经、功效等记忆保持困难。',
    },
    fallback: '这张牌表示用户已经提到、值得优先补强的薄弱点。',
  },
}

export const dimensionLabelFor = (key: DimensionKey): string => identityCardDescriptions[key]?.label ?? key

export const identityCardDescriptionFor = (
  dimension: DimensionKey,
  kind: IdentityCardKind,
  card: IdentityDimensionCard,
): string => {
  const meta = identityCardDescriptions[dimension]
  const label = String(card.display_name || card.level_name || card.label || card.canonical_label || '').trim()
  if (kind === 'level' && label && meta.levels?.[label]) return meta.levels[label]
  if (label && meta.labels[label]) return meta.labels[label]
  const canonical = String(card.canonical_label || '').trim()
  if (canonical && meta.labels[canonical]) return meta.labels[canonical]
  if (kind === 'tags' && label) return `薄弱点·${label} = ${meta.fallback}`
  return `${meta.dimension}${meta.fallback ? ` ${meta.fallback}` : ''}`
}
