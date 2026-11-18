<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import MethodPanel from './MethodPanel.vue'
import ResultCard from '../workspace/ResultCard.vue'
import ResultInspector from '../workspace/ResultInspector.vue'
import { useI18n } from 'vue-i18n'
import { multi, type Changes, type Inputs, type Purpose, type Run, type Task } from '@/api/cbiMulti'

const props = defineProps<{ chatId?: string; parent?: string; label?: string; inbox?: boolean }>()
const emit = defineEmits<{ refresh: []; started: []; follow: [run: Run] }>()
const { t, te } = useI18n()
const tr = (key: string) => t('multi.' + key)
const ui = (key: string) => t('cbiWorkspace.' + key)
const fileDrawer = ref(false)
const evidenceDrawer = ref(false)
const transferDrawer = ref(false)
const task = ref<Task>()
const recipients = ref<{ id: string; name: string }[]>([])
const store = ref('')
const question = ref('')
const followQuestion = ref('')
const inputs = ref<Inputs>({})
const selectedHistory = ref('')
const purpose = ref<Purpose>('roster')
const uploadFile = ref<File>()
const replacementFile = ref<File>()
const feeCandidate = ref('')
const changes = ref<Changes>()
const issue = ref('')
const message = ref('')
const busy = ref(false)
const receiver = ref('')
const share = ref(false)
const reason = ref('')
const handoffReason = ref('')
const choiceSku = ref('')
const rosterField = ref('')
const inboxId = ref('')
const inboxEntries = ref<{ id: string; question: string; status: string; kind?: string }[]>([])
const methodInbox = computed(() => props.inbox && inboxEntries.value.find(row => row.id === inboxId.value)?.kind === 'methods')
const id = computed(() => props.inbox ? inboxId.value : props.chatId)
const isOwner = computed(() => task.value?.role === 'owner')
const completedResults = computed(() => Object.values(task.value?.runs ?? {}).filter((r) => r.record_id))
const handoff = computed(() => task.value?.handoff)
const current = computed(() => task.value?.runs[task.value?.work ?? handoff.value?.base_run ?? ''])
const runningCostFile = computed(() => task.value?.files.find(file => file.id === current.value?.definition.inputs.costs))
const activeCostFile = computed(() => task.value?.files.find(file => file.id === task.value?.definition?.inputs.costs))
const appliedCostFile = computed(() => {
  const file = activeCostFile.value
  return file?.replaces && runningCostFile.value && file.id !== runningCostFile.value.id ? file : undefined
})
const proposedCostFile = computed(() => task.value?.files.find(file => file.id === feeCandidate.value))
const candidate = computed(() => task.value?.runs[handoff.value?.candidate ?? ''])
const baselineRun = computed(() => task.value?.runs[handoff.value?.base_run ?? ''])
const adopted = computed(() => task.value?.runs[task.value?.adopted ?? ''])
const result = computed(() => task.value?.runs[selectedHistory.value] ?? current.value)
const decisions = computed(() => Object.entries(current.value?.result.decisions ?? {}))
const decision = computed(() => decisions.value[0])
const canTransfer = computed(() => {
  if (!isOwner.value || !current.value?.record_id || result.value?.id !== current.value.id || !recipients.value.length) return false
  const active = handoff.value
  return !active || ['adopted', 'requested'].includes(active.status) ||
    (active.base_run !== task.value?.work && !['returned', 'applied'].includes(active.status))
})
const roles: Purpose[] = ['roster', 'costs', 'mapping']
const analystTab = ref<'analysis'|'conversation'>('analysis')
let inboxRead = 0
const methodHandoffBusy = ref(false)
const methodDetailVersion = ref(0)

async function refreshInbox() {
  const read = ++inboxRead
  const entries = await multi.inbox()
  if (read !== inboxRead) return
  const selected = entries.some(entry => entry.id === inboxId.value)
  inboxEntries.value = entries
  if (!selected) inboxId.value = entries[0]?.id ?? ''
  else if (methodInbox.value) methodDetailVersion.value++
  else await load()
}

function syncHandoffStatus(chatId: string, status?: string) {
  const entry = inboxEntries.value.find(item => item.id === chatId)
  if (status && entry && entry.status !== status) {
    ++inboxRead
    entry.status = status
  }
}

