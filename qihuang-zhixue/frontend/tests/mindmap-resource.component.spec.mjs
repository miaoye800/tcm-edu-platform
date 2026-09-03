import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/MindmapResourceView.vue', import.meta.url), 'utf8')
assert.match(source, /patchMindmapTemplate\(props\.resourceId, id\)/, 'theme changes persist immediately')
assert.match(source, /content\.value\.mindmap_data = result\.mindmap_data/, 'expand response merges incrementally')
assert.match(source, /const exportSvg/, 'SVG export is available')
assert.match(source, /const exportPng/, 'PNG export is available')
assert.match(source, /props\.usingMock \? await mockExpand/, 'mock fallback covers expansion')
console.log('mindmap resource component contract checks passed')
