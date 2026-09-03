<template><section class="step-rail"><div class="copy"><p class="kicker">丹房生成中</p><Transition name="step" mode="out-in"><h2 :key="current?.id">{{ current?.label || '炉火待起' }}</h2></Transition><p class="subcopy">{{ stateCopy }}</p></div><svg class="rail" viewBox="0 0 430 36" aria-hidden="true"><path class="rail-base" d="M12 18 C100 2 180 33 270 18 S365 8 418 18"/><path ref="drawRef" class="rail-draw" d="M12 18 C100 2 180 33 270 18 S365 8 418 18"/></svg><ol><li v-for="(step,index) in steps" :key="step.id" :class="{ done:(completedIndices || []).includes(index), active:index === currentIndex }"><span v-if="(completedIndices || []).includes(index)" class="seal">✓</span><span class="vertical">{{ step.short }}</span></li></ol></section></template>
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { gsap } from 'gsap'
import { DrawSVGPlugin } from 'gsap/DrawSVGPlugin'

gsap.registerPlugin(DrawSVGPlugin)
export interface AlchemyStep { id: string; label: string; short: string }
const props = defineProps<{ steps: AlchemyStep[]; currentIndex: number; stateCopy: string; completedIndices?: number[] }>()
const drawRef = ref<SVGPathElement>()
const current = computed(() => props.steps[props.currentIndex])
const animate = () => {
  if (drawRef.value) gsap.to(drawRef.value, { drawSVG: `0% ${Math.max(3, ((props.currentIndex + 1) / props.steps.length) * 100)}%`, duration: .7, ease: 'power2.out' })
}
watch(() => props.currentIndex, animate)
onMounted(() => { if (drawRef.value) gsap.set(drawRef.value, { drawSVG: '0% 3%' }); animate() })
</script>
<style scoped>.step-rail{position:relative;text-align:center;width:min(520px,92vw)}.copy{min-height:112px}.kicker{margin:0 0 12px;color:var(--alchemy-jade);font-size:11px;letter-spacing:3px;font-weight:700}.copy h2{margin:0;color:var(--alchemy-ink);font-size:clamp(25px,3.2vw,42px);line-height:1.18;font-weight:700;letter-spacing:3px;text-shadow:0 1px rgba(255,255,255,.12)}.subcopy{margin:12px 0 0;color:var(--alchemy-muted);font-size:13px}.rail{width:100%;height:38px;margin-top:18px;overflow:visible}.rail-base,.rail-draw{fill:none;stroke-linecap:round}.rail-base{stroke:color-mix(in srgb,var(--alchemy-jade) 25%,transparent);stroke-width:1}.rail-draw{stroke:var(--alchemy-jade);stroke-width:2}ol{display:flex;justify-content:space-between;list-style:none;padding:0;margin:3px 4px 0}li{display:grid;place-items:center;min-width:31px;color:var(--alchemy-muted);font-size:12px;transition:.35s}.vertical{writing-mode:vertical-rl;letter-spacing:2px}.active{color:var(--alchemy-gold);font-weight:700}.done{color:var(--alchemy-ink)}.seal{display:grid;place-items:center;width:22px;height:22px;margin-bottom:6px;border:1px solid var(--alchemy-seal);color:var(--alchemy-seal);font-family:serif;font-weight:700;transform:rotate(-4deg)}.step-enter-active,.step-leave-active{transition:opacity .3s,transform .3s}.step-enter-from{opacity:0;transform:translateY(10px)}.step-leave-to{opacity:0;transform:translateY(-6px)}</style>
