/**
 * simple-mind-map 的插件目录没有类型声明。
 *
 * 包根 `index.js` 只带核心，Drag 等能力在 `src/plugins/` 下按需注册；
 * 这里只给「默认导出一个插件类」这一条最小声明，避免整包降级成 any。
 */

declare module 'simple-mind-map/src/plugins/Drag.js' {
  const Drag: unknown
  export default Drag
}
