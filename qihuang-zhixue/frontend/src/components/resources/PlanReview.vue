<template>
  <div class="plan-backdrop" role="dialog" aria-modal="true" aria-labelledby="plan-title">
    <section class="plan-review">
      <header class="plan-head">
        <div>
          <p class="eyebrow">生成前确认 · {{ kindLabel }}</p>
          <h2 id="plan-title">这次准备这样制作</h2>
          <p class="hint">方案是这次任务的施工图。你可以逐项改、写反馈让我们重拟，或推翻重来；<strong>确认之后才会开始生成</strong>。</p>
        </div>
        <button class="ghost" type="button" :disabled="busy" @click="$emit('cancel')">暂不生成</button>
      </header>

      <div class="facts">
        <!-- 标题是成品页面上最大的那行字。以前它由「用户原句 + ·规划」拼出来，
             用户看着别扭却改不了——给一个能改的输入框比任何解释都实在。 -->
        <label v-if="draft.kind === 'doc'" class="wide">标题<input v-model="draft.title" placeholder="文档标题，8-20 字" /></label>
        <label class="wide">目标<textarea v-model="draft.goal" rows="2" /></label>
        <label>受众<input v-model="draft.audience" /></label>
        <label>组织方式<input v-model="draft.organization" :title="draft.organization" /></label>
        <label class="wide">呈现方式<input v-model="draft.presentation" /></label>
      </div>

      <!--
        导图这一块是「方案能不能被信任」的关键：先把系统怎么理解你的话摆出来，
        再给真正能改的数。以前层数只写在一行说明文字里，改了生成读不到。
      -->
      <section v-if="mindSpec" class="map-spec">
        <header class="map-spec-head">
          <h3>这次这样排</h3>
          <span class="basis" :class="mindSpec.basis === 'model' ? 'is-model' : 'is-rules'">
            {{ mindSpec.basis === 'model' ? '按你的原话解读' : '按规则拟的初稿' }}
          </span>
        </header>
        <ul v-if="mindSpec.interpretation?.length" class="interpret">
          <li v-for="line in mindSpec.interpretation" :key="line">{{ line }}</li>
        </ul>
        <p v-else class="hint">这一版没有留下解读说明；可直接改层数与结构。</p>
        <div class="map-controls">
          <label>层数
            <select :value="mindDepth" @change="setDepth(Number(($event.target as HTMLSelectElement).value))">
              <option v-for="level in depthOptions" :key="level" :value="level">{{ level }} 层</option>
            </select>
          </label>
          <label>画法
            <select :value="mindStructure" @change="setStructure(($event.target as HTMLSelectElement).value)">
              <option v-for="option in mindStructureOptions" :key="option.id" :value="option.id" :disabled="option.maxDepth < mindDepth">
                {{ option.label }}（≤{{ option.maxDepth }} 层）
              </option>
            </select>
          </label>
          <label>每支至少
            <select :value="mindLeaves" @change="setLeaves(Number(($event.target as HTMLSelectElement).value))">
              <option v-for="count in [2, 3, 4, 5, 6]" :key="count" :value="count">{{ count }} 条</option>
            </select>
          </label>
        </div>
        <p class="hint">
          一级分支就是下面的 {{ draft.units.length }} 支：加减分支，图上就是加减分支。
          <template v-if="depthCapped">当前画法最多 {{ mindStructureMax }} 层，要更深请换成向右分支。</template>
        </p>
      </section>

      <!--
        文稿这一块回答的是「这篇到底写什么、多长、每节长什么样」。
        以前卡片上只有「目标 / 受众 / 组织方式」三行字，体裁与章节是关键词规则
        定的，用户在面板上改什么都没用——现在这三项都能改，且生成真的读它。
      -->
      <section v-if="docSpec" class="map-spec">
        <header class="map-spec-head">
          <h3>这次这样写</h3>
          <span class="basis" :class="docSpec.basis === 'model' ? 'is-model' : 'is-rules'">
            {{ docSpec.basis === 'model' ? '按你的原话解读' : '按规则拟的初稿' }}
          </span>
        </header>
        <ul v-if="docSpec.interpretation?.length" class="interpret">
          <li v-for="line in docSpec.interpretation" :key="line">{{ line }}</li>
        </ul>
        <p v-else class="hint">这一版没有留下解读说明；可直接改体裁、篇幅和每一节的形态。</p>
        <div class="map-controls">
          <label>体裁
            <select :value="docGenre" @change="setGenre(($event.target as HTMLSelectElement).value)">
              <option v-for="option in genreOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </label>
          <label>篇幅（字）
            <input
              type="number"
              min="400"
              max="12000"
              step="200"
              :value="docWords"
              @change="setWords(Number(($event.target as HTMLInputElement).value))"
            />
          </label>
          <label>排版要素
            <span class="device-text">{{ docDeviceText || '按各节形态自动决定' }}</span>
          </label>
        </div>
        <p class="hint">
          每一节都能单独定形态：有可对照的给对照表，有先后顺序给编号步骤，要读者执行给待办清单。
          <template v-if="denseFormCount">当前 {{ denseFormCount }} 节是纯文字。</template>
        </p>
      </section>

      <!--
        讲稿这一块回答的是「这次到底给谁讲、讲多久、每页用什么版式」。
        以前卡片上只有「目标 / 受众 / 组织方式」三行字：目标是
        「围绕「{原句}」做一次{受众}演示」，版式全靠关键词猜，猜不中一律要点页，
        一份九页的稿子七页长得一样。现在场合、页数、时长、每页版式与素材都能改，
        而且改的是生成真的会读的那份施工图。
      -->
      <section v-if="deckSpec" class="map-spec">
        <header class="map-spec-head">
          <h3>这次这样讲</h3>
          <span class="basis" :class="deckSpec.basis === 'model' ? 'is-model' : 'is-rules'">
            {{ deckSpec.basis === 'model' ? '按你的原话解读' : '按规则拟的初稿' }}
          </span>
        </header>
        <ul v-if="deckSpec.interpretation?.length" class="interpret">
          <li v-for="line in deckSpec.interpretation" :key="line">{{ line }}</li>
        </ul>
        <p v-else class="hint">这一版没有留下解读说明；可直接改场合、页数、时长和每一页的版式。</p>
        <div class="map-controls">
          <label>场合
            <select :value="deckGenre" @change="setDeckGenre(($event.target as HTMLSelectElement).value)">
              <option v-for="option in deckGenreOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </label>
          <label>页数
            <input
              type="number"
              min="4"
              max="20"
              step="1"
              :value="deckPages"
              @change="setDeckPages(Number(($event.target as HTMLInputElement).value))"
            />
          </label>
          <label>时长（分钟）
            <input
              type="number"
              min="3"
              max="180"
              step="5"
              :value="deckMinutes"
              @change="setDeckMinutes(Number(($event.target as HTMLInputElement).value))"
            />
          </label>
        </div>
        <p class="hint">
          叙事弧：{{ deckArcText || '按场合的常规起承转合' }}。
          <template v-if="deckVariety">当前 {{ deckVariety }} 种版式<template v-if="deckLongestRun > 2">，但连续 {{ deckLongestRun }} 页同一版式，建议换一页</template>。</template>
        </p>
        <p v-if="deckThesis" class="hint">讲完要记住这一句：{{ deckThesis }}</p>

        <!--
          视觉方案：**模型定稿、用户不喜欢才改**。母版名、tagline、mood、色板、
          背景、逐页页型全部只读摆出来——它们是模型读你的话定下的，不是空表单。
          三个一键动作（换配色 / 换装饰 / 整体换一版）都是前端确定性轮换，
          没有任何必填输入；细控收进「高级调整」折叠，不再逼用户当排版师。
        -->
        <div class="look-block">
          <header class="look-head">
            <h4>长什么样</h4>
            <span class="hint">{{ deckMood || lookMaster?.tagline || '按场合定气质' }}</span>
          </header>

          <div v-if="lookMaster" class="look-final">
            <div class="look-master">
              <strong>{{ lookMaster.label }}</strong>
              <span class="look-tagline">{{ lookMaster.tagline }}</span>
            </div>
            <div class="look-palette" :title="lookVariantLabel">
              <span
                v-for="swatch in lookSwatches"
                :key="swatch.key"
                class="swatch"
                :style="{ background: swatch.color }"
                :title="swatch.label"
              />
            </div>
            <p class="hint look-facts">
              背景：{{ lookBackgroundText }}<template v-if="lookDecoLabel">；装饰：{{ lookDecoLabel }}</template>；{{ deckRoleSummary }}
            </p>
            <div class="look-actions">
              <button class="ghost small" type="button" data-look-action="accent" @click="cycleAccent">换配色</button>
              <button class="ghost small" type="button" data-look-action="decoration" @click="cycleDecoration">换装饰</button>
              <button class="ghost small" type="button" data-look-action="master" @click="cycleMaster">整体换一版</button>
            </div>
          </div>
          <p v-else class="hint">
            这一版没有选母版，按自由排版出稿；点「整体换一版」可套用母版。
          </p>

          <details class="look-advanced">
            <summary>高级调整</summary>
            <div class="map-controls">
              <label>主题色
                <select :value="deckTheme" @change="setDeckTheme(($event.target as HTMLSelectElement).value)">
                  <option v-for="theme in deckThemeOptions" :key="theme.value" :value="theme.value">{{ theme.label }}</option>
                </select>
              </label>
              <label>角花
                <select :value="deckOrnament" @change="setDeckOrnament(($event.target as HTMLSelectElement).value)">
                  <option value="none">不挂角花</option>
                  <option v-for="motif in ornamentMotifs" :key="motif.id" :value="motif.id">{{ motif.label }}</option>
                </select>
              </label>
              <label>配图
                <select :value="deckTreatment" @change="setDeckTreatment(($event.target as HTMLSelectElement).value)">
                  <option v-for="option in treatmentOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </label>
            </div>
            <div class="map-controls">
              <label class="wide">背景
                <select :value="deckBackgroundKind" @change="setDeckBackgroundKind(($event.target as HTMLSelectElement).value)">
                  <option value="motif">纸纹 / 纹样</option>
                  <option value="asset">素材库实拍</option>
                  <option value="solid">纯色</option>
                </select>
              </label>
              <label v-if="deckBackgroundKind === 'motif'" class="wide">纹样
                <select :value="deckBackgroundMotif" @change="setDeckBackgroundMotif(($event.target as HTMLSelectElement).value)">
                  <option v-for="motif in groundMotifs" :key="motif.id" :value="motif.id">{{ motif.label }}</option>
                </select>
              </label>
            </div>
          </details>
          <p v-if="deckRhythm" class="hint">版式节奏：{{ deckRhythm }}</p>
        </div>
      </section>

      <div class="units">
        <div class="units-head">
          <h3>{{ unitLabel }}（{{ draft.units.length }}）</h3>
          <button class="ghost small" type="button" :disabled="busy" @click="addUnit">加一{{ unitWord }}</button>
        </div>
        <ol>
          <li v-for="(unit, index) in draft.units" :key="index" class="unit">
            <div class="unit-order">
              <button type="button" :disabled="busy || index === 0" aria-label="上移" @click="move(index, -1)">↑</button>
              <button type="button" :disabled="busy || index === draft.units.length - 1" aria-label="下移" @click="move(index, 1)">↓</button>
              <button type="button" :disabled="busy" aria-label="删除" @click="remove(index)">✕</button>
            </div>
            <div class="unit-body">
              <input v-model="unit.label" class="unit-label" :placeholder="`第 ${index + 1} ${unitWord}标题`" />
              <input v-model="unit.intent" class="unit-intent" placeholder="这一项要达成什么" />
              <!-- 导图的 detail 是「这一支必须覆盖什么」，文稿的是「这一节必须写到什么」，
                   幻灯的是「这一页必须讲到什么」——三者都得能看见、能改。 -->
              <input
                v-if="draft.kind === 'mindmap' || draft.kind === 'doc' || draft.kind === 'ppt'"
                v-model="unit.detail"
                class="unit-detail"
                :placeholder="draft.kind === 'doc' ? '这一节必须覆盖什么（用；分隔）' : draft.kind === 'ppt' ? '这一页必须讲到什么（用；分隔）' : '必须覆盖什么（用；分隔）'"
              />
              <div class="unit-meta">
                <select v-model="unit.source">
                  <option v-for="option in sourceOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
                <!-- 这一节写成一屏大白话，还是给一张表？决定权在用户手上。 -->
                <select
                  v-if="draft.kind === 'doc'"
                  class="unit-form"
                  :value="sectionForm(index)"
                  @change="setSectionForm(index, ($event.target as HTMLSelectElement).value)"
                >
                  <option v-for="option in formOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
                <!-- 这一页是要点、对照还是流程？以前由关键词猜，现在用户说了算。 -->
                <select
                  v-if="draft.kind === 'ppt'"
                  class="unit-form"
                  :value="deckRole(index)"
                  @change="setDeckRole(index, ($event.target as HTMLSelectElement).value)"
                >
                  <option v-for="option in roleOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
                <!-- 这一页怎么摆：图在左还是在右、要不要图。排版层照这个执行。 -->
                <select
                  v-if="draft.kind === 'ppt'"
                  class="unit-form unit-composition"
                  :value="deckComposition(index)"
                  @change="setDeckComposition(index, ($event.target as HTMLSelectElement).value)"
                >
                  <option v-for="option in compositionOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
                <span v-if="unit.evidence?.length" class="evidence" :title="unit.evidence.join('\n')">依据：{{ unit.evidence[0] }}<template v-if="unit.evidence.length > 1"> 等 {{ unit.evidence.length }} 条</template></span>
                <span v-else-if="unit.gap" class="gap-inline">{{ unit.gap }}</span>
              </div>
              <!-- 素材：这一页要什么画面。留空就是不要图——空白比错图好。 -->
              <input
                v-if="draft.kind === 'ppt'"
                class="unit-detail unit-material"
                :value="deckMaterial(index)"
                placeholder="这一页要什么画面（如「人参饮片实拍」，留空则不配图）"
                @input="setDeckMaterial(index, ($event.target as HTMLInputElement).value)"
              />
            </div>
          </li>
        </ol>
      </div>

      <p v-if="draft.gaps.length" class="gap">资料缺口：{{ draft.gaps.join('；') }}</p>
      <p v-if="draft.clarification" class="clarify">需要你确认：{{ draft.clarification }}</p>
      <p v-if="draft.notice" class="notice">{{ draft.notice }}</p>

      <!-- 设计单：与方案同批确认。文字描述判断不了观感，所以这里给的是线框 -->
      <section v-if="deckDesign" class="design">
        <header class="design-head">
          <h3>版式与配色</h3>
          <p class="hint">十二页按同一套几何渲成线框，图位是灰块、正文是占位线。<strong>确认后成品按这份设计排版</strong>，不会回到固定模板。</p>
        </header>

        <div class="design-facts">
          <label>纸面
            <select v-model="deckDesign.theme_id">
              <option v-for="theme in themeOptions" :key="theme.id" :value="theme.id">{{ theme.label }}</option>
            </select>
          </label>
          <label>角饰
            <select v-model="deckDesign.ornament_set">
              <option value="vine-a">卷草（有花有须）</option>
              <option value="branch-b">折枝（疏）</option>
              <option value="bamboo-d">竹叶（最轻）</option>
              <option value="fret-e">回纹（纯几何）</option>
              <option value="scroll-c">缠枝（压深底）</option>
              <option value="none">不用角饰</option>
            </select>
          </label>
          <label>字体对
            <select v-model="deckDesign.type_pair">
              <option value="song-kai">宋楷 · 正文宋体</option>
              <option value="kai-song">楷宋 · 通篇文楷</option>
              <option value="hei-song">黑宋 · 标题黑体</option>
              <option value="serif-full">全宋 · 思源宋</option>
              <option value="sans-full">全黑 · 思源黑</option>
            </select>
          </label>
        </div>

        <WireframePreview
          :design="deckDesign"
          :titles="draft.units.map((unit) => unit.label)"
          :active="activePage"
          @select="activePage = $event"
        />

        <div v-if="activeDesignPage" class="page-design">
          <h4>第 {{ activePage + 1 }} 页 · {{ activePageTitle }}</h4>
          <label>版式
            <select v-model="activeDesignPage.archetype">
              <option v-for="archetype in ARCHETYPE_ORDER" :key="archetype" :value="archetype">
                {{ ARCHETYPE_META[archetype].label }} — {{ ARCHETYPE_META[archetype].hint }}
              </option>
            </select>
          </label>
          <label>密度
            <select v-model="activeDesignPage.density">
              <option v-for="(meta, key) in DENSITY_META" :key="key" :value="key">{{ meta.label }}（≤{{ meta.maxBlocks }} 块）</option>
            </select>
          </label>
          <div class="slot-actions">
            <span>图位：</span>
            <button class="ghost small" type="button" @click="setFigure('keep')">保留图位</button>
            <button class="ghost small" type="button" :disabled="!hasFigure" @click="setFigure('motif')">改用母题</button>
            <button class="ghost small" type="button" :disabled="!hasFigure" @click="setFigure('none')">不要图</button>
          </div>
        </div>

        <details class="design-why" open>
          <summary>为什么这样排（{{ deckDesign.rationale.length }} 条）</summary>
          <ul>
            <li v-for="line in deckDesign.rationale" :key="line">{{ line }}</li>
          </ul>
          <p v-if="deckDesign.assumptions.length" class="assume">假设：{{ deckDesign.assumptions.join('；') }}</p>
        </details>
      </section>

      <p v-else-if="docDesign" class="design-brief">
        <strong>体例：</strong>{{ GENRE_LABELS[docDesign.genre] }} · 约 {{ docDesign.target_words }} 字 ·
        引用{{ docDesign.citation === 'footnote' ? '脚注式' : docDesign.citation === 'endnote' ? '尾注式' : '不编号' }} ·
        图表{{ docDesign.charts === 'table' ? '允许表格' : docDesign.charts === 'flow' ? '允许流程图' : '仅文字' }}
      </p>

      <details class="sources">
        <summary>这次用到的依据（{{ evidenceTotal }} 条）</summary>
        <div v-for="tier in tierList" :key="tier.value" class="tier">
          <h4>{{ tier.label }}<span v-if="!tier.items.length">（无）</span></h4>
          <ul v-if="tier.items.length">
            <li v-for="item in tier.items" :key="item">{{ item }}</li>
          </ul>
        </div>
      </details>

      <div class="negotiate">
        <label>反馈（一次写清即可，我们会合并处理）<textarea v-model="feedback" rows="2" placeholder="例如：第二节拆成两节；少讲配伍，多讲临床应用" /></label>
        <button class="ghost" type="button" :disabled="busy || !feedback.trim()" @click="submitFeedback">按反馈修订</button>
        <label>推翻重来：重新描述你要的目标<textarea v-model="goalDraft" rows="2" placeholder="例如：其实我要的是一份给临床医师看的用药对比" /></label>
        <button class="ghost" type="button" :disabled="busy || !goalDraft.trim()" @click="submitGoal">按新目标重拟</button>
      </div>

      <p v-if="error" class="error">{{ error }}</p>

      <footer class="plan-actions">
        <span class="revision" v-if="draft.revision">第 {{ draft.revision }} 次修订</span>
        <button class="primary" type="button" :disabled="busy || !draft.goal.trim() || !draft.units.length" @click="confirm">
          {{ busy ? '处理中…' : '确认方案并生成' }}
        </button>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { pptThemes } from '../../config/pptThemes'
