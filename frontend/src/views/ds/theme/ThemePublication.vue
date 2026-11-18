<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ThemeDetail, ThemeDimension, ThemeMetric, ThemeQualitySummary, TopicSemantics } from '@/api/theme'

const props = defineProps<{
  theme: ThemeDetail
  staleDraft: boolean
  dirty: boolean
  busy: boolean
  qualityReports: ThemeQualitySummary[]
  qualityLoading: boolean
  qualityError: string
}>()
const emit = defineEmits<{
  publish: [validationId: string, qualityReportId?: string]
  rollback: [target: string]
  availability: []
}>()
const { t, locale } = useI18n()
const validationId = ref('')
const qualityReportId = ref('')
const rollbackTarget = ref('')
const validPreviews = computed(() => {
  const draft = props.theme.draft
  if (!draft) return []
  return props.theme.validations.filter(
    (validation) =>
      validation.draft_id === draft.id &&
      validation.revision === draft.revision &&
      (validation.kind ?? validation.receipt.kind) === 'preview' &&
      validation.receipt.execution_status === 'succeeded' &&
      validation.checked
  )
})
const needsQuality = computed(() => !!props.theme.draft?.content.quality?.rules.length)
const selectableReports = computed(() => {
  const preview = validPreviews.value.find(({ id }) => id === validationId.value)
  return props.qualityReports.filter(
    (report) => report.status !== 'warning' || preview?.quality_report_id === report.id
  )
})
watch(selectableReports, (items) => {
  if (!items.some(({ id }) => id === qualityReportId.value)) qualityReportId.value = ''
})
function reportName(item: ThemeQualitySummary) {
  return [
    new Date(item.completed_at).toLocaleString(locale.value),
    t('themeQuality.status.' + item.status),
    item.id,
  ].join(' · ')
}

const rollbackVersions = computed(() =>
  props.theme.revisions.filter(({ version }) => version !== props.theme.active_version)
)
watch(
  validPreviews,
  (items) => {
    if (!items.some(({ id }) => id === validationId.value)) validationId.value = items[0]?.id ?? ''
  },
  { immediate: true }
)
watch(rollbackVersions, (versions) => {
  if (!versions.some(({ version }) => version === rollbackTarget.value)) rollbackTarget.value = ''
})

type Change = { key: string; label: string; before: string; after: string }
type MetricDefinition = ThemeMetric | ThemeDimension

function describe(value: MetricDefinition | undefined) {
  if (!value) return '—'
  const definition = [value.label, value.field]
  if ('op' in value) {
    definition.push(t('themeConfig.operators.' + value.op), value.unit, value.description)
    if (value.precision != null) {
      definition.push(t('themeRegistration.precisionValue', { digits: value.precision }))
    }
  } else {
    if (value.identity) definition.push(t('themeConfig.identity') + ' ' + value.identity)
    if (value.values) definition.push(Object.values(value.values).join(', '))
  }
  return definition.filter(Boolean).join(' · ')
}

