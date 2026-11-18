import { request, type RequestError } from '@/utils/request'

export type MetricOperator = 'count_distinct' | 'sum' | 'sum_zero' | 'average' | 'repeat_count'

export interface ThemeMetric {
  op: MetricOperator
  field: string
  label: string
  unit: string
  description: string
  precision?: number | null
}

export interface ThemeDimension {
  field: string
  label: string
  identity?: string | null
  numeric?: boolean
  ordered?: boolean
  values?: Record<string, string>
}

export interface ThemeDecomposition {
  metrics: Record<string, boolean>
  dimensions: Record<string, string[]>
  tolerance?: string | number
}

export type ThemeQualityRuleType = 'unique' | 'not_null' | 'range' | 'freshness'
export type ThemeQualitySeverity = 'warning' | 'failure'
export type ThemeQualityStatus =
  | 'passed'
  | 'warning'
  | 'failed'
  | 'incomplete'
  | 'no_samples'
  | 'not_applicable'
export type ThemeQualityPeriod = 'current' | 'baseline' | 'readable'

export interface ThemeQualityRule {
  id: string
  type: ThemeQualityRuleType
  fields: string[]
  description: string
  suggestion: string
  severity: ThemeQualitySeverity
  metrics: string[]
  dimensions?: string[]
  minimum?: string
  maximum?: string
  max_age_days?: number
}

export interface ThemeQualityConfig {
  source_mode: 'static' | 'rolling'
  sample_keys?: string[]
  rules: ThemeQualityRule[]
}

export interface ThemeQualityWindow {
  start: string
  end: string
  compare_start?: string
  compare_end?: string
  as_of?: string
}

export type ThemeQualityTarget = { version: string } | { draft_id: string }
export type ThemeQualityRequest = ThemeQualityWindow &
  ({ version: string } | { draft_id: string; revision: number }) & { request_id: string }

export interface ThemeQualitySummary {
  id: string
  version?: string
  draft_id?: string
  revision?: number
  status: ThemeQualityStatus
  started_at: string
  completed_at: string
  scope: ThemeQualityWindow
  counts: Partial<Record<ThemeQualityStatus, number>>
  publishable: boolean
}

export interface ThemeQualityResult {
  rule_id: string
  type: ThemeQualityRuleType
  fields: string[]
  severity: ThemeQualitySeverity
  metrics: string[]
  dimensions: string[]
  description: string
  suggestion: string
  scope: ThemeQualityPeriod
  status: ThemeQualityStatus
  checked_count?: number | null
  issue_count?: number | null
  count_unit: 'rows' | 'key_groups' | 'freshness'
  affected_rows?: number | null
  sample_keys?: Record<string, string | null>[]
  samples_truncated?: boolean
  query_ref?: string | null
}

export interface ThemeQualityReport extends ThemeQualitySummary {
  source_mode: ThemeQualityConfig['source_mode'] | null
  checked_on: string | null
  reason?: 'quality_budget_exhausted' | 'quality_source_schema_changed' | 'quality_source_unavailable'
  source: { view: string; source_version?: string; dependency?: string }
  read_receipt: {
    isolation?: string | null
    read_at?: string | null
    snapshot_id?: string | null
    quality_queries: number
  }
  coverage: {
    scope: ThemeQualityPeriod
    start?: string | null
    end?: string | null
    checked_count?: number | null
    latest_observed_date?: string | null
    completeness_confirmed: boolean
  }[]
  results: ThemeQualityResult[]
  queries: { query_ref: string; sql: string; row_count?: number; duration_ms?: number }[]
}

export interface TopicSemantics {
  date_field: string
  start: string
  end: string
  default_metric: string
  metrics: Record<string, ThemeMetric>
  dimensions: Record<string, ThemeDimension>
  detail: Record<string, string>
  guidance: string[]
  quality?: ThemeQualityConfig | null
  decomposition?: ThemeDecomposition | null
  description?: string
  grain?: string[]
  missing_facts?: ThemeMissingFact[]
}

export type ThemeMissingFact = 'payment' | 'refund'
export type ThemeStatus = 'draft' | 'published' | 'disabled' | 'legacy'

export interface ThemeField {
  name: string
  type: string
  operators: MetricOperator[]
}

export interface ThemeAsset {
  id: string
  view: string
  fields: ThemeField[]
}

export interface ThemeAssetSource {
  id: string
  label: string
  assets: ThemeAsset[]
}

export interface ThemeAssetPreview {
  fields: ThemeField[]
  rows: Record<string, unknown>[]
  query_count: number
}

export interface ThemeSummary {
  id: string
  label: string
  description: string
  managed: boolean
  view: string
  active_version: string | null
  owner?: string | number | null
  draft_revision?: number | null
  last_verified?: string | null
  status?: ThemeStatus
  registered?: boolean
  source_version?: string | null
  asset_id?: string | null
}

export interface ThemeDraft {
  id: string
  revision: number
  base_version: string
  content: TopicSemantics
}