import { GENRE_DEFAULT_MASTER, PPT_MASTERS, masterById, type MasterDecorationSet, type MasterSpec } from '../../config/pptMasters'
import { MOTIFS } from '../../render/motifs.catalog'
import WireframePreview from '../ppt/WireframePreview.vue'
import {
  ARCHETYPE_META,
  ARCHETYPE_ORDER,
  DENSITY_META,
  GENRE_LABELS,
  isDeckDesign,
  type AnyDesign,
  type DeckDesign,
  type DocDesign,
  type DesignSlot,
} from '../../types/design'
import type {
  DeckLook,
  PlanSource,
  ResourcePlan,
  ResourcePlanSpec,
  ResourcePlanUnit,
} from '../../types/resource'

const props = defineProps<{
  plan: ResourcePlan
  /** 设计单与方案同批确认。没有它时面板只改方案（老任务/无设计的能力）。 */
  design?: AnyDesign | null
  busy?: boolean
  error?: string
}>()
const emit = defineEmits<{
  revise: [feedback: string, goal: string, plan: ResourcePlan]
  confirm: [plan: ResourcePlan, design: AnyDesign | null]
  cancel: []
}>()

const themeOptions = pptThemes

const KIND_LABELS: Record<string, string> = { ppt: '幻灯片', doc: '文稿', mindmap: '思维导图' }
const UNIT_LABELS: Record<string, string> = { ppt: '页面', doc: '章节', mindmap: '分支' }
const UNIT_WORDS: Record<string, string> = { ppt: '页', doc: '节', mindmap: '支' }

