import { request } from '@/utils/request'

export type Purpose = 'roster' | 'costs' | 'mapping'
export type Inputs = Partial<Record<Purpose, string>>
export type Changes = { fee_id: string; old: string[] | null; new: string[] | null }[]
export interface FileVersion {
  id: string
  name: string
  purpose: Purpose
  replaces?: string
  sha256: string
  summary: { row_count?: number; preview?: Record<string, string | null>[] }
  issue?: { code: string; row?: number; column?: string }
}
export interface Run {
  id: string
  record_id?: string
  actor: string
  definition: { id: string; inputs: Inputs; fields: Record<string, string>; window: string }
  result: {
    state: string
    currency: string
    sales?: string
    recorded_fee?: string
    input_fee?: string
    unmatched_fee?: string
    independent_source_sales?: string
    decisions: Record<string, string[]>
    rows?: { sku: string; net: string | null; fee: string | null }[]
    sales_coverage?: number[]
    fee_coverage?: number[]
  }
  evidence: { inputs: Record<string, { name: string; sha256: string; rows: number }>; steps: unknown[]; sales: unknown }
}
export interface Task {
  id: string
  role: 'owner' | 'analyst'
  revision: number
  scope: string
  question: string
  parent_record_id?: string
  topic: { datasource: number; label: string; window: string; version: string }
  files: FileVersion[]
  definition?: Run['definition']
  runs: Record<string, Run>
  work?: string
  adopted?: string
  handoff?: {
    id: string
    question: string
    status: string
    receiver_name: string
    base_run: string
    candidate?: string
    change?: string
  }
}
export interface ActionResult {
  task: Task
  receipt: { outcome?: string; changes?: Changes; run_id?: string; action: string }
}

const root = '/chat/multi'
const task = (id: string) => root + '/task/' + id
const quiet = { requestOptions: { customError: true } }
export const multi = {
  available: (id: string) => request.get<{ enabled: boolean; label?: string; kind?: string }>(root + '/available/' + id, quiet),
  inbox: () => request.get<{ id: string; question: string; status: string; kind?: string }[]>(root + '/inbox', quiet),
  get: (id: string) => request.get<Task>(task(id), quiet),
  start: (id: string, data: { store?: string; question: string; parent_record_id?: string }) =>
    request.post<Task>(task(id), data, quiet),
  upload: (id: string, data: FormData) => request.post<{ file_id: string; task: Task }>(
    task(id) + '/files', data, { ...quiet, headers: { 'Content-Type': 'multipart/form-data' } }
  ),
  action: (id: string, data: Record<string, unknown>) =>
    request.post<ActionResult>(task(id) + '/action', data, quiet),
  reviewers: (id: string) => request.get<{ id: string; name: string }[]>(task(id) + '/reviewers', quiet),
  replacement: (id: string, file: string) => request.get<Changes>(task(id) + '/replacement/' + file, quiet),
  original: (id: string, file: string) => request.get<{ base64: string; name: string }>(task(id) + '/file/' + file, quiet),
  notebook: (id: string, run: string) =>
    request.get<Blob>(task(id) + '/notebook/' + run, { ...quiet, responseType: 'blob', transformResponse: [] }),
}