function acceptTaskSnapshot(snapshot: Task) {
  if (snapshot.id !== id.value || (task.value && task.value.revision > snapshot.revision)) return false
  task.value = snapshot
  syncHandoffStatus(snapshot.id, snapshot.handoff?.status)
  return true
}

onMounted(async () => {
  if (props.inbox) await act(refreshInbox)
})
let pending: { id: string; signature: string; body: Record<string, unknown> } | undefined
let epoch = 0

function failure(error: unknown) {
  const e = error as { response?: { status?: number; data?: { detail?: string | { code: string; row?: number; column?: string } } } }
  const data = e.response?.data
  const detail = typeof data === 'string' ? data : data?.detail ?? (data && 'code' in data ? data as { code: string; row?: number; column?: string } : undefined)
  const code = typeof detail === 'object' ? detail.code : detail
  issue.value = code && te('multi.errors.' + code) ? tr('errors.' + code) : tr('failed')
  if (typeof detail === 'object' && detail.row) issue.value += ` (${detail.row}, ${detail.column ?? ''})`
}

async function load() {
  const token = ++epoch
  task.value = undefined
  inputs.value = {}
  issue.value = ''
  selectedHistory.value = ''
  choiceSku.value = ''
  reason.value = ''
  handoffReason.value = ''
  pending = undefined
  recipients.value = []
  feeCandidate.value = ''
  uploadFile.value = undefined
  replacementFile.value = undefined
  purpose.value = 'roster'
  fileDrawer.value = false
  evidenceDrawer.value = false
  transferDrawer.value = false
  changes.value = undefined
  message.value = ''
  store.value = ''
  question.value = ''
  if (!id.value || methodInbox.value) return
  const target = id.value
  try {
    const found = await multi.get(target)
    if (token !== epoch || !acceptTaskSnapshot(found)) return
    inputs.value = { ...found.definition?.inputs }
    if (found.role === 'owner') {
      const options = await multi.reviewers(target)
      if (token === epoch) recipients.value = options
    }
  } catch (error) {
    if (token === epoch && (error as { response?: { status: number } }).response?.status !== 404) failure(error)
  }
}
watch(id, load, { immediate: true })

async function act(operation: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  issue.value = ''
  message.value = ''
  try { await operation() } catch (error) { failure(error) } finally { busy.value = false }
}

function create() {
  return act(async () => {
    if (!id.value) return
    task.value = await multi.start(id.value, {
      store: store.value.trim() || undefined, question: question.value.trim(), parent_record_id: props.parent
    })
    emit('started')
    recipients.value = await multi.reviewers(id.value)
    emit('refresh')
  })
}

function pick(event: Event, replacing = false) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (replacing) replacementFile.value = file
  else uploadFile.value = file
}

function upload(form: HTMLFormElement, replaces?: string) {
  return act(async () => {
    const file = replaces ? replacementFile.value : uploadFile.value
    if (!id.value || !file) return
    const role = replaces ? 'costs' : purpose.value
    const data = new FormData()
    data.append('file', file)
    data.append('purpose', role)
    if (replaces) data.append('replaces', replaces)
    const response = await multi.upload(id.value, data)
    task.value = response.task
    if (replaces) replacementFile.value = undefined
    else uploadFile.value = undefined
    form.reset()
    const item = response.task.files.find(file => file.id === response.file_id)
    if (!response.task.definition && !item?.issue) inputs.value[role] = response.file_id
    if (replaces && item?.issue) issue.value = tr('errors.' + item.issue.code)
    if (replaces && !item?.issue) {
      feeCandidate.value = response.file_id
      changes.value = await multi.replacement(id.value, feeCandidate.value)
    }
  })
}

function retryPreview() {
  return act(async () => {
    if (id.value && feeCandidate.value) changes.value = await multi.replacement(id.value, feeCandidate.value)
  })
}

function cancelPreview() {
  feeCandidate.value = ''
  changes.value = undefined
  issue.value = ''
}

