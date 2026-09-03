<template><div class="meteors" :style="{ '--meteor-duration': `${stage4Visual.inspira.meteorSeconds}s` }" aria-hidden="true"><i v-for="item in 4" :key="item" :style="{ '--delay': `${item * 1.15}s`, '--offset': `${item * 21}%` }" /></div></template>
<script setup lang="ts">
import { stage4Visual } from '../../../config/stage4Visual'
</script>
<style scoped lang="scss">
.meteors{position:absolute;inset:0;overflow:hidden;pointer-events:none}.meteors i{position:absolute;top:var(--offset);right:12%;width:76px;height:1px;opacity:0;background:linear-gradient(90deg,transparent,var(--qh-color-blue),transparent);transform:rotate(-24deg);animation:meteor var(--meteor-duration) var(--delay) ease-in infinite}@keyframes meteor{20%{opacity:.8;transform:translate3d(-120px,50px,0) rotate(-24deg)}28%,100%{opacity:0}}@media (prefers-reduced-motion:reduce){.meteors i{animation:none;opacity:.18}}
</style>
