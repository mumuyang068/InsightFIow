<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  themeApi,
  themeError,
  type ThemeDraft,
  type ThemePreviewIntent,
  type ThemeQualityReport,
  type ThemeQualitySummary,
  type ThemeValidation,
} from '@/api/theme'

const props = defineProps<{
  draft?: ThemeDraft | null
  activeVersion: string | null
  staleDraft: boolean
  validations: ThemeValidation[]
  busy: boolean
  dirty: boolean
  latestResult: string
  themeId: string
  qualityReports: ThemeQualitySummary[]
}>()
const emit = defineEmits<{
  validate: []
  preview: [intent: ThemePreviewIntent]
  confirm: [id: string, note: string, qualityReportId?: string]
}>()
const start = ref('')
const end = ref('')
const compareStart = ref('')
const compareEnd = ref('')
const metrics = ref<string[]>([])
const dimension = ref('')
const value = ref<string | number>('')
const group = ref('none')
const selectedId = ref('')
const note = ref('')
const { t, locale } = useI18n()
const qualityReportId = ref('')
const warningReport = ref<ThemeQualityReport>()
const warningError = ref('')
const warningReports = computed(() => props.qualityReports.filter(({ status }) => status === 'warning'))
const selectedWarning = computed(() => warningReports.value.find(({ id }) => id === qualityReportId.value))
watch(warningReports, (items) => {
  if (!items.some(({ id }) => id === qualityReportId.value)) qualityReportId.value = ''
})
watch(selectedWarning, async (selected, _previous, onCleanup) => {
  warningReport.value = undefined
  warningError.value = ''
  if (!selected) return
  let active = true
  onCleanup(() => { active = false })
  try {
    const report = await themeApi.getQuality(props.themeId, selected.id)
    if (active) warningReport.value = report
  } catch (cause) {
    if (active) warningError.value = themeError(cause).detail || t('themeConfig.loadFailed')
  }
})
function warningReportName(item: ThemeQualitySummary) {
  return [new Date(item.completed_at).toLocaleString(locale.value),
    t('themeQuality.status.warning'), item.id].join(' · ')
}
const selected = computed(() => props.validations.find(({ id }) => id === selectedId.value))
const filter = computed(() => props.draft?.content.dimensions[dimension.value])
const table = computed(
  () => selected.value?.receipt.presentation?.table ?? selected.value?.receipt.table
)
const rows = computed(() => table.value?.rows ?? selected.value?.receipt.rows ?? [])
const columns = computed(() => {
  if (table.value?.columns?.length) return table.value.columns
  return Object.keys(rows.value[0] ?? {}).map((key) => ({
    key,
    label: (isCurrent.value && props.draft?.content.metrics[key]?.label) || key,
  }))
})
const isCurrent = computed(
  () =>
    !!props.draft &&
    selected.value?.draft_id === props.draft.id &&
    selected.value?.revision === props.draft.revision
)
const groupedMetrics = computed(() =>
  metrics.value.some((key) => props.draft?.content.metrics[key]?.op === 'repeat_count')
)
const compareReady = computed(() => {
  if (!compareStart.value && !compareEnd.value) return true
  const separated = compareEnd.value <= start.value || end.value <= compareStart.value
  if (!compareStart.value || !compareEnd.value || compareStart.value >= compareEnd.value || !separated) return false
  if (group.value === 'none') return true
  const config = props.draft?.content.decomposition
  return !!config && group.value in config.dimensions && metrics.value.length === 1 && metrics.value[0] in config.metrics
})
const canRun = computed(
  () =>
    !!props.draft &&
    !props.staleDraft &&
    !props.dirty &&
    !props.busy &&
    compareReady.value &&
    start.value &&
    end.value &&
    start.value < end.value &&
    metrics.value.length > 0 &&
    (!dimension.value || String(value.value).trim()) &&
    !(groupedMetrics.value && group.value !== 'none')
)
watch(
  () => props.draft?.id,
  () => {
    start.value = props.draft?.content.start ?? ''
    end.value = props.draft?.content.end ?? ''
    metrics.value = props.draft ? [props.draft.content.default_metric] : []
    dimension.value = ''
    value.value = ''
    group.value = 'none'
    compareStart.value = ''
    compareEnd.value = ''
  },
  { immediate: true }
)
watch(
  () => props.draft?.revision,
  () => {
    metrics.value = metrics.value.filter((key) => key in (props.draft?.content.metrics ?? {}))
    if (!metrics.value.length)
      metrics.value = props.draft ? [props.draft.content.default_metric] : []
    if (!props.draft?.content.decomposition) {
      compareStart.value = ''
      compareEnd.value = ''
    }
    if (!(dimension.value in (props.draft?.content.dimensions ?? {}))) dimension.value = ''
    if (
      !['none', 'day'].includes(group.value) &&
      !(group.value in (props.draft?.content.dimensions ?? {}))
    )
      group.value = 'none'
  }
)
watch(
  () => props.latestResult,
  (id) => {
    if (id) selectedId.value = id
  }
)
watch(
  () => props.validations,
  (validations) => {
    if (!validations.some(({ id }) => id === selectedId.value))
      selectedId.value = validations[0]?.id ?? ''
  },
  { immediate: true }
)
watch(selectedId, () => {
  note.value = ''
  qualityReportId.value = ''
})

