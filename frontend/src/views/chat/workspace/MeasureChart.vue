<script setup lang="ts">
import { computed, ref, watchEffect } from 'vue'
import { Chart, type G2Spec } from '@antv/g2'
import { useI18n } from 'vue-i18n'
import type { analysisPeriods } from './resultFormat'
import { resultNumber } from './resultFormat'
import { horizontalChartLayout } from './chartLabelLayout'
import { downloadChartImage } from './chartImage'

const props = defineProps<{
  values: { label: string; identity?: string; value: number | null; current?: number | null; baseline?: number | null }[]
  comparison?: { current: string; baseline: string }
  signed?: boolean
  selectable?: boolean
  maximum?: number
  percent?: boolean
  line?: boolean
  horizontal?: boolean
  funnel?: boolean
  label: string
  wrapCategoryLabels?: boolean
}>()
const emit = defineEmits<{ select: [step: number, converted: boolean, identity?: string] }>()
const container = ref<HTMLElement>()
const rendered = ref(false)
const { locale, t } = useI18n()
const availableWidth = ref(0)
const layout = computed(() => props.horizontal && availableWidth.value
  ? horizontalChartLayout(props.values.map(item => item.label), availableWidth.value, !!props.comparison)
  : undefined)

async function downloadImage(
  name: string,
  periods?: ReturnType<typeof analysisPeriods>,
  caption?: { title: string; lines: string[] }
) {
  const target = container.value
  if (!rendered.value || !target) return false
  const { values, comparison } = props
  const language = locale.value
  const isCurrent = () => target.isConnected && props.values === values &&
    props.comparison === comparison && locale.value === language
  const lines = [
    periods?.current && t('cbi.current_date') + ' ' + periods.current,
    periods?.baseline && t('cbi.baseline_date') + ' ' + periods.baseline,
    ...(caption?.lines ?? []),
  ].filter((line): line is string => Boolean(line))
  if (!caption || !props.horizontal) {
    const source = target.querySelector('canvas')
    return !!source && downloadChartImage(source, name, { lines }, isCurrent)
  }
  await document.fonts.ready
  if (!isCurrent()) return false
  const width = 1120
  const printLayout = horizontalChartLayout(values.map(item => item.label), width, !!comparison)
  const host = document.createElement('div')
  host.setAttribute('aria-hidden', 'true')
  Object.assign(host.style, { position: 'fixed', left: '-12000px', top: '0', width: width + 'px' })
  document.body.appendChild(host)
  const chart = new Chart({ container: host, width, height: printLayout.height })
  try {
    await chart.options(chartOptions(host, printLayout)).render()
    const source = host.querySelector('canvas')
    return !!source && await downloadChartImage(source, name, { title: caption.title, lines }, isCurrent)
  } finally {
    chart.destroy()
    host.remove()
  }
}

defineExpose({ rendered, downloadImage })

