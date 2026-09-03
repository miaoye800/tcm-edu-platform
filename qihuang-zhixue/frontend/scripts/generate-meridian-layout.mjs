#!/usr/bin/env node
/* Generate stable, manually editable coordinates from GET /knowledge-tree output. */
import { readFileSync, writeFileSync } from 'node:fs'
const input = process.argv[2]
const output = process.argv[3] || 'src/config/meridian-layout.json'
if (!input) throw new Error('Usage: node scripts/generate-meridian-layout.mjs knowledge-tree.json [output.json]')
const source = JSON.parse(readFileSync(input, 'utf8'))
const domains = (source.domains || []).filter((node) => node.level === 'domain').sort((a, b) => (a.sort_hint || 0) - (b.sort_hint || 0))
const columns = Math.max(domains.length, 1)
const directions = domains.map((node, index) => {
  const x = Math.round(110 + ((index + .5) * 940) / columns)
  return { id: node.id, label: node.name, segment: node.description || '学习分段', path: `M ${x} 580 C ${x - 30} 430, ${x + 35} 260, ${x} 54`, x, y: 360 }
})
const layout = { viewBox: '0 0 1160 640', directions, nodes: directions.map((item) => ({ id: item.id, x: item.x, y: 505, segment: item.segment })) }
writeFileSync(output, `${JSON.stringify(layout, null, 2)}\n`)
console.log(`Wrote ${output}. Adjust nodes[].x/y and directions[].path manually, then commit the JSON.`)