const sourceOptions: Array<{ value: PlanSource; label: string }> = [
  { value: 'knowledge_base', label: '知识库 / 图谱依据' },
  { value: 'user_material', label: '用户材料' },
  { value: 'model', label: '模型补充' },
]
const tierList = computed(() => sourceOptions.map(option => ({
  ...option,
  items: draft.evidence?.[option.value] ?? [],
})))

const clone = (plan: ResourcePlan): ResourcePlan => ({
  ...plan,
  units: (plan.units ?? []).map(unit => ({ ...unit, evidence: [...(unit.evidence ?? [])] })),
  gaps: [...(plan.gaps ?? [])],
  evidence: {
    knowledge_base: [...(plan.evidence?.knowledge_base ?? [])],
    user_material: [...(plan.evidence?.user_material ?? [])],
    model: [...(plan.evidence?.model ?? [])],
  },
  // spec 必须深拷贝：浅拷贝会让面板上的改动直接改到 props 里的方案对象，
  // 于是「暂不生成」也退不回去，外层 watch 还会跟着来回触发。
  spec: plan.spec ? JSON.parse(JSON.stringify(plan.spec)) as ResourcePlanSpec : undefined,
})

const draft = reactive<ResourcePlan>(clone(props.plan))
const feedback = ref('')
const goalDraft = ref('')

