import {
  Compass,
  Connection,
  DataAnalysis,
  DataBoard,
  Document,
  Film,
  ChatLineSquare,
  FirstAidKit,
  HomeFilled,
  MagicStick,
  MapLocation,
  Notebook,
  Postcard,
  Refresh,
  Share,
  Timer,
  UserFilled,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'

export type MenuItem = {
  icon: Component
  label: string
  route: string
  dynamicLabel?: 'profileOnboarding'
  /** 角标由真实数据算出来，不能写死数字——写死就等于对学生撒谎。 */
  dynamicBadge?: 'mistakeCount'
  badge?: string | number
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
      { icon: Refresh, label: '焕醒画像', dynamicLabel: 'profileOnboarding', route: '/onboarding' },
      { icon: MapLocation, label: '闯关地图', route: '/path' },
      { icon: MagicStick, label: '我的资源库', route: '/resources' },
    ],
  },
  {
    group: '评测',
    items: [
      { icon: DataAnalysis, label: '题库测评', route: '/quiz' },
      { icon: Notebook, label: '错题本', route: '/mistakes', dynamicBadge: 'mistakeCount' },
      { icon: Timer, label: '复习计划', route: '/review' },
    ],
  },
  {
    group: '图谱',
    items: [
      { icon: Connection, label: '中药图谱', route: '/atlas' },
      { icon: Share, label: '导图工坊', route: '/mindmap' },
      { icon: DataBoard, label: '两药对比', route: '/compare' },
      { icon: FirstAidKit, label: '处方审查', route: '/prescription' },
      { icon: ChatLineSquare, label: '图谱问答', route: '/kg-ask' },
    ],
  },
  {
    group: '创作',
    items: [
      { icon: Postcard, label: '幻灯工坊', route: '/ppt' },
      { icon: Document, label: '文档工坊', route: '/doc-studio' },
    ],
  },
  {
    group: '互动',
    items: [
      { icon: Film, label: '漫剧剧场', route: '/theater', badge: 'soon', disabled: true },
      { icon: FirstAidKit, label: '开药游戏', route: '/clinic', badge: 'soon', disabled: true },
    ],
  },
  {
    group: '助手',
    items: [{ icon: MagicStick, label: 'AI 服务助手', route: '/assistant' }],
  },
]

export const findMenuLabel = (path: string): string => {
  const item = menuGroups.flatMap((group) => group.items).find((entry) => entry.route === path)
  return item?.label ?? '岐黄智学'
}
