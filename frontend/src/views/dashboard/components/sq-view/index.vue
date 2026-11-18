<script setup lang="ts">
import { chatApi } from '@/api/chat'
import type { AnalysisState, ComparisonView, ResultView } from '@/api/analysis'
import AnalysisEvidence from '@/views/chat/analysis/AnalysisEvidence.vue'
import AnalysisQualityNotice from '@/views/chat/analysis/AnalysisQualityNotice.vue'
import ResultViews from '@/views/chat/workspace/ResultViews.vue'
import InvestigationDetails from '@/views/chat/workspace/InvestigationDetails.vue'
import AnalysisCard from '@/views/chat/analysis/AnalysisCard.vue'
import { analysisPeriods } from '@/views/chat/workspace/resultFormat'
import ResultSeriesTable from '@/views/chat/workspace/ResultSeriesTable.vue'
import ChartComponent from '@/views/chat/component/ChartComponent.vue'
import SavedRunVisual from './SavedRunVisual.vue'
import { hasSavedRunVisual } from './savedRun'
import icon_window_mini_outlined from '@/assets/svg/icon_window-mini_outlined.svg'
import SqViewDisplay from '@/views/dashboard/components/sq-view/index.vue'
const props = defineProps({
  viewInfo: {
    type: Object,
    required: true,
  },
  outerId: {
    type: String,
    required: false,
    default: null,
  },
  showPosition: {
    type: String,
    required: false,
    default: 'default',
  },
  fitSavedResult: Boolean,
})

import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ChartPopover from '@/views/chat/chat-block/ChartPopover.vue'
import ICON_TABLE from '@/assets/svg/chart/icon_form_outlined.svg'
import ICON_COLUMN from '@/assets/svg/chart/icon_dashboard_outlined.svg'
import ICON_BAR from '@/assets/svg/chart/icon_bar_outlined.svg'
import ICON_LINE from '@/assets/svg/chart/icon_chart-line.svg'
import ICON_PIE from '@/assets/svg/chart/icon_pie_outlined.svg'
import type { ChartTypes } from '@/views/chat/component/BaseChart.ts'
const { t } = useI18n()
const chartRef = ref(null)
const currentChartType = ref<ChartTypes | undefined>(undefined)

const renderChart = () => {
  //@ts-expect-error eslint-disable-next-line @typescript-eslint/no-unused-expressions
  chartRef.value?.destroyChart()
  //@ts-expect-error eslint-disable-next-line @typescript-eslint/no-unused-expressions
  chartRef.value?.renderChart()
}

const enlargeDialogVisible = ref(false)
const savedAnalysisVisible = ref(false)
const savedAnalysisTrigger = ref<{ $el: HTMLButtonElement }>()
const unavailable = ref(false)
const exportingImage = ref(false)
const imageError = ref(false)
const savedTable = ref(false)
const resultView = ref<ResultView>('chart')
const comparisonMode = ref<ComparisonView>('delta')
const savedState = computed<AnalysisState | undefined>(() => props.viewInfo.analysis_state)
const decomposition = computed(() => savedState.value?.presentation.decomposition)
const fixedTable = computed(() => savedState.value?.presentation.table)
const inline = computed(() => !!savedState.value?.semantic_plan &&
  Boolean(fixedTable.value || decomposition.value || savedState.value?.investigation))
