<template>
  <article
    ref="root"
    class="scene"
    :style="[vars, fontVars]"
    :data-slide-id="slideId"
    :data-scene-note="scene.note"
  >
    <!-- 背景：纸母题 / 满版照片 / 纯色。这是「带角花」与「整幅图铺底」的唯一分野。 -->
    <svg
      v-if="background.kind === 'motif' && backgroundSvg"
      class="bg-svg"
      viewBox="0 0 100 56.25"
      preserveAspectRatio="none"
      aria-hidden="true"
      v-html="backgroundSvg"
    />
    <img
      v-if="background.kind === 'asset' && !isDecorativeAsset(background.asset) && backgroundFile && !failed.has('__bg__')"
      class="bg-img"
      :src="backgroundFile"
      alt=""
      loading="lazy"
      @error="onImageError"
    />
    <!-- 真纹样底纹（ground- 资产）：单色重着色 + 低透明度。这批资产是黑填充的
         整幅图案，直接 <img> 上纸就是一张深灰墙纸；染成前景色压到 12%，
         才是「纸上有纹」而不是「纸上贴了一张花布」。 -->
    <span
      v-else-if="background.kind === 'asset' && isDecorativeAsset(background.asset) && backgroundFile"
      class="bg-decor"
      :style="bgDecorStyle"
      aria-hidden="true"
    />
    <span
      v-if="background.kind === 'asset' && background.veil && !isDecorativeAsset(background.asset)"
      class="bg-veil"
      :style="veilStyle"
      aria-hidden="true"
    />

    <!-- 基元层。按 z 排序后顺序画，后画的压前画的。 -->
    <template v-for="(item, position) in painted" :key="`${item.type}-${position}`">
      <!-- 图片：库内素材 / 生成图。装裱（hairline/mat）与调性（toned/duotone）
           是母版的设计决定，渲染层只翻译；旧场景没有这些字段就是原样。
           真纹样（ornament-/ground-）走单色重着色，不走 <img>：这批资产是
           黑填充的整幅图案，染色后才是「母版自己的角花」。 -->
      <span
        v-if="item.type === 'asset' && isDecorativeAsset(item.asset)"
        class="el is-decor"
        :style="decorStyle(item)"
        aria-hidden="true"
      />
      <figure
        v-else-if="item.type === 'asset'"
        class="el is-asset"
        :style="[boxStyle(item), frameStyle(item)]"
      >
        <img
          v-if="!failed.has(item.asset)"
          :src="assetFile(item.asset)"
          :alt="item.caption"
          :style="assetImgStyle(item)"
          loading="lazy"
          @error="failed.add(item.asset)"
        />
        <figcaption v-if="item.caption" class="cap">{{ item.caption }}</figcaption>
      </figure>

      <!-- 母题：参数化 SVG，现场生成。viewBox 由母题自己声明，不在这里猜。 -->
      <svg
        v-else-if="item.type === 'motif'"
        class="el"
        :style="boxStyle(item)"
        :viewBox="motifViewBox(item.motif)"
        preserveAspectRatio="xMidYMid meet"
        aria-hidden="true"
        v-html="motifMarkup(item)"
      />

      <!-- 面：色块 / 描边块 -->
      <span v-else-if="item.type === 'rect'" class="el is-rect" :style="rectStyle(item)" aria-hidden="true" />

      <!-- 线：横竖细线，界面里最常见的结构元素 -->
      <span v-else-if="item.type === 'rule'" class="el is-rule" :style="ruleStyle(item)" aria-hidden="true" />

      <span v-else-if="item.type === 'circle'" class="el is-circle" :style="circleStyle(item)" aria-hidden="true" />

      <!-- 印章：方/圆，强调色底、纸色字 -->
      <span v-else-if="item.type === 'seal'" class="el is-seal" :style="sealStyle(item)">
        {{ item.content }}
      </span>

      <!-- 文字：横排 / 竖排，字族由 font 角色决定 -->
      <p
        v-else-if="item.type === 'text' && item.text"
        class="el is-text"
        :style="textStyle(item)"
      >{{ item.text.content }}</p>
    </template>

    <!-- 页脚：只有正文页给，封面/章节不压页码。 -->
    <footer v-if="showFoot" class="foot">
      <span class="foot-title">{{ deckTitle }}</span>
      <span class="page-no">{{ String(index + 1).padStart(2, '0') }} / {{ String(total).padStart(2, '0') }}</span>
    </footer>
  </article>
</template>