const changes = computed<Change[]>(() => {
  const active = props.theme.content
  const draft = props.theme.draft?.content
  if (!active || !draft) return []
  const result: Change[] = []
  const keyNames: Record<
    'date_field' | 'start' | 'end' | 'default_metric' | 'description',
    string
  > = {
    date_field: 'dateField',
    start: 'start',
    end: 'end',
    default_metric: 'defaultMetric',
    description: 'description',
  }
  for (const [key, label] of Object.entries(keyNames)) {
    const property = key as keyof typeof keyNames
    if ((active[property] ?? '') === (draft[property] ?? '')) continue
    result.push({
      key,
      label: t('themeConfig.' + label),
      before: active[property] || '—',
      after: draft[property] || '—',
    })
  }
  for (const section of ['metrics', 'dimensions'] as const) {
    const previous: TopicSemantics[typeof section] = active[section]
    const next: TopicSemantics[typeof section] = draft[section]
    const keys = new Set([...Object.keys(previous), ...Object.keys(next)])
    for (const key of keys) {
      const before = describe(previous[key])
      const after = describe(next[key])
      if (before === after) continue
      result.push({
        key: section + ':' + key,
        label: t('themeConfig.' + section) + ' · ' + key,
        before,
        after,
      })
    }
  }
  const compare = (key: string, label: string, before: string, after: string) => {
    if (before !== after) result.push({ key, label, before: before || '—', after: after || '—' })
  }
  if (props.theme.registered) {
    compare('grain', t('themeRegistration.grain'), active.grain?.join(' · ') ?? '', draft.grain?.join(' · ') ?? '')
    const describeFacts = (content: TopicSemantics) =>
      [...(content.missing_facts ?? [])].sort().map((fact) => t('themeRegistration.fact.' + fact)).join(' · ')
    compare('missing_facts', t('themeRegistration.missingFacts'), describeFacts(active), describeFacts(draft))
    const describeFields = (content: TopicSemantics) =>
      Object.entries(content.detail).sort(([left], [right]) => left.localeCompare(right))
        .map(([field, label]) => field + ' · ' + label).join('\n')
    compare('detail', t('themeRegistration.fields'), describeFields(active), describeFields(draft))
  }
  const describeDecomposition = (content: TopicSemantics) => {
    const value = content.decomposition
    if (!value) return ''
    const metrics = Object.keys(value.metrics).sort().map((key) => [
      key,
      value.metrics[key] && t('resultExplorer.configuration.allowEmpty'),
    ].filter(Boolean).join(' · '))
    const dimensions = Object.keys(value.dimensions).sort().map((key) => {
      const children = [...value.dimensions[key]].sort()
      return [key, ...children].join(' → ')
    })
    return [...metrics, ...dimensions, t('resultExplorer.configuration.tolerance') + ' · ' + (value.tolerance ?? 0)]
      .join('\n')
  }
  compare(
    'decomposition',
    t('resultExplorer.configuration.title'),
    describeDecomposition(active),
    describeDecomposition(draft)
  )
  if (JSON.stringify(active.quality ?? null) !== JSON.stringify(draft.quality ?? null)) {
    const describePolicy = (content: TopicSemantics) => {
      const policy = content.quality
      if (!policy?.rules.length) return t('themeQuality.noRules')
      return [
        t('themeQuality.' + policy.source_mode),
        policy.sample_keys?.join(', '),
        ...policy.rules.map(({ id, type, fields, severity, metrics, dimensions, description, suggestion, minimum, maximum, max_age_days }) =>
          [id, t('themeQuality.kind.' + type), fields.join(', '), t('themeQuality.severity.' + severity),
            metrics.join(', '), dimensions?.join(', '), description, suggestion, minimum, maximum, max_age_days]
            .filter(value => value != null && value !== '').join(' · ')
        ),
      ].filter(Boolean).join('\n')
    }
    result.push({
      key: 'quality',
      label: t('themeQuality.rules'),
      before: describePolicy(active),
      after: describePolicy(draft),
    })
  }
  return result
})
</script>

