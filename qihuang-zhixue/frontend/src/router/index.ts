import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import AppShell from '../components/layout/AppShell.vue'
import AtlasView from '../views/AtlasView.vue'
import CompareView from '../views/CompareView.vue'
import MindmapStudioView from '../views/MindmapStudioView.vue'
import KgAskView from '../views/KgAskView.vue'
import ComingSoonView from '../views/ComingSoonView.vue'
import DashboardView from '../views/DashboardView.vue'
import DocStudioView from '../views/DocStudioView.vue'
import LoginView from '../views/LoginView.vue'
import MyResourcesView from '../views/MyResourcesView.vue'
import ResourceDetailView from '../views/ResourceDetailView.vue'
import MistakesView from '../views/MistakesView.vue'
import MeridianDemo from '../views/MeridianDemo.vue'
import AlchemyDemo from '../views/AlchemyDemo.vue'
import MeridianMapView from '../views/MeridianMapView.vue'
import OnboardingView from '../views/OnboardingView.vue'
import PrescriptionView from '../views/PrescriptionView.vue'
import ProfileView from '../views/ProfileView.vue'
import QuizView from '../views/QuizView.vue'
import PptStudioView from '../views/PptStudioView.vue'
import AssessmentView from '../views/AssessmentView.vue'
import ApprenticeStoryView from '../views/ApprenticeStoryView.vue'
import ReviewView from '../views/ReviewView.vue'
import AssistantView from '../views/AssistantView.vue'
import { useAuthStore } from '../stores/auth'
import { useProfileStore } from '../stores/profile'
import { readOnboardingState, readOnboardingInvite, markOnboardingInvited } from '../services/onboardingStorage'

