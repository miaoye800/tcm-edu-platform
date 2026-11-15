<template>
  <article
    class="surface"
    :class="[`lay-${layout}`, `kind-${slide.kind}`, { interactive, 'has-figure': showFigure }]"
    :style="[vars, layers]"
    :data-slide-id="slide.id"
    :data-slide-kind="slide.kind"
    :data-slide-layout="layout"
  >
    <!-- 纸面：母题现场生成。装饰预算 ≤2 = 一层底纹 + 一组四角角饰。 -->
    <svg v-if="paperSvg" class="ground" viewBox="0 0 100 56.25" preserveAspectRatio="none" aria-hidden="true" v-html="paperSvg" />
    <template v-if="ornamentSvg">
      <svg class="ornament is-tl" viewBox="0 0 20 20" aria-hidden="true">
        <g v-html="ornamentSvg" />
      </svg>
      <svg class="ornament is-tr" viewBox="0 0 20 20" aria-hidden="true">
        <g transform="translate(20 0) scale(-1 1)" v-html="ornamentSvg" />
      </svg>
      <svg class="ornament is-bl" viewBox="0 0 20 20" aria-hidden="true">
        <g transform="translate(0 20) scale(1 -1)" v-html="ornamentSvg" />
      </svg>
      <svg class="ornament is-br" viewBox="0 0 20 20" aria-hidden="true">
        <g transform="translate(20 20) scale(-1 -1)" v-html="ornamentSvg" />
      </svg>
    </template>

    <!-- 封面：左边一条朱砂竖线，标题压在左上，留白就是版面 -->
    <template v-if="layout === 'cover'">
      <span class="cover-rule" aria-hidden="true" />
      <div class="body cover-body">
        <p class="cover-kicker">中医药学 · 讲稿</p>
        <h1 class="cover-title">
          <input
            v-if="interactive"
            :value="slide.title"
            :maxlength="SLIDE_LIMITS.title"
            aria-label="封面标题"
            @input="emit('update', { title: inputValue($event) })"
          />
          <span v-else>{{ breakableTitle }}</span>
        </h1>
        <p class="cover-sub">
          <input
            v-if="interactive"
            :value="slide.subtitle"
            :maxlength="SLIDE_LIMITS.subtitle"
            placeholder="副标题（可空）"
            aria-label="封面副标题"
            @input="emit('update', { subtitle: inputValue($event) })"
          />
          <span v-else>{{ slide.subtitle }}</span>
        </p>
        <span class="cover-line" aria-hidden="true" />
      </div>
      <figure v-if="showFigure" class="figure is-cover">
        <img v-if="figureFile" :src="figureFile" :alt="figureRef?.attribution || ''" loading="lazy" />
        <svg v-else-if="figureSvg" viewBox="0 0 100 56.25" preserveAspectRatio="xMidYMid slice" aria-hidden="true" v-html="figureSvg" />
        <figcaption v-if="figureRef?.note">{{ figureRef.note }}</figcaption>
      </figure>
    </template>

    <!-- 章节页：巨号汉字序号做水印，标题居中 -->
    <template v-else-if="layout === 'section'">
      <span class="chapter-mark" aria-hidden="true">{{ chapterNumber }}</span>
      <div class="body section-body">
        <h1 class="section-title">
          <input
            v-if="interactive"
            :value="slide.title"
            :maxlength="SLIDE_LIMITS.title"
            aria-label="章节标题"
            @input="emit('update', { title: inputValue($event) })"
          />
          <span v-else>{{ slide.title }}</span>
        </h1>
        <p v-if="slide.subtitle || interactive" class="section-sub">
          <input
            v-if="interactive"
            :value="slide.subtitle"
            :maxlength="SLIDE_LIMITS.subtitle"
            placeholder="这一节解决什么问题"
            aria-label="章节副标题"
            @input="emit('update', { subtitle: inputValue($event) })"
          />
          <span v-else>{{ slide.subtitle }}</span>
        </p>
      </div>
    </template>

    <!-- 其余八种版式：眉标 + 标题 + 内容块 -->
    <template v-else>
      <div class="body page-body">
        <header class="head">
          <!-- 眉标为版式标签，标题里已有这个词时留空但占位：翻页时标题不会上下跳。 -->
          <p class="kicker" :class="{ 'is-blank': !kicker }">{{ kicker }}</p>
          <h1 class="title">
            <input
              v-if="interactive"
              :value="slide.title"
              :maxlength="SLIDE_LIMITS.title"
              aria-label="页标题"
              @input="emit('update', { title: inputValue($event) })"
            />
            <span v-else>{{ breakableTitle }}</span>
          </h1>
          <span class="title-rule" aria-hidden="true" />
        </header>

        <div class="content" :class="{ 'has-figure': showFigure }">
          <div class="blocks" :class="`as-${layout}`">
            <template v-for="(block, index) in viewBlocks" :key="`${block.type}-${index}`">
              <!-- 要点 / 收束：一条一句，菱形朱点 -->
              <p v-if="block.type === 'point'" class="b-point">
                <span class="dot" aria-hidden="true" />
                <input
                  v-if="interactive"
                  :value="block.text"
                  :maxlength="SLIDE_LIMITS.bullet"
                  :aria-label="`要点 ${index + 1}`"
                  @input="editText(block, inputValue($event))"
                />
                <span v-else class="text">{{ block.text }}</span>
                <button
                  v-if="interactive && block.proxy && slide.bullets.length > 1"
                  class="drop"
                  type="button"
                  :aria-label="`删除要点 ${index + 1}`"
                  @click.stop="emit('remove-bullet', block.origin)"
                >
                  ×
                </button>
              </p>

              <!-- 导览 / 流程：序号 + 条目。流程版式在 CSS 里横排并连线 -->
              <ol v-else-if="block.type === 'steps'" class="b-steps">
                <li v-for="(item, itemIndex) in block.items" :key="itemIndex">
                  <span class="num" aria-hidden="true">{{ String(itemIndex + 1).padStart(2, '0') }}</span>
                  <input
                    v-if="interactive"
                    :value="item"
                    :maxlength="SLIDE_LIMITS.bullet"
                    :aria-label="`第 ${itemIndex + 1} 条`"
                    @input="editItem(block, itemIndex, inputValue($event))"
                  />
                  <span v-else class="text">{{ item }}</span>
                </li>
              </ol>

              <!-- 对照（两栏）/ 矩阵（三栏）：斑马格 -->
              <div v-else-if="block.type === 'matrix'" class="b-matrix" :class="{ 'is-pair': block.columns.length <= 2 }">
                <section v-for="(column, columnIndex) in block.columns" :key="columnIndex" class="cell-col">
                  <h2 class="column-head">
                    <input
                      v-if="interactive"
                      :value="column.heading"
                      :maxlength="SLIDE_LIMITS.columnHeading"
                      :aria-label="`第 ${columnIndex + 1} 栏标题`"
                      @input="editColumnHeading(block, columnIndex, inputValue($event))"
                    />
                    <span v-else>{{ column.heading }}</span>
                  </h2>
                  <ul class="column-items">
                    <li v-for="(item, itemIndex) in column.items" :key="itemIndex">
                      <input
                        v-if="interactive"
                        :value="item"
                        :maxlength="SLIDE_LIMITS.bullet"
                        :aria-label="`第 ${columnIndex + 1} 栏第 ${itemIndex + 1} 条`"
                        @input="editCell(block, columnIndex, itemIndex, inputValue($event))"
                      />
                      <span v-else>{{ item }}</span>
                    </li>
                  </ul>
                </section>
              </div>

              <!-- 数据：数字卡。数值一排大号，标签在下 -->
              <div v-else-if="block.type === 'figures'" class="b-figures">
                <p class="figure-value">
                  <input
                    v-if="interactive"
                    :value="block.value"
                    maxlength="12"
                    aria-label="数值"
                    @input="editValue(block, inputValue($event))"
                  />
                  <span v-else>{{ block.value }}</span>
                </p>
                <p class="figure-label">
                  <input
                    v-if="interactive"
                    :value="block.heading"
                    :maxlength="SLIDE_LIMITS.columnHeading"
                    aria-label="数值标签"
                    @input="editHeading(block, inputValue($event))"
                  />
                  <span v-else>{{ block.heading }}</span>
                </p>
                <p v-if="block.source" class="figure-source">{{ block.source }}</p>
              </div>

              <!-- 警示：朱砂竖条 -->
              <p v-else-if="block.type === 'alert'" class="b-alert">
                <input
                  v-if="interactive"
                  :value="block.text"
                  :maxlength="SLIDE_LIMITS.bullet"
                  aria-label="警示内容"
                  @input="editText(block, inputValue($event))"
                />
                <span v-else>{{ block.text }}</span>
              </p>

              <!-- 引文：读原文才用的版式，字号略大、带出处 -->
              <blockquote v-else-if="block.type === 'quote'" class="b-quote">
                <input
                  v-if="interactive"
                  :value="block.text"
                  :maxlength="200"
                  aria-label="引文"
                  @input="editText(block, inputValue($event))"
                />
                <span v-else>{{ block.text }}</span>
                <cite v-if="block.source">{{ block.source }}</cite>
              </blockquote>

              <!-- 术语定义：术语一行、释义一行 -->
              <div v-else-if="block.type === 'def'" class="b-def">
                <h4 class="def-term">
                  <input
                    v-if="interactive"
                    :value="block.heading"
                    :maxlength="SLIDE_LIMITS.columnHeading"
                    aria-label="术语"
                    @input="editHeading(block, inputValue($event))"
                  />
                  <span v-else>{{ block.heading }}</span>
                </h4>
                <p class="def-text">
                  <input
                    v-if="interactive"
                    :value="block.text"
                    :maxlength="SLIDE_LIMITS.bullet"
                    aria-label="释义"
                    @input="editText(block, inputValue($event))"
                  />
                  <span v-else>{{ block.text }}</span>
                </p>
              </div>
            </template>

            <p v-if="!viewBlocks.length && !interactive" class="draft-note">这一页还没有正文。</p>
            <button
              v-if="interactive && canAddPoint"
              class="add"
              type="button"
              @click.stop="emit('add-bullet')"
            >
              ＋ 加一条
            </button>
          </div>

          <figure v-if="showFigure" class="figure">
            <img v-if="figureFile" :src="figureFile" :alt="figureRef?.attribution || ''" loading="lazy" />
            <svg v-else-if="figureSvg" viewBox="0 0 100 56.25" preserveAspectRatio="xMidYMid slice" aria-hidden="true" v-html="figureSvg" />
            <figcaption v-if="figureRef?.note">{{ figureRef.note }}</figcaption>
          </figure>
        </div>
      </div>
    </template>

    <footer class="foot">
      <span class="foot-title">{{ layout === 'cover' ? '' : deckTitle }}</span>
      <span class="page-no">{{ String(index + 1).padStart(2, '0') }} / {{ String(total).padStart(2, '0') }}</span>
    </footer>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { pptThemeVars } from '../../config/pptThemes'
