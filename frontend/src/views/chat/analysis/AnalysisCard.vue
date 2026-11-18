<script setup lang="ts">
import { computed, h, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AnalysisScopeChange, AnalysisState, ComparisonView, DecompositionRow, InvestigationAction, ResultView } from '@/api/analysis.ts'
import { chatApi } from '@/api/chat'
import ComparisonResult from '../workspace/ComparisonResult.vue'
import ResultViews from '../workspace/ResultViews.vue'
import InvestigationDetails from '../workspace/InvestigationDetails.vue'
import AnalysisEvidence from './AnalysisEvidence.vue'
import AnalysisQualityNotice from './AnalysisQualityNotice.vue'
import ResultCard from '../workspace/ResultCard.vue'
import ResultInspector from '../workspace/ResultInspector.vue'
import MeasureChart from '../workspace/MeasureChart.vue'
import ResultSeriesTable from '../workspace/ResultSeriesTable.vue'
import { analysisPeriods, resultNumber } from '../workspace/resultFormat'

const props = defineProps<{
  state: AnalysisState
  busy: boolean
  error?: string
  readonly?: boolean
  recordId?: number
}>()
const emit = defineEmits<{
  confirm: [choice: string]
  continue: []
  recalculate: []
  retryQuality: []
  retryInterpretation: [question: string]
  publication: [confirmed: boolean]
  reviseScope: [conditions: AnalysisScopeChange]
  query: [recordId: string]
  follow: [draft?: string]
  investigate: [action: InvestigationAction, choice?: string]
  table: [data: unknown]
  unavailable: []
}>()
const { t, locale } = useI18n()
const reasonKey = computed(() => {
  const { missing_fact, missing_fact_source, reason } = props.state.decision
  if (missing_fact === 'payment' && missing_fact_source === 'configuration') {
    return 'semantic_unsupported_configured_payment'
  }
  return missing_fact ? 'semantic_unsupported_' + missing_fact : reason
})
const inspecting = ref(false)
const showTable = ref(false)
const comparison = ref<InstanceType<typeof ComparisonResult>>()
const investigationDetails = ref<InstanceType<typeof InvestigationDetails>>()
const view = ref<ResultView>('chart')
const comparisonView = ref<ComparisonView>('delta')
const selectedGroup = ref<DecompositionRow>()
const groupAction = ref<HTMLButtonElement>()
let selectedViewRequest = 0
const metricChoice = ref('')
const editing = ref(false)
const showMore = ref(false)
const editShape = ref<AnalysisScopeChange['shape']>()
const dates = ref<AnalysisScopeChange>({ current: '' })
const series = computed(() => props.state.presentation.shape)
const semanticLimitKey = computed(() => props.state.semantic_plan?.top === 100
  ? 'cbi.semantic_execution_cap' : 'cbi.semantic_limit')
const rows = computed(() => props.state.presentation.rows ?? [])
const table = computed(() => props.state.presentation.table)
const seriesTable = () => h(ResultSeriesTable, { rows: rows.value, trend: series.value === 'trend', table: table.value })
const hasResult = computed(() =>
  props.state.outputs.some(({ reason }) => reason !== 'quality_blocked' && reason !== 'quality_incomplete') ||
  series.value != null || !!table.value
)
const canFollowMissingFact = computed(() =>
  !props.readonly && props.state.task_status === 'blocked' && !!props.state.decision.missing_fact
)
const canRetryInterpretation = computed(() =>
  !props.readonly && props.state.task_status === 'blocked' &&
  props.state.decision.reason === 'interpretation_failed'
)
const canRevise = computed(() => !props.state.semantic_plan && !props.readonly && !!props.state.scope.current &&
  !!props.state.scope.definition && ['completed', 'partial'].includes(props.state.task_status))

function openDates(shape?: 'category') {
  const scope = props.state.scope
  editShape.value = shape ?? series.value
  dates.value = {
    current: scope.current ?? '',
    baseline: scope.baseline,
    comparison_rule: scope.comparison_rule ?? undefined,
    range_start: scope.range_start,
  }
  showMore.value = false
  editing.value = true
}

