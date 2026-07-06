<template>
  <aside class="app-sidebar" :class="{ 'is-collapsed': collapsed }">
    <div class="brand">
      <img class="brand__logo" :src="qihuangLogo" alt="岐黄智学" />
      <div class="brand__copy">
        <strong>岐黄智学</strong>
        <span>TCM EDU AGENT</span>
      </div>
    </div>

    <el-dropdown trigger="click" placement="bottom-start" @command="handleUserCommand">
      <button class="user-card" type="button" aria-label="当前登录用户">
        <img :src="xiaoqi" alt="当前用户头像" />
        <span class="user-card__copy">
          <strong>{{ auth.displayName }}</strong>
          <em>{{ auth.user?.is_guest ? '游客体验' : '已登录' }}</em>
        </span>
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="logout">
            <el-icon><SwitchButton /></el-icon>
            退出登录
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>

    <nav class="sidebar-nav" aria-label="主导航">
      <NavGroup
        v-for="group in displayMenuGroups"
        :key="group.group"
        :group="group"
        :collapsed="collapsed"
        @assistant="assistant.show"
      />
    </nav>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { SwitchButton } from '@element-plus/icons-vue'
import qihuangLogo from '../../assets/brand/qihuang-logo.png'
import xiaoqi from '../../assets/mascot/xiaoqi-avatar.png'
import { menuGroups } from '../../config/menu'
import { readOnboardingState } from '../../services/onboardingStorage'
import { useAssistantStore } from '../../stores/assistant'
import { useAuthStore } from '../../stores/auth'
import { useProfileStore } from '../../stores/profile'
import NavGroup from './NavGroup.vue'

defineProps<{
  collapsed: boolean
}>()

const assistant = useAssistantStore()
const auth = useAuthStore()
const profile = useProfileStore()
const router = useRouter()

const displayMenuGroups = computed(() =>
  menuGroups.map((group) => ({
    ...group,
    items: group.items.map((item) => {
      if (item.dynamicLabel !== 'profileOnboarding') return item
      const userId = auth.user?.user_id ?? ''
      const state = readOnboardingState(userId)
      return {
        ...item,
        label: profile.hasBuiltProfile || state?.status === 'completed' ? '重塑画像' : '焕醒画像',
      }
    }),
  })),
)

const handleUserCommand = (command: string) => {
  if (command !== 'logout') return
  auth.logout()
  void router.replace('/login')
}
</script>

<style scoped lang="scss">
@use "../../styles/tokens.scss" as *;

.app-sidebar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  flex: 0 0 var(--qh-sidebar-width);
  flex-direction: column;
  width: var(--qh-sidebar-width);
  height: 100vh;
  padding: 18px 14px;
  border-right: 1px solid var(--qh-border);
  background: var(--qh-sidebar-fill);
  box-shadow: var(--qh-shadow-soft), var(--qh-inner-highlight);
  backdrop-filter: blur(18px);
  transition:
    width 220ms ease,
    flex-basis 220ms ease;
}

.app-sidebar.is-collapsed {
  flex-basis: var(--qh-sidebar-collapsed);
  width: var(--qh-sidebar-collapsed);
  padding-inline: 10px;
}

.brand {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  min-height: 58px;
}

.user-card img {
  border: 1px solid rgba(143, 208, 173, 0.22);
  border-radius: 16px;
  object-fit: cover;
  box-shadow: var(--qh-inner-highlight);
  transform-origin: 50% 80%;
  animation:
    bob 5.8s ease-in-out infinite,
    wave 9s ease-in-out infinite;
}

.brand__logo {
  width: 48px;
  height: 48px;
  padding: 5px;
  border: 1px solid rgba(231, 194, 116, 0.22);
  border-radius: 16px;
  background:
    linear-gradient(150deg, rgba(238, 245, 239, 0.12), rgba(143, 208, 173, 0.05)),
    rgba(16, 30, 29, 0.42);
  box-shadow: var(--qh-inner-highlight), 0 10px 24px rgba(0, 0, 0, 0.22);
  object-fit: contain;
}

.brand__copy {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.brand__copy strong {
  font-family: var(--qh-font-display);
  font-size: 19px;
  font-weight: 900;
}

.brand__copy span {
  color: var(--qh-text-muted);
  font-family: var(--qh-font-display);
  font-size: 10px;
  font-weight: 900;
}

.user-card {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  align-items: center;
  width: 100%;
  gap: 10px;
  min-height: 58px;
  margin: 18px 0;
  padding: 9px;
  border: 1px solid rgba(143, 208, 173, 0.14);
  border-radius: 18px;
  background: rgba(238, 245, 239, 0.06);
  color: var(--qh-text);
  cursor: pointer;
  text-align: left;
  box-shadow: var(--qh-inner-highlight);
  backdrop-filter: blur(16px);
}

.user-card:focus-visible {
  @include focus-ring;
}

.user-card img {
  width: 40px;
  height: 40px;
}

.user-card__copy {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.user-card__copy strong,
.user-card__copy em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-card__copy strong {
  color: rgba(238, 245, 239, 0.78);
  font-size: 13px;
  font-weight: 900;
}

.user-card__copy em {
  color: var(--qh-color-jade);
  font-size: 11px;
  font-style: normal;
  font-weight: 900;
}

.sidebar-nav {
  display: grid;
  gap: 18px;
  overflow-y: auto;
  padding-right: 2px;
}

.is-collapsed .brand {
  grid-template-columns: 1fr;
  justify-items: center;
}

.is-collapsed .brand__copy,
.is-collapsed .user-card__copy {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}

.is-collapsed .user-card {
  grid-template-columns: 1fr;
  justify-items: center;
  padding: 8px;
}

@media (max-width: 900px) {
  .app-sidebar {
    flex-basis: var(--qh-sidebar-collapsed);
    width: var(--qh-sidebar-collapsed);
    padding-inline: 10px;
  }

  .brand {
    grid-template-columns: 1fr;
    justify-items: center;
  }

  .brand__copy,
  .user-card__copy {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }

  .user-card {
    grid-template-columns: 1fr;
    justify-items: center;
    padding: 8px;
  }
}
</style>