// —— 导图施工图：面板上能改的都要真的进生成 ——

/** 画法与其可渲染层数。上限与后端 STRUCTURE_BUDGET 一一对应。 */
const mindStructureOptions = [
  { id: 'logic-right', label: '向右分支', maxDepth: 5 },
  { id: 'mind-both', label: '四面发散', maxDepth: 5 },
  { id: 'org-down', label: '向下分支', maxDepth: 4 },
  { id: 'fishbone', label: '鱼骨图', maxDepth: 3 },
]
const depthOptions = [2, 3, 4, 5]

const mindSpec = computed<ResourcePlanSpec | null>(() => (draft.kind === 'mindmap' ? (draft.spec ?? null) : null))
const mindDepth = computed(() => clampDepth(Number(draft.spec?.depth ?? depthFromText(draft.presentation) ?? 3)))
const mindStructure = computed(() => String(draft.spec?.structure ?? 'logic-right'))
const mindStructureMax = computed(() => mindStructureOptions.find(item => item.id === mindStructure.value)?.maxDepth ?? 5)
const depthCapped = computed(() => mindDepth.value > mindStructureMax.value)
const mindLeaves = computed(() => Number(draft.spec?.leaves_min ?? 3))

const clampDepth = (value: number) => (Number.isFinite(value) ? Math.min(Math.max(Math.round(value), 2), 5) : 3)

/** 从「呈现方式」那行字里读层数。用户手打「5 层」也算数。 */
const depthFromText = (text: string): number | null => {
  const matched = /([一二两三四五六七八九1-9])\s*层/.exec(String(text ?? ''))
  if (!matched) return null
  const token = matched[1]
  const table: Record<string, number> = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 }
  return token in table ? table[token] : Number(token)
}

/** 把「N 层」写回呈现方式。后端以这行字里的数为准，所以两者必须一起变。 */
const writeDepth = (text: string, depth: number): string => {
  const current = String(text ?? '')
  if (/([一二两三四五六七八九1-9])\s*层/.test(current)) return current.replace(/([一二两三四五六七八九1-9])\s*层/, `${depth} 层`)
  return `${depth} 层 · ${current}`.trim()
}
/** 同上，「N 支」。支数以单元个数为准，这行字跟着它走。 */
const writeBranches = (text: string, count: number): string => {
  const current = String(text ?? '')
  if (/([二三四五六七八2-8])\s*支/.test(current)) return current.replace(/([二三四五六七八2-8])\s*支/, `${count} 支`)
  return current
}

const ensureSpec = (): ResourcePlanSpec => {
  if (!draft.spec) {
    draft.spec = {
      depth: clampDepth(depthFromText(draft.presentation) ?? 3),
      structure: 'logic-right',
      leaves_min: 3,
      interpretation: [],
      basis: 'rules',
    }
  }
  return draft.spec
}
const setDepth = (value: number) => {
  const spec = ensureSpec()
  spec.depth = clampDepth(value)
  // 画法装不下就往前推一档，别让用户以为选了 5 层却是 3 层。
  if ((mindStructureMax.value ?? 5) < spec.depth) spec.structure = 'logic-right'
  spec.depth_requested = Math.max(Number(spec.depth_requested ?? 0), spec.depth)
  draft.presentation = writeDepth(draft.presentation, spec.depth)
  // 解读里写着「按 4 层展开」时，改了层数就得跟着改，否则面板自相矛盾。
  spec.interpretation = (spec.interpretation ?? []).map(line => line.replace(/(\d+)\s*层/g, `${spec.depth} 层`))
}
const setStructure = (value: string) => { ensureSpec().structure = value }
const setLeaves = (value: number) => { ensureSpec().leaves_min = value }
/** 支数变了，呈现方式与解读里的「N 支」一起改。 */
const syncBranches = () => {
  const count = draft.units.length
  draft.presentation = writeBranches(draft.presentation, count)
  const spec = draft.spec
  if (spec?.interpretation) spec.interpretation = spec.interpretation.map(line => line.replace(/(\d+)\s*支/g, `${count} 支`))
  if (draft.kind === 'doc') draft.presentation = writeChapters(draft.presentation, count)
  // 讲稿加/删一页，施工图与「共 N 页」都得跟着走，否则面板自相矛盾。
  if (draft.kind === 'ppt') syncDeckPages()
}

// —— 文稿施工图：体裁、篇幅、每节形态 ——

const genreOptions = (Object.keys(GENRE_LABELS) as Array<keyof typeof GENRE_LABELS>)
  .map(value => ({ value, label: GENRE_LABELS[value] }))

const formOptions = [
  { value: 'prose', label: '自然段' },
  { value: 'table', label: '对照表' },
  { value: 'steps', label: '编号步骤' },
  { value: 'checklist', label: '待办清单' },
  { value: 'qa', label: '一问一答' },
  { value: 'mixed', label: '段落+结构' },
]

const DEVICE_LABELS: Record<string, string> = {
  table: '对照表',
  checklist: '行动清单',
  steps: '分步说明',
  timeline: '时间线',
  callout: '重点提示',
  summary: '速记小结',
  glossary: '术语表',
}

const docSpec = computed<ResourcePlanSpec | null>(() => (draft.kind === 'doc' ? (draft.spec ?? null) : null))
const docGenre = computed(() => String(draft.spec?.genre ?? 'explain'))
const docWords = computed(() => Number(draft.spec?.target_words ?? 2200))
const docDeviceText = computed(() => (draft.spec?.devices ?? []).map(device => DEVICE_LABELS[device] ?? device).join('、'))
const denseFormCount = computed(() => (draft.spec?.sections ?? []).filter(section => (section.form ?? 'prose') === 'prose').length)

const ensureDocSpec = (): ResourcePlanSpec => {
  if (!draft.spec) {
    draft.spec = { genre: 'explain', target_words: 2200, sections: [], devices: [], interpretation: [], basis: 'rules' }
  }
  if (!draft.spec.sections) draft.spec.sections = []
  while (draft.spec.sections.length < draft.units.length) {
    draft.spec.sections.push({ heading: draft.units[draft.spec.sections.length]?.label ?? '', form: 'prose' })
  }
  return draft.spec
}

/** 呈现方式那行字里的章节数跟着单元个数走，卡片别自相矛盾。 */
const writeChapters = (text: string, count: number): string => {
  const current = String(text ?? '')
  if (/\d+\s*章/.test(current)) return current.replace(/\d+\s*章/, `${count} 章`)
  return `${current} · ${count} 章`.trim()
}
/** 同上，字数。后端以这行字里的数为准，所以两者必须一起变。 */
const writeWords = (text: string, words: number): string => {
  const current = String(text ?? '')
  if (/\d+\s*字/.test(current)) return current.replace(/\d+\s*字/, `${words} 字`)
  return `${current} · 约 ${words} 字`.trim()
}

const setGenre = (value: string) => {
  const spec = ensureDocSpec()
  spec.genre = value as ResourcePlanSpec['genre']
  const label = GENRE_LABELS[value as keyof typeof GENRE_LABELS] ?? value
  const rest = String(draft.presentation ?? '').replace(/^[^·]+·\s*/, '')
  draft.presentation = rest ? `${label} · ${rest}` : `${label} · 约 ${spec.target_words} 字`
}

