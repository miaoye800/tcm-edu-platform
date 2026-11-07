/**
 * 导图资源的「产物能力」契约：换色要存、导出要能出图。
 *
 * 老版本守的是已下线的 MindmapResourceView（单独的资源阅读页）。
 * P1 之后看图和改图都收进了导图工坊，能力本身没消失，只是换了地方，
 * 所以断言跟着搬过来 —— 删掉这组检查等于把「换色没存住」的回归口子放开。
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')

const studio = read('../src/composables/useMindmapStudio.ts')
const api = read('../src/services/mindmapStudioApi.ts')
const capabilityApi = read('../src/services/capabilityApi.ts')
const view = read('../src/views/MindmapStudioView.vue')
const canvas = read('../src/components/mindmap/StudioCanvas.vue')

// —— 换色/换结构立刻落库 ——
assert.match(studio, /presentMindmap\(resourceId\.value, \{ structure: structureId\.value, theme_id: themeId\.value \}\)/, '外观改动走 mindmap.present，结构与主题一起存')
assert.match(studio, /APPEARANCE_DEBOUNCE_MS/, '连点色卡攒一下再发，不刷请求')
assert.match(capabilityApi, /'\/api\/capabilities\/mindmap\.present'/, 'present 是契约里的同步命令')
assert.match(api, /saveMindmapAppearance/, '底层 PUT 接口保留，供不改契约的场景使用')
assert.match(studio, /applyAppearanceFrom/, '打开历史图能复原当初的结构与配色')

// —— 导出：SVG / PNG ——
assert.match(view, /const exportSvg/, 'SVG 导出可用')
assert.match(view, /const exportPng/, 'PNG 导出可用')
assert.match(canvas, /exportSvg|svg/, '画布提供 SVG 数据源')
assert.match(view, /URL\.createObjectURL/, '导出走浏览器下载')

// —— 整树保存：版本号 + 冲突出口 ——
assert.match(studio, /saveMindmapTree\(/, '整树保存走独立接口')
assert.match(studio, /version\.value/, '保存带乐观并发版本号')
assert.match(api, /MindmapSaveError/, '保存错误有独立类型，409 才好分辨')

console.log('mindmap resource component contract checks passed')
