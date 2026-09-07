/** Shared primitives for the Web settings tree. */
import { useCallback, useEffect, useState, type ReactNode } from 'react'

// ---------------------------------------------------------------------------
// formatting helpers
// ---------------------------------------------------------------------------
export function fmtBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

export function fmtTime(ms: number): string {
  if (!ms) return '—'
  const d = new Date(ms)
  const pad = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`
}

// ---------------------------------------------------------------------------
// tiny async hook
// ---------------------------------------------------------------------------
export interface AsyncState<T> {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    fn()
      .then((d) => {
        if (alive) setData(d)
      })
      .catch((e) => {
        if (alive) setError((e as Error).message)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, error, loading, reload }
}

// ---------------------------------------------------------------------------
// layout atoms
// ---------------------------------------------------------------------------
export function DetailShell(props: {
  title: string
  subtitle?: string
  onBack: () => void
  extra?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="view settings">
      <div className="settings-detail">
        <div className="detail-bar">
          <button className="back" onClick={props.onBack}>
            ‹ 设置
          </button>
          <div className="detail-title">
            <h2>{props.title}</h2>
            {props.subtitle && <p className="muted">{props.subtitle}</p>}
          </div>
          {props.extra}
        </div>
        {props.children}
      </div>
    </div>
  )
}

export function SectionCard(props: {
  title?: string
  hint?: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <section className={`settings-section ${props.className ?? ''}`}>
      {props.title && (
        <header>
          <h3>{props.title}</h3>
          {props.hint && <p className="muted">{props.hint}</p>}
        </header>
      )}
      {props.children}
    </section>
  )
}

export function Note(props: {
  kind?: 'info' | 'warn' | 'ok'
  children: ReactNode
}) {
  const kind = props.kind ?? 'info'
  return <p className={`note note-${kind}`}>{props.children}</p>
}

export function StatusLine(props: {
  ok?: string | null
  err?: string | null
}) {
  if (props.err)
    return (
      <p className="statusline">
        <span className="error">{props.err}</span>
      </p>
    )
  if (props.ok) return <p className="statusline ok">{props.ok}</p>
  return null
}

export function Spinner({ text }: { text?: string }) {
  return <p className="empty">{text ?? 'Loading…'}</p>
}

export function KvFacts(props: { rows: [string, string][] }) {
  return (
    <dl className="facts">
      {props.rows.flatMap(([k, v], i) => [
        <dt key={`k${i}`}>{k}</dt>,
        <dd key={`v${i}`}>{v}</dd>,
      ])}
    </dl>
  )
}

export function MiniBar(props: { label: string; frac: number; bytes: number; sub: string }) {
  const frac = Math.max(0.004, Math.min(1, props.frac))
  return (
    <div className="minibar">
      <div className="minibar-head">
        <span className="minibar-label">{props.label}</span>
        <span className="muted">
          {fmtBytes(props.bytes)} · {props.sub}
        </span>
      </div>
      <div className="minibar-track">
        <div className="minibar-fill" style={{ width: `${frac * 100}%` }} />
      </div>
    </div>
  )
}