function runDates() {
  const { current, baseline, comparison_rule, range_start } = dates.value
  const shape = editShape.value
  emit('reviseScope', {
    current,
    shape,
    baseline: shape || comparison_rule === 'previous_day' ? undefined : baseline,
    comparison_rule: shape ? undefined : comparison_rule,
    range_start: shape ? range_start || undefined : undefined,
  })
  editing.value = false
}
const chart = ref<InstanceType<typeof MeasureChart>>()
const unavailable = ref(false)
const accessError = ref(false)
const exportingImage = ref(false)
watch(() => props.recordId, () => { unavailable.value = false; accessError.value = false })

async function withSavedAccess(open: (data: unknown) => void | Promise<void>) {
  const id = props.recordId
  accessError.value = false
  try {
    const data = id == null ? undefined : await chatApi.get_chart_data(id)
    if (props.recordId === id) await open(data)
  } catch (cause) {
    if (props.recordId !== id) return
    const status = (cause as { response?: { status: number } }).response?.status
    unavailable.value = status === 403 || status === 404 || status === 409
    accessError.value = true
    if (unavailable.value) {
      inspecting.value = false
      showTable.value = false
      emit('unavailable')
    }
  }
}

async function exportImage() {
  if (exportingImage.value) return
  const visual = exportChart.value
  const mode = comparisonView.value
  exportingImage.value = true
  try {
    await withSavedAccess(async () => {
      if (visual !== exportChart.value || mode !== comparisonView.value) return
      const name = [decomposition.value?.label ?? t('cbiWorkspace.operatingResult'),
        decomposition.value?.dimension_label, props.state.scope.store, props.state.scope.current]
        .filter(Boolean).join(' - ')
      const downloaded = decomposition.value
        ? await comparison.value?.downloadImage(name)
        : await visual?.downloadImage(name, periods.value)
      if (!downloaded) accessError.value = true
    })
  } finally {
    exportingImage.value = false
  }
}
const periods = computed(() => analysisPeriods(props.state.scope))
const scope = computed(() => [
  props.state.scope.store && `${t('cbi.store')} ${props.state.scope.store}`,
  periods.value.current,
  periods.value.baseline && `${t('cbi.baseline_date')} ${periods.value.baseline}`,
  props.state.scope.definition_label,
  ...(props.state.scope.filter_labels ?? []).map(filter => filter.label + ' ' + filter.value),
  props.state.scope.baseline && props.state.scope.comparison_rule && t('cbi.comparison.' + props.state.scope.comparison_rule),
].filter(Boolean).join(' · '))
const canRetryQuality = computed(() =>
  !props.readonly && !props.state.investigation && !props.state.publication_confirmation &&
  !!props.state.quality?.unavailable_outputs.length &&
  ['blocked', 'partial', 'incomplete'].includes(props.state.quality.status)
)
const lead = computed(() => props.state.outputs.find((output) => output.id === 'current'))
const outputs = computed(() => props.state.outputs.filter((output) => output !== lead.value))
const amountChart = computed(() => props.state.outputs
  .filter(output => ['current', 'baseline', 'target'].includes(output.id) && output.status === 'verified' && output.value != null)
  .map(output => ({ label: t('cbi.output.' + output.id), value: output.value! })))