const setWords = (value: number) => {
  const words = Number.isFinite(value) ? Math.min(Math.max(Math.round(value), 400), 12000) : 2200
  ensureDocSpec().target_words = words
  draft.presentation = writeWords(draft.presentation, words)
}

const sectionForm = (index: number) => String(draft.spec?.sections?.[index]?.form ?? 'prose')

const setSectionForm = (index: number, value: string) => {
  const spec = ensureDocSpec()
  if (spec.sections?.[index]) spec.sections[index].form = value
}

// —— 讲稿施工图：场合、页数、时长、每页版式与素材 ——

/** 场合。与后端 `ppt.spec.GENRE_LABEL` 同源，改一处要两边一起改。 */
const DECK_GENRE_LABELS: Record<string, string> = {
  lecture: '课堂讲授',
  orientation: '入门宣讲',
  brief: '工作汇报',
  training: '技能培训',
  defense: '答辩',
  pitch: '路演',
}
const deckGenreOptions = Object.entries(DECK_GENRE_LABELS).map(([value, label]) => ({ value, label }))
/** 版式菜单直接取设计单的那十种，避免面板与线框各说一套。 */
const roleOptions = ARCHETYPE_ORDER.map(value => ({ value, label: ARCHETYPE_META[value].label }))

const deckSpec = computed<ResourcePlanSpec | null>(() => (draft.kind === 'ppt' ? (draft.spec ?? null) : null))
const deckGenre = computed(() => String(draft.spec?.genre ?? 'lecture'))
const deckPages = computed(() => Number(draft.spec?.target_pages ?? draft.units.length))
const deckMinutes = computed(() => Number(draft.spec?.duration_min ?? 15))
const deckArcText = computed(() => (draft.spec?.arc ?? []).join(' → '))
const deckVariety = computed(() => new Set((draft.spec?.pages ?? []).map(page => page.role ?? 'points')).size)
/** 视觉方案：模型读出来的「长什么样」。方案卡上每一项都能改，改完生成真的按它走。 */
const deckLook = computed<DeckLook>(() => draft.spec?.look ?? {})
const deckThesis = computed(() => String(draft.spec?.thesis ?? ''))
const deckMood = computed(() => String(deckLook.value.mood ?? ''))
const deckRhythm = computed(() => String(deckLook.value.rhythm ?? ''))
const deckTheme = computed(() => String(deckLook.value.theme ?? 'paper'))
const deckOrnament = computed(() => String(deckLook.value.ornament?.motif ?? 'none'))
const deckTreatment = computed(() => String(deckLook.value.image_treatment ?? 'plate'))
const deckBackgroundKind = computed(() => String(deckLook.value.background?.kind ?? 'motif'))
const deckBackgroundMotif = computed(() => String(deckLook.value.background?.motif ?? 'paper-plain'))
const deckThemeOptions = pptThemes.map(theme => ({ value: theme.id, label: theme.label }))
/** 角花只列母题族里的 ornament；`none` 是「不挂」的显式选项。 */
const ornamentMotifs = MOTIFS.filter(motif => motif.family === 'ornament')
const groundMotifs = MOTIFS.filter(motif => motif.family === 'ground' || motif.family === 'scene')
const treatmentOptions = [
  { value: 'plate', label: '古籍彩绘 / 版画' },
  { value: 'photo', label: '实拍' },
  { value: 'line', label: '线描' },
  { value: 'collage', label: '拼贴' },
  { value: 'none', label: '不用图' },
]
const COMPOSITION_LABELS: Record<string, string> = {
  auto: '自动',
  'text-only': '纯文字',
  'text-image-right': '左文右图',
  'text-image-left': '左图右文',
  'full-bleed': '满版图压字',
  centered: '居中极简',
  split: '上下分栏',
}
const compositionOptions = Object.entries(COMPOSITION_LABELS).map(([value, label]) => ({ value, label }))

/** 改视觉方案 = 同时改施工图（生成读它）与设计单（线框读它）。 */
const touchLook = () => {
  const spec = ensureDeckSpec()
  if (!spec.look) spec.look = { mood: '', theme: 'paper', background: { kind: 'motif', motif: 'paper-plain' }, ornament: { motif: 'none' } }
  if (!spec.look.background) spec.look.background = { kind: 'motif', motif: 'paper-plain' }
  if (!spec.look.ornament) spec.look.ornament = { motif: 'none' }
  return spec.look
}

// —— 模型定稿展示：母版、色板变体、装饰组、页型摘要，全部只读 ——

const lookMaster = computed<MasterSpec | null>(() => masterById(deckLook.value.master_id) ?? null)
const lookVariantIndex = computed(() => {
  const raw = Number(deckLook.value.accent_variant ?? 0)
  const count = lookMaster.value?.palettes.length ?? 1
  return Number.isFinite(raw) ? Math.min(Math.max(Math.trunc(raw), 0), Math.max(0, count - 1)) : 0
})
const lookVariant = computed(() => lookMaster.value?.palettes[lookVariantIndex.value] ?? null)
const lookVariantLabel = computed(() => {
  const variant = lookVariant.value
  return variant ? `配色：${variant.label}` : ''
})
const lookSwatches = computed(() => {
  const variant = lookVariant.value
  if (!variant) return []
  const entries: Array<{ key: string; color: string; label: string }> = [
    { key: 'bg', color: variant.bg, label: `底色 ${variant.bg}` },
    { key: 'fg', color: variant.fg, label: `正文 ${variant.fg}` },
    { key: 'muted', color: variant.muted, label: `次要 ${variant.muted}` },
    { key: 'accent', color: variant.accent, label: `强调 ${variant.accent}` },
    { key: 'line', color: variant.line, label: `线条 ${variant.line}` },
  ]
  return entries
})
const motifLabel = (id: string | undefined | null) =>
  MOTIFS.find(motif => motif.id === id)?.label ?? String(id ?? '')
const lookBackgroundText = computed(() => {
  const background = deckLook.value.background
  if (background?.kind === 'asset') return background.subject ? `${background.subject}（素材库实拍）` : '素材库实拍'
  if (background?.kind === 'solid') return '纯色'
  if (background?.motif) return `纸纹 · ${motifLabel(background.motif)}`
  const master = lookMaster.value
  if (master?.background?.kind === 'solid') return '纯色'
  if (master?.background?.motif) return `纸纹 · ${motifLabel(master.background.motif)}`
  return '按母版默认'
})
/** 当前装饰组在母版 decorations 里的序号；模型显式改过、对不上任何一组时为 -1。 */
const lookDecorationIndex = computed(() => {
  const master = lookMaster.value
  if (!master) return -1
  const motif = String(deckLook.value.ornament?.motif ?? '')
  const subject = String(deckLook.value.ornament?.subject ?? '')
  const index = master.decorations.findIndex(set => {
    const ornament = set.ornament
    if (ornament.motif && ornament.motif !== 'none') return motif === ornament.motif
    if (ornament.subject || ornament.keywords?.length) return Boolean(subject) && subject === ornament.subject
    return !motif || motif === 'none'
  })
  return index
})
const lookDecoLabel = computed(() => {
  const index = lookDecorationIndex.value
  const set = index >= 0 ? lookMaster.value?.decorations[index] : undefined
  return set?.label ?? ''
})
/** 逐页页型摘要：封面 · 要点×3 · 对照 · 收束。连续同版式合并计数，一屏读完。 */
const deckRoleSummary = computed(() => {
  const roles = (draft.spec?.pages ?? []).map(page => page.role ?? 'points')
  if (!roles.length) return '页型按内容自动定'
  const parts: string[] = []
  let index = 0
  while (index < roles.length) {
    let end = index
    while (end + 1 < roles.length && roles[end + 1] === roles[index]) end += 1
    const label = roleOptions.find(option => option.value === roles[index])?.label ?? roles[index]
    const count = end - index + 1
    parts.push(count > 1 ? `${label}×${count}` : label)
    index = end + 1
  }
  return `页型：${parts.join(' · ')}`
})

