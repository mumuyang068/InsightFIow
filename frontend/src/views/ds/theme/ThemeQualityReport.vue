<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type {
  ThemeQualityReport,
  ThemeQualityRuleType,
  ThemeQualityStatus,
  TopicSemantics,
} from '@/api/theme'

const props = defineProps<{ report: ThemeQualityReport; content: TopicSemantics }>()
const emit = defineEmits<{ export: [] }>()
const { t, locale } = useI18n()
const issuesOnly = ref(false)
const selectedKind = ref<ThemeQualityRuleType | ''>('')
const statuses: ThemeQualityStatus[] = [
  'passed', 'warning', 'failed', 'incomplete', 'no_samples', 'not_applicable',
]
const categories: ThemeQualityRuleType[] = ['unique', 'not_null', 'range', 'freshness']
const time = computed(() => new Date(props.report.completed_at).toLocaleString(locale.value))
const results = computed(() => props.report.results.filter(
  ({ type, status }) => (!selectedKind.value || selectedKind.value === type) &&
    (!issuesOnly.value || ['warning', 'failed', 'incomplete'].includes(status))
))
const groups = computed(() => categories.map((kind) => ({
  kind,
  items: results.value.filter(({ type }) => type === kind),
})).filter(({ items }) => items.length))
const queryByRef = computed(() => new Map(props.report.queries.map((query) => [query.query_ref, query])))
const range = (start?: string | null, end?: string | null) =>
  start && end ? t('themeQuality.windowLabel', { start, end }) : t('themeQuality.period.readable')
const number = (value?: number | null) => value == null ? '—' : value.toLocaleString(locale.value)
watch(() => props.report.id, () => {
  issuesOnly.value = false
  selectedKind.value = ''
})
</script>

<template>
  <section class="saved-report" data-testid="theme-quality-report" :data-report="report.id">
    <div class="report-heading">
      <div>
        <h2>{{ t('themeQuality.reportOverview') }}</h2>
        <p>{{ report.source.view }} · {{ report.source_mode ? t('themeQuality.' + report.source_mode) : '—' }} · {{ time }}</p>
      </div>
      <el-button data-testid="theme-quality-export" @click="emit('export')">
        {{ t('themeQuality.export') }}
      </el-button>
    </div>
    <dl class="scope-info">
      <div>
        <dt>{{ t('themeQuality.statusLabel') }}</dt>
        <dd><span class="status" :data-status="report.status">{{ t('themeQuality.status.' + report.status) }}</span></dd>
      </div>
      <div><dt>{{ t('themeQuality.period.current') }}</dt><dd>{{ range(report.scope.start, report.scope.end) }}</dd></div>
      <div v-if="report.scope.compare_start">
        <dt>{{ t('themeQuality.period.baseline') }}</dt>
        <dd>{{ range(report.scope.compare_start, report.scope.compare_end) }}</dd>
      </div>
      <div v-if="report.checked_on"><dt>{{ t('themeQuality.checkedOn') }}</dt><dd>{{ report.checked_on }}</dd></div>
      <div v-if="report.draft_id">
        <dt>{{ t('themeQuality.configuration') }}</dt>
        <dd>{{ t('themeConfig.revision', { revision: report.revision }) }} · {{ report.draft_id }}</dd>
      </div>
      <div v-else><dt>{{ t('themeQuality.configuration') }}</dt><dd>{{ report.version }}</dd></div>
      <div><dt>{{ t('themeQuality.reportId') }}</dt><dd>{{ report.id }}</dd></div>
    </dl>
    <p v-if="report.reason" role="status">{{ t('themeQuality.reportReason.' + report.reason) }}</p>
    <dl v-if="report.results.length" class="status-grid">
      <div v-for="status in statuses" :key="status">
        <dt>{{ t('themeQuality.status.' + status) }}</dt>
        <dd :data-status="status">{{ report.counts[status] ?? 0 }}</dd>
      </div>
    </dl>

    <div v-if="report.coverage.length" class="coverage">
      <h3>{{ t('themeQuality.coverage') }}</h3>
      <ul>
        <li v-for="item in report.coverage" :key="item.scope">
          <strong>{{ t('themeQuality.period.' + item.scope) }}</strong>
          · {{ t('themeQuality.checkedRows', { count: number(item.checked_count) }) }}
          · {{ t('themeQuality.latestObserved', { date: item.latest_observed_date ?? '—' }) }}
          · {{ t('themeQuality.completenessUnknown') }}
        </li>
      </ul>
    </div>

    <div class="report-heading filters">
      <h3>{{ t('themeQuality.results') }}</h3>
      <div class="filter-controls">
        <el-checkbox v-model="issuesOnly" data-testid="theme-quality-issues-only">{{ t('themeQuality.issuesOnly') }}</el-checkbox>
        <el-select v-model="selectedKind" :aria-label="t('themeQuality.ruleType')"
          class="category-picker" :placeholder="t('themeQuality.allKinds')" data-testid="theme-quality-filter-kind">
          <el-option value="" :label="t('themeQuality.allKinds')" />
          <el-option v-for="kind in categories" :key="kind" :value="kind" :label="t('themeQuality.kind.' + kind)" />
        </el-select>
      </div>
    </div>
    <p v-if="!groups.length">{{ t('themeQuality.noMatchingResults') }}</p>
    <section v-for="group in groups" :key="group.kind" class="rule-group">
      <h4>{{ t('themeQuality.kind.' + group.kind) }}</h4>
      <details v-for="item in group.items" :key="item.rule_id + ':' + item.scope"
        class="rule-result" :data-rule-id="item.rule_id" :data-rule-scope="item.scope"
        :data-rule-status="item.status">
        <summary>
          <span class="status" :data-status="item.status">{{ t('themeQuality.status.' + item.status) }}</span>
          <strong>{{ item.description }}</strong>
          <span>{{ t('themeQuality.period.' + item.scope) }}</span>
          <span>{{ item.metrics.map((key) => content.metrics[key]?.label ?? key).join(' / ') }}</span>
          <span>{{ t('themeQuality.issuesChecked', {
            issues: number(item.issue_count), checked: number(item.checked_count),
          }) }}</span>
          <time :datetime="report.completed_at">{{ time }}</time>
        </summary>
        <dl class="scope-info item-detail">
          <div><dt>{{ t('themeQuality.ruleId') }}</dt><dd>{{ item.rule_id }}</dd></div>
          <div><dt>{{ t('themeQuality.fields') }}</dt><dd>{{ item.fields.join(', ') }}</dd></div>
          <div v-if="item.dimensions.length">
            <dt>{{ t('themeQuality.dimensions') }}</dt><dd>{{ item.dimensions.join(', ') }}</dd>
          </div>
          <div><dt>{{ t('themeQuality.countUnit') }}</dt><dd>{{ t('themeQuality.units.' + item.count_unit) }}</dd></div>
          <div v-if="item.affected_rows != null">
            <dt>{{ t('themeQuality.affectedRows') }}</dt><dd>{{ number(item.affected_rows) }}</dd>
          </div>
          <div><dt>{{ t('themeQuality.suggestion') }}</dt><dd>{{ item.suggestion }}</dd></div>
          <div v-if="item.sample_keys?.length">
            <dt>{{ t('themeQuality.sampleKeys') }}</dt>
            <dd>
              <code v-for="(key, index) in item.sample_keys" :key="index">{{ JSON.stringify(key) }}</code>
              <small v-if="item.samples_truncated">{{ t('themeQuality.samplesTruncated') }}</small>
            </dd>
          </div>
        </dl>
        <details v-if="item.query_ref && queryByRef.has(item.query_ref)" class="query-details">
          <summary>{{ t('themeQuality.queryDetails') }}</summary>
          <code>{{ item.query_ref }}</code>
          <pre>{{ queryByRef.get(item.query_ref)?.sql }}</pre>
        </details>
      </details>
    </section>
    <details v-if="report.read_receipt" class="read-details">
      <summary>{{ t('themeQuality.readReceipt') }}</summary>
      <dl class="scope-info">
        <div><dt>{{ t('themeQuality.sourceMode') }}</dt><dd>{{ report.source_mode ? t('themeQuality.' + report.source_mode) : '—' }} · {{ report.source.source_version ?? '—' }}</dd></div>
        <div><dt>{{ t('themeQuality.sourceView') }}</dt><dd>{{ report.source.view }}</dd></div>
        <div><dt>{{ t('themeQuality.snapshot') }}</dt><dd>{{ report.read_receipt.snapshot_id ?? '—' }}</dd></div>
        <div><dt>{{ t('themeQuality.isolation') }}</dt><dd>{{ report.read_receipt.isolation ?? '—' }}</dd></div>
        <div><dt>{{ t('themeQuality.qualityQueries') }}</dt><dd>{{ report.read_receipt.quality_queries }}</dd></div>
      </dl>
    </details>
  </section>
