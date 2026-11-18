<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { EvaluationCase, EvaluationRow, EvaluationTurnReceipt } from '@/api/themeEvaluation'
import { evaluationValue } from '@/api/themeEvaluation'

const props = defineProps<{
  row: EvaluationRow
  testCase?: EvaluationCase
  label: string
}>()
const { t, te } = useI18n()
const selection = ref<number | 'latest'>('latest')
const attempts = computed(() =>
  props.row.attempts?.length
    ? props.row.attempts
    : [props.row.first, props.row.latest]
        .filter((item) => item != null)
        .filter((item, index, list) => list.findIndex(({ id }) => id === item.id) === index)
)
const attempt = computed(() =>
  selection.value === 'latest'
    ? (props.row.latest ?? attempts.value[attempts.value.length - 1])
    : attempts.value.find(({ number }) => number === selection.value)
)
watch(
  () => props.row.case_id,
  () => {
    selection.value = 'latest'
  }
)
const tr = (key: string) => t('themeEvaluation.' + key)
function named(prefix: string, value?: unknown) {
  const label = evaluationValue(value)
  const key = 'themeEvaluation.' + prefix + '.' + label
  return te(key) ? t(key) : label
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function expectedTurn(index: number) {
  const turns = props.testCase?.expected?.turns
  return record(Array.isArray(turns) ? turns[index] : undefined)
}

function condition(plan: unknown) {
  const value = record(plan)
  return [
    { label: 'metrics', value: value.metrics },
    { label: 'period', value: [value.start, value.end].filter(Boolean).join(' → ') },
    {
      label: 'comparisonPeriod',
      value: [value.compare_start, value.compare_end].filter(Boolean).join(' → '),
    },
    { label: 'filters', value: value.filters },
    { label: 'group', value: value.group },
    { label: 'operation', value: value.operation },
  ].filter(({ value }) => value !== undefined && value !== '')
}

function receiptSections(turn: EvaluationTurnReceipt) {
  const state = turn.state ?? {}
  return [
    { label: 'outputs', value: state.outputs },
    { label: 'quality', value: state.quality },
    { label: 'investigation', value: state.investigation },
  ].filter(({ value }) => value != null)
}
</script>

<template>
  <section class="receipt">
    <header>
      <h3>{{ label }}</h3>
      <el-select v-if="attempts.length > 1" v-model="selection" :aria-label="tr('attempts')">
        <el-option value="latest" :label="tr('latest')" />
        <el-option
          v-for="item in attempts"
          :key="item.id"
          :value="item.number"
          :label="
            item.number === attempts[0]?.number
              ? tr('first')
              : t('themeEvaluation.attempt', { number: item.number })
          "
        />
      </el-select>
    </header>
    <p>
      {{ tr('first') }} ·
      <strong>{{
        named('status', row.first?.judgement?.status ?? row.first?.status ?? row.status)
      }}</strong>
      <span v-if="attempts.length > 1">
        · {{ tr('latest') }} ·
        <strong>{{
          named('status', row.latest?.judgement?.status ?? row.latest?.status)
        }}</strong></span
      >
    </p>
    <template v-if="attempt">
      <ul v-if="attempt.judgement?.issues?.length" class="issues">
        <li v-for="(issue, i) in attempt.judgement.issues" :key="i">
          <strong>{{ named('dimension', issue.dimension) }}</strong>
          <span v-if="issue.turn"> · {{ t('themeEvaluation.turn', { number: issue.turn }) }}</span>
          <p>{{ named('reason', issue.detail) }}</p>
          <div
            v-if="issue.expected !== undefined || issue.actual !== undefined"
            class="payload-diff"
          >
            <div>
              <small>{{ tr('expected') }}</small>
              <pre>{{ evaluationValue(issue.expected) }}</pre>
            </div>
            <div>
              <small>{{ tr('actual') }}</small>
              <pre>{{ evaluationValue(issue.actual) }}</pre>
            </div>
          </div>
        </li>
      </ul>
      <section v-for="(turn, i) in attempt.observation?.turns ?? []" :key="i" class="turn-receipt">
        <h4>{{ t('themeEvaluation.turn', { number: i + 1 }) }}</h4>
        <p v-if="testCase?.turns[i]?.question">{{ testCase.turns[i]?.question }}</p>
        <dl class="conditions">
          <div>
            <dt>{{ tr('taskStatus') }}</dt>
            <dd>{{ named('status', turn.state?.task_status) }}</dd>
          </div>
          <div v-if="turn.record_id">
            <dt>{{ tr('record') }}</dt>
            <dd>
              <RouterLink
                v-if="turn.chat_id"
                :to="{
                  path: '/chat/index',
                  query: { chat: String(turn.chat_id), record: String(turn.record_id) },
                }"
                target="_blank"
              >
                {{ turn.record_id }}
              </RouterLink>
              <template v-else>{{ turn.record_id }}</template>
            </dd>
          </div>
          <div v-if="turn.state?.config_version">
            <dt>{{ tr('configuration') }}</dt>
            <dd>{{ evaluationValue(turn.state.config_version) }}</dd>
          </div>
          <div v-for="item in condition(turn.state?.semantic_plan)" :key="item.label">
            <dt>{{ tr(item.label) }}</dt>
            <dd>{{ evaluationValue(item.value) }}</dd>
          </div>
        </dl>
        <div v-if="Object.keys(expectedTurn(i)).length" class="expected-turn">
          <strong>{{ tr('expected') }}</strong>
          <p v-if="expectedTurn(i).task_status">
            {{ tr('taskStatus') }} · {{ named('status', expectedTurn(i).task_status) }}
          </p>
          <dl v-if="condition(expectedTurn(i).plan).length" class="conditions">
            <div v-for="item in condition(expectedTurn(i).plan)" :key="item.label">
              <dt>{{ tr(item.label) }}</dt>
              <dd>{{ evaluationValue(item.value) }}</dd>
            </div>
          </dl>
        </div>
        <details v-for="section in receiptSections(turn)" :key="section.label">
          <summary>{{ tr(section.label) }}</summary>
          <pre>{{ evaluationValue(section.value) }}</pre>
        </details>
        <details v-if="turn.queries?.length">
          <summary>{{ tr('sqlReceipts') }} ({{ turn.queries.length }})</summary>
          <div v-for="(query, q) in turn.queries" :key="q" class="query-receipt">
            <strong
              >{{ named('query', query.purpose) }}
              <span v-if="query.record_id">· {{ query.record_id }}</span></strong
            >
            <pre v-if="query.sql">{{ query.sql }}</pre>
          </div>
        </details>
        <details v-if="turn.model != null || turn.events != null || turn.raw_rows != null">
          <summary>{{ tr('rawReceipts') }}</summary>
          <pre>{{
            evaluationValue({ model: turn.model, rows: turn.raw_rows, events: turn.events })
          }}</pre>
        </details>
      </section>
      <details v-if="attempt.observation?.usage || attempt.model_calls !== undefined" class="usage">
        <summary>{{ tr('usage') }}</summary>
        <p v-if="attempt.model_calls !== undefined">
          {{ tr('modelCalls') }} · {{ attempt.model_calls }}
        </p>
        <pre v-if="attempt.observation?.usage">{{
          evaluationValue(attempt.observation.usage)
        }}</pre>
      </details>
    </template>
  </section>
