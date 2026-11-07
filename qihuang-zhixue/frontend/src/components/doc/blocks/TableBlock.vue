<template>
  <div class="table-block">
    <table>
      <thead>
        <tr>
          <th v-for="(column, columnIndex) in columns" :key="`h-${columnIndex}`">
            <EditableText
              :model-value="column"
              :editable="editable"
              placeholder="列名"
              @update:model-value="value => updateCell(-1, columnIndex, value)"
            />
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, rowIndex) in rows" :key="`r-${rowIndex}`">
          <td v-for="(cell, columnIndex) in row" :key="`c-${rowIndex}-${columnIndex}`">
            <EditableText
              :model-value="cell"
              :editable="editable"
              placeholder=""
              @update:model-value="value => updateCell(rowIndex, columnIndex, value)"
            />
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="editable" class="table-tools">
      <button type="button" @mousedown.prevent="addRow">+ 行</button>
      <button type="button" @mousedown.prevent="addColumn">+ 列</button>
      <button v-if="rows.length > 1" type="button" @mousedown.prevent="removeRow">− 行</button>
      <button v-if="columns.length > 1" type="button" @mousedown.prevent="removeColumn">− 列</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import EditableText from '../EditableText.vue'
import type { DocBlock } from '../../../types/doc'

const props = defineProps<{ block: DocBlock; editable: boolean }>()
const emit = defineEmits<{ (e: 'update', block: DocBlock): void }>()

const columns = computed<string[]>(() => (props.block.props?.columns as string[]) || [])
const rows = computed<string[][]>(() => (props.block.props?.rows as string[][]) || [])

const commit = (nextColumns: string[], nextRows: string[][]) => {
  emit('update', { ...props.block, props: { ...(props.block.props || {}), columns: nextColumns, rows: nextRows, header: true } })
}

/** 单元格编辑只改 props，不回写 text：表格的真相在 columns/rows 里 */
const updateCell = (rowIndex: number, columnIndex: number, value: string) => {
  const nextColumns = columns.value.map((column, index) => (index === columnIndex && rowIndex < 0 ? value : column))
  const nextRows = rows.value.map((row, rIndex) =>
    rIndex === rowIndex ? row.map((cell, cIndex) => (cIndex === columnIndex ? value : cell)) : [...row],
  )
  commit(nextColumns, nextRows)
}

const addRow = () => commit([...columns.value], [...rows.value, columns.value.map(() => '')])
const removeRow = () => commit([...columns.value], rows.value.slice(0, -1))
const addColumn = () => commit([...columns.value, '新列'], rows.value.map(row => [...row, '']))
const removeColumn = () => commit(columns.value.slice(0, -1), rows.value.map(row => row.slice(0, -1)))
</script>

<style scoped lang="scss">
.table-block {
  margin: 8px 0;
}

table {
  width: 100%;
  border-collapse: collapse;
  border: 1px solid var(--qh-line-strong);
  table-layout: fixed;
}

th,
td {
  padding: 6px 9px;
  border: 1px solid var(--qh-line-strong);
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}

th {
  background: color-mix(in srgb, var(--qh-fg) 4%, transparent);
}

/* 以表格观感为准，单元格文本不再跟随段落的 1.75 行高 */
th :deep(.doc-editable),
td :deep(.doc-editable) {
  font-size: 14px;
  font-weight: inherit;
  line-height: 1.6;
}

th :deep(.doc-editable) {
  font-weight: 600;
}

.table-tools {
  display: flex;
  gap: 4px;
  margin-top: 6px;
  opacity: 0;
  transition: opacity 120ms ease;

  button {
    padding: 3px 8px;
    border: 1px solid var(--qh-line);
    border-radius: 4px;
    color: var(--qh-fg-muted);
    background: transparent;
    font-size: 12px;
    cursor: pointer;

    &:hover {
      border-color: var(--qh-line-strong);
      color: var(--qh-fg);
    }
  }
}

.table-block:hover .table-tools {
  opacity: 1;
}
</style>