import { sectionOrdinal } from '../../utils/slideOrdinal'
import { frameFor } from '../../render/frames'
import { ornamentMotif, paletteFor, paperMotif } from '../../render/skin'
import { renderMotif } from '../../render/motifs'
import { blocksOf, layoutOf, type ViewBlock } from '../../utils/slideBlocks'
import { SLIDE_LIMITS, SLIDE_PURPOSE_LABELS } from '../../types/ppt'
import type { AssetRef, SlideBlock, SlideNode } from '../../types/ppt'
import type { Archetype, Density } from '../../types/design'

const props = withDefaults(
  defineProps<{
    slide: SlideNode
    themeId: string
    index: number
    total: number
    deckTitle?: string
    interactive?: boolean
    /** 设计单里的纸面处方（`xuan-plain` 等）。没有设计单时用默认纸。 */
    paper?: string
    /** 设计单里的角饰处方（`vine-a` 等），`none` 不画。 */
    ornament?: string
  }>(),
  { deckTitle: '', interactive: false, paper: 'xuan-plain', ornament: 'none' },
)

const emit = defineEmits<{
  update: [patch: Partial<SlideNode>]
  'update-column': [payload: { index: number; patch: { heading?: string; items?: string[] } }]
  'add-bullet': []
  'remove-bullet': [index: number]
}>()

