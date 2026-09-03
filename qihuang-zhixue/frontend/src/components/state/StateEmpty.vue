<template>
  <section class="state-empty" :class="`size-${size}`">
    <slot name="icon">
      <component v-if="typeof icon !== 'string'" :is="icon" class="custom-icon" />
      <svg v-else viewBox="0 0 96 76" role="img" aria-label="小岐整理药签的小插画">
        <path d="M16 59c9-12 19-17 31-17s22 5 33 17" class="line" />
        <path d="M28 44c1-17 8-27 20-31 12 4 19 14 20 31" class="line" />
        <path d="M39 31c3-4 9-4 12 0M34 41c4 5 9 5 14 0" class="detail" />
        <path d="M68 15c9 3 12 10 10 19-9-3-12-10-10-19Z" class="leaf" />
        <path d="M68 15c0 9-4 14-10 18" class="detail" />
        <path d="M14 61h68" class="detail" />
      </svg>
    </slot>
    <div class="copy"><h3>{{ title }}</h3><p>{{ description }}</p></div>
    <el-button v-if="actionText" type="primary" @click="$emit('action')">{{ actionText }}</el-button>
  </section>
</template>

<script setup lang="ts">
import type { Component, PropType } from 'vue'
type StateSize = 'sm' | 'md' | 'lg'
defineProps({ icon: { type: [String, Object] as PropType<string | Component>, default: 'herb' }, title: { type: String, default: '这里还没有药签' }, description: { type: String, default: '小岐已留好位置，等你带回第一条线索。' }, actionText: { type: String, default: '' }, size: { type: String as PropType<StateSize>, default: 'md' } })
defineEmits<{ action: [] }>()
</script>

<style scoped lang="scss">
.state-empty{--state-size:76px;display:grid;place-items:center;gap:var(--qh-space-3);padding:var(--qh-space-6);color:var(--qh-text-muted);text-align:center}.size-sm{--state-size:54px;padding:var(--qh-space-3)}.size-lg{--state-size:108px;padding:var(--qh-space-8)}svg,.custom-icon{width:var(--state-size);height:calc(var(--state-size) * .8)}.line,.detail,.leaf{fill:none;stroke:var(--qh-color-jade);stroke-linecap:round;stroke-linejoin:round}.line{stroke-width:2}.detail{stroke:var(--qh-color-blue);stroke-width:1.5}.leaf{fill:color-mix(in srgb,var(--qh-color-gold) 16%,transparent);stroke:var(--qh-color-gold)}.copy{display:grid;gap:var(--qh-space-2);max-width:38rem}.copy h3,.copy p{margin:0}.copy h3{color:var(--qh-text);font-family:var(--qh-font-display);font-size:18px}.copy p{font-size:13px;line-height:1.7}
</style>
