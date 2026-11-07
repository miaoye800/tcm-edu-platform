<template>
  <main class="studio" data-qh-theme="light">
    <PptTopicPanel :studio="studio" />

    <section class="sheet">
      <!-- 稿头：左「稿名 + 体量」，右一把工具。放在舞台之外，不遮纸面内容。 -->
      <header class="deck-head">
        <div class="deck-id">
          <p class="deck-name">{{ deckName }}</p>
          <p class="deck-meta">
            <template v-if="hasSlides">
              {{ slides.length }} 页 · {{ stats.bullets }} 条要点 · {{ groundedLabel }}
            </template>
            <template v-else>立骨 → 谋篇 → 敷演 → 校色</template>
          </p>
        </div>

        <PptToolbar
          :editable="phase === 'editing'"
          :slide-count="slides.length"
          :theme-id="themeId"
          :save-status="saveStatus"
          :save-message="saveMessage"
          :exporting="exporting"
          @add="addSlide"
          @remove="removeSlide(current)"
          @theme="setTheme"
          @export="exportPptx"
        />
      </header>

      <div class="stage-zone">
        <div class="stage-wrap">
          <DeckStage
            v-if="hasSlides && currentSlide"
            :slides="slides"
            :current="current"
            :theme-id="themeId"
            :deck-title="deckName"
            @update="updateSlide(current, $event)"
            @update-column="applyColumn"
            @add-bullet="addBullet(current)"
            @remove-bullet="removeBullet(current, $event)"
            @go="goTo"
          />

          <!-- 空态／生成中／失败：都落在同一张 16:9 的纸位上，版面不跳。 -->
          <div v-else class="ghost" :class="{ 'is-paused': phase === 'awaiting-outline' }">
            <StateLoading v-if="phase === 'generating'" size="md" :title="loadingTitle" :description="loadingHint" />
            <StateError
              v-else-if="phase === 'failed'"
              size="md"
              title="这一炉没炼成"
              :description="errorMessage || '后端没有返回可用的幻灯，请稍后再试。'"
              action-text="再起一稿"
              @action="generate"
            />
            <!-- 谋篇已停：焦点在右边的稿纸上。这里不再说「白纸」——骨架其实已经立好了。
                 说明写在纸张左上角，是因为右半张纸会被大纲面板压住。 -->
            <div v-else-if="phase === 'awaiting-outline'" class="paused-note">
              <p class="paused-title">骨架已立 · {{ outline.length }} 页</p>
              <p class="paused-sub">右边可以改标题、调页序、删页，确认后逐页敷演。</p>
            </div>
            <StateEmpty
              v-else
              icon="herb"
              title="案上还是白纸"
              description="左边定个题，点「起稿」，我先把骨架立起来。"
            />
          </div>
        </div>

        <p v-if="hasSlides" class="stage-hint">← → 翻页 · 纸面上的字直接改</p>

        <div v-if="saveStatus === 'conflict'" class="conflict" role="alert">
          <p>这份讲稿在别处被改过（服务端已是第 {{ version }} 版）。</p>
          <div class="conflict-actions">
            <button type="button" @click="reloadFromServer">重新载入</button>
            <button type="button" class="primary" @click="keepMine">保留我的版本</button>
          </div>
        </div>
      </div>

      <SlideFilmstrip
        v-if="hasSlides"
        :slides="slides"
        :current="current"
        :theme-id="themeId"
        :limit="SLIDE_LIMITS.slides"
        @select="goTo"
        @add="addSlide"
      />

      <!-- 谋篇之后停流，右侧滑出一叠可改的稿纸。 -->
      <OutlineConfirm
        v-if="phase === 'awaiting-outline'"
        :outline="outline"
        :deck-title="deckName"
        :error="errorMessage"
        :busy="false"
        @rename="renameOutline($event.index, $event.value)"
        @points="updateOutlinePoints($event.index, splitPoints($event.value))"
        @remove="removeOutlineSlide"
        @move="moveOutlineSlide($event.from, $event.to)"
        @confirm="confirm"
      />
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { StateEmpty, StateError, StateLoading } from '../components/state'
import DeckStage from '../components/ppt/DeckStage.vue'
import OutlineConfirm from '../components/ppt/OutlineConfirm.vue'
import PptToolbar from '../components/ppt/PptToolbar.vue'
import PptTopicPanel from '../components/ppt/PptTopicPanel.vue'
import SlideFilmstrip from '../components/ppt/SlideFilmstrip.vue'
import { usePptStudio } from '../composables/usePptStudio'
import { SLIDE_LIMITS } from '../types/ppt'

