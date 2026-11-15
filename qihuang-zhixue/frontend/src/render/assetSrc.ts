/**
 * 素材地址解析。
 *
 * 一份稿子里的图有两条来源，但渲染层不该知道差别：
 *
 * - **本地库**：`/assets/pictorial/{id}.webp`，随仓库走，`registry.json` 登记。
 * - **远程源**：一条已合成好的 wsrv 代理 URL（Cleveland / Heritage / pngimg …），
 *   素材本身不落盘，稿子里只钉地址。
 *
 * 所以统一成「先查稿件里给出的地址表，查不到再按本地库的命名规则拼」。这个函数是
 * 预览、胶片条、导出三条路径的唯一入口——三处各写一份 `url || file` 迟早会漂。
 */

import type { AssetRef } from '../types/ppt'

/** 本地素材目录。命名规则与 `tools/assets/local.py` 的产出保持一致。 */
export const LOCAL_ASSET_DIR = '/assets/pictorial'

/** 稿件里的 `asset_refs` → `asset_id` 到地址的映射。同一 id 出现多次只留第一个。 */
export const assetMapOf = (refs: AssetRef[] | undefined): Record<string, string> => {
  const map: Record<string, string> = {}
  for (const ref of refs ?? []) {
    if (!ref.asset_id) continue
    const src = ref.url || ref.file
    if (src && !map[ref.asset_id]) {
      // registry 的 file 是 `assets/...`（无前导斜杠）——相对路径会跟着
      // 当前路由走，嵌套路由下必错。本地路径一律挂回站点根。
      map[ref.asset_id] = src.startsWith('assets/') ? `/${src}` : src
    }
  }
  return map
}

/**
 * 一个素材 id 的显示地址。
 *
 * `map` 命中就返回远程地址；否则按本地库规则拼。空 id 返回空串——调用方据此
 * 决定「不画这个图位」而不是画一个裂图标。
 */
export const assetSrc = (id: string, map?: Record<string, string>): string => {
  if (!id) return ''
  const pinned = map?.[id]
  if (pinned) return pinned
  return `${LOCAL_ASSET_DIR}/${id}.webp`
}
