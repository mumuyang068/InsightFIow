<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { multi } from '@/api/cbiMulti'
import { chatApi } from '@/api/chat'
import AddViewDashboard from '@/views/dashboard/common/AddViewDashboard.vue'
import MethodMembers from './MethodMembers.vue'
import ResultCard from '../workspace/ResultCard.vue'
import ResultInspector from '../workspace/ResultInspector.vue'
import MeasureChart from '../workspace/MeasureChart.vue'
import { isImeKey } from '../workspace/keyboard'
import { useEmitt } from '@/utils/useEmitt'
import { methods, methodConfig, type BindingSummary, type Feedback, type MethodChange, type MethodReceipt, type MethodRun, type MethodTask } from '@/api/cbiMethods'

const props = defineProps<{ chatId: string; savedRecord?: string; inbox?: boolean }>()
const emit = defineEmits<{
  refresh: []
  follow: [record: string]
  handoffStatus: [chatId: string, status: string]
  handoffBusy: [busy: boolean]
}>()
const { t } = useI18n()
const tr = (key: string) => t('methods.' + key)
const ui = (key: string) => t('cbiWorkspace.' + key)
const router = useRouter()
const route = useRoute()
const { emitter } = useEmitt()
const focusView = computed(() => !props.inbox && route.query.view === 'analysis' && String(route.query.chat) === props.chatId)
const focusTab = ref<'analysis' | 'conversation'>('analysis')
const composer = ref<HTMLTextAreaElement>()
const anchor = ref<HTMLElement>()
const memberDrawer = ref(false)
const chart = ref<InstanceType<typeof MeasureChart>>()
const dashboard = ref<InstanceType<typeof AddViewDashboard>>()
const evidenceDrawer = ref(false)
const conditionDrawer = ref(false)
const transferDrawer = ref(false)
const converted = ref(true)
const rateBasis = ref<'overall' | 'adjacent'>('overall')
const pitch = ref('')
const pinned = ref('')
let originalScroll = 0
const task = ref<MethodTask>()
const binding = ref('')
const question = ref('')
const label = ref('')
const error = ref('')
const busy = ref(false)
const history = ref('')
const step = ref(-1)
const note = ref('')
const receiver = ref('')
const share = ref(false)
const recipients = ref<{id: string; name: string}[]>([])
const inspected = ref<Record<string, unknown>[]>()
const comparing = ref('')
const selectedHistory = computed(() => history.value || undefined)
const entryStart = ref('')
const entryEnd = ref('')
const observation = ref('')
const windowHours = ref(24)
const cohort = ref('')
const repeat = ref('')
const eligibility = ref('paid')
const bindings=ref<BindingSummary[]>([])
const bindingMeta = computed(()=>bindings.value.find(item=>item.id===binding.value))
const feedback=ref<Feedback[]>([])
const showFeedback=ref(false)
const observed=ref('')
const expected=ref('')
let feedbackRequest: {id:string; signature:string}|undefined
const ops=(key:string)=>t('methodOps.'+key)
const channels = ['ad','direct','search','email']
const stepLabels = ['view','cart','pay']
const methodRun = computed(() => {
  const result=task.value?.runs[history.value || task.value.work || task.value.handoff?.base_run || '']
  return task.value?.role==='owner' && result && binding.value && result.definition.plan.binding!==binding.value && !history.value
    ? undefined : result
})
const draft = computed(() => {
  const definition = task.value?.definition
  return definition && (!binding.value || definition.plan.binding === binding.value) ? definition : undefined
})
const handoff = computed(() => task.value?.handoff)
watch(() => handoff.value?.status, status => {
  if (props.inbox && status) emit('handoffStatus', props.chatId, status)
})
const candidate = computed(() => task.value?.runs[handoff.value?.candidate || ''])
const base = computed(() => task.value?.runs[handoff.value?.base_run || ''])
const selectedMembers = computed(() => {
  const run = methodRun.value
  if (!run || step.value < 0) return run?.result.trace
  const reached = new Set(run.result.members?.[step.value] ?? [])
  const prior = new Set(run.result.members?.[step.value - 1] ?? [])
  return run.result.trace.filter(row => converted.value ? reached.has(String(row.attempt)) :
    prior.has(String(row.attempt)) && !reached.has(String(row.attempt)))
})
const isOwner = computed(() => task.value?.role === 'owner')
const draftUnrun = computed(() => !!draft.value && draft.value.id !== task.value?.runs[task.value.work || '']?.definition.id)
const draftForResult = computed(() => draftUnrun.value &&
  (draft.value?.parent_run || undefined) === methodRun.value?.id)
let sequence = 0
let conditionCommand: { key: string; configure?: Record<string, unknown>; execute?: Record<string, unknown>; configured: boolean } | undefined

function withRunAccess(open: () => void | Promise<void>) {
  const chatId = props.chatId
  const runId = methodRun.value?.id
  return attempt(async () => {
    const current = await multi.get(chatId) as unknown as MethodTask
    if (props.chatId !== chatId || methodRun.value?.id !== runId) return
    if (!runId || !current.runs[runId]) {
      task.value = current
      error.value = ui('resultUnavailable')
      return
    }
    await open()
  })
}