/** 把一套装饰组写进 look（角饰 + 底纹一起换，decorations 本来就是成对的）。 */
const applyDecorationSet = (look: DeckLook, set: MasterDecorationSet) => {
  const ornament = set.ornament
  if (ornament.motif && ornament.motif !== 'none') {
    look.ornament = { motif: ornament.motif }
  } else if (ornament.subject || ornament.keywords?.length) {
    look.ornament = { motif: 'none', subject: ornament.subject ?? '', keywords: [...(ornament.keywords ?? [])] }
  } else {
    look.ornament = { motif: 'none' }
  }
  const ground = set.ground
  if (ground.motif) {
    look.background = { kind: 'motif', motif: ground.motif }
  } else if (ground.subject || ground.keywords?.length) {
    look.background = { kind: 'asset', subject: ground.subject ?? '', keywords: [...(ground.keywords ?? [])], veil: 0.42 }
  } else {
    look.background = { kind: 'solid' }
  }
}

const cycleAccent = () => {
  const master = lookMaster.value
  if (!master || master.palettes.length < 2) return
  const look = touchLook()
  look.master_id = master.id
  look.accent_variant = (lookVariantIndex.value + 1) % master.palettes.length
  syncDesignLook()
}

const cycleDecoration = () => {
  const master = lookMaster.value
  if (!master?.decorations.length) return
  const look = touchLook()
  look.master_id = master.id
  const next = master.decorations[(lookDecorationIndex.value + 1 + master.decorations.length) % master.decorations.length]
  applyDecorationSet(look, next)
  syncDesignLook()
}

const cycleMaster = () => {
  const genre = deckGenre.value
  const matched = PPT_MASTERS.filter(master => master.genres.includes(genre))
  const pool = matched.length ? matched : PPT_MASTERS
  const currentId = String(deckLook.value.master_id ?? '')
  const current = pool.findIndex(master => master.id === currentId)
  // 当前不在候选里（旧方案没选母版 / 手改过 genre）：从该场合的默认母版起跳。
  const next = current >= 0
    ? pool[(current + 1) % pool.length]
    : pool.find(master => master.id === (GENRE_DEFAULT_MASTER[genre] ?? '')) ?? pool[0]
  const look = touchLook()
  look.master_id = next.id
  look.accent_variant = 0
  look.theme = next.theme as DeckLook['theme']
  look.type_pair = next.type_pair
  // 换母版 = 换整套默认。旧 look 里的 theme/背景/角饰是从上一个母版回填的
  // 显式值，原样留着会盖掉新母版自己的样子，所以按新母版重置。
  const defaultSet = next.decorations[0]
  if (defaultSet) {
    applyDecorationSet(look, defaultSet)
  } else if (next.background?.kind === 'solid') {
    look.background = { kind: 'solid' }
  } else {
    look.background = { kind: 'motif', motif: next.background?.motif || 'paper-plain' }
  }
  syncDesignLook()
}

const syncDesignLook = () => {
  const look = touchLook()
  const design = deckDesign.value
  if (!design) return
  const master = lookMaster.value
  const variant = lookVariant.value
  design.master_id = String(look.master_id ?? '')
  design.accent_variant = lookVariantIndex.value
  if (master) {
    // 母版命中：主题/字体对由母版定；色板 = 变体（look.palette 只作覆盖）。
    design.theme_id = master.theme
    design.type_pair = master.type_pair
    design.paper_motif = String(look.background?.motif ?? master.background?.motif ?? '')
    design.palette = look.palette && Object.keys(look.palette).length
      ? { ...look.palette } as Record<string, string>
      : variant ? { bg: variant.bg, fg: variant.fg, muted: variant.muted, accent: variant.accent, line: variant.line } : { ...design.palette }
  } else {
    design.theme_id = String(look.theme ?? 'paper')
    design.paper_motif = String(look.background?.motif ?? 'paper-plain')
    design.palette = (look.palette ?? {}) as Record<string, string>
  }
  design.ornament_motif = String(look.ornament?.motif ?? 'none')
  design.background = { ...(look.background ?? {}) }
  design.ornament = { ...(look.ornament ?? {}) }
  design.image_treatment = String(look.image_treatment ?? 'plate')
  design.mood = String(look.mood ?? '')
  design.rhythm = String(look.rhythm ?? '')
}
const setDeckTheme = (value: string) => {
  touchLook().theme = value as DeckLook['theme']
  syncDesignLook()
}
const setDeckOrnament = (value: string) => {
  const look = touchLook()
  look.ornament = { ...(look.ornament ?? {}), motif: value }
  syncDesignLook()
}
const setDeckTreatment = (value: string) => {
  touchLook().image_treatment = value as DeckLook['image_treatment']
  syncDesignLook()
}
const setDeckBackgroundKind = (value: string) => {
  const look = touchLook()
  look.background = {
    ...(look.background ?? {}),
    kind: value as 'motif' | 'asset' | 'solid',
    ...(value === 'asset' ? { veil: look.background?.veil ?? 0.42 } : {}),
  }
  syncDesignLook()
}
const setDeckBackgroundMotif = (value: string) => {
  const look = touchLook()
  look.background = { ...(look.background ?? {}), kind: 'motif', motif: value }
  syncDesignLook()
}
const deckComposition = (index: number) => String(draft.spec?.pages?.[index]?.composition ?? 'auto')
const setDeckComposition = (index: number, value: string) => {
  const spec = ensureDeckSpec()
  if (spec.pages?.[index]) spec.pages[index].composition = value
}

const deckLongestRun = computed(() => {
  const roles = (draft.spec?.pages ?? []).map(page => page.role ?? 'points')
  let longest = 1
  let run = 1
  for (let index = 1; index < roles.length; index += 1) {
    run = roles[index] === roles[index - 1] ? run + 1 : 1
    longest = Math.max(longest, run)
  }
  return roles.length ? longest : 0
})

const ensureDeckSpec = (): ResourcePlanSpec => {
  if (!draft.spec) {
    draft.spec = { genre: 'lecture', target_pages: draft.units.length, duration_min: 15, pages: [], interpretation: [], basis: 'rules' }
  }
  if (!draft.spec.pages) draft.spec.pages = []
  while (draft.spec.pages.length < draft.units.length) {
    const index = draft.spec.pages.length
    draft.spec.pages.push({
      heading: draft.units[index]?.label ?? '',
      role: index === 0 ? 'cover' : 'points',
      goal: draft.units[index]?.intent ?? '',
      must_cover: [],
    })
  }
  return draft.spec
}

/** 「呈现方式」那行字里的页数跟着实际页数走，卡片别自相矛盾。 */
const writeDeckPageCount = (text: string, count: number): string => {
  const current = String(text ?? '')
  if (/共\s*\d+\s*页/.test(current)) return current.replace(/共\s*\d+\s*页/, `共 ${count} 页`)
  return `共 ${count} 页（含封面），${current}`.trim()
}

