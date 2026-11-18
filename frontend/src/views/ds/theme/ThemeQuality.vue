<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  themeApi,
  themeError,
  type ThemeQualityReport as Report,
  type ThemeQualitySummary,
  type ThemeQualityTarget,
  type ThemeQualityWindow,
  type ThemeDetail,
  type TopicSemantics,
} from '@/api/theme'
import ThemeQualityReport from './ThemeQualityReport.vue'
import ThemeQualityRules from './ThemeQualityRules.vue'

const props = defineProps<{
  theme: ThemeDetail
  target: ThemeQualityTarget
  content: TopicSemantics
  persisted: TopicSemantics
  editable: boolean
  dirty: boolean
  stale: boolean
  reports: ThemeQualitySummary[]
  historyLoading: boolean
  historyError: string
}>()
const emit = defineEmits<{ edit: [content: TopicSemantics]; checked: [report: Report] }>()
const { t, locale } = useI18n()
const scopeWindow = ref<ThemeQualityWindow>({ start: '', end: '' })
const selectedId = ref('')
const report = shallowRef<Report>()
const error = ref('')
const errorDetail = ref('')
const loadingReport = ref(false)
const checking = ref(false)
const exporting = ref(false)
let reportRequest = 0
let pendingCheck: { fingerprint: string; id: string } | undefined

const isDraft = computed(() => 'draft_id' in props.target)
const draftUnready = computed(() => isDraft.value && (props.dirty || props.stale || !props.theme.draft))
const canCheck = computed(() => !!props.persisted.quality?.rules.length && !draftUnready.value)
const adoptedId = computed(() => !isDraft.value && props.theme.publications.find(
  (item) => (item.kind ?? item.action) === 'publish' && item.version === props.theme.active_version
)?.quality_report_id)

watch(() => props.persisted, (value) => {
  scopeWindow.value = { start: value.start, end: value.end }
  pendingCheck = undefined
}, { immediate: true })

watch(() => props.reports, (reports) => {
  if (!selectedId.value || !reports.some(({ id }) => id === selectedId.value)) {
    selectedId.value = reports[0]?.id ?? ''
  }
}, { immediate: true })

function showError(cause: unknown, key: string) {
  error.value = key
  errorDetail.value = themeError(cause).detail
}

watch(selectedId, async (id) => {
  const request = ++reportRequest
  error.value = ''
  if (report.value && report.value.id === id) return
  report.value = undefined
  loadingReport.value = !!id
  if (!id) return
  try {
    const result = await themeApi.getQuality(props.theme.id, id)
    if (request === reportRequest) report.value = result
  } catch (cause) {
    if (request === reportRequest) showError(cause, 'reportFailed')
  } finally {
    if (request === reportRequest) loadingReport.value = false
  }
}, { immediate: true })

onBeforeUnmount(() => { reportRequest += 1 })

function optionLabel(item: ThemeQualitySummary) {
  return [
    new Date(item.completed_at).toLocaleString(locale.value),
    t('themeQuality.status.' + item.status),
    item.draft_id ? t('themeConfig.revision', { revision: item.revision }) : item.version,
  ].filter(Boolean).join(' · ')
}

async function runCheck() {
  if (!canCheck.value || checking.value) return
  const value = scopeWindow.value
  const { start, end, compare_start, compare_end, as_of } = value
  if (
    !start || !end || start >= end ||
    ((compare_start || compare_end) && (!compare_start || !compare_end || compare_start >= compare_end ||
      (start < compare_end && compare_start < end)))
  ) {
    error.value = 'invalidWindow'
    errorDetail.value = ''
    return
  }
  const reference = 'version' in props.target
    ? props.target
    : { ...props.target, revision: props.theme.draft!.revision }
  const scope = {
    start, end, compare_start: compare_start || undefined, compare_end: compare_end || undefined,
    as_of: as_of || undefined,
  }
  const fingerprint = JSON.stringify({ ...reference, ...scope })
  if (pendingCheck?.fingerprint !== fingerprint) {
    pendingCheck = { fingerprint, id: crypto.randomUUID() }
  }
  const request = ++reportRequest
  error.value = ''
  loadingReport.value = false
  checking.value = true
  try {
    const result = await themeApi.checkQuality(props.theme.id, {
      ...reference, ...scope, request_id: pendingCheck.id,
    })
    if (request !== reportRequest) return
    report.value = result
    selectedId.value = result.id
    pendingCheck = undefined
    emit('checked', result)
  } catch (cause) {
    if (request === reportRequest) showError(cause, 'checkFailed')
  } finally {
    checking.value = false
  }
}

