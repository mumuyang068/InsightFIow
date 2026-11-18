import type { AnalysisState } from '@/api/analysis'

export function resultNumber(value: number, locale: string, signed = false, precision?: number | null) {
  const digits = precision != null && Number.isInteger(precision) && precision >= 0 && precision <= 6
    ? precision
    : undefined
  return Intl.NumberFormat(locale, {
    maximumFractionDigits: digits ?? 2,
    minimumFractionDigits: digits,
    signDisplay: signed ? 'exceptZero' : 'auto',
  }).format(value)
}


function calendarRange(start?: string, endInclusive?: string, endExclusive?: string) {
  if (!start) return endInclusive
  // These are business-calendar dates, not instants in the browser's time zone.
  const endUtc = endExclusive ? Date.parse(endExclusive + 'T00:00:00Z') : NaN
  const lastDay = Number.isFinite(endUtc)
    ? new Date(endUtc - 86_400_000).toISOString().slice(0, 10)
    : endInclusive
  return lastDay && lastDay > start ? start + '—' + lastDay : start
}

export function analysisPeriods(scope: AnalysisState['scope']) {
  return {
    current: calendarRange(scope.range_start ?? scope.current, scope.current, scope.range_end_exclusive),
    baseline: calendarRange(scope.baseline, undefined, scope.baseline_end_exclusive),
  }
}