const seriesChart = computed(() => rows.value.map((row) => ({
  label: row.label ?? t('cbi.unknown_category'), value: row.value,
})))
const hasSeriesValues = computed(() => seriesChart.value.some(row => row.value != null))
const canPlot = computed(() => amountChart.value.length > 1 || hasSeriesValues.value)
const decomposition = computed(() => props.state.presentation.decomposition)
const investigation = computed(() => props.state.investigation)
const inline = computed(() => props.state.semantic_plan && Boolean(table.value || decomposition.value || investigation.value))
const chartEligible = computed(() => canPlot.value || !!decomposition.value || hasResult.value)
const defaultView = computed<ResultView>(() => chartEligible.value ? 'chart' : table.value ? 'data' : 'evidence')
const currentView = computed(() => view.value === 'chart' && !chartEligible.value ? defaultView.value : view.value)
const exportChart = computed(() => decomposition.value ? comparison.value?.chart : chart.value)
const eligible = computed(() => props.readonly ? [] : props.state.available_investigation ?? [])
const existingGroup = computed(() => investigation.value?.steps.find(step =>
  step.parent_total?.key != null && step.parent_total.key === selectedGroup.value?.key &&
  step.status === 'verified' && !!(step.decomposition || step.table || step.tables?.length)
))
const childDimension = computed(() => decomposition.value?.children?.[0])
watch(() => props.recordId, () => {
  view.value = defaultView.value
  comparisonView.value = 'delta'
  selectedGroup.value = undefined
  metricChoice.value = ''
})
function chooseView(next: ResultView) {
  const request = ++selectedViewRequest
  if (currentView.value !== next) return withSavedAccess(() => {
    if (request === selectedViewRequest) view.value = next
  })
}
function query(id: string) {
  return withSavedAccess(() => emit('query', id))
}
function selectGroup(row: DecompositionRow) {
  return withSavedAccess(async () => {
    selectedGroup.value = row
    await nextTick()
    groupAction.value?.focus()
  })
}
function exploreGroup() {
  return withSavedAccess(() => {
    if (existingGroup.value) {
      investigationDetails.value?.show(existingGroup.value)
      return
    }
    const row = selectedGroup.value
    const result = decomposition.value
    if (!row || !result || !childDimension.value) return
    emit('follow', t('resultExplorer.drillDraft', {
      dimension: result.dimension_label, value: row.label ?? row.key,
      id: row.key === row.label ? '' : ' (' + row.key + ')',
      child: childDimension.value.label, metric: result.label,
    }))
  })
}
function startInvestigation() {
  const key = eligible.value.length === 1 ? eligible.value[0].key : metricChoice.value
  if (key) emit('investigate', 'start_investigation', key)
}
const title = computed(() => props.state.presentation.title ??
  (series.value ? t('cbi.series.' + series.value) : t('cbiWorkspace.operatingResult')))
const amount = (value: number, plus = false, precision?: number | null) =>
  resultNumber(value, locale.value, plus, precision)
const outputUnit = (unit?: string) => unit === 'CNY' ? t('cbi.yuan') : unit
const outputReason = (reason?: string) => t(
  reason === 'quality_blocked' ? 'themeQuality.status.blocked' :
    reason === 'quality_incomplete' ? 'themeQuality.status.incomplete' :
    'cbi.reason.' + (reason ?? 'missing_data')
)
const evidenceCanContinue = computed(
  () =>
    props.state.task_status === 'partial' &&
    props.state.outputs.some(
      (output) => output.id === 'refund_members' && output.status !== 'verified'
    ) &&
    props.state.steps.some((step) => step.kind === 'summary' && step.status === 'verified') &&
    !props.state.publication_confirmation &&
    [...props.state.events].reverse().find(event => ['continue_evidence', 'cancel_publication'].includes(event.action))?.action !== 'continue_evidence'
)
</script>

