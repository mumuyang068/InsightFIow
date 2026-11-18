<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AnalysisState } from '@/api/analysis'
import { analysisPeriods, resultNumber } from '../workspace/resultFormat'

const props = defineProps<{ state: AnalysisState; hasQuery?: boolean }>()
const emit = defineEmits<{ query: [recordId: string] }>()
const { t, locale } = useI18n()
const amount = (value: number, plus = false) => resultNumber(value, locale.value, plus)
const periods = computed(() => analysisPeriods(props.state.scope))
const queries = computed(() => props.state.steps.filter(
  (step) => step.record_id && step.status === 'verified' && step.purpose !== 'quality_check'
))
</script>

<template>
  <section class="analysis-evidence-view">
    <dl class="analysis-scope">
      <template v-if="state.scope.store">
        <dt>{{ t('cbi.store') }}</dt>
        <dd>{{ state.scope.store }}</dd>
      </template>
      <template v-if="state.scope.current">
        <dt>{{ t('cbi.current_date') }}</dt>
        <dd>{{ periods.current }}</dd>
      </template>
      <template v-if="state.scope.baseline">
        <dt>{{ t('cbi.baseline_date') }}</dt>
        <dd>{{ periods.baseline }}</dd>
      </template>
      <template v-if="state.scope.definition">
        <dt>{{ t('cbi.definition') }}</dt>
        <dd>
          {{ state.scope.definition_label }} · {{ state.scope.definition }}
          <small v-if="state.scope.source">{{ t('cbi.source.' + state.scope.source) }}</small>
        </dd>
      </template>
      <dt>{{ t('cbi.timezone') }}</dt>
      <dd>
        {{
          state.scope.timezone === 'source_calendar'
            ? t('cbi.source_calendar')
            : state.scope.timezone
        }}
      </dd>
    </dl>
    <p>{{ t('cbi.data_version') }} · {{ state.data_version || state.config_version }}</p>
    <p v-if="state.config_digest">
      {{ t('cbiTheme.configurationVersion') }} · {{ state.config_version }}
    </p>
    <div class="quality-evidence" data-testid="analysis-quality-evidence">
      <strong>{{ t('themeQuality.tab') }}</strong>
      <template v-if="state.quality">
        <p>{{ t('themeQuality.status.' + state.quality.status) }}</p>
        <p v-if="state.quality.checked_at">
          {{ new Date(state.quality.checked_at).toLocaleString(locale) }}
        </p>
        <p v-if="state.quality.report_ids.length">
          {{ t('themeQuality.reportId') }} · {{ state.quality.report_ids.join(' · ') }}
        </p>
        <ul v-if="state.quality.checks.length">
          <li v-for="(check, index) in state.quality.checks" :key="check.rule_id + ':' + check.scope + ':' + index">
            {{ check.label || check.rule_id }} · {{ t('themeQuality.period.' + check.scope) }} ·
            {{ t('themeQuality.status.' + check.status) }}
          </li>
        </ul>
      </template>
      <p v-else>{{ t('themeQuality.noRunRecord') }}</p>
    </div>
    <p v-if="state.investigation?.plan?.method_version">
      {{ t('resultExplorer.methodVersion') }} · {{ state.investigation.plan.method_version }}
    </p>
    <details v-if="state.source_proof">
      <summary>{{ t('resultExplorer.sourceProof') }}</summary>
      <p>
        {{ state.source_proof.view }} ·
        {{ t('resultExplorer.sourceRows', { count: state.source_proof.rows }) }}
      </p>
      <code>{{ state.source_proof.digest }}</code>
    </details>
    <p v-if="state.publication_confirmation">
      {{ t('cbi.data_version') }} · {{ state.publication_confirmation.prior_version }} →
      {{ state.publication_confirmation.version }}
    </p>
    <div v-if="state.evidence?.members" class="analysis-evidence">
      <strong>{{ t('cbi.evidence') }}</strong>
      <p v-for="(value, day) in state.evidence.differences" :key="day">
        {{ day }} · {{ state.evidence.reference_label }}
        {{
          state.evidence.reference_totals?.[day] == null
            ? '—'
            : amount(state.evidence.reference_totals[day])
        }}
        {{ t('cbi.yuan') }} · {{ t('cbi.difference') }} {{ amount(value, true) }}
        {{ t('cbi.yuan') }}
      </p>
      <div class="evidence-table">
        <table>
          <thead>
            <tr>
              <th>{{ t('cbi.member') }}</th>
              <th>{{ t('cbi.period') }}</th>
              <th>{{ t('cbi.amount') }}</th>
              <th>{{ t('cbi.paid') }}</th>
              <th>{{ t('cbi.refunded') }}</th>
              <th>{{ t('cbi.inclusion') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="member in state.evidence.members"
              :key="member.business_date + member.refund_id"
            >
              <td>{{ member.refund_id }} · {{ member.line_id }}</td>
              <td>{{ member.business_date }}</td>
              <td>{{ amount(member.amount_cents / 100) }} {{ t('cbi.yuan') }}</td>
              <td>{{ member.paid_at_utc }} UTC</td>
              <td>{{ member.refund_at_utc }} UTC</td>
              <td>
                {{ t(member.in_reference ? 'cbi.reference_in' : 'cbi.reference_out') }} ·
                {{ t(member.in_cohort ? 'cbi.cohort_in' : 'cbi.cohort_out') }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div
      v-if="hasQuery && queries.length"
      class="analysis-queries"
    >
      <el-button
        v-for="(step, index) in queries"
        :key="step.record_id"
        text
        @click="emit('query', step.record_id!)"
        >{{ t('cbi.query', { number: index + 1 }) }}</el-button
      >
    </div>
  </section>
</template>

<style scoped>
.analysis-evidence-view {
  min-width: 0;
  overflow-wrap: anywhere;
}
.analysis-evidence-view p {
  margin: 8px 0;
}
.quality-evidence {
  margin: 12px 0;
  padding: 8px 12px;
  border: 1px solid var(--ed-border-color-lighter);
  border-radius: 6px;
}
.quality-evidence ul {
  margin: 8px 0;
  padding-inline-start: 20px;
}
.analysis-scope {
  display: grid;
  grid-template-columns: minmax(96px, max-content) minmax(0, 1fr);
  gap: 6px 12px;
  margin: 0;
}
.analysis-scope dt {
  color: #535b63;
}
.analysis-scope dd {
  margin: 0;
}
.analysis-scope small {
  display: block;
  color: #535b63;
}
.evidence-table {
  max-width: 100%;
  overflow: auto;
}
.evidence-table table {
  width: 100%;
  text-align: left;
  border-collapse: collapse;
}
.evidence-table td,
.evidence-table th {
  padding: 8px;
  border-bottom: 1px solid #dee0e3;
}
.analysis-queries {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