function revealMembers(index = -1, success = true) {
  return withRunAccess(() => {
    step.value = index
    converted.value = success
    inspected.value = undefined
    if (!focusView.value) memberDrawer.value = true
    else nextTick(() => anchor.value?.querySelector('.focus-members')?.scrollIntoView({ block: 'nearest' }))
  })
}

function exportImage() {
  return withRunAccess(async () => {
    const run = methodRun.value
    if (!run || !await chart.value?.downloadImage(run.definition.binding_label || tr('title'))) {
      error.value = tr('failed')
    }
  })
}

function addToDashboard() {
  const run = methodRun.value
  const recordId = run?.record_id
  const datasource = task.value?.topic.datasource
  if (!recordId || !datasource || !run?.result.counts) return
  return withRunAccess(async () => {
    const data = await chatApi.get_chart_data(recordId)
    dashboard.value?.optInit({
      id: recordId,
      record_id: recordId,
      datasource,
      analysis_state: { multi: { record_id: recordId, method: run.definition.plan.method } },
      data,
      chart: {
        type: 'table',
        title: run.definition.binding_label || tr(run.definition.plan.binding),
        columns: [{ name: ui('savedStep'), value: 'step' }, { name: ui('savedCount'), value: 'count' }],
      },
    })
  })
}

function editConditions() {
  if (draftForResult.value) {
    synchronize(draft.value?.plan)
  } else if (methodRun.value) {
    synchronize(methodRun.value.definition.plan)
  } else {
    changeBinding()
  }
  conditionDrawer.value = true
}

function conditionChanges(useDraft = false): MethodChange {
  const plan = (useDraft ? draft.value?.plan : undefined) ?? methodRun.value?.definition.plan
  const before = plan ?? bindingMeta.value
  const changes: MethodChange = {}
  if (bindingMeta.value?.method === 'ordered_attempt_funnel') {
    if (windowHours.value !== before?.window_hours) changes.window_hours = windowHours.value
  } else {
    if (eligibility.value !== (plan?.eligibility ?? 'paid')) changes.eligibility = eligibility.value
    if ((cohort.value || null) !== (plan?.cohort_channel ?? null)) changes.cohort_channel = cohort.value || null
    if ((repeat.value || null) !== (plan?.repeat_channel ?? null)) changes.repeat_channel = repeat.value || null
    if (entryStart.value !== before?.entry_start) changes.entry_start = entryStart.value
    if (entryEnd.value !== before?.entry_end) changes.entry_end = entryEnd.value
  }
  if (observation.value !== before?.observation_asof) changes.observation_asof = observation.value
  return changes
}

function applyConditions(execute = true) {
  return attempt(async () => {
    const selection = history.value
    if (!task.value) task.value = await multi.start(props.chatId, { question: bindingMeta.value?.label || tr('title') }) as unknown as MethodTask
    const changes = conditionChanges()
    const key = JSON.stringify([binding.value, selectedHistory.value, windowHours.value, observation.value,
      entryStart.value, entryEnd.value, eligibility.value, cohort.value, repeat.value])
    if (!conditionCommand || conditionCommand.key !== key) {
      const resumable = draftForResult.value && !!draft.value && Object.keys(conditionChanges(true)).length === 0
      conditionCommand = { key, configured: resumable, configure: {
        action: 'configure', revision: task.value.revision, request_id: crypto.randomUUID(),
        binding: binding.value, run_id: selectedHistory.value, changes,
      } }
    }
    const pending = conditionCommand
    if (!pending.configured) {
      const response = await methods.action(props.chatId, pending.configure!)
      task.value = response.task
      if (response.receipt.outcome !== 'configured') { applyReceipt(response.receipt); return }
      pending.configured = true
    }
    if (!execute) return
    pending.execute ??= { action: 'execute', revision: task.value.revision, request_id: crypto.randomUUID(), binding: binding.value }
    const response = await methods.action(props.chatId, pending.execute)
    task.value = response.task
    if (history.value === selection) applyReceipt(response.receipt)
    if (response.receipt.outcome === 'ran') {
      conditionCommand = undefined
      conditionDrawer.value = false
      if (history.value === selection || !history.value) synchronize(response.task.definition?.plan)
    } else {
      pending.execute = undefined
    }
  })
}

function pinRun() {
  if (!methodRun.value) return
  pinned.value = methodRun.value.id
  focusTab.value = 'conversation'
  nextTick(() => composer.value?.focus())
}

function expandAnalysis() {
  if (!methodRun.value) return
  originalScroll = anchor.value?.querySelector('.method-results')?.scrollTop ?? 0
  focusTab.value = 'analysis'
  void router.push({ query: { ...route.query, view: 'analysis', chat: props.chatId, run: methodRun.value.id } })
}

function closeAnalysis() {
  const query = { ...route.query }
  delete query.view
  delete query.run
  void router.push({ query })
}

function chooseHistory() {
  step.value = -1
  inspected.value = undefined
  showFeedback.value = false
  conditionDrawer.value = false
  if (!focusView.value) return
  const run = history.value || task.value?.work
  void router.replace({ query: { ...route.query, run } })
}