<script setup lang="ts">
/**
 * 场景渲染器。
 *
 * 这个组件只做一件事：把 `Scene` 画出来。它**不认识任何版式**——没有 `if (cover)`，
 * 没有「封面走这套、内页走那套」。九个基元画完，剩下的全靠数据。
 *
 * 与 `SlideSurface.vue` 的关系：那个是旧的十版式渲染器，靠 CSS class 分支；这个是新
 * 路径，靠 `Scene`。两者并存一段时间，`SceneSurface` 优先，缺 `scene` 时回落旧路径。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch, type CSSProperties } from 'vue'
import { pptThemeById, pptThemeVars } from '../../config/pptThemes'
import { pptFontVars } from '../../config/pptFonts'
import { renderMotif } from '../../render/motifs'
import { assetSrc } from '../../render/assetSrc'
import {
  SCENE_TEXT_ROLES,
  countElements,
  isDecorativeAsset,
  motifViewBox,
  type Scene,
  type SceneElement,
} from '../../render/scene'

const props = withDefaults(
  defineProps<{
    scene: Scene
    themeId: string
    fontPairId?: string
    index?: number
    total?: number
    deckTitle?: string
    slideId?: string
    /**
     * 素材地址表：`asset_id` → 地址。远程素材（馆藏 / 生成图）由稿件里的
     * `asset_refs.url` 给出；缺省时按本地库命名规则拼。
     */
    assets?: Record<string, string>
    /** 封面与章节页不压页码：页码是「册子」的东西，封面是封面。 */
    hideFoot?: boolean
    /**
     * 自定义配色（设计单上的 palette）。`#RRGGBB`，只覆盖给到的槽位。
     * 模型为用户调了色，就必须真的画出来——否则「按方案做」是句空话。
     */
    palette?: Record<string, string>
  }>(),
  {
    fontPairId: 'song-kai',
    index: 0,
    total: 1,
    deckTitle: '',
    slideId: '',
    assets: () => ({}),
    hideFoot: false,
    palette: () => ({}),
  },
)

/** 加载失败的素材记在这里，避免裂图。 */
const failed = reactive(new Set<string>())

const theme = computed(() => pptThemeById(props.themeId))

/** 方案里的自定义色覆盖主题色。缺哪一项就用主题的，绝不半套。 */
const hex = computed(() => {
  const base = theme.value.hex
  const custom = props.palette || {}
  return {
    bg: custom.bg || `#${base.bg}`,
    fg: custom.fg || `#${base.fg}`,
    muted: custom.muted || `#${base.muted}`,
    accent: custom.accent || `#${base.accent}`,
    line: custom.line || `#${base.line}`,
  }
})
const vars = computed(() => ({
  ...pptThemeVars(props.themeId),
  '--ppt-bg': hex.value.bg,
  '--ppt-fg': hex.value.fg,
  '--ppt-muted': hex.value.muted,
  '--ppt-accent': hex.value.accent,
  '--ppt-line': hex.value.line,
}))
const fontVars = computed(() => pptFontVars(props.fontPairId))
const motifPalette = computed(() => ({
  bg: hex.value.bg,
  fg: hex.value.fg,
  muted: hex.value.muted,
  accent: hex.value.accent,
  line: hex.value.line,
}))

const background = computed(() => props.scene.background)

const backgroundSvg = computed(() =>
  background.value.kind === 'motif'
    ? renderMotif(background.value.motif, {
        palette: motifPalette.value,
        params: background.value.params as Record<string, number>,
        seed: (props.index ?? 0) + 1,
      })
    : '',
)

/** 素材地址：先查稿件钉的地址，再按本地库命名规则推（`render/assetSrc.ts`）。 */
const assetFile = (id: string) => assetSrc(id, props.assets)
const backgroundFile = computed(() =>
  background.value.kind === 'asset' ? assetFile(background.value.asset) : '',
)

/**
 * 真纹样资产（装饰素材包）。它们是黑填充的整幅 SVG 图案——**不能当照片画**：
 * 直接 <img> 上纸就是一张深灰墙纸/黑角块。统一走单色重着色（CSS mask 染成
 * 前景色），底纹再压到低透明度，纹样才融得进纸面。判定函数在 render/scene.ts，
 * 与导出端共用。
 */
const decorTint = computed(() => hex.value.fg)