/** 页数与单元同步：用户删一页，卡片上的「共 N 页」不能还写着旧数。 */
const syncDeckPages = () => {
  if (draft.kind !== 'ppt' || !draft.spec) return
  const spec = ensureDeckSpec()
  spec.pages = draft.units.map((unit, index) => {
    const old = spec.pages?.[index]
    return {
      ...(old ?? { heading: unit.label, role: index === 0 ? 'cover' : 'points' }),
      heading: unit.label,
      goal: unit.intent || old?.goal || '',
      // 占位文案说的是「用；分隔」，就真的只按分号拆——按顿号逗号拆会把
      // 用户一条里的并列词也拆开，一页 must_cover 被拆成八条。
      must_cover: unit.detail ? unit.detail.split(/[；;]/).map(item => item.trim()).filter(Boolean) : old?.must_cover ?? [],
    }
  })
  spec.target_pages = draft.units.length
  draft.presentation = writeDeckPageCount(draft.presentation, draft.units.length)
}

const setDeckGenre = (value: string) => {
  // 只改场合，不动页面列表、也不重写「呈现方式」：换场合时把用户排好的页删掉，
  // 比不改更糟；那行字里的页数与版式数由页数/版式控件维护。
  ensureDeckSpec().genre = value as ResourcePlanSpec['genre']
}

const setDeckPages = (value: number) => {
  const count = Number.isFinite(value) ? Math.min(Math.max(Math.round(value), 4), 20) : draft.units.length
  // 页数就是下面那个列表的长度：加页就补空行，减页就从末尾删。
  // 只写数字不动列表，会让「共 12 页」和 9 行页面对不上——用户第一眼就会发现。
  while (draft.units.length > count) draft.units.pop()
  while (draft.units.length < count) {
    const index = draft.units.length + 1
    draft.units.push({ id: `u${index}`, label: '', intent: '', detail: '', source: 'model', evidence: [] })
  }
  ensureDeckSpec().target_pages = count
  syncBranches()
}

const setDeckMinutes = (value: number) => {
  const minutes = Number.isFinite(value) ? Math.min(Math.max(Math.round(value), 3), 180) : 15
  ensureDeckSpec().duration_min = minutes
}

/** 每一页的版式。改了要同时写进设计单，否则线框与成品还是旧版式。 */
const deckRole = (index: number) => String(draft.spec?.pages?.[index]?.role ?? (index === 0 ? 'cover' : 'points'))

const setDeckRole = (index: number, value: string) => {
  const spec = ensureDeckSpec()
  if (spec.pages?.[index]) spec.pages[index].role = value
  const page = deckDesign.value?.pages?.[index]
  if (page) page.archetype = value as typeof page.archetype
}

const deckMaterial = (index: number) => String(draft.spec?.pages?.[index]?.material?.subject ?? '')

const setDeckMaterial = (index: number, value: string) => {
  const spec = ensureDeckSpec()
  const page = spec.pages?.[index]
  if (!page) return
  const subject = value.trim()
  // 留空就是「这页不要图」，不是「用主题当主体」——后者正是搜不到图的原因。
  page.material = subject ? { ...(page.material ?? {}), subject, slot: page.material?.slot ?? 'figure', style: page.material?.style ?? 'plate' } : undefined
  const designPage = deckDesign.value?.pages?.[index]
  if (designPage) {
    const others = (designPage.slots ?? []).filter((slot: DesignSlot) => slot.slot !== 'figure' && slot.slot !== 'evidence')
    designPage.slots = subject
      ? [{ slot: 'figure', role: '配图', subject, style: 'plate', priority: 'optional', aspect: index === 0 ? 'portrait' : 'landscape' }, ...others]
      : others
  }
}

/** 设计单的本地副本。确认时才回传——中途改版式不必每敲一下就发请求。 */
const cloneDesign = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const designDraft = ref<AnyDesign | null>(props.design ? cloneDesign(props.design) : null)
const activePage = ref(0)

watch(
  () => props.design,
  (value) => {
    designDraft.value = value ? cloneDesign(value) : null
    activePage.value = 0
  },
  { deep: true },
)

const deckDesign = computed<DeckDesign | null>(() => (isDeckDesign(designDraft.value) ? designDraft.value : null))
/** 文稿单靠 `genre` 认：导图单没有这个键，但同样不是幻灯单。 */
const docDesign = computed<DocDesign | null>(() =>
  designDraft.value && !isDeckDesign(designDraft.value) && 'genre' in designDraft.value
    ? (designDraft.value as DocDesign)
    : null,
)
const activeDesignPage = computed(() => deckDesign.value?.pages?.[activePage.value] ?? null)
const activePageTitle = computed(() => draft.units[activePage.value]?.label ?? '')
const hasFigure = computed(() =>
  Boolean(activeDesignPage.value?.slots?.some((slot) => slot.slot === 'figure' || slot.slot === 'evidence')),
)

/** 图位三选一：保留 / 改用母题（不占图片预算）/ 不要图（版式自动退成无图范式）。 */
const setFigure = (mode: 'keep' | 'motif' | 'none') => {
  const page = activeDesignPage.value
  if (!page) return
  const others = (page.slots ?? []).filter((slot) => slot.slot !== 'figure' && slot.slot !== 'evidence')
  if (mode === 'none') {
    page.slots = others
    return
  }
  const existing = page.slots?.find((slot) => slot.slot === 'figure' || slot.slot === 'evidence')
  const style = mode === 'motif' ? 'motif' : existing?.style ?? 'plate'
  const figure: DesignSlot = { ...(existing ?? { slot: 'figure', role: '主图' }), slot: 'figure', style }
  page.slots = [figure, ...others]
}

watch(() => props.plan, value => { Object.assign(draft, clone(value)) }, { deep: true })

const kindLabel = computed(() => KIND_LABELS[draft.kind] ?? '资源')
const unitLabel = computed(() => UNIT_LABELS[draft.kind] ?? '内容单元')
const unitWord = computed(() => UNIT_WORDS[draft.kind] ?? '项')
const evidenceTotal = computed(() => Object.values(draft.evidence ?? {}).reduce((sum, items) => sum + (items?.length ?? 0), 0))

const addUnit = () => {
  const index = draft.units.length + 1
  draft.units.push({ id: `u${index}`, label: '', intent: '', detail: '', source: 'model', evidence: [] })
  // 讲稿：加一页就该有一页的版式记录，否则新页会借用上一页的角色。
  if (draft.kind === 'ppt' && draft.spec?.pages) {
    draft.spec.pages.push({ heading: '', role: 'points', goal: '', must_cover: [] })
  }
  syncBranches()
}
const remove = (index: number) => {
  draft.units.splice(index, 1)
  if (draft.kind === 'ppt' && draft.spec?.pages) draft.spec.pages.splice(index, 1)
  syncBranches()
}
const move = (index: number, delta: number) => {
  const target = index + delta
  if (target < 0 || target >= draft.units.length) return
  const [unit] = draft.units.splice(index, 1)
  draft.units.splice(target, 0, unit)
  // 版式跟着这一页走：把第 5 页的对照表挪到第 2 页，它还是对照表。
  if (draft.kind === 'ppt' && draft.spec?.pages) {
    const [page] = draft.spec.pages.splice(index, 1)
    draft.spec.pages.splice(target, 0, page)
  }
  syncBranches()
}
const submitFeedback = () => {
  emit('revise', feedback.value.trim(), '', currentPlan())
  feedback.value = ''
}
const submitGoal = () => {
  emit('revise', feedback.value.trim(), goalDraft.value.trim(), currentPlan())
  goalDraft.value = ''
}
/** 面板里手改过的那一版。修订要基于它，否则「改完标题又说要拆节」手改就白改了。 */
const currentPlan = (): ResourcePlan => {
  const payload = clone(draft)
  payload.units = payload.units.filter((unit: ResourcePlanUnit) => unit.label.trim())
  return payload
}
const confirm = () => {
  // 方案与设计一起交出去：只交方案等于让服务端自己再猜一遍版式。
  emit('confirm', currentPlan(), designDraft.value ? cloneDesign(designDraft.value) : null)
}
</script>

