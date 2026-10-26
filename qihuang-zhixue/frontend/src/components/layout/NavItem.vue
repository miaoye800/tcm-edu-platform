<template>
  <button
    v-if="item.assistantTrigger"
    class="nav-item"
    :class="{ 'is-collapsed': collapsed, 'is-disabled': item.disabled }"
    type="button"
    @click="emit('assistant')"
  >
    <el-icon class="nav-icon"><component :is="item.icon" /></el-icon>
    <span class="nav-label">{{ item.label }}</span>
    <Badge v-if="item.badge" class="nav-badge" tone="coral" :label="item.badge" />
  </button>

  <RouterLink
    v-else
    class="nav-item"
    :class="{ 'is-collapsed': collapsed, 'is-disabled': item.disabled }"
    :to="item.route"
  >
    <el-icon class="nav-icon"><component :is="item.icon" /></el-icon>
    <span class="nav-label">{{ item.label }}</span>
    <Badge v-if="item.badge" class="nav-badge" tone="coral" :label="item.badge" />
  </RouterLink>
</template>

<script setup lang="ts">
import { RouterLink } from 'vue-router'
import type { MenuItem } from '../../config/menu'
import Badge from '../ui/Badge.vue'

defineProps<{
  item: MenuItem
  collapsed: boolean
}>()

const emit = defineEmits<{
  assistant: []
}>()
</script>

<style scoped lang="scss">
@use "../../styles/tokens.scss" as *;

.nav-item {
  position: relative;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  width: 100%;
  min-height: 44px;
  padding: 5px 8px;
  border-radius: 14px;
  color: var(--qh-nav-text);
  background: transparent;
  cursor: pointer;
  transition:
    color 180ms ease,
    background 180ms ease,
    transform 180ms ease;
}

.nav-item:focus-visible {
  @include focus-ring;
}

.nav-item:hover,
.nav-item.router-link-active {
  color: var(--qh-text);
  background: rgba(143, 208, 173, 0.11);
}

.nav-item.is-disabled {
  opacity: 0.48;
  filter: grayscale(0.3);
}

.nav-item.is-disabled .nav-icon {
  color: var(--qh-text-muted);
}

.nav-item.router-link-active::before {
  position: absolute;
  left: -8px;
  width: 3px;
  height: 24px;
  border-radius: 999px;
  background: linear-gradient(180deg, var(--qh-color-jade), var(--qh-color-gold));
  content: "";
}

.nav-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 12px;
  color: var(--qh-color-jade);
  background: rgba(238, 245, 239, 0.06);
  font-size: 18px;
}

.nav-label {
  min-width: 0;
  padding: 0 8px;
  font-size: 14px;
  font-weight: 800;
  white-space: nowrap;
}

.nav-badge {
  margin-right: 2px;
}

.is-collapsed {
  grid-template-columns: 1fr;
  justify-items: center;
  padding-inline: 6px;
}

.is-collapsed .nav-label,
.is-collapsed .nav-badge {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

.is-collapsed.router-link-active::before {
  left: -5px;
}
</style>