function chartOptions(target: HTMLElement, horizontal?: ReturnType<typeof horizontalChartLayout>): G2Spec {
  const series = props.values.map((item, index) => ({
    ...item, index, category: item.identity ?? item.label, value: item.value == null ? null : Number(item.value),
  }))
  const axisLabels = new Map(series.map((item, index) => [item.category, horizontal?.labels[index] ?? item.label]))
  const comparison = props.comparison
  const plotted = comparison
    ? series.flatMap(item => [
      { ...item, period: comparison.baseline, value: item.baseline ?? null },
      { ...item, period: comparison.current, value: item.current ?? null },
    ])
    : series.map(item => ({ ...item, sign: item.value != null && item.value < 0 ? 'decrease' : 'increase' }))
  const paddingLeft = horizontal?.paddingLeft ?? 36
  const paddingRight = 8
  const categoryMargin = 16
  const labelFontSize = 13
  const axis = {
    x: {
      title: false as const, tick: false, labelFontSize, labelFill: '#535b63', labelOpacity: 1,
      ...(horizontal && {
        size: paddingLeft,
        labelAutoHide: false,
        labelAutoEllipsis: false,
        labelAutoRotate: false,
        labelFontFamily: horizontal.labelFont,
        labelLineHeight: horizontal.labelLineHeight,
        labelFormatter: (key: string) => axisLabels.get(key) ?? key,
      }),
      ...(!props.horizontal && !props.line && props.wrapCategoryLabels && {
        size: 52,
        labelAutoHide: false,
        labelAutoWrap: {
          maxLines: 3,
          recoverWhenFailed: false,
          wordWrapWidth: () => {
            const plotWidth = target.clientWidth - paddingLeft - paddingRight - 2 * categoryMargin
            return Math.max(labelFontSize, plotWidth / series.length - 4)
          },
        },
      }),
    },
    y: { title: false as const, labelFill: '#535b63', labelOpacity: 1,
      labelFormatter: (label: string) => {
        const value = Number(label)
        return (Number.isFinite(value) ? resultNumber(value, locale.value) : label) + (props.percent ? '%' : '')
      } },
  }
  const scale = { y: props.maximum ? { domain: [0, props.maximum] } : { nice: true, zero: true } }
  const background = props.funnel && props.maximum
    ? [{
        type: 'interval' as const,
        animate: false as const,
        data: series.map(item => ({ label: item.label, ceiling: props.maximum })),
        encode: { x: 'label', y: 'ceiling' },
        style: { fill: '#f0f4f2', radiusTopLeft: 6, radiusTopRight: 6 },
        tooltip: false as const,
        axis,
        scale,
      }, {
        type: 'interval' as const,
        animate: false as const,
        data: series.slice(1).map((item, index) => ({ ...item, ceiling: series[index].value })),
        encode: { x: 'label', y: 'ceiling' },
        style: { fill: '#c9ded3', radiusTopLeft: 6, radiusTopRight: 6, cursor: 'pointer' },
        tooltip: false as const,
        axis,
        scale,
      }]
    : []
  return {
    type: 'view', animate: false, paddingTop: horizontal?.paddingTop ?? 16, paddingLeft, paddingRight,
    paddingBottom: horizontal?.paddingBottom ?? (props.wrapCategoryLabels ? 'auto' : 5),
    ...(props.wrapCategoryLabels && { marginLeft: categoryMargin, marginRight: categoryMargin }),
    ...(props.horizontal && { coordinate: { transform: [{ type: 'transpose' }] } }),
    children: [...background, {
      type: props.line ? 'line' : 'interval',
      animate: false,
      // A missing interval has no mark. Keep its category and series in the scale domains.
      data: horizontal ? plotted.filter(item => item.value != null) : plotted,
      encode: { x: horizontal ? 'category' : 'label', y: 'value', ...(comparison ? { color: 'period' } : props.signed ? { color: 'sign' } : {}) },
      ...(comparison && { transform: [{ type: 'dodgeX' as const }] }),
      ...(props.signed && !comparison && { legend: false as const }),
      style: props.line ? { stroke: '#25826b', lineWidth: 2, connect: false } :
        { ...(!comparison && !props.signed && { fill: '#25826b' }), radiusTopLeft: 4, radiusTopRight: 4,
          cursor: props.funnel || props.selectable ? 'pointer' : 'default' },
      axis, scale: {
        ...scale,
        ...(horizontal && { x: { domain: [...axisLabels.keys()] } }),
        ...(comparison ? {
          color: { domain: [comparison.baseline, comparison.current], range: ['#75909a', '#25826b'] },
          series: { domain: [comparison.baseline, comparison.current] },
        } : {}),
        ...(props.signed && !comparison ? { color: { domain: ['increase', 'decrease'], range: ['#25826b', '#b05b50'] } } : {}),
      },
      tooltip: { title: 'label', items: [{ field: 'value', valueFormatter: (value: number) =>
        resultNumber(value, locale.value, props.signed) + (props.percent ? '%' : '') }] },
    }, ...(props.line ? [{
      type: 'point' as const,
      data: series,
      encode: { x: 'label', y: 'value' },
      style: { fill: '#25826b', stroke: '#25826b', r: 3 },
      tooltip: false as const,
      axis, scale,
    }] : [])],
  }
}

watchEffect(cleanup => {
  const target = container.value
  if (!target || !props.horizontal) return
  availableWidth.value = target.clientWidth
  const observer = new ResizeObserver(() => { availableWidth.value = target.clientWidth })
  observer.observe(target)
  cleanup(() => observer.disconnect())
}, { flush: 'post' })

watchEffect((cleanup) => {
  rendered.value = false
  const target = container.value
  const horizontal = layout.value
  if (!target || !props.values.length || (props.horizontal && !horizontal)) return
  const chart = new Chart({ container: target, width: target.clientWidth, height: horizontal?.height ?? target.clientHeight })
  chart.options(chartOptions(target, horizontal))
  if (props.funnel || props.selectable) {
    chart.on('interval:click', (event: { data?: { data?: { index?: number; ceiling?: number; identity?: string } } }) => {
      const datum = event.data?.data
      if (datum?.index !== undefined && (datum.ceiling === undefined || datum.index > 0)) {
        emit('select', datum.index, datum.ceiling === undefined, datum.identity)
      }
    })
  }
  let available = false
  let stopped = false
  let lastWidth = target.clientWidth
  let lastHeight = target.clientHeight
  const observer = new ResizeObserver(() => {
    const width = target.clientWidth
    const height = target.clientHeight
    if (!available || !width || !height || (width === lastWidth && height === lastHeight)) return
    lastWidth = width
    lastHeight = height
    rendered.value = false
    void chart.changeSize(width, height).then(() => {
      if (!stopped && lastWidth === width && lastHeight === height) rendered.value = true
    }).catch(error => { if (!stopped) console.error(error) })
  })
  void chart.render().then(() => {
    if (stopped) return
    available = true
    if (!horizontal) observer.observe(target)
    rendered.value = true
  }).catch(error => { if (!stopped) console.error(error) })
  cleanup(() => {
    stopped = true
    observer.disconnect()
    chart.destroy()
  })
}, { flush: 'post' })
</script>

<template>
  <div ref="container" class="measure-chart" :class="{ 'horizontal-chart': horizontal }"
    :style="layout && { height: layout.height + 'px' }"
    role="img" :aria-label="label" :data-rendered="rendered"></div>
</template>

<style scoped>
.measure-chart { height: 238px; min-width: 0; }
</style>