/** 角花/横带位：染前景色、保持形状（contain 不裁切），透明度随元素。 */
const decorStyle = (item: SceneElement) => {
  const [x, y, w, h] = item.box
  const src = assetFile(item.asset)
  // mask-image 是 longhand，只收图片引用；位置/尺寸/平铺必须拆成
  // mask-position / mask-size / mask-repeat 分项写，混写整条声明会被丢弃
  // → 没有遮罩 → 整块背景色矩形上纸（四角实心黑方块就是它）。
  const maskImage = `url("${src}")`
  return {
    left: `${x}cqw`,
    top: `${y}cqw`,
    width: `${w}cqw`,
    height: `${h}cqw`,
    zIndex: String(item.z),
    opacity: String(item.opacity),
    transform: transformOf(item),
    backgroundColor: decorTint.value,
    maskImage,
    WebkitMaskImage: maskImage,
    maskPosition: 'center',
    WebkitMaskPosition: 'center',
    maskSize: 'contain',
    WebkitMaskSize: 'contain',
    maskRepeat: 'no-repeat',
    WebkitMaskRepeat: 'no-repeat',
  } as Record<string, string | undefined>
}

/** 底纹位：整幅铺满、染前景色、压到 12%——「纸上有纹」，不是「贴了张花布」。 */
const bgDecorStyle = computed(() => {
  const src = backgroundFile.value
  const maskImage = `url("${src}")`
  return {
    backgroundColor: decorTint.value,
    maskImage,
    WebkitMaskImage: maskImage,
    maskPosition: 'center',
    WebkitMaskPosition: 'center',
    maskSize: 'cover',
    WebkitMaskSize: 'cover',
    maskRepeat: 'repeat',
    WebkitMaskRepeat: 'repeat',
    opacity: '0.12',
  } as Record<string, string | undefined>
})

const veilStyle = computed(() => {
  const veil = background.value.veil
  if (!veil) return {}
  const key = veil.color === 'bg' ? 'bg' : veil.color === 'muted' ? 'muted' : veil.color === 'accent' ? 'accent' : veil.color === 'line' ? 'line' : 'fg'
  const solid = hex.value[key]
  const gradient =
    veil.direction === 'flat'
      ? solid
      : `linear-gradient(${
          { from_left: '90deg', from_right: '270deg', from_bottom: '0deg', from_top: '180deg', flat: '0deg' }[
            veil.direction
          ]
        }, ${solid} 0%, rgba(0,0,0,0) 100%)`
  return { background: gradient, opacity: String(veil.opacity) }
})

const onImageError = () => failed.add('__bg__')

/**
 * group 拍平。
 *
 * 不做递归组件：group 只是「一组相对坐标」，拍平成绝对坐标之后再画，渲染逻辑
 * 就只有一个循环。递归组件会为了省这一次循环，引入 `name`、`self-reference`、
 * 递归深度上限三个问题——收益为负。
 */
const painted = computed<SceneElement[]>(() => {
  const out: SceneElement[] = []
  const walk = (elements: SceneElement[], origin?: [number, number, number, number]) => {
    for (const element of elements) {
      if (element.type === 'group') {
        const [gx, gy, gw, gh] = element.box
        const base: [number, number, number, number] = origin
          ? [origin[0] + gx * origin[2], origin[1] + gy * origin[3], gw * origin[2], gh * origin[3]]
          : [gx, gy, gw, gh]
        walk(element.items, base)
        continue
      }
      if (!origin) {
        out.push(element)
        continue
      }
      const [x, y, w, h] = element.box
      out.push({
        ...element,
        box: [origin[0] + x * origin[2], origin[1] + y * origin[3], w * origin[2], h * origin[3]],
      })
    }
  }
  walk(props.scene.elements)
  return out.sort((a, b) => a.z - b.z)
})

const showFoot = computed(() => !props.hideFoot && countElements(props.scene) > 0)

/** box → 绝对定位。单位 cqw：舞台变宽，一切等比变。 */
const box = (item: SceneElement) => {
  const [x, y, w, h] = item.box
  return {
    left: `${x}cqw`,
    top: `${y}cqw`,
    width: `${w}cqw`,
    height: `${h}cqw`,
    zIndex: String(item.z),
  }
}

/** 旋转 + 镜像合成一个 transform。先镜像再旋转：角花先翻面再摆位，语义上更自然。 */
const transformOf = (item: SceneElement) => {
  const scale =
    item.flip === 'x' ? 'scaleX(-1)' : item.flip === 'y' ? 'scaleY(-1)' : item.flip === 'xy' ? 'scale(-1, -1)' : ''
  const rotate = item.rotate ? `rotate(${item.rotate}deg)` : ''
  return [scale, rotate].filter(Boolean).join(' ') || undefined
}

const boxStyle = (item: SceneElement) => ({
  ...box(item),
  opacity: String(item.opacity),
  transform: transformOf(item),
  filter: shadowFilter(item),
})

