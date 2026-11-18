<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Investigation, InvestigationStep } from '@/api/analysis'
import ComparisonResult from './ComparisonResult.vue'
import ResultSeriesTable from './ResultSeriesTable.vue'
import { resultNumber } from './resultFormat'

const props = defineProps<{
  investigation: Investigation
  hasQuery?: boolean
  primaryReason?: string
}>()
const emit = defineEmits<{ query: [recordId: string] }>()
const { t, locale } = useI18n()
const process = ref<HTMLDetailsElement>()
const active = ref<InvestigationStep>()
const inspector = ref<HTMLElement>()
const isComparison = computed(() => props.investigation.plan?.execution_scope === 'single_partition')
const steps = computed(() =>
  props.investigation.steps.length || !props.investigation.first_failure
    ? props.investigation.steps
    : [props.investigation.first_failure]
)
function show(step: InvestigationStep) {
  active.value = step
  if (process.value) process.value.open = true
  void nextTick(() => inspector.value?.focus())
}
watch(
  () => props.investigation,
  () => {
    if (active.value && !props.investigation.steps.includes(active.value)) active.value = undefined
  }
)
defineExpose({ show })
</script>

<template>
  <section v-if="!isComparison || investigation.status !== 'completed'" class="investigation">
    <div v-if="investigation.findings.length" class="investigation-findings">
      <h4>{{ t('resultExplorer.findings') }}</h4>
      <ul>
        <li v-for="finding in investigation.findings" :key="finding.fact_id">
          <span>{{ finding.label ?? t('resultExplorer.unclassified') }}</span>
          <strong :data-direction="finding.direction"
            >{{ resultNumber(Number(finding.delta), locale, true) }}
            {{ finding.unit === 'CNY' ? t('cbi.yuan') : finding.unit }}</strong
          >
        </li>
      </ul>
    </div>
    <p
      v-if="!isComparison && investigation.reason_status === 'unconfirmed' && investigation.status !== 'running'"
      class="investigation-cause"
    >
      {{ t('resultExplorer.unconfirmed') }}
    </p>
    <p
      v-if="investigation.stop_reason && investigation.status !== 'completed' && investigation.stop_reason !== primaryReason"
      class="investigation-stop"
    >
      {{ t('resultExplorer.stop.' + investigation.stop_reason, investigation.stop_reason) }}
    </p>
    <details
      v-if="steps.length || investigation.status === 'completed'"
      ref="process"
      data-testid="cbi-investigation-process"
    >
      <summary>{{ t('resultExplorer.process') }}</summary>
      <ol class="investigation-steps">
        <li v-for="(step, index) in steps" :key="index">
          <div>
            <strong>{{ step.label ?? t('resultExplorer.steps.' + step.kind) }}</strong>
            <small>{{ t('resultExplorer.stepStatus.' + step.status, step.status) }}</small>
          </div>
          <button
            v-if="step.decomposition || step.table || step.tables?.length"
            type="button"
            @click="show(step)"
          >
            {{ t('resultExplorer.viewStep') }}
          </button>
          <button
            v-if="hasQuery && step.record_id && step.status === 'verified'"
            type="button"
            @click="emit('query', step.record_id)"
          >
            {{ t('cbi.query', { number: index + 1 }) }}
          </button>
        </li>
      </ol>
      <section v-if="active" ref="inspector" tabindex="-1" class="investigation-step-result">
        <strong>{{ active.label ?? t('resultExplorer.steps.' + active.kind) }}</strong>
        <ComparisonResult v-if="active.decomposition" :result="active.decomposition" show-data />
        <ResultSeriesTable
          v-if="active.table && !active.tables?.length"
          :table="active.table"
          :rows="[]"
          :trend="false"
        />
        <template v-for="(item, index) in active.tables" :key="index">
          <p>{{ t('cbi.output.' + item.period, item.period) }}</p>
          <ResultSeriesTable :table="item" :rows="[]" :trend="false" />
          <p v-if="item.truncated">{{ t('cbi.semantic_limit', { count: item.rows.length }) }}</p>
          <button
            v-if="hasQuery && active.records?.[index]"
            type="button"
            @click="emit('query', active.records[index])"
          >
            {{ t('cbi.query', { number: index + 1 }) }}
          </button>
        </template>
      </section>
      <p
        v-if="investigation.stop_reason && investigation.status === 'completed'"
        class="investigation-stop"
      >
        {{ t('resultExplorer.stop.' + investigation.stop_reason, investigation.stop_reason) }}
      </p>
    </details>
  </section>
</template>

<style scoped>
.investigation {
  display: grid;
  gap: 9px;
  margin-top: 12px;
  border-top: 1px solid #e1e8e3;
  padding-top: 12px;
  min-width: 0;
}
.investigation h4,
.investigation p {
  margin: 0;
}
.investigation-cause,
.investigation-stop {
  color: #46514d;
  line-height: 1.6;
}
.investigation ul,
.investigation ol {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
}
.investigation-findings li,
.investigation-steps li {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 4px 12px;
  padding: 6px 0;
}
.investigation-findings strong {
  font-variant-numeric: tabular-nums;
}
.investigation-findings strong[data-direction='increase'] {
  color: #17634d;
}
.investigation-findings strong[data-direction='decrease'] {
  color: #9b4940;
}
.investigation summary {
  cursor: pointer;
  color: #17634d;
  font-weight: 600;
  padding: 4px 0;
}
.investigation ol {
  padding-left: 10px;
  border-left: 2px solid #dbe7df;
}
.investigation small {
  margin-left: 10px;
  color: #535b63;
}
.investigation button {
  cursor: pointer;
  background: transparent;
  border: 0;
  color: #17634d;
  text-decoration: underline;
  font: inherit;
}
.investigation-step-result {
  display: grid;
  gap: 8px;
  border: 1px solid #dbe7df;
  border-radius: 8px;
  padding: 10px;
  margin: 8px 0;
}
</style>
