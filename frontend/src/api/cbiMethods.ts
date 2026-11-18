import { request } from '@/utils/request'
import type { Task } from './cbiMulti'

export type MethodChange = {
  window_hours?: number
  observation_asof?: string
  entry_start?: string
  entry_end?: string
  cohort_channel?: string | null
  repeat_channel?: string | null
  eligibility?: string
}

export interface MethodRun {
  id: string
  record_id?: string
  question?: string
  definition: { id: string; parent_run?: string; binding_label?: string; plan: MethodChange & { binding: string; binding_version: string; method: string; version: string; unit: string; read_strategy: string; epoch?: string; store: string; timezone: string }; sources: Record<string, { kind: string; id?: string; version?: string }> }
  result: {
    numerator: number; denominator: number; rate_pct: number | null; status: string; immature: number; candidate_count: number;
    counts?: number[]; members?: string[][]; adjacent_pct?: (number | null)[]; each_of_entry_pct?: (number | null)[];
    mature_members?: string[]; repeated_members?: string[]; verified_non_repeat?: string[];
    trace: Record<string, unknown>[];
  }
  evidence: Record<string, unknown>
  display?: {epoch?: string; timezone: string}
  comparison?: { base_run: string; changed: string[]; delta_pp?: number; new_completions?: string[] }
  correction?: { change: string; guards: Record<string, { numerator: number; denominator: number }> }
}
export type MethodTask = Omit<Task, 'runs' | 'definition'> & {
  runs: Record<string, MethodRun>
  definition?: MethodRun['definition']
  saved?: Record<string, { run_id: string; record_id: string }>
  quality?: Record<string, { status: string; replacement: string }>
}

export interface MethodReceipt {
  outcome?: string
  run_id?: string
  clarification?: string
  problem?: { code: string }
  members?: Record<string, unknown>[]
}
const task = (id: string) => '/chat/multi/task/' + id
const quiet = { requestOptions: { customError: true } }

export const methods = {
  catalog: (id: string) => request.get<BindingSummary[]>('/chat/multi/catalog/' + id, quiet),
  action: (id: string, data: Record<string, unknown>) =>
    request.post<{ receipt: MethodReceipt; task: MethodTask }>(task(id) + '/method', data, quiet),
  comparison: (id: string) =>
    request.get<{ status: string; reference: string; candidate?: string }>(task(id) + '/comparison', quiet),
}
export interface BindingSummary {
  id: string; label: string; method: string; version: string; store: string; window_hours: number
  allowed_windows: number[]; entry_start: string; entry_end: string; observation_asof: string
  observations?: string[]; reading?: string
}
export interface BindingForm {
  id: string; label: string; datasource: number; view: string; store: string
  entry_start: string; entry_end: string; observation_asof: string; window_hours: number
}
export interface BindingVersion {
  id: string; version: number; active: number; based_on: number; config: BindingForm
  published_at?: string; check?: {status: 'passed' | 'failed'; detail?: string | {code: string; fields?: string[]}}
}
export interface ResultReference {
  chat_id: string; run_id: string; question: string; adopted: BindingSummary
  plan: MethodRun['definition']['plan']; sources: MethodRun['definition']['sources']
  result: {numerator: number; denominator: number; rate_pct: number | null}
}
export interface Feedback {
  id: string; text: string; expected: string; status: string; revision: number; run: ResultReference
  handling?: { note: string; category: string; comparison?: ResultReference }
  current?: BindingSummary
  comparisons?: ResultReference[]
}
const configRoot = '/chat/method-admin/bindings'
const feedbackRoot = '/chat/method-feedback'
export const methodConfig = {
  list: () => request.get<{versions: BindingVersion[]; sources: {id: number; name: string}[]}>(configRoot, quiet),
  save: (config: BindingForm, base: number) => request.post<BindingVersion>(configRoot, {config, base}, quiet),
  step: (v: BindingVersion, action: string) => request.post<BindingVersion>(
    configRoot + '/' + v.id + '/' + v.version, {action, base: v.based_on}, quiet
  ),
  own: (chat: string) => request.get<Feedback[]>(feedbackRoot + '/task/' + chat, quiet),
  submit: (chat: string, data: Record<string,unknown>) => request.post<Feedback>(feedbackRoot + '/task/' + chat, data, quiet),
  inbox: () => request.get<Feedback[]>(feedbackRoot + '/admin', quiet),
  process: (id: string,data: Record<string,unknown>) => request.post<Feedback>(feedbackRoot + '/admin/' + id, data, quiet),
}
