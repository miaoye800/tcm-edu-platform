<template>
  <el-dialog
    v-model="visible"
    class="plan-wizard"
    :title="title"
    width="760px"
    :close-on-click-modal="false"
    append-to-body
    @closed="reset"
  >
    <el-steps :active="step" finish-status="success" simple class="wizard-steps">
      <el-step title="说目标" />
      <el-step title="定取舍" />
      <el-step title="确认" />
    </el-steps>

    <!-- ① 说目标：先问缺的，再排 -->
    <section v-if="step === 0" class="wizard-panel">
      <p class="panel-lead">
        先说清楚为什么学、什么时候要。缺的信息我会先问你一轮，再按答案排——
        <strong>不会拿默认值悄悄替你决定。</strong>
      </p>
      <el-input
        v-model="goal"
        type="textarea"
        :rows="3"
        maxlength="600"
        show-word-limit
        placeholder="例如：老师教过我什么药配什么药成什么剂，我想先把中药学完，再学其他内容；两年后考研"
        @blur="runClarify"
      />

      <div class="clarify-actions">
        <el-button text :loading="clarifying" @click="runClarify">
          {{ clarify ? '重新看看还缺什么' : '看看还缺什么' }}
        </el-button>
        <span v-if="clarify?.understood?.length" class="panel-note">
          我理解到：{{ clarify.understood.join('；') }}
        </span>
      </div>

      <!--
        追问一屏就问完：每一项要么给选项、要么允许直接写。答不答都能继续，
        但没答的那几项会以"我会按这样排"的方式明写出来，不让默认值隐形生效。
      -->
      <div v-if="clarify?.needs" class="clarify-block">
        <div v-for="question in clarify.questions" :key="question.id" class="clarify-row">
          <p class="clarify-question">{{ question.question }}</p>
          <p class="panel-note">{{ question.hint }}</p>
          <el-radio-group
            v-if="!question.multi"
            :model-value="answerOf(question.id)"
            size="small"
            @update:model-value="(value: string | number | boolean | undefined) => setAnswer(question.id, String(value ?? ''))"
          >
            <el-radio v-for="option in question.options" :key="option.value" :value="option.value" border>
              {{ option.label }}
            </el-radio>
          </el-radio-group>
          <el-checkbox-group
            v-else
            :model-value="splitAnswer(question.id)"
            size="small"
            @update:model-value="(value: Array<string | number | boolean>) => setAnswer(question.id, value.join(','))"
          >
            <el-checkbox v-for="option in question.options" :key="option.value" :value="option.value" border>
              {{ option.label }}
            </el-checkbox>
          </el-checkbox-group>
          <el-input
            v-if="question.allow_text"
            size="small"
            class="clarify-text"
            :placeholder="question.placeholder || '也可以直接写'"
            :model-value="freeTextOf(question.id)"
            @update:model-value="(value: string) => setFreeText(question.id, String(value ?? ''))"
          />
        </div>
        <div v-if="clarify.assumptions.length" class="assume-block">
          <p class="assume-title">不填的话，我按这样排</p>
          <p v-for="line in clarify.assumptions" :key="line" class="panel-note">· {{ line }}</p>
        </div>
      </div>

      <p v-if="notice" class="panel-notice">{{ notice }}</p>

      <div class="panel-actions">
        <el-button v-if="draft" :disabled="busy" @click="step = 1">继续编辑上一版</el-button>
        <el-button type="primary" :loading="busy" @click="generateSkeleton">
          {{ draft ? '换一版重新排' : '排一份路线' }}
        </el-button>
      </div>
    </section>

    <!-- ② 定取舍：一张导图，点掉不学的、点回来要学的 -->
    <section v-else-if="step === 1 && draft" class="wizard-panel">
      <p class="panel-lead">{{ draft.suggestion || '这是按教材章节顺序与前修关系排出来的路线。' }}</p>

      <!--
        范围要写在最上面。用户说"就想考中药学期末"，如果摘要只有章节数，
        他会以为范围又没生效——而这一版范围是真的生效了（后端按学科裁的域清单）。
      -->
      <p v-if="scopeText" class="scope-line">本次只排 {{ scopeText }}</p>

      <div class="budget-row">
        <span>
          共 {{ totals.units }} 节 · {{ totals.chapters }} 章 · {{ totals.points }} 个知识点 ·
          约 {{ hours(totals.minutes) }}
        </span>
        <span v-if="draft.budget">预算 {{ draft.budget.total_days }} 天 × {{ draft.budget.daily_minutes }} 分钟</span>
      </div>
      <p v-if="draft.reused_from" class="panel-note">
        沿用第 {{ draft.reused_from }} 版的取舍（同样的画像和知识图，不会得到不同的结果）。
      </p>

      <PathPlanTree
        :stages="draft.stages"
        :excluded="draft.excluded"
        :busy="busy"
        @toggle="toggleUnit"
      />

      <!--
        整科跳过要说清楚。用户说过"已经掌握中药学"，如果这一栏一个字都不提，
        他会以为模型没听懂——哪怕路线其实是对的。
      -->
      <div v-if="draft.exempt_subjects?.length" class="exempt-block">
        <p class="exempt-title">按「已有基础」整科跳过</p>
        <p v-for="item in draft.exempt_subjects" :key="item.subject_id" class="exempt-row">
          <strong>{{ item.name }}</strong>
          <span class="stage-meta">这一科的知识点不进路线；想补随时可以加回来</span>
        </p>
      </div>

      <p v-if="notice" class="panel-notice">{{ notice }}</p>

      <div class="panel-actions">
        <el-button :disabled="busy" @click="step = 0">上一步</el-button>
        <el-button type="primary" :loading="busy" @click="finalize">
          就按这个学 · 去地图
        </el-button>
      </div>
    </section>

    <!--
      ③ 确认：路线已生效。
      这里**不再推销任何"材料"**。用户要的是"排完就能看地图"：以前这一步摆四张
      卡片（导图 / 说明书 / 任务卡 / 对照表），还得再点一次"打开看一眼"——正文是模板
      生成的一堆固定段落，对"我要考这学期期末"的人没有一分信息量。确认完直接去图。
    -->
    <section v-else class="wizard-panel">
      <p class="panel-lead">路线已经生效，地图上对应的穴位会依次点亮。</p>
      <p class="panel-note">{{ doneText }}</p>
      <p class="panel-note">第一步就是最前面那一节，底部进度条会一直告诉你「现在第几步、下一步是什么」。</p>

      <div class="panel-actions">
        <el-button type="primary" @click="finish">去地图上看</el-button>
      </div>
    </section>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 三步向导：说目标 → 定取舍 → 确认。
 *
 * **为什么从四步收到三步**：原来的第 3 步是「看细节 + 调目标掌握度」。把
 * 0.70~0.95 的滑杆摆给一个还没开始学中药学的人，他能做出的判断只有"这俩数字
 * 看起来差不多"——那一屏因此变成"点下一步"的仪式，而不是决策。真正需要他判断
 * 的只有一件事：**学这些、不学那些**。所以明细并入取舍，确认后再也没有第二次点击。
 *
 * **为什么取舍是一张导图**：291 行清单把层级摊平成 291 个同权重条目，用户看不出
 * 自己砍掉的是哪一块。导图上"学科 → 章 → 节"一眼可见，砍掉的节变灰留在原位。
 *
 * **为什么编辑仍走 PATCH**：草稿阶段绝不能污染进度（`user_path_state`）。
 * 所有增删都只追加 draft 行，取消后地图上不会留一片幽灵点位。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  clarifyPlan,
  createPlanDraft,
  finalizePlanDraft,
  patchPlanDraft,
} from '../../services/pathApi'
import type { ClarifyResult, DraftOp, PlanDraft } from '../../types/path'
import PathPlanTree from './PathPlanTree.vue'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (event: 'update:modelValue', value: boolean): void; (event: 'confirmed'): void }>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const step = ref(0)
const busy = ref(false)
const clarifying = ref(false)
const goal = ref('')
const draft = ref<PlanDraft | null>(null)
const clarify = ref<ClarifyResult | null>(null)
/** 追问答案：单选取值、多选逗号连、自由文本原样。键是问题 id。 */
const answers = ref<Record<string, string>>({})
/** 每题的自由文本，与选项分开存：选了选项再改文字，不应该互相覆盖。 */
const freeText = ref<Record<string, string>>({})
const notice = ref('')
const doneText = ref('')

