/**
 * 字体对：标题 + 正文字族的组合，后端 `app/ppt/fontpairs.py` 的五对与之同源
 * （契约测试逐 id 对表，两边不许各写各的）。
 *
 * ## 字体从哪来
 *
 * `public/fonts/ppt-fonts.css`（897 条 @font-face，按 unicode-range 分片）注册了三个
 * 中文字族：**Noto Serif SC**（思源宋）、**Noto Sans SC**（思源黑）、**LXGW WenKai**
 * （霞鹜文楷）。分片是按需加载的：浏览器只为页面上真正出现的字符下载对应 woff2，
 * 所以覆盖全字符集也不重——模型生成任意文字、编辑改字，都不会掉回系统字体。
 * PptStudioView / PptDeckLab 挂载时注入这份 css（`utils/pptFonts.ts::ensurePptFonts`）。
 *
 * ## 栈的顺序
 *
 * 自带 webfont 放最前（三平台观感一致），系统字体作兜底（css 没加载到时至少
 * 不错版式）。Windows 与 macOS 同款不同名（SimSun / Songti SC）都要列，
 * 只写前者 macOS 会掉到默认无衬线，标点宽度全变。
 *
 * ## PPTX 的限制
 *
 * PPTX 不能嵌入字体。导出写的是族名（Noto Serif SC 等），放映端没有就替换成
 * 本地宋楷——要精确还原用导出 PDF。
 */

export interface FontPair {
  id: string
  label: string
  /** 一句话：什么时候用它。显示在面板上。 */
  note: string
  display: string
  body: string
}

const SERIF = '"Noto Serif SC"'
const SANS = '"Noto Sans SC"'
const WENKAI = '"LXGW WenKai"'

export const pptFontPairs: FontPair[] = [
  {
    id: 'song-kai',
    label: '宋楷',
    note: '正文宋体、标题文楷。最稳的一套，讲义默认用它。',
    display: `${WENKAI}, KaiTi, "Kaiti SC", STKaiti, ${SERIF}, serif`,
    body: `${SERIF}, SimSun, "Songti SC", serif`,
  },
  {
    id: 'kai-song',
    label: '楷宋',
    note: '通篇文楷。手写感强，适合引经据典、单页金句。',
    display: `${WENKAI}, KaiTi, "Kaiti SC", STKaiti, ${SERIF}, serif`,
    body: `${WENKAI}, ${SERIF}, serif`,
  },
  {
    id: 'hei-song',
    label: '黑宋',
    note: '标题黑体、正文宋体。现代对比，适合汇报与数据页。',
    display: `${SANS}, "Microsoft YaHei", SimHei, "Heiti SC", sans-serif`,
    body: `${SERIF}, SimSun, "Songti SC", serif`,
  },
  {
    id: 'serif-full',
    label: '全宋',
    note: '全宋体（思源宋），字面均匀。打印与小字号投影都清楚。',
    display: `${SERIF}, SimSun, "Songti SC", serif`,
    body: `${SERIF}, SimSun, "Songti SC", serif`,
  },
  {
    id: 'sans-full',
    label: '全黑',
    note: '全黑体（思源黑）。屏幕阅读最清楚，适合阶梯教室与录屏。',
    display: `${SANS}, "Microsoft YaHei", SimHei, "Heiti SC", sans-serif`,
    body: `${SANS}, "Microsoft YaHei", SimHei, "Heiti SC", sans-serif`,
  },
]

export const DEFAULT_FONT_PAIR_ID = 'song-kai'

export const pptFontPairById = (id: string | undefined | null): FontPair =>
  pptFontPairs.find((item) => item.id === id) ?? pptFontPairs[0]

/**
 * 舞台根节点上的字体变量。场景里的 `font: 'display' | 'body'` 读这两个。
 *
 * 用 CSS 变量而不是给每个文字块写 `font-family`：换字对只改根节点一处，
 * 一页二十个文字块不用重渲。
 */
export const pptFontVars = (id: string | undefined | null): Record<string, string> => {
  const pair = pptFontPairById(id)
  return {
    '--s-font-display': pair.display,
    '--s-font-body': pair.body,
  }
}
