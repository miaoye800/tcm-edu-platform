<template>
  <main class="stage4-lab tw-text-text">
    <header class="lab-head"><div><p>STAGE 04 / VISUAL FOUNDATION</p><h1>观感层选件货架</h1><span>仅开发环境可见</span></div><el-tag effect="plain">tokens only</el-tag></header>
    <section class="lab-grid">
      <article class="lab-panel inspira-panel"><GridPattern/><Meteors/><BorderBeam/><PulseRing/><div class="panel-copy"><p class="kicker">INSPIRA UI / 4 options</p><h2>光迹、边框与背景</h2><p>Border Beam、Grid Pattern、Meteors、Pulse Ring 均以现有 tokens 换肤。</p></div></article>
      <article class="lab-panel"><p class="kicker">MOTION-V</p><h2>基础进出场</h2><button class="motion-toggle" type="button" @click="showMotion = !showMotion">{{ showMotion ? '收起药签' : '展开药签' }}</button><AnimatePresence><motion.div v-if="showMotion" class="motion-card" :initial="{ opacity: 0, y: 12 }" :animate="{ opacity: 1, y: 0 }" :exit="{ opacity: 0, y: -8 }" :transition="{ duration: stage4Visual.motion.duration, ease: stage4Visual.motion.ease }">气随脉行，信息在此安静出现。</motion.div></AnimatePresence></article>
      <article class="lab-panel x-panel"><p class="kicker">ELEMENT PLUS X / BUBBLE</p><Bubble content="小岐已收到你的药性线索。" placement="start" variant="outlined" shape="corner" :typing="false" /><div class="typewriter-wrap"><Typewriter content="正在依次归纳：性味、归经、主治。" :typing="{ step: 2, interval: 55, suffix: '|' }" /></div></article>
      <article class="lab-panel states-panel"><p class="kicker">STANDARD STATES</p><div class="state-tabs"><button v-for="item in states" :key="item" type="button" :class="{ active: activeState === item }" @click="activeState = item">{{ item }}</button></div><StateEmpty v-if="activeState === '空态'" size="sm" action-text="去添加" @action="notice('小岐已记下你的下一步。')"/><StateLoading v-else-if="activeState === '加载'" size="sm"/><StateError v-else size="sm" @action="notice('已发出重试请求。')"/></article>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { AnimatePresence, motion } from 'motion-v'
import { Bubble, Typewriter } from 'vue-element-plus-x'
import { ElMessage } from 'element-plus'
import { StateEmpty, StateError, StateLoading } from '../components/state'
import BorderBeam from '../components/stage4/inspira/BorderBeam.vue'
import GridPattern from '../components/stage4/inspira/GridPattern.vue'
import Meteors from '../components/stage4/inspira/Meteors.vue'
import PulseRing from '../components/stage4/inspira/PulseRing.vue'
import { stage4Visual } from '../config/stage4Visual'

const showMotion = ref(true)
const states = ['空态', '加载', '错误']
const activeState = ref('空态')
const notice = (message: string) => ElMessage.success(message)
</script>

<style scoped lang="scss">
.stage4-lab{min-height:100vh;padding:clamp(var(--qh-space-5),4vw,var(--qh-space-10));background:var(--qh-page-bg)}.lab-head{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--qh-space-4);max-width:1240px;margin:0 auto var(--qh-space-6)}.lab-head p,.kicker{margin:0;color:var(--qh-color-gold);font-size:11px;font-weight:900;letter-spacing:1.4px}.lab-head h1{margin:var(--qh-space-2) 0;color:var(--qh-text);font-family:var(--qh-font-display);font-size:clamp(28px,4vw,42px);letter-spacing:0}.lab-head span{color:var(--qh-text-muted);font-size:13px}.lab-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--qh-space-4);max-width:1240px;margin:auto}.lab-panel{position:relative;display:grid;align-content:start;gap:var(--qh-space-3);min-height:260px;padding:var(--qh-space-6);overflow:hidden;border:1px solid var(--qh-border);border-radius:var(--qh-radius-card-sm);background:var(--qh-card-fill);box-shadow:var(--qh-inner-highlight),var(--qh-shadow-soft)}.lab-panel h2{position:relative;margin:0;color:var(--qh-text);font-family:var(--qh-font-display);font-size:22px}.panel-copy{position:relative;z-index:1;max-width:28rem}.panel-copy p:last-child{margin:0;color:var(--qh-text-muted);font-size:13px;line-height:1.75}.motion-toggle{width:max-content;padding:var(--qh-space-2) var(--qh-space-3);border:1px solid var(--qh-border);border-radius:var(--qh-radius-sm);color:var(--qh-text);background:color-mix(in srgb,var(--qh-color-jade) 10%,transparent);cursor:pointer}.motion-card{padding:var(--qh-space-3);border-left:2px solid var(--qh-color-jade);color:var(--qh-text-muted);background:color-mix(in srgb,var(--qh-color-jade) 8%,transparent);font-size:13px;line-height:1.7}.x-panel :deep(.el-bubble){--el-color-primary:var(--qh-color-jade);--el-fill-color-light:color-mix(in srgb,var(--qh-color-jade) 8%,transparent)}.x-panel :deep(.el-bubble-content){color:var(--qh-text);background:color-mix(in srgb,var(--qh-color-jade) 10%,transparent);border-color:var(--qh-border)}.typewriter-wrap{padding:var(--qh-space-3);border:1px solid color-mix(in srgb,var(--qh-color-blue) 32%,transparent);border-radius:var(--qh-radius-sm);color:var(--qh-text-muted);background:color-mix(in srgb,var(--qh-color-blue) 8%,transparent);font-size:13px;line-height:1.7}.states-panel{grid-row:span 1}.state-tabs{display:flex;gap:var(--qh-space-2)}.state-tabs button{padding:var(--qh-space-1) var(--qh-space-2);border:1px solid var(--qh-border);border-radius:var(--qh-radius-pill);color:var(--qh-text-muted);background:transparent;cursor:pointer;font-size:12px}.state-tabs button.active{border-color:var(--qh-color-jade);color:var(--qh-text);background:color-mix(in srgb,var(--qh-color-jade) 13%,transparent)}@media(max-width:760px){.lab-grid{grid-template-columns:1fr}.lab-head{display:grid}.lab-panel{min-height:230px;padding:var(--qh-space-4)}}
</style>
