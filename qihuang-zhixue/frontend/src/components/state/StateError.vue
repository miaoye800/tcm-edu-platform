<template>
  <section class="state-error" :class="`size-${size}`" role="status">
    <slot name="icon"><svg viewBox="0 0 88 72" role="img" aria-label="待重新连接的药签"><path d="M16 54 36 28l13 14 13-18 12 30H16Z"/><path d="m43 15 2 6m-9 1 5 4m15-4-5 4" class="spark"/></svg></slot>
    <div><h3>{{ title }}</h3><p>{{ description }}</p><p class="comfort">{{ comfort }}</p></div>
    <el-button v-if="actionText" plain @click="$emit('action')">{{ actionText }}</el-button>
  </section>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'
type StateSize = 'sm' | 'md' | 'lg'
defineProps({ title: { type: String, default: '这条线索暂时没接上' }, description: { type: String, default: '请检查网络或稍后重试。' }, comfort: { type: String, default: '小岐在这里，重试一次就好。' }, actionText: { type: String, default: '重新尝试' }, size: { type: String as PropType<StateSize>, default: 'md' } })
defineEmits<{ action: [] }>()
</script>

<style scoped lang="scss">
.state-error{display:grid;place-items:center;gap:var(--qh-space-3);padding:var(--qh-space-6);color:var(--qh-text-muted);text-align:center}.state-error svg{width:80px;height:66px;fill:color-mix(in srgb,var(--qh-color-coral) 14%,transparent);stroke:var(--qh-color-coral);stroke-linecap:round;stroke-linejoin:round;stroke-width:2}.state-error .spark{fill:none;stroke:var(--qh-color-gold)}.state-error h3,.state-error p{margin:0}.state-error h3{color:var(--qh-text);font-family:var(--qh-font-display);font-size:18px}.state-error p{margin-top:var(--qh-space-2);font-size:13px;line-height:1.7}.state-error .comfort{color:var(--qh-color-jade)}.size-sm{padding:var(--qh-space-3)}.size-sm svg{width:58px}.size-lg{padding:var(--qh-space-8)}.size-lg svg{width:108px}
</style>
