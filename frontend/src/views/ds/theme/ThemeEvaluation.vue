<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus-secondary'
import { useI18n } from 'vue-i18n'
import { themeApi, themeError, type ThemeDetail } from '@/api/theme'
import type { RequestError } from '@/utils/request'
import {
  evaluationValue,
  themeEvaluationApi as api,
  type EvaluationBatch,
  type EvaluationComparison,
  type EvaluationLaunch,
  type EvaluationOptions,
  type EvaluationRow,
  type EvaluationSuite,
  type EvaluationSuiteInput,
} from '@/api/themeEvaluation'
import ThemeEvaluationSuite from './ThemeEvaluationSuite.vue'
import ThemeEvaluationReceipt from './ThemeEvaluationReceipt.vue'

const props = defineProps<{
  theme: ThemeDetail
  dirty: boolean
  stale: boolean
  refreshToken: number
}>()
const { t, te, locale } = useI18n()
const tr = (key: string) => t('themeEvaluation.' + key)
const suites = shallowRef<EvaluationSuite[]>([])
const batches = shallowRef<EvaluationBatch[]>([])
const options = shallowRef<EvaluationOptions>()
const selectedSuite = ref('')
const selectedBatch = ref('')
const selectedBaseline = ref('')
const batch = shallowRef<EvaluationBatch>()
const comparison = shallowRef<EvaluationComparison>()
const suiteEditor = shallowRef<EvaluationSuite>()
const launchSuite = shallowRef<EvaluationSuite>()
const suiteOpen = ref(false)
const launchOpen = ref(false)
const rowId = ref('')
const rowOpen = ref(false)
const importRunOpen = ref(false)
const recordId = ref('')
const rowSuites = shallowRef<EvaluationSuite[]>([])
const rowLoading = ref(false)
const actionError = ref('')
const statusFilter = ref('')
const categoryFilter = ref('')
const listError = ref('')
const reportError = ref('')
const loading = ref(false)
const loadingReport = ref(false)
const busy = ref(false)
const label = ref('')
const budget = ref(1)
const caseIds = ref<string[]>([])
const versions = ref<Record<string, string>>({})
const parentId = ref<string>()
let listSerial = 0
let reportSerial = 0
let rowSerial = 0
let disposed = false
let timer: ReturnType<typeof setTimeout> | undefined
let pending: { key: string; id: string } | undefined

const suite = computed(() => suites.value.find(({ id }) => id === selectedSuite.value))
const canManage = computed(() => !!options.value && options.value.can_manage !== false)
const canRun = computed(() => !!options.value?.identity && options.value.can_run !== false)
const baseline = computed(() => comparison.value?.baseline)
const candidate = computed(() => comparison.value?.candidate ?? batch.value)
const cases = computed(() => launchSuite.value?.cases ?? [])
const chosenCases = computed(() => cases.value.filter((item) => caseIds.value.includes(item.id)))
const plannedSubmissions = computed(() =>
  chosenCases.value.reduce((sum, item) => sum + item.turns.length, 0)
)
const targets = computed(() =>
  (options.value?.datasources ?? []).filter(({ id }) =>
    chosenCases.value.some((item) => item.datasource_id === id)
  )
)
const canLaunch = computed(() => {
  const limit = options.value?.budget_limit ?? 0
  return (
    canRun.value &&
    !!label.value.trim() &&
    chosenCases.value.length > 0 &&
    Number.isInteger(budget.value) &&
    budget.value >= 1 &&
    budget.value <= limit &&
    chosenCases.value.every((item) =>
      targets.value.some(
        ({ id, versions: choices }) =>
          id === item.datasource_id && choices.some((choice) => choice.id === versions.value[id])
      )
    )
  )
})
const canFreeze = computed(
  () => canManage.value && !!props.theme.draft && !props.dirty && !props.stale
)
const active = (value?: EvaluationBatch) =>
  value?.status === 'pending' || value?.status === 'running'
const counts = ['planned', 'executed', 'scored', 'first_passed', 'failed', 'review'] as const
const otherCounts = [
  'unexecuted',
  'indeterminate',
  'system_error',
  'evaluator_error',
  'interrupted',
] as const
const currentRowStatus = (row?: EvaluationRow | null) =>
  row?.latest?.judgement?.status ?? row?.status ?? 'not_run'
const compareOptions = computed(() => batches.value.filter(({ id }) => id !== selectedBatch.value))
const recentSuiteBatch = computed(() =>
  batches.value.find(({ suite_id }) => suite_id === selectedSuite.value)
)
const coverageCategories = computed(() => suite.value?.categories ?? [])
const reportRows = computed(
  () =>
    comparison.value?.rows?.map((row) => ({
      ...row,
      status: row.status,
      source: row.candidate ? candidate.value : baseline.value,
    })) ??
    (batch.value?.rows ?? []).map((row) => ({
      case_id: row.case_id,
      question: row.question,
      category: row.category,
      status: currentRowStatus(row),
      baseline: null,
      candidate: row,
      source: batch.value,
    }))
)
const statuses = computed(() => [...new Set(reportRows.value.map(({ status }) => status))])
const categories = computed(() => [...new Set(reportRows.value.map(({ category }) => category))])
const filteredRows = computed(() =>
  reportRows.value.filter(
    (row) =>
      (!statusFilter.value || row.status === statusFilter.value) &&
      (!categoryFilter.value || row.category === categoryFilter.value)
  )
)
const detail = computed(() => reportRows.value.find(({ case_id }) => case_id === rowId.value))
const lookupCase = (suiteId?: string) =>
  rowSuites.value.find(({ id }) => id === suiteId)?.cases?.find(({ id }) => id === rowId.value)
