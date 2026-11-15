<template>
  <section class="nav-group">
    <p class="nav-group__title" :class="{ 'is-collapsed': collapsed }">{{ group.group }}</p>
    <div class="nav-group__items">
      <NavItem
        v-for="item in group.items"
        :key="item.route"
        :item="item"
        :collapsed="collapsed"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import type { MenuGroup } from '../../config/menu'
import NavItem from './NavItem.vue'

defineProps<{
  group: MenuGroup
  collapsed: boolean
}>()
</script>

<style scoped lang="scss">
.nav-group {
  display: grid;
  gap: 8px;
}

.nav-group__title {
  margin: 0;
  padding: 0 10px;
  color: var(--qh-nav-title);
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
}

.nav-group__title.is-collapsed {
  height: 1px;
  overflow: hidden;
  opacity: 0;
}

.nav-group__items {
  display: grid;
  gap: 4px;
}
</style>