export interface ThemeValidation {
  id: string
  draft_id: string
  revision: number
  kind?: 'static' | 'preview'
  checked: boolean
  created_at?: string
  checked_at?: string
  note?: string
  quality_report_id?: string | null
  receipt: {
    kind?: 'static' | 'preview'
    execution_status?: 'succeeded' | 'failed' | 'not_run'
    sql?: string
    plan?: ThemePreviewIntent
    intent?: ThemePreviewIntent
    row_count?: number
    rows?: Record<string, unknown>[]
    table?: ThemePreviewTable
    presentation?: { table?: ThemePreviewTable }
  }
}

interface ThemePreviewTable {
  columns: { key: string; label: string }[]
  rows: Record<string, unknown>[]
}

export interface ThemePublication {
  id?: string
  request_id?: string
  kind?: 'publish' | 'rollback'
  action?: 'publish' | 'rollback'
  version: string
  prior_version?: string
  previous_version?: string
  created_at: string
  quality_report_id?: string | null
}

export interface ThemeDetail extends ThemeSummary {
  content: TopicSemantics | null
  draft: ThemeDraft | null
  fields: ThemeField[]
  validations: ThemeValidation[]
  revisions: { version: string; created_at: string }[]
  publications: ThemePublication[]
  adoption: { record_id: number | string; version: string } | null
}

export interface ThemePreviewIntent {
  operation: 'new'
  compare_start?: string
  compare_end?: string
  start: string
  end: string
  metrics: string[]
  filters?: Record<string, string | number>
  group: string
}

const collection = '/cbi/themes'
const themePath = (id: string) => collection + '/' + encodeURIComponent(id)
const options = { requestOptions: { customError: true } }

export const themeApi = {
  list: () => request.get<ThemeSummary[]>(collection, options),
  assets: () => request.get<ThemeAssetSource[]>(collection + '/assets', options),
  register: (
    id: string,
    body: { request_id: string; asset_id: string; label: string; source_data_version?: string; content: TopicSemantics }
  ) => request.post<ThemeDetail>(themePath(id) + '/register', body, options),
  assetPreview: (id: string, asset_id: string) =>
    request.post<ThemeAssetPreview>(themePath(id) + '/asset-preview', { asset_id }, options),
  availability: (id: string, body: { request_id: string; expected_version: string; enabled: boolean }) =>
    request.post<ThemeSummary>(themePath(id) + '/availability', body, options),
  detail: (id: string) => request.get<ThemeDetail>(themePath(id), options),
  save: (
    id: string,
    body: { draft_id?: string; revision?: number; base_version: string; content: TopicSemantics }
  ) => request.post<ThemeDraft>(themePath(id) + '/draft', body, options),
  validate: (id: string, body: { draft_id: string; revision: number }) =>
    request.post<ThemeValidation>(themePath(id) + '/validate', body, options),
  preview: (id: string, body: { draft_id: string; revision: number; intent: ThemePreviewIntent }) =>
    request.post<ThemeValidation>(themePath(id) + '/preview', body, options),
  confirm: (id: string, body: { validation_id: string; note: string; quality_report_id?: string }) =>
    request.post<ThemeValidation>(themePath(id) + '/confirm', body, options),
  listQuality: (id: string, target: ThemeQualityTarget) =>
    request.get<ThemeQualitySummary[]>(themePath(id) + '/quality', { ...options, params: target }),
  getQuality: (id: string, reportId: string) =>
    request.get<ThemeQualityReport>(
      themePath(id) + '/quality/' + encodeURIComponent(reportId),
      options
    ),
  checkQuality: (id: string, body: ThemeQualityRequest) =>
    request.post<ThemeQualityReport>(themePath(id) + '/quality', body, options),
  publish: (
    id: string,
    body: {
      draft_id: string
      revision: number
      expected_version: string
      validation_id: string
      quality_report_id?: string
      request_id: string
    }
  ) => request.post<ThemePublication>(themePath(id) + '/publish', body, options),
  rollback: (
    id: string,
    body: { target_version: string; expected_version: string; request_id: string }
  ) => request.post<ThemePublication>(themePath(id) + '/rollback', body, options),
}

type ErrorDetail =
  | string
  | { reason?: string; fields?: Record<string, string>; current_version?: string }
  | { loc?: (string | number)[]; msg?: string }[]

export function themeError(error: unknown) {
  const response = (error as RequestError<{ detail?: ErrorDetail }> | undefined)?.response
  const detail = response?.data?.detail
  return {
    status: response?.status,
    detail:
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map(({ loc, msg }) => [loc?.slice(1).join('.'), msg].filter(Boolean).join(' · ')).join(' / ')
          : [
              detail?.reason,
              ...Object.entries(detail?.fields ?? {}).map(([path, code]) => path + ' · ' + code),
              detail?.current_version,
            ]
              .filter(Boolean)
              .join(' / '),
  }
}