/**
 * 阴影：强度 0~1 翻译成一枚向下的软影。阴影该多深是母版（深底母版给 0.4）的事，
 * 这里只做翻译——drop-shadow 跟随元素轮廓，圆角图位也能跟着圆。
 */
const shadowFilter = (item: SceneElement) =>
  item.shadow > 0
    ? `drop-shadow(0 0.35cqw 0.7cqw rgba(0, 0, 0, ${Math.min(0.55, 0.14 + item.shadow * 0.32).toFixed(2)}))`
    : undefined

/** 装裱。mat = 衬纸一圈再嵌图（宣纸系母版的古籍图版感）；hairline = 细线框。 */
const frameStyle = (item: SceneElement): CSSProperties => {
  if (item.frame === 'hairline') {
    return { border: `0.14cqw solid ${colorOf('line')}`, boxSizing: 'border-box' }
  }
  if (item.frame === 'mat') {
    return {
      background: colorOf('bg'),
      padding: '0.9cqw',
      border: `0.1cqw solid ${colorOf('line')}`,
      boxSizing: 'border-box',
    }
  }
  return {}
}

/** 图片调性。真双色调需要 SVG 滤镜，这两条 CSS 近似在屏幕上已足够「不是原图」。 */
const treatmentFilter = (item: SceneElement) =>
  item.img_treatment === 'toned'
    ? 'grayscale(0.32) sepia(0.28) saturate(1.05)'
    : item.img_treatment === 'duotone'
      ? 'grayscale(1) sepia(0.62) saturate(1.9) contrast(1.05)'
      : undefined

/** 角色名 → hex。只认五个角色，写错的角色退到 `fg` 而不是变透明（透明=看不见字）。 */
const ROLE_KEYS = ['bg', 'fg', 'muted', 'accent', 'line'] as const
type RoleKey = (typeof ROLE_KEYS)[number]

const colorOf = (role: string | null | undefined) => {
  if (!role) return 'transparent'
  const key = ROLE_KEYS.find((item) => item === role) as RoleKey | undefined
  return key ? hex.value[key] : hex.value.fg
}

const rectStyle = (item: SceneElement) => ({
  ...boxStyle(item),
  // fill2 = 双色渐变（数值卡的软底板：fg→bg 纵向淡出）。fill 为 null 时从透明渐入。
  background: item.fill2
    ? `linear-gradient(${item.gradient_dir === 'h' ? 'to right' : 'to bottom'}, ${colorOf(item.fill)}, ${colorOf(item.fill2)})`
    : item.fill
      ? colorOf(item.fill)
      : 'transparent',
  border: item.stroke ? `${item.stroke_width || 0.12}cqw ${item.dash === 'solid' ? 'solid' : item.dash} ${colorOf(item.stroke)}` : 'none',
  borderRadius: item.radius ? `${item.radius}cqw` : undefined,
})

const ruleStyle = (item: SceneElement) => {
  const color = colorOf(item.fill ?? 'line')
  return {
    ...boxStyle(item),
    background: item.dash === 'solid' ? color : undefined,
    borderTop: item.dash !== 'solid' && item.orient === 'h' ? `${item.weight}cqw ${item.dash} ${color}` : undefined,
    borderLeft: item.dash !== 'solid' && item.orient === 'v' ? `${item.weight}cqw ${item.dash} ${color}` : undefined,
  }
}

const circleStyle = (item: SceneElement) => ({
  ...boxStyle(item),
  background: item.fill ? colorOf(item.fill) : 'transparent',
  border: item.stroke ? `${item.stroke_width || 0.12}cqw solid ${colorOf(item.stroke)}` : 'none',
  borderRadius: '50%',
})

const sealStyle = (item: SceneElement) => ({
  ...boxStyle(item),
  background: colorOf(item.fill ?? 'accent'),
  color: colorOf('bg'),
  borderRadius: item.shape === 'round' ? '50%' : '0.25cqw',
})

const textStyle = (item: SceneElement) => {
  const text = item.text!
  const spec = SCENE_TEXT_ROLES[text.role] ?? SCENE_TEXT_ROLES.body
  const size = text.size ?? spec.size
  return {
    ...boxStyle(item),
    fontFamily: text.font === 'display' ? 'var(--s-font-display)' : 'var(--s-font-body)',
    fontSize: `${size}cqw`,
    fontWeight: String(text.weight ?? spec.weight),
    lineHeight: String(text.line_height),
    letterSpacing: text.tracking ? `${text.tracking}cqw` : undefined,
    color: colorOf(text.color),
    textAlign: text.align,
    writingMode: text.write === 'v' ? 'vertical-rl' : 'horizontal-tb',
    textOrientation: text.write === 'v' ? 'upright' : undefined,
  } as Record<string, string | undefined>
}

