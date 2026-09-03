<template>
  <section class="state-loading" :class="`size-${size}`" :style="{ '--trace-duration': `${stage4Visual.state.loadingSeconds}s` }" aria-live="polite" aria-busy="true">
    <slot name="icon"><svg viewBox="0 0 120 64" role="img" aria-label="经络勾画中"><path d="M8 43C26 43 27 18 46 18s18 28 36 28c13 0 18-10 30-22"/><circle cx="46" cy="18" r="4"/><circle cx="82" cy="46" r="4"/><circle cx="112" cy="24" r="4"/></svg></slot>
    <div><h3>{{ title }}</h3><p>{{ description }}</p></div>
  </section>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'
import { stage4Visual } from '../../config/stage4Visual'
type StateSize = 'sm' | 'md' | 'lg'
defineProps({ title: { type: String, default: '正在循脉勾画' }, description: { type: String, default: '小岐正把线索接回经络，请稍候。' }, size: { type: String as PropType<StateSize>, default: 'md' } })
</script>

<style scoped lang="scss">
.state-loading{display:grid;place-items:center;gap:var(--qh-space-3);padding:var(--qh-space-6);color:var(--qh-text-muted);text-align:center}.state-loading svg{width:112px;overflow:visible;fill:none;stroke:var(--qh-color-jade);stroke-linecap:round;stroke-width:2}.state-loading path{stroke-dasharray:128;stroke-dashoffset:128;animation:meridian-trace var(--trace-duration) ease-in-out infinite}.state-loading circle{fill:var(--qh-color-gold);stroke:var(--qh-color-cream);transform-origin:center;animation:node-light var(--trace-duration) ease-in-out infinite}.state-loading circle:nth-of-type(2){animation-delay:160ms}.state-loading circle:nth-of-type(3){animation-delay:320ms}.state-loading h3,.state-loading p{margin:0}.state-loading h3{color:var(--qh-text);font-family:var(--qh-font-display);font-size:18px}.state-loading p{margin-top:var(--qh-space-2);font-size:13px;line-height:1.7}.size-sm{padding:var(--qh-space-3)}.size-sm svg{width:80px}.size-lg{padding:var(--qh-space-8)}.size-lg svg{width:150px}@keyframes meridian-trace{50%{stroke-dashoffset:0;opacity:1}100%{stroke-dashoffset:-128;opacity:.45}}@keyframes node-light{50%{opacity:1;transform:scale(1.35)}100%{opacity:.48;transform:scale(1)}}@media (prefers-reduced-motion:reduce){.state-loading path,.state-loading circle{animation:none}.state-loading path{stroke-dashoffset:0}}
</style>