const title = computed(() => {
  if (step.value === 2) return '路线已生效'
  return '小岐的路线建议'
})

const totals = computed(() => {
  const rows = draft.value?.stages ?? []
  return (
    draft.value?.totals ?? {
      units: rows.length,
      chapters: new Set(rows.map((row) => `${row.subject_id}:${row.chapter_title}`)).size,
      points: rows.reduce((sum, row) => sum + row.point_count, 0),
      minutes: rows.reduce((sum, row) => sum + row.estimated_minutes, 0),
    }
  )
})

/** 本次的学科范围；空 = 六门全参与（不提"只排"两个字）。 */
const scopeText = computed(() =>
  (draft.value?.scope_subjects ?? []).map((item) => item.name).join('、'),
)

const hours = (minutes: number) => {
  if (!minutes) return '—'
  return minutes < 60 ? `${minutes} 分钟` : `${(minutes / 60).toFixed(1)} 小时`
}

const answerOf = (id: string) => answers.value[id] ?? ''
const freeTextOf = (id: string) => freeText.value[id] ?? ''
const splitAnswer = (id: string) => (answers.value[id] ? answers.value[id].split(',').filter(Boolean) : [])
const setAnswer = (id: string, value: string) => {
  answers.value = { ...answers.value, [id]: value }
}
const setFreeText = (id: string, value: string) => {
  freeText.value = { ...freeText.value, [id]: value }
}

