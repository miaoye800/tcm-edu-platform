import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import AppShell from '../components/layout/AppShell.vue'
import AtlasView from '../views/AtlasView.vue'
import ComingSoonView from '../views/ComingSoonView.vue'
import DashboardView from '../views/DashboardView.vue'
// LearnView is retained for compatibility; /learn redirects to the meridian entry.
import LoginView from '../views/LoginView.vue'
import MyResourcesView from '../views/MyResourcesView.vue'
import ResourceDetailView from '../views/ResourceDetailView.vue'
import MistakesView from '../views/MistakesView.vue'
import MeridianDemo from '../views/MeridianDemo.vue'
import AlchemyDemo from '../views/AlchemyDemo.vue'
import MindmapThemesDemo from '../views/MindmapThemesDemo.vue'
import MeridianMapView from '../views/MeridianMapView.vue'
import OnboardingView from '../views/OnboardingView.vue'
import PathView from '../views/PathView.vue'
import ProfileView from '../views/ProfileView.vue'
import QuizView from '../views/QuizView.vue'
import AssessmentView from '../views/AssessmentView.vue'
import ApprenticeStoryView from '../views/ApprenticeStoryView.vue'
import ReviewView from '../views/ReviewView.vue'
import { useAuthStore } from '../stores/auth'
import { useProfileStore } from '../stores/profile'
import { readOnboardingState } from '../services/onboardingStorage'

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
  {
    path: '/mindmap-themes',
    name: 'mindmap-themes',
    component: MindmapThemesDemo,
    meta: { title: '思维导图模板库', public: true },
  },
  {
    path: '/meridian-demo',
    name: 'meridian-demo',
    component: MeridianDemo,
    meta: { title: '经络学习脉络样张', public: true },
  },
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
        redirect: '/dashboard',
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

    if (!profile.usingMock && !profile.hasBuiltProfile && !onboarding) {
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
