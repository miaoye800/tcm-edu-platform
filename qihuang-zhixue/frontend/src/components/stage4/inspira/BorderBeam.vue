<template><span class="border-beam" :style="{ '--beam-duration': `${stage4Visual.inspira.beamSeconds}s` }" aria-hidden="true" /></template>
<script setup lang="ts">
import { stage4Visual } from '../../../config/stage4Visual'
</script>
<style scoped lang="scss">
.border-beam{position:absolute;inset:-1px;overflow:hidden;border-radius:inherit;pointer-events:none}.border-beam::after{position:absolute;top:-35%;left:-20%;width:42%;height:170%;background:linear-gradient(180deg,transparent,var(--qh-color-jade),var(--qh-color-gold),transparent);content:"";filter:blur(2px);animation:border-orbit var(--beam-duration) linear infinite}.border-beam::before{position:absolute;inset:1px;border-radius:inherit;background:var(--qh-card-fill);content:""}@keyframes border-orbit{to{transform:translateX(330%)}}@media (prefers-reduced-motion:reduce){.border-beam::after{animation:none;opacity:.55}}
</style>