watch(focusView, (active) => {
  emitter.emit('cbi-analysis-focus', active)
  if (!active) nextTick(() => {
    const main = anchor.value?.querySelector('.method-results')
    if (main) main.scrollTop = originalScroll
  })
}, { immediate: true })
watch([() => route.query.run, task], ([run]) => {
  if (focusView.value && typeof run === 'string' && task.value?.runs[run]) history.value = run
})
onBeforeUnmount(() => { if (focusView.value) emitter.emit('cbi-analysis-focus', false) })

function synchronize(plan?: MethodRun['definition']['plan']) {
  if (!plan) return
  binding.value = plan.binding
  entryStart.value = plan.entry_start ?? ''
  entryEnd.value = plan.entry_end ?? ''
  observation.value = plan.observation_asof ?? ''
  windowHours.value = plan.window_hours ?? 24
  cohort.value = plan.cohort_channel || ''
  repeat.value = plan.repeat_channel || ''
  eligibility.value = plan.eligibility ?? 'paid'
}

async function load() {
  const request = ++sequence
  task.value = undefined
  error.value = ''
  try {
    const response = await multi.get(props.chatId) as unknown as MethodTask
    if (request !== sequence) return
    task.value = response
    synchronize(response.definition?.plan)
    if (response.role === 'owner') {
      recipients.value = await multi.reviewers(props.chatId)
      feedback.value = await methodConfig.own(props.chatId)
    }
  } catch (cause) {
    if ((cause as { response?: { status: number }}).response?.status !== 404) error.value = tr('failed')
  }
  if (!task.value || task.value.role==='owner') {
    try {
      const choices=await methods.catalog(props.chatId)
      if (request!==sequence) return
      bindings.value=choices
      if(!binding.value) binding.value=choices[0]?.id ?? ''
      if(!task.value?.definition) changeBinding()
    } catch { if(request===sequence) error.value=tr('failed') }
  }
}
watch(() => props.chatId, load, { immediate: true })

async function attempt(operation: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  label.value = ''
  try { await operation() } catch (cause) {
    const response = (cause as { response?: { status?: number; data?: { code?: string } | string } }).response
    const reason = typeof response?.data === 'string' ? response.data : response?.data?.code
    error.value = reason ? tr('failed') + ' · ' + reason : tr('failed')
    if (response?.status === 403 || response?.status === 404) {
      task.value = undefined
      inspected.value = undefined
      memberDrawer.value = false
      evidenceDrawer.value = false
      transferDrawer.value = false
      showFeedback.value = false
      error.value = ui('resultUnavailable')
    }
    if (response?.status === 409) { const message=error.value; await load(); error.value=message }
  } finally { busy.value = false }
}

function applyReceipt(receipt: MethodReceipt) {
  label.value = receipt.clarification || (receipt.outcome ? tr(receipt.outcome) : '')
  if (receipt.problem) error.value = receipt.problem.code
  inspected.value = receipt.members
  if (receipt.outcome === 'read_members') step.value = -1
  if (receipt.outcome === 'read_members' && !focusView.value) memberDrawer.value = true
  if (receipt.outcome === 'ran') {
    history.value = ''
    step.value = -1
    if (focusView.value && receipt.run_id) void router.replace({ query: { ...route.query, run: receipt.run_id } })
    emit('refresh')
  }
}

function handleQuestionEnter(event: KeyboardEvent) {
  if (isImeKey(event)) return
  event.preventDefault()
  if (!event.repeat && !busy.value && question.value.trim()) void askQuestion()
}

function askQuestion() {
  return attempt(async () => {
    if (!task.value) task.value = await multi.start(props.chatId, {
      question: question.value,
    }) as unknown as MethodTask
    const target = pinned.value && task.value.runs[pinned.value]
    const response = await methods.action(props.chatId, {
      action: 'question', revision: task.value.revision, request_id: crypto.randomUUID(),
      binding: target ? target.definition.plan.binding : history.value && methodRun.value ? methodRun.value.definition.plan.binding : binding.value,
      run_id: target ? target.id : history.value || undefined, question: question.value, saved_record: props.savedRecord || undefined,
    })
    task.value = response.task
    synchronize(response.task.definition?.plan)
    if (!recipients.value.length) recipients.value = await multi.reviewers(props.chatId)
    applyReceipt(response.receipt)
    question.value = ''
    pinned.value = ''
    pitch.value = response.receipt.outcome === 'ran' ? '' : label.value
  })
}

function action(action: string, extra: Record<string, unknown> = {}) {
  return attempt(async () => {
    if (!task.value) {
      task.value = await multi.start(props.chatId, { question: bindingMeta.value?.label || tr('title') }) as unknown as MethodTask
    }
    const response = await methods.action(props.chatId, {
      action, revision: task.value.revision, request_id: crypto.randomUUID(), binding: binding.value, ...extra,
    })
    task.value = response.task
    synchronize(response.task.definition?.plan)
    applyReceipt(response.receipt)
  })
}