/**
 * 自由文本优先：用户既点了"一年内考研"又在框里写"明年 3 月"，他要的是后者。
 * 但**不能**把两者简单拼起来（"exam_far 明年 3 月"这种脏值会让选项匹配失效），
 * 所以有文字就用文字，没有才用选项。
 */
const collectAnswers = () => {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(answers.value)) {
    const text = (freeText.value[key] ?? '').trim()
    if (text) out[key] = text
    else if (value.trim()) out[key] = value
  }
  for (const [key, value] of Object.entries(freeText.value)) {
    if (!out[key] && value.trim()) out[key] = value.trim()
  }
  return out
}

const reset = () => {
  step.value = 0
  notice.value = ''
}

const guard = async (work: () => Promise<void>) => {
  busy.value = true
  notice.value = ''
  try {
    await work()
  } catch (error) {
    const message = error instanceof Error ? error.message : '这一步没走通'
    notice.value = message
    ElMessage.warning(message)
  } finally {
    busy.value = false
  }
}

const runClarify = () =>
  guard2(async () => {
    clarify.value = await clarifyPlan({ goal: goal.value, answers: collectAnswers() })
  }, (value) => (clarifying.value = value))

/** 追问接口要单独管 loading：它不该把整个面板变成"排路线中"。 */
async function guard2(work: () => Promise<void>, setLoading: (value: boolean) => void) {
  setLoading(true)
  try {
    await work()
  } catch (error) {
    // 追问失败不挡路：拿不到问题就按默认值排，但要说一句
    notice.value = error instanceof Error ? `追问没问成，仍可以直接排：${error.message}` : ''
  } finally {
    setLoading(false)
  }
}

const generateSkeleton = () =>
  guard(async () => {
    if (!clarify.value) {
      // 用户直接点"排一份路线"：先补一次追问检查，缺的项仍按默认值排，但要摆出来
      clarify.value = await clarifyPlan({ goal: goal.value, answers: collectAnswers() })
    }
    draft.value = await createPlanDraft({ goal: goal.value, answers: collectAnswers() })
    step.value = 1
  })

const applyOps = (ops: DraftOp[]) =>
  guard(async () => {
    if (!draft.value) return
    const updated = await patchPlanDraft(draft.value.version, ops)
    draft.value = updated
    if (updated.rejected.length) {
      notice.value = updated.rejected.map((item) => item.message).join('；')
    }
  })

/**
 * 点一下导图上的节 = 加回或去掉。
 *
 * `include` 只给域名时后端会把该域所有被排除的单元一起加回，所以加回来
 * 用 `after` 定位到**同章最后一行**，让它回到原来的章里，而不是整条路线末尾。
 */
const toggleUnit = (payload: { stageId: string; domainId: string; include: boolean }) => {
  const rows = draft.value?.stages ?? []
  if (!payload.include) {
    void applyOps([{ op: 'remove', ids: [payload.stageId] }])
    return
  }
  const excludedRow = (draft.value?.excluded ?? []).find(
    (row) => String(row.stage_id || row.domain_id) === payload.stageId,
  )
  const chapter = excludedRow?.chapter_title ?? ''
  const anchor = [...rows]
    .reverse()
    .find((row) => (row.chapter_title ?? '') === chapter && (row.subject_id ?? '') === (excludedRow?.subject_id ?? ''))
  void applyOps([{ op: 'include', domain_id: payload.domainId, after: anchor?.stage_id || anchor?.domain_id || '' }])
}

