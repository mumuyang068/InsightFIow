<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AnalysisState, ComparisonView, SavedFunnel } from '@/api/analysis'
import ComparisonResult from '@/views/chat/workspace/ComparisonResult.vue'
import MeasureChart from '@/views/chat/workspace/MeasureChart.vue'
import ResultSeriesTable from '@/views/chat/workspace/ResultSeriesTable.vue'
import { analysisPeriods, resultNumber } from '@/views/chat/workspace/resultFormat'

const props = defineProps<{ analysis?: AnalysisState; funnel?: SavedFunnel; title: string; wrapCategoryLabels?: boolean; showData?: boolean }>()
const { t, locale } = useI18n()
const measureChart = ref<InstanceType<typeof MeasureChart>>()
const comparison = ref<InstanceType<typeof ComparisonResult>>()
const comparisonMode = defineModel<ComparisonView>('comparisonMode', { default: 'delta' })
const chart = computed(() => props.analysis?.presentation.decomposition ? comparison.value?.chart : measureChart.value)
const decomposition = computed(() => props.analysis?.presentation.decomposition)
const stepKeys = ['view', 'cart', 'pay']
const metrics = computed(() => props.analysis?.outputs.map(output => ({
  id: output.id,
  label: output.label ?? t('cbi.output.' + output.id),
  value: output.value == null
    ? t(output.status === 'verified' ? 'cbi.verified' : 'cbi.reason.' + (output.reason ?? 'missing_data'))
    : resultNumber(output.value, locale.value, /(?:^|_)(?:delta|rate)$/.test(output.id), output.precision),
  unit: output.value == null ? undefined : output.unit === 'CNY' ? t('cbi.yuan') : output.unit,
})) ?? [])
const shape = computed(() => props.analysis?.presentation.shape)
const fixedRows = computed(() => props.analysis?.presentation.rows)
const series = computed(() => props.funnel
  ? props.funnel.each_of_entry_pct.flatMap((value, i) =>
      value == null ? [] : [{ label: t('methods.' + stepKeys[i]), value }])
  : fixedRows.value?.map(row => ({ label: row.label ?? t('cbi.unknown_category'), value: row.value })) ??
    (props.analysis?.outputs ?? []).flatMap(output =>
      ['current', 'baseline', 'target'].includes(output.id) && output.status === 'verified' && output.value != null
        ? [{ label: t('cbi.output.' + output.id), value: output.value }] : []))
const rate = computed(() => props.funnel?.rate_pct == null ? '—' :
  resultNumber(props.funnel.rate_pct, locale.value) + '%')
function downloadImage(name: string, periods?: ReturnType<typeof analysisPeriods>) {
  return decomposition.value ? comparison.value?.downloadImage(name) : measureChart.value?.downloadImage(name, periods)
}
defineExpose({ chart, downloadImage })
</script>

<template>
  <section class="saved-run" :data-saved-kind="funnel ? 'funnel' : 'operating'">
    <dl v-if="funnel" class="saved-metrics">
      <div class="leading">
        <dt>{{ t('cbiWorkspace.overall') }}</dt><dd>{{ rate }} <small>{{ funnel.numerator }}/{{ funnel.denominator }}</small></dd>
      </div>
      <div>
        <dt>{{ t('methods.immature') }}</dt><dd>{{ funnel.immature }}</dd>
      </div>
      <div v-for="(count, i) in funnel.counts" :key="i">
        <dt>{{ t('methods.' + stepKeys[i]) }}</dt><dd>{{ count }}</dd>
      </div>
      <div v-if="funnel.comparison">
        <dt>{{ t('cbiWorkspace.comparison') }} · {{ funnel.comparison.window_hours }} h</dt>
        <dd>{{ resultNumber(funnel.comparison.delta_pp, locale, true) }} {{ t('methods.pp') }}</dd>
      </div>
    </dl>
    <dl v-else-if="metrics.length" class="saved-metrics">
      <div v-for="metric in metrics" :key="metric.id" :class="{ leading: metric.id === 'current' }" :data-saved-output="metric.id">
        <dt>{{ metric.label }}</dt>
        <dd>
          {{ metric.value + (metric.unit === '%' ? '%' : '') }}<small v-if="metric.unit && metric.unit !== '%'" class="metric-unit">{{ ' ' + metric.unit }}</small>
        </dd>
      </div>
    </dl>
    <ResultSeriesTable v-else-if="analysis?.presentation.table && !shape && !decomposition" :table="analysis.presentation.table" :rows="[]" :trend="false" />
    <p v-if="shape && analysis?.presentation.truncated">{{ t(analysis.semantic_plan ? 'cbi.semantic_limit' : 'cbi.series_limit', { count: analysis.presentation.limit }) }}</p>
    <small v-if="shape && analysis?.semantic_plan && analysis.presentation.unit" class="chart-unit">{{ analysis.presentation.unit }}</small>
    <p v-if="shape && !series.some(row => row.value != null)">{{ t('cbi.reason.missing_data') }}</p>
    <ComparisonResult v-if="decomposition" ref="comparison" v-model:mode="comparisonMode" :result="decomposition"
      :scope="analysis?.scope" :status="analysis?.task_status" :show-data="showData" />
    <MeasureChart v-else-if="shape ? series.some(row => row.value != null) : series.length > 1"
      ref="measureChart" :values="series" :maximum="funnel ? 100 : undefined"
      :horizontal="shape === 'category'" :line="shape === 'trend'"
      :percent="!!funnel" :funnel="!!funnel" :label="title" :wrap-category-labels="wrapCategoryLabels" />
  </section>
</template>

<style scoped>
.saved-run { display: flex; flex-direction: column; flex: 1; padding: 4px 4px 8px; min-width: 0; min-height: 0; }
.saved-metrics { display: flex; gap: 6px 18px; flex-wrap: wrap; margin: 0 0 8px; }
.saved-metrics dt { color: #535b63; font-size: 12px; overflow-wrap: anywhere; }
.saved-metrics dd { margin: 2px 0; font-weight: 600; font-size: 15px; font-variant-numeric: tabular-nums; }
.saved-metrics .leading dd { font-size: 23px; line-height: 1.2; }
.saved-metrics small { margin-inline-start: 4px; color: #535b63; font-size: 12px; }
.saved-metrics .metric-unit { display: block; margin-inline-start: 0; font-weight: 400; }
.saved-run :deep(.measure-chart:not(.horizontal-chart)) { flex: 1; height: auto; min-height: 96px; max-height: 176px; }
</style>