const finishStatuses = [
  'failed',
  'review',
  'unjudgeable',
  'indeterminate',
  'system_error',
  'evaluator_error',
  'interrupted',
]
const canDraft = computed(
  () =>
    canManage.value &&
    !!detail.value?.source &&
    [detail.value.candidate, detail.value.baseline].some(
      (row) => row && finishStatuses.includes(currentRowStatus(row))
    )
)
const failureBatch = computed(() => {
  if (detail.value?.candidate && finishStatuses.includes(currentRowStatus(detail.value.candidate)))
    return candidate.value
  if (detail.value?.baseline && finishStatuses.includes(currentRowStatus(detail.value.baseline)))
    return baseline.value
  return undefined
})

function textFor(prefix: string, value?: string) {
  if (!value) return '—'
  const key = 'themeEvaluation.' + prefix + '.' + value
  return te(key) ? t(key) : value
}

function errorText(error: unknown) {
  const parsed = themeError(error)
  if (parsed.status === 401 || parsed.status === 403) return tr('accessDenied')
  if (parsed.detail) return textFor('reason', parsed.detail)
  const validation = (error as RequestError<{ detail?: unknown }> | undefined)?.response?.data
    ?.detail
  if (Array.isArray(validation)) {
    const reason = validation[0]?.ctx?.error
    if (typeof reason === 'string') return textFor('reason', reason)
    return tr('invalidInput')
  }
  return tr('requestFailed')
}

function statusType(status: string) {
  if (['passed', 'confirmed', 'improved', 'both_passed'].includes(status)) return 'success'
  if (['failed', 'regressed', 'both_failed', 'system_error', 'evaluator_error'].includes(status))
    return 'danger'
  if (['review', 'stopped', 'incomparable', 'interrupted'].includes(status)) return 'warning'
  return 'info'
}

function date(value?: string) {
  if (!value) return ''
  const result = new Date(value)
  return Number.isNaN(result.valueOf()) ? value : result.toLocaleString(locale.value)
}

function batchLabel(item: EvaluationBatch) {
  return [item.label || item.id, date(item.created_at), textFor('status', item.status)]
    .filter(Boolean)
    .join(' · ')
}

function requestId(key: string) {
  if (pending?.key !== key) {
    const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
      value.toString(16).padStart(2, '0')
    ).join('')
    pending = { key, id }
  }
  return pending.id
}

function protectionClosure(ids: string[]) {
  const selected = new Set(ids)
  for (const id of selected) {
    cases.value
      .find((item) => item.id === id)
      ?.protects?.forEach((related) => selected.add(related))
  }
  return [...selected]
}

function selectCases(ids: (string | number)[]) {
  caseIds.value = protectionClosure(ids.map(String))
}

function storeBatch(value: EvaluationBatch) {
  const index = batches.value.findIndex(({ id }) => id === value.id)
  if (index === -1) {
    batches.value = [value, ...batches.value]
  } else {
    batches.value = batches.value.map((item, i) => (i === index ? value : item))
  }
}

async function refreshList() {
  const serial = ++listSerial
  listError.value = ''
  loading.value = true
  try {
    const result = await api.list(props.theme.id)
    if (serial !== listSerial) return
    ;({ suites: suites.value, batches: batches.value, options: options.value } = result)
    if (!suites.value.some(({ id }) => id === selectedSuite.value))
      selectedSuite.value = suites.value[0]?.id ?? ''
    if (!batches.value.some(({ id }) => id === selectedBaseline.value)) selectedBaseline.value = ''
    if (!batches.value.some(({ id }) => id === selectedBatch.value))
      selectedBatch.value = batches.value[0]?.id ?? ''
  } catch (error) {
    if (serial !== listSerial) return
    listError.value = errorText(error)
    suites.value = []
    batches.value = []
    options.value = undefined
    selectedSuite.value = ''
    selectedBatch.value = ''
    selectedBaseline.value = ''
    rowOpen.value = false
    importRunOpen.value = false
    suiteOpen.value = false
    launchOpen.value = false
  } finally {
    if (serial === listSerial) loading.value = false
  }
}