function command(action: string, fields: Record<string, unknown> = {}) {
  return act(async () => {
    const target = id.value
    const token = epoch
    if (!target || !task.value) return
    const signature = JSON.stringify([action, fields])
    if (!pending || pending.id !== target || pending.signature !== signature) {
      pending = { id: target, signature, body: {
        action, revision: task.value.revision, request_id: crypto.randomUUID(), ...fields
      } }
    }
    try {
      const response = await multi.action(target, pending.body)
      if (target !== id.value) {
        syncHandoffStatus(response.task.id, response.task.handoff?.status)
        return
      }
      if (!acceptTaskSnapshot(response.task)) return
      pending = undefined
      if (response.task.definition) inputs.value = { ...response.task.definition.inputs }
      selectedHistory.value = ''
      if (action === 'plan') message.value = tr('plan_' + response.receipt.outcome)
      if (['apply', 'adopt'].includes(action)) message.value = tr('state_' + response.task.handoff?.status)
      if (action === 'apply_file' || action === 'plan') cancelPreview()
      if (['run', 'confirm', 'adopt'].includes(action)) emit('refresh')
      if (action === 'run') fileDrawer.value = false
      if (action === 'request') {
        handoffReason.value = ''
        transferDrawer.value = false
      }
      if (action === 'confirm') choiceSku.value = ''
      if (['return', 'adopt', 'confirm'].includes(action)) reason.value = ''
    } catch (error) {
      if (token !== epoch) return
      const status = (error as { response?: { status: number } }).response?.status
      if (status && status < 500) pending = undefined
      if (status === 409) {
        const latest = await multi.get(target)
        if (token !== epoch) return
        acceptTaskSnapshot(latest)
      }
      throw error
    }
  })
}
function runFiles() {
  return command('run')
}

function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function download(run: Run) {
  return act(async () => {
    if (id.value) downloadBlob(await multi.notebook(id.value, run.id), 'analysis-notebook.zip')
  })
}

function original(file: string) {
  return act(async () => {
    if (!id.value) return
    const value = await multi.original(id.value, file)
    downloadBlob(new Blob([Uint8Array.from(atob(value.base64), c => c.charCodeAt(0))]), value.name)
  })
}
</script>

