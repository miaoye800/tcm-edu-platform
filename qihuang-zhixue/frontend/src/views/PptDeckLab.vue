<template>
  <main class="lab">
    <header class="lab-head">
      <p class="eyebrow">仅开发环境 · 版式验证</p>
      <h1>十种版式一张纸看一遍</h1>
      <p class="hint">
        同一份设计单渲出的十页：封面 / 导览 / 章节 / 要点 / 对照 / 矩阵 / 流程 / 数据 / 警示 / 收束。
        底纹与角饰来自参数化母题，图位来自选材结果（参数化一张、库内一张）。
        这张页不参与生产构建，只用来对版式。
      </p>
      <div class="toggles">
        <label>纸面
          <select v-model="paper">
            <option value="xuan-plain">宣纸</option>
            <option value="ink-night">墨夜</option>
            <option value="celadon-mist">青瓷</option>
            <option value="cinnabar-warm">朱墨</option>
          </select>
        </label>
        <label>角饰
          <select v-model="ornament">
            <option value="vine-a">卷草</option>
            <option value="branch-b">折枝</option>
            <option value="bamboo-d">竹叶</option>
            <option value="fret-e">回纹</option>
            <option value="scroll-c">缠枝</option>
            <option value="none">不用</option>
          </select>
        </label>
        <label>主题
          <select v-model="themeId">
            <option v-for="theme in pptThemes" :key="theme.id" :value="theme.id">{{ theme.label }}</option>
          </select>
        </label>
      </div>

      <!-- 五款角饰并排：评审时比的是「哪一款配得上这页」，不是一个个点开看。 -->
      <div class="orn-row">
        <figure v-for="option in ORNAMENTS" :key="option.value" class="orn-cell" :class="{ 'is-current': ornament === option.value }">
          <div class="orn-box" v-html="ornPreview(option.value)" />
          <figcaption>{{ option.label }}</figcaption>
        </figure>
      </div>
    </header>

    <!--
      手写场景：这一页没有经过任何"版式原型"，是逐元素写出来的。
      存在的目的是回答一个问题——**这套 DSL 够不够画出一张参考图那样的封面**。
      左图出血、右侧竖排标题、两道界格竖线、一枚印章、四角角花。
    -->
    <section class="hand">
      <p class="eyebrow">手写场景 · 不经版式原型</p>
      <div class="frame">
        <SceneSurface :scene="handScene" :theme-id="themeId" font-pair-id="li-kai" :total="1" hide-foot />
      </div>
    </section>

    <!--
      远程素材：两张图说明两件事。
      左：馆藏图**不落盘**，稿子里只钉一条 wsrv 代理地址就能直接上版面。
      右：同一条地址故意写错——图位消失、版式照旧，不裂图。这是断网时的样子。
    -->
    <section class="hand">
      <p class="eyebrow">远程素材 · 钉 URL 与断图降级</p>
      <div class="pair">
        <figure v-for="item in remoteScenes" :key="item.label">
          <div class="frame">
            <SceneSurface
              :scene="item.scene"
              :theme-id="themeId"
              font-pair-id="li-kai"
              :total="1"
              :assets="item.assets"
              hide-foot
            />
          </div>
          <figcaption>{{ item.label }}</figcaption>
        </figure>
      </div>
    </section>

    <!--
      模型自己排的版面：这三页由 `scripts/smoke_scene_art.py` 调真模型产出，
      没有经过任何版式原型。看的是「同一套基元能不能排出三种完全不同的页面」。
    -->
    <section v-if="modelPages.length" class="hand">
      <p class="eyebrow">模型排的版面 · scene_art 实测 {{ modelPages.length }} 页</p>
      <div class="pair">
        <figure v-for="item in modelPages" :key="item.title">
          <div class="frame">
            <SceneSurface
              :scene="item.scene"
              :theme-id="themeId"
              font-pair-id="li-kai"
              :total="modelPages.length"
              hide-foot
            />
          </div>
          <figcaption>{{ item.title }}</figcaption>
        </figure>
      </div>
    </section>

    <section class="grid">
      <article v-for="(slide, index) in slides" :key="slide.id" class="cell">
        <p class="cell-cap">{{ slide.purpose }} · {{ slide.kind }}</p>
        <div class="frame">
          <SlideRenderer
            :slide="slide"
            :theme-id="themeId"
            :index="index"
            :total="slides.length"
            deck-title="黄芪的临床应用"
          />
        </div>
      </article>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import SlideRenderer from '../components/ppt/SlideRenderer.vue'
import SceneSurface from '../components/ppt/SceneSurface.vue'
import { normalizeScene } from '../render/scene'
import type { Scene } from '../render/scene'
import { pptThemes } from '../config/pptThemes'
import { ensurePptFonts } from '../utils/pptFonts'
import { ornamentMarkup, ornamentMotif, paperMotif } from '../render/skin'
import { fallbackScene } from '../render/sceneFallback'
import type { SlideNode } from '../types/ppt'