const finalize = () =>
  guard(async () => {
    if (!draft.value) return
    const result = await finalizePlanDraft(draft.value.version)
    const points = result.totals?.points ?? result.node_count
    doneText.value = `共 ${result.versions.length} 条记录，${points} 个知识点已排进路线。`
    step.value = 2
    emit('confirmed')
  })

const finish = () => {
  visible.value = false
  reset()
}
</script>

<style scoped lang="scss">
@use '../../styles/tokens.scss' as *;

.wizard-steps {
  margin-bottom: var(--qh-space-4);
}

.wizard-panel {
  display: grid;
  gap: var(--qh-space-3);
}

.panel-lead {
  margin: 0;
  color: var(--qh-text);
  font-size: 13.5px;
  line-height: 1.7;
}

.panel-note {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 12px;
  line-height: 1.65;
}

.panel-notice {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--qh-color-gold) 40%, transparent);
  border-radius: var(--qh-radius-sm);
  background: color-mix(in srgb, var(--qh-color-gold) 10%, transparent);
  color: var(--qh-text);
  font-size: 12px;
  line-height: 1.6;
}

.panel-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--qh-space-2);
  margin-top: var(--qh-space-2);
}

.clarify-actions {
  display: flex;
  align-items: center;
  gap: var(--qh-space-2);
}

.clarify-block {
  display: grid;
  gap: var(--qh-space-3);
  padding: var(--qh-space-3);
  border-left: 2px solid color-mix(in srgb, var(--qh-color-jade) 55%, transparent);
  border-radius: var(--qh-radius-sm);
  background: color-mix(in srgb, var(--qh-color-jade) 5%, transparent);
}

.clarify-row {
  display: grid;
  gap: 4px;
}

.clarify-question {
  margin: 0;
  color: var(--qh-text);
  font-size: 13px;
  font-weight: 600;
}

/* 选项可能是 4~6 个，换行排列，不要横向滚动 */
.clarify-row :deep(.el-radio-group),
.clarify-row :deep(.el-checkbox-group) {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.clarify-row :deep(.el-radio),
.clarify-row :deep(.el-checkbox) {
  margin-right: 0;
}

.clarify-text {
  max-width: 320px;
}

.assume-block {
  display: grid;
  gap: 2px;
  padding-top: 6px;
  border-top: 1px dashed color-mix(in srgb, var(--qh-text-muted) 30%, transparent);
}

.assume-title {
  margin: 0;
  color: var(--qh-text);
  font-size: 12px;
  font-weight: 600;
}

/*
 * 选择器是**卡片流**而不是单选按钮组。
 *
 * 三种文稿的差别不在名字上（"说明书 / 任务卡 / 对照表"读起来都像好东西），
 * 而在"应含什么、长什么样、为什么这样更好用、什么时候拿得出手"。这些必须
 * 摊开写在卡片里，用户才有办法做判断；一颗 radio 装不下四行字。
 */
.budget-row {
  display: flex;
  justify-content: space-between;
  gap: var(--qh-space-3);
  color: var(--qh-text-muted);
  font-size: 12px;
}

.exempt-block {
  display: grid;
  gap: 4px;
  padding: var(--qh-space-3);
  border-left: 2px solid color-mix(in srgb, var(--qh-color-gold) 60%, transparent);
  border-radius: var(--qh-radius-sm);
  background: color-mix(in srgb, var(--qh-color-gold) 7%, transparent);
}

.exempt-title {
  margin: 0;
  color: var(--qh-text);
  font-size: 12.5px;
  font-weight: 700;
}

.exempt-row {
  display: flex;
  align-items: baseline;
  gap: var(--qh-space-2);
  margin: 0;

  strong {
    color: var(--qh-text);
    font-size: 12.5px;
  }
}

.stage-meta {
  color: var(--qh-text-muted);
  font-size: 11px;
  line-height: 1.5;
}

@media (max-width: 640px) {
  .budget-row {
    flex-direction: column;
    gap: 2px;
  }
}
</style>
