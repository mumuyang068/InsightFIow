import { request } from '@/utils/request'

export interface EvaluationCase {
  id: string
  title?: string
  datasource_id: string
  category: string
  turns: { question: string; parent?: number }[]
  fixture: string
  expected: Record<string, unknown> | null
  protects?: string[]
  origin?: Record<string, unknown>
}

export interface EvaluationSuite {
  id: string
  title: string
  description: string
  revision: number
  status: 'draft' | 'confirmed'
  digest: string
  case_count: number
  categories?: string[]
  cases?: EvaluationCase[]
  previous_id?: string | null
  created_at?: string
  confirmed_by?: string | number | null
}

export interface EvaluationVersion {
  id: string
  label: string
}

export interface EvaluationDatasource {
  id: string
  name: string
  active_version: string | null
  versions: EvaluationVersion[]
}

export interface EvaluationOptions {
  identity: { id: string; name: string } | null
  datasources: EvaluationDatasource[]
  budget_limit: number
  fixtures: string[]
  data_version: string
  can_manage?: boolean
  can_run?: boolean
  model?: { name: string; prompt: string }
}

export interface EvaluationIssue {
  dimension: string
  detail: string
  turn?: number
  expected?: unknown
  actual?: unknown
}

export interface EvaluationTurnReceipt {
  record_id?: string | number
  chat_id?: string | number
  state?: Record<string, unknown>
  raw_rows?: unknown
  queries?: {
    record_id?: string | number
    sql?: string
    purpose?: string
    duration_ms?: number
    rows?: unknown
  }[]
  model?: unknown
  events?: unknown
}

export interface EvaluationAttempt {
  id: string
  number: number
  status: string
  observation?: { turns?: EvaluationTurnReceipt[]; usage?: Record<string, unknown> } | null
  judgement?: { status: string; issues: EvaluationIssue[] } | null
  model_calls?: number
  started_at?: string
  finished_at?: string
}

export interface EvaluationRow {
  case_id: string
  question: string
  category: string
  status: string
  selected: boolean
  first?: EvaluationAttempt | null
  latest?: EvaluationAttempt | null
  attempts?: EvaluationAttempt[]
}

export interface EvaluationBatch {
  id: string
  label: string
  suite_id: string
  status: 'pending' | 'running' | 'stopped' | 'completed'
  counts: Record<string, number>
  context: Record<string, unknown>
  rows?: EvaluationRow[]
  parent_batch_id?: string | null
  stop_reason?: string | null
  created_at?: string
  updated_at?: string
  budget: number
  planned_submissions: number
}

export interface EvaluationComparison {
  baseline: EvaluationBatch
  candidate: EvaluationBatch
  comparable: boolean
  differences: { field: string; baseline: unknown; candidate: unknown; allowed: boolean }[]
  rows: {
    case_id: string
    question: string
    category: string
    status: string
    baseline: EvaluationRow | null
    candidate: EvaluationRow | null
  }[]
}

export interface EvaluationSuiteInput {
  title: string
  description?: string
  cases: EvaluationCase[]
  previous_id?: string
  confirm?: boolean
}

export interface EvaluationLaunch {
  suite_id: string
  versions: Record<string, string>
  case_ids?: string[]
  parent_batch_id?: string
  label: string
  budget: number
}

const path = (id: string) => '/cbi/themes/' + encodeURIComponent(id) + '/evaluation'
const options = { requestOptions: { customError: true } }
const batchPath = (id: string, batch: string) => path(id) + '/batches/' + encodeURIComponent(batch)

export const themeEvaluationApi = {
  list: (id: string) =>
    request.get<{
      suites: EvaluationSuite[]
      batches: EvaluationBatch[]
      options: EvaluationOptions
    }>(path(id), options),
  suite: (id: string, suite: string) =>
    request.get<EvaluationSuite>(path(id) + '/suites/' + encodeURIComponent(suite), options),
  save: (id: string, body: EvaluationSuiteInput & { request_id: string }) =>
    request.post<EvaluationSuite>(path(id) + '/suites', body, options),
  freeze: (
    id: string,
    body: { draft_id: string; revision: number; validation_id: string; request_id: string }
  ) => request.post<EvaluationVersion>(path(id) + '/configurations', body, options),
  launch: (id: string, body: EvaluationLaunch & { request_id: string }) =>
    request.post<EvaluationBatch>(path(id) + '/batches', body, options),
  batch: (id: string, batch: string) => request.get<EvaluationBatch>(batchPath(id, batch), options),
  control: (
    id: string,
    batch: string,
    action: 'stop' | 'resume',
    request_id: string,
    case_ids?: string[]
  ) =>
    request.post<EvaluationBatch>(
      batchPath(id, batch) + '/' + action,
      { request_id, case_ids },
      options
    ),
  compare: (id: string, baseline: string, candidate: string) =>
    request.get<EvaluationComparison>(path(id) + '/compare', {
      ...options,
      params: { baseline, candidate },
    }),
  export: (id: string, batch: string) =>
    request.get<Blob>(batchPath(id, batch) + '/export', { ...options, responseType: 'blob' }),
  draftFromRun: (
    id: string,
    body: ({ batch_id: string; case_id: string } | { record_id: string }) & { request_id: string }
  ) => request.post<EvaluationSuite>(path(id) + '/draft-from-run', body, options),
}

export function evaluationValue(value: unknown): string {
  if (value == null || value === '') return '—'
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}