/** 模型排的样张。由 `backend/scripts/smoke_scene_art.py` 写到 `public/lab/`。
 *
 *  读不到不是错误：这个文件只在跑过冒烟之后存在，lab 页照常显示其余样张。
 */
const modelPages = ref<{ title: string; scene: Scene }[]>([])
onMounted(async () => {
  ensurePptFonts()
  try {
    const response = await fetch('/lab/scene-samples.json', { cache: 'no-store' })
    if (!response.ok) return
    modelPages.value = (await response.json()) as { title: string; scene: Scene }[]
  } catch {
    modelPages.value = []
  }
})

const ORNAMENTS = [
  { value: 'vine-a', label: '卷草' },
  { value: 'branch-b', label: '折枝' },
  { value: 'bamboo-d', label: '竹叶' },
  { value: 'fret-e', label: '回纹' },
  { value: 'scroll-c', label: '缠枝' },
] as const

/** 角饰母题画在 0..20 的方格内：外面套一层 svg 才看得见。 */
const ornPreview = (id: string): string =>
  `<svg viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">${ornamentMarkup(id, themeId.value, { frame: 1 })}</svg>`

const themeId = ref('paper')
/** 与真任务同形：纸面、角饰是**处方名**（`xuan-plain` / `vine-a`），不是母题 id。 */
const paper = ref('xuan-plain')
const ornament = ref('vine-a')

/** 预设十页，一页一种版式。
 *
 *  主题用**黄芪**而不是人参：库里现在只有黄芪的饮片实拍，写成人参然后配黄芪的图，
 *  这一页就是在教错东西。等`photo-renshen-*` 入库了再把主题换回来。
 */
const rawSlides = ref<SlideNode[]>([
  {
    id: 's0',
    kind: 'cover',
    purpose: 'cover',
    title: '黄芪：补气固表',
    subtitle: '性味、归经与临床应用',
    bullets: [],
    columns: [],
    note: '',
    source_indexes: [0],
    blocks: [],
    // 封面不放图位：`select` 对 figure 位从不返回参数化结果，封面图要么来自库、要么走无图版式。
    asset_refs: [],
  },
  {
    id: 's1',
    kind: 'agenda',
    purpose: 'agenda',
    title: '本讲导览',
    subtitle: '',
    bullets: ['性味归经', '主要功效', '配伍要点', '使用禁忌'],
    columns: [],
    note: '',
    source_indexes: [0],
    blocks: [],
  },
  {
    id: 's2',
    kind: 'section',
    purpose: 'section',
    title: '一、性味归经',
    subtitle: '甘，微温；归肺、脾经',
    bullets: [],
    columns: [],
    note: '',
    source_indexes: [0],
    blocks: [],
  },
  {
    id: 's3',
    kind: 'bullets',
    purpose: 'points',
    title: '主要功效',
    subtitle: '',
    bullets: ['补气升阳：中气下陷、久泻脱肛', '固表止汗：表虚自汗、气虚感冒', '利水消肿：气虚水肿、小便不利', '托毒生肌：疮疡难溃、久不收口'],
    columns: [],
    note: '学生常把「补气」与「补阳」混为一谈，这里点一句。',
    source_indexes: [0],
    blocks: [],
    // 真任务里这张由 `select` 按 subjects 命中；这里硬写是为了让版式能看。
    asset_refs: [
      {
        slot: 'figure',
        asset_id: 'photo-huangqi-01',
        result: 'hit',
        provenance: 'library',
        license: 'CC0',
        attribution: 'Fumikas Sagisavas',
        file: '/assets/pictorial/photo-huangqi-01.webp',
        note: '',
      },
    ],
  },
  {
    id: 's4',
    kind: 'compare',
    purpose: 'compare',
    title: '黄芪与党参',
    subtitle: '',
    bullets: [],
    columns: [
      { heading: '黄芪', items: ['补气升阳，固表止汗', '偏于走表，升提', '中气下陷常用'] },
      { heading: '党参', items: ['补中益气，健脾益肺', '偏于守中，平补', '脾肺气虚常用'] },
    ],
    note: '',
    source_indexes: [0],
    blocks: [],
  },
  {
    id: 's5',
    kind: 'bullets',
    purpose: 'matrix',
    title: '归经与主治对照',
    subtitle: '',
    bullets: [],
    columns: [
      { heading: '脾', items: ['食少倦怠', '久泻脱肛'] },
      { heading: '肺', items: ['表虚自汗', '气虚感冒'] },
      { heading: '水湿', items: ['气虚水肿', '小便不利'] },
    ],
    note: '',
    source_indexes: [0],
    blocks: [],
  },
  {
    id: 's6',
    kind: 'bullets',
    purpose: 'process',
    title: '从采收到入药',
    subtitle: '',
    bullets: ['春秋采挖', '去须晒干', '切片生用', '蜜炙补中'],
    columns: [],
    note: '',
    source_indexes: [0],
    blocks: [],
  },
  {
    id: 's7',
    kind: 'bullets',
    purpose: 'data',
    title: '用量与疗程',
    subtitle: '',
    bullets: ['9-30 克 常规用量', '30-60 克 大剂升提', '2-4 周 连续疗程'],
    columns: [],
    note: '',
    source_indexes: [0],
    blocks: [
      { type: 'figures', value: '9-30', heading: '常规用量（克）', text: '', source: '《中国药典》2020' },
      { type: 'figures', value: '30-60', heading: '大剂升提（克）', text: '', source: '' },
      { type: 'figures', value: '2-4', heading: '连续疗程（周）', text: '', source: '' },
    ],
  },
  {
    id: 's8',
    kind: 'bullets',
    purpose: 'warning',
    title: '使用注意',
    subtitle: '',
    bullets: ['表实邪盛、气滞湿阻者忌服', '阴虚阳亢、疮疡初起者不宜'],
    columns: [],
    note: '',
    source_indexes: [0],
    blocks: [],
  },
  {
    id: 's9',
    kind: 'summary',
    purpose: 'summary',
    title: '一页收束',
    subtitle: '',
    bullets: ['补气之要，在升与固两字', '升则举陷，固则止汗'],
    columns: [],
    note: '',
    source_indexes: [0],
    blocks: [
      { type: 'point', text: '补气之要，在升与固两字' },
      { type: 'point', text: '升则举陷，固则止汗' },
      { type: 'def', heading: '一句话', text: '气不足者补之，气下陷者举之；补而不滞，方为上工。' },
    ],
  },
])

