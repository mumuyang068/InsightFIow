import type { AnalysisState, SavedFunnel } from '@/api/analysis'

export function hasSavedRunVisual(analysis?: AnalysisState, funnel?: SavedFunnel) {
  return Boolean(analysis?.scope || funnel)
}
