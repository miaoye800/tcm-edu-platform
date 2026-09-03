<template><span class="pulse-ring" :style="{ '--pulse-duration': `${stage4Visual.inspira.pulseSeconds}s` }" aria-hidden="true" /></template>
<script setup lang="ts">
import { stage4Visual } from '../../../config/stage4Visual'
</script>
<style scoped lang="scss">
.pulse-ring{position:absolute;right:var(--qh-space-4);bottom:var(--qh-space-4);width:34px;height:34px;border:1px solid color-mix(in srgb,var(--qh-color-gold) 65%,transparent);border-radius:50%;opacity:.7;animation:ring-pulse var(--pulse-duration) ease-out infinite}.pulse-ring::after{position:absolute;inset:10px;border-radius:inherit;background:var(--qh-color-gold);content:""}@keyframes ring-pulse{50%{box-shadow:0 0 0 11px color-mix(in srgb,var(--qh-color-gold) 0%,transparent);transform:scale(1.08)}}@media (prefers-reduced-motion:reduce){.pulse-ring{animation:none}}
</style>
