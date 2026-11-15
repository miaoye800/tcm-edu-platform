/**
 * 幻灯渲染的 webfont 装载。
 *
 * `public/fonts/ppt-fonts.css` 把思源宋 / 思源黑 / 霞鹜文楷按 unicode-range 分成
 * 897 片 @font-face——浏览器只下载页面上真正出现的字符所在的片，因此全字符集
 * 覆盖也不重。它不属于全局样式（story 模块用自己的静态子集），只在渲染幻灯的
 * 视图挂载时按需注入一次。
 *
 * idempotent：重复调用只挂一个 link，热更新与路由往返都不会堆积。
 */

const LINK_ID = 'qh-ppt-fonts'

export const ensurePptFonts = (): void => {
  if (document.getElementById(LINK_ID)) return
  const link = document.createElement('link')
  link.id = LINK_ID
  link.rel = 'stylesheet'
  link.href = '/fonts/ppt-fonts.css'
  document.head.appendChild(link)
}