async function readReport(poll = false) {
  const id = selectedBatch.value
  const compareId = selectedBaseline.value
  const serial = ++reportSerial
  clearTimeout(timer)
  if (!id) return
  loadingReport.value = !poll
  reportError.value = ''
  try {
    if (compareId) {
      const result = await api.compare(props.theme.id, compareId, id)
      if (serial !== reportSerial) return
      comparison.value = result
      batch.value = result.candidate
      storeBatch(result.baseline)
    } else {
      const result = await api.batch(props.theme.id, id)
      if (serial !== reportSerial) return
      batch.value = result
    }
    if (batch.value) storeBatch(batch.value)
    if (active(batch.value) || active(comparison.value?.baseline)) {
      timer = setTimeout(() => void readReport(true), 2000)
    }
  } catch (error) {
    if (serial === reportSerial) {
      reportError.value = errorText(error)
      batch.value = undefined
      comparison.value = undefined
      rowOpen.value = false
    }
  } finally {
    if (serial === reportSerial) loadingReport.value = false
  }
}

watch(
  () => props.refreshToken,
  () => void refreshList(),
  { immediate: true }
)
watch([selectedBatch, selectedBaseline], () => {
  if (selectedBatch.value === selectedBaseline.value) selectedBaseline.value = ''
  batch.value = undefined
  comparison.value = undefined
  statusFilter.value = ''
  categoryFilter.value = ''
  rowOpen.value = false
  void readReport()
})
watch(rowOpen, (open) => {
  if (!open) {
    rowSerial += 1
    rowSuites.value = []
  }
})
onBeforeUnmount(() => {
  listSerial += 1
  reportSerial += 1
  rowSerial += 1
  disposed = true
  clearTimeout(timer)
})

async function action(run: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  actionError.value = ''
  try {
    await run()
    pending = undefined
  } catch (error) {
    actionError.value = errorText(error)
    ElMessage.error(actionError.value)
    if ([401, 403].includes(themeError(error).status ?? 0)) {
      rowOpen.value = false
      void refreshList()
    }
  } finally {
    busy.value = false
  }
}

async function confirm(message: string, title: string) {
  try {
    await ElMessageBox.confirm(tr(message), tr(title), {
      type: 'warning',
      confirmButtonText: tr(title),
      cancelButtonText: t('themeConfig.cancel'),
    })
    return true
  } catch {
    return false
  }
}

function openSuite(id?: string) {
  void action(async () => {
    suiteEditor.value = id ? await api.suite(props.theme.id, id) : undefined
    suiteOpen.value = true
  })
}

function saveSuite(payload: EvaluationSuiteInput) {
  void action(async () => {
    const value = await api.save(props.theme.id, {
      ...payload,
      request_id: requestId(JSON.stringify(payload)),
    })
    await refreshList()
    selectedSuite.value = value.id
    suiteOpen.value = false
  })
}

function openLaunch(id: string, parent?: EvaluationBatch, only?: string) {
  void action(async () => {
    const value = await api.suite(props.theme.id, id)
    launchSuite.value = value
    label.value = parent ? t('themeEvaluation.retryLabel', { label: parent.label }) : value.title
    caseIds.value = only ? protectionClosure([only]) : (value.cases?.map(({ id }) => id) ?? [])
    budget.value = Math.max(1, Math.min(plannedSubmissions.value, options.value?.budget_limit ?? 1))
    parentId.value = parent?.id
    versions.value = Object.fromEntries(
      (options.value?.datasources ?? []).map((item) => [
        item.id,
        item.versions.find(
          ({ id }) =>
            parent?.context.versions &&
            (parent.context.versions as Record<string, unknown>)[item.id] === id
        )?.id ??
          item.active_version ??
          item.versions[0]?.id ??
          '',
      ])
    )
    rowOpen.value = false
    launchOpen.value = true
  })
}

function launch() {
  if (!canLaunch.value || !launchSuite.value) return
  const payload: EvaluationLaunch = {
    suite_id: launchSuite.value.id,
    budget: budget.value,
    label: label.value.trim(),
    parent_batch_id: parentId.value,
    case_ids: caseIds.value,
    versions: Object.fromEntries(targets.value.map(({ id }) => [id, versions.value[id]!])),
  }
  void action(async () => {
    const result = await api.launch(props.theme.id, {
      ...payload,
      request_id: requestId(JSON.stringify(payload)),
    })
    launchOpen.value = false
    rowOpen.value = false
    storeBatch(result)
    selectedBaseline.value = ''
    if (selectedBatch.value === result.id) await readReport()
    else selectedBatch.value = result.id
  })
}

async function freeze() {
  const draft = props.theme.draft
  if (
    !canFreeze.value ||
    !draft ||
    !(await confirm('freezePrompt', 'freezeConfig')) ||
    disposed ||
    !canFreeze.value ||
    props.theme.draft !== draft
  )
    return
  void action(async () => {
    const { id: draft_id, revision } = draft
    const existing = props.theme.validations.find(
      (item) =>
        item.draft_id === draft_id &&
        item.revision === revision &&
        (item.kind ?? item.receipt.kind) === 'static'
    )
    const receipt = existing ?? (await themeApi.validate(props.theme.id, { draft_id, revision }))
    if (disposed) return
    const key = JSON.stringify(['freeze', draft_id, revision])
    const result = await api.freeze(props.theme.id, {
      draft_id,
      revision,
      validation_id: receipt.id,
      request_id: requestId(key),
    })
    await refreshList()
    ElMessage.success(t('themeEvaluation.configFrozen', { version: result.label || result.id }))
  })
}