<template>
  <section class="publication">
    <h2>{{ $t(theme.active_version ? 'themeConfig.diff' : 'themeRegistration.firstDraft') }}</h2>
    <dl
      class="comparison"
      data-testid="theme-publication-subject"
      :data-version="theme.active_version"
      :data-draft-id="theme.draft?.id"
      :data-revision="theme.draft?.revision"
      :data-base-version="theme.draft?.base_version"
    >
      <div>
        <dt>{{ $t('themeConfig.activeVersion') }}</dt>
        <dd>{{ theme.active_version || '—' }}</dd>
      </div>
      <div>
        <dt>{{ $t('themeConfig.publicationDraft') }}</dt>
        <template v-if="theme.draft">
          <dd>
            {{ theme.draft.id }} ·
            {{ $t('themeConfig.revision', { revision: theme.draft.revision }) }}
          </dd>
          <dd v-if="theme.active_version">{{ $t('themeConfig.baseVersion', { version: theme.draft.base_version }) }}</dd>
          <dd v-else>{{ $t('themeRegistration.firstDraft') }}</dd>
        </template>
        <dd v-else>{{ $t('themeConfig.noDraft') }}</dd>
      </div>
    </dl>
    <el-alert
      v-if="theme.draft && staleDraft"
      data-testid="theme-publication-stale"
      :title="
        $t('themeConfig.staleDraft', {
          base: theme.draft.base_version,
          current: theme.active_version,
        })
      "
      type="warning"
      :closable="false"
    />
    <div v-if="theme.draft" class="publish-panel">
      <el-alert
        v-if="dirty"
        :title="$t('themeConfig.diffSavedOnly')"
        type="info"
        :closable="false"
      />
      <table v-if="changes.length" data-testid="theme-publication-diff">
        <thead>
          <tr>
            <th scope="col">{{ $t('themeConfig.change') }}</th>
            <th scope="col">{{ $t('themeConfig.activeVersion') }}</th>
            <th scope="col">{{ $t('themeConfig.publicationDraft') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in changes" :key="item.key" :class="{ 'multiline-change': ['quality', 'detail', 'decomposition'].includes(item.key) }">
            <th scope="row">{{ item.label }}</th>
            <td>{{ item.before }}</td>
            <td>{{ item.after }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="theme.active_version">{{ $t('themeConfig.noDiff') }}</p>
      <div class="publish-actions">
        <el-select
          v-if="!staleDraft && validPreviews.length"
          v-model="validationId"
          class="version-picker"
          :aria-label="$t('themeConfig.verifiedPreview')"
        >
          <el-option
            v-for="item in validPreviews"
            :key="item.id"
            :value="item.id"
            :label="item.note || item.id"
          />
        </el-select>
        <p v-else-if="!staleDraft">{{ $t('themeConfig.requireVerifiedPreview') }}</p>
        <template v-if="needsQuality && !staleDraft">
          <el-select v-if="selectableReports.length" v-model="qualityReportId"
            class="version-picker" data-testid="theme-publish-quality-report"
            :aria-label="$t('themeQuality.selectPublishReport')"
            :placeholder="$t('themeQuality.selectPublishReport')">
            <el-option v-for="item in selectableReports" :key="item.id" :value="item.id" :label="reportName(item)" />
          </el-select>
          <p v-else-if="qualityLoading">{{ $t('themeQuality.loadingReport') }}</p>
          <p v-else-if="qualityError" role="alert">{{ $t('themeQuality.historyFailed') }} {{ qualityError }}</p>
          <p v-else>{{ $t('themeQuality.requireReport') }}</p>
        </template>
        <el-button
          type="primary"
          data-testid="theme-publish"
          :disabled="busy || dirty || staleDraft || !validationId || (!!theme.active_version && !changes.length) || (needsQuality && !qualityReportId)"
          @click="emit('publish', validationId, needsQuality ? qualityReportId : undefined)"
          >{{ $t('themeConfig.publish') }}</el-button
        >
      </div>
    </div>
    <div v-if="theme.registered && theme.active_version" class="availability">
      <el-button :disabled="busy || dirty" data-testid="theme-availability" @click="emit('availability')">
        {{ $t(theme.status === 'disabled' ? 'themeRegistration.enable' : 'themeRegistration.disable') }}
      </el-button>
    </div>
    <div v-if="theme.active_version" class="adoption" data-testid="theme-adoption">
      <h2>{{ $t('themeConfig.adoption') }}</h2>
      <p v-if="theme.adoption">{{ $t('themeConfig.adoptionConfirmed', theme.adoption) }}</p>
      <p v-else>{{ $t('themeConfig.adoptionPending') }}</p>
    </div>

    <div class="section-header history-header">
      <h2>{{ $t('themeConfig.publications') }}</h2>
      <div v-if="rollbackVersions.length" class="inline-controls">
        <el-select
          v-model="rollbackTarget"
          class="version-picker"
          data-testid="theme-rollback-select"
          :aria-label="$t('themeConfig.rollbackTarget')"
          :placeholder="$t('themeConfig.rollbackTarget')"
        >
          <el-option
            v-for="item in rollbackVersions"
            :key="item.version"
            :label="item.version"
            :value="item.version"
          />
        </el-select>
        <el-button
          :disabled="busy || dirty || !rollbackTarget"
          data-testid="theme-rollback"
          @click="emit('rollback', rollbackTarget)"
          >{{ $t('themeConfig.rollback') }}</el-button
        >
      </div>
    </div>
    <table v-if="theme.publications.length" data-testid="theme-publication-history">
      <thead>
        <tr>
          <th scope="col">{{ $t('themeConfig.action') }}</th>
          <th scope="col">{{ $t('themeConfig.fromVersion') }}</th>
          <th scope="col">{{ $t('themeConfig.targetVersion') }}</th>
          <th scope="col">{{ $t('themeConfig.time') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in theme.publications" :key="item.id ?? item.request_id">
          <td>
            {{
              $t(
                (item.kind ?? item.action) === 'rollback'
                  ? 'themeConfig.rollback'
                  : 'themeConfig.publish'
              )
            }}
          </td>
          <td>{{ item.prior_version ?? item.previous_version ?? '—' }}</td>
          <td>{{ item.version }}
            <small v-if="item.quality_report_id" class="publication-quality">{{ $t('themeQuality.reportId') }} · {{ item.quality_report_id }}</small>
          </td>
          <td>{{ new Date(item.created_at).toLocaleString($i18n.locale) }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else>{{ $t('themeConfig.noPublications') }}</p>
  </section>
</template>

<style scoped lang="less">
.section-header,
.inline-controls,
.publish-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.section-header {
  justify-content: space-between;
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
.adoption,
.availability,
.history-header {
  margin-top: 24px;
}
.publish-panel {
  margin-top: 12px;
}
.comparison {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr));
  gap: 8px 16px;
  margin: 12px 0;
  padding: 10px 12px;
  background: var(--ed-fill-color-light);
  border: 1px solid var(--ed-border-color-lighter);
  border-radius: 6px;
  overflow-wrap: anywhere;
}
.comparison dt {
  font-weight: 600;
}
.comparison dd {
  margin: 4px 0 0;
  color: var(--ed-text-color-regular);
}
.publication :deep(.ed-alert) {
  overflow-wrap: anywhere;
}
.publish-actions {
  justify-content: flex-end;
  margin-top: 12px;
}
.publish-actions p {
  margin: 0;
}
.version-picker {
  width: min(280px, 100%);
}
table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
th,
td {
  text-align: left;
  padding: 10px;
  border: 1px solid var(--ed-border-color-lighter);
  overflow-wrap: anywhere;
}
.multiline-change td { white-space: pre-line; }
.publication-quality { display: block; margin-top: 4px; }
thead th {
  background: var(--ed-fill-color-light);
}
</style>