<template>
  <section class="multi-panel" :aria-label="tr('title')">
    <template v-if="inbox">
      <el-button :disabled="methodHandoffBusy" @click="act(refreshInbox)">{{ tr('inbox') }}</el-button>
      <label v-if="inboxEntries.length">{{ tr('select_task') }}
        <select v-model="inboxId" :disabled="methodHandoffBusy">
          <option value="">{{ tr('choose') }}</option>
          <option v-for="entry in inboxEntries" :key="entry.id" :value="entry.id">{{ entry.question }} · {{ tr('state_' + entry.status) }}</option>
        </select>
      </label>
    </template>
    <p v-if="issue" role="alert" class="error">{{ issue }}</p>
    <p v-if="message" role="status">{{ message }}</p>
    <p v-if="busy" role="status">{{ tr('busy') }}</p>
    <ResultCard v-if="!task && !inbox" :title="label || tr('title')">
      <form class="new-task" @submit.prevent="create">
        <label v-if="!parent">{{ tr('store') }}<input v-model="store" required maxlength="48" /></label>
        <label class="task-question">{{ tr('question') }}<textarea v-model="question" required maxlength="800" rows="2" /></label>
        <el-button type="primary" native-type="submit" :disabled="busy">{{ tr('begin') }}</el-button>
      </form>
    </ResultCard>

    <MethodPanel v-if="methodInbox" :key="inboxId + ':' + methodDetailVersion" :chat-id="inboxId" inbox
      @handoff-status="syncHandoffStatus" @handoff-busy="methodHandoffBusy = $event" />
    <template v-else-if="task">
    <el-segmented v-if="inbox" v-model="analystTab" class="analyst-tabs"
      :options="[{label:ui('analysis'),value:'analysis'},{label:ui('conversation'),value:'conversation'}]" />
    <div class="multi-body" :class="{ 'analyst-body': inbox, 'show-context': analystTab==='conversation' }">
    <div class="multi-primary">
      <p class="original-question">{{ task.question }}</p>
      <div v-if="isOwner" class="files-toolbar">
        <el-button @click="fileDrawer=true">{{ ui('attachment') }}</el-button>
        <button v-for="file in task.files" :key="file.id" class="file-tag" @click="fileDrawer=true">
          {{ file.name }}<span v-if="file.issue"> · {{ tr('errors.' + file.issue.code) }}</span>
        </button>
      </div>
      <ResultInspector v-if="isOwner" v-model="fileDrawer" :title="ui('attachment')">
      <section v-if="runningCostFile" class="revision" :aria-label="ui('replaceCostFile')">
        <h3>{{ ui('replaceCostFile') }}</h3>
        <dl>
          <div><dt>{{ ui('usingFile') }}</dt><dd>{{ runningCostFile.name }}</dd></div>
          <div v-if="activeCostFile && activeCostFile.id !== runningCostFile.id && !appliedCostFile">
            <dt>{{ tr('definition') }}</dt><dd>{{ activeCostFile.name }}</dd>
          </div>
          <div v-if="appliedCostFile || proposedCostFile">
            <dt>{{ ui('replacementFile') }}</dt><dd>{{ (appliedCostFile || proposedCostFile)?.name }}</dd>
          </div>
        </dl>
        <p v-if="current?.result.recorded_fee !== undefined">
          {{ tr('fee') }} · <strong>{{ current.result.recorded_fee }} {{ current.result.currency }}</strong>
        </p>
        <p v-if="issue" role="alert" class="error">{{ issue }}</p>
        <template v-if="appliedCostFile">
          <p role="status">{{ ui('revisionApplied') }}</p>
          <el-button type="primary" :disabled="busy" @click="runFiles">{{ tr('run') }}</el-button>
        </template>
        <template v-else-if="feeCandidate">
          <template v-if="changes">
            <h4>{{ tr('replacement_diff') }}</h4>
            <ul class="revision-changes">
              <li v-for="item in changes" :key="item.fee_id">
                <b>{{ item.fee_id }}</b> · {{ item.old?.join(' / ') ?? '—' }} → {{ item.new?.join(' / ') ?? '—' }}
              </li>
            </ul>
            <el-button type="primary" :disabled="busy" @click="command('apply_file', { file_id: feeCandidate })">{{ tr('apply_file') }}</el-button>
          </template>
          <el-button v-else type="primary" :disabled="busy" @click="retryPreview">{{ tr('replacement_diff') }}</el-button>
          <el-button :disabled="busy" @click="cancelPreview">{{ tr('cancel') }}</el-button>
        </template>
        <form v-else-if="activeCostFile" :key="activeCostFile.id"
          @submit.prevent="upload($event.currentTarget as HTMLFormElement, activeCostFile.id)">
          <label>{{ tr('csv') }}<input type="file" accept=".csv,text/csv" @change="pick($event, true)" /></label>
          <el-button type="primary" native-type="submit" :disabled="busy || !replacementFile">{{ tr('upload') }}</el-button>
        </form>
      </section>
      <p v-else-if="issue" role="alert" class="error">{{ issue }}</p>
      <details class="file-settings" :open="!runningCostFile">
      <summary>{{ ui('fileSettings') }}</summary>
      <form @submit.prevent="upload($event.currentTarget as HTMLFormElement)">
        <label>{{ tr('purpose') }}
          <select v-model="purpose">
            <option v-for="kind in roles" :key="kind" :value="kind">{{ tr(kind) }}</option>
          </select>
        </label>
        <label>{{ tr('csv') }}<input type="file" accept=".csv,text/csv" @change="pick" /></label>
        <button :disabled="busy || !uploadFile">{{ tr('upload') }}</button>
      </form>
      <details v-for="file in task.files" :key="file.id">
        <summary>{{ tr(file.purpose) }} · {{ file.name }} · {{ file.summary.row_count ?? '—' }}</summary>
        <p v-if="file.issue" role="alert">{{ tr('errors.' + file.issue.code) }} · {{ file.issue.row }} {{ file.issue.column }}</p>
        <pre v-if="file.summary.preview">{{ file.summary.preview }}</pre>
        <code>{{ file.sha256 }}</code>
        <button @click="original(file.id)">{{ tr('original') }}</button>
      </details>

      <template v-if="isOwner">
        <div class="row">
          <label v-for="kind in roles" :key="kind">{{ tr(kind) }}
            <select v-model="inputs[kind]">
              <option :value="undefined">{{ tr('choose') }}</option>
              <option v-for="file in task.files.filter(f => f.purpose === kind && !f.issue && (!f.replaces || f.id === task?.definition?.inputs[kind]))"
                :key="file.id" :value="file.id">{{ file.name }} · {{ file.sha256.slice(0, 8) }}</option>
            </select>
          </label>
        </div>
        <label>{{ tr('follow_question') }}<input v-model="followQuestion" maxlength="800" /></label>
        <div class="row">
          <el-button :disabled="busy || !inputs.roster || !inputs.costs" @click="command('plan', { inputs, question: followQuestion || undefined })">{{ tr('plan') }}</el-button>
          <el-button v-if="!appliedCostFile" type="primary" :disabled="busy || !task.definition" @click="runFiles">{{ tr('run') }}</el-button>
        </div>
        <details v-if="task.definition"><summary>{{ tr('definition') }}</summary><pre>{{ task.definition }}</pre></details>
      </template>
      </details>
      </ResultInspector>
      <label v-if="isOwner && Object.keys(task.runs).length > 1" class="history">{{ tr('history') }}
          <select v-model="selectedHistory">
            <option value="">{{ tr('current') }}</option>
            <option v-for="run in completedResults" :key="run.id" :value="run.id">
              {{ run.result.sales ?? '—' }} / {{ run.result.recorded_fee ?? '—' }} · {{ tr('state_' + run.result.state) }}
            </option>
          </select>
      </label>

      <ResultCard v-if="result" :data-run="result.id" :title="tr('result')"
        :scope="tr('store') + ' ' + task.scope + ' · ' + task.topic.window" :status="tr('state_' + result.result.state)">
        <p v-if="adopted?.id === result.id">{{ tr('adopted') }}</p>
        <p v-if="result.result.independent_source_sales">{{ tr('source_sales') }} {{ result.result.independent_source_sales }} {{ result.result.currency }}</p>
        <dl v-if="Object.keys(result.result.decisions).length" class="mapping-summary">
          <template v-for="(choices, code) in result.result.decisions" :key="code">
            <dt>{{ tr('mapping_choice') }} {{ code }}</dt>
            <dd>{{ choices.join(' / ') }}</dd>
          </template>
        </dl>
        <div v-if="result.result.rows" class="totals">
          <div><span>{{ tr('sales') }}</span><strong data-multi-value="net">{{ result.result.sales }}</strong><small>{{ result.result.currency }}</small></div>
          <div><span>{{ tr('fee') }}</span><strong data-multi-value="fee">{{ result.result.recorded_fee }}</strong><small>{{ result.result.currency }}</small></div>
        </div>
        <p v-if="result.result.fee_coverage">{{ tr('coverage') }} · {{ tr('sales') }} {{ result.result.sales_coverage?.join('/') }} ·
          {{ tr('fee') }} {{ result.result.fee_coverage.join('/') }} · {{ tr('unmatched') }} {{ result.result.unmatched_fee }}</p>
        <div v-if="result.result.rows" class="table-wrap" tabindex="0" role="region" :aria-label="tr('sku')">
          <table>
            <thead><tr><th>{{ tr('sku') }}</th><th>{{ tr('sales') }}</th><th>{{ tr('fee') }}</th></tr></thead>
            <tbody><tr v-for="row in result.result.rows" :key="row.sku"><th scope="row">{{ row.sku }}</th>
              <td>{{ row.net ?? tr('missing') }}</td><td>{{ row.fee ?? tr('missing') }}</td>
            </tr></tbody>
          </table>
        </div>
        <ResultInspector v-model="evidenceDrawer" :title="ui('inspect')">
          <p>{{ task.topic.version }} · {{ result.definition.window }}</p>
          <p v-for="file in result.evidence.inputs" :key="file.sha256">{{ file.name }} · {{ file.rows }}</p>
          <details><summary>{{ tr('evidence') }}</summary><pre>{{ result.evidence }}</pre></details>
        </ResultInspector>
        <template #actions>
          <el-button text @click="evidenceDrawer=true">{{ ui('inspect') }}</el-button>
          <el-button text :disabled="busy" @click="download(result)">{{ tr('notebook') }}</el-button>
          <el-button v-if="isOwner && result.record_id" text @click="emit('follow', result)">{{ tr('continue_result') }}</el-button>
          <el-button v-if="canTransfer" text @click="transferDrawer=true">{{ ui('analyst') }}</el-button>
        </template>
      </ResultCard>

      <ResultCard v-if="handoff" class="handoff-result" :title="tr('handoff')"
        :scope="handoff.receiver_name + ' · ' + handoff.question" :status="tr('state_' + handoff.status)">
        <button v-if="!isOwner && handoff.status === 'requested'" :disabled="busy" @click="command('accept')">{{ tr('accept') }}</button>
        <template v-if="candidate">
          <div class="table-wrap" data-multi-comparison tabindex="0" role="region" :aria-label="tr('comparison')">
            <table>
              <thead><tr><th>{{ tr('comparison') }}</th><th>{{ ui('beforeHandoff') }}</th><th>{{ ui('returnedCandidate') }}</th></tr></thead>
              <tbody>
                <tr><th scope="row">{{ tr('sales') }}</th><td>{{ baselineRun?.result.sales ?? tr('missing') }}</td><td>{{ candidate.result.sales ?? tr('missing') }}</td></tr>
                <tr><th scope="row">{{ tr('fee') }}</th><td>{{ baselineRun?.result.recorded_fee ?? tr('missing') }}</td><td>{{ candidate.result.recorded_fee ?? tr('missing') }}</td></tr>
              </tbody>
            </table>
          </div>
          <p v-if="baselineRun && (baselineRun.result.sales == null || baselineRun.result.recorded_fee == null)">
            {{ ui('beforeHandoff') }} · {{ tr('state_' + baselineRun.result.state) }}
          </p>
          <p v-if="handoff.status === 'adopted' && adopted">
            {{ ui('adoptedResult') }} · {{ adopted.result.sales ?? tr('missing') }} / {{ adopted.result.recorded_fee ?? tr('missing') }} {{ adopted.result.currency }}
          </p>
          <p>{{ handoff.change }}</p>
          <details><summary>{{ tr('candidate_evidence') }}</summary><pre>{{ candidate.evidence }}</pre></details>
          <button @click="download(candidate)">{{ tr('candidate_notebook') }}</button>
          <button v-if="isOwner && handoff.status === 'returned'" :disabled="busy" @click="command('apply')">{{ tr('apply_draft') }}</button>
        </template>
        <p v-else>{{ ui('notReady') }}</p>
      </ResultCard>
      <template v-if="current && (isOwner || handoff?.status === 'accepted')">
        <label v-if="decision" :key="decision[0]">{{ tr('mapping_choice') }} · {{ decision[0] }}
          <select v-model="choiceSku">
            <option value="">{{ tr('choose') }}</option><option v-for="sku in decision[1]" :key="sku">{{ sku }}</option>
          </select>
        </label>
        <label v-if="!isOwner">{{ tr('roster_field') }}<input v-model="rosterField" :placeholder="current.definition.fields.roster_sku" /></label>
        <label v-if="decision || !isOwner || handoff?.status === 'applied'">{{ tr('reason') }}<input v-model="reason" maxlength="800" /></label>
        <button v-if="isOwner && decision" :disabled="busy || !choiceSku || !reason.trim()"
          @click="command('confirm', { external: decision[0], sku: choiceSku, reason })">{{ tr('confirm') }}</button>
        <button v-if="!isOwner" :disabled="busy || !reason.trim() || (!!decision && !choiceSku)"
          @click="command('return', { external: decision?.[0], sku: choiceSku || undefined, reason, roster_field: rosterField || undefined })">{{ tr('return') }}</button>
        <button v-if="isOwner && handoff?.status === 'applied'" :disabled="busy || !reason.trim()" @click="command('adopt', { reason })">{{ tr('adopt') }}</button>
        <ResultInspector v-if="canTransfer" v-model="transferDrawer" :title="ui('analyst')">
          <p>{{ task.question }} · {{ current.result.sales ?? '—' }} / {{ current.result.recorded_fee ?? '—' }}</p>
          <p v-if="issue" role="alert" class="error">{{ issue }}</p>
          <label>{{ tr('handoff_question') }}<input v-model="handoffReason" maxlength="800" /></label>
          <label>{{ tr('reviewer') }}<select v-model="receiver"><option value="">{{ tr('choose') }}</option>
            <option v-for="reviewer in recipients" :key="reviewer.id" :value="reviewer.id">{{ reviewer.name }}</option>
          </select></label>
          <label class="share"><input v-model="share" type="checkbox" />{{ tr('share') }}</label>
          <p v-if="share">{{ Object.values(current.evidence.inputs).map(file => file.name).join(' · ') }} · {{ task.topic.label }}</p>
          <button :disabled="busy || !share || !receiver || !handoffReason.trim()" @click="command('request', { receiver, share, reason: handoffReason, base_run: current.id })">{{ tr('request') }}</button>
        </ResultInspector>
      </template>
    </div>
    <aside v-if="inbox" class="analyst-context">
      <h3>{{ ui('conversation') }}</h3>
      <p>{{ task.question }}</p>
      <dl>
        <dt>{{ tr('store') }}</dt><dd>{{ task.scope }}</dd>
        <dt>{{ ui('beforeHandoff') }}</dt><dd>{{ baselineRun?.result.sales ?? tr('missing') }} / {{ baselineRun?.result.recorded_fee ?? tr('missing') }}</dd>
      </dl>
      <p v-for="file in task.files" :key="file.id">{{ file.name }}</p>
    </aside>
    </div>
    </template>
  </section>