<style scoped lang="scss">
.plan-backdrop{position:fixed;inset:0;z-index:60;display:grid;place-items:center;padding:16px;background:color-mix(in srgb,var(--qh-fg) 28%,transparent);backdrop-filter:blur(2px)}
.plan-review{width:min(760px,100%);max-height:88vh;overflow:auto;padding:22px 24px;border:1px solid var(--qh-line);border-radius:var(--qh-radius-card);background:var(--qh-surface);box-shadow:var(--qh-shadow-3);color:var(--qh-fg);display:grid;gap:14px}
.plan-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
.eyebrow{margin:0;color:var(--qh-accent);font-size:12px;letter-spacing:.04em}
h2{margin:4px 0 6px;font-size:21px}
.hint{margin:0;color:var(--qh-fg-muted);font-size:12px;line-height:1.6;max-width:52ch}
.facts{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.facts .wide{grid-column:1 / -1}
.map-spec{display:grid;gap:8px;padding:10px 12px;border:1px solid var(--qh-line);border-radius:8px;background:var(--qh-bg)}
.map-spec-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.map-spec-head h3{margin:0;font-size:13px}
.look-block{display:grid;gap:8px;padding:10px 12px;border:1px dashed var(--qh-line);border-radius:8px;background:color-mix(in srgb,var(--qh-bg) 60%,var(--qh-bg-soft))}
.look-head{display:flex;align-items:baseline;gap:10px}
.look-head h4{margin:0;font-size:12.5px}
.look-final{display:grid;gap:8px;padding:10px 12px;border:1px solid var(--qh-line);border-radius:8px;background:var(--qh-bg)}
.look-master{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.look-master strong{font-size:14px}
.look-tagline{font-size:12px;color:var(--qh-fg-muted)}
.look-palette{display:flex;gap:6px;align-items:center}
.look-palette .swatch{width:22px;height:22px;border-radius:5px;border:1px solid var(--qh-line);box-shadow:inset 0 0 0 1px color-mix(in srgb,#fff 18%,transparent)}
.look-facts{margin:0}
.look-actions{display:flex;gap:8px;flex-wrap:wrap}
.look-actions button{padding:5px 12px;font-size:12px}
.look-advanced{border-top:1px dashed var(--qh-line);padding-top:6px}
.look-advanced summary{cursor:pointer;color:var(--qh-fg-muted);font-size:12px}
.look-advanced[open] .map-controls{margin-top:8px}
.unit-composition{max-width:132px}
.basis{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--qh-line);color:var(--qh-fg-muted)}
.basis.is-model{border-color:var(--qh-accent);color:var(--qh-accent)}
.interpret{margin:0;padding-left:16px;display:grid;gap:4px;font-size:12px;line-height:1.6;color:var(--qh-fg)}
.map-controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.map-controls select{width:100%}
.device-text{display:flex;align-items:center;min-height:34px;padding:0 10px;border:1px dashed var(--qh-line);border-radius:6px;color:var(--qh-fg-muted);font-size:12px}
.unit-form{width:auto;padding:4px 6px;font-size:11px}
.unit-detail{font-size:12px;color:var(--qh-fg-muted)}
label{display:grid;gap:5px;font-size:12px;color:var(--qh-fg-muted)}
input,textarea,select{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--qh-line);border-radius:6px;background:var(--qh-bg);color:var(--qh-fg);font:inherit;font-size:13px}
textarea{resize:vertical}
.units{border-top:1px solid var(--qh-line);padding-top:12px}
.units-head{display:flex;justify-content:space-between;align-items:center}
h3{margin:0;font-size:14px}
ol{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:8px}
.unit{display:grid;grid-template-columns:34px 1fr;gap:8px;padding:10px;border:1px solid var(--qh-line);border-radius:8px;background:var(--qh-bg)}
.unit-order{display:grid;gap:2px;align-content:start}
.unit-order button{padding:2px 0;font-size:11px;border:1px solid var(--qh-line);background:transparent;color:var(--qh-fg-muted);cursor:pointer}
.unit-body{display:grid;gap:6px}
.unit-label{font-weight:600}
.unit-intent{font-size:12px}
.unit-meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:11px;color:var(--qh-fg-muted)}
.unit-meta select{width:auto;padding:4px 6px;font-size:11px}
.evidence{color:var(--qh-accent);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:46ch}
.gap-inline{color:var(--qh-warning,#9a6424)}
.gap,.clarify,.notice,.error{margin:0;font-size:12px;line-height:1.6}
.gap{color:var(--qh-warning,#9a6424)}
.clarify{padding:8px 10px;border-left:3px solid var(--qh-accent);background:var(--qh-bg)}
.notice{padding:8px 10px;border-left:3px solid var(--qh-warning,#9a6424);background:var(--qh-bg)}
.error{color:var(--qh-danger,#b43e3e)}
.sources{border-top:1px solid var(--qh-line);padding-top:10px;font-size:12px}
.sources summary{cursor:pointer;color:var(--qh-fg-muted)}
.tier{margin-top:8px}
.tier h4{margin:0 0 4px;font-size:12px;color:var(--qh-fg-muted);font-weight:600}
.tier ul{margin:0;padding-left:18px;line-height:1.6}
.negotiate{display:grid;gap:8px;border-top:1px solid var(--qh-line);padding-top:12px}
.design{display:grid;gap:10px;border-top:1px solid var(--qh-line);padding-top:12px}
.design-head .hint{max-width:none}
.design-facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.page-design{display:grid;gap:8px;padding:10px;border:1px solid var(--qh-line);border-radius:8px;background:var(--qh-bg)}
.page-design h4{margin:0;font-size:13px}
.slot-actions{display:flex;gap:6px;align-items:center;font-size:12px;color:var(--qh-fg-muted)}
.design-why{border-top:1px dashed var(--qh-line);padding-top:8px;font-size:12px;color:var(--qh-fg-muted)}
.design-why summary{cursor:pointer}
.design-why ul{margin:6px 0 0;padding-left:18px;line-height:1.6}
.design-why .assume{margin:6px 0 0;color:var(--qh-warning,#9a6424)}
.design-brief{margin:0;padding:8px 10px;border-left:3px solid var(--qh-accent);background:var(--qh-bg);font-size:12px;line-height:1.6}
.negotiate button{justify-self:start}
.plan-actions{display:flex;justify-content:flex-end;align-items:center;gap:12px;position:sticky;bottom:-22px;padding:12px 0;background:var(--qh-surface)}
.revision{font-size:12px;color:var(--qh-fg-muted);margin-right:auto}
button{padding:8px 14px;border:1px solid var(--qh-line);border-radius:6px;background:var(--qh-bg);color:var(--qh-fg);font:inherit;font-size:13px;cursor:pointer}
button.small{padding:4px 10px;font-size:12px}
button.ghost{background:transparent}
button.primary{border-color:var(--qh-accent);background:var(--qh-accent);color:#fff}
button:disabled{opacity:.5;cursor:not-allowed}
@media (max-width:640px){.facts{grid-template-columns:1fr}.plan-review{padding:16px}}
</style>