function hand(action: string, extra: Record<string, unknown> = {}) {
  return attempt(async () => {
    if (!task.value) return
    if (props.inbox) emit('handoffBusy', true)
    try {
      const response = await multi.action(props.chatId, {
        action, revision: task.value.revision, request_id: crypto.randomUUID(), ...extra,
      })
      task.value = response.task as unknown as MethodTask
      note.value = ''
      if (action === 'request') transferDrawer.value = false
      if (action === 'adopt') emit('refresh')
    } finally {
      if (props.inbox) emit('handoffBusy', false)
    }
  })
}

function changeBinding() {
  pinned.value = ''
  memberDrawer.value = false
  evidenceDrawer.value = false
  conditionCommand = undefined
  const plan = task.value?.runs[task.value?.work || '']?.definition.plan
  if (plan?.binding === binding.value) synchronize(plan)
  else {
    const b=bindingMeta.value
    entryStart.value = b?.entry_start ?? ''
    entryEnd.value = b?.entry_end ?? ''
    observation.value = b?.observation_asof ?? ''
    windowHours.value = b?.window_hours ?? 24
    history.value=''
    inspected.value=undefined
    step.value=-1
    eligibility.value = 'paid'
    cohort.value = ''
    repeat.value = ''
  }
}

function sendFeedback() {
  return attempt(async()=>{
    const run=methodRun.value
    if(!run) return
    const signature=JSON.stringify([run.id,observed.value,expected.value])
    if(feedbackRequest?.signature!==signature) feedbackRequest={id:crypto.randomUUID(),signature}
    const sent=await methodConfig.submit(props.chatId,{run_id:run.id,text:observed.value,expected:expected.value,request_id:feedbackRequest.id})
    feedback.value=[...feedback.value.filter(item=>item.id!==sent.id),sent]
    showFeedback.value=false
    observed.value=''
    expected.value=''
  })
}
const currentVersion=computed(()=>bindings.value.find(item=>item.id===methodRun.value?.definition.plan.binding)?.version)
const legacySources=computed(()=>methodRun.value && !methodRun.value.definition.binding_label)
function sourceName(kind:string){return kind==='binding'?ops('source_binding'):kind==='run'?ops('source_run'):ops('source_input')}

const pct = (x: number | null | undefined) => x == null ? '—' : Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(x) + '%'
const points = (x: number) => Intl.NumberFormat(undefined, { maximumFractionDigits: 2, signDisplay: 'exceptZero' }).format(x)
const canEdit = computed(() => (!task.value || isOwner.value) && !props.savedRecord && !!bindingMeta.value)
const chosenQuestion = computed(() => methodRun.value?.question ?? task.value?.question ?? '')
const steps = computed(() => (methodRun.value?.result.counts ?? []).map((count, index) => ({
  count, index, label: tr(stepLabels[index]), total: methodRun.value?.result.each_of_entry_pct?.[index] ?? null,
  adjacent: methodRun.value?.result.adjacent_pct?.[index - 1] ?? null,
})))
const funnelChart = computed(() => steps.value.map(item => ({
  label: item.label,
  value: rateBasis.value === 'adjacent' && item.index ? item.adjacent : item.total,
})))
const scopeText = computed(() => {
  const plan = methodRun.value?.definition.plan
  return plan ? [tr('unit_' + plan.unit), plan.window_hours + ' h',
    plan.entry_start + ' → ' + plan.entry_end, tr('observation') + ' ' + plan.observation_asof].join(' · ') : ''
})
const memberTitle = computed(() => step.value < 0 ? tr('members') :
  tr(stepLabels[step.value]) + ' · ' + ui(converted.value ? 'completed' : 'dropped'))
</script>