const routes: RouteRecordRaw[] = [
  {
    path: '/apprentice',
    name: 'apprentice-story',
    component: ApprenticeStoryView,
    meta: { title: '收徒剧情', mode: 'fullscreen' },
  },
  {
    path: '/assessment/:nodeId',
    name: 'assessment',
    component: AssessmentView,
    meta: { title: '过关试炼', mode: 'fullscreen' },
  },
  {
    path: '/alchemy-demo',
    name: 'alchemy-demo',
    component: AlchemyDemo,
    meta: { title: '炼药演出样张', public: true },
  },
  // 仅开发环境：四结构 × 四主题的版式验证页，不进生产包。
  ...(import.meta.env.DEV
    ? [
        {
          path: '/_lab/mindmap',
          name: 'mindmap-lab',
          component: () => import('../views/MindmapStyleLab.vue'),
          meta: { title: '导图版式验证', public: true },
        } as RouteRecordRaw,
        {
          // 挂真实 StudioCanvas 的联调页：验证适配层，而不是验证库。
          path: '/_lab/canvas',
          name: 'mindmap-canvas-lab',
          component: () => import('../views/MindmapCanvasHarness.vue'),
          meta: { title: '导图画布联调', public: true },
        } as RouteRecordRaw,
        {
          // 十种版式 × 四套纸面的视觉验收页：版式一改就能一眼看出哪里塌了。
          path: '/_lab/ppt',
          name: 'ppt-deck-lab',
          component: () => import('../views/PptDeckLab.vue'),
          meta: { title: '幻灯版式验证', public: true },
        } as RouteRecordRaw,
      ]
    : []),
  {
    path: '/meridian-demo',
    name: 'meridian-demo',
    component: MeridianDemo,
    meta: { title: '经络学习脉络样张', public: true },
  },
  // Dev-only component gallery for the apprentice story. Lazy so production
  // builds never ship the review surface.
  ...(import.meta.env.DEV
    ? [
        {
          path: '/story-kit',
          name: 'story-kit',
          component: () => import('../views/StoryKitView.vue'),
          meta: { title: '剧情组件画廊', public: true },
        } satisfies RouteRecordRaw,
        {
          path: '/story-preview',
          name: 'story-preview',
          component: () => import('../views/StoryPreviewView.vue'),
          meta: { title: '剧情整屏预览', public: true },
        } satisfies RouteRecordRaw,
      ]
    : []),
  {
    path: '/login',
    name: 'login',
    component: LoginView,
    meta: { title: '登录', public: true },
  },
  {
    path: '/',
    component: AppShell,
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: DashboardView,
        meta: { title: '总览仪表盘', group: '学习', mode: 'embedded' },
      },
      {
        path: 'profile',
        name: 'profile',
        component: ProfileView,
        meta: { title: '弟子档案', group: '学习', mode: 'embedded' },
      },
      {
        path: 'onboarding',
        name: 'onboarding',
        component: OnboardingView,
        meta: { title: '焕醒画像', group: '学习', mode: 'embedded' },
      },
      {
        path: 'learn',
        name: 'learn',
        // deprecated: immersive learning now begins from a map node.
        redirect: '/path',
      },
      {
        path: 'path',
        name: 'path',
        component: MeridianMapView,
        meta: { title: '闯关地图', group: '学习', mode: 'embedded' },
      },
      {
        path: 'resources',
        name: 'resources',
        component: MyResourcesView,
        meta: { title: '我的资源库', group: '学习', mode: 'embedded' },
      },
      {
        path: 'resources/:id',
        name: 'resource-detail',
        component: ResourceDetailView,
        meta: { title: '资源查看', group: '学习', mode: 'embedded' },
      },
      {
        path: 'quiz',
        name: 'quiz',
        component: QuizView,
        meta: { title: '题库测评', group: '评测', mode: 'embedded' },
      },
      {
        path: 'mistakes',
        name: 'mistakes',
        component: MistakesView,
        meta: { title: '错题本', group: '评测', mode: 'embedded' },
      },
      {
        path: 'review',
        name: 'review',
        component: ReviewView,
        meta: { title: '复习计划', group: '评测', mode: 'embedded' },
      },
      {
        path: 'atlas',
        name: 'atlas',
        component: AtlasView,
        meta: { title: '中药图谱', group: '图谱', mode: 'embedded' },
      },
      {
        path: 'mindmap',
        name: 'mindmap-studio',
        component: MindmapStudioView,
        meta: { title: '导图工坊', group: '图谱', mode: 'embedded' },
      },
      {
        path: 'compare',
        name: 'compare',
        component: CompareView,
        meta: { title: '两药对比', group: '图谱', mode: 'embedded' },
      },
      {
        path: 'prescription',
        name: 'prescription',
        component: PrescriptionView,
        meta: { title: '处方审查', group: '图谱', mode: 'embedded' },
      },
      {
        path: 'kg-ask',
        name: 'kg-ask',
        component: KgAskView,
        meta: { title: '图谱问答', group: '图谱', mode: 'embedded' },
      },
      {
        path: 'ppt',
        name: 'ppt-studio',
        component: PptStudioView,
        meta: { title: '幻灯工坊', group: '创作', mode: 'embedded' },
      },
      {
        path: 'doc-studio',
        name: 'doc-studio',
        component: DocStudioView,
        meta: { title: '文档工坊', group: '创作', mode: 'embedded' },
      },
      {
        path: 'theater',
        name: 'theater',
        component: ComingSoonView,
        meta: { title: '漫剧剧场', group: '互动', mode: 'embedded' },
      },
      {
        path: 'clinic',
        name: 'clinic',
        component: ComingSoonView,
        meta: { title: '开药游戏', group: '互动', mode: 'embedded' },
      },
      {
        path: 'assistant',
        name: 'assistant',
        component: AssistantView,
        meta: { title: 'AI 服务助手', group: '助手', mode: 'embedded' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/dashboard',
  },
]

if (import.meta.env.DEV) {
  routes.unshift({
    path: '/profile-dialogue-fixture',
    name: 'profile-dialogue-fixture',
    component: () => import('../views/ProfileDialogueFixtureView.vue'),
    meta: { title: '命格卡视觉验收', public: true },
  })
  routes.unshift({
    path: '/stage4-lab',
    name: 'stage4-lab',
    component: () => import('../views/Stage4Lab.vue'),
    meta: { title: '阶段四选件货架', public: true },
  })
}

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  // Isolated visual-capture builds only; normal dev and production keep auth.
  if (import.meta.env.VITE_E2E_CAPTURE === 'true') return true
  if (import.meta.env.VITE_E2E_GRAPH_CAPTURE === 'true' && to.name === 'atlas') return true

  const auth = useAuthStore()
  const isPublic = Boolean(to.meta.public)

  if (!isPublic && !auth.isAuthenticated) {
    return {
      name: 'login',
      query: { redirect: to.fullPath },
    }
  }

  if (to.name === 'login' && auth.isAuthenticated) {
    const redirect = typeof to.query.redirect === 'string' ? to.query.redirect : '/dashboard'
    return redirect
  }

  if (!isPublic && auth.isAuthenticated && to.name !== 'onboarding' && to.name !== 'apprentice-story') {
    const profile = useProfileStore()
    const userId = auth.user?.user_id ?? ''
    const onboarding = readOnboardingState(userId)
    const needsProfileCheck = !profile.loaded || profile.activeUserId !== userId

    if (needsProfileCheck) {
      await profile.loadProfile()
    }

    // 画像入口只邀请一次：用户只要离开过（点侧栏任何页面），就视为暂不绘制
    // 画像，此后不再拦截页面切换。是否继续完全由侧栏入口决定。
    // 读失败（loadFailed）时不做任何判断：拿不到数据不等于"还没画画像"，
    // 把用户弹进入口只会掩盖一次网络故障。
    if (!profile.loadFailed && !profile.usingMock && !profile.hasBuiltProfile && !onboarding && !readOnboardingInvite(userId)) {
      markOnboardingInvited(userId)
      return {
        name: 'onboarding',
        query: { redirect: to.fullPath },
      }
    }
  }

  return true
})

router.afterEach((to) => {
  if (to.meta.public) return

  const auth = useAuthStore()
  if (!auth.isAuthenticated) return

  const profile = useProfileStore()
  window.setTimeout(() => {
    void profile.ensureNarrativeLoaded()
  }, 0)
})

export default router
