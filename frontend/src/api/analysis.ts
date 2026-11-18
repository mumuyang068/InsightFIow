export type ResultView = 'chart' | 'data' | 'evidence'
export type ComparisonView = 'delta' | 'periods'

export interface DecompositionRow {
  key: string | number | null
  label: string | null
  current: string | number | null
  baseline: string | number | null
  delta: string | number | null
  current_status?: string
  baseline_status?: string
  other?: boolean
}

export interface Decomposition {
  metric: string
  label: string
  unit: string
  precision?: number | null
  dimension: string
  dimension_label: string
  rows: DecompositionRow[]
  total: Pick<DecompositionRow, 'current' | 'baseline' | 'delta'>
  reconciled: boolean
  truncated?: boolean
  omitted?: number | null
  reason?: string | null
  other?: Decomposition['total'] | null
  coverage?: { source_complete: boolean; tolerance?: string }
  children?: { key: string; label: string }[]
}

export interface InvestigationStep {
  kind: string
  status: string
  dimension?: string
  label?: string
  record_id?: string
  parent_total?: Pick<DecompositionRow, 'key'>
  decomposition?: Decomposition
  table?: AnalysisTable
  tables?: (AnalysisTable & { period: string; truncated?: boolean })[]
  records?: string[]
}

export interface Investigation {
  status: 'running' | 'completed' | 'partial' | 'blocked'
  reason_status: 'unconfirmed'
  stop_reason?: string
  plan?: { method_version?: string; execution_scope?: 'single_partition' | 'investigation' }
  first_failure?: InvestigationStep
  findings: { fact_id: string; label: string | null; direction: string; delta: number | string; unit: string }[]
  steps: InvestigationStep[]
  source_confirmation?: { id: string }
}

export type InvestigationAction = 'start_investigation' | 'resume_investigation' |
  'confirm_investigation_source' | 'cancel_investigation_source'


export type AnalysisStatus =
  'awaiting_confirmation' | 'running' | 'completed' | 'partial' | 'blocked' | 'stopped'

export type SeriesMode = 'grouped' | 'stacked' | 'none'

export interface SavedFunnel {
  window_hours: number
  entry_start: string
  entry_end: string
  observation_asof: string
  unit: string
  counts: number[]
  each_of_entry_pct: (number | null)[]
  immature: number
  numerator: number
  denominator: number
  rate_pct: number | null
  status: string
  comparison?: { run_id: string; window_hours: number; delta_pp: number }
}

export interface AnalysisChoice {
  id: string
  label: string
  description: string
}

export interface AnalysisScopeChange {
  current: string
  baseline?: string
  comparison_rule?: "fixed" | "previous_day"
  shape?: "summary" | "trend" | "category"
  range_start?: string
}

export interface AnalysisAction {
  action: 'retry_quality' | 'confirm_definition' | 'continue_evidence' | 'recalculate_latest' | 'confirm_publication' | 'cancel_publication' | 'revise_scope' | InvestigationAction
  request_id: string
  expected_revision: number
  decision_id?: string
  choice_id?: string
  scope_change?: AnalysisScopeChange
}

export interface AnalysisTable {
  columns: { key: string; label: string; numeric?: boolean; precision?: number | null }[]
  rows: Record<string, string | number | null>[]
}

export interface AnalysisQuality {
  status: 'passed' | 'warning' | 'failed' | 'blocked' | 'partial' | 'incomplete' | 'no_samples' | 'not_applicable'
  report_ids: string[]
  checked_at?: string
  checks: {
    rule_id: string
    label?: string
    affected_metrics: string[]
    type: 'unique' | 'not_null' | 'range' | 'freshness'
    scope: 'current' | 'baseline' | 'readable'
    status: 'passed' | 'warning' | 'failed' | 'incomplete' | 'no_samples' | 'not_applicable'
  }[]
  unavailable_outputs: { key: string; label: string; reason: string }[]
}

export interface AnalysisState {
  quality?: AnalysisQuality
  semantic_plan?: { detail: boolean; top?: number }
  available_investigation?: { key: string; label: string }[]
  investigation?: Investigation
  multi?: { run_id: string; task_id: string; window: string; currency?: string; revision: string; sales?: string; recorded_fee?: string; method?: string; numerator?: number; denominator?: number }
  revision: number
  config_version: string
  config_digest?: string
  data_version?: string | null
  source_proof?: { view: string; rows: number; digest: string; source_version?: string | null }
  publication_confirmation?: { id: string; prior_version: string | null; version: string }
  task_status: AnalysisStatus
  request: { text: string; parent_record_id?: string }
  scope: {
    store?: string
    current?: string
    range_start?: string
    range_end_exclusive?: string
    baseline_end_exclusive?: string
    filter_labels?: { label: string; value: string | number }[]
    category_available?: boolean
    baseline?: string
    comparison_rule?: 'fixed' | 'previous_day' | null
    timezone: string
    definition?: string
    definition_label?: string
    source?: string
  }
  decision: {
    id?: string
    options?: AnalysisChoice[]
    reason?: string
    metric_labels?: string[]
    missing_fact?: 'payment' | 'refund'
    missing_fact_source?: 'configuration'
  }
  outputs: {
    id: string
    label?: string
    status: 'verified' | 'missing' | 'failed' | 'not_applicable'
    value?: number
    unit?: string
    precision?: number | null
    reason?: string
  }[]
  steps: { kind: string; status: string; record_id?: string; purpose?: string }[]
  events: { action: string }[]
  presentation: {
    decomposition?: Decomposition
    series_mode: SeriesMode
    series_semantics: string
    title?: string
    table?: AnalysisTable
    shape?: "trend" | "category"
    unit?: string
    limit?: number
    truncated?: boolean
    rows?: { label: string | null; value: number | null }[]
  }
  evidence?: {
    status: string
    reference_label?: string
    reference_totals?: Record<string, number>
    differences?: Record<string, number>
    members?: {
      refund_id: string
      line_id: string
      business_date: string
      amount_cents: number
      paid_at_utc: string
      refund_at_utc: string
      in_reference: boolean
      in_cohort: boolean
    }[]
  }
}
