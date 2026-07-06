import {
  ChatDotRound,
  Compass,
  Connection,
  DataAnalysis,
  DataBoard,
  Film,
  FirstAidKit,
  HomeFilled,
  MagicStick,
  MapLocation,
  Notebook,
  Timer,
  UserFilled,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'

export type MenuItem = {
  icon: Component
  label: string
  route: string
  badge?: string | number
  assistantTrigger?: boolean
  disabled?: boolean
}

export type MenuGroup = {
  group: string
  items: MenuItem[]
}

export const menuGroups: MenuGroup[] = [
  {
    group: '学习',
    items: [
      { icon: HomeFilled, label: '总览仪表盘', route: '/dashboard' },
      { icon: UserFilled, label: '弟子档案', route: '/profile' },
      { icon: ChatDotRound, label: '智能学习', route: '/learn' },
      { icon: MapLocation, label: '闯关地图', route: '/path' },
    ],
  },
  {
    group: '评测',
    items: [
      { icon: DataAnalysis, label: '题库测评', route: '/quiz' },
      { icon: Notebook, label: '错题本', route: '/mistakes', badge: 3 },
      { icon: Timer, label: '复习计划', route: '/review' },
    ],
  },
  {
    group: '图谱',
    items: [{ icon: Connection, label: '中药图谱', route: '/atlas' }],
  },
  {
    group: '互动',
    items: [
      { icon: Film, label: '漫剧剧场', route: '/theater', badge: 'soon', disabled: true },
      { icon: FirstAidKit, label: '开药游戏', route: '/clinic', badge: 'soon', disabled: true },
    ],
  },
  {
    group: '浮层',
    items: [{ icon: MagicStick, label: '学习助手·小岐', route: '/assistant', assistantTrigger: true }],
  },
]

export const findMenuLabel = (path: string): string => {
  const item = menuGroups.flatMap((group) => group.items).find((entry) => entry.route === path)
  return item?.label ?? '岐黄智学'
}