/**
 * 十页样张：每页用兜底生成器铺一份场景。
 *
 * 这个调试页的用处就是「同一批内容，换纸、换角饰、换主题，看场景渲染器跟不跟得上」。
 * 生成期产出的场景与这里同源（都是 `scene_fallback` 的十原型构造），所以这里看到的
 * 就是真任务会渲出来的样子。
 */
const slides = computed<SlideNode[]>(() =>
  rawSlides.value.map((slide) => {
    const ref = (slide.asset_refs ?? []).find((item) => item.result !== 'none')
    return {
      ...slide,
      scene: fallbackScene({
        archetype: slide.purpose ?? 'points',
        title: slide.title,
        subtitle: slide.subtitle,
        blocks: (slide.blocks ?? []) as never,
        bullets: slide.bullets,
        columns: (slide.columns ?? []) as never,
        figureId: ref?.asset_id,
        figureCaption: ref?.note,
        figureKind: ref?.result === 'parametric' ? 'motif' : 'asset',
        paperMotif: paperMotif(paper.value),
        ornamentMotif: ornamentMotif(ornament.value),
      }),
    }
  }),
)

/**
 * 手写场景：一张「左图右字、竖排、带界格与印章」的封面。
 *
 * 这不是模板，是一个**测试用例**。它可以被拆成任意别的版式——换 background 就是
 * 满版照片压字，删掉角花就是极简，把竖排改横排就是常规封面。DSL 的表达力在这里
 * 得到证明，而不是在"我们有几套模板"里。
 */
/** 远程素材样张。地址真的在，取不到只是说明断网时不会裂。 */
const WELLCOME = 'https://wsrv.nl/?url=https%3A%2F%2Fiiif.wellcomecollection.org%2Fimage%2FL0039301%2Ffull%2F1600%2C%2F0%2Fdefault.jpg&w=1600&output=webp'

const remoteScenes = computed(() => {
  const make = (url: string) =>
    normalizeScene({
      note: '满版馆藏图压字',
      background: { kind: 'asset', motif: 'paper-plain', params: {}, asset: 'plate-demo', fit: 'cover', veil: { color: 'bg', opacity: 0.62, direction: 'from_left' }, tone: 'bg' },
      elements: [
        { type: 'text', box: [8, 18, 46, 18], z: 22, text: { content: '黄精', role: 'title', size: 9, font: 'display', color: 'fg', write: 'h', align: 'left', line_height: 1.2, tracking: 0.06, max_lines: 2 } },
        { type: 'text', box: [8, 37, 40, 6], z: 22, text: { content: '《本草品汇精要》明抄本 · 滁州黄精', role: 'subtitle', size: 2.2, font: 'body', color: 'muted', write: 'h', align: 'left', line_height: 1.5, tracking: 0, max_lines: 2 } },
      ],
    })
  return [
    { label: '钉住 wsrv 地址 · 正常', scene: make(WELLCOME), assets: { 'plate-demo': WELLCOME } },
    { label: '地址失效 · 图位消失，版式不塌', scene: make(WELLCOME), assets: { 'plate-demo': WELLCOME.replace('L0039301', 'L0000000') } },
  ]
})