const chartEligible = computed(() => !!(decomposition.value || savedState.value?.presentation.shape || savedState.value?.outputs.length))
const dataEligible = computed(() => !!(fixedTable.value || decomposition.value))
const defaultView = computed<ResultView>(() => chartEligible.value ? 'chart' : dataEligible.value ? 'data' : 'evidence')
watch(() => props.viewInfo.record_id, () => {
  const chart = props.viewInfo.chart
  const initial = chart?.result_view
  resultView.value = initial === 'evidence' || (initial === 'data' && dataEligible.value) ||
    (initial === 'chart' && chartEligible.value) ? initial : defaultView.value
  comparisonMode.value = chart?.comparison_view === 'periods' ? 'periods' : 'delta'
}, { immediate: true })
function chooseView(next: ResultView) {
  const id = props.viewInfo.record_id
  if (next !== resultView.value) return withSavedAccess(() => {
    if (props.viewInfo.record_id === id) resultView.value = next
  })
}
const savedVisual = ref<InstanceType<typeof SavedRunVisual>>()
const hasSavedVisual = computed(() => hasSavedRunVisual(props.viewInfo.analysis_state, props.viewInfo.method_result))
const savedPeriods = computed(() => {
  const analysis = props.viewInfo.analysis_state as AnalysisState | undefined
  return analysis && !analysis.multi ? analysisPeriods(analysis.scope) : undefined
})
const savedScope = computed(() => {
  const method = props.viewInfo.method_result
  if (method) return [t('methods.unit_' + method.unit), method.window_hours + ' h',
    method.entry_start + ' → ' + method.entry_end, t('methods.observation') + ' ' + method.observation_asof].join(' · ')
  const state = props.viewInfo.analysis_state
  if (!state) return ''
  if (state.multi) return state.multi.window || ''
  const scope = state.scope
  const periods = savedPeriods.value
  return [
    scope.store && `${t('cbi.store')} ${scope.store}`,
    periods?.current,
    periods?.baseline && `${t('cbi.baseline_date')} ${periods.baseline}`,
    scope.definition_label,
    ...(scope.filter_labels ?? []).map((filter: { label: string; value: string | number }) => filter.label + " " + filter.value),
    state.task_status !== 'completed' && t('cbi.status.' + state.task_status),
  ].filter(Boolean).join(' · ')
})
const exportName = computed(() => [props.viewInfo.chart.title, savedScope.value].filter(Boolean).join(' - '))

async function withSavedAccess(open: () => void | Promise<void>, onError?: () => void) {
  try {
    if (props.viewInfo.analysis_state) await chatApi.get_chart_data(props.viewInfo.record_id)
    await open()
  } catch (cause) {
    const status = (cause as { response?: { status: number } }).response?.status
    if (status === 403 || status === 404 || status === 409) {
      unavailable.value = true
      savedAnalysisVisible.value = false
      enlargeDialogVisible.value = false
    } else onError?.()
  }
}

async function exportSavedImage() {
  if (exportingImage.value) return
  const id = props.viewInfo.record_id
  const visual = savedVisual.value
  const mode = comparisonMode.value
  exportingImage.value = true
  imageError.value = false
  try {
    await withSavedAccess(async () => {
      if (id !== props.viewInfo.record_id || visual !== savedVisual.value || mode !== comparisonMode.value) return
      if (!await visual?.downloadImage(exportName.value, savedPeriods.value)) imageError.value = true
    }, () => { imageError.value = true })
  } finally {
    exportingImage.value = false
  }
}