/** 每道工序在空态里的一句话，替用户说清「现在在等什么」。 */
const STEP_HINTS: Record<string, string> = {
  frame: '先搭骨架：封面、章节、收束',
  outline: '再排页序：每页讲哪一件事',
  compose: '逐页敷演：把要点摊成能讲的话',
  polish: '最后校色：换纸、对齐、收口',
}

const route = useRoute()
const studio = usePptStudio()

const {
  phase,
  errorMessage,
  stepStates,
  outline,
  deckTitle,
  title,
  slides,
  current,
  currentSlide,
  saveStatus,
  saveMessage,
  version,
  themeId,
  stats,
  generate,
  confirm,
  renameOutline,
  updateOutlinePoints,
  removeOutlineSlide,
  moveOutlineSlide,
  updateSlide,
  addBullet,
  removeBullet,
  addSlide,
  removeSlide,
  setTheme,
  goTo,
  loadNodes,
  refreshRecent,
  openResource,
  reloadFromServer,
  keepMine,
} = studio

const exporting = ref(false)

const hasSlides = computed(() => slides.value.length > 0)
/**
 * 稿名取封面页的标题，而不是只取模型给的 deck_title：
 * 用户在大纲里改了封面、或在纸面上直接改，稿头必须跟着变，否则一份稿两个名字。
 */
const deckName = computed(() => {
  const cover = slides.value[0]
  const coverTitle = cover?.kind === 'cover' ? cover.title.trim() : ''
  return coverTitle || deckTitle.value || title.value || '未起稿'
})

/** 「依据 12 页」里的两个「页」挤在一起读不通，满有据时直接说满。 */
const groundedLabel = computed(() =>
  stats.value.grounded >= stats.value.slides ? '每页有据' : `${stats.value.grounded}/${stats.value.slides} 页有据`,
)

const activeStep = computed(() => stepStates.value.find((item) => item.state === 'active') ?? null)
const loadingTitle = computed(() => (activeStep.value ? `正在${activeStep.value.label}` : '正在起稿'))
const loadingHint = computed(() => {
  const id = activeStep.value?.id
  return (id && STEP_HINTS[id]) || '四道工序正在跑，纸马上就铺开'
})

/**
 * 分栏里改一个字要连同整栏回写：SlideSurface 只报「哪一栏、改了什么」，
 * 合并逻辑留在页面这一层，组件就永远只关心自己显示的那一栏。
 */
const applyColumn = (payload: { index: number; patch: { heading?: string; items?: string[] } }) => {
  const slide = currentSlide.value
  if (!slide) return
  const columns = slide.columns.map((column, position) =>
    position === payload.index ? { ...column, ...payload.patch } : column,
  )
  updateSlide(current.value, { columns })
}

/** 大纲里一条要点一行；空行丢掉，交给后端按上限裁剪。 */
const splitPoints = (value: string) =>
  value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 5)

const exportPptx = async () => {
  if (!slides.value.length || exporting.value) return
  exporting.value = true
  try {
    // pptxgenjs 三百多 KB，只有真要导出时才值得付这份钱——按需加载，切一个独立 chunk。
    const { exportDeckToPptx } = await import('../utils/pptxExport')
    const filename = await exportDeckToPptx({
      title: deckName.value,
      themeId: themeId.value,
      slides: slides.value,
    })
    ElMessage.success(`已导出 ${filename}`)
  } catch (error) {
    const reason = error instanceof Error && error.message ? error.message : '未知原因'
    ElMessage.error(`导出失败：${reason}`)
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  void loadNodes()
  void refreshRecent()
  const target = route.query.resource
  if (typeof target === 'string' && target) await openResource(target)
})
</script>

<style scoped lang="scss">
/*
 * 三区：左栏定题、右区一张讲台、下面一条胶片。
 * 高度精确吃掉 .content-scroll 的上下 padding（22 + 40），页面内只留左栏一根滚动条。
 */
.studio {
  display: grid;
  grid-template-columns: minmax(288px, 320px) minmax(0, 1fr);
  height: calc(100vh - var(--qh-topbar-height) - 62px);
  min-height: 520px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-card);
  background: var(--qh-surface);
  overflow: hidden;
}

.sheet {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  /* 讲台：顶上一束极淡的暖光，让纸浮起来，而不是贴在灰底上。 */
  background:
    radial-gradient(120% 88% at 50% 0%, color-mix(in srgb, var(--qh-accent) 5%, transparent), transparent 62%),
    var(--qh-surface-sunken);
}

.deck-head {
  display: flex;
  flex-wrap: wrap;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: var(--qh-space-3);
  min-height: 58px;
  padding: 10px 18px;
  border-bottom: 1px solid var(--qh-line);
  background: color-mix(in srgb, var(--qh-surface) 74%, transparent);
}