const handScene = computed(() =>
  normalizeScene({
    note: '左图出血 + 右侧竖排 + 两道界格 + 印章 + 四角角花',
    background: { kind: 'motif', motif: paperMotif(paper.value), params: {}, asset: '', fit: 'cover', veil: null, tone: 'bg' },
    elements: [
      { type: 'asset', box: [3, 5, 36, 47], z: 15, asset: 'photo-huangqi-01', fit: 'contain', caption: '饮片实拍 · 示意' },
      { type: 'rule', box: [70, 8, 0.08, 34], z: 12, orient: 'v', weight: 0.08, fill: 'line' },
      { type: 'rule', box: [88, 8, 0.08, 34], z: 12, orient: 'v', weight: 0.08, fill: 'line' },
      {
        type: 'text', box: [73.5, 9.5, 6.4, 31], z: 22,
        text: { content: '中药文化知识', role: 'title', size: 4.6, font: 'display', color: 'fg', write: 'v', align: 'left', line_height: 1.35, tracking: 0.35, max_lines: 6 },
      },
      {
        type: 'text', box: [82, 11, 5.2, 28], z: 21,
        text: { content: '中医药学 · 讲稿', role: 'subtitle', size: 1.8, font: 'body', color: 'muted', write: 'v', align: 'left', line_height: 1.6, tracking: 0.2, max_lines: 8 },
      },
      { type: 'seal', box: [76.6, 6.4, 2.6, 2.6], z: 23, content: '传统', shape: 'square', fill: 'accent' },
      { type: 'motif', box: [0, 0, 14, 14], z: 8, motif: ornamentMotif(ornament.value), flip: 'none', fill: null },
      { type: 'motif', box: [86, 0, 14, 14], z: 8, motif: ornamentMotif(ornament.value), flip: 'x', fill: null },
      { type: 'motif', box: [0, 42.25, 14, 14], z: 8, motif: ornamentMotif(ornament.value), flip: 'y', fill: null },
      { type: 'motif', box: [86, 42.25, 14, 14], z: 8, motif: ornamentMotif(ornament.value), flip: 'xy', fill: null },
    ],
  }),
)
</script>

<style scoped lang="scss">
.lab {
  display: grid;
  gap: 20px;
  padding: 28px 32px 48px;
  background: var(--qh-bg);
  color: var(--qh-fg);
}

.lab-head {
  max-width: 78ch;
}

.eyebrow {
  margin: 0;
  color: var(--qh-accent);
  font-size: 12px;
  letter-spacing: 0.06em;
}

h1 {
  margin: 6px 0 8px;
  font-family: var(--qh-font-reading);
  font-size: 22px;
}

.hint {
  margin: 0;
  color: var(--qh-fg-muted);
  font-size: 13px;
  line-height: 1.7;
}

.toggles {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 14px;
  font-size: 12px;
  color: var(--qh-fg-muted);
}

.toggles label {
  display: grid;
  gap: 4px;
}

.toggles select {
  padding: 5px 8px;
  border: 1px solid var(--qh-line);
  border-radius: 6px;
  background: var(--qh-surface);
  color: var(--qh-fg);
  font: inherit;
  font-size: 12px;
}

.orn-row {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 18px;
}

.orn-cell {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 4px;
  border: 1px solid transparent;
  border-radius: 6px;
}

.orn-cell.is-current {
  border-color: var(--qh-accent);
}

.orn-box {
  width: 88px;
  height: 88px;
  border: 1px solid var(--qh-line);
  border-radius: 4px;
  background: #faf7f0;
}

.orn-box :deep(svg) {
  display: block;
  width: 100%;
  height: 100%;
}

.orn-cell figcaption {
  color: var(--qh-fg-muted);
  font-size: 11px;
  text-align: center;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
  gap: 18px;
}

.cell {
  display: grid;
  gap: 6px;
}

.cell-cap {
  margin: 0;
  color: var(--qh-fg-faint);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.04em;
}

/* 纸上容器：SlideSurface 里的 cqw 以这里为参照物。 */
/* 手写场景给足宽度：这一页是用来细看竖排、界格、印章的，缩到三列宽就看不出对不对。 */
.hand {
  display: grid;
  gap: 8px;
}

.hand .frame {
  max-width: 920px;
}

.pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.pair figure {
  display: grid;
  gap: 4px;
  margin: 0;
}

.pair figcaption {
  color: var(--qh-fg-muted);
  font-size: 11px;
}

.frame {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  border: 1px solid var(--qh-line);
  border-radius: 6px;
  background: #faf7f0;
  box-shadow: var(--qh-shadow-1);
  container-type: inline-size;
  overflow: hidden;
}
</style>