</template>

<style scoped>
.multi-panel { max-width: 960px; margin: 16px auto; padding: 0 24px 24px; min-width: 0; }
.original-question { margin-left:auto; padding:10px 16px; background:#f1f4f3; border-radius:10px; width:fit-content; max-width:88%; }
.new-task .task-question { flex:1 1 250px; }
.task-question textarea { padding:8px; border:1px solid #ccd8d1; border-radius:8px; font:inherit; }
.files-toolbar { display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin:12px 0; }
.file-tag { border-color:#d8e4de; background:#f1f7f3; color:#385d50; font-size:13px; }
.revision { padding: 12px 16px; border: 1px solid #dfe5e2; border-radius: 10px; background: #fafcfb; }
.revision dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr)); gap: 8px 16px; }
.revision dt { color: #535b63; font-size: 13px; }
.revision dd { margin: 4px 0 0; overflow-wrap: anywhere; }
.revision-changes { padding-inline-start: 20px; margin: 6px 0 12px; overflow-wrap: anywhere; }
.file-settings { padding-top: 10px; }
.file-settings summary { cursor: pointer; }
.file-settings code { overflow-wrap: anywhere; }
.history { width:fit-content; margin:8px 0 8px auto; display:flex; align-items:center; flex-direction:row; }
.totals { display:flex; flex-wrap:wrap; gap:24px; margin:4px 0 12px; }
.totals span { display:block; color:#535b63; }
.totals strong { font-size:30px; font-variant-numeric:tabular-nums; }
.totals small { margin-left:6px; }
.mapping-summary { display:grid; grid-template-columns:max-content 1fr; gap:6px 16px; border-top:1px solid #e5ebe7; padding-top:12px; }
.mapping-summary dd { margin:0; overflow-wrap:anywhere; }
.handoff-result { margin-top:16px; }
.multi-panel:has(.analyst-body) { max-width:1240px; }
.analyst-body { display:grid; grid-template-columns:minmax(0,2fr) minmax(0,1fr); gap:24px; }
.multi-primary { min-width:0; }
.analyst-context { padding:16px; background:#f5f8f6; border:1px solid #e5ebe7; border-radius:12px; height:fit-content; }
.analyst-context dd { margin:0 0 8px; }
.analyst-tabs { display:none; margin:8px 0; }
h2, h3 { margin: 8px 0; font-weight: 650; }
p, summary { margin: 6px 0; overflow-wrap: anywhere; }
form, .row { display: flex; flex-wrap: wrap; align-items: end; gap: 8px; margin: 8px 0; }
label { display: inline-flex; flex-direction: column; max-width: 100%; gap: 4px; margin: 4px 8px 4px 0; }
.share { flex-direction: row; align-items: center; }
input, select, button:not(.ed-button) { min-height: 36px; max-width: 100%; padding: 4px 8px; background: var(--ed-bg-color, #fff); color: inherit; border: 1px solid #858e92; border-radius: 4px; font: inherit; }
button:not(.ed-button) { margin: 4px 8px 4px 0; cursor: pointer; }
button:not(.ed-button):disabled { opacity: .55; cursor: default; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; padding: 8px; background: var(--ed-bg-color-page, #f3f5f5); max-height: 240px; overflow: auto; }
.table-wrap { overflow-x: auto; }
.table-wrap:focus-visible { outline: 2px solid #166451; outline-offset: -2px; }
table { width: 100%; text-align: left; border-collapse: collapse; }
td, th { border-bottom: 1px solid #d8dedf; padding: 6px; }
.error { color: #a22222; }
td { text-align:right; font-variant-numeric:tabular-nums; }
@media (max-width:1024px) { .multi-panel { padding-inline:16px; } }
@media (max-width:1439px) {
  .analyst-tabs { display:inline-flex; }
  .analyst-body { display:block; }
  .analyst-body:not(.show-context) .analyst-context,.analyst-body.show-context .multi-primary { display:none; }
}
</style>
