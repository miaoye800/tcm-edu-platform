export type MeridianNodeStatus = 'recommended' | 'current' | 'mastered' | 'free' | 'deferred' | 'exam_hot' | 'custom'

export interface MeridianPoint {
  id: string
  label: string
  detail?: string
  x: number
  y: number
  status: MeridianNodeStatus
  labelSide?: 'left' | 'right'
  color?: string
}

export const meridianStatusStyles: Record<MeridianNodeStatus, { label: string; description: string }> = {
  recommended: { label: '推荐路线', description: '玉色双描边，强调推荐顺序' },
  current: { label: '当前学习', description: '玉色呼吸光晕，核心常亮' },
  mastered: { label: '已达标', description: '玉色点亮，主干承接气血流动' },
  free: { label: '顺序自由', description: '蓝色虚线外圈，表示可自由选择' },
  deferred: { label: '暂缓', description: '低饱和灰化，降低存在感' },
  exam_hot: { label: '考研高频', description: '金色描边与内核，优先提示' },
  custom: { label: '自定义', description: '用户色标记，保留个人路径语义' },
}