<template>
  <p v-if="unavailable" role="alert" class="analysis-error">{{ t('cbiWorkspace.resultUnavailable') }}</p>
  <ResultCard
    v-else
    class="analysis-card"
    :title="state.task_status === 'awaiting_confirmation' ? t('cbi.choose') : title"
    :scope="scope"
    :status="state.task_status !== 'completed' ? t('cbi.status.' + state.task_status) : undefined"
    :data-status="state.task_status"
  >
    <p v-if="error" role="alert" class="analysis-error">{{ error }}</p>
    <p v-if="accessError" role="alert" class="analysis-error">{{ t('cbi.action_error') }}</p>
    <div v-if="!readonly && state.task_status === 'awaiting_confirmation'" class="analysis-options">
      <div v-for="choice in state.decision.options" :key="choice.id" class="analysis-option">
        <el-button :disabled="busy" @click="emit('confirm', choice.id)">{{ choice.label }}</el-button>
        <span>{{ choice.description }}</span>
      </div>
    </div>
    <div v-if="!readonly && state.publication_confirmation" class="publication-notice">
      <p>{{ t('cbi.publication_message') }}</p>
      <div>
      <el-button type="primary" :disabled="busy" @click="emit('publication', true)">{{ t('cbi.publication_confirm') }}</el-button>
      <el-button :disabled="busy" @click="emit('publication', false)">{{ t('cbi.publication_cancel') }}</el-button>
      </div>
    </div>
    <AnalysisQualityNotice :quality="state.quality" />
    <p v-if="reasonKey && !['quality_blocked', 'quality_incomplete'].includes(reasonKey)">{{ t('cbi.reason.' + reasonKey, { metrics: state.decision.metric_labels?.join(' / ') ?? '' }) }}</p>
    <div v-if="lead" class="lead-value" :data-output="lead.id" :data-verification="lead.status">
      <span>{{ lead.label ?? t('cbi.output.' + lead.id) }}</span>
      <strong v-if="lead.value != null">
        {{ amount(lead.value, false, lead.precision) }}
      </strong>
      <strong v-else>{{ outputReason(lead.reason) }}</strong>
      <small v-if="lead.value != null">{{ outputUnit(lead.unit ?? (state.semantic_plan ? undefined : 'CNY')) }}</small>
    </div>
    <dl v-if="outputs.length" class="analysis-values">
      <div v-for="output in outputs" :key="output.id" :data-output="output.id" :data-verification="output.status">
        <dt>{{ output.label ?? t('cbi.output.' + output.id) }}</dt>
        <dd v-if="output.value != null">
          {{ amount(output.value, output.id === 'rate' || output.id === 'delta' || output.id.endsWith('_rate') || output.id.endsWith('_delta'), output.precision) + (output.unit === '%' ? '%' : '') }}
          <small v-if="output.unit && output.unit !== '%'">{{ outputUnit(output.unit) }}</small>
        </dd>
        <dd v-else-if="output.status === 'verified'">{{ t('cbi.verified') }}</dd>
        <dd v-else>{{ outputReason(output.reason) }}</dd>
      </div>
    </dl>
    <section v-if="inline" class="inline-result">
      <ResultViews :view="currentView" :chart="chartEligible" :overview="!canPlot && !decomposition"
        :data="!!(table || decomposition)" evidence @select="chooseView" />
      <ComparisonResult v-if="decomposition && currentView !== 'evidence'"
        ref="comparison" v-model:mode="comparisonView" :result="decomposition" :scope="state.scope" :status="state.task_status"
        :show-data="currentView === 'data'" :selectable="!readonly && !!(childDimension || investigation?.steps.some(step => step.parent_total && step.status === 'verified'))"
        @select="selectGroup" />
      <ResultSeriesTable v-else-if="currentView === 'data'" :rows="rows" :trend="series === 'trend'" :table="table" />
      <AnalysisEvidence v-else-if="currentView === 'evidence'" :state="state" :has-query="!readonly" @query="query" />
      <template v-else>
        <p v-if="state.presentation.truncated">{{ t(semanticLimitKey, { count: state.presentation.limit }) }}</p>
        <p v-if="series && state.presentation.unit">{{ state.presentation.unit }}</p>
        <MeasureChart v-if="canPlot" ref="chart" :values="series ? seriesChart : amountChart"
          :line="series === 'trend'" :horizontal="series === 'category'" :label="title" />
      </template>
      <section v-if="selectedGroup && !readonly" class="selected-comparison-group">
        <p><strong>{{ decomposition?.dimension_label }} · {{ selectedGroup.label ?? selectedGroup.key }}</strong><br />{{ scope }}</p>
        <button ref="groupAction" data-testid="cbi-investigate-group" type="button" :disabled="busy || !(existingGroup || childDimension)"
          @click="exploreGroup">{{ t(existingGroup ? 'resultExplorer.viewStep' : 'resultExplorer.exploreGroup') }}</button>
        <button type="button" @click="selectedGroup = undefined">{{ t('cbiWorkspace.cancel') }}</button>
      </section>
      <InvestigationDetails v-if="investigation" ref="investigationDetails" :investigation="investigation"
        :has-query="!readonly" :primary-reason="state.decision.reason" @query="query" />
      <div v-if="!readonly && investigation?.source_confirmation" class="publication-notice">
        <p>{{ t('resultExplorer.sourceChanged') }}</p>
        <div>
          <el-button type="primary" data-testid="cbi-confirm-investigation-source" :disabled="busy"
            @click="emit('investigate', 'confirm_investigation_source')">{{ t('cbi.publication_confirm') }}</el-button>
          <el-button data-testid="cbi-cancel-investigation-source" :disabled="busy"
            @click="emit('investigate', 'cancel_investigation_source')">{{ t('cbi.publication_cancel') }}</el-button>
        </div>
      </div>
      <div v-else-if="!readonly && investigation && ['partial', 'blocked'].includes(state.task_status) &&
        !['plan_complete', 'refund_facts_unavailable'].includes(investigation.stop_reason ?? '')">
        <el-button text :disabled="busy" data-testid="cbi-resume-investigation"
          @click="emit('investigate', 'resume_investigation')">{{ t('resultExplorer.resume') }}</el-button>
      </div>
    </section>
    <template v-else>
    <p v-if="state.presentation.truncated">{{ t(state.semantic_plan ? semanticLimitKey : 'cbi.series_limit', { count: state.presentation.limit }) }}</p>
    <p v-if="series && state.presentation.unit && state.semantic_plan">{{ state.presentation.unit }}</p>
    <p v-if="series && !hasSeriesValues">{{ t('cbi.reason.missing_data') }}</p>
    <MeasureChart
      v-if="canPlot"
      ref="chart"
      :values="series ? seriesChart : amountChart"
      :line="series === 'trend'"
      :horizontal="series === 'category'"
      :label="title"
    />
    </template>
    <ResultInspector v-model="editing" :title="t('cbi.change_dates')">
      <form class="analysis-dates" @submit.prevent="runDates">
        <label v-if="editShape">
          {{ t('cbi.range_start') }}
          <input v-model="dates.range_start" type="date" :max="dates.current" :required="editShape === 'trend'" />
        </label>
        <label>
          {{ t('cbi.current_date') }}
          <input v-model="dates.current" type="date" :min="editShape ? dates.range_start : undefined" required />
        </label>
        <template v-if="!editShape && (state.scope.baseline || state.outputs.some(output => output.id === 'baseline'))">
          <label>
            {{ t('cbi.comparison_method') }}
            <select v-model="dates.comparison_rule" required>
              <option :value="undefined" disabled>{{ t('cbi.comparison_method') }}</option>
              <option value="fixed">{{ t('cbi.comparison.fixed') }}</option>
              <option value="previous_day">{{ t('cbi.comparison.previous_day') }}</option>
            </select>
          </label>
          <label v-if="dates.comparison_rule === 'fixed'">
            {{ t('cbi.baseline_date') }}
            <input v-model="dates.baseline" type="date" required />
          </label>
        </template>
        <p>{{ scope }}</p>
        <el-button type="primary" native-type="submit" :disabled="busy">{{ t('cbi.run_conditions') }}</el-button>
      </form>
    </ResultInspector>
    <ResultInspector v-model="inspecting" :title="t('cbiWorkspace.inspect')">
      <AnalysisEvidence :state="state" :has-query="!readonly" @query="query" />
    </ResultInspector>
    <template v-if="hasResult || state.data_version || state.quality || canFollowMissingFact || canRetryInterpretation" #actions>
    <el-button v-if="canRetryInterpretation" text :disabled="busy" data-testid="cbi-retry-interpretation"
      @click="emit('retryInterpretation', state.request.text)">{{ t('cbi.retry_interpretation') }}</el-button>
    <el-button v-if="canFollowMissingFact" text :disabled="busy" data-testid="cbi-follow-missing-fact"
      @click="emit('follow')">{{ t('cbiWorkspace.follow') }}</el-button>
    <el-button v-else-if="!readonly && hasResult" text @click="withSavedAccess(() => emit('follow'))">{{ t('cbiWorkspace.follow') }}</el-button>
    <el-button v-if="canRevise" text :disabled="busy" @click="openDates()">{{ t('cbi.change_dates') }}</el-button>
    <el-button v-if="!inline" text @click="withSavedAccess(() => { inspecting = true })">{{ t('cbiWorkspace.inspect') }}</el-button>
    <el-button v-if="canRetryQuality" text :disabled="busy" data-testid="cbi-retry-quality"
      @click="emit('retryQuality')">{{ t('themeQuality.retry') }}</el-button>
    <el-button
      v-if="!inline && (series || table || ($slots.default && hasResult))"
      text
      @click="withSavedAccess(data => { emit('table', data); showTable = true })"
    >{{ t('cbiWorkspace.resultsTable') }}</el-button>
    <ResultInspector v-model="showTable" :title="t('cbiWorkspace.resultsTable')">
      <ResultSeriesTable v-if="(series || table) && showTable && !$slots.default" :rows="rows" :trend="series === 'trend'" :table="table" />
      <slot v-if="showTable" :series="seriesTable" />
    </ResultInspector>
    <label v-if="eligible.length > 1" class="investigation-choice">
      <span>{{ t('resultExplorer.metricChoice') }}</span>
      <select v-model="metricChoice" data-testid="cbi-investigation-metric" :disabled="busy">
        <option value="" disabled>{{ t('resultExplorer.metricChoice') }}</option>
        <option v-for="metric in eligible" :key="metric.key" :value="metric.key">{{ metric.label }}</option>
      </select>
    </label>
    <el-button v-if="eligible.length" text data-testid="cbi-start-investigation"
      :disabled="busy || (eligible.length > 1 && !metricChoice)" @click="startInvestigation">{{ t('resultExplorer.start') }}</el-button>
    <slot v-if="inline && !readonly" name="toolbar" :view="currentView" :comparison-view="comparisonView" />
    <el-popover v-if="canPlot || !!decomposition || (!readonly && (evidenceCanContinue || (['completed', 'partial'].includes(state.task_status) && state.scope.definition)))" v-model:visible="showMore" trigger="click" :width="220">
      <template #reference><el-button text>{{ t('cbiWorkspace.more') }}</el-button></template>
    <el-button v-if="canRevise && !series && state.scope.category_available" text :disabled="busy" @click="openDates('category')">{{ t('cbi.series.category') }}</el-button>
    <el-button v-if="canPlot || decomposition" text :loading="exportingImage" :disabled="!exportChart?.rendered" @click="exportImage">{{ t('cbiWorkspace.downloadImage') }}</el-button>
    <el-button v-if="!readonly && evidenceCanContinue" text :disabled="busy" @click="showMore = false; emit('continue')">{{
      t('cbi.continue')
    }}</el-button>
    <el-button
      v-if="
        !readonly && ['completed', 'partial'].includes(state.task_status) && state.scope.definition
      "
      :disabled="busy"
      text
      @click="showMore = false; emit('recalculate')"
      >{{ t('cbi.recalculate') }}</el-button
    >
    </el-popover>
    </template>
  </ResultCard>
