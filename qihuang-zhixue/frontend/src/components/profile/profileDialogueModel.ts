export const identityCardNames = ['考前冲刺', '临床学徒', '草木好奇者', '研读派'] as const

export type IdentityCardName = (typeof identityCardNames)[number]
export type CardScores = Partial<Record<IdentityCardName | string, number>>

export type IdentityCardMeta = {
  name: IdentityCardName
  seal: string
  tone: 'gold' | 'jade' | 'blue' | 'coral'
  summary: string
  sketch: string
}

export type AnswerPayload = { utterance: string } | { quick_reply: string }
export type ConfirmPayload = { identity_card: IdentityCardName | string | null }

export const identityCards: IdentityCardMeta[] = [
  {
    name: '考前冲刺',
    seal: '冲',
    tone: 'gold',
    summary: '目标清晰，偏爱短程任务、高频考点和即时反馈。',
    sketch: '适合用倒计时、错题热区和小测闭环，把注意力收束到最能提分的地方。',
  },
  {
    name: '临床学徒',
    seal: '诊',
    tone: 'jade',
    summary: '喜欢从证候、病例和处方变化里理解药性。',
    sketch: '适合跟着病例走，用辨证线索串起功效、配伍、禁忌和剂量取舍。',
  },
  {
    name: '草木好奇者',
    seal: '草',
    tone: 'blue',
    summary: '对药材来源、形态故事和感官记忆更敏锐。',
    sketch: '适合先看图像和故事，再把性味归经、功效主治落成稳定记忆点。',
  },
  {
    name: '研读派',
    seal: '读',
    tone: 'coral',
    summary: '愿意深读教材与原文，重视体系和概念边界。',
    sketch: '适合用章节脉络、概念对照和摘记复述，把知识网织得更密。',
  },
]

export const normalizeCardScore = (value: unknown): number => {
  const score = Number(value)
  if (!Number.isFinite(score)) return 0
  if (score <= 1) return Math.round(Math.min(1, Math.max(0, score)) * 100)
  return Math.round(Math.min(100, Math.max(0, score)))
}

export const cardScoreFor = (cardName: string, cardScores: CardScores): number => normalizeCardScore(cardScores[cardName])

export const cardIsFlipped = (cardName: string, cardScores: CardScores): boolean => cardScoreFor(cardName, cardScores) > 0

export const bestScoredCard = (cardScores: CardScores): IdentityCardName => {
  return identityCards.reduce(
    (best, card) => (cardScoreFor(card.name, cardScores) > cardScoreFor(best.name, cardScores) ? card : best),
    identityCards[0],
  ).name
}

export const createAnswerPayload = (kind: 'utterance' | 'quick_reply', value: string): AnswerPayload =>
  kind === 'utterance' ? { utterance: value } : { quick_reply: value }

export const createConfirmPayload = (identityCard: IdentityCardName | string | null): ConfirmPayload => ({
  identity_card: identityCard,
})
