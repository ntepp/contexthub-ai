// Formatting and periods of the consumption pages. Figures are those returned by the API
// (read from the LLM gateway): nothing here estimates or completes them.

import type { DailyUsage, UsageTotals, UserUsage } from './api'

// ---------------------------------------------------------------- periods

export type PeriodPreset = 'month' | 'previous-month' | '30-days' | '12-months' | 'custom'

export interface Period {
  from: string
  to: string
}

export const PERIOD_PRESETS: [PeriodPreset, string][] = [
  ['month', 'Mois en cours'],
  ['previous-month', 'Mois précédent'],
  ['30-days', '30 derniers jours'],
  ['12-months', '12 derniers mois'],
  ['custom', 'Dates choisies'],
]

/** Same limit as the API (src/api/routes/usage.py). */
export const MAX_PERIOD_DAYS = 366

/** Local calendar day as "YYYY-MM-DD" (not toISOString, which would shift to UTC). */
export function isoDay(date: Date): string {
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${m}-${d}`
}

/** "YYYY-MM-DD" read as a calendar day (midday: no time-zone surprise). */
export function parseDay(day: string): Date {
  const [y, m, d] = day.split('-').map((n) => parseInt(n, 10))
  return new Date(y, m - 1, d, 12)
}

export function daysBetween(from: string, to: string): number {
  return Math.round((parseDay(to).getTime() - parseDay(from).getTime()) / 86_400_000) + 1
}

export function presetPeriod(preset: Exclude<PeriodPreset, 'custom'>, today = new Date()): Period {
  const y = today.getFullYear()
  const m = today.getMonth()
  const d = today.getDate()
  switch (preset) {
    case 'month':
      return { from: isoDay(new Date(y, m, 1)), to: isoDay(today) }
    case 'previous-month':
      return { from: isoDay(new Date(y, m - 1, 1)), to: isoDay(new Date(y, m, 0)) }
    case '30-days':
      return { from: isoDay(new Date(y, m, d - 29)), to: isoDay(today) }
    case '12-months':
      return { from: isoDay(new Date(y - 1, m, d + 1)), to: isoDay(today) }
  }
}

/** French message when a chosen period cannot be sent, else null. */
export function periodProblem(from: string, to: string): string | null {
  if (!from || !to) return 'Indiquez une date de début et une date de fin.'
  if (from > to) return 'La date de début doit précéder la date de fin.'
  if (daysBetween(from, to) > MAX_PERIOD_DAYS) return `La période ne peut pas dépasser ${MAX_PERIOD_DAYS} jours.`
  return null
}

// ---------------------------------------------------------------- numbers and dates

const integer = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 })
const compact = new Intl.NumberFormat('fr-FR', { notation: 'compact', maximumFractionDigits: 1 })
const percent = new Intl.NumberFormat('fr-FR', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 })
const longDay = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' })
const shortDay = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short' })
const monthYear = new Intl.DateTimeFormat('fr-FR', { month: 'long', year: 'numeric' })
const shortMonth = new Intl.DateTimeFormat('fr-FR', { month: 'short', year: '2-digit' })
const dateTime = new Intl.DateTimeFormat('fr-FR', { dateStyle: 'long', timeStyle: 'short' })

export function formatInteger(n: number): string {
  return integer.format(n)
}

/** Large counts shortened ("1,2 M"); the exact value goes in a title or a table. */
export function formatCompact(n: number): string {
  return n < 10_000 ? integer.format(n) : compact.format(n)
}

const currencyFormats = new Map<string, Intl.NumberFormat[]>()

function currencyFormat(currency: string, small: boolean): Intl.NumberFormat {
  let formats = currencyFormats.get(currency)
  if (!formats) {
    formats = [
      new Intl.NumberFormat('fr-FR', { style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }),
      new Intl.NumberFormat('fr-FR', { style: 'currency', currency, maximumSignificantDigits: 2 }),
    ]
    currencyFormats.set(currency, formats)
  }
  return formats[small ? 1 : 0]
}

/**
 * Costs are often fractions of a cent: below 0,10 the amount keeps two significant digits
 * (0,004 $ rather than 0,00 $), below 0,0001 it reads "< 0,0001 $".
 */
export function formatCost(value: number, currency: string): string {
  if (!/^[A-Za-z]{3}$/.test(currency)) {
    // Not an ISO 4217 code (Intl would throw): the number, then the code as received.
    return `${new Intl.NumberFormat('fr-FR', { maximumSignificantDigits: 6 }).format(value)} ${currency}`
  }
  const code = currency.toUpperCase()
  if (value === 0) return currencyFormat(code, false).format(0)
  if (Math.abs(value) < 0.0001) return `< ${currencyFormat(code, true).format(0.0001)}`
  return currencyFormat(code, Math.abs(value) < 0.1).format(value)
}

/** Share of a total; "< 0,1 %" for a tiny non-zero share, "—" when the total is zero. */
export function formatShare(part: number, total: number): string {
  if (total <= 0) return '—'
  const ratio = part / total
  if (ratio > 0 && ratio < 0.001) return `< ${percent.format(0.001)}`
  return percent.format(ratio)
}

export function formatDay(day: string): string {
  return longDay.format(parseDay(day))
}

export function formatShortDay(day: string): string {
  return shortDay.format(parseDay(day))
}

export function formatPeriod(period: Period): string {
  if (period.from === period.to) return `le ${formatDay(period.from)}`
  return `du ${formatDay(period.from)} au ${formatDay(period.to)}`
}

export function formatDateTimeLong(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : dateTime.format(date)
}

const DURATION_UNITS: Record<string, [string, string]> = {
  s: ['seconde', 'secondes'],
  m: ['minute', 'minutes'],
  h: ['heure', 'heures'],
  d: ['jour', 'jours'],
  w: ['semaine', 'semaines'],
  mo: ['mois', 'mois'],
  y: ['an', 'ans'],
}

/** Gateway budget window ("30d", "1mo"…) in French; unknown formats are shown as is. */
export function formatBudgetWindow(window: string): string {
  const match = /^(\d+)\s*(mo|[smhdwy])$/i.exec(window.trim())
  if (!match) return window
  const n = parseInt(match[1], 10)
  const [one, many] = DURATION_UNITS[match[2].toLowerCase()]
  return `${n} ${n > 1 ? many : one}`
}

// ---------------------------------------------------------------- chart series

export interface ChartPoint extends UsageTotals {
  key: string
  /** Full label (tooltip, accessible table). */
  label: string
  /** Short label (axis). */
  tick: string
}

/**
 * One point per day of the period (days without activity at zero: the gateway reports
 * only days with requests), or per month beyond three months, summed from the days.
 */
export function chartSeries(daily: DailyUsage[], period: Period): { unit: 'day' | 'month'; points: ChartPoint[] } {
  const byDay = new Map(daily.map((d) => [d.date, d]))
  const unit = daysBetween(period.from, period.to) > 92 ? 'month' : 'day'
  const points: ChartPoint[] = []
  const end = parseDay(period.to)
  for (let cursor = parseDay(period.from); cursor <= end; cursor.setDate(cursor.getDate() + 1)) {
    const day = isoDay(cursor)
    const totals = byDay.get(day)
    const key = unit === 'day' ? day : day.slice(0, 7)
    let point = points[points.length - 1]
    if (!point || point.key !== key) {
      const first = parseDay(day)
      point = {
        key,
        label: unit === 'day' ? formatDay(day) : monthYear.format(first),
        tick: unit === 'day' ? formatShortDay(day) : shortMonth.format(first),
        cost: 0,
        input_tokens: 0,
        output_tokens: 0,
        requests: 0,
      }
      points.push(point)
    }
    if (totals) {
      point.cost += totals.cost
      point.input_tokens += totals.input_tokens
      point.output_tokens += totals.output_tokens
      point.requests += totals.requests
    }
  }
  return { unit, points }
}

// ---------------------------------------------------------------- CSV export

function csvCell(value: string): string {
  // A cell starting with = + - @ would run as a formula in a spreadsheet (names are user input).
  const safe = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value
  return /[";\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe
}

/** Decimal comma, no grouping: what a French Excel reads as a number. */
function csvNumber(n: number, digits = 0): string {
  return n.toFixed(digits).replace('.', ',')
}

/** Per-user table as CSV for a French Excel: ";" separator, UTF-8 BOM, CRLF. */
export function usersCsv(rows: UserUsage[], totalCost: number, currency: string): Blob {
  const header = [
    'Utilisateur',
    'E-mail',
    'Identifiant',
    'Requêtes',
    "Jetons d'entrée",
    'Jetons de sortie',
    `Coût (${currency})`,
    'Part du coût total (%)',
  ]
  const lines = [header.map(csvCell).join(';')]
  for (const r of rows) {
    lines.push(
      [
        csvCell(r.label),
        csvCell(r.email ?? ''),
        csvCell(r.user_id ?? ''),
        csvNumber(r.requests),
        csvNumber(r.input_tokens),
        csvNumber(r.output_tokens),
        csvNumber(r.cost, 6),
        totalCost > 0 ? csvNumber((r.cost / totalCost) * 100, 2) : '',
      ].join(';'),
    )
  }
  return new Blob(['﻿' + lines.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' })
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
