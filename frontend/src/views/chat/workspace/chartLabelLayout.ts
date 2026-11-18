/// <reference lib="es2022.intl" />

const labelFontSize = 13
const labelLineHeight = 18
const labelFont = 'system-ui, sans-serif'
const words = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, { granularity: 'word' }) : undefined
const graphemes = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter() : undefined

export function wrapChartText(text: string, maxWidth: number, measure: (text: string) => number) {
  return text.split(/\r?\n/).flatMap(paragraph => {
    const lines: string[] = []
    let line = ''
    const segments = words ? Array.from(words.segment(paragraph), item => item.segment) : paragraph.split(/(\s+)/)
    for (const segment of segments) {
      if (line && measure(line + segment) > maxWidth && segment.trim()) {
        lines.push(line.trimEnd())
        line = ''
      }
      if (measure(segment) <= maxWidth) {
        line += line ? segment : segment.trimStart()
        continue
      }
      // Unicode grapheme boundaries keep marks and emoji attached to their characters.
      const characters = graphemes ? Array.from(graphemes.segment(segment), item => item.segment) : Array.from(segment)
      for (const character of characters) {
        if (line && measure(line + character) > maxWidth) {
          lines.push(line.trimEnd())
          line = ''
        }
        line += character
      }
    }
    lines.push(line.trimEnd())
    return lines
  })
}

export function horizontalChartLayout(labels: string[], width: number, paired: boolean) {
  const context = document.createElement('canvas').getContext('2d')!
  context.font = labelFontSize + 'px ' + labelFont
  const measure = (text: string) => context.measureText(text).width
  const labelWidth = Math.ceil(Math.min(width * 0.43, 330, Math.max(76, ...labels.map(measure))))
  const lines = labels.map(label => wrapChartText(label, labelWidth, measure))
  const lineCount = Math.max(1, ...lines.map(label => label.length))
  const rowHeight = Math.max(paired ? 48 : 35, lineCount * labelLineHeight + 14)
  const paddingTop = paired ? 40 : 16
  const paddingBottom = 34
  return {
    labels: lines.map(label => label.join('\n')),
    paddingLeft: labelWidth + 18,
    paddingTop,
    paddingBottom,
    height: paddingTop + labels.length * rowHeight + paddingBottom,
    labelFont,
    labelFontSize,
    labelLineHeight,
  }
}
