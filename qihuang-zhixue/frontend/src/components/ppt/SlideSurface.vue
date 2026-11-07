<template>
  <article
    class="surface"
    :class="[`kind-${slide.kind}`, { interactive }]"
    :style="vars"
    :data-slide-id="slide.id"
    :data-slide-kind="slide.kind"
  >
    <!-- 封面：左边一条朱砂竖线，标题压在左上，留白就是版面 -->
    <template v-if="slide.kind === 'cover'">
      <div class="cover-rule" aria-hidden="true" />
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
          <span v-else>{{ slide.title }}</span>
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
    </template>

    <!-- 章节页：章号做水印，标题居中 -->
    <template v-else-if="slide.kind === 'section'">
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

    <!-- 导览 / 要点 / 总结：标题 + 条目 -->
    <template v-else>
      <div class="body list-body">
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
            <span v-else>{{ slide.title }}</span>
          </h1>
          <span class="title-rule" aria-hidden="true" />
        </header>

        <!-- 对比版式：两栏，中间一条竖线；栏数不足时退化成普通条目，不画空栏 -->
        <div v-if="showColumns" class="columns">
          <section v-for="(column, columnIndex) in slide.columns" :key="columnIndex" class="column">
            <h2 class="column-head">
              <input
                v-if="interactive"
                :value="column.heading"
                :maxlength="SLIDE_LIMITS.columnHeading"
                :aria-label="`第 ${columnIndex + 1} 栏标题`"
                @input="emit('update-column', { index: columnIndex, patch: { heading: inputValue($event) } })"
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
                  @input="emit('update-column', { index: columnIndex, patch: { items: replaceAt(column.items, itemIndex, inputValue($event)) } })"
                />
                <span v-else>{{ item }}</span>
              </li>
            </ul>
          </section>
        </div>

        <ol
          v-else
          class="items"
          :class="{ numbered: slide.kind === 'agenda', sparse: slide.bullets.length <= 2 }"
        >
          <li v-for="(bullet, index) in slide.bullets" :key="index">
            <span v-if="slide.kind === 'agenda'" class="num" aria-hidden="true">{{ String(index + 1).padStart(2, '0') }}</span>
            <span v-else class="dot" aria-hidden="true" />
            <input
              v-if="interactive"
              :value="bullet"
              :maxlength="SLIDE_LIMITS.bullet"
              :aria-label="`要点 ${index + 1}`"
              @input="emit('update', { bullets: replaceAt(slide.bullets, index, inputValue($event)) })"
            />
            <span v-else class="text">{{ bullet }}</span>
            <button
              v-if="interactive && slide.bullets.length > 1"
              class="drop"
              type="button"
              :aria-label="`删除要点 ${index + 1}`"
              @click.stop="emit('remove-bullet', index)"
            >
              ×
            </button>
          </li>
          <li v-if="interactive && slide.bullets.length < 6" class="adder">
            <button type="button" class="add" @click.stop="emit('add-bullet')">＋ 加一条</button>
          </li>
        </ol>

        <p v-if="!interactive && !slide.bullets.length && !showColumns" class="draft-note">这一页还没有正文。</p>
      </div>
    </template>

    <footer class="foot">
      <span class="foot-title">{{ slide.kind === 'cover' ? '' : deckTitle }}</span>
      <span class="page-no">{{ String(index + 1).padStart(2, '0') }} / {{ String(total).padStart(2, '0') }}</span>
    </footer>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { pptThemeVars } from '../../config/pptThemes'
import { sectionOrdinal } from '../../utils/slideOrdinal'
import { SLIDE_LIMITS, SLIDE_KIND_LABELS } from '../../types/ppt'
import type { SlideNode } from '../../types/ppt'

const props = withDefaults(
  defineProps<{
    slide: SlideNode
    themeId: string
    index: number
    total: number
    deckTitle?: string
    interactive?: boolean
  }>(),
  { deckTitle: '', interactive: false },
)

const emit = defineEmits<{
  update: [patch: Partial<SlideNode>]
  'update-column': [payload: { index: number; patch: { heading?: string; items?: string[] } }]
  'add-bullet': []
  'remove-bullet': [index: number]
}>()

const vars = computed(() => pptThemeVars(props.themeId))
const kicker = computed(() => {
  const label = SLIDE_KIND_LABELS[props.slide.kind] ?? '正文'
  // 标题里已经有这个词时不再加眉标：「导览」压在「本讲导览」上只是噪音。
  return props.slide.title.includes(label) ? '' : label
})
const chapterNumber = computed(() => sectionOrdinal(props.slide.title))
/** compare 至少要有两栏才画分栏，否则退化成条目——空栏比不分栏更难看。 */
const showColumns = computed(() => props.slide.kind === 'compare' && props.slide.columns.length >= 2)

const inputValue = (event: Event) => (event.target as HTMLInputElement).value

const replaceAt = (items: string[], index: number, value: string) => {
  const next = [...items]
  next[index] = value
  return next
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
  padding: 5.2cqw 6cqw 4.2cqw;
  color: var(--ppt-fg);
  background: var(--ppt-bg);
  overflow: hidden;
}