/** 老稿只有 `kind`，没有 `purpose`：版式换算见 `utils/slideBlocks.ts`（导出读同一份）。 */
const layout = computed<Archetype>(() => layoutOf(props.slide))

const viewBlocks = computed(() => blocksOf(props.slide))

/** 只有「一条一句」的版式给加条入口；矩阵/数据卡的加条是另一套编辑，不混进来。 */
const canAddPoint = computed(
  () =>
    props.slide.bullets.length < 6 &&
    ['points', 'summary', 'warning', 'agenda', 'process'].includes(layout.value) &&
    viewBlocks.value.every((block) => block.proxy),
)

/** 密度由块数反推：渲染时没有设计单在手，但块数就是密度的结果。 */
const density = computed<Density>(() => {
  const count = viewBlocks.value.length
  if (count >= 5) return 'dense'
  if (count <= 2) return 'airy'
  return 'normal'
})

const frame = computed(() => frameFor(layout.value, density.value))

/** 字号七级只从 frames 来：线框、成品、导出读同一套，改一处三处生效。 */
const layers = computed(() => ({
  '--s-title': `${frame.value.scale.title}cqw`,
  '--s-body': `${frame.value.scale.body}cqw`,
  '--s-kicker': `${frame.value.scale.kicker}cqw`,
}))

const vars = computed(() => pptThemeVars(props.themeId))

/**
 * 纸面与角饰：处方名 → 母题 id 的映射在 `render/skin.ts`，这里只负责给种子。
 * 底纹按页号取 seed——同一页永远同一张纸，翻页时纸纹不跳。
 */
const paperSvg = computed(() =>
  renderMotif(paperMotif(props.paper), { palette: paletteFor(props.themeId), seed: props.index + 1 }),
)
/** 大幅面版式：整张纸都是留白，角饰可以带界栏。正文页只留内花。 */
const isDisplayPage = computed(() => layout.value === 'cover' || layout.value === 'section' || layout.value === 'summary')
const ornamentSvg = computed(() =>
  renderMotif(ornamentMotif(props.ornament), {
    palette: paletteFor(props.themeId),
    params: { frame: isDisplayPage.value ? 1 : 0 },
  }),
)