async function exportReport() {
  const id = report.value?.id
  if (!id || exporting.value) return
  const request = reportRequest
  exporting.value = true
  try {
    const result = await themeApi.getQuality(props.theme.id, id)
    if (request !== reportRequest) return
    const file = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(file)
    const link = document.createElement('a')
    link.href = url
    link.download = 'quality-' + id + '.json'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (cause) {
    if (request === reportRequest) {
      report.value = undefined
      showError(cause, 'reportFailed')
    }
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <section class="theme-quality" data-testid="theme-quality">
    <div class="quality-heading">
      <div>
        <h2>{{ t('themeQuality.checkTitle') }}</h2>
        <p>{{ t('themeQuality.sourceView') }} · {{ theme.view }} · {{ persisted.date_field }}</p>
      </div>
      <div class="history">
        <label>
          <span>{{ t('themeQuality.history') }}</span>
          <el-select v-model="selectedId" :loading="historyLoading" :disabled="checking || !reports.length"
            :placeholder="t('themeQuality.noReports')" :aria-label="t('themeQuality.history')"
            data-testid="theme-quality-history">
            <el-option v-for="item in reports" :key="item.id" :value="item.id" :label="optionLabel(item)" />
          </el-select>
        </label>
      </div>
    </div>
    <p v-if="adoptedId">{{ t('themeQuality.adopted', { id: adoptedId }) }}</p>
    <p v-if="historyError" role="alert">{{ t('themeQuality.historyFailed') }} {{ historyError }}</p>
    <form class="check-form" @submit.prevent="runCheck">
      <div class="check-fields">
        <label>
          <span>{{ t('themeQuality.start') }}</span>
          <input v-model="scopeWindow.start" required type="date" data-testid="theme-quality-start" />
        </label>
        <label>
          <span>{{ t('themeQuality.end') }}</span>
          <input v-model="scopeWindow.end" required type="date" data-testid="theme-quality-end" />
        </label>
        <label>
          <span>{{ t('themeQuality.compareStart') }}</span>
          <input v-model="scopeWindow.compare_start" type="date" data-testid="theme-quality-baseline-start" />
        </label>
        <label>
          <span>{{ t('themeQuality.compareEnd') }}</span>
          <input v-model="scopeWindow.compare_end" type="date" data-testid="theme-quality-baseline-end" />
        </label>
        <label v-if="persisted.quality?.source_mode === 'rolling'">
          <span>{{ t('themeQuality.checkedOn') }}</span>
          <input v-model="scopeWindow.as_of" type="date" data-testid="theme-quality-as-of" />
        </label>
        <el-button native-type="submit" type="primary" :loading="checking" :disabled="!canCheck"
          data-testid="theme-quality-check">
          {{ t(reports.length ? 'themeQuality.recheck' : 'themeQuality.check') }}
        </el-button>
      </div>
      <p v-if="draftUnready && dirty">{{ t('themeQuality.saveBeforeCheck') }}</p>
    </form>
    <el-alert v-if="error" type="error" :title="t('themeQuality.' + error)" :description="errorDetail" :closable="false" />
    <p v-if="loadingReport" role="status">{{ t('themeQuality.loadingReport') }}</p>
    <ThemeQualityReport v-if="report" :report="report" :content="persisted" @export="exportReport" />
    <ThemeQualityRules :content="content" :fields="theme.fields" :editable="editable && !checking" @edit="emit('edit', $event)" />
  </section>
</template>

<style scoped lang="less">
.theme-quality { display: grid; gap: 10px; min-width: 0; overflow-wrap: anywhere; }
.quality-heading { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
h2 { margin: 0; font-size: 16px; }
p { margin: 4px 0 0; color: var(--ed-text-color-regular); }
.history { flex: 0 1 min(320px, 100%); min-width: 0; }
.history label, .check-fields label { display: grid; gap: 4px; min-width: 0; }
.history :deep(.ed-select) { width: 100%; }
.check-form { padding: 10px 12px; border: 1px solid var(--ed-border-color-lighter); border-radius: 6px; }
.check-fields { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end; }
.check-fields label { flex: 0 1 152px; max-width: 100%; }
.check-fields input {
  min-height: 32px; min-width: 0; padding: 2px 8px; font: inherit; color: var(--ed-text-color-primary);
  background: var(--ed-bg-color); border: 1px solid var(--ed-border-color); border-radius: 4px;
}
</style>
