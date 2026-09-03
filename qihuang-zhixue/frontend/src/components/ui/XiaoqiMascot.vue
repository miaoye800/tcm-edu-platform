<template>
  <span class="xiaoqi-mascot" :class="{ 'is-animated': animated }" :style="styleVars" :aria-label="alt">
    <Transition name="expression" mode="out-in">
      <img :key="expression" :src="src" :alt="alt" @error="useFallback" />
    </Transition>
  </span>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import fallbackSrc from '../../assets/mascot/xiaoqi-avatar.png'
import { immersiveVisual } from '../../config/immersive'

export type XiaoqiExpression = 'default' | 'happy' | 'thinking' | 'encourage' | 'hint'
const props = withDefaults(defineProps<{ expression?: XiaoqiExpression; size?: number | string; animated?: boolean; alt?: string }>(), {
  expression: 'default', size: 40, animated: false, alt: '小岐',
})
const failed = ref(false)
const expressionFile = computed(() => props.expression === 'thinking' ? 'thinking' : props.expression)
const src = computed(() => failed.value || props.expression === 'default' ? fallbackSrc : new URL(`../../assets/mascot/expressions/${expressionFile.value}.png`, import.meta.url).href)
const styleVars = computed(() => ({ '--xiaoqi-size': typeof props.size === 'number' ? `${props.size}px` : props.size, '--xiaoqi-crossfade': `${immersiveVisual.mascotCrossfadeMs}ms` }))
const useFallback = () => { failed.value = true }
watch(() => props.expression, () => { failed.value = false })
</script>

<style scoped lang="scss">
.xiaoqi-mascot{display:inline-grid;width:var(--xiaoqi-size);height:var(--xiaoqi-size);place-items:center;vertical-align:middle}.xiaoqi-mascot img{display:block;width:100%;height:100%;object-fit:contain}.is-animated{animation:xiaoqi-bob 2.8s ease-in-out infinite}.expression-enter-active,.expression-leave-active{transition:opacity var(--xiaoqi-crossfade) ease}.expression-enter-from,.expression-leave-to{opacity:0}@keyframes xiaoqi-bob{50%{transform:translateY(-3px)}}@media(prefers-reduced-motion:reduce){.is-animated{animation:none}}
</style>