</template>

<style scoped lang="less">
.saved-report { display: grid; gap: 12px; min-width: 0; margin-top: 16px; overflow-wrap: anywhere; }
.report-heading, .filter-controls { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
h2, h3, h4, p, dl { margin: 0; }
h2 { font-size: 16px; }
h3, h4 { font-size: 14px; }
p { color: var(--ed-text-color-regular); }
.report-heading p { margin-top: 4px; }
.scope-info, .status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(160px, 100%), 1fr));
  gap: 10px;
}
.scope-info { padding: 10px 12px; border: 1px solid var(--ed-border-color-lighter); border-radius: 6px; }
.scope-info dd, .status-grid dd { margin: 4px 0 0; }
.scope-info dt, .status-grid dt { color: var(--ed-text-color-regular); }
.status-grid > div { padding: 8px 12px; background: var(--ed-fill-color-light); border-radius: 6px; }
.status-grid dd { font-size: 20px; font-weight: 600; }
.status { font-weight: 600; }
[data-status='failed'] { color: #a62834; }
[data-status='warning'] { color: #885818; }
[data-status='passed'] { color: #21664e; }
.coverage ul { margin: 6px 0 0; padding-inline-start: 20px; }
.coverage li + li { margin-top: 4px; }
.filters { padding-top: 8px; border-top: 1px solid var(--ed-border-color-lighter); }
.filter-controls { justify-content: flex-end; }
.filter-controls :deep(.ed-select) { width: 160px; max-width: 100%; }
.rule-group { display: grid; gap: 6px; }
.rule-result { border: 1px solid var(--ed-border-color-lighter); border-radius: 6px; padding: 8px 10px; }
.rule-result > summary { cursor: pointer; line-height: 1.75; }
.rule-result > summary > span, .rule-result > summary > strong, .rule-result > summary > time { display: inline-block; margin-inline-end: 12px; }
.rule-result > summary > time { font-size: 12px; color: var(--ed-text-color-regular); }
.item-detail { margin-top: 8px; }
.item-detail code, .item-detail small { display: block; }
.query-details, .read-details { margin: 8px 0; }
.query-details summary, .read-details summary { cursor: pointer; padding: 4px 0; }
.query-details pre { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
