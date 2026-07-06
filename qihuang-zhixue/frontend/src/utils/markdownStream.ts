export type MarkdownFlush = {
  stable: string
  pending: string
}

export class MarkdownStreamBuffer {
  private stableText = ''
  private pendingText = ''

  push(chunk: string): MarkdownFlush {
    this.pendingText += chunk
    const splitIndex = this.findSafeSplitIndex(this.pendingText)
    if (splitIndex > 0) {
      this.stableText += this.pendingText.slice(0, splitIndex)
      this.pendingText = this.pendingText.slice(splitIndex)
    }
    return this.snapshot()
  }

  flush(): MarkdownFlush {
    this.stableText += this.pendingText
    this.pendingText = ''
    return this.snapshot()
  }

  reset() {
    this.stableText = ''
    this.pendingText = ''
  }

  snapshot(): MarkdownFlush {
    return {
      stable: this.stableText,
      pending: this.pendingText,
    }
  }

  private findSafeSplitIndex(text: string): number {
    let safe = 0
    const lines = text.split(/(\n)/)
    let cursor = 0
    let fenceOpen = this.isFenceOpen(this.stableText)

    for (let index = 0; index < lines.length; index += 2) {
      const line = lines[index] ?? ''
      const newline = lines[index + 1] ?? ''
      const segment = line + newline
      cursor += segment.length

      if (line.trimStart().startsWith('```')) {
        fenceOpen = !fenceOpen
      }
      if (fenceOpen) continue

      const trimmed = line.trim()
      const nextLine = lines[index + 2] ?? ''
      if (!trimmed && newline) {
        safe = cursor
        continue
      }
      if (/^#{1,6}\s.+/.test(trimmed) && newline) {
        safe = cursor
        continue
      }
      if (/^[-*+]\s.+/.test(trimmed) && newline && !/^[-*+]\s/.test(nextLine.trim())) {
        safe = cursor
        continue
      }
      if (/^\d+\.\s.+/.test(trimmed) && newline && !/^\d+\.\s/.test(nextLine.trim())) {
        safe = cursor
      }
    }

    return this.endsWithDanglingMarkdown(text.slice(0, safe)) ? 0 : safe
  }

  private endsWithDanglingMarkdown(text: string): boolean {
    const trimmed = text.trimEnd()
    return trimmed.endsWith('*') || trimmed.endsWith('**') || trimmed.endsWith('```')
  }

  private isFenceOpen(text: string): boolean {
    return text
      .split('\n')
      .filter((line) => line.trimStart().startsWith('```')).length % 2 === 1
  }
}
