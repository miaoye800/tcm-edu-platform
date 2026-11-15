<template>
  <main class="meridian-map-view">
    <header class="map-header">
      <div class="title-group">
        <p class="eyebrow">MERIDIAN ATLAS</p>
        <h1>人体经脉学习图</h1>
        <p class="map-intro">一具身体装下六门课：一个穴位对一节课，科目只决定高亮。点一个光点，看它挂着哪一节。</p>
      </div>
      <div class="header-actions">
        <el-button v-if="!path.hasPlan" :loading="path.planning" @click="planVisible = true">生成我的路线</el-button>
        <el-button v-else text @click="planVisible = true">调整顺序</el-button>
      </div>
    </header>

    <el-alert
      v-if="path.error"
      :title="path.fallback ? '先按标准路线走，随时可以重新规划' : path.error"
      type="warning"
      :closable="false"
      show-icon
    />

    <MeridianScene
      ref="sceneRef"
      v-model:layer="layer"
      @ask-assistant="openAssistant"
      @focus-node="syncFocus"
    />

    <PathPlanWizard v-model="planVisible" @confirmed="onConfirmed" />

    <ImmersiveLayer />
  </main>
</template>

<script setup lang="ts">
/**
 * `/path` 路由的壳。3D 场景、HUD、图层切换都在 `components/meridian/*` 里，
 * 视图层只做三件事：标题、规划入口、沉浸式对话层。
 *
 * 这样拆的理由不是"组件化好看"：Three 的渲染循环不能被路由级组件的重渲染打断，
 * 场景必须独占自己的生命周期。
 */
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { assessmentVisual } from '../config/assessment'
import { usePathStore } from '../stores/path'
import { useImmersiveStore } from '../stores/immersive'
import ImmersiveLayer from '../components/immersive/ImmersiveLayer.vue'
import MeridianScene from '../components/meridian/MeridianScene.vue'
import PathPlanWizard from '../components/path/PathPlanWizard.vue'

const route = useRoute()
const router = useRouter()
const path = usePathStore()
const immersive = useImmersiveStore()
const sceneRef = ref<InstanceType<typeof MeridianScene>>()
// 一具身体装全部学科：默认进「全部」，六门课的 293 个小节同时铺在 362 个穴位上
// （一穴一节，`assign.assign_all` 保证）。学科按钮是**筛选高亮**，不是切换整张图。
const layer = ref('all')
const planVisible = ref(false)

/**
 * 两段式确认走的是草稿 + 确认（向导自己完成），确认完再拉一次当前路线：
 * `current_path` 会滤掉草稿，只有确认过的行才会出现在地图上。
 *
 * **不要在这里关窗**。关了的话「路线已经生效」那一屏用户永远看不到——他刚点完
 * 一个需要读两分钟的确认动作，界面直接弹回地图，会以为没成功。关窗交给向导里
 * 的「去地图上看」。
 */
const onConfirmed = async () => {
  await path.load()
}

/** 深链：测评回跳与对话里的「在地图上看」都带 focus=<node_id>。 */
const focusFromQuery = () => {
  const focus = route.query.focus
  return typeof focus === 'string' ? focus : ''
}

const applyFocus = () => {
  const nodeId = focusFromQuery()
  if (!nodeId) return
  const scene = sceneRef.value as unknown as { selectByNode?: (id: string) => void } | null
  scene?.selectByNode?.(nodeId)
}

const openAssistant = async (payload: { id: string; name: string }) => {
  await immersive.begin(
    { id: payload.id, label: payload.name, detail: '来自经脉图', stage: 1 },
    { x: window.innerWidth * 0.62, y: window.innerHeight * 0.5 },
  )
}

onMounted(async () => {
  await nextTick()
  // 测评通过后回到地图：直接把对应的穴位推到特写，比放个礼花有用
  const passed = route.query[assessmentVisual.passReturnQuery]
  if (typeof passed === 'string') {
    applyFocusOf(passed)
    ElMessage.success('这一关过了，地图上对应的穴位已经点亮')
    return
  }
  applyFocus()
})

// 同一页面内再次点「在地图上看」时 query 会变，但组件不会重挂载
watch(() => route.query.focus, () => applyFocus())

/**
 * 点中刻度 / 穴位后把位置写回地址栏。
 *
 * 用 replace 不用 push：这是"我在看哪一条"，不是一次页面跳转，`push` 会让
 * 浏览器的后退键变成"退到上一条知识点"，退十几下才出得去。
 */
const syncFocus = (nodeId: string) => {
  if (route.query.focus === nodeId) return
  void router.replace({ query: { ...route.query, focus: nodeId } })
}

function applyFocusOf(nodeId: string) {
  const scene = sceneRef.value as unknown as { selectByNode?: (id: string) => void } | null
  scene?.selectByNode?.(nodeId)
}
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.meridian-map-view {
  color: var(--qh-text);
}

.map-header {
  display: flex;
  justify-content: space-between;
  align-items: end;
  gap: var(--qh-space-6);
  margin: var(--qh-space-1) var(--qh-space-1) var(--qh-space-4);
}

.title-group {
  min-width: 0;
}

.eyebrow {
  margin: 0 0 7px;
  color: var(--qh-color-jade);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1.7px;
}

.map-header h1 {
  margin: 0;
  color: var(--qh-text);
  font-family: var(--qh-font-display);
  font-size: 30px;
  letter-spacing: 0;
}

.map-intro {
  margin: 7px 0 0;
  color: var(--qh-text-muted);
  font-size: 13px;
}

.header-actions {
  display: flex;
  gap: var(--qh-space-2);
}

.dialog-copy {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.75;
}

@media (max-width: 760px) {
  .map-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .header-actions {
    width: 100%;
  }
}
</style>