/**
 * 素材槽位：`select` 步解析出来的那一张。
 *
 * 只认 figure / evidence：底纹与角饰是「画面的一部分」，不是这一页的图。
 * 早先取的是「第一个非 none 的引用」，而 ground 永远排在最前——于是每页都拿到
 * 一张纸纹当主图，封面那张馆藏彩绘反而永远不显示。
 */
const figureRef = computed<AssetRef | null>(
  () =>
    (props.slide.asset_refs ?? []).find(
      (ref) => ref.result !== 'none' && (ref.slot === 'figure' || ref.slot === 'evidence'),
    ) ?? null,
)
/** 参数化母题直接画成 SVG；库内资产走 `<img>`。 */
const figureSvg = computed(() =>
  figureRef.value?.result === 'parametric'
    ? renderMotif(figureRef.value.asset_id, { palette: paletteFor(props.themeId), seed: props.index + 7 })
    : '',
)
/**
 * 图片地址：`url` 优先。
 *
 * 远程馆藏素材不落盘，`file` 是空串——以前只认 `file`，于是「库里有图、
 * 稿子里也钉了图」，页面上却一张都看不见。没有页面的素材就是没用上。
 */
const figureFile = computed(() => figureRef.value?.url || figureRef.value?.file || '')
/** 画不出来（没文件、母题也没实现）就不占位：空灰块比不画更难看。 */
const showFigure = computed(() => Boolean(figureFile.value || figureSvg.value))

const kicker = computed(() => {
  const label = SLIDE_PURPOSE_LABELS[layout.value] ?? '正文'
  // 标题里已经有这个词时不再加眉标：「导览」压在「本讲导览」上只是噪音。
  return props.slide.title.includes(label) ? '' : label
})
const chapterNumber = computed(() => sectionOrdinal(props.slide.title))

/**
 * 标题里的**可断点**。
 *
 * 中文没有空格，浏览器默认「任何两字之间都能断」，于是「人参：补气第一药」会断成
 * 「补 / 气第一药」。这里只在标点后插零宽空格，配合 `word-break: keep-all`，
 * 断行就只能落在「：」「，」这些语义停顿处——这是中文标题的排法。
 */
const breakableTitle = computed(() => props.slide.title.replace(/([：:，,。.、；;！!？?·])/g, '$1\u200B'))

const inputValue = (event: Event) => (event.target as HTMLInputElement).value

const replaceAt = (items: string[], index: number, value: string) => {
  const next = [...items]
  next[index] = value
  return next
}

const setBulletAt = (index: number, value: string) =>
  emit('update', { bullets: replaceAt(props.slide.bullets, index, value) })

const writeBlock = (index: number, patch: Partial<SlideBlock>) =>
  emit('update', {
    blocks: (props.slide.blocks ?? []).map((block, position) => (position === index ? { ...block, ...patch } : block)),
  })

const editText = (row: ViewBlock, value: string) =>
  row.proxy ? setBulletAt(row.origin, value) : writeBlock(row.origin, { text: value })

const editHeading = (row: ViewBlock, value: string) => {
  if (row.proxy) {
    emit('update-column', { index: row.origin, patch: { heading: value } })
    return
  }
  writeBlock(row.origin, { heading: value })
}

const editItem = (row: ViewBlock, itemIndex: number, value: string) =>
  row.proxy ? setBulletAt(itemIndex, value) : writeBlock(row.origin, { items: replaceAt(row.items, itemIndex, value) })

const editColumnHeading = (row: ViewBlock, columnIndex: number, value: string) => {
  if (row.proxy) {
    emit('update-column', { index: columnIndex, patch: { heading: value } })
    return
  }
  writeBlock(row.origin, {
    columns: row.columns.map((column, index) => (index === columnIndex ? { ...column, heading: value } : column)),
  })
}

const editCell = (row: ViewBlock, columnIndex: number, itemIndex: number, value: string) => {
  if (row.proxy) {
    const column = props.slide.columns[columnIndex]
    if (!column) return
    emit('update-column', { index: columnIndex, patch: { items: replaceAt(column.items, itemIndex, value) } })
    return
  }
  writeBlock(row.origin, {
    columns: row.columns.map((column, index) =>
      index === columnIndex ? { ...column, items: replaceAt(column.items, itemIndex, value) } : column,
    ),
  })
}

const editValue = (row: ViewBlock, value: string) => {
  if (!row.proxy) writeBlock(row.origin, { value })
}
</script>