.body {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
}

/* ── 封面 ─────────────────────────────────────────────────────────────── */
.kind-cover {
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
  padding-left: 1.6cqw;
}

.cover-kicker {
  margin: 0 0 2.4cqw;
  color: var(--ppt-accent);
  font-size: 1.5cqw;
  letter-spacing: 0.5cqw;
}

.cover-title {
  margin: 0;
  font-family: var(--qh-font-reading);
  font-size: 6.2cqw;
  font-weight: 600;
  line-height: 1.22;
  letter-spacing: 0.08cqw;
}

.cover-sub {
  margin: 1.8cqw 0 0;
  color: var(--ppt-muted);
  font-size: 2.2cqw;
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
.kind-section {
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
  font-size: 4.6cqw;
  font-weight: 600;
  line-height: 1.3;
}

.section-sub {
  margin: 1.6cqw 0 0;
  color: var(--ppt-muted);
  font-size: 2cqw;
  line-height: 1.5;
}

/* ── 列表（导览 / 要点 / 总结） ────────────────────────────────────────── */
.kicker {
  margin: 0 0 1.1cqw;
  color: var(--ppt-accent);
  font-size: 1.4cqw;
  letter-spacing: 0.42cqw;
}

/* 留空占位：字不可见，但行高还在，换页时标题不乱跳。 */
.kicker.is-blank {
  visibility: hidden;
}

.title {
  margin: 0;
  font-family: var(--qh-font-reading);
  font-size: 3.4cqw;
  font-weight: 600;
  line-height: 1.3;
}

.title-rule {
  display: block;
  width: 8cqw;
  height: 0.3cqw;
  margin: 1.4cqw 0 0;
  background: var(--ppt-accent);
}

.items {
  display: flex;
  flex-direction: column;
  gap: 1.7cqw;
  margin: 3.2cqw 0 0;
  padding: 0;
  list-style: none;
}

.items li {
  position: relative;
  display: flex;
  align-items: baseline;
  gap: 1.1cqw;
  font-size: 2.05cqw;
  line-height: 1.5;
}

/*
 * 只有一两条要点的页（配伍、使用注意）字号放大一档：
 * 同样是一页纸，2cqw 的字放在上面显得内容漏了，2.6cqw 就显得是「一句一句讲」。
 */
.items.sparse {
  gap: 2.5cqw;
  margin-top: 4cqw;
}

.items.sparse li {
  font-size: 2.6cqw;
  line-height: 1.45;
}

.items.sparse .num {
  font-size: 2.2cqw;
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

.num {
  flex: none;
  color: var(--ppt-accent);
  font-family: var(--qh-font-reading);
  font-size: 1.9cqw;
  letter-spacing: 0.1cqw;
}

.text {
  min-width: 0;
}

/* 总结页整页压一层浅朱砂，让学生一眼知道「这是收尾」 */
.kind-summary {
  background: color-mix(in srgb, var(--ppt-accent) 7%, var(--ppt-bg));
}

/* ── 对比 ─────────────────────────────────────────────────────────────── */
.columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  flex: 1;
  gap: 3.4cqw;
  margin-top: 3cqw;
  min-height: 0;
}

.column {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding-left: 1.6cqw;
}

.column + .column::before {
  position: absolute;
  top: 0.3cqw;
  left: -1.7cqw;
  width: 1px;
  height: 100%;
  background: var(--ppt-line);
  content: '';
}

.column-head {
  margin: 0 0 1.5cqw;
  color: var(--ppt-accent);
  font-family: var(--qh-font-reading);
  font-size: 2.4cqw;
  font-weight: 600;
}

.column-items {
  display: flex;
  flex-direction: column;
  gap: 1.2cqw;
  margin: 0;
  padding: 0;
  list-style: none;
}

.column-items li {
  position: relative;
  padding-left: 1.3cqw;
  font-size: 1.85cqw;
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

/* ── 页脚 ─────────────────────────────────────────────────────────────── */
.foot {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 2cqw;
  margin-top: 2.4cqw;
  padding-top: 1.1cqw;
  border-top: 1px solid var(--ppt-line);
  color: var(--ppt-muted);
  font-size: 1.25cqw;
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
  font-size: 1.9cqw;
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

.interactive .items li {
  align-items: flex-start;
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

.interactive .items li:hover .drop {
  opacity: 1;
}

.drop:hover {
  color: var(--ppt-accent);
  background: color-mix(in srgb, var(--ppt-accent) 12%, transparent);
}

.adder {
  list-style: none;
}

.add {
  padding: 0.35cqw 0.9cqw;
  border: 1px dashed color-mix(in srgb, var(--ppt-muted) 42%, transparent);
  border-radius: 0.3cqw;
  color: var(--ppt-muted);
  background: transparent;
  font-size: 1.5cqw;
  cursor: pointer;
}

.add:hover {
  color: var(--ppt-accent);
  border-color: var(--ppt-accent);
}
</style>
