import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import AppShell from '../components/layout/AppShell.vue'
import AtlasView from '../views/AtlasView.vue'
import ComingSoonView from '../views/ComingSoonView.vue'
import DashboardView from '../views/DashboardView.vue'
import LearnView from '../views/LearnView.vue'
import LoginView from '../views/LoginView.vue'
import MistakesView from '../views/MistakesView.vue'
import OnboardingView from '../views/OnboardingView.vue'
import PathView from '../views/PathView.vue'
import ProfileView from '../views/ProfileView.vue'
import QuizView from '../views/QuizView.vue'
import ReviewView from '../views/ReviewView.vue'
import { useAuthStore } from '../stores/auth'
import { useProfileStore } from '../stores/profile'
import { readOnboardingState } from '../services/onboardingStorage'

const routes: RouteRecordRaw[] = [
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
        component: LearnView,
        meta: { title: '智能学习', group: '学习', mode: 'immersive' },
      },
      {
        path: 'path',
        name: 'path',
        component: PathView,
        meta: { title: '闯关地图', group: '学习', mode: 'embedded' },
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

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
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

  if (!isPublic && auth.isAuthenticated && to.name !== 'onboarding') {
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

export default router