.deck-id {
  min-width: 0;
}

.deck-name {
  margin: 0;
  overflow: hidden;
  color: var(--qh-fg);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-ui);
  font-weight: 600;
  letter-spacing: 0.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.deck-meta {
  margin: 3px 0 0;
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.3px;
}

/*
 * 舞台位：自己就是尺寸容器，纸的宽度取「可用宽 / 可用高换算宽 / 1080」三者最小，
 * 于是永远完整可见——不裁、不缩字、不出现滚动条。cqh 不支持时才退回按宽排。
 */
.stage-zone {
  position: relative;
  display: grid;
  flex: 1;
  min-height: 0;
  padding: 22px 28px 18px;
  place-items: center;
  container-type: size;
}

.stage-wrap {
  position: relative;
  width: min(1080px, 100%);
  aspect-ratio: 16 / 9;
}

@supports (height: 1cqh) {
  .stage-wrap {
    width: min(1080px, 100%, calc(100cqh * 16 / 9));
  }
}

.stage-wrap :deep(.stage) {
  width: 100%;
  height: 100%;
  aspect-ratio: auto;
}

/* 空态用同一张 16:9 的虚线纸位，起稿后版面不跳。 */
.ghost {
  display: grid;
  width: 100%;
  height: 100%;
  border: 1px dashed var(--qh-line-strong);
  border-radius: var(--qh-radius-card-sm);
  background: color-mix(in srgb, var(--qh-surface) 55%, transparent);
  place-items: center;
}

.ghost :deep(.state-empty),
.ghost :deep(.state-loading),
.ghost :deep(.state-error) {
  background: transparent;
}

/* 谋篇暂停：说明贴左上角，右半张纸被大纲面板压着。 */
.ghost.is-paused {
  padding: clamp(16px, 4cqw, 30px);
  place-items: start;
}

.paused-note {
  max-width: 15em;
}

.paused-title {
  margin: 0;
  color: var(--qh-fg);
  font-family: var(--qh-font-reading);
  font-size: clamp(13px, 2.4cqw, 17px);
  font-weight: 600;
  letter-spacing: 0.4px;
}

.paused-sub {
  margin: 8px 0 0;
  color: var(--qh-fg-muted);
  font-size: clamp(11px, 1.7cqw, 13px);
  line-height: 1.6;
}

.stage-hint {
  position: absolute;
  bottom: 8px;
  left: 50%;
  margin: 0;
  color: var(--qh-fg-faint);
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.4px;
  pointer-events: none;
  transform: translateX(-50%);
}

.conflict {
  position: absolute;
  top: 12px;
  left: 50%;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: var(--qh-space-3);
  max-width: min(560px, calc(100% - 48px));
  padding: 8px 12px;
  border: 1px solid var(--qh-accent-line);
  border-left-width: 2px;
  border-radius: var(--qh-radius-sm);
  background: var(--qh-accent-soft);
  transform: translateX(-50%);
}

.conflict p {
  margin: 0;
  color: var(--qh-accent-strong);
  font-size: var(--qh-text-footnote);
}

.conflict-actions {
  display: flex;
  flex: none;
  gap: 6px;
}

.conflict-actions button {
  padding: 4px 9px;
  border: 1px solid var(--qh-accent-line);
  border-radius: var(--qh-radius-pill);
  color: var(--qh-accent-strong);
  background: var(--qh-surface-raised);
  font-size: var(--qh-text-footnote);
  cursor: pointer;
}

.conflict-actions button.primary {
  color: var(--qh-accent-ink);
  background: var(--qh-accent);
}

/* 大纲面板自己带 absolute + 滑入动画，这里只负责把它钉在右侧整条上。 */
.confirm-layer {
  top: 0;
}

@media (max-width: 1240px) {
  .studio {
    grid-template-columns: minmax(272px, 288px) minmax(0, 1fr);
  }

  .stage-zone {
    padding: 18px 20px 14px;
  }
}

@media (max-width: 1020px) {
  .studio {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(220px, 40%) minmax(0, 1fr);
  }

  .deck-head {
    padding: 8px 12px;
  }

  .stage-zone {
    padding: 14px 14px 10px;
  }

  .stage-hint {
    display: none;
  }
}

/* 900px 以下 .content-scroll 的 padding 变成 18/34，高度要跟着改。 */
@media (max-width: 900px) {
  .studio {
    height: calc(100vh - var(--qh-topbar-height) - 52px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .conflict {
    transition: none;
  }
}
</style>