function kind(validation: ThemeValidation) {
  return validation.kind ?? validation.receipt.kind
}

function run() {
  if (!canRun.value) return
  emit('preview', {
    operation: 'new',
    start: start.value,
    end: end.value,
    ...(compareStart.value && { compare_start: compareStart.value, compare_end: compareEnd.value }),
    metrics: metrics.value,
    group: group.value,
    filters: dimension.value ? { [dimension.value]: String(value.value).trim() } : undefined,
  })
}
</script>

<template>
  <section class="preview">
    <div class="section-header">
      <div>
        <h2>{{ $t('themeConfig.preview') }}</h2>
        <p
          data-testid="theme-validation-subject"
          :data-draft-id="draft?.id"
          :data-revision="draft?.revision"
          :data-base-version="draft?.base_version"
        >
          {{
            $t('themeConfig.validationSubject', {
              draft: draft
                ? $t('themeConfig.draftIdentity', { id: draft.id, revision: draft.revision })
                : $t('themeConfig.noDraft'),
            })
          }}
        </p>
      </div>
      <el-button
        v-if="draft && !staleDraft"
        data-testid="theme-validate"
        :disabled="busy || dirty"
        @click="emit('validate')"
        >{{ $t('themeConfig.validate') }}</el-button
      >
    </div>

    <el-alert
      v-if="draft && staleDraft"
      data-testid="theme-validation-stale"
      :title="$t('themeConfig.staleDraft', { base: draft.base_version, current: activeVersion })"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-else-if="dirty"
      :title="$t('themeConfig.saveBeforeValidation')"
      type="warning"
      :closable="false"
    />
    <el-form
      v-if="draft && !staleDraft"
      label-position="top"
      class="preview-form"
      @submit.prevent="run"
    >
      <div class="date-controls">
        <el-form-item :label="$t('themeConfig.start')" required>
          <el-input v-model="start" data-testid="theme-preview-start" type="date" />
        </el-form-item>
        <el-form-item :label="$t('themeConfig.end')" required>
          <el-input v-model="end" data-testid="theme-preview-end" type="date" />
        </el-form-item>
      </div>
      <details v-if="draft.content.decomposition" class="comparison-preview">
        <summary>{{ $t('resultExplorer.periodChart') }}</summary>
        <div class="date-controls">
          <el-form-item :label="$t('resultExplorer.configuration.baseStart')">
            <el-input v-model="compareStart" data-testid="theme-preview-compare-start" type="date" />
          </el-form-item>
          <el-form-item :label="$t('resultExplorer.configuration.baseEnd')">
            <el-input v-model="compareEnd" data-testid="theme-preview-compare-end" type="date" />
          </el-form-item>
        </div>
        <p v-if="!compareReady" role="alert">{{ $t('resultExplorer.configuration.previewInvalid') }}</p>
      </details>
      <el-form-item :label="$t('themeConfig.metrics')" required>
        <el-select
          v-model="metrics"
          data-testid="theme-preview-metrics"
          multiple
          filterable
          :multiple-limit="5"
        >
          <el-option
            v-for="(metric, key) in draft.content.metrics"
            :key="key"
            :label="metric.label"
            :value="key"
          />
        </el-select>
      </el-form-item>
      <div class="filter-controls">
        <el-form-item :label="$t('themeConfig.filterDimension')">
          <el-select
            v-model="dimension"
            data-testid="theme-preview-dimension"
            clearable
            @change="value = ''"
          >
            <el-option
              v-for="(item, key) in draft.content.dimensions"
              :key="key"
              :label="item.label"
              :value="key"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('themeConfig.filterValue')">
          <el-select
            v-if="filter?.values && Object.keys(filter.values).length"
            v-model="value"
            data-testid="theme-preview-value"
          >
            <el-option
              v-for="(label, raw) in filter.values"
              :key="raw"
              :label="label"
              :value="raw"
            />
          </el-select>
          <el-input
            v-else
            v-model="value"
            data-testid="theme-preview-value"
            :disabled="!filter"
            :maxlength="filter?.numeric ? 15 : 256"
          />
        </el-form-item>
        <el-form-item :label="$t('themeConfig.group')">
          <el-select v-model="group" data-testid="theme-preview-group">
            <el-option value="none" :label="$t('themeConfig.noGroup')" />
            <el-option value="day" :disabled="groupedMetrics" :label="$t('themeConfig.groupDay')" />
            <el-option
              v-for="(item, key) in draft.content.dimensions"
              :key="key"
              :label="item.label"
              :value="key"
              :disabled="groupedMetrics"
            />
          </el-select>
        </el-form-item>
      </div>
      <p v-if="groupedMetrics && group !== 'none'" role="status">
        {{ $t('themeConfig.groupUnavailable') }}
      </p>
      <el-button
        type="primary"
        native-type="submit"
        data-testid="theme-preview-run"
        :disabled="!canRun"
        >{{ $t('themeConfig.runPreview') }}</el-button
      >
    </el-form>

    <div v-if="validations.length" class="receipts">
      <div class="section-header">
        <h2>{{ $t('themeConfig.receipts') }}</h2>
        <el-select
          v-model="selectedId"
          class="receipt-select"
          :aria-label="$t('themeConfig.receipts')"
        >
          <el-option
            v-for="item in validations"
            :key="item.id"
            :value="item.id"
            :label="
              $t('themeConfig.receiptOption', {
                kind: $t(
                  kind(item) === 'static' ? 'themeConfig.staticCheck' : 'themeConfig.preview'
                ),
                revision: item.revision,
                time: item.created_at
                  ? new Date(item.created_at).toLocaleString($i18n.locale)
                  : item.id,
              })
            "
          />
        </el-select>
      </div>
      <template v-if="selected">
        <p
          class="receipt-subject"
          data-testid="theme-receipt-subject"
          :data-draft-id="selected.draft_id"
          :data-revision="selected.revision"
        >
          {{
            $t('themeConfig.receiptSubject', {
              draft: $t('themeConfig.draftIdentity', {
                id: selected.draft_id,
                revision: selected.revision,
              }),
            })
          }}
        </p>
        <div class="receipt-status">
          <el-tag v-if="kind(selected) === 'static'" type="info">{{
            $t('themeConfig.staticPassed')
          }}</el-tag>
          <el-tag v-else-if="selected.receipt.execution_status === 'succeeded'" type="success">{{
            $t('themeConfig.querySucceeded')
          }}</el-tag>
          <el-tag v-else type="danger">{{ $t('themeConfig.queryFailed') }}</el-tag>
          <el-tag v-if="!isCurrent" type="warning">{{ $t('themeConfig.staleReceipt') }}</el-tag>
          <el-tag v-if="selected.checked" type="success">{{
            $t('themeConfig.businessVerified')
          }}</el-tag>
        </div>
        <p v-if="selected.receipt.plan ?? selected.receipt.intent">
          {{ (selected.receipt.plan ?? selected.receipt.intent)?.start }} –
          {{ (selected.receipt.plan ?? selected.receipt.intent)?.end }}
          <template
            v-for="(item, key) in (selected.receipt.plan ?? selected.receipt.intent)?.filters"
            :key="key"
          >
            · {{ (isCurrent && draft?.content.dimensions[key]?.label) || key }} = {{ item }}
          </template>
        </p>
        <div v-if="columns.length" class="results" data-testid="theme-preview-results">
          <table>
            <thead>
              <tr>
                <th v-for="column in columns" :key="column.key" scope="col">{{ column.label }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in rows" :key="index">
                <td v-for="column in columns" :key="column.key">
                  {{ row[column.key] ?? $t('themeConfig.nullValue') }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <el-empty
          v-else-if="selected.receipt.execution_status === 'succeeded'"
          :description="$t('themeConfig.noRows')"
        />
        <details v-if="selected.receipt.sql" class="sql">
          <summary>{{ $t('themeConfig.viewQuery') }}</summary>
          <pre>{{ selected.receipt.sql }}</pre>
        </details>
        <p v-if="selected.note">{{ selected.note }}</p>
        <p v-if="selected.quality_report_id">
          {{ t('themeQuality.reportId') }} · {{ selected.quality_report_id }}
        </p>
        <el-form
          v-if="
            isCurrent &&
            !staleDraft &&
            kind(selected) === 'preview' &&
            selected.receipt.execution_status === 'succeeded' &&
            !selected.checked
          "
          label-position="top"
          class="confirmation"
          @submit.prevent="emit('confirm', selected.id, note.trim(), qualityReportId || undefined)"
        >
          <el-form-item v-if="warningReports.length" :label="t('themeQuality.acknowledgeWarnings')">
            <el-select v-model="qualityReportId" clearable
              :aria-label="t('themeQuality.acknowledgeWarnings')"
              :placeholder="t('themeQuality.selectWarningReport')"
              data-testid="theme-preview-quality-report">
              <el-option v-for="item in warningReports" :key="item.id" :value="item.id" :label="warningReportName(item)" />
            </el-select>
            <div v-if="warningReport" class="warning-report">
              <p>{{ t('themeQuality.windowLabel', { start: warningReport.scope.start, end: warningReport.scope.end }) }}</p>
              <ul>
                <li v-for="item in warningReport.results.filter(({ status }) => status === 'warning')"
                  :key="item.rule_id + ':' + item.scope">
                  {{ item.description }} · {{ t('themeQuality.period.' + item.scope) }} · {{ item.suggestion }}
                </li>
              </ul>
            </div>
            <p v-else-if="warningError" role="alert">{{ t('themeQuality.reportFailed') }} {{ warningError }}</p>
            <p v-else-if="selectedWarning" role="status">{{ t('themeQuality.loadingReport') }}</p>
          </el-form-item>
          <el-form-item :label="$t('themeConfig.verificationNote')" required>
            <el-input
              v-model="note"
              type="textarea"
              :rows="3"
              maxlength="500"
              :placeholder="$t('themeConfig.verificationPlaceholder')"
              data-testid="theme-preview-confirmation-note"
            />
          </el-form-item>
          <el-button
            native-type="submit"
            data-testid="theme-preview-confirm"
            :disabled="busy || dirty || !note.trim() || (!!qualityReportId && warningReport?.id !== qualityReportId)"
          >
            {{ $t('themeConfig.confirmVerification') }}
          </el-button>
        </el-form>
      </template>
    </div>
  </section>
</template>

<style scoped lang="less">
.comparison-preview summary { cursor: pointer; color: #17634d; font-weight: 600; }
.comparison-preview .date-controls { margin-top: 8px; }
.comparison-preview p { color: #a52924; }
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 12px;
}
h2 {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
}
p {
  margin: 6px 0 0;
  color: var(--ed-text-color-regular);
  overflow-wrap: anywhere;
}
.preview-form {
  border: 1px solid var(--ed-border-color);
  border-radius: 6px;
  padding: 16px;
  margin-top: 12px;
}
.date-controls,
.filter-controls {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(175px, 100%), 1fr));
  gap: 0 12px;
}
.ed-select {
  width: 100%;
}
.receipts {
  margin-top: 24px;
}
.receipt-select {
  width: min(360px, 100%);
}
.receipt-subject {
  margin-bottom: 8px;
}
.receipt-status {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.results {
  max-width: 100%;
  overflow: auto;
  margin-top: 12px;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  text-align: left;
  padding: 10px;
  border: 1px solid var(--ed-border-color-lighter);
}
th {
  background: var(--ed-fill-color-light);
}
.sql,
.warning-report { overflow-wrap: anywhere; }
.warning-report ul { margin: 4px 0; padding-inline-start: 20px; }
.warning-report p { margin: 4px 0; }
.confirmation {
  margin-top: 16px;
}
summary {
  cursor: pointer;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