</template>

<style scoped lang="less">
.inline-result { display: grid; gap: 12px; min-width: 0; }
.inline-result > p { margin: 0; }
.selected-comparison-group { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 10px; border: 1px solid #dbe7df; border-radius: 8px; }
.selected-comparison-group p { margin: 0; flex: 1; min-width: min(230px, 100%); }
.selected-comparison-group button { cursor: pointer; border: 1px solid #c9dbd0; background: #fff; border-radius: 5px; padding: 5px 9px; min-height: 32px; color: #17634d; font: inherit; }
.selected-comparison-group button:disabled { opacity: .55; cursor: default; }
.investigation-choice { display: inline-flex; align-items: center; gap: 6px; }
.investigation-choice select { border: 1px solid #c9dbd0; border-radius: 5px; padding: 5px; background: #fff; min-height: 32px; }

.publication-notice { display: grid; gap: 8px; padding: 12px; border-radius: 8px; background: #f3f7f5; margin-bottom: 12px; }
.publication-notice p { margin: 0; line-height: 1.5; }
.publication-notice > div { display: flex; flex-wrap: wrap; gap: 8px; }
.publication-notice :deep(.ed-button + .ed-button) { margin-left: 0; }
.analysis-dates { display: grid; gap: 12px; justify-items: start; }
.analysis-dates label { display: grid; gap: 6px; }
.analysis-dates input, .analysis-dates select { min-height: 32px; padding: 0 8px; border: 1px solid #c8cfcc; border-radius: 4px; font: inherit; }
.lead-value { display: grid; gap: 2px; color: #535b63; }
.lead-value strong { font-size: 32px; font-weight: 650; color: #1f2329; font-variant-numeric: tabular-nums; }
.lead-value small, .analysis-values small { display: block; font-size: 12px; color: #535b63; font-weight: 400; }
.analysis-values {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin: 12px 0;
}
.analysis-values dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.analysis-values dt {
  color: #535962;
}
.analysis-values {
  column-gap: 24px;
  row-gap: 12px;
  padding-block: 12px;
  border-block: 1px solid #ecf0ee;
}
.analysis-values > div {
  flex: 1 0 112px;
}
.analysis-values dd {
  margin-top: 4px;
  font-size: 17px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}
.analysis-options { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
.analysis-option {
  display: grid;
  justify-items: start;
  align-content: start;
  gap: 8px;
  border: 1px solid #dfe5e2;
  border-radius: 8px;
  padding: 12px;
}
.analysis-error {
  color: #a52924;
}
</style>
