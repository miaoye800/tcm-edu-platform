<template>
  <aside ref="root" class="mascot" :class="[`is-${heat}`, { 'is-offline': offline, 'is-failed': failed, 'is-celebrating': celebrating }]" aria-label="小岐在炉边照看丹火">
    <div class="speech"><span>{{ line }}</span></div>
    <div class="character">
      <img :src="xiaoqi" alt="小岐" />
      <svg class="prop" viewBox="0 0 120 150" aria-hidden="true">
        <g v-if="heat === 'complete' || celebrating" class="hands"><path d="M30 80 Q48 67 58 84"/><path d="M72 84 Q82 67 100 80"/></g>
        <g v-else-if="stepId === 'verify'" class="book"><path d="M30 70 Q57 58 61 76 Q69 58 96 70 L96 109 Q71 98 61 113 Q51 98 30 109 Z"/><path d="M61 76 V113"/></g>
        <g v-else class="fan"><path d="M60 96 L60 132"/><path d="M60 96 C24 84 24 46 60 30 C96 46 96 84 60 96 Z"/><path d="M36 62 L84 62 M60 38 V91"/></g>
      </svg>
    </div>
  </aside>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { gsap } from 'gsap'
import xiaoqi from '../../assets/mascot/xiaoqi-full.png'
import type { AlchemyHeat } from '../../config/alchemy-stage'
const props = defineProps<{ heat: AlchemyHeat; stepId: string; offline?: boolean; failed?: boolean; celebrating?: boolean; motion: number; frequency: number }>()
const root = ref<HTMLElement>(); let ctx: gsap.Context | undefined
const lines: Record<string, string[]> = { source: ['我去翻翻《本草》！', '这味药藏得真深呀…'], relation: ['呼——火候正好！', '药证要一根根理顺。'], distill: ['考点入炉，听我扇！', '武火一到，精神啦！'], verify: ['咦，这条要再核一遍…', '小册子里有答案。'], compose: ['快成卷啦，别眨眼！', '收好这一炉心血！'] }
const line = computed(() => props.failed ? '这炉废了，师父再开一炉嘛' : props.offline ? '炉火渐弱，我再加把劲…' : lines[props.stepId]?.[Math.floor(props.frequency * 7) % 2] ?? '我看着炉火呢！')
const animate = () => { ctx?.revert(); ctx = gsap.context(() => { const speed = props.heat === 'fierce' ? .42 : 1.25; gsap.to('.character', { y: -7 * props.motion, duration: 1.5 / props.motion, repeat: -1, yoyo: true, ease: 'sine.inOut' }); if (props.celebrating) gsap.to('.character', { y: -32 * props.motion, duration: .25, repeat: 3, yoyo: true, ease: 'power2.out' }); if (!props.failed) gsap.to('.fan', { rotation: props.heat === 'fierce' ? 16 : 7, transformOrigin: '50% 92%', duration: speed, repeat: -1, yoyo: true, ease: 'sine.inOut' }); }, root.value) }
watch(() => [props.heat, props.stepId, props.offline, props.failed, props.celebrating, props.motion], animate); onMounted(animate); onBeforeUnmount(() => ctx?.revert())
</script>
<style scoped>
.mascot{position:absolute;z-index:4;right:max(7%,calc(50% - 520px));bottom:100px;width:220px;pointer-events:none}.character{position:relative;height:270px;filter:drop-shadow(0 16px 15px rgba(0,0,0,.32))}.character img{position:absolute;right:3px;bottom:0;height:255px;width:auto;object-fit:contain}.prop{position:absolute;right:20px;bottom:42px;width:95px;height:120px;overflow:visible;stroke:var(--alchemy-gold);stroke-width:4;stroke-linecap:round;stroke-linejoin:round;fill:color-mix(in srgb,var(--alchemy-panel) 86%,transparent)}.speech{position:absolute;z-index:2;right:78px;top:-8px;min-width:160px;max-width:220px;padding:12px 15px;border:1px solid color-mix(in srgb,var(--alchemy-gold) 65%,transparent);border-radius:17px 17px 5px 17px;background:color-mix(in srgb,var(--alchemy-panel) 92%,transparent);color:var(--alchemy-ink);font-size:13px;line-height:1.45;box-shadow:0 10px 22px rgba(0,0,0,.2)}.speech:after{position:absolute;right:18px;bottom:-8px;width:13px;height:13px;background:inherit;border-right:1px solid color-mix(in srgb,var(--alchemy-gold) 65%,transparent);border-bottom:1px solid color-mix(in srgb,var(--alchemy-gold) 65%,transparent);transform:rotate(45deg);content:''}.is-offline .prop{stroke:var(--alchemy-jade)}.is-failed .character{transform:rotate(-5deg)}.is-failed .fan{display:none}.hands{fill:none}.book{stroke:var(--alchemy-jade)}@media(max-width:900px){.mascot{right:2%;width:170px;bottom:120px}.character{height:215px}.character img{height:205px}.speech{right:40px;top:-20px;min-width:130px;font-size:11px}.prop{right:5px;bottom:25px;width:78px}}
</style>
<style scoped>.prop{right:8px;bottom:22px;width:66px;height:90px}</style>
