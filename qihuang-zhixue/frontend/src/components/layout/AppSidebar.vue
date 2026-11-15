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
        <XiaoqiMascot expression="default" :size="40" alt="当前用户头像" />
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
      <template v-for="(group, groupIndex) in displayMenuGroups" :key="group.group">
        <section v-if="groupIndex === 0" class="nav-group">
          <p class="nav-group__title" :class="{ 'is-collapsed': collapsed }">{{ group.group }}</p>
          <div class="nav-group__items">
            <NavItem :item="group.items[0]" :collapsed="collapsed" />
            <button class="theme-switch" type="button" :class="{ 'is-collapsed': collapsed }" :aria-label="theme === 'dark' ? '切换浅色模式' : '切换深色模式'" @click="toggleTheme"><el-icon><Sunny v-if="theme === 'dark'" /><Moon v-else /></el-icon><span>{{ theme === 'dark' ? '浅色模式' : '深色模式' }}</span></button>
            <NavItem v-for="item in group.items.slice(1)" :key="item.route" :item="item" :collapsed="collapsed" />
          </div>
        </section>
        <NavGroup v-else :group="group" :collapsed="collapsed" />
      </template>
    </nav>
  </aside>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Moon, Sunny, SwitchButton } from '@element-plus/icons-vue'
import qihuangLogo from '../../assets/brand/qihuang-logo.png'
import xiaoqi from '../../assets/mascot/xiaoqi-avatar.png'
import XiaoqiMascot from '../ui/XiaoqiMascot.vue'
import { menuGroups } from '../../config/menu'
import { readOnboardingState } from '../../services/onboardingStorage'
import { useAuthStore } from '../../stores/auth'
import { useProfileStore } from '../../stores/profile'
import NavGroup from './NavGroup.vue'
import NavItem from './NavItem.vue'
import { useTheme } from '../../composables/useTheme'
import { listMyMistakes } from '../../services/kgApi'

defineProps<{
  collapsed: boolean
}>()

const auth = useAuthStore()
const profile = useProfileStore()
const router = useRouter()
const route = useRoute()
const { theme, toggleTheme } = useTheme()

/** 侧栏「错题本」角标。0 或取不到时不显示——宁可空着，也不显示一个假数字。 */
const mistakeCount = ref(0)
const refreshMistakeCount = async () => {
  if (!auth.user?.user_id) {
    mistakeCount.value = 0
    return
  }
  try {
    const result = await listMyMistakes(1)
    mistakeCount.value = result.total ?? 0
  } catch {
    mistakeCount.value = 0
  }
}
onMounted(() => void refreshMistakeCount())
// 答完题回到列表、或从错题本返回时都要重新取，否则角标停在登录那一刻的数字。
watch(() => route.fullPath, () => void refreshMistakeCount())

const displayMenuGroups = computed(() =>
  menuGroups.map((group) => ({
    ...group,
    items: group.items.map((item) => {
      // 角标：用真实错题数。取不到就**不显示**，不拿一个猜的数字顶上。
      const withBadge =
        item.dynamicBadge === 'mistakeCount'
          ? { ...item, badge: mistakeCount.value || undefined }
          : item
      if (withBadge.dynamicLabel !== 'profileOnboarding') return withBadge
      const userId = auth.user?.user_id ?? ''
      const state = readOnboardingState(userId)
      return {
        ...withBadge,
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
  border-right: 1px solid var(--qh-line);
  background: var(--qh-sidebar-fill);
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
  display: none;
  border: 1px solid var(--qh-line);
  border-radius: 14px;
  object-fit: cover;
  transform-origin: 50% 80%;
  animation:
    bob 5.8s ease-in-out infinite,
    wave 9s ease-in-out infinite;
}

.brand__logo {
  width: 48px;
  height: 48px;
  padding: 5px;
  border: 1px solid var(--qh-line);
  border-radius: 14px;
  background: var(--qh-surface-raised);
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
  border: 1px solid var(--qh-line);
  border-radius: 14px;
  background: var(--qh-surface-raised);
  color: var(--qh-text);
  cursor: pointer;
  text-align: left;
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
  color: var(--qh-user-name);
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

.theme-switch { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 42px; padding: 9px 12px; border: 1px solid var(--qh-border); border-radius: var(--qh-radius-stage); color: var(--qh-nav-text); background: var(--qh-button-secondary-fill); cursor: pointer; font-weight: 800; text-align: left; }
.theme-switch:focus-visible { @include focus-ring; }
.theme-switch.is-collapsed { justify-content: center; padding-inline: 0; }
.theme-switch.is-collapsed span { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.nav-group { display: grid; gap: 8px; }
.nav-group__title { margin: 0; padding: 0 10px; color: var(--qh-nav-title); font-size: 12px; font-weight: 900; }
.nav-group__title.is-collapsed { height: 1px; overflow: hidden; opacity: 0; }
.nav-group__items { display: grid; gap: 4px; }

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