function exportSavedExcel() {
  return withSavedAccess(async () => {
    const blob = await chatApi.export2Excel(props.viewInfo.record_id, props.viewInfo.chat_id)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = exportName.value.replace(/[\\/:*?"<>|]/g, '_') + '.xlsx'
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  })
}

const enlargeView = () => {
  return withSavedAccess(() => { enlargeDialogVisible.value = true })
}

const chartTypeList = computed(() => {
  const _list = []
  if (props.viewInfo.chart) {
    switch (props.viewInfo.chart['sourceType']) {
      case 'table':
        break
      case 'column':
      case 'bar':
      case 'line':
        _list.push({
          value: 'column',
          name: t('chat.chart_type.column'),
          icon: ICON_COLUMN,
        })
        _list.push({
          value: 'bar',
          name: t('chat.chart_type.bar'),
          icon: ICON_BAR,
        })
        _list.push({
          value: 'line',
          name: t('chat.chart_type.line'),
          icon: ICON_LINE,
        })
        break
      case 'pie':
        _list.push({
          value: 'pie',
          name: t('chat.chart_type.pie'),
          icon: ICON_PIE,
        })
    }
  }

  return _list
})

function changeTable() {
  onTypeChange('table')
}

const chartType = computed<ChartTypes>({
  get() {
    if (currentChartType.value) {
      return currentChartType.value
    }
    return props.viewInfo.chart['sourceType'] ?? 'table'
  },
  set(v) {
    currentChartType.value = v
  },
})

function onTypeChange(val: any) {
  chartType.value = val
  // eslint-disable-next-line vue/no-mutating-props
  props.viewInfo.chart.type = val
  nextTick(() => {
    //@ts-expect-error eslint-disable-next-line @typescript-eslint/no-unused-expressions
    chartRef.value?.destroyChart()
    //@ts-expect-error eslint-disable-next-line @typescript-eslint/no-unused-expressions
    chartRef.value?.renderChart()
  })
}

onMounted(() => {
  // eslint-disable-next-line vue/no-mutating-props
  props.viewInfo.chart['sourceType'] =
    props.viewInfo.chart['sourceType'] ?? props.viewInfo.chart.type
})

defineExpose({
  renderChart,
  enlargeView,
})
</script>

<template>
  <p v-if="unavailable" class="error-info" role="alert">{{ t('cbiWorkspace.resultUnavailable') }}</p>
  <div v-else class="chart-base-container"
    :class="{ 'saved-chart-container': hasSavedVisual, 'fit-saved-result': hasSavedVisual && fitSavedResult }">
    <div class="header-bar flex-gap-fallback">
      <div class="title" :title="savedScope || undefined">
        {{ viewInfo.chart.title }}
        <span v-if="savedScope" class="saved-scope">{{ savedScope }}</span>
      </div>
      <el-button
        v-if="viewInfo.analysis_state"
        ref="savedAnalysisTrigger"
        class="saved-analysis-button"
        text
        @click="withSavedAccess(() => { savedAnalysisVisible = true })"
        >{{ t('cbi.scope') }}</el-button
      >
      <div v-if="showPosition === 'multiplexing'" class="buttons-bar flex-gap-fallback">
        <div class="chart-select-container flex-gap-fallback">
          <el-tooltip effect="dark" :content="t('chat.type')" placement="top">
            <ChartPopover
              v-if="chartTypeList.length > 0"
              :chart-type-list="chartTypeList"
              :chart-type="chartType"
              :title="t('chat.type')"
              @type-change="onTypeChange"
            ></ChartPopover>
          </el-tooltip>
          <el-tooltip effect="dark" :content="t('chat.chart_type.table')" placement="top">
            <el-button
              class="tool-btn"
              :class="{ 'chart-active': currentChartType === 'table' }"
              text
              @click="changeTable"
            >
              <el-icon size="16">
                <ICON_TABLE />
              </el-icon>
            </el-button>
          </el-tooltip>
        </div>
        <div class="divider" />
      </div>
    </div>
    <AnalysisQualityNotice v-if="hasSavedVisual" :quality="savedState?.quality" />
    <ResultViews v-if="inline" :view="resultView" :chart="chartEligible" :data="dataEligible"
      :overview="!decomposition && !savedState?.presentation.shape" evidence @select="chooseView" />
    <div
      class="chart-show-area"
      :class="{ 'saved-table': savedTable || (inline && resultView !== 'chart') }"
      :tabindex="hasSavedVisual ? 0 : undefined"
      :role="hasSavedVisual ? 'region' : undefined"
      :aria-label="hasSavedVisual ? viewInfo.chart.title : undefined"
    >
      <div v-if="inline && resultView === 'evidence' && savedState" class="saved-inline-evidence">
        <AnalysisEvidence :state="savedState" />
        <InvestigationDetails v-if="savedState.investigation" :investigation="savedState.investigation" />
      </div>
      <div v-else-if="inline && resultView === 'data' && !decomposition && savedState" class="saved-series-table">
        <ResultSeriesTable :rows="savedState.presentation.rows ?? []"
          :trend="savedState.presentation.shape === 'trend'" :table="fixedTable" />
      </div>
      <SavedRunVisual v-else-if="hasSavedVisual && !savedTable" ref="savedVisual"
        v-model:comparison-mode="comparisonMode" :show-data="inline && resultView === 'data'"
        :analysis="viewInfo.analysis_state?.scope ? viewInfo.analysis_state : undefined" :funnel="viewInfo.method_result"
        :title="viewInfo.chart.title" :wrap-category-labels="fitSavedResult" />
      <div v-else-if="savedTable && (viewInfo.analysis_state?.presentation.shape || viewInfo.analysis_state?.presentation.table)" class="saved-series-table">
        <ResultSeriesTable
          :rows="viewInfo.analysis_state.presentation.rows ?? []"
          :trend="viewInfo.analysis_state.presentation.shape === 'trend'"
          :table="viewInfo.analysis_state.presentation.table"
        />
      </div>
      <div v-else-if="viewInfo.status === 'failed'" class="error-info">
        {{ viewInfo.message }}
      </div>
      <ChartComponent
        v-else-if="viewInfo.id"
        :id="outerId || viewInfo.id"
        ref="chartRef"
        :type="hasSavedVisual ? 'table' : chartType"
        :columns="viewInfo.chart.columns"
        :x="viewInfo.chart?.xAxis"
        :y="viewInfo.chart?.yAxis"
        :series="viewInfo.chart?.series"
        :series-mode="viewInfo.chart?.series_mode"
        :data="viewInfo.data?.data"
        :multi-quota-name="viewInfo.chart?.multiQuotaName"
      />
    </div>
    <div v-if="hasSavedVisual" class="saved-chart-actions">
      <el-button v-if="!inline" text @click="withSavedAccess(() => { savedTable = !savedTable })">
        {{ t(savedTable ? 'cbiWorkspace.showChart' : 'cbiWorkspace.resultsTable') }}
      </el-button>
      <el-button v-if="savedVisual?.chart" text :loading="exportingImage" :disabled="!savedVisual.chart.rendered"
        @click="exportSavedImage">{{ t('cbiWorkspace.downloadImage') }}</el-button>
      <router-link v-if="viewInfo.analysis_state?.semantic_plan && viewInfo.chat_id"
        class="saved-members-link" :to="{ name: 'chat', query: { chat: String(viewInfo.chat_id), record: String(viewInfo.record_id) } }">
        {{ t('cbi.original_run') }}
      </router-link>
      <el-button v-if="viewInfo.chat_id" text @click="exportSavedExcel">{{ t('chat.excel') }}</el-button>
    </div>
    <p v-if="imageError" class="error-info" role="alert">{{ t('cbi.action_error') }}</p>
    <el-dialog
      v-if="viewInfo.analysis_state"
      v-model="savedAnalysisVisible"
      :title="t('cbi.scope')"
      :close-on-press-escape="true"
      append-to-body
      @closed="savedAnalysisTrigger?.$el.focus()"
    >
      <div class="saved-analysis">
        <p v-if="viewInfo.analysis_state.multi?.method">
          {{ viewInfo.analysis_state.multi.window }} · {{ viewInfo.analysis_state.multi.numerator }}/{{ viewInfo.analysis_state.multi.denominator }}
          · {{ viewInfo.analysis_state.multi.revision }}
        </p>
        <p v-else-if="viewInfo.analysis_state.multi">
          {{ viewInfo.analysis_state.multi.window }} · {{ viewInfo.analysis_state.multi.revision }}
          · {{ t('multi.sales') }} {{ viewInfo.analysis_state.multi.sales }}
          · {{ t('multi.fee') }} {{ viewInfo.analysis_state.multi.recorded_fee }} {{ viewInfo.analysis_state.multi.currency }}
        </p>
        <AnalysisCard v-else :state="viewInfo.analysis_state" :record-id="viewInfo.record_id"
          :busy="false" readonly />
        <router-link
          v-if="viewInfo.method_result && viewInfo.analysis_state.multi?.run_id && viewInfo.chat_id"
          class="saved-members-link"
          :to="{ name: 'chat', query: { chat: String(viewInfo.chat_id), view: 'analysis', run: viewInfo.analysis_state.multi.run_id } }"
        >{{ t('methods.members') }}</router-link>
      </div>
    </el-dialog>
    <el-dialog
      v-if="enlargeDialogVisible"
      v-model="enlargeDialogVisible"
      fullscreen
      :show-close="false"
      class="chart-fullscreen-dialog-view"
      header-class="chart-fullscreen-dialog-header-view"
      body-class="chart-fullscreen-dialog-body-view"
    >
      <div style="position: absolute; right: 15px; top: 15px; cursor: pointer">
        <el-tooltip effect="dark" :content="t('dashboard.exit_preview')" placement="top">
          <el-button
            class="tool-btn"
            style="width: 26px"
            text
            @click="() => (enlargeDialogVisible = false)"
          >
            <el-icon size="16">
              <icon_window_mini_outlined />
            </el-icon>
          </el-button>
        </el-tooltip>
      </div>
      <SqViewDisplay :view-info="viewInfo" :outer-id="'enlarge-' + viewInfo.id" />
    </el-dialog>
  </div>
</template>

<style lang="less">
.chart-fullscreen-dialog-view {
  padding: 0;
}
.chart-fullscreen-dialog-header-view {
  display: none;
}
.chart-fullscreen-dialog-body-view {
  padding: 0;
  height: 100%;
}
</style>

<style scoped lang="less">
.saved-series-table { width: 100%; max-height: 100%; overflow: auto; }
.chart-base-container {
  width: 100%;
  height: 100%;
  background: #fff;
  padding: 12px !important;
  border-radius: 12px;
  div::-webkit-scrollbar {
    width: 0 !important;
    height: 0 !important;
  }
  .header-bar {
    height: 32px;
    display: flex;
    margin-bottom: 16px;

    align-items: center;
    flex-direction: row;

    .tool-btn {
      width: 24px;
      height: 24px;

      font-size: 16px;
      font-weight: 400;
      line-height: 24px;
      border-radius: 6px;
      color: rgba(100, 106, 115, 1);

      .tool-btn-inner {
        display: flex;
        flex-direction: row;
        align-items: center;
      }

      &:hover {
        background: rgba(31, 35, 41, 0.1);
      }
      &:active {
        background: rgba(31, 35, 41, 0.1);
      }
    }

    .chart-active {
      background: var(--ed-color-primary-1a, rgba(28, 186, 144, 0.1));
      color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      border-radius: 6px;

      :deep(.ed-select__wrapper) {
        background: transparent;
      }
      :deep(.ed-select__input) {
        color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      }
      :deep(.ed-select__placeholder) {
        color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      }
      :deep(.ed-select__caret) {
        color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      }
    }

    .title {
      flex: 1;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;

      color: rgba(31, 35, 41, 1);
      font-weight: 500;
      font-size: 16px;
      line-height: 24px;
    }

    .buttons-bar {
      display: flex;
      flex-direction: row;
      align-items: center;
      --gap-size: 16px;
      gap: 16px;
      margin-right: 36px;
      .divider {
        width: 1px;
        height: 16px;
        border-left: 1px solid rgba(31, 35, 41, 0.15);
      }
    }

    .chart-select-container {
      padding: 3px;
      display: flex;
      flex-direction: row;
      --gap-size: 4px;
      gap: 4px;
      border-radius: 6px;

      border: 1px solid rgba(217, 220, 223, 1);

      .chart-select {
        min-width: 40px;
        width: 40px;
        height: 24px;

        :deep(.ed-select__wrapper) {
          padding: 4px;
          min-height: 24px;
          box-shadow: unset;
          border-radius: 6px;

          &:hover {
            background: rgba(31, 35, 41, 0.1);
          }
          &:active {
            background: rgba(31, 35, 41, 0.1);
          }
        }
        :deep(.ed-select__caret) {
          font-size: 12px !important;
        }
      }
    }
  }
}

.saved-analysis-button {
  margin-left: 8px;
}
.saved-chart-container { display: flex; flex-direction: column; }
.saved-chart-container .header-bar { flex-shrink: 0; margin-bottom: 4px; }
.saved-chart-container .chart-show-area {
  display: flex;
  flex-direction: column;
  flex: 1;
  height: auto;
  min-height: 0;
  overflow: auto;
  scrollbar-width: thin;
  scrollbar-color: #858e92 transparent;
}
.saved-chart-container .chart-show-area:focus-visible { outline: 2px solid #166451; outline-offset: -2px; }
.saved-chart-container .chart-show-area::-webkit-scrollbar { width: 7px !important; height: 7px !important; }
.saved-chart-container .chart-show-area::-webkit-scrollbar-thumb { background: #858e92; border-radius: 4px; }
.fit-saved-result .header-bar {
  align-items: flex-start;
  height: auto;
  min-height: 32px;

  .title {
    min-width: 0;
    overflow-wrap: anywhere;
    white-space: normal;
  }
  .saved-scope {
    display: block;
    margin-inline-start: 0;
    line-height: 20px;
  }
}
.fit-saved-result .chart-show-area:not(.saved-table) {
  flex: 1 0 auto;
  overflow: visible;
}
.fit-saved-result .chart-show-area.saved-table {
  min-height: 120px;
}
.fit-saved-result :deep(.saved-metrics) {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 118px), 1fr));
  gap: 8px 12px;
}
.fit-saved-result :deep(.saved-run .measure-chart:not(.horizontal-chart)) {
  min-height: 156px;
}
.saved-inline-evidence { width: 100%; padding: 8px; overflow: auto; }
.saved-chart-actions { display: flex; flex-shrink: 0; flex-wrap: wrap; align-items: center; gap: 2px; }
.saved-chart-actions :deep(.ed-button + .ed-button) { margin-left: 0; }
.saved-scope {
  margin-inline-start: 8px;
  color: #535b63;
  font-size: 13px;
  font-weight: 400;
}
.saved-analysis {
  max-height: 65vh;
  overflow: auto;
}
.saved-members-link {
  color: var(--ed-color-primary);
  text-decoration: underline;
}
.chart-show-area {
  width: 100%;
  height: calc(100% - 32px);
}

.buttons-bar {
  display: flex;
  flex-direction: row;
  align-items: center;

  --gap-size: 16px;
  gap: 16px;

  .divider {
    width: 1px;
    height: 16px;
    border-left: 1px solid rgba(31, 35, 41, 0.15);
  }
}

.chart-select-container {
  padding: 3px;
  display: flex;
  flex-direction: row;
  --gap-size: 4px;
  gap: 4px;
  border-radius: 6px;

  border: 1px solid rgba(217, 220, 223, 1);

  .chart-select {
    min-width: 40px;
    width: 40px;
    height: 24px;

    :deep(.ed-select__wrapper) {
      padding: 4px;
      min-height: 24px;
      box-shadow: unset;
      border-radius: 6px;

      &:hover {
        background: rgba(31, 35, 41, 0.1);
      }
      &:active {
        background: rgba(31, 35, 41, 0.1);
      }
    }
    :deep(.ed-select__caret) {
      font-size: 12px !important;
    }
  }
}

.error-info {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  font-size: 12px;
  color: var(--N600, #646a73);
}
</style>