<template>
  <section ref="anchor" class="method-workspace" :class="{ 'focus-view': focusView }" :aria-label="tr('title')">
    <AddViewDashboard ref="dashboard" />
    <header v-if="focusView" class="focus-heading">
      <el-button text @click="closeAnalysis">{{ ui('back') }}</el-button>
      <strong>{{ methodRun?.definition.binding_label || bindingMeta?.label }}</strong>
      <el-segmented v-model="focusTab" class="focus-tabs"
        :options="[{label: ui('analysis'), value: 'analysis'}, {label: ui('conversation'), value: 'conversation'}]" />
    </header>
    <div class="method-body" :class="'tab-' + focusTab">
      <main class="method-results">
        <p v-if="error && !conditionDrawer && !transferDrawer" role="alert" class="error">{{ error }}</p>
        <div v-if="!methodRun && !handoff" class="method-empty">
          <h2>{{ bindingMeta?.label || tr('title') }}</h2>
          <p v-if="bindingMeta">{{ bindingMeta.window_hours }} h · {{ bindingMeta.entry_start }} → {{ bindingMeta.entry_end }} · {{ tr('observation') }} {{ bindingMeta.observation_asof }}</p>
          <el-button v-if="canEdit" text @click="editConditions">{{ ui('edit') }}</el-button>
        </div>
        <div v-if="methodRun && chosenQuestion && !focusView && !inbox" class="method-question">{{ chosenQuestion }}</div>
        <label v-if="isOwner && methodRun && Object.keys(task?.runs ?? {}).length > 1" class="result-history">{{ tr('history') }}
          <select v-model="history" @change="chooseHistory">
            <option value="">{{ tr('current') }}</option>
            <option v-for="item in Object.values(task?.runs ?? {}).filter(r => r.record_id)" :key="item.id" :value="item.id">
              {{ item.definition.binding_label || tr(item.definition.plan.binding) }} · {{ item.definition.plan.window_hours }} h · {{ item.result.numerator }}/{{ item.result.denominator }}
            </option>
          </select>
        </label>
        <ResultCard v-if="methodRun" :data-method-run="methodRun.id"
          :title="methodRun.definition.binding_label || tr(methodRun.definition.plan.binding)"
          :scope="scopeText" :status="methodRun.result.status !== 'verified' ? tr(methodRun.result.status) : undefined">
          <div class="method-metric">
            <div>
              <p>{{ ui(methodRun.result.counts ? 'overall' : 'repeat') }}</p>
              <strong>{{ pct(methodRun.result.rate_pct) }}</strong>
            </div>
            <div class="metric-details">
              <b data-method-ratio>{{ methodRun.result.numerator }}/{{ methodRun.result.denominator }}</b>
              <span>{{ tr('immature') }} {{ methodRun.result.immature }}</span>
            </div>
            <p v-if="methodRun.comparison?.delta_pp != null" class="comparison">
              {{ ui('comparison') }} <b>{{ points(methodRun.comparison.delta_pp) }} {{ tr('pp') }}</b>
              <span v-if="methodRun.comparison.new_completions?.length"> · {{ tr('new_members') }} {{ methodRun.comparison.new_completions.join('、') }}</span>
            </p>
          </div>
          <p v-if="methodRun.definition.plan.eligibility" class="result-terms">{{ tr(methodRun.definition.plan.eligibility) }} ·
            {{ tr('cohort_channel') }} {{ methodRun.definition.plan.cohort_channel || tr('all') }} ·
            {{ tr('repeat_channel') }} {{ methodRun.definition.plan.repeat_channel || tr('all') }}</p>
          <p v-if="task?.quality?.[methodRun.id]" role="status">{{ tr('corrected') }} · {{ task.quality[methodRun.id].replacement.slice(0,8) }}</p>
          <template v-if="steps.length">
            <el-segmented
              v-model="rateBasis"
              size="small"
              :options="[{ label: ui('overall'), value: 'overall' }, { label: tr('adjacent'), value: 'adjacent' }]"
            />
            <MeasureChart v-if="funnelChart.some(item => item.value != null) && methodRun.result.rate_pct != null" ref="chart"
              :values="funnelChart" :maximum="100" percent :funnel="rateBasis === 'overall'"
              :label="rateBasis === 'overall' ? ui('overall') : tr('adjacent')" @select="revealMembers" />
            <div class="step-grid">
              <div v-for="item in steps" :key="item.index">
                <strong>{{ item.label }} <span>{{ item.count }}</span></strong>
                <p>{{ pct(item.total) }} <span v-if="item.index"> · {{ tr('adjacent') }} {{ pct(item.adjacent) }}</span></p>
                <button type="button" class="link" @click="revealMembers(item.index)">{{ ui('completed') }}</button>
                <button v-if="item.index" type="button" class="link" @click="revealMembers(item.index,false)">{{ ui('dropped') }}</button>
              </div>
            </div>
          </template>
          <p v-for="item in feedback.filter(item=>item.run.run_id===methodRun?.id)" :key="item.id" class="feedback-note">
            {{ ops(item.status) }} · {{ item.text }} <span v-if="item.handling"> · {{ item.handling.note }}</span>
          </p>
          <template #actions>
            <el-button v-if="!focusView && !inbox" type="primary" plain @click="expandAnalysis">{{ ui('expand') }}</el-button>
            <el-button v-else text @click="revealMembers()">{{ ui('allMembers') }}</el-button>
            <el-button text @click="withRunAccess(() => { evidenceDrawer = true })">{{ ui('inspect') }}</el-button>
            <el-button v-if="isOwner && methodRun.record_id" text :disabled="busy || !!task?.quality?.[methodRun.id] || !!task?.saved?.[methodRun.id]" @click="action('save',{ run_id: methodRun.id })">
              {{ task?.saved?.[methodRun.id] ? ui('saved') : tr('save') }}
            </el-button>
            <el-popover v-if="isOwner && methodRun.record_id" trigger="click" :width="230" placement="bottom-end">
              <template #reference><el-button text>{{ ui('more') }}</el-button></template>
              <div class="menu-actions">
                <el-button text @click="pinRun">{{ ui('follow') }}</el-button>
                <el-button v-if="chart" text :disabled="!chart.rendered" @click="exportImage">{{ ui('downloadImage') }}</el-button>
                <el-button v-if="methodRun.result.counts" text @click="addToDashboard">{{ t('chat.add_to_dashboard') }}</el-button>
                <el-button v-if="canEdit" text @click="editConditions">{{ ui('edit') }}</el-button>
                <el-button text :disabled="busy || !!savedRecord" @click="action('reread',{run_id:methodRun.id})">{{ tr('reread') }}</el-button>
                <el-button v-if="task?.saved?.[methodRun.id] && !task?.quality?.[methodRun.id]" text @click="emit('follow', methodRun.record_id)">{{ tr('follow') }}</el-button>
                <el-button text @click="showFeedback = true">{{ ops('submit_feedback') }}</el-button>
                <el-button v-if="!handoff && recipients.length" text @click="transferDrawer = true">{{ ui('analyst') }}</el-button>
              </div>
            </el-popover>
          </template>
        </ResultCard>
        <section v-if="focusView && methodRun" class="focus-members">
          <h3>{{ memberTitle }}</h3>
          <el-button v-if="step>=0" text @click="revealMembers()">{{ ui('allMembers') }}</el-button>
          <MethodMembers :run="methodRun" :rows="inspected ?? selectedMembers ?? []" />
          <details><summary>{{ ops('technical') }}</summary><pre>{{ inspected ?? selectedMembers }}</pre></details>
        </section>
        <ResultCard v-if="handoff" class="handoff-card" :title="tr('handoff')" :scope="handoff.question" :status="tr('phase_' + handoff.status)">
          <el-button text @click="attempt(async () => { comparing=(await methods.comparison(chatId)).status })">{{ tr('compare') }}</el-button>
          <p v-if="comparing" role="status">{{ tr(comparing) }}</p>
          <p v-if="candidate" data-method-comparison>{{ base?.result.numerator }}/{{ base?.result.denominator }} →
            {{ candidate.result.numerator }}/{{ candidate.result.denominator }} · {{ candidate.correction?.change }}</p>
          <p v-if="candidate?.result.repeated_members">{{ tr('repeated') }} {{ base?.result.repeated_members?.join('、') || '—' }} →
            {{ candidate.result.repeated_members.join('、') || '—' }}</p>
          <details v-if="candidate?.correction"><summary>{{ tr('candidate_checks') }}</summary><pre>{{ candidate.correction.guards }}</pre></details>
          <el-button v-if="!isOwner && handoff.status==='requested'" type="primary" :disabled="busy" @click="hand('accept')">{{ tr('accept') }}</el-button>
          <el-button v-if="isOwner && handoff.status==='returned'" type="primary" :disabled="busy" @click="hand('apply')">{{ tr('apply') }}</el-button>
          <template v-if="(!isOwner && handoff.status==='accepted') || (isOwner && handoff.status==='applied')">
            <label>{{ tr('note') }}<input v-model="note" maxlength="800" /></label>
            <el-button v-if="!isOwner" type="primary" :disabled="busy || !note.trim()" @click="hand('return',{ reason:note })">{{ tr('return') }}</el-button>
            <el-button v-else type="primary" :disabled="busy || !note.trim()" @click="hand('adopt',{ reason:note })">{{ tr('adopt') }}</el-button>
          </template>
        </ResultCard>
      </main>
      <aside v-if="focusView" class="method-context">
        <h3>{{ ui('conversation') }}</h3>
        <p v-if="chosenQuestion" class="method-question">{{ chosenQuestion }}</p>
        <p v-if="methodRun">{{ methodRun.definition.binding_label || tr(methodRun.definition.plan.binding) }} · {{ methodRun.definition.plan.window_hours }} h · {{ methodRun.result.numerator }}/{{ methodRun.result.denominator }}</p>
      </aside>
    </div>
    <form v-if="!task || isOwner" class="method-composer" @submit.prevent="askQuestion">
      <p v-if="busy || pitch" role="status">{{ busy ? tr('busy') : pitch }}</p>
      <div v-if="pinned || savedRecord" class="reference">
        {{ ui('reference') }} · {{ pinned ? task?.runs[pinned]?.definition.binding_label || task?.runs[pinned]?.question : savedRecord }}<span v-if="pinned"> · {{ task?.runs[pinned]?.definition.plan.window_hours }} h</span>
        <el-button v-if="pinned" text :aria-label="ui('removeReference')" @click="pinned = ''">×</el-button>
      </div>
      <label class="composer-input">
        <span class="visually-hidden">{{ tr('question') }}</span>
        <textarea ref="composer" v-model="question" :placeholder="ui('prompt')" rows="2" maxlength="800" required
          @keydown.enter.exact="handleQuestionEnter"></textarea>
      </label>
      <div class="composer-actions">
        <label v-if="bindings.length" class="theme"><span class="visually-hidden">{{ tr('binding') }}</span>
          <select v-model="binding" :disabled="!!savedRecord || busy" @change="changeBinding">
            <option v-for="option in bindings" :key="option.id" :value="option.id">{{ option.label }}</option>
          </select>
        </label>
        <el-button v-if="canEdit" text @click="editConditions">{{ ui('edit') }}</el-button>
        <el-button type="primary" native-type="submit" :disabled="busy || !question.trim() || (!binding && !savedRecord)">{{ tr('ask') }}</el-button>
      </div>
    </form>
    <ResultInspector v-model="conditionDrawer" :title="ui('conditions')">
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <p v-if="draftForResult">{{ ui('pending') }} · {{ draft?.plan.window_hours }} h · {{ draft?.plan.observation_asof }}</p>
      <form class="condition-fields" @submit.prevent="applyConditions()">
        <label v-if="bindingMeta?.method === 'ordered_attempt_funnel'">{{ tr('window') }}
          <select v-model.number="windowHours"><option v-for="hours in bindingMeta.allowed_windows" :key="hours" :value="hours">{{ hours }} h</option></select>
        </label>
        <template v-else>
          <label>{{ tr('eligibility') }}<select v-model="eligibility"><option value="paid">{{ tr('paid') }}</option><option value="net">{{ tr('net') }}</option></select></label>
          <label v-if="bindingMeta?.reading !== 'refunds_known_by_observation'">{{ tr('cohort_channel') }}<select v-model="cohort"><option value="">{{ tr('all') }}</option><option v-for="c in channels" :key="c">{{ c }}</option></select></label>
          <label v-if="bindingMeta?.reading !== 'refunds_known_by_observation'">{{ tr('repeat_channel') }}<select v-model="repeat"><option value="">{{ tr('all') }}</option><option v-for="c in channels" :key="c">{{ c }}</option></select></label>
          <label>{{ tr('entry_start') }}<input v-model="entryStart" type="date" required /></label>
          <label>{{ tr('entry_end') }}<input v-model="entryEnd" type="date" required /></label>
        </template>
        <label v-if="bindingMeta?.method==='ordered_attempt_funnel'">{{ tr('observation') }}
          <select v-model="observation"><option v-for="at in bindingMeta.observations ?? [bindingMeta.observation_asof]" :key="at">{{ at }}</option></select>
        </label>
        <label v-else>{{ tr('observation') }}<input v-model="observation" type="date" :disabled="bindingMeta?.reading === 'fixed_2026-11-10_snapshot'" required /></label>
        <div class="condition-actions">
          <el-button :disabled="busy" @click="applyConditions(false)">{{ ui('saveDraft') }}</el-button>
          <el-button type="primary" native-type="submit" :disabled="busy">{{ ui('apply') }}</el-button>
          <el-button :disabled="busy" @click="conditionDrawer = false">{{ ui('cancel') }}</el-button>
        </div>
      </form>
    </ResultInspector>
    <ResultInspector v-if="methodRun" v-model="memberDrawer" :title="memberTitle">
      <el-button v-if="step>=0" text @click="revealMembers()">{{ ui('allMembers') }}</el-button>
      <MethodMembers :run="methodRun" :rows="inspected ?? selectedMembers ?? []" />
      <details><summary>{{ ops('technical') }}</summary><pre>{{ inspected ?? selectedMembers }}</pre></details>
    </ResultInspector>
    <ResultInspector v-if="methodRun" v-model="evidenceDrawer" :title="ui('inspect')">
      <p>{{ ops('adopted_version') }} <strong data-adopted-version>{{ methodRun.definition.plan.binding_version }}</strong> ·
        {{ ops('active_version') }} <strong data-active-version>{{ currentVersion || '—' }}</strong></p>
      <p>{{ scopeText }} · {{ methodRun.definition.plan.timezone }}</p>
      <p v-if="legacySources">{{ ops('legacy_sources') }}</p>
      <dl class="source-list"><template v-for="(source,key) in methodRun.definition.sources" :key="key">
        <dt>{{ ops('params.'+key) }}</dt><dd>{{ sourceName(source.kind) }} · {{ source.id || '—' }} {{ source.version }}</dd>
      </template></dl>
      <details><summary>{{ ops('technical') }}</summary><pre>{{ methodRun.evidence }}{{ methodRun.definition.sources }}</pre></details>
    </ResultInspector>
    <ResultInspector v-if="isOwner && methodRun" v-model="showFeedback" :title="ops('submit_feedback')">
      <form class="condition-fields" @submit.prevent="sendFeedback">
        <label>{{ ops('observed') }}<textarea v-model="observed" required maxlength="1500" rows="3" /></label>
        <label>{{ ops('expected') }}<textarea v-model="expected" required maxlength="1500" rows="3" /></label>
        <el-button type="primary" native-type="submit" :disabled="busy">{{ ops('send') }}</el-button>
      </form>
    </ResultInspector>
    <ResultInspector v-if="isOwner && methodRun && !handoff" v-model="transferDrawer" :title="ui('analyst')">
      <p>{{ chosenQuestion }} · {{ scopeText }}</p>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <form class="condition-fields" @submit.prevent="hand('request',{ receiver, reason: note, share, base_run: methodRun.id })">
        <label>{{ tr('receiver') }}<select v-model="receiver"><option value="">—</option><option v-for="r in recipients" :key="r.id" :value="r.id">{{ r.name }}</option></select></label>
        <label>{{ tr('note') }}<textarea v-model="note" maxlength="800" rows="3" /></label>
        <label class="check"><input v-model="share" type="checkbox" />{{ tr('share') }}</label>
        <el-button type="primary" native-type="submit" :disabled="busy || !share || !receiver || !note.trim()">{{ tr('request') }}</el-button>
      </form>
    </ResultInspector>
  </section>