</template>

<style scoped lang="less">
.receipt,
.turn-receipt,
.issues {
  min-width: 0;
  display: grid;
  align-content: start;
  gap: 8px;
  overflow-wrap: anywhere;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
header :deep(.ed-select) {
  width: 160px;
}
h3,
h4,
p,
dd {
  margin: 0;
}
.issues {
  list-style: none;
  margin: 0;
  padding: 0;
}
.issues li {
  padding: 8px 10px;
  border: 1px solid var(--ed-border-color-lighter);
  border-radius: 4px;
}
.issues p {
  margin: 4px 0;
}
.conditions,
.payload-diff {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.conditions {
  margin: 0;
}
dt {
  color: var(--ed-text-color-regular);
  font-size: 12px;
}
dd {
  white-space: pre-wrap;
}
.turn-receipt {
  padding: 10px;
  border: 1px solid var(--ed-border-color-light);
  border-radius: 6px;
}
.expected-turn {
  display: grid;
  gap: 4px;
  padding-top: 8px;
  border-top: 1px solid var(--ed-border-color-lighter);
}
summary {
  cursor: pointer;
}
pre {
  margin: 4px 0;
  padding: 8px;
  background: var(--ed-fill-color-light);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.query-receipt {
  margin: 8px 0 0;
}
</style>