async function control(kind: 'stop' | 'resume') {
  const value = batch.value
  if (
    !value ||
    !(await confirm(kind === 'stop' ? 'stopPrompt' : 'resumePrompt', kind)) ||
    disposed ||
    value.id !== batch.value?.id
  )
    return
  void action(async () => {
    await api.control(props.theme.id, value.id, kind, requestId(kind + value.id))
    if (selectedBatch.value === value.id) await readReport()
  })
}

async function showDetails(id: string) {
  const serial = ++rowSerial
  rowId.value = id
  rowOpen.value = true
  rowSuites.value = []
  rowLoading.value = true
  const ids = [
    ...new Set(
      [baseline.value?.suite_id, candidate.value?.suite_id].filter((item): item is string => !!item)
    ),
  ]
  try {
    const results = await Promise.all(ids.map((suiteId) => api.suite(props.theme.id, suiteId)))
    if (serial === rowSerial) rowSuites.value = results
  } catch (error) {
    if (serial === rowSerial) {
      rowOpen.value = false
      ElMessage.error(errorText(error))
    }
  } finally {
    if (serial === rowSerial) rowLoading.value = false
  }
}

function createDraft() {
  const id = failureBatch.value?.id
  if (!id || !detail.value) return
  const payload = { batch_id: id, case_id: detail.value.case_id }
  createDraftFrom(payload)
}

function openRunImport() {
  recordId.value = ''
  actionError.value = ''
  importRunOpen.value = true
}

function createDraftFrom(payload: { batch_id: string; case_id: string } | { record_id: string }) {
  void action(async () => {
    const draft = await api.draftFromRun(props.theme.id, {
      ...payload,
      request_id: requestId(JSON.stringify(payload)),
    })
    await refreshList()
    selectedSuite.value = draft.id
    rowOpen.value = false
    importRunOpen.value = false
    suiteEditor.value = draft.cases ? draft : await api.suite(props.theme.id, draft.id)
    suiteOpen.value = true
  })
}

function download() {
  const id = batch.value?.id
  if (!id) return
  const baseline = selectedBaseline.value
  void action(async () => {
    const file = baseline
      ? new Blob([JSON.stringify(await api.compare(props.theme.id, baseline, id), null, 2)], {
          type: 'application/json',
        })
      : await api.export(props.theme.id, id)
    if (disposed) return
    const url = URL.createObjectURL(file)
    const link = document.createElement('a')
    link.href = url
    link.download = (baseline ? 'evaluation-' + baseline + '-vs-' : 'evaluation-') + id + '.json'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  })
}
</script>