<style scoped lang="scss">
/*
 * 一块 16:9 的「纸」。
 *
 * 所有字号与间距用 cqw：舞台宽度变、排版等比变，不需要 transform: scale，
 * 也就不会出现缩放后文字发虚、输入框点不准的问题。
 * 所有颜色只读 --ppt-*：这套纸有自己的底色，跟页面主题无关。
 *
 * 版心取 frames.ts 的四边（天头 8.4 / 左右 9 / 地脚 7.2）：确认单的线框按同一组
 * 数字画，所以「确认过的样子」与「拿到的样子」是同一套几何。
 *
 * 注意：容器由父级（DeckStage 的 .stage / 胶片条的 .thumb）声明，
 * 元素不能查询自己——这里写 container-type 的话，自身 padding 的 cqw 会
 * 落到更外层的容器上，量出来是错的。
 */
.surface {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  padding: 8.4cqw 9cqw 7.2cqw;
  color: var(--ppt-fg);
  background: var(--ppt-bg);
  overflow: hidden;
}

/* 纸面底纹压在最底层；内容都在它之上，所以不设 z-index 时按 DOM 顺序覆盖。 */
.ground {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

/*
 * 角饰：四角各一枚，靠镜像而不是靠四份不同的画——传统角花本来就是四面对称的。
 *
 * 尺寸分两档，因为两档的**可用余地**根本不同：
 * 正文页版心是 9 × 8.4，角落只剩这么大一块，角饰超过 7cqw 就会压到眉标；
 * 封面/章节/尾页整张纸都是留白，可以给到 13cqw 让它有分量。
 */
.ornament {
  position: absolute;
  width: 9.5cqw;
  height: 9.5cqw;
  pointer-events: none;
}

.lay-cover .ornament,
.lay-section .ornament,
.lay-summary .ornament {
  width: 14cqw;
  height: 14cqw;
}

.ornament.is-tl {
  top: 0;
  left: 0;
}

/* 镜像写在 SVG 里（`<g transform>`），不写在 CSS 上：
   角饰的对称是图形的事，不是盒子的事——用 CSS transform 缩放，
   等于把「装饰镜像」和「整页缩放冒充自适应」写成同一个动作。 */
.ornament.is-tr {
  top: 0;
  right: 0;
}

.ornament.is-bl {
  bottom: 0;
  left: 0;
}

.ornament.is-br {
  right: 0;
  bottom: 0;
}

.body {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
}

/* ── 封面 ─────────────────────────────────────────────────────────────── */
.lay-cover {
  justify-content: center;
}

.cover-rule {
  position: absolute;
  top: 0;
  left: 0;
  width: 0.55cqw;
  height: 100%;
  background: var(--ppt-accent);
}

.cover-body {
  justify-content: center;
  /* 让出左上角饰：角花与标题抢同一块地方时，永远是文字先让。 */
  padding-left: 5.2cqw;
  max-width: 88%;
}

/* 有图位的封面要「左文右图」，标题栏让出右侧那块：无图时不必让。
   `max-width` 是相对版心（82cqw）算的，所以 64% ≈ 52.5cqw ≈ 6 个七级大字。 */
.lay-cover.has-figure .cover-body {
  max-width: 64%;
}

.cover-kicker {
  margin: 0 0 2.4cqw;
  color: var(--ppt-accent);
  font-size: var(--s-kicker);
  letter-spacing: 0.5cqw;
}

.cover-title {
  margin: 0;
  font-family: var(--qh-font-reading);
  font-size: var(--s-title);
  font-weight: 600;
  line-height: 1.22;
  letter-spacing: 0.08cqw;
  /* 只在标点处断行：按字硬断会把「补气」拆成「补 / 气」。 */
  word-break: keep-all;
  overflow-wrap: anywhere;
}

.cover-sub {
  margin: 1.8cqw 0 0;
  color: var(--ppt-muted);
  font-size: var(--s-body);
  letter-spacing: 0.18cqw;
}

.cover-line {
  display: block;
  width: 12cqw;
  height: 0.32cqw;
  margin-top: 3.2cqw;
  background: var(--ppt-accent);
}

/* ── 章节 ─────────────────────────────────────────────────────────────── */
.lay-section {
  justify-content: center;
}

.chapter-mark {
  position: absolute;
  top: 50%;
  right: 5cqw;
  color: color-mix(in srgb, var(--ppt-accent) 15%, transparent);
  font-family: var(--qh-font-reading);
  font-size: 17cqw;
  font-weight: 600;
  letter-spacing: 0.4cqw;
  line-height: 1;
  transform: translateY(-50%);
  user-select: none;
}

.section-body {
  justify-content: center;
  max-width: 72%;
}

.section-title {
  margin: 0;
  font-family: var(--qh-font-reading);
  font-size: var(--s-title);
  font-weight: 600;
  line-height: 1.3;
}

.section-sub {
  margin: 1.6cqw 0 0;
  color: var(--ppt-muted);
  font-size: var(--s-body);
  line-height: 1.5;
}

/* ── 内容页的公共头 ───────────────────────────────────────────────────── */
.page-body {
  gap: 0;
}

.kicker {
  margin: 0 0 1.1cqw;
  color: var(--ppt-accent);
  font-size: var(--s-kicker);
  letter-spacing: 0.42cqw;
}

/* 留空占位：字不可见，但行高还在，换页时标题不乱跳。 */
.kicker.is-blank {
  visibility: hidden;
}

.title {
  margin: 0;
  font-family: var(--qh-font-reading);
  font-size: var(--s-title);
  font-weight: 600;
  line-height: 1.3;
  word-break: keep-all;
  overflow-wrap: anywhere;
}

.title-rule {
  display: block;
  width: 8cqw;
  height: 0.3cqw;
  margin: 1.4cqw 0 0;
  background: var(--ppt-accent);
}

/* 内容区：默认单栏，有图位时切成「左文右图」。 */
.content {
  display: grid;
  flex: 1;
  grid-template-columns: minmax(0, 1fr);
  /* 行高必须钉成 1fr：auto 行会按图片的原始高度撑开，图一高就溢出纸面。 */
  grid-template-rows: minmax(0, 1fr);
  gap: 2.6cqw;
  min-height: 0;
  margin-top: 2.6cqw;
}

.content.has-figure {
  grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
}

.blocks {
  display: flex;
  flex-direction: column;
  gap: 1.7cqw;
  min-width: 0;
  min-height: 0;
  /* 块在版心内纵向均分。
     之前是「全部堆在顶上、下半页空着」——那看起来不是留白，是没排完。
     单块时回到居中：一句话的页，本来就该居中。 */
  justify-content: space-evenly;
}

.blocks:has(> :only-child) {
  justify-content: center;
}

/* 数据卡与流程横排：一页上一个动作一组，竖排会显得空。 */
.blocks.as-data,
.blocks.as-process {
  flex-direction: row;
  align-items: stretch;
  gap: 2.2cqw;
}

.blocks.as-data > *,
.blocks.as-process > * {
  flex: 1;
  min-width: 0;
}

/* ── 要点 ─────────────────────────────────────────────────────────────── */
.b-point {
  position: relative;
  display: flex;
  align-items: baseline;
  gap: 1.1cqw;
  margin: 0;
  font-size: var(--s-body);
  line-height: 1.62;
}

/* 只有一两条要点的页字号放大一档：2.2cqw 的字放在一页上显得内容漏了，
   2.6cqw 就显得是「一句一句讲」。 */
.blocks.as-points > .b-point:only-of-type,
.blocks.as-summary > .b-point:only-of-type,
.blocks.as-warning > .b-point:only-of-type {
  font-size: calc(var(--s-body) * 1.18);
}

.dot {
  flex: none;
  width: 0.62cqw;
  height: 0.62cqw;
  /* em 而不是 cqw：字号变了，菱点跟着首行中线走。 */
  margin-top: 0.44em;
  border-radius: 1px;
  background: var(--ppt-accent);
  transform: rotate(45deg);
}

.text {
  min-width: 0;
}

/* ── 导览 / 流程（steps） ─────────────────────────────────────────────── */
.b-steps {
  display: flex;
  flex-direction: column;
  gap: 1.5cqw;
  margin: 0;
  padding: 0;
  list-style: none;
}

/* 导览只有四五条，挤在纸的上半截会显得没排完：让它们把版心用完。 */
.blocks.as-agenda .b-steps {
  flex: 1;
  justify-content: space-around;
}

.b-steps li {
  display: flex;
  align-items: baseline;
  gap: 1.2cqw;
  font-size: var(--s-body);
  line-height: 1.55;
}

/* 导览只有四五个词，用正文号会显得「一页没写完」。放大到 1.4 倍，
   这一页才立得住——目录本来就该比正文响。 */
.blocks.as-agenda .b-steps li {
  font-size: calc(var(--s-body) * 1.4);
  gap: 2cqw;
}

.blocks.as-agenda .b-steps .num {
  font-size: 0.78em;
}

.num {
  flex: none;
  color: var(--ppt-accent);
  font-family: var(--qh-font-reading);
  font-size: 0.92em;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.1cqw;
}

/* 流程：横排 + 大号序号 + 一条连接线，读起来是「先后」而不是「并列」。 */
.as-process .b-steps {
  flex-direction: row;
  gap: 0;
}

.as-process .b-steps li {
  position: relative;
  flex: 1;
  flex-direction: column;
  gap: 1.1cqw;
  padding-right: 2cqw;
  font-size: calc(var(--s-body) * 1.05);
}

.as-process .b-steps .num {
  font-size: 1.9em;
  line-height: 1;
}

/* 轴线：每条 li 从序号右侧铺到末端，四段拼起来是一条贯穿的「流程线」。
   之前只画了一小截横杠，读起来像四个孤立的词。 */
.as-process .b-steps li::after {
  position: absolute;
  top: 1.05em;
  right: 1.4cqw;
  left: 3.4cqw;
  height: 1px;
  background: color-mix(in srgb, var(--ppt-fg) 22%, transparent);
  content: '';
}

.as-process .b-steps li:last-child::after {
  display: none;
}

/* ── 对照 / 矩阵 ──────────────────────────────────────────────────────── */
.b-matrix {
  display: grid;
  flex: 1;
  grid-auto-columns: minmax(0, 1fr);
  grid-auto-flow: column;
  gap: 0;
  min-height: 0;
}

.cell-col {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 0 1.8cqw;
}

/* 对照：中间一条竖线，两侧各一栏。 */
.b-matrix.is-pair .cell-col + .cell-col::before {
  position: absolute;
  top: 0.3cqw;
  left: 0;
  width: 1px;
  height: 100%;
  background: color-mix(in srgb, var(--ppt-fg) 16%, transparent);
  content: '';
}

.b-matrix.is-pair .cell-col:first-child {
  padding-left: 0;
}

/* 三栏以上：栏间一道竖细线。
   原来铺的是斑马灰底，在纸上像「这一栏被选中了」——表格的骨架该是线，不是底。 */
.b-matrix:not(.is-pair) .cell-col + .cell-col {
  border-left: 1px solid color-mix(in srgb, var(--ppt-fg) 12%, transparent);
}

.b-matrix:not(.is-pair) .cell-col:first-child {
  padding-left: 0;
}

.b-matrix:not(.is-pair) .cell-col:last-child {
  padding-right: 0;
}

.column-head {
  margin: 0 0 1.4cqw;
  color: var(--ppt-accent);
  font-family: var(--qh-font-reading);
  font-size: calc(var(--s-body) * 1.08);
  font-weight: 600;
}

.column-items {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 1.1cqw;
  justify-content: space-evenly;
  margin: 0;
  padding: 0;
  list-style: none;
}

.column-items li {
  position: relative;
  padding-left: 1.3cqw;
  font-size: var(--s-body);
  line-height: 1.5;
}

/* 小横杠做项目符号：按行高居中，否则放大看像浮在字上方的连字符。 */
.column-items li::before {
  position: absolute;
  top: 0.5lh;
  left: 0;
  width: 0.38em;
  height: 0.08em;
  background: var(--ppt-muted);
  content: '';
  transform: translateY(-50%);
}

/* ── 数据卡 ───────────────────────────────────────────────────────────── */
.b-figures {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 0.6cqw;
  padding: 1.5cqw 0 0;
  /* 一道细顶线 + 一片留白。之前铺了灰底，像从表格里抠出来的。
     0.16cqw 而不是 0.28：三张卡并排时，粗线会连成一条横贯全页的红杠。 */
  border-top: 0.16cqw solid var(--ppt-accent);
}

/* 卡与卡之间的竖线：等宽 flex 里内容各自左对齐，没有这刀就分不清「一组数字」。 */
.blocks.as-data > * + * {
  border-left: 1px solid color-mix(in srgb, var(--ppt-fg) 12%, transparent);
  padding-left: 2.2cqw;
}

.figure-value {
  margin: 0;
  color: var(--ppt-accent);
  font-family: var(--qh-font-reading);
  font-size: calc(var(--s-title) * 1.15);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  line-height: 1.1;
}

.figure-label {
  margin: 0;
  font-size: var(--s-body);
  line-height: 1.4;
}

.figure-source {
  margin: 0.4cqw 0 0;
  color: var(--ppt-muted);
  font-size: calc(var(--s-kicker) * 0.95);
}

/* ── 警示 ─────────────────────────────────────────────────────────────── */
.b-alert {
  position: relative;
  margin: 0;
  padding: 0.4cqw 0 0.4cqw 1.8cqw;
  font-size: var(--s-body);
  line-height: 1.6;
}

.b-alert::before {
  position: absolute;
  top: 0;
  left: 0;
  width: 0.34cqw;
  height: 100%;
  background: var(--ppt-accent);
  content: '';
}

/* ── 引文 ─────────────────────────────────────────────────────────────── */
.b-quote {
  position: relative;
  margin: 0;
  padding-left: 2.4cqw;
  color: var(--ppt-fg);
  font-family: var(--qh-font-reading);
  font-size: calc(var(--s-body) * 1.05);
  line-height: 1.75;
}

.b-quote::before {
  position: absolute;
  top: -0.4cqw;
  left: 0;
  color: color-mix(in srgb, var(--ppt-accent) 55%, transparent);
  content: '「';
  font-size: 2.2em;
  line-height: 1;
}

.b-quote cite {
  display: block;
  margin-top: 0.8cqw;
  color: var(--ppt-muted);
  font-size: calc(var(--s-kicker) * 1.05);
  font-style: normal;
}

/* ── 术语定义 ─────────────────────────────────────────────────────────── */
.b-def {
  display: grid;
  gap: 0.4cqw;
  padding-bottom: 0.6cqw;
  border-bottom: 1px solid var(--ppt-line);
}

.def-term {
  margin: 0;
  color: var(--ppt-accent);
  font-family: var(--qh-font-reading);
  font-size: calc(var(--s-body) * 1.05);
  font-weight: 600;
}

.def-text {
  margin: 0;
  font-size: calc(var(--s-body) * 0.94);
  line-height: 1.6;
}

/* 收束页整页压一层浅朱砂，让学生一眼知道「这是收尾」 */
/* 尾页原来整页铺了一层 7% 的朱砂，在宣纸上直接变成粉纸，和前面九页不是一套。
   现在换成一条压在纸顶的朱砂横线：一样是「收束」，但不换纸。 */
.lay-summary::before {
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  height: 0.5cqw;
  background: var(--ppt-accent);
  content: '';
}

/* ── 图位 ─────────────────────────────────────────────────────────────── */
.figure {
  position: relative;
  display: grid;
  /* 图占满剩余高度、角标自己一行：图高由行高定，不由图片原始尺寸定。 */
  grid-template-rows: minmax(0, 1fr) auto;
  gap: 0.8cqw;
  min-width: 0;
  min-height: 0;
  margin: 0;
  overflow: hidden;
}

.figure img,
.figure svg {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 0;
  border-radius: 0.3cqw;
  object-fit: cover;
}

/* 封面图位不占满整条右边：出血到页脚会压住页码，也让封面显得没装订。
   收进版心上下沿，做成一块「图版」。 */
.figure.is-cover {
  position: absolute;
  top: 8.4cqw;
  right: 0;
  bottom: 7.2cqw;
  width: 28%;
  height: auto;
}

/* 角标：`示意` / `AI 生成示意`。不标就是骗人——用户以为那是教材原图。 */
.figure figcaption {
  color: var(--ppt-muted);
  font-size: calc(var(--s-kicker) * 0.92);
  letter-spacing: 0.05em;
  text-align: right;
}

/* ── 页脚 ─────────────────────────────────────────────────────────────── */
.foot {
  position: relative;
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 2cqw;
  margin-top: 2.4cqw;
  padding-top: 1.1cqw;
  border-top: 1px solid var(--ppt-line);
  color: var(--ppt-muted);
  font-size: var(--s-kicker);
  letter-spacing: 0.1cqw;
}

.foot-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.page-no {
  flex: none;
  font-variant-numeric: tabular-nums;
}

.draft-note {
  margin: 2.4cqw 0 0;
  color: color-mix(in srgb, var(--ppt-muted) 70%, transparent);
  font-size: var(--s-body);
}

/* ── 内联编辑：输入框长得像文字，只有聚焦时才露出框 ──────────────────── */
.interactive input {
  width: 100%;
  padding: 0;
  border: 0;
  border-radius: 0.2cqw;
  color: inherit;
  background: transparent;
  font: inherit;
  letter-spacing: inherit;
  outline: none;
}

.interactive input::placeholder {
  color: color-mix(in srgb, var(--ppt-muted) 55%, transparent);
}

.interactive input:hover {
  background: color-mix(in srgb, var(--ppt-accent) 6%, transparent);
}

.interactive input:focus-visible {
  background: color-mix(in srgb, var(--ppt-accent) 9%, transparent);
  box-shadow: inset 0 -0.16cqw 0 var(--ppt-accent);
}

.drop {
  position: absolute;
  top: 0;
  right: 0;
  width: 1.6cqw;
  height: 1.6cqw;
  border: 0;
  border-radius: 50%;
  color: var(--ppt-muted);
  background: transparent;
  font-size: 1.3cqw;
  line-height: 1;
  opacity: 0;
  cursor: pointer;
  transition: opacity var(--qh-duration-fast) var(--qh-ease);
}

.interactive .b-point:hover .drop {
  opacity: 1;
}

.drop:hover {
  color: var(--ppt-accent);
  background: color-mix(in srgb, var(--ppt-accent) 12%, transparent);
}

.add {
  align-self: flex-start;
  padding: 0.35cqw 0.9cqw;
  border: 1px dashed color-mix(in srgb, var(--ppt-muted) 42%, transparent);
  border-radius: 0.3cqw;
  color: var(--ppt-muted);
  background: transparent;
  font-size: calc(var(--s-kicker) * 1.05);
  cursor: pointer;
}

.add:hover {
  color: var(--ppt-accent);
  border-color: var(--ppt-accent);
}
</style>