</template>

<style scoped>
.method-workspace { color: #1f2329; height: calc(100% - 56px); display: flex; flex-direction: column; min-width: 0; }
.method-body { flex: 1 1 auto; min-height: 0; display: flex; }
.method-results { width: 100%; min-width: 0; overflow: auto; padding: 24px max(24px, calc((100% - 960px) / 2)); }
.method-empty { padding: 42px 0; color: #535b63; }
.method-empty h2 { font-size: 23px; color: #1f2329; margin: 0 0 8px; }
.method-empty p,.metric-details,.result-terms { color: #535b63; font-size: 13px; }
.method-question { padding: 10px 16px; background: #f1f4f3; border-radius: 10px; margin: 0 0 16px auto; max-width: 85%; width: fit-content; overflow-wrap: anywhere; }
.result-history { display: flex; align-items: center; gap: 8px; margin: 0 0 12px auto; width: fit-content; color: #535b63; font-size: 13px; }
.result-history select { max-width: min(260px, 58vw); }
.method-metric { display: flex; align-items: flex-end; gap: 12px 24px; flex-wrap: wrap; }
.method-metric p { color: #535b63; margin: 0; }
.method-metric strong { font-size: 32px; font-weight: 650; line-height: 1.2; font-variant-numeric: tabular-nums; }
.metric-details { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding-bottom: 3px; }
.metric-details b { color: #1f2329; }
.comparison { margin-left: auto !important; font-size: 13px; padding-bottom: 3px; }
.comparison b { color: #23654f; font-variant-numeric: tabular-nums; }
.step-grid { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0,1fr); border-top: 1px solid #e5ebe7; }
.step-grid > div { padding: 10px 12px 2px; }
.step-grid > div + div { border-left: 1px solid #e5ebe7; }
.step-grid strong span { padding-left: 8px; font-variant-numeric: tabular-nums; }
.step-grid p { margin: 4px 0; font-size: 12px; color: #535b63; }
.link { background: transparent; color: #23654f; min-height: 30px; padding: 2px 4px; border: 0; cursor: pointer; }
.link:hover { text-decoration: underline; }
.method-composer { flex: 0 0 auto; width: calc(100% - 48px); max-width: 960px; margin: 8px auto 12px; padding: 8px 12px; border: 1px solid #dce5e0; border-radius: 12px; background: #f8faf8; }
.method-composer:focus-within { border-color: #25826b; }
.composer-input { display: block; }
.composer-input textarea { width: 100%; resize: vertical; max-height: 140px; background: transparent; border: none; padding: 6px 4px; outline: none; }
.composer-input textarea::placeholder { color: #535b63; }
.composer-actions { display:flex; align-items:center; gap: 6px; }
.composer-actions > :last-child { margin-left:auto; }
.theme { min-width: 0; }
.theme select { max-width: min(235px, 36vw); background: #fff; }
.reference { color: #23654f; background: #edf5f1; border-radius: 6px; padding: 4px 8px; font-size: 13px; }
.focus-heading { display: flex; align-items:center; gap: 8px; padding: 4px 16px; border-bottom: 1px solid #e5ebe7; min-height: 42px; }
.focus-tabs { margin-left: auto; display: none; }
.focus-view .method-results { width: 70%; padding-inline: 24px; }
.method-context { width: 30%; padding: 20px; border-left: 1px solid #e5ebe7; background: #f8faf8; overflow:auto; }
.method-context .method-question { max-width: 100%; margin-left: 0; }
.focus-members,.handoff-card { margin-top: 16px; }
.focus-members h3,.method-context h3 { font-size: 16px; margin: 0 0 8px; }
.feedback-note { font-size: 13px; color: #405b52; }
.menu-actions,.condition-fields { display: grid; gap: 8px; min-width: 0; }
.menu-actions :deep(.ed-button) { justify-content: flex-start; margin-left: 0; }
.condition-fields label { display:grid; gap: 4px; font-size: 14px; }
.condition-fields .check { display:flex; align-items:center; gap:8px; }
.condition-actions { margin-top: 8px; }
input,select,textarea { font: inherit; color: inherit; min-width: 0; max-width: 100%; box-sizing: border-box; }
select,input:not([type=checkbox]),textarea { border: 1px solid #ccd8d1; border-radius: 7px; padding: 6px 8px; min-height: 34px; }
.error { color: #9c2525; }
.source-list { display:grid; grid-template-columns:minmax(90px,1fr) 2fr; gap: 6px 12px; }
.source-list dd { margin:0; overflow-wrap:anywhere; }
pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:280px; overflow:auto; background:#f3f6f4; padding:8px; }
.visually-hidden { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; }
@media (max-width: 1439px) {
  .focus-tabs { display:flex; }
  .focus-view .method-results,.method-context { width:100%; }
  .focus-view .tab-analysis .method-context,.focus-view .tab-conversation .method-results { display:none; }
}
@media (max-width: 1024px) {
  .method-results,.focus-view .method-results { padding:16px; }
  .method-composer { width:calc(100% - 32px); }
  .step-grid > div { padding-inline:6px; }
}
</style>
