<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AnalysisState, Decomposition, DecompositionRow, ComparisonView } from '@/api/analysis'
import MeasureChart from './MeasureChart.vue'
import { analysisPeriods, resultNumber } from './resultFormat'

const props = defineProps<{
  result: Decomposition
  scope?: AnalysisState['scope']
  status?: AnalysisState['task_status']
  showData?: boolean
  selectable?: boolean
}>()
const emit = defineEmits<{ select: [row: DecompositionRow] }>()
const mode = defineModel<ComparisonView>('mode', { default: 'delta' })
const { t, locale } = useI18n()
const chart = ref<InstanceType<typeof MeasureChart>>()

const labelFor = (row: DecompositionRow) => row.label || t('resultExplorer.unclassified')
const unit = computed(() => (props.result.unit === 'CNY' ? t('cbi.yuan') : props.result.unit))
const format = (value: number | string | null, signed = false) =>
  value == null ? t('cbi.reason.missing_data') : resultNumber(Number(value), locale.value, signed, props.result.precision)
const periodState = (state?: string) =>
  state && !['observed', 'verified', 'present'].includes(state)
    ? t('resultExplorer.observation.' + state, state)
    : ''
const rowIdentity = (row: DecompositionRow) => JSON.stringify([row.key, !!row.other])
const rowsByIdentity = computed(() => new Map(props.result.rows.map(row => [rowIdentity(row), row])))

function selectRow(row?: DecompositionRow) {
  if (props.selectable && row?.key != null && !row.other) emit('select', row)
}

function selectPoint(_index: number, _converted: boolean, identity?: string) {
  if (identity !== undefined) selectRow(rowsByIdentity.value.get(identity))
}

