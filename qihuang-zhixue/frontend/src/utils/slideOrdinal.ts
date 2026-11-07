/**
 * 章节页的水印号码。
 *
 * 原先直接把「一、」的章号放大到 26cqw 当装饰，结果第一章是单个「一」——
 * 放大后就是一横加个三角，看着像版式坏了。改成两位数字：无论第几章，
 * 笔画量都稳定，也和导览页、页脚上的 01 / 02 是同一套数字语言。
 */

const NUMERALS = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']

/** 「二、用量与禁忌」→「02」；不是章节标题就返回空串。 */
export const sectionOrdinal = (title: string): string => {
  const match = title.match(/^([一二三四五六七八九十]+)、/)
  if (!match) return ''
  const index = NUMERALS.indexOf(match[1])
  return index < 0 ? '' : String(index + 1).padStart(2, '0')
}