const assetImgStyle = (item: SceneElement) => ({
  objectFit: item.fit,
  borderRadius: item.radius ? `${item.radius}cqw` : undefined,
  filter: treatmentFilter(item),
})

const motifMarkup = (item: SceneElement) =>
  renderMotif(item.motif, {
    palette: motifPalette.value,
    params: (item.params ?? {}) as Record<string, number>,
    seed: (props.index ?? 0) + 3,
  })

/**
 * 文字装不下就在**框内缩字号**。
 *
 * 几何是模型给的，字数是这一页的正文给的，两者对不上时后端只能报
 * `text_overflow` 警告——渲染层若不接住，用户看到的就是半句话被切断
 * （「道地药材：特定产区出产、品质与疗效公认更优」后面没了）。
 *
 * 只缩这一块的字号（最多到基准的 54%，一行两行的高度差通常就在这个量级），
 * 不缩放整页：整页缩放会把留白、笔画粗细、图位一起改小，那才是「字发虚」。
 * 缩到底仍装不下就由 `overflow: hidden` 兜底，不让正文溢到纸上。
 */
const root = ref<HTMLElement | null>(null)

const fitTexts = () => {
  const host = root.value
  if (!host) return
  host.querySelectorAll<HTMLElement>('.is-text').forEach((el) => {
    el.style.removeProperty('font-size')
    const base = Number.parseFloat(getComputedStyle(el).fontSize) || 0
    if (!base) return
    let size = base
    for (let step = 0; step < 10 && el.scrollHeight > el.clientHeight + 1; step += 1) {
      size *= 0.94
      el.style.fontSize = `${size}px`
    }
  })
}

let fitObserver: ResizeObserver | null = null
onMounted(() => {
  void nextTick(fitTexts)
  if (typeof ResizeObserver === 'undefined') return
  fitObserver = new ResizeObserver(() => fitTexts())
  if (root.value) fitObserver.observe(root.value)
})
onBeforeUnmount(() => fitObserver?.disconnect())
watch(() => props.scene, () => void nextTick(fitTexts), { deep: true })
</script>

<style scoped lang="scss">
/*
 * 一块 16:9 的纸。
 *
 * 与旧 `SlideSurface` 不同，这里**没有 padding**：坐标全部由 `box` 给，版心是
 * 模型自己的事。给一个全局 padding 等于偷偷加一层版心，模型的 `x=9` 就变成 18 了。
 *
 * 单位一律 cqw：舞台宽度变，所有坐标等比变，不需要 `transform: scale`，
 * 也就不会出现缩放后文字发虚。
 */
.scene {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  color: var(--ppt-fg);
  background: var(--ppt-bg);
  font-family: var(--s-font-body);
}

.bg-svg,
.bg-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.bg-img {
  object-fit: cover;
}

.bg-veil {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.bg-decor {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.el {
  position: absolute;
  margin: 0;
}

.is-asset {
  display: block;
  overflow: hidden;
}

.is-decor {
  display: block;
  pointer-events: none;
}

.is-asset img {
  display: block;
  width: 100%;
  height: 100%;
}

.is-text {
  /* 竖排时 `break-all` 会把汉字按字挤成一列，正是要的效果；横排则只断在标点。 */
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: normal;
  text-wrap: pretty;
  /* 框是模型给的边界：装不下由脚本在框内缩字号，不让正文溢到纸上。 */
  overflow: hidden;
}

.is-seal {
  display: grid;
  place-items: center;
  font-family: var(--s-font-display);
  font-size: 0.62em;
  font-weight: 600;
  letter-spacing: 0.04em;
  line-height: 1.05;
  writing-mode: vertical-rl;
  text-orientation: upright;
}

.cap {
  position: absolute;
  right: 0;
  bottom: 0;
  padding: 0.2cqw 0.4cqw;
  color: var(--ppt-muted);
  background: color-mix(in srgb, var(--ppt-bg) 78%, transparent);
  font-size: 1.15cqw;
  text-align: right;
}

.foot {
  position: absolute;
  right: 9cqw;
  bottom: 3cqw;
  left: 9cqw;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 0.8cqw;
  border-top: 1px solid var(--ppt-line);
  color: var(--ppt-muted);
  font-size: 1.15cqw;
  letter-spacing: 0.1cqw;
  pointer-events: none;
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
</style>
