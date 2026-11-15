<template>
  <div class="a11y-mirror" :class="{ 'is-open': open }">
    <button type="button" class="a11y-toggle" :aria-expanded="open" @click="open = !open">
      {{ open ? '收起键盘浏览' : '键盘浏览穴位' }}
    </button>

    <div v-show="open" class="a11y-panel">
      <p class="a11y-head">{{ channelTitle }} · {{ items.length }} 个穴位</p>
      <ul ref="listRef" role="list" :aria-label="`${channelTitle}，${items.length} 个穴位`" @keydown="onKeydown">
        <li v-for="item in items" :key="item.id">
          <button
            type="button"
            :aria-current="item.id === selected ? 'true' : undefined"
            :aria-label="item.label"
            @click="select(item.id)"
            @focus="announce(item.spoken)"
          >
            <span class="a11y-name">{{ item.name }}</span>
            <span class="a11y-code">{{ item.code }}</span>
            <span class="a11y-state">{{ item.stateLabel }}</span>
          </button>
        </li>
      </ul>
      <p class="a11y-hint">↑↓ 移动 · Enter 打开 · Esc 退回全身</p>
    </div>

    <p class="a11y-live" role="status" aria-live="polite">{{ live }}</p>
  </div>
</template>

<script setup lang="ts">
/**
 * 3D 的无障碍镜像。
 *
 * Canvas 对屏幕阅读器是空白的——读到的只有一堆 0，没有任何语义。唯一可行的做法是
 * 把当前视野里的穴位渲染成一份**视觉隐藏但可聚焦**的 DOM 列表：Tab / ↑↓ 在这里移动，
 * 选中项在 3D 里高亮并播报。3D 画布本身标 `aria-hidden`。
 *
 * 这一层如果省掉，整个 3D 方案在无障碍上是不及格的——2D 里 aria-label 是白送的，
 * 3D 里必须自己补回来。
 */
import { computed, ref } from 'vue'
import type { MeridianAcupoint } from '../../services/meridianApi'
import { presenceLabel, type AcupointPresence } from './meridianPresence'

const props = defineProps<{
  acupoints: MeridianAcupoint[]
  presence: Map<string, AcupointPresence>
  selected: string | null
  channelTitle: string
  sectionTitles: Map<string, string[]>
}>()

const emit = defineEmits<{
  (event: 'select', id: string): void
  (event: 'back'): void
}>()

const open = ref(false)
const live = ref('')
const listRef = ref<HTMLElement>()

const items = computed(() =>
  props.acupoints.map((point) => {
    const state = props.presence.get(point.id)
    const titles = props.sectionTitles.get(point.id) ?? []
    const stateLabel = state ? presenceLabel[state.presence] : '未挂章节'
    const progress = state && state.total > 0 ? `，完成度 ${Math.round(state.ratio * 100)}%` : ''
    return {
      id: point.id,
      name: point.name,
      code: point.code,
      stateLabel,
      label: `${point.name} ${point.code}，${props.channelTitle}${titles.length ? `，挂${titles.join('、')}` : ''}，${stateLabel}${progress}`,
      spoken: `${point.name}，${stateLabel}${progress}`,
    }
  }),
)

const announce = (text: string) => {
  live.value = text
}

const select = (id: string) => {
  emit('select', id)
}

const onKeydown = (event: KeyboardEvent) => {
  const buttons = [...(listRef.value?.querySelectorAll('button') ?? [])] as HTMLButtonElement[]
  if (!buttons.length) return
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const next = event.key === 'ArrowDown' ? index + 1 : index - 1
    buttons[(next + buttons.length) % buttons.length]?.focus()
  } else if (event.key === 'Escape') {
    event.preventDefault()
    emit('back')
  }
}
</script>

<style scoped lang="scss">
.a11y-mirror {
  position: absolute;
  /* 底部那一行让给路线条：条从左铺到 right:190px，入口占住最右侧。 */
  right: var(--qh-space-4);
  bottom: var(--qh-space-4);
  z-index: 4;
  display: flex;
  /* 反着排：面板向上长，按钮永远压在底边 */
  flex-direction: column-reverse;
  align-items: flex-end;
}

.a11y-toggle {
  padding: 4px 10px;
  border: 1px solid color-mix(in srgb, var(--qh-color-jade) 28%, transparent);
  border-radius: var(--qh-radius-pill);
  background: color-mix(in srgb, var(--qh-color-night-soft) 78%, transparent);
  color: var(--qh-text-muted);
  font-size: 11px;
  cursor: pointer;

  &:hover,
  &:focus-visible {
    border-color: var(--qh-color-jade);
    color: var(--qh-color-cream);
  }
}

.a11y-panel {
  width: 240px;
  max-height: 320px;
  margin-top: 8px;
  padding: var(--qh-space-3);
  overflow: auto;
  border: 1px solid color-mix(in srgb, var(--qh-color-jade) 22%, transparent);
  border-radius: var(--qh-radius-stage);
  background: color-mix(in srgb, var(--qh-color-night-soft) 86%, transparent);
  backdrop-filter: blur(12px);

  ul {
    display: grid;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  button {
    display: grid;
    width: 100%;
    padding: 5px 6px;
    border: 1px solid transparent;
    border-radius: 6px;
    background: none;
    color: var(--qh-text);
    font-size: 11px;
    text-align: left;
    cursor: pointer;
    grid-template-columns: 1fr auto;

    &:hover,
    &:focus-visible {
      border-color: color-mix(in srgb, var(--qh-color-jade) 55%, transparent);
      background: color-mix(in srgb, var(--qh-color-jade) 12%, transparent);
      outline: none;
    }

    &[aria-current='true'] {
      border-color: var(--qh-color-gold);
      background: color-mix(in srgb, var(--qh-color-gold) 14%, transparent);
    }
  }

  .a11y-name {
    font-weight: 600;
  }

  .a11y-code {
    grid-column: 2;
    color: var(--qh-color-jade);
    font-size: 9.5px;
    letter-spacing: 0.08em;
  }

  .a11y-state {
    grid-column: 1 / -1;
    color: var(--qh-text-muted);
    font-size: 9.5px;
  }
}

.a11y-head,
.a11y-hint {
  margin: 0 0 6px;
  color: var(--qh-text-muted);
  font-size: 10px;
  letter-spacing: 0.06em;
}

.a11y-hint {
  margin: 6px 0 0;
}

// 视觉隐藏但可聚焦：屏幕阅读器与键盘用户走这里，鼠标用户看不见它
.a11y-live {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
</style>
