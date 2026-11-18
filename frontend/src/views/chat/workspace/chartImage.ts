import { wrapChartText } from './chartLabelLayout'

export async function downloadChartImage(
  source: HTMLCanvasElement,
  name: string,
  caption: { title?: string; lines: string[] },
  isCurrent: () => boolean
) {
  if (!source.width || !source.height) return false
  const image = document.createElement('canvas')
  const context = image.getContext('2d')
  if (!context) return false
  const ratio = source.width / (source.clientWidth || source.width)
  const inset = Math.round((caption.title ? 24 : 10) * ratio)
  const bodyFont = `${Math.round((caption.title ? 15 : 12) * ratio)}px system-ui, sans-serif`
  const titleFont = `650 ${Math.round(24 * ratio)}px system-ui, sans-serif`
  const lines = [
    ...(caption.title ? [{ text: caption.title, font: titleFont, height: 32 * ratio }] : []),
    ...caption.lines.map(text => ({ text, font: bodyFont, height: (caption.title ? 23 : 18) * ratio })),
  ].flatMap(({ text, font, height }) => {
    context.font = font
    return wrapChartText(text, source.width - inset * 2, value => context.measureText(value).width)
      .map(line => ({ line, font, height }))
  })
  const captionHeight = lines.length
    ? Math.ceil(inset * 2 + lines.reduce((sum, row) => sum + row.height, 0))
    : 0
  image.width = source.width
  image.height = source.height + captionHeight
  context.fillStyle = '#fff'
  context.fillRect(0, 0, image.width, image.height)
  context.fillStyle = '#202b28'
  context.textBaseline = 'top'
  let top = inset
  for (const { line, font, height } of lines) {
    context.font = font
    context.fillText(line, inset, top)
    top += height
  }
  context.drawImage(source, 0, captionHeight)
  const blob = await new Promise<Blob | null>(resolve => image.toBlob(resolve, 'image/png'))
  if (!blob || !isCurrent()) return false
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name.replace(/[\\/:*?"<>|]/g, '_') + '.png'
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  return true
}
