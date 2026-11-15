<template>
  <!--
    图标是**装饰**，不是内容。取不到就什么都不画——留个空位比画个裂图标好，
    因为空位读者看不出来，裂图标会把注意力全吸走。
  -->
  <span v-if="body" class="q-icon" :style="style" aria-hidden="true" v-html="body" />
</template>

<script setup lang="ts">
/**
 * Iconify 图标。
 *
 * 为什么运行时取而不是打包：图标是**可选装饰**，一套几十万个图标打进包只会拖慢首屏。
 * 而 Iconify 的接口带 CORS、返回 `currentColor` 的 SVG——颜色自动跟着文字走，不用为
 * 每套配色再出一份图标。
 *
 * 三层缓存：内存 Map（同步命中）→ localStorage（跨刷新）→ 网络。
 * 失败一律静默：图标不该让页面崩。
 */

import { computed, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    /** `prefix:name`，如 `fluent:leaf-24-regular`。**不是**中文词。 */
    name: string
    size?: number | string
    /** 描边粗细随字号缩放时用；不传就跟随文字。 */
    stroke?: number
  }>(),
  { size: 18, stroke: undefined },
)

const STORAGE_PREFIX = 'qh:icon:'
const TTL_MS = 30 * 24 * 3600 * 1000

const memory = new Map<string, string>()
const body = ref('')

const style = computed(() => ({
  width: typeof props.size === 'number' ? `${props.size}px` : props.size,
  height: typeof props.size === 'number' ? `${props.size}px` : props.size,
}))

const readCache = (key: string): string => {
  if (memory.has(key)) return memory.get(key) ?? ''
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + key)
    if (!raw) return ''
    const parsed = JSON.parse(raw) as { body: string; at: number }
    if (!parsed?.body || Date.now() - parsed.at > TTL_MS) {
      localStorage.removeItem(STORAGE_PREFIX + key)
      return ''
    }
    memory.set(key, parsed.body)
    return parsed.body
  } catch {
    // 隐私模式下 localStorage 会抛异常，直接当没有缓存。
    return ''
  }
}

const writeCache = (key: string, markup: string) => {
  memory.set(key, markup)
  try {
    localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify({ body: markup, at: Date.now() }))
  } catch {
    // 配额满 / 禁用：内存缓存仍然有效，不影响渲染。
  }
}

/** 只取 `<svg>` 内部：外层由我们自己控制尺寸与颜色。 */
const innerOf = (svg: string) => {
  const start = svg.indexOf('>')
  const end = svg.lastIndexOf('</svg>')
  return start >= 0 && end > start ? svg.slice(start + 1, end) : ''
}

const load = async (name: string) => {
  body.value = ''
  const [prefix, icon] = name.split(':')
  if (!prefix || !icon) return

  const cached = readCache(name)
  if (cached) {
    body.value = cached
    return
  }
  try {
    const response = await fetch(`https://api.iconify.design/${prefix}/${icon}.svg`)
    if (!response.ok) return
    const markup = innerOf(await response.text())
    if (!markup) return
    writeCache(name, markup)
    // 请求期间名字可能已经变了，落笔前再确认一次。
    if (props.name === name) body.value = markup
  } catch {
    // 离线 / 被拦截：不画。
  }
}

watch(
  () => props.name,
  (name) => {
    if (name) void load(name)
  },
  { immediate: true },
)
</script>

<style scoped lang="scss">
.q-icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  color: currentColor;
  vertical-align: -0.15em;
}

/* 图标内部一律用 currentColor，颜色只从父级继承——一图标两色是最常见的丑。 */
.q-icon :deep(svg) {
  width: 100%;
  height: 100%;
  fill: currentColor;
  stroke: currentColor;
  stroke-width: v-bind('props.stroke ?? undefined');
}
</style>