<template>
  <section class="evaluation" data-testid="theme-evaluation">
    <el-alert v-if="listError" :title="listError" type="error" :closable="false" />
    <template v-if="!listError">
      <section v-loading="loading" class="panel suites-panel">
        <header class="section-heading">
          <div>
            <h2>{{ tr('suites') }}</h2>
            <p>{{ tr('suiteIntroduction') }}</p>
          </div>
          <div v-if="canManage" class="actions">
            <el-button :disabled="busy" @click="openRunImport">
              {{ tr('draftFromAnalysis') }}
            </el-button>
            <el-button
              :disabled="busy || !canFreeze"
              :title="!canFreeze ? tr('freezeRequiresDraft') : undefined"
              @click="freeze"
              >{{ tr('freezeConfig') }}</el-button
            >
            <el-button :disabled="busy" @click="openSuite()">{{ tr('newSuite') }}</el-button>
          </div>
        </header>
        <div v-if="suite" class="suite-summary">
          <div class="selection">
            <label
              >{{ tr('suiteVersion') }}
              <el-select v-model="selectedSuite" :aria-label="tr('suiteVersion')">
                <el-option
                  v-for="item in suites"
                  :key="item.id"
                  :value="item.id"
                  :label="
                    [
                      item.title,
                      t('themeEvaluation.version', { version: item.revision }),
                      textFor('status', item.status),
                    ].join(' · ')
                  "
                />
              </el-select>
            </label>
          </div>
          <dl>
            <div>
              <dt>{{ tr('cases') }}</dt>
              <dd>{{ suite.case_count }}</dd>
            </div>
            <div>
              <dt>{{ tr('suiteStatus') }}</dt>
              <dd>
                <el-tag :type="statusType(suite.status)">{{
                  textFor('status', suite.status)
                }}</el-tag>
              </dd>
            </div>
            <div v-if="recentSuiteBatch">
              <dt>{{ tr('recentBatch') }}</dt>
              <dd>{{ recentSuiteBatch.label }}</dd>
            </div>
          </dl>
          <div class="actions">
            <el-button :disabled="busy" @click="openSuite(suite.id)">{{
              tr('suiteDetails')
            }}</el-button>
            <el-button
              type="primary"
              :disabled="busy || !canRun || suite.status !== 'confirmed'"
              @click="openLaunch(suite.id)"
              >{{ tr('start') }}</el-button
            >
          </div>
        </div>
        <p v-if="suite?.description" class="suite-purpose">{{ suite.description }}</p>
        <p v-if="coverageCategories.length">
          {{ tr('category') }} · {{ coverageCategories.join(' · ') }}
        </p>
        <p v-if="!loading && !suite" class="empty">{{ tr('noSuites') }}</p>
        <p v-if="options && !options.identity" class="suite-purpose" role="status">
          {{ tr('identityUnavailable') }}
        </p>
      </section>

      <section class="report-panel" :aria-label="tr('reports')">
        <header class="section-heading">
          <div>
            <h2>{{ tr('reports') }}</h2>
            <p v-if="batch">
              {{ batch.label }} · {{ textFor('status', batch.status)
              }}<template v-if="batch.created_at"> · {{ date(batch.created_at) }}</template>
            </p>
          </div>
          <div v-if="batch" class="actions">
            <el-button v-if="canRun && active(batch)" :disabled="busy" @click="control('stop')">{{
              tr('stop')
            }}</el-button>
            <el-button
              v-if="canRun && batch.status === 'stopped'"
              :disabled="busy"
              @click="control('resume')"
              >{{ tr('resume') }}</el-button
            >
            <el-button
              v-if="canRun && !active(batch)"
              :disabled="busy"
              @click="openLaunch(batch.suite_id, batch)"
              >{{ tr('rerun') }}</el-button
            >
            <el-button :disabled="busy || active(batch) || active(baseline)" @click="download">{{
              tr('export')
            }}</el-button>
          </div>
        </header>
        <div v-if="batches.length" class="report-controls">
          <label
            >{{ tr('currentBatch') }}
            <el-select v-model="selectedBatch" :aria-label="tr('currentBatch')">
              <el-option
                v-for="item in batches"
                :key="item.id"
                :value="item.id"
                :label="batchLabel(item)"
              />
            </el-select>
          </label>
          <label
            >{{ tr('baseline') }}
            <el-select
              v-model="selectedBaseline"
              clearable
              :placeholder="tr('noComparison')"
              :aria-label="tr('baseline')"
            >
              <el-option
                v-for="item in compareOptions"
                :key="item.id"
                :value="item.id"
                :label="batchLabel(item)"
              />
            </el-select>
          </label>
        </div>
        <p v-if="loadingReport" role="status">{{ tr('loadingReport') }}</p>
        <el-alert v-if="reportError" :title="reportError" type="error" :closable="false">
          <el-button @click="readReport()">{{ $t('themeConfig.refresh') }}</el-button>
        </el-alert>
        <p v-if="!loading && !batches.length" class="empty panel">{{ tr('noBatches') }}</p>
        <template v-if="batch">
          <dl class="scoreboard" :aria-label="tr('progress')">
            <div v-for="key in counts" :key="key" :class="{ primary: key === 'first_passed' }">
              <dt>{{ tr('counts.' + key) }}</dt>
              <dd>
                {{ batch.counts[key] ?? '—'
                }}<span v-if="key === 'first_passed'"> / {{ batch.counts.planned ?? '—' }}</span>
              </dd>
            </div>
          </dl>
          <dl class="other-counts">
            <div v-for="key in otherCounts" :key="key">
              <dt>{{ tr('counts.' + key) }}</dt>
              <dd>{{ batch.counts[key] ?? '—' }}</dd>
            </div>
          </dl>
          <p v-if="batch.stop_reason" role="status">
            {{ tr('stopReason') }} · {{ textFor('reason', batch.stop_reason) }}
          </p>
          <p v-if="batch.parent_batch_id">
            {{ tr('parentBatch') }} ·
            {{
              batches.find(({ id }) => id === batch?.parent_batch_id)?.label ??
              batch.parent_batch_id
            }}
          </p>
          <details class="panel">
            <summary>{{ tr('runContext') }}</summary>
            <dl class="run-details">
              <div>
                <dt>{{ tr('plannedSubmissions') }}</dt>
                <dd>{{ batch.planned_submissions }}</dd>
              </div>
              <div>
                <dt>{{ tr('modelCalls') }}</dt>
                <dd>{{ batch.counts.model_calls ?? '—' }} / {{ batch.budget }}</dd>
              </div>
              <div v-for="(value, key) in batch.context" :key="key">
                <dt>{{ textFor('context', key) }}</dt>
                <dd>{{ evaluationValue(value) }}</dd>
              </div>
            </dl>
          </details>
          <div v-if="comparison" class="comparison" data-testid="evaluation-comparison">
            <el-tag :type="comparison.comparable ? 'success' : 'warning'">{{
              tr(comparison.comparable ? 'comparable' : 'incomparable')
            }}</el-tag>
            <p>{{ baseline?.label }} → {{ candidate?.label }}</p>
            <details
              v-if="comparison.differences?.length"
              class="comparison-diff"
              :open="!comparison.comparable"
            >
              <summary>{{ tr('comparisonConditions') }}</summary>
              <dl>
                <div v-for="item in comparison.differences" :key="item.field">
                  <dt>
                    {{ textFor('context', item.field) }} ·
                    {{ tr(item.allowed ? 'allowedDifference' : 'blockedDifference') }}
                  </dt>
                  <dd v-if="item.field !== 'configurations'">
                    {{ evaluationValue(item.baseline) }} → {{ evaluationValue(item.candidate) }}
                  </dd>
                </div>
              </dl>
            </details>
          </div>
          <section class="results">
            <header class="section-heading">
              <h3>{{ tr('caseResults') }}</h3>
              <div class="filters">
                <el-select
                  v-model="statusFilter"
                  clearable
                  :placeholder="tr('allStatuses')"
                  :aria-label="tr('statusFilter')"
                >
                  <el-option
                    v-for="status in statuses"
                    :key="status"
                    :value="status"
                    :label="textFor('status', status)"
                  />
                </el-select>
                <el-select
                  v-model="categoryFilter"
                  clearable
                  :placeholder="tr('allCategories')"
                  :aria-label="tr('category')"
                >
                  <el-option
                    v-for="category in categories"
                    :key="category"
                    :value="category"
                    :label="category"
                  />
                </el-select>
              </div>
            </header>
            <table v-if="filteredRows.length" :class="{ paired: comparison }">
              <thead>
                <tr>
                  <th scope="col">{{ tr('question') }}</th>
                  <th v-if="comparison" scope="col">{{ tr('baselineResult') }}</th>
                  <th scope="col">{{ tr(comparison ? 'candidateResult' : 'first') }}</th>
                  <th v-if="!comparison" scope="col">{{ tr('latest') }}</th>
                  <th scope="col">{{ tr('finding') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in filteredRows" :key="row.case_id" :data-case-id="row.case_id">
                  <th scope="row">
                    <div class="case-label">
                      {{ row.case_id }} <small>{{ row.category }}</small>
                    </div>
                    <span>{{ row.question }}</span>
                  </th>
                  <td v-if="comparison">
                    <el-tag :type="statusType(currentRowStatus(row.baseline))">{{
                      textFor('status', currentRowStatus(row.baseline))
                    }}</el-tag>
                  </td>
                  <td v-if="comparison">
                    <el-tag :type="statusType(currentRowStatus(row.candidate))">{{
                      textFor('status', currentRowStatus(row.candidate))
                    }}</el-tag>
                  </td>
                  <td v-else>
                    <el-tag
                      :type="statusType(row.candidate?.first?.judgement?.status ?? row.status)"
                      >{{
                        textFor(
                          'status',
                          row.candidate?.first?.judgement?.status ??
                            row.candidate?.first?.status ??
                            row.status
                        )
                      }}</el-tag
                    >
                  </td>
                  <td v-if="!comparison">
                    <el-tag :type="statusType(currentRowStatus(row.candidate))">{{
                      textFor('status', currentRowStatus(row.candidate))
                    }}</el-tag>
                  </td>
                  <td>
                    <el-tag v-if="comparison" :type="statusType(row.status)">{{
                      textFor('status', row.status)
                    }}</el-tag>
                    <p
                      v-if="row.candidate?.latest?.judgement?.issues?.[0]?.detail"
                      class="issue-preview"
                    >
                      {{ textFor('reason', row.candidate.latest.judgement.issues[0].detail) }}
                    </p>
                    <el-button
                      link
                      type="primary"
                      :aria-label="t('themeEvaluation.caseDetails', { id: row.case_id })"
                      @click="showDetails(row.case_id)"
                      >{{ tr('details') }}</el-button
                    >
                  </td>
                </tr>
              </tbody>
            </table>
            <p v-else class="empty">{{ tr('noMatches') }}</p>
          </section>
        </template>
      </section>
    </template>

    <ThemeEvaluationSuite
      v-model="suiteOpen"
      :suite="suiteEditor"
      :datasources="options?.datasources ?? []"
      :fixtures="options?.fixtures ?? ['clean']"
      :busy="busy"
      :writable="canManage"
      @save="saveSuite"
    />

    <el-dialog
      v-model="importRunOpen"
      :title="tr('draftFromAnalysis')"
      width="min(440px, calc(100% - 24px))"
    >
      <form
        id="theme-evaluation-import-run"
        class="drawer-form"
        @submit.prevent="createDraftFrom({ record_id: recordId.trim() })"
      >
        <label
          >{{ tr('recordId') }}<el-input v-model="recordId" inputmode="numeric" maxlength="19"
        /></label>
        <p>{{ tr('draftFromAnalysisHint') }}</p>
        <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" />
      </form>
      <template #footer>
        <el-button
          type="primary"
          form="theme-evaluation-import-run"
          native-type="submit"
          :disabled="!/^\d{1,19}$/.test(recordId.trim())"
          :loading="busy"
          >{{ tr('createDraft') }}</el-button
        >
      </template>
    </el-dialog>

    <el-drawer
      v-model="launchOpen"
      :title="tr(parentId ? 'rerun' : 'start')"
      size="min(580px, 100%)"
      :close-on-click-modal="false"
      close-on-press-escape
    >
      <form
        v-if="launchSuite"
        id="theme-evaluation-launch"
        class="drawer-form"
        @submit.prevent="launch"
      >
        <strong
          >{{ launchSuite.title }} ·
          {{ t('themeEvaluation.version', { version: launchSuite.revision }) }}</strong
        >
        <label>{{ tr('batchName') }}<el-input v-model="label" maxlength="120" /></label>
        <dl class="run-details">
          <div>
            <dt>{{ tr('identity') }}</dt>
            <dd>{{ options?.identity?.name ?? tr('identityUnavailable') }}</dd>
          </div>
          <div>
            <dt>{{ tr('dataVersion') }}</dt>
            <dd>{{ options?.data_version ?? '—' }}</dd>
          </div>
          <div v-if="options?.model">
            <dt>{{ tr('model') }}</dt>
            <dd>{{ options.model.name }}</dd>
          </div>
          <div v-if="options?.model?.prompt">
            <dt>{{ tr('promptVersion') }}</dt>
            <dd>{{ options.model.prompt }}</dd>
          </div>
        </dl>
        <label v-for="item in targets" :key="item.id"
          >{{ item.name }} · {{ tr('configuration') }}
          <el-select
            v-model="versions[item.id]"
            :aria-label="item.name + ' · ' + tr('configuration')"
          >
            <el-option
              v-for="version in item.versions"
              :key="version.id"
              :value="version.id"
              :label="version.label || version.id"
            />
          </el-select>
        </label>
        <label
          >{{ tr('callBudget') }} ·
          {{ t('themeEvaluation.budgetLimit', { limit: options?.budget_limit ?? 0 }) }}
          <el-input-number
            v-model="budget"
            :min="1"
            :max="options?.budget_limit ?? 1"
            :step="1"
            step-strictly
            :aria-label="tr('callBudget')"
          />
        </label>
        <fieldset>
          <legend>{{ tr('selectedCases') }}</legend>
          <div class="actions">
            <el-button text @click="caseIds = cases.map(({ id }) => id)">{{
              tr('selectAll')
            }}</el-button>
            <el-button text @click="caseIds = []">{{ tr('clearCases') }}</el-button>
          </div>
          <el-checkbox-group :model-value="caseIds" @update:model-value="selectCases">
            <el-checkbox v-for="item in cases" :key="item.id" :value="item.id"
              >{{ item.id }} · {{ item.turns[0]?.question }}</el-checkbox
            >
          </el-checkbox-group>
        </fieldset>
        <p>
          {{
            t('themeEvaluation.launchCoverage', {
              cases: chosenCases.length,
              total: cases.length,
              submissions: plannedSubmissions,
            })
          }}
        </p>
        <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" />
      </form>
      <template #footer>
        <el-button
          type="primary"
          form="theme-evaluation-launch"
          native-type="submit"
          :disabled="!canLaunch"
          :loading="busy"
          >{{ tr('start') }}</el-button
        >
      </template>
    </el-drawer>

    <el-drawer
      v-model="rowOpen"
      :title="t('themeEvaluation.caseDetails', { id: rowId })"
      :size="comparison ? 'min(960px, 100%)' : 'min(680px, 100%)'"
      close-on-press-escape
      destroy-on-close
    >
      <div v-if="detail" v-loading="rowLoading" class="drawer-form">
        <h3>{{ detail.question }}</h3>
        <p>{{ detail.category }} · {{ textFor('status', detail.status) }}</p>
        <div class="receipts-grid" :class="{ 'has-comparison': comparison }">
          <ThemeEvaluationReceipt
            v-if="detail.baseline"
            :row="detail.baseline"
            :label="baseline?.label ?? tr('baselineResult')"
            :test-case="lookupCase(baseline?.suite_id)"
          />
          <ThemeEvaluationReceipt
            v-if="detail.candidate"
            :row="detail.candidate"
            :label="candidate?.label ?? tr('candidateResult')"
            :test-case="lookupCase(candidate?.suite_id)"
          />
        </div>
      </div>
      <template #footer>
        <el-button v-if="canDraft" :loading="busy" @click="createDraft">{{
          tr('draftFromRun')
        }}</el-button>
        <el-button
          v-if="canRun && detail && failureBatch && !active(failureBatch)"
          type="primary"
          :disabled="busy"
          @click="openLaunch(failureBatch.suite_id, failureBatch, rowId)"
          >{{ tr('rerunWithProtection') }}</el-button
        >
      </template>
    </el-drawer>
  </section>
</template>

<style scoped lang="less">
.evaluation {
  display: grid;
  gap: 20px;
  min-width: 0;
  overflow-wrap: anywhere;
}
.panel {
  padding: 14px;
  border: 1px solid var(--ed-border-color-lighter);
  border-radius: 6px;
}
.section-heading,
.actions,
.suite-summary,
.filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.section-heading {
  justify-content: space-between;
}
.section-heading h2 {
  font-size: 16px;
}
.section-heading h3 {
  font-size: 14px;
}
h2,
h3,
p,
dl,
dd {
  margin: 0;
}
.section-heading p,
.suite-purpose {
  margin-top: 4px;
}
p,
dt,
small {
  color: var(--ed-text-color-regular);
}
.suites-panel,
.report-panel,
.results,
.drawer-form {
  display: grid;
  min-width: 0;
  gap: 12px;
}
.suite-summary {
  justify-content: space-between;
  align-items: end;
}
.suite-summary dl {
  display: flex;
  align-items: end;
  flex-wrap: wrap;
  gap: 8px 20px;
}
.suite-summary dl > div {
  display: grid;
  gap: 4px;
}
.suite-summary .selection {
  flex: 1 1 220px;
  min-width: 0;
  max-width: 420px;
}
label {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.report-controls {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(245px, 100%), 1fr));
  gap: 8px;
}
.actions :deep(.ed-button + .ed-button) {
  margin-left: 0;
}
.scoreboard {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  border: 1px solid var(--ed-border-color-lighter);
  border-radius: 6px;
}
.scoreboard > div {
  padding: 10px 12px;
  background: var(--ed-bg-color);
}
.scoreboard > div + div {
  border-left: 1px solid var(--ed-border-color-lighter);
}
.scoreboard dt {
  font-size: 12px;
}
.scoreboard dd {
  margin-top: 3px;
  font-variant-numeric: tabular-nums;
  font-size: 19px;
  font-weight: 600;
  color: var(--ed-text-color-primary);
}
.scoreboard dd span {
  font-size: 13px;
  font-weight: 400;
  color: var(--ed-text-color-regular);
}
.scoreboard .primary {
  background: var(--ed-color-primary-light-9);
}
.other-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  font-size: 12px;
}
.other-counts > div {
  display: flex;
  gap: 5px;
}
.other-counts dd {
  font-weight: 600;
}
.run-details,
.comparison-diff dl {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(190px, 100%), 1fr));
  gap: 10px;
}
.run-details dt,
.comparison-diff dt {
  font-size: 12px;
}
.run-details dd,
.comparison-diff dd {
  white-space: pre-wrap;
}
details summary {
  cursor: pointer;
}
details[open] .run-details {
  margin-top: 10px;
}
.comparison {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.comparison-diff {
  flex-basis: 100%;
}
.comparison-diff dl {
  margin-top: 8px;
}
.filters :deep(.ed-select) {
  flex: 1 1 130px;
  width: 145px;
}
table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
}
thead {
  background: var(--ed-fill-color-light);
}
table th,
table td {
  padding: 8px;
  border-bottom: 1px solid var(--ed-border-color-lighter);
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}
thead th:first-child {
  width: 42%;
}
thead th:nth-child(2),
thead th:nth-child(3) {
  width: 16%;
}
table :deep(.ed-tag) {
  min-height: 24px;
  height: auto;
  max-width: 100%;
  white-space: normal;
}
tbody th {
  font-weight: normal;
}
.case-label {
  font-weight: 600;
  margin-bottom: 3px;
}
.case-label small {
  font-weight: normal;
}
.issue-preview {
  margin: 4px 0;
}
.empty {
  padding: 20px 12px;
  text-align: center;
}
.drawer-form {
  overflow-wrap: anywhere;
}
.drawer-form fieldset {
  margin: 0;
  min-width: 0;
  border: 1px solid var(--ed-border-color-lighter);
  border-radius: 6px;
}
.drawer-form :deep(.ed-checkbox-group) {
  display: grid;
  gap: 5px;
}
.drawer-form :deep(.ed-checkbox) {
  display: flex;
  align-items: center;
  margin-right: 0;
  height: auto;
  min-height: 31px;
  white-space: normal;
}
.drawer-form :deep(.ed-checkbox__label) {
  white-space: normal;
  overflow-wrap: anywhere;
}
.receipts-grid {
  display: grid;
  gap: 16px;
}
.receipts-grid.has-comparison {
  grid-template-columns: repeat(auto-fit, minmax(min(310px, 100%), 1fr));
}
@media (max-width: 1100px) {
  .issue-preview {
    display: none;
  }
}
@media (max-width: 600px) {
  .scoreboard {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .scoreboard > div:nth-child(4) {
    border-left: 0;
  }
  .scoreboard > div:nth-child(n + 4) {
    border-top: 1px solid var(--ed-border-color-lighter);
  }
  table th,
  table td {
    padding: 6px 4px;
  }
  thead th:nth-child(2),
  thead th:nth-child(3) {
    width: 18%;
  }
}
</style>