const duplicates = computed(() => {
  const counts = new Map<string, number>()
  for (const row of props.result.rows) {
    const label = labelFor(row)
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  return counts
})
const chartLabel = (row: DecompositionRow) =>
  duplicates.value.get(labelFor(row)) === 1
    ? labelFor(row)
    : labelFor(row) + ' · ' + (row.key ?? t('resultExplorer.unclassified'))
const values = computed(() =>
  props.result.rows.map((row) => ({
    label: chartLabel(row),
    identity: rowIdentity(row),
    value: row.delta == null ? null : Number(row.delta),
    baseline: row.baseline == null ? null : Number(row.baseline),
    current: row.current == null ? null : Number(row.current),
  }))
)
const hasDelta = computed(() => values.value.some((row) => row.value != null))
const hasPeriods = computed(() =>
  values.value.some((row) => row.current != null || row.baseline != null)
)
watch(
  hasDelta,
  (available) => {
    if (!available) mode.value = 'periods'
  },
  { immediate: true }
)
const compareChart = computed(() => mode.value === 'periods')
const periods = computed(() => ({
  current: t('cbi.current_date'),
  baseline: t('cbi.baseline_date'),
}))
const title = computed(() => [
  props.result.label,
  props.result.dimension_label,
  t(compareChart.value ? 'resultExplorer.periodChart' : 'resultExplorer.deltaChart'),
].join(' · '))
const truncation = computed(() => props.result.truncated
  ? props.result.omitted == null ? t('resultExplorer.sourceTruncated')
    : t('resultExplorer.truncated', { shown: props.result.rows.length, omitted: props.result.omitted })
  : '')
const reconciliation = computed(() => props.result.reconciled ? '' : t(
  'resultExplorer.reconciliation.' + (props.result.reason || 'reconciliation_failed'),
  t('resultExplorer.unreconciled')
))
const observations = computed(() => {
  const notes = new Map<string, string[]>()
  for (const row of props.result.rows) {
    for (const period of ['current', 'baseline'] as const) {
      const state = periodState(row[`${period}_status`]) ||
        (row[period] == null ? t('cbi.reason.missing_data') : '')
      if (!state) continue
      const target = chartLabel(row) + ' (' + periods.value[period] + ')'
      const entries = notes.get(state) ?? []
      entries.push(target)
      notes.set(state, entries)
    }
  }
  return Array.from(notes, ([state, entries]) => state + ' · ' + entries.join(' · '))
})

function downloadImage(name: string) {
  const { scope, result, status } = props
  const dateRanges = scope && analysisPeriods(scope)
  const totals = (row: Decomposition['total']) => [
    periods.value.baseline + ' ' + format(row.baseline),
    periods.value.current + ' ' + format(row.current),
    t('cbi.output.delta') + ' ' + format(row.delta, true),
  ].join(' · ')
  return chart.value?.downloadImage(name, dateRanges, {
    title: title.value,
    lines: [
      [unit.value, scope?.definition_label, scope?.store && t('cbi.store') + ' ' + scope.store,
        ...(scope?.filter_labels ?? []).map(filter => filter.label + ' ' + filter.value)].filter(Boolean).join(' · '),
      t('resultExplorer.total') + '  ' + totals(result.total),
      result.other ? t('resultExplorer.other') + '  ' + totals(result.other) : '',
      status && status !== 'completed' ? t('cbi.status.' + status) : '',
      truncation.value,
      reconciliation.value,
      ...observations.value,
    ].filter(Boolean),
  })
}

defineExpose({ chart, downloadImage })
</script>

<template>
  <section class="comparison-result" :data-reconciled="result.reconciled">
    <div class="comparison-heading">
      <p>
        {{ result.dimension_label }} <span v-if="unit">· {{ unit }}</span>
      </p>
      <div
        v-if="!showData && hasDelta && hasPeriods"
        class="comparison-modes"
        role="group"
        :aria-label="t('resultExplorer.chart')"
      >
        <button
          type="button"
          data-testid="comparison-delta"
          :aria-pressed="!compareChart"
          @click="mode = 'delta'"
        >
          {{ t('resultExplorer.deltaChart') }}
        </button>
        <button
          type="button"
          data-testid="comparison-periods"
          :aria-pressed="compareChart"
          @click="mode = 'periods'"
        >
          {{ t('resultExplorer.periodChart') }}
        </button>
      </div>
    </div>
    <div v-if="showData" class="comparison-table-scroll">
      <table class="comparison-table">
        <thead>
          <tr>
            <th scope="col">{{ result.dimension_label }}</th>
            <th scope="col">{{ periods.baseline }}</th>
            <th scope="col">{{ periods.current }}</th>
            <th scope="col">{{ t('cbi.output.delta') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in result.rows" :key="rowIdentity(row)">
            <th scope="row">
              <button
                v-if="selectable && row.key != null && !row.other"
                type="button"
                class="comparison-point"
                data-testid="comparison-select-group"
                @click="selectRow(row)"
              >
                {{ chartLabel(row) }}
              </button>
              <span v-else>{{ chartLabel(row) }}</span>
            </th>
            <td>
              {{ format(row.baseline) }}
              <small v-if="periodState(row.baseline_status)">{{
                periodState(row.baseline_status)
              }}</small>
            </td>
            <td>
              {{ format(row.current) }}
              <small v-if="periodState(row.current_status)">{{
                periodState(row.current_status)
              }}</small>
            </td>
            <td>{{ format(row.delta, true) }}</td>
          </tr>
          <tr v-if="result.other">
            <th scope="row">{{ t('resultExplorer.other') }}</th>
            <td>{{ format(result.other.baseline) }}</td>
            <td>{{ format(result.other.current) }}</td>
            <td>{{ format(result.other.delta, true) }}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">{{ t('resultExplorer.total') }}</th>
            <td>{{ format(result.total.baseline) }}</td>
            <td>{{ format(result.total.current) }}</td>
            <td>{{ format(result.total.delta, true) }}</td>
          </tr>
        </tfoot>
      </table>
    </div>
    <MeasureChart
      v-else-if="hasPeriods || hasDelta"
      ref="chart"
      :values="values"
      horizontal
      :comparison="compareChart ? periods : undefined"
      :signed="!compareChart"
      :label="title"
      :selectable="selectable"
      @select="selectPoint"
    />
    <p v-else>{{ t('cbi.reason.missing_data') }}</p>
    <template v-if="!showData">
      <p v-for="note in observations" :key="note">{{ note }}</p>
    </template>
    <p v-if="truncation">{{ truncation }}</p>
    <p v-if="reconciliation" role="status">{{ reconciliation }}</p>
  </section>
</template>

<style scoped>
.comparison-result {
  min-width: 0;
  display: grid;
  gap: 8px;
}
.comparison-result p {
  margin: 0;
  color: #46514d;
  font-size: 13px;
}
.comparison-heading,
.comparison-modes {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.comparison-heading {
  justify-content: space-between;
}
.comparison-modes button {
  border: 1px solid #dbe3de;
  border-radius: 5px;
  color: #42534c;
  background: #fff;
  padding: 4px 9px;
  min-height: 30px;
  font: inherit;
  cursor: pointer;
}
.comparison-modes button[aria-pressed='true'] {
  background: #eaf4ee;
  border-color: #b5d2c3;
  color: #17634d;
}
.comparison-table-scroll {
  overflow: auto;
}
.comparison-table {
  width: 100%;
  min-width: 300px;
  border-collapse: collapse;
  text-align: left;
}
.comparison-table th,
.comparison-table td {
  padding: 8px;
  border-bottom: 1px solid #e4ebe7;
  overflow-wrap: anywhere;
  font-weight: 400;
}
.comparison-table th[scope='col'],
.comparison-table tfoot th {
  font-weight: 600;
}
.comparison-table td,
.comparison-table th:not(:first-child) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.comparison-table small {
  display: block;
  color: #535b63;
}
.comparison-point {
  border: 0;
  background: transparent;
  color: #17634d;
  text-align: left;
  cursor: pointer;
  padding: 2px;
  font: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.comparison-point:focus-visible,
.comparison-modes button:focus-visible {
  outline: 2px solid #25826b;
  outline-offset: 2px;
}
</style>
